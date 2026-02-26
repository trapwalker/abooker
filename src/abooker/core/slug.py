import re

from unidecode import unidecode


def make_slug(text: str, max_length: int = 80) -> str:
    transliterated = unidecode(text)
    lowered = transliterated.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered)
    cleaned = cleaned.strip("-")
    return cleaned[:max_length].rstrip("-")


async def unique_slug(base: str, session) -> str:
    """Return a slug that does not exist in the DB, appending -2, -3 etc if needed."""
    from sqlalchemy import select
    from abooker.core.models import Publication

    slug = make_slug(base)
    candidate = slug
    n = 2
    while True:
        result = await session.execute(select(Publication.id).where(Publication.slug == candidate))
        if result.scalar_one_or_none() is None:
            return candidate
        candidate = f"{slug}-{n}"
        n += 1
