"""Cache lifecycle: client tracking, warmup, eviction."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from abooker.core.config import settings
from abooker.core.models import AudioFile, Client, DownloadEvent, FileStatus, Publication

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
    """Record delivery event, update client timestamps, then warmup."""
    client = await _get_or_create_client(session, client_ip, user_agent)

    af_q = await session.execute(
        select(AudioFile)
        .where(AudioFile.publication_id == publication_id)
        .where(AudioFile.index == file_index)
    )
    af = af_q.scalar_one_or_none()
    if af is None:
        return

    event = DownloadEvent(
        client_id=client.id,
        file_id=af.id,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    session.add(event)

    # Trigger warmup in background (non-blocking relative to the request).
    asyncio.create_task(_warmup_and_evict(publication_id, file_index))


async def compute_needed_files(session: AsyncSession, publication_id: int) -> set[int]:
    """Return set of file indexes that should be in cache."""
    needed: set[int] = set()

    # Always keep first WARMUP_COUNT files.
    for i in range(settings.WARMUP_COUNT):
        needed.add(i)

    # For each active client: current position + next WARMUP_DEPTH files.
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.CLIENT_TTL_DAYS)
    events_q = await session.execute(
        select(DownloadEvent.file_id, Client.last_seen)
        .join(Client, DownloadEvent.client_id == Client.id)
        .join(AudioFile, DownloadEvent.file_id == AudioFile.id)
        .where(AudioFile.publication_id == publication_id)
        .where(Client.last_seen >= cutoff)
        .order_by(DownloadEvent.completed_at.desc())
    )
    events = events_q.all()

    # Find each client's most recent file_id → look up its index.
    seen_clients: set[int] = set()
    file_ids = [e.file_id for e in events]
    if file_ids:
        af_q = await session.execute(
            select(AudioFile)
            .where(AudioFile.id.in_(file_ids))
        )
        file_map = {af.id: af.index for af in af_q.scalars()}

        for event in events:
            # Approximate: use file_id as proxy; group by client is not tracked here.
            idx = file_map.get(event.file_id, 0)
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
            await qbt.set_file_priority(pub.torrent_hash, af.qbt_file_index, 1)
            af.status = FileStatus.downloading
            log.info("Warmup queued: %s[%d]", pub.slug, af.index)
        except Exception:
            log.warning("Could not queue warmup for %s[%d]", pub.slug, af.index, exc_info=True)


async def run_eviction(session: AsyncSession, publication_id: int) -> None:
    """Remove files from cache that are no longer needed."""
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
        actual = Path(settings.DATA_DIR) / pub.slug / "files" / af.filename
        if actual.exists():
            try:
                actual.unlink()
                log.info("Evicted: %s/%s", pub.slug, af.filename)
            except OSError:
                log.warning("Could not evict %s", actual, exc_info=True)
        af.status = FileStatus.evicted
        # Tell qBittorrent to stop seeding/caching this file.
        if af.qbt_file_index is not None:
            try:
                await qbt.set_file_priority(pub.torrent_hash, af.qbt_file_index, 0)
            except Exception:
                pass


async def _warmup_and_evict(publication_id: int, current_index: int) -> None:
    """Background task: warmup + evict after file delivery."""
    from abooker.core.database import get_session_factory
    try:
        factory = get_session_factory()
        async with factory() as session:
            await run_warmup(session, publication_id)
            await run_eviction(session, publication_id)
            await session.commit()
    except Exception:
        log.exception("Error in warmup/evict for publication %d", publication_id)


async def periodic_eviction(interval_seconds: int = 3600) -> None:
    """Background task: run eviction for all publications periodically."""
    from abooker.core.database import get_session_factory
    from abooker.core.models import Publication

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            factory = get_session_factory()
            async with factory() as session:
                pubs_q = await session.execute(select(Publication.id))
                pub_ids = pubs_q.scalars().all()
                for pub_id in pub_ids:
                    await run_eviction(session, pub_id)
                await session.commit()
        except Exception:
            log.exception("Error in periodic eviction")
