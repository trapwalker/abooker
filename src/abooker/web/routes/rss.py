from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from sqlalchemy import select

from abooker.core.config import settings
from abooker.core.database import get_session_factory
from abooker.core.models import Publication

router = APIRouter()


@router.get("/rss/{slug}")
async def get_rss(slug: str):
    """Serve the feed for any existing publication — is_public does *not*
    gate this. It only controls whether OTHER users see the book listed in
    the bot's /list (see bot.handlers.commands.cmd_list); an RSS URL is
    already only handed to whoever added or was given access to the book,
    and its slug is the only thing needed to fetch it (same trust model as
    most personal podcast-feed links). Gating this on is_public as well
    made every freshly-added, still-private book's own RSS link 404 by
    default — the bot would tell you "RSS: <link>" for a link that didn't
    actually work until an admin separately flipped it public.
    """
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Publication).where(Publication.slug == slug))
        pub = result.scalar_one_or_none()

    if pub is None:
        raise HTTPException(status_code=404, detail="Publication not found")

    rss_path = settings.DATA_DIR / slug / "playlist.rss"
    if not rss_path.exists():
        raise HTTPException(status_code=404, detail="RSS file not found")

    content = rss_path.read_bytes()
    return Response(content=content, media_type="application/rss+xml")
