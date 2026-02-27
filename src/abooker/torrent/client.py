"""Async wrapper around qbittorrent-api for the operations we need."""
import asyncio
import logging
from pathlib import Path
from typing import Any

import qbittorrentapi

from abooker.core.config import settings

log = logging.getLogger(__name__)

POLL_INTERVAL = 2.0  # seconds between progress checks


def _make_client() -> qbittorrentapi.Client:
    return qbittorrentapi.Client(
        host=settings.QB_HOST,
        port=settings.QB_PORT,
        username=settings.QB_USER,
        password=settings.QB_PASS,
    )


def _run_sync(func, *args, **kwargs):
    """Run a blocking qbittorrent-api call in a thread pool."""
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, lambda: func(*args, **kwargs))


async def add_torrent(source: str) -> str:
    """Add a torrent from magnet link, .torrent URL, or local file path.

    Returns the info_hash of the added torrent.
    """
    def _add():
        client = _make_client()
        if source.startswith("magnet:"):
            client.torrents_add(urls=source, stopped=True)
        elif source.startswith("http://") or source.startswith("https://"):
            client.torrents_add(urls=source, stopped=True)
        else:
            # Local .torrent file
            path = Path(source)
            with path.open("rb") as fh:
                client.torrents_add(torrent_files=fh, stopped=True)

        # Retrieve the hash by looking at recently added torrents
        # qbittorrent-api doesn't return hash from add, so we poll
        import time
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            torrents = client.torrents_info()
            for t in torrents:
                # Match by most-recently-added; this is a heuristic
                return t.hash
            time.sleep(0.5)
        raise RuntimeError("Torrent not found after adding")

    return await asyncio.get_event_loop().run_in_executor(None, _add)


async def add_torrent_ex(source: str) -> str:
    """Add torrent and reliably return its hash by comparing before/after."""
    def _add():
        client = _make_client()
        before = {t.hash for t in client.torrents_info()}

        if source.startswith("magnet:"):
            client.torrents_add(urls=source, stopped=True)
        elif source.startswith("http://") or source.startswith("https://"):
            client.torrents_add(urls=source, stopped=True)
        else:
            path = Path(source)
            with path.open("rb") as fh:
                client.torrents_add(torrent_files=fh, stopped=True)

        import time
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            after = {t.hash for t in client.torrents_info()}
            new = after - before
            if new:
                return new.pop()
            time.sleep(0.5)
        raise RuntimeError("Torrent not visible in qBittorrent after 30 seconds")

    return await asyncio.get_event_loop().run_in_executor(None, _add)


async def get_files(torrent_hash: str) -> list[dict[str, Any]]:
    """Return list of {name, size, index} for a torrent."""
    def _get():
        client = _make_client()
        files = client.torrents_files(torrent_hash=torrent_hash)
        return [
            {"index": i, "name": f.name, "size": f.size}
            for i, f in enumerate(files)
        ]

    return await asyncio.get_event_loop().run_in_executor(None, _get)


async def set_file_priority(torrent_hash: str, file_index: int, priority: int) -> None:
    """Set download priority for a specific file (0=skip, 1=normal, 7=max)."""
    def _set():
        client = _make_client()
        client.torrents_file_priority(
            torrent_hash=torrent_hash,
            file_ids=file_index,
            priority=priority,
        )
        # Resume torrent if paused and we're requesting a download
        if priority > 0:
            client.torrents_resume(torrent_hashes=torrent_hash)

    await asyncio.get_event_loop().run_in_executor(None, _set)


async def get_download_progress(torrent_hash: str, file_index: int) -> float:
    """Return download progress (0.0 – 1.0) for a specific file."""
    def _get():
        client = _make_client()
        files = client.torrents_files(torrent_hash=torrent_hash)
        for f in files:
            if f.index == file_index:
                return float(f.progress)
        return 0.0

    return await asyncio.get_event_loop().run_in_executor(None, _get)


async def wait_for_file(torrent_hash: str, file_index: int, poll: float = POLL_INTERVAL) -> None:
    """Block until the file at file_index is fully downloaded (progress == 1.0)."""
    while True:
        progress = await get_download_progress(torrent_hash, file_index)
        if progress >= 1.0:
            return
        log.debug("Waiting for %s[%d]: %.1f%%", torrent_hash, file_index, progress * 100)
        await asyncio.sleep(poll)


async def remove_torrent(torrent_hash: str, delete_files: bool = False) -> None:
    """Remove a torrent from qBittorrent (optionally delete files)."""
    def _remove():
        client = _make_client()
        client.torrents_delete(torrent_hashes=torrent_hash, delete_files=delete_files)

    await asyncio.get_event_loop().run_in_executor(None, _remove)


async def get_torrent_save_path(torrent_hash: str) -> str | None:
    """Return the save path configured in qBittorrent for the torrent."""
    def _get():
        client = _make_client()
        info = client.torrents_info(torrent_hashes=torrent_hash)
        if info:
            return info[0].save_path
        return None

    return await asyncio.get_event_loop().run_in_executor(None, _get)
