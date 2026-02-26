import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from abooker.core.config import settings
from abooker.core.models import AudioFile, Base, FileStatus, Publication

log = logging.getLogger(__name__)

_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.db_url, echo=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def init_db():
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    async with get_session_factory()() as session:
        yield session


async def recover_from_json():
    """Scan DATA_DIR for publication.json files and restore missing DB records."""
    factory = get_session_factory()
    async with factory() as session:
        for pub_json in settings.DATA_DIR.glob("*/publication.json"):
            try:
                data = json.loads(pub_json.read_text())
                slug = data["slug"]
                result = await session.execute(select(Publication).where(Publication.slug == slug))
                existing = result.scalar_one_or_none()
                if existing:
                    continue

                pub = Publication(
                    slug=slug,
                    title=data.get("title", slug),
                    author=data.get("author"),
                    source=data.get("source", ""),
                    torrent_hash=data.get("torrent_hash", ""),
                    created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(timezone.utc),
                    warmup_count=data.get("warmup_count", 0),
                )
                session.add(pub)
                await session.flush()

                files_dir = pub_json.parent / "files"
                for f_data in data.get("files", []):
                    url_path = f_data["url_path"]
                    filename = f_data["filename"]
                    actual_path = files_dir / filename
                    if actual_path.exists():
                        status = FileStatus.cached
                    else:
                        status = FileStatus.evicted

                    audio_file = AudioFile(
                        publication_id=pub.id,
                        index=f_data["index"],
                        url_path=url_path,
                        filename=filename,
                        size_bytes=f_data.get("size_bytes", 0),
                        qbt_file_index=f_data.get("qbt_file_index"),
                        status=status,
                    )
                    session.add(audio_file)

                await session.commit()
                log.info("Recovered publication %s from JSON", slug)
            except Exception:
                log.exception("Failed to recover publication from %s", pub_json)
