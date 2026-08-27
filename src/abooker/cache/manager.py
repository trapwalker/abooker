"""Cache lifecycle: client tracking, warmup, eviction, disk-quota enforcement.

Terminology:
  "needed" files — chapters that *should* sit in cache right now, because
  either they're within the always-warm head of a book (WARMUP_COUNT) or an
  active listener is at/near them (their position + WARMUP_DEPTH lookahead).
  "quota" eviction — a separate, disk-space-driven safety net: whenever free
  space on DATA_DIR drops below MIN_FREE_DISK_GB, the globally
  least-recently-requested cached chapters (across *all* books) are deleted
  first, regardless of whether they're still "needed" per the rule above.
"""
import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from abooker.core.config import settings
from abooker.core.models import AudioFile, Client, DownloadEvent, FileStatus, Publication, PublicationStatus

log = logging.getLogger(__name__)


async def _get_or_create_client(session: AsyncSession, ip: str, user_agent: str) -> Client:
    result = await session.execute(
        select(Client).where(Client.ip == ip).where(Client.user_agent == user_agent)
    )
    client = result.scalar_one_or_none()
    if client is None:
        client = Client(ip=ip, user_agent=user_agent)
        session.add(client)
        await session.flush()
    else:
        client.last_seen = datetime.now(timezone.utc)
    return client


async def on_file_delivered(
    session: AsyncSession,
    client_ip: str,
    user_agent: str,
    publication_id: int,
    file_index: int,
) -> None:
    """Record delivery event, update client/file timestamps, then warmup."""
    client = await _get_or_create_client(session, client_ip, user_agent)

    af_q = await session.execute(
        select(AudioFile)
        .where(AudioFile.publication_id == publication_id)
        .where(AudioFile.index == file_index)
    )
    af = af_q.scalar_one_or_none()
    if af is None:
        return

    now = datetime.now(timezone.utc)
    af.last_requested_at = now

    event = DownloadEvent(
        client_id=client.id,
        file_id=af.id,
        started_at=now,
        completed_at=now,
    )
    session.add(event)
    await session.flush()

    # Trigger warmup/eviction in background (non-blocking relative to the request).
    asyncio.create_task(_warmup_and_evict(publication_id, file_index))


async def compute_needed_files(session: AsyncSession, publication_id: int) -> set[int]:
    """Return set of file indexes that should be in cache right now."""
    needed: set[int] = set(range(settings.WARMUP_COUNT))

    # Each active client's *current* (most recent) position, plus lookahead.
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.CLIENT_TTL_DAYS)
    events_q = await session.execute(
        select(DownloadEvent.client_id, AudioFile.index, DownloadEvent.completed_at)
        .join(Client, DownloadEvent.client_id == Client.id)
        .join(AudioFile, DownloadEvent.file_id == AudioFile.id)
        .where(AudioFile.publication_id == publication_id)
        .where(Client.last_seen >= cutoff)
        .order_by(DownloadEvent.completed_at.asc())
    )
    latest_index_by_client: dict[int, int] = {}
    for client_id, file_index, _completed_at in events_q.all():
        # Rows are ascending by time, so the last write per client wins == latest position.
        latest_index_by_client[client_id] = file_index

    for idx in latest_index_by_client.values():
        for offset in range(settings.WARMUP_DEPTH + 1):
            needed.add(idx + offset)

    return needed


async def run_warmup(session: AsyncSession, publication_id: int) -> None:
    """Download files that should be in cache but aren't."""
    from abooker.torrent import client as qbt

    pub_q = await session.execute(select(Publication).where(Publication.id == publication_id))
    pub = pub_q.scalar_one_or_none()
    if pub is None:
        return

    needed = await compute_needed_files(session, publication_id)

    af_q = await session.execute(
        select(AudioFile)
        .where(AudioFile.publication_id == publication_id)
        .where(AudioFile.index.in_(needed))
        .where(AudioFile.status.in_([FileStatus.pending, FileStatus.evicted]))
    )
    to_download = af_q.scalars().all()

    for af in to_download:
        if af.qbt_file_index is None:
            continue
        try:
            await qbt.set_file_priority(pub.torrent_hash, af.qbt_file_index, 7)
            af.status = FileStatus.downloading
            log.info("Warmup queued: %s[%d]", pub.slug, af.index)
        except Exception:
            log.warning("Could not queue warmup for %s[%d]", pub.slug, af.index, exc_info=True)


async def run_eviction(session: AsyncSession, publication_id: int) -> None:
    """Remove files from cache that are no longer needed for this publication."""
    from abooker.torrent import client as qbt

    pub_q = await session.execute(select(Publication).where(Publication.id == publication_id))
    pub = pub_q.scalar_one_or_none()
    if pub is None:
        return

    needed = await compute_needed_files(session, publication_id)

    af_q = await session.execute(
        select(AudioFile)
        .where(AudioFile.publication_id == publication_id)
        .where(AudioFile.status == FileStatus.cached)
    )
    cached_files = af_q.scalars().all()

    for af in cached_files:
        if af.index in needed:
            continue
        await _evict_one(pub, af, qbt)


