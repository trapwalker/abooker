import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from abooker.core.config import settings
from abooker.core.database import get_session
from abooker.core.models import AudioFile, FileStatus, Publication
from abooker.torrent import client as qbt

log = logging.getLogger(__name__)

router = APIRouter()


async def _get_audio_file(slug: str, url_path: str, session: AsyncSession) -> tuple[Publication, AudioFile]:
    pub_q = await session.execute(select(Publication).where(Publication.slug == slug))
    pub = pub_q.scalar_one_or_none()
    if pub is None:
        raise HTTPException(status_code=404, detail="Publication not found")

    af_q = await session.execute(
        select(AudioFile)
        .where(AudioFile.publication_id == pub.id)
        .where(AudioFile.url_path == url_path)
    )
    af = af_q.scalar_one_or_none()
    if af is None:
        raise HTTPException(status_code=404, detail="File not found")

    return pub, af


def _actual_path(slug: str, filename: str) -> Path:
    return settings.DATA_DIR / slug / "files" / filename


@router.get("/file/{slug}/{url_path}")
async def serve_file(
    slug: str,
    url_path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    pub, af = await _get_audio_file(slug, url_path, session)
    actual = _actual_path(slug, af.filename)

    if not actual.exists():
        # Lazy download: set max priority and wait.
        if not pub.torrent_hash:
            raise HTTPException(status_code=503, detail="No torrent hash; cannot download")

        if af.qbt_file_index is None:
            raise HTTPException(status_code=503, detail="No qBittorrent file index available")

        af.status = FileStatus.downloading
        await session.commit()

        log.info("Lazy fetch %s/%s (qbt index %d)", slug, url_path, af.qbt_file_index)
        try:
            await qbt.set_file_priority(pub.torrent_hash, af.qbt_file_index, 7)
            await qbt.wait_for_file(pub.torrent_hash, af.qbt_file_index)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Download failed: {exc}") from exc

        # qBittorrent saves into its configured save path; symlink or copy into our files/ dir.
        await _link_or_copy_from_qbt(pub, af, actual)

        af.status = FileStatus.cached
        await session.commit()

    # Log download event and trigger warmup in background.
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")
    asyncio.create_task(_post_serve(session, pub, af, client_ip, user_agent))

    return FileResponse(
        path=actual,
        media_type=_mime_for(af.filename),
        filename=af.filename,
    )


async def _link_or_copy_from_qbt(pub: Publication, af: AudioFile, dest: Path) -> None:
    """Locate the downloaded file inside qBittorrent's save path and link it."""
    save_path = await qbt.get_torrent_save_path(pub.torrent_hash)
    if save_path:
        src = Path(save_path) / af.filename
        if not src.exists():
            # Walk the save_path tree looking for the filename.
            candidates = list(Path(save_path).rglob(af.filename))
            src = candidates[0] if candidates else None

        if src and src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                dest.symlink_to(src)
                return
            except OSError:
                import shutil
                shutil.copy2(src, dest)
                return

    raise RuntimeError(f"Cannot locate downloaded file {af.filename} in qBittorrent save path")


def _mime_for(filename: str) -> str:
    from abooker.files import media_types
    ext = Path(filename).suffix.lstrip(".").lower()
    return media_types.get(ext, "application/octet-stream")


async def _post_serve(
    session: AsyncSession,
    pub: Publication,
    af: AudioFile,
    client_ip: str,
    user_agent: str,
) -> None:
    """Log DownloadEvent and trigger warmup/eviction after a file is served."""
    try:
        from datetime import datetime, timezone

        from sqlalchemy import select as sa_select

        from abooker.cache.manager import on_file_delivered
        from abooker.core.models import Client, DownloadEvent

        factory = session.get_bind()  # type: ignore[arg-type]
        from abooker.core.database import get_session_factory
        async with get_session_factory()() as new_session:
            await on_file_delivered(new_session, client_ip, user_agent, pub.id, af.index)
            await new_session.commit()
    except Exception:
        log.exception("Error in post-serve hook for %s/%s", pub.slug, af.url_path)
