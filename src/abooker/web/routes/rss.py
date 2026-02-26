from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from abooker.core.config import settings

router = APIRouter()


@router.get("/rss/{slug}")
async def get_rss(slug: str):
    rss_path = settings.DATA_DIR / slug / "playlist.rss"
    if not rss_path.exists():
        raise HTTPException(status_code=404, detail="Publication not found")
    content = rss_path.read_bytes()
    return Response(content=content, media_type="application/rss+xml")