async def _evict_one(pub: Publication, af: AudioFile, qbt) -> None:
    """Delete one chapter's cached bytes from disk and mark it evicted."""
    actual = Path(settings.DATA_DIR) / pub.slug / "files" / af.filename
    if actual.exists() or actual.is_symlink():
        try:
            actual.unlink()
            log.info("Evicted: %s/%s", pub.slug, af.filename)
        except OSError:
            log.warning("Could not evict %s", actual, exc_info=True)
    af.status = FileStatus.evicted
    if af.qbt_file_index is not None:
        try:
            await qbt.set_file_priority(pub.torrent_hash, af.qbt_file_index, 0)
        except Exception:
            pass


async def warmup_file(session: AsyncSession, publication_id: int, file_index: int) -> AudioFile | None:
    """Manually queue one chapter for download (bot "🔥 прогреть" button)."""
    from abooker.torrent import client as qbt

    pub_q = await session.execute(select(Publication).where(Publication.id == publication_id))
    pub = pub_q.scalar_one_or_none()
    af_q = await session.execute(
        select(AudioFile)
        .where(AudioFile.publication_id == publication_id)
        .where(AudioFile.index == file_index)
    )
    af = af_q.scalar_one_or_none()
    if pub is None or af is None or af.qbt_file_index is None:
        return af

    af.last_requested_at = datetime.now(timezone.utc)
    if af.status != FileStatus.cached:
        await qbt.set_file_priority(pub.torrent_hash, af.qbt_file_index, 7)
        af.status = FileStatus.downloading
    return af


async def evict_file(session: AsyncSession, publication_id: int, file_index: int) -> AudioFile | None:
    """Manually drop one chapter from cache (bot "🗑 удалить" button)."""
    from abooker.torrent import client as qbt

    pub_q = await session.execute(select(Publication).where(Publication.id == publication_id))
    pub = pub_q.scalar_one_or_none()
    af_q = await session.execute(
        select(AudioFile)
        .where(AudioFile.publication_id == publication_id)
        .where(AudioFile.index == file_index)
    )
    af = af_q.scalar_one_or_none()
    if pub is None or af is None:
        return af

    await _evict_one(pub, af, qbt)
    return af


async def link_or_copy_from_qbt(pub: Publication, af: AudioFile) -> Path:
    """Locate a finished download inside qBittorrent's save path and expose it
    under DATA_DIR/{slug}/files/{af.filename} (our numbering-prefixed alias;
    symlink when possible, copy as a fallback across filesystems). Returns
    the destination path.

    The source is looked up by af.source_path — the file's *real* relative
    path inside the torrent (subdirectories included) — never af.filename,
    since qBittorrent never renames anything to our alias.
    """
    from abooker.torrent import client as qbt

    dest = Path(settings.DATA_DIR) / pub.slug / "files" / af.filename
    if dest.exists() or dest.is_symlink():
        return dest

    save_path = await qbt.get_torrent_save_path(pub.torrent_hash)
    if not save_path:
        raise RuntimeError(f"qBittorrent has no save path for torrent {pub.torrent_hash}")

    source_name = af.source_path or af.filename
    src = Path(save_path) / source_name
    if not src.exists():
        candidates = list(Path(save_path).rglob(Path(source_name).name))
        src = candidates[0] if candidates else None

    if not (src and src.exists()):
        raise RuntimeError(f"Cannot locate downloaded file {source_name} in qBittorrent save path")

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.symlink_to(src)
    except OSError:
        shutil.copy2(src, dest)
    return dest


async def finalize_if_complete(session: AsyncSession, pub: Publication, af: AudioFile) -> bool:
    """If af just finished downloading, materialize it into cache. Returns
    True if the chapter is now cached (whether it already was, or just became so)."""
    from abooker.torrent import client as qbt

    if af.status == FileStatus.cached:
        return True
    if af.qbt_file_index is None:
        return False

    progress = await qbt.get_download_progress(pub.torrent_hash, af.qbt_file_index)
    if progress < 1.0:
        return False

    await link_or_copy_from_qbt(pub, af)
    af.status = FileStatus.cached
    log.info("Finalized download: %s/%s", pub.slug, af.filename)
    await _maybe_mark_ready(session, pub)
    return True


async def _maybe_mark_ready(session: AsyncSession, pub: Publication) -> None:
    """Flip a `warming_up` publication to `ready` once its warmup head
    (index < WARMUP_COUNT) is fully cached. Purely informational — no route
    gates on Publication.status, per-chapter FileStatus already does that —
    but it's the observable "book is ready" signal the bot/status views want.
    """
    if pub.status != PublicationStatus.warming_up:
        return
    head_q = await session.execute(
        select(AudioFile)
        .where(AudioFile.publication_id == pub.id)
        .where(AudioFile.index < settings.WARMUP_COUNT)
    )
    head = head_q.scalars().all()
    if all(af.status == FileStatus.cached for af in head):
        pub.status = PublicationStatus.ready
        log.info("Publication %s finished warmup, now ready", pub.slug)


