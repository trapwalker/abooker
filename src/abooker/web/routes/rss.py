from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from sqlalchemy import select

from abooker.core.config import settings
from abooker.core.database import get_session_factory
from abooker.core.models import Publication

router = APIRouter()


@router.get("/rss/{slug}")
async def get_rss(slug: str):
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Publication).where(Publication.slug == slug))
        pub = result.scalar_one_or_none()

    if pub is None or not pub.is_public:
        raise HTTPException(status_code=404, detail="Publication not found")

    rss_path = settings.DATA_DIR / slug / "playlist.rss"
    if not rss_path.exists():
        raise HTTPException(status_code=404, detail="RSS file not found")

    content = rss_path.read_bytes()
    return Response(content=content, media_type="application/rss+xml")
