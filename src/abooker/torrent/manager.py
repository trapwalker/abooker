"""Publication creation: torrent → RSS + DB records."""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from abooker.core.config import settings
from abooker.core.models import AudioFile, FileStatus, Publication, UserBook
from abooker.core.slug import unique_slug
from abooker.files import chapter_sort_key, media_types
from abooker.rss import make_rss
from abooker.torrent import client as qbt

log = logging.getLogger(__name__)


def _is_audio(name: str) -> bool:
    ext = Path(name).suffix.lstrip(".").lower()
    return ext in media_types


def _pad_width(n: int) -> int:
    return len(str(max(n - 1, 0)))


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
) -> tuple[Publication, bool]:
    """Add torrent, discover audio files, build RSS, persist to DB and FS.

    Args:
        session: async DB session (caller is responsible for commit).
        source: magnet link, .torrent URL, or local file path.
        title: optional title override (defaults to torrent name).
        author: optional author metadata.
        user_id: DB User.id to associate via UserBook (optional).

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

    if pre_hash:
        result = await session.execute(
            select(Publication).where(Publication.torrent_hash == pre_hash)
        )
        existing_pub = result.scalar_one_or_none()
        if existing_pub is not None:
            if user_id:
                await _ensure_user_book(session, user_id, existing_pub.id)
            log.info("Deduplication: returning existing publication %s", existing_pub.slug)
            return existing_pub, False

    # 1. Add torrent to qBittorrent with all files set to priority=0 (skip).
    torrent_hash = await qbt.add_torrent_ex(source)
    log.info("Torrent added: %s", torrent_hash)

    # --- Post-add deduplication (for HTTP URLs) ---
    if not pre_hash:
        result = await session.execute(
            select(Publication).where(Publication.torrent_hash == torrent_hash)
        )
        existing_pub = result.scalar_one_or_none()
        if existing_pub is not None:
            if user_id:
                await _ensure_user_book(session, user_id, existing_pub.id)
            log.info("Deduplication: returning existing publication %s", existing_pub.slug)
            return existing_pub, False

    # 2. List files, filter audio.
    all_files = await qbt.get_files(torrent_hash)
    audio_files = [f for f in all_files if _is_audio(f["name"])]
    if not audio_files:
        raise ValueError("Нет аудио-файлов в раздаче")

    # 3. Content validation.
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

    # 4. Order audio files by chapter number (see files.chapter_sort_key for why
    #    plain natural/alphabetic sort is not good enough here).
    audio_files.sort(key=lambda f: chapter_sort_key(Path(f["name"]).name))

    # 5. Generate slug from title or first filename.
    slug_base = title or Path(audio_files[0]["name"]).parent.name or Path(audio_files[0]["name"]).stem
    slug = await unique_slug(slug_base, session)

    # 6. Create data directory.
    pub_dir = settings.DATA_DIR / slug
    files_dir = pub_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    # 7. Build url_path for each file (zero-padded index + extension).
    pad = _pad_width(len(audio_files))
    file_records = []
    for pos, f in enumerate(audio_files):
        ext = Path(f["name"]).suffix.lstrip(".").lower()
        url_path = f"{str(pos).zfill(pad)}.{ext}"
        file_records.append({
            "index": pos,
            "url_path": url_path,
            "filename": Path(f["name"]).name,
            "size_bytes": f["size"],
            "qbt_file_index": f["index"],
        })

    total_size = sum(r["size_bytes"] for r in file_records)

    # 8. Create Publication ORM.
    pub = Publication(
        slug=slug,
        title=title or slug_base,
        author=author,
        source=source,
        torrent_hash=torrent_hash,
        created_at=datetime.now(timezone.utc),
        total_files=len(file_records),
        total_size=total_size,
    )
    session.add(pub)
    await session.flush()  # get pub.id

    # 9. Create UserBook if user_id provided.
    if user_id:
        await _ensure_user_book(session, user_id, pub.id)

    # 10. Create AudioFile ORM records.
    audio_orm = []
    for r in file_records:
        af = AudioFile(
            publication_id=pub.id,
            index=r["index"],
            url_path=r["url_path"],
            filename=r["filename"],
            size_bytes=r["size_bytes"],
            qbt_file_index=r["qbt_file_index"],
            status=FileStatus.pending,
        )
        session.add(af)
        audio_orm.append(af)

    await session.flush()

    # 11. Generate RSS with URL = {WEB_BASE_URL}/file/{slug}/{url_path}.
    rss_items = []
    for r, af in zip(file_records, audio_orm):
        rss_items.append({
            "path": Path(r["filename"]),
            "url": f"{settings.WEB_BASE_URL}/file/{slug}/{r['url_path']}",
            "size": r["size_bytes"],
        })

    rss_bytes = make_rss(
        rss_items,
        title=pub.title,
        author=pub.author,
    )
    (pub_dir / "playlist.rss").write_bytes(rss_bytes)

    # 12. Write publication.json for crash-recovery.
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
                "size_bytes": r["size_bytes"],
                "qbt_file_index": r["qbt_file_index"],
            }
            for r in file_records
        ],
    }
    (pub_dir / "publication.json").write_text(json.dumps(pub_json, ensure_ascii=False, indent=2))

    # 13. Set priority=7 (max) for first WARMUP_COUNT files.
    for r in file_records[: settings.WARMUP_COUNT]:
        try:
            await qbt.set_file_priority(torrent_hash, r["qbt_file_index"], 7)
        except Exception:
            log.warning("Could not set priority for file %s", r["filename"], exc_info=True)

    log.info("Publication created: %s (%d files)", slug, len(file_records))
    return pub, True