async def finalize_downloads(session: AsyncSession) -> int:
    """Scan every chapter currently marked 'downloading' across all books;
    materialize into cache any whose torrent download has actually finished.
    Returns the number of chapters finalized.
    """
    from abooker.torrent import client as qbt

    q = await session.execute(
        select(AudioFile, Publication)
        .join(Publication, AudioFile.publication_id == Publication.id)
        .where(AudioFile.status == FileStatus.downloading)
    )
    by_pub: dict[int, tuple[Publication, list[AudioFile]]] = {}
    for af, pub in q.all():
        by_pub.setdefault(pub.id, (pub, []))[1].append(af)

    finalized = 0
    for pub, afs in by_pub.values():
        try:
            statuses = await qbt.get_files_status(pub.torrent_hash)
        except Exception:
            log.warning("finalize_downloads: could not query qBittorrent for %s", pub.slug, exc_info=True)
            continue
        progress_by_idx = {s["index"]: s["progress"] for s in statuses}

        for af in afs:
            if af.qbt_file_index is None:
                continue
            if progress_by_idx.get(af.qbt_file_index, 0.0) < 1.0:
                continue
            try:
                await link_or_copy_from_qbt(pub, af)
                af.status = FileStatus.cached
                finalized += 1
                log.info("Finalized download: %s/%s", pub.slug, af.filename)
            except Exception:
                log.warning("Could not finalize %s/%s", pub.slug, af.filename, exc_info=True)

        await _maybe_mark_ready(session, pub)

    return finalized


async def periodic_finalize(interval_seconds: int = 15) -> None:
    """Background task: periodically materialize completed background downloads
    (warmup/prefetch) into the served cache, since nothing blocks on them anymore."""
    from abooker.core.database import get_session_factory

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            factory = get_session_factory()
            async with factory() as session:
                n = await finalize_downloads(session)
                if n:
                    await session.commit()
        except Exception:
            log.exception("Error in periodic finalize")


async def estimate_eta_seconds(publication: Publication, af: AudioFile, progress: float) -> float | None:
    """Rough remaining-time estimate for a downloading chapter, or None if unknown."""
    from abooker.torrent import client as qbt

    if progress >= 1.0:
        return 0.0
    speed = await qbt.get_torrent_speed(publication.torrent_hash)
    if speed <= 0:
        return None
    remaining_bytes = af.size_bytes * (1.0 - progress)
    return remaining_bytes / speed


async def enforce_disk_quota(session: AsyncSession) -> int:
    """Evict globally least-recently-requested cached chapters until
    MIN_FREE_DISK_GB is satisfied again. Returns the number of chapters evicted.

    Runs across *all* publications — under real disk pressure a chapter no one
    has touched in weeks loses to keeping the disk usable, even if it happens
    to sit in some other book's warmup head.
    """
    from abooker.torrent import client as qbt

    if not settings.MIN_FREE_DISK_GB:
        return 0

    min_free_bytes = settings.MIN_FREE_DISK_GB * 1024**3
    evicted_count = 0

    while True:
        try:
            free = shutil.disk_usage(settings.DATA_DIR).free
        except OSError:
            break
        if free >= min_free_bytes:
            break

        # Oldest-requested cached file first; NULLs (never explicitly
        # requested — pre-warmed only) sort first in SQLite's ASC ordering,
        # so they're the first to go, which is the right call under pressure.
        af_q = await session.execute(
            select(AudioFile, Publication)
            .join(Publication, AudioFile.publication_id == Publication.id)
            .where(AudioFile.status == FileStatus.cached)
            .order_by(AudioFile.last_requested_at.asc().nulls_first())
            .limit(1)
        )
        row = af_q.first()
        if row is None:
            log.warning(
                "Disk quota: %.1f GB free < %.1f GB target, but nothing left to evict",
                free / 1024**3, settings.MIN_FREE_DISK_GB,
            )
            break

        af, pub = row
        await _evict_one(pub, af, qbt)
        evicted_count += 1
        log.info(
            "Disk quota eviction: %s/%s (last requested %s)",
            pub.slug, af.filename, af.last_requested_at,
        )

    return evicted_count


async def _warmup_and_evict(publication_id: int, current_index: int) -> None:
    """Background task: warmup + evict + quota-check after file delivery."""
    from abooker.core.database import get_session_factory
    try:
        factory = get_session_factory()
        async with factory() as session:
            await run_warmup(session, publication_id)
            await run_eviction(session, publication_id)
            await enforce_disk_quota(session)
            await session.commit()
    except Exception:
        log.exception("Error in warmup/evict for publication %d", publication_id)


async def periodic_eviction(interval_seconds: int = 3600) -> None:
    """Background task: run eviction + disk quota for all publications periodically."""
    from abooker.core.database import get_session_factory

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            factory = get_session_factory()
            async with factory() as session:
                pubs_q = await session.execute(select(Publication.id))
                pub_ids = pubs_q.scalars().all()
                for pub_id in pub_ids:
                    await run_eviction(session, pub_id)
                await enforce_disk_quota(session)
                await session.commit()
        except Exception:
            log.exception("Error in periodic eviction")
