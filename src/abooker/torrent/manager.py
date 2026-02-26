"""Publication creation: torrent → RSS + DB records."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from abooker.core.config import settings
from abooker.core.models import AudioFile, FileStatus, Publication
from abooker.core.slug import unique_slug
from abooker.files import filename_key, media_types
from abooker.rss import make_rss
from abooker.torrent import client as qbt

log = logging.getLogger(__name__)


def _is_audio(name: str) -> bool:
    ext = Path(name).suffix.lstrip(".").lower()
    return ext in media_types


def _pad_width(n: int) -> int:
    return len(str(max(n - 1, 0)))


async def create_publication(
    session: AsyncSession,
    source: str,
    title: str | None = None,
    author: str | None = None,
) -> Publication:
    """Add torrent, discover audio files, build RSS, persist to DB and FS.

    Args:
        session: async DB session (caller is responsible for commit).
        source: magnet link, .torrent URL, or local file path.
        title: optional title override (defaults to torrent name).
        author: optional author metadata.

    Returns:
        The newly created Publication ORM object (not yet committed).
    """
    # 1. Add torrent to qBittorrent with all files set to priority=0 (skip).
    torrent_hash = await qbt.add_torrent_ex(source)
    log.info("Torrent added: %s", torrent_hash)

    # 2. List files, filter audio.
    all_files = await qbt.get_files(torrent_hash)
    audio_files = [f for f in all_files if _is_audio(f["name"])]
    if not audio_files:
        raise ValueError("No audio files found in torrent")

    # 3. Natural-sort audio files by filename.
    audio_files.sort(key=lambda f: filename_key(Path(f["name"])))

    # 4. Generate slug from title or first filename.
    slug_base = title or Path(audio_files[0]["name"]).parent.name or Path(audio_files[0]["name"]).stem
    slug = await unique_slug(slug_base, session)

    # 5. Create data directory.
    pub_dir = settings.DATA_DIR / slug
    files_dir = pub_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    # 6. Build url_path for each file (zero-padded index + extension).
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

    # 7. Create Publication ORM.
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

    # 8. Create AudioFile ORM records.
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

    # 9. Generate RSS with URL = {WEB_BASE_URL}/file/{slug}/{url_path}.
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

    # 10. Write publication.json for crash-recovery.
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

    # 11. Set priority=7 (max) for first WARMUP_COUNT files.
    for r in file_records[: settings.WARMUP_COUNT]:
        try:
            await qbt.set_file_priority(torrent_hash, r["qbt_file_index"], 7)
        except Exception:
            log.warning("Could not set priority for file %s", r["filename"], exc_info=True)

    log.info("Publication created: %s (%d files)", slug, len(file_records))
    return pub
