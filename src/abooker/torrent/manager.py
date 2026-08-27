"""Publication creation: torrent → RSS + DB records."""
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from qbittorrentapi.exceptions import Conflict409Error
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from abooker.core.config import settings
from abooker.core.models import AudioFile, FileStatus, Publication, PublicationStatus, UserBook
from abooker.core.slug import unique_slug
from abooker.files import build_chapter_aliases, media_types
from abooker.rss import make_rss
from abooker.torrent import client as qbt

log = logging.getLogger(__name__)


def _is_audio(name: str) -> bool:
    ext = Path(name).suffix.lstrip(".").lower()
    return ext in media_types


def _extract_magnet_hash(magnet: str) -> str | None:
    """Extract info_hash from a magnet URI (hex, lowercase)."""
    m = re.search(r"xt=urn:btih:([a-fA-F0-9]{40}|[A-Z2-7]{32})", magnet, re.IGNORECASE)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 32:
        import base64
        try:
            return base64.b32decode(h.upper()).hex()
        except Exception:
            return None
    return h.lower()


def _compute_torrent_file_hash(torrent_bytes: bytes) -> str | None:
    """Compute SHA1 info_hash from raw .torrent file bytes."""
    info_start = torrent_bytes.find(b"4:info")
    if info_start == -1:
        return None
    info_start += 6  # skip "4:info"
    if torrent_bytes[info_start : info_start + 1] != b"d":
        return None
    depth = 0
    i = info_start
    while i < len(torrent_bytes):
        b = torrent_bytes[i : i + 1]
        if b in (b"d", b"l"):
            depth += 1
            i += 1
        elif b == b"e":
            depth -= 1
            i += 1
            if depth == 0:
                return hashlib.sha1(torrent_bytes[info_start:i]).hexdigest()
        elif b == b"i":
            end = torrent_bytes.index(b"e", i + 1)
            i = end + 1
        elif b"0" <= b <= b"9":
            colon = torrent_bytes.index(b":", i)
            length = int(torrent_bytes[i:colon])
            i = colon + 1 + length
        else:
            return None
    return None


def _format_metadata_timeout_hint(diag: dict[str, Any]) -> str:
    """Turn swarm diagnostics into a human hint about *why* metadata never arrived."""
    dht_nodes = diag.get("dht_nodes")
    status = diag.get("connection_status")
    seeds, leechs = diag.get("num_seeds"), diag.get("num_leechs")
    if (dht_nodes is not None and dht_nodes == 0) or status == "disconnected":
        return (
            "DHT-узлов не видно (connection_status="
            f"{status!r}, dht_nodes={dht_nodes}) — похоже, что P2P-трафик "
            "заблокирован на уровне сети, а не проблема конкретной раздачи."
        )
    if seeds is not None and seeds == 0 and (leechs or 0) == 0:
        return (
            f"DHT работает (dht_nodes={dht_nodes}), но у раздачи так и не "
            "нашлось ни одного сида — похоже, раздача мертва или очень редкая."
        )
    return (
        f"dht_nodes={dht_nodes}, сидов/личей={seeds}/{leechs} — соединение "
        "есть, но раздача отвечает медленно. Попробуйте ещё раз позже."
    )


async def _ensure_user_book(session: AsyncSession, user_id: int, publication_id: int) -> UserBook:
    """Create UserBook if it doesn't exist; return it either way."""
    existing = await session.execute(
        select(UserBook).where(
            UserBook.user_id == user_id,
            UserBook.publication_id == publication_id,
        )
    )
    ub = existing.scalar_one_or_none()
    if ub is None:
        ub = UserBook(user_id=user_id, publication_id=publication_id)
        session.add(ub)
        await session.flush()
    return ub


