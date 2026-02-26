"""Jackett search proxy integration."""
import asyncio
import logging
from typing import Any
from urllib.parse import quote_plus

import aiohttp

from abooker.core.config import settings

log = logging.getLogger(__name__)

AUDIO_BOOKS_CATEGORY = 3030   # Jackett's AudioBook category ID


class TorrentResult:
    def __init__(self, data: dict[str, Any]):
        self.title: str = data.get("Title", "")
        self.size_bytes: int = data.get("Size", 0)
        self.seeders: int = data.get("Seeders", 0)
        self.magnet: str = data.get("MagnetUri", "") or ""
        self.torrent_url: str = data.get("Link", "") or ""
        self.description: str = data.get("Description", "") or ""
        # Count audio files if Jackett provides Files list
        files = data.get("Files") or []
        from abooker.files import media_types
        self.audio_files_count: int = sum(
            1 for f in files if isinstance(f, str) and f.rsplit(".", 1)[-1].lower() in media_types
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "size_bytes": self.size_bytes,
            "seeders": self.seeders,
            "magnet": self.magnet,
            "torrent_url": self.torrent_url,
            "description": self.description,
            "audio_files_count": self.audio_files_count,
        }


async def search(query: str, category: int = AUDIO_BOOKS_CATEGORY) -> list[dict[str, Any]]:
    """Search Jackett and return a list of TorrentResult dicts."""
    if not settings.JACKETT_URL or not settings.JACKETT_KEY:
        raise RuntimeError("Jackett not configured (JACKETT_URL / JACKETT_KEY missing)")

    url = (
        f"{settings.JACKETT_URL.rstrip('/')}/api/v2.0/indexers/all/results"
        f"?apikey={settings.JACKETT_KEY}"
        f"&Query={quote_plus(query)}"
        f"&Category[]={category}"
    )

    async with aiohttp.ClientSession() as http:
        async with http.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            data = await resp.json()

    results = data.get("Results", [])
    return [TorrentResult(r).to_dict() for r in results]
