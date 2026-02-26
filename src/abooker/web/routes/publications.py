from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from abooker.core.config import settings
from abooker.core.database import get_session
from abooker.core.models import AudioFile, FileStatus, Publication

router = APIRouter()


@router.get("/publications")
async def list_publications(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Publication))
    publications = result.scalars().all()

    out = []
    for pub in publications:
        cached_q = await session.execute(
            select(func.count(), func.sum(AudioFile.size_bytes))
            .where(AudioFile.publication_id == pub.id)
            .where(AudioFile.status == FileStatus.cached)
        )
        cached_count, cached_size = cached_q.one()

        downloading_q = await session.execute(
            select(func.count())
            .where(AudioFile.publication_id == pub.id)
            .where(AudioFile.status == FileStatus.downloading)
        )
        downloading_count = downloading_q.scalar()

        out.append({
            "slug": pub.slug,
            "title": pub.title,
            "author": pub.author,
            "rss_url": f"{settings.WEB_BASE_URL}/rss/{pub.slug}",
            "total_files": pub.total_files,
            "cached_files": cached_count or 0,
            "cached_size_bytes": cached_size or 0,
            "downloading_count": downloading_count or 0,
            "source": pub.source,
            "created_at": pub.created_at.isoformat() if pub.created_at else None,
        })

    return out