async def create_publication(
    session: AsyncSession,
    source: str,
    title: str | None = None,
    author: str | None = None,
    user_id: int | None = None,
    on_metadata_progress: Callable[[float, dict[str, Any]], Awaitable[None]] | None = None,
) -> tuple[Publication, bool]:
    """Add torrent, discover audio files, build RSS, persist to DB and FS.

    Args:
        session: async DB session (caller is responsible for commit).
        source: magnet link, .torrent URL, or local file path.
        title: optional title override (defaults to torrent name).
        author: optional author metadata.
        user_id: DB User.id to associate via UserBook (optional).
        on_metadata_progress: optional async callback(elapsed_seconds, diagnostics)
            invoked periodically while waiting for a magnet's metadata — see
            torrent.client.wait_for_metadata.

    Returns:
        (Publication, is_new) — is_new=False means deduplication occurred.
    """
    # --- Pre-add deduplication for magnet links ---
    pre_hash: str | None = None
    if source.startswith("magnet:"):
        pre_hash = _extract_magnet_hash(source)
    elif not (source.startswith("http://") or source.startswith("https://")):
        # Local .torrent file — compute hash from bytes
        try:
            pre_hash = _compute_torrent_file_hash(Path(source).read_bytes())
        except Exception:
            pass

    # Both dedup checks exclude pending_metadata rows: those are another
    # in-flight attempt at the same torrent that hasn't resolved a real
    # slug/RSS yet, not a usable publication to hand back as "already added".
    if pre_hash:
        result = await session.execute(
            select(Publication)
            .where(Publication.torrent_hash == pre_hash)
            .where(Publication.status != PublicationStatus.pending_metadata)
        )
        existing_pub = result.scalar_one_or_none()
        if existing_pub is not None:
            if user_id:
                await _ensure_user_book(session, user_id, existing_pub.id)
            log.info("Deduplication: returning existing publication %s", existing_pub.slug)
            return existing_pub, False

    # 1. Add torrent to qBittorrent with all files set to priority=0 (skip).
    try:
        torrent_hash = await qbt.add_torrent_ex(source)
    except Conflict409Error:
        # qBittorrent already has a torrent with this hash — most likely an
        # earlier failed attempt at the same magnet/.torrent that we never
        # cleaned up there (older versions of this code didn't; a failure now
        # does, see the except block below). Only recoverable when we can
        # name the hash ourselves (magnet/local .torrent); an HTTP URL gives
        # us no way to know which existing torrent it collided with.
        if not pre_hash:
            raise ValueError(
                "qBittorrent сообщил о конфликте при добавлении этой раздачи "
                "по URL, а определить её хэш заранее нельзя (это не magnet и "
                "не локальный .torrent-файл) — нечем сопоставить с уже "
                "существующим торрентом. Нужна ручная очистка в qBittorrent."
            ) from None
        torrent_hash = pre_hash
        log.warning("Torrent add conflicted (already present in qBittorrent), reusing known hash: %s", torrent_hash)
    else:
        log.info("Torrent added: %s", torrent_hash)

    # --- Post-add deduplication (for HTTP URLs) ---
    if not pre_hash:
        result = await session.execute(
            select(Publication)
            .where(Publication.torrent_hash == torrent_hash)
            .where(Publication.status != PublicationStatus.pending_metadata)
        )
        existing_pub = result.scalar_one_or_none()
        if existing_pub is not None:
            if user_id:
                await _ensure_user_book(session, user_id, existing_pub.id)
            log.info("Deduplication: returning existing publication %s", existing_pub.slug)
            return existing_pub, False

    # 2. Persist a pending_metadata placeholder *now*, before we know the file
    #    list. This is what makes "waiting for a magnet's metadata" a real,
    #    observable state (PublicationStatus.pending_metadata) instead of
    #    something that only lives inside this coroutine's local variables —
    #    it survives long enough to be queried, and cleanup_orphaned_pending()
    #    can recognize + drop it if the process dies before we get past this
    #    point. slug/title are throwaway placeholders, overwritten in step 6
    #    once the real file list is known.
    placeholder_slug = await unique_slug(f"pending-{torrent_hash[:12]}", session)
    pub = Publication(
        slug=placeholder_slug,
        title=title or "Загрузка…",
        author=author,
        source=source,
        torrent_hash=torrent_hash,
        created_at=datetime.now(timezone.utc),
        status=PublicationStatus.pending_metadata,
    )
    session.add(pub)
    await session.flush()  # get pub.id
    if user_id:
        await _ensure_user_book(session, user_id, pub.id)
    # Commit now (not just flush) so the pending_metadata row is durable
    # while we wait, possibly for minutes — otherwise a crash mid-wait would
    # leave no trace at all, defeating the point of persisting this phase.
    await session.commit()
    pub_id = pub.id  # captured now: `pub` may be expired after a rollback below

    try:
        # 3. List files, filter audio. wait_for_metadata is needed for magnet
        #    links: qBittorrent knows the hash immediately but the file list
        #    only arrives once metadata is fetched from peers/DHT — see its
        #    docstring.
        all_files = await qbt.wait_for_metadata(
            torrent_hash,
            timeout=settings.METADATA_TIMEOUT,
            on_progress=on_metadata_progress,
            progress_interval=settings.METADATA_PROGRESS_INTERVAL,
        )
        if not all_files:
            diag = await qbt.get_swarm_diagnostics(torrent_hash)
            hint = _format_metadata_timeout_hint(diag)
            raise ValueError(
                f"Не удалось получить список файлов раздачи за "
                f"{settings.METADATA_TIMEOUT:.0f} с. {hint}"
            )
        audio_files = [f for f in all_files if _is_audio(f["name"])]
        if not audio_files:
            raise ValueError("Нет аудио-файлов в раздаче")

        # 4. Content validation.
        if settings.MAX_FILE_SIZE_GB:
            max_bytes = settings.MAX_FILE_SIZE_GB * 1024**3
            too_large = [f for f in audio_files if f["size"] > max_bytes]
            if too_large:
                name = Path(too_large[0]["name"]).name
                size_gb = too_large[0]["size"] / 1024**3
                raise ValueError(f"Файл слишком большой: {name} ({size_gb:.1f} ГБ)")

        if settings.MIN_AUDIO_RATIO and all_files:
            audio_size = sum(f["size"] for f in audio_files)
            total_size_all = sum(f["size"] for f in all_files)
            if total_size_all > 0 and audio_size / total_size_all < settings.MIN_AUDIO_RATIO:
                ratio = audio_size / total_size_all
                raise ValueError(f"Мало аудио-файлов: {ratio:.0%} объёма")

        # 5. Order audio files by a numbering-prefixed alias built from each
        #    file's *full* path inside the torrent (directories included, not
        #    just the filename) — see files.build_chapter_aliases for why
        #    plain natural/alphabetic sort of just the filename is not good
        #    enough here.
        audio_paths = [Path(f["name"]) for f in audio_files]
        aliases = build_chapter_aliases(audio_paths)
        audio_files = [f for f, _ in sorted(zip(audio_files, aliases), key=lambda pair: pair[1])]
        aliases = sorted(aliases)

        # 6. Now that we know the real file list, replace the placeholder
        #    slug/title with the real ones. Safe to rename in place — nothing
        #    outside this process has been told the placeholder slug yet.
        slug_base = title or Path(audio_files[0]["name"]).parent.name or Path(audio_files[0]["name"]).stem
        slug = await unique_slug(slug_base, session)
        pub.slug = slug
        pub.title = title or slug_base

        # 7. Create data directory.
        pub_dir = settings.DATA_DIR / slug
        files_dir = pub_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        # 8. Build file records: url_path/filename are the alias (also used as
        #    the on-disk name under files/ — stays close to the original
        #    filename, so the cache directory is directly usable by a plain
        #    offline mp3 player). source_path is the file's real path inside
        #    the torrent, used to find it under qBittorrent's save path once
        #    downloaded (never renamed there).
        file_records = []
        for pos, (f, alias) in enumerate(zip(audio_files, aliases)):
            file_records.append({
                "index": pos,
                "url_path": alias,
                "filename": alias,
                "source_path": f["name"],
                "size_bytes": f["size"],
                "qbt_file_index": f["index"],
            })

        total_size = sum(r["size_bytes"] for r in file_records)
        pub.total_files = len(file_records)
        pub.total_size = total_size
        pub.status = PublicationStatus.warming_up

        # 9. Create AudioFile ORM records.
        audio_orm = []
        for r in file_records:
            af = AudioFile(
                publication_id=pub.id,
                index=r["index"],
                url_path=r["url_path"],
                filename=r["filename"],
                source_path=r["source_path"],
                size_bytes=r["size_bytes"],
                qbt_file_index=r["qbt_file_index"],
                status=FileStatus.pending,
            )
            session.add(af)
            audio_orm.append(af)

        await session.flush()

        # 10. Generate RSS with URL = {WEB_BASE_URL}/file/{slug}/{url_path}.
        #     The alias can contain spaces/Cyrillic/parens now, so it needs
        #     quoting in the URL — the episode title uses the original leaf
        #     filename (without our numbering prefix or extension) for
        #     readability.
        rss_items = []
        for r, af in zip(file_records, audio_orm):
            rss_items.append({
                "path": Path(r["filename"]),
                "title": Path(r["source_path"]).stem,
                "url": f"{settings.WEB_BASE_URL}/file/{slug}/{quote(r['url_path'])}",
                "size": r["size_bytes"],
            })

        rss_bytes = make_rss(
            rss_items,
            title=pub.title,
            author=pub.author,
        )
        (pub_dir / "playlist.rss").write_bytes(rss_bytes)

        # 11. Write publication.json for crash-recovery.
        pub_json = {
            "slug": slug,
            "title": pub.title,
            "author": pub.author,
            "source": source,
            "torrent_hash": torrent_hash,
            "created_at": pub.created_at.isoformat(),
            "warmup_count": 0,
            "files": [
                {
                    "index": r["index"],
                    "url_path": r["url_path"],
                    "filename": r["filename"],
                    "source_path": r["source_path"],
                    "size_bytes": r["size_bytes"],
                    "qbt_file_index": r["qbt_file_index"],
                }
                for r in file_records
            ],
        }
        (pub_dir / "publication.json").write_text(json.dumps(pub_json, ensure_ascii=False, indent=2))

        # 12. Set priority=7 (max) for first WARMUP_COUNT files and mark them
        #     `downloading` so periodic_finalize (cache.manager) picks them up
        #     and materializes them into files/ on its own, without waiting
        #     for a listener to request them first. pub itself stays
        #     `warming_up` until that same background pass notices they're
        #     all actually cached and flips it to `ready` — purely
        #     informational, no route gates on it.
        for r, af in list(zip(file_records, audio_orm))[: settings.WARMUP_COUNT]:
            try:
                await qbt.set_file_priority(torrent_hash, r["qbt_file_index"], 7)
                af.status = FileStatus.downloading
            except Exception:
                log.warning("Could not set priority for file %s", r["filename"], exc_info=True)

        log.info("Publication created: %s (%d files)", slug, len(file_records))
        return pub, True

    except Exception:
        # The torrent never resolved (dead magnet) or failed content
        # validation — nothing usable was ever created, so drop the
        # placeholder row rather than leave a permanently-pending stub that
        # would also break dedup (a retry of the same hash would otherwise
        # find it and report false success). Roll back first to discard any
        # AudioFile rows already flushed this attempt, then delete the
        # already-committed placeholder itself.
        await session.rollback()
        await session.execute(delete(UserBook).where(UserBook.publication_id == pub_id))
        await session.execute(delete(Publication).where(Publication.id == pub_id))
        await session.commit()
        # Also remove the torrent from qBittorrent itself — otherwise it sits
        # there forever, and a retry of the exact same magnet/.torrent later
        # hits Conflict409Error on re-add instead of a clean second attempt.
        try:
            await qbt.remove_torrent(torrent_hash)
        except Exception:
            log.warning("Could not remove torrent %s after failed publication", torrent_hash, exc_info=True)
        raise
