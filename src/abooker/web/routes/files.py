import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from abooker.cache.manager import estimate_eta_seconds, finalize_if_complete
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


def _mime_for(filename: str) -> str:
    from abooker.files import media_types
    ext = Path(filename).suffix.lstrip(".").lower()
    return media_types.get(ext, "application/octet-stream")


@router.get("/file/{slug}/{url_path}")
async def serve_file(
    slug: str,
    url_path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Serve a cached chapter, or queue it for download and fail fast.

    The RSS feed points podcast clients here. If the chapter is already
    cached it's handed back immediately (via nginx X-Accel-Redirect when
    NGINX_ACCEL is on, so this process never streams the bytes itself). If
    it isn't, we do *not* block the request waiting for the torrent client —
    we bump its download priority (plus a short lookahead of the following
    chapters) and return 503 right away, so the caller can retry shortly.
    """
    pub, af = await _get_audio_file(slug, url_path, session)
    actual = _actual_path(slug, af.filename)

    # Cheap opportunistic check: a background warmup may have just finished.
    if not actual.exists() and af.status == FileStatus.downloading:
        try:
            await finalize_if_complete(session, pub, af)
        except Exception:
            log.warning("finalize_if_complete failed for %s/%s", slug, url_path, exc_info=True)
        await session.commit()

    if actual.exists() or actual.is_symlink():
        af.last_requested_at = datetime.now(timezone.utc)
        await session.commit()

        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "")
        asyncio.create_task(_post_serve(pub.id, af.index, client_ip, user_agent))

        if settings.NGINX_ACCEL:
            accel_path = f"{settings.NGINX_ACCEL_PREFIX}/{quote(slug)}/files/{quote(af.filename)}"
            return Response(
                status_code=200,
                media_type=_mime_for(af.filename),
                headers={
                    "X-Accel-Redirect": accel_path,
                    "Content-Disposition": f'inline; filename="{af.filename}"',
                },
            )

        return FileResponse(
            path=actual,
            media_type=_mime_for(af.filename),
            filename=af.filename,
        )

    # Not cached: queue this chapter (and a short lookahead) for download,
    # fail fast instead of holding the connection open.
    if not pub.torrent_hash or af.qbt_file_index is None:
        raise HTTPException(status_code=503, detail="Глава недоступна: нет данных торрента")

    if af.status != FileStatus.downloading:
        try:
            await qbt.set_file_priority(pub.torrent_hash, af.qbt_file_index, 7)
            af.status = FileStatus.downloading
            af.last_requested_at = datetime.now(timezone.utc)
            await session.commit()
            log.info("Queued for download: %s/%s (qbt index %d)", slug, url_path, af.qbt_file_index)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Не удалось поставить главу на закачку: {exc}") from exc

    asyncio.create_task(_prefetch_lookahead(pub.id, af.index))

    eta = await estimate_eta_seconds(pub, af, 0.0)
    body = {
        "detail": "Глава временно недоступна: поставлена на закачку торрент-клиентом. Повторите запрос через несколько секунд.",
        "status": "downloading",
    }
    headers = {}
    if eta is not None:
        body["eta_seconds"] = round(eta)
        headers["Retry-After"] = str(max(5, round(eta)))
    else:
        headers["Retry-After"] = "20"

    return JSONResponse(status_code=503, content=body, headers=headers)


async def _prefetch_lookahead(publication_id: int, current_index: int) -> None:
    """Bump priority for the next WARMUP_DEPTH chapters ahead of the one just
    requested, same as after a successful delivery — a 503 is still a signal
    of listener intent, no need to wait for a later successful GET."""
    from abooker.core.database import get_session_factory
    from abooker.cache.manager import run_warmup

    try:
        factory = get_session_factory()
        async with factory() as session:
            await run_warmup(session, publication_id)
            await session.commit()
    except Exception:
        log.exception("Error in prefetch lookahead for publication %d", publication_id)


async def _post_serve(
    publication_id: int,
    file_index: int,
    client_ip: str,
    user_agent: str,
) -> None:
    """Log DownloadEvent and trigger warmup/eviction after a file is served."""
    try:
        from abooker.cache.manager import on_file_delivered
        from abooker.core.database import get_session_factory

        async with get_session_factory()() as new_session:
            await on_file_delivered(new_session, client_ip, user_agent, publication_id, file_index)
            await new_session.commit()
    except Exception:
        log.exception("Error in post-serve hook for publication %d file %d", publication_id, file_index)
