"""Async wrapper around qbittorrent-api for the operations we need."""
import asyncio
import logging
from collections.abc import Awaitable, Callable
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


async def get_swarm_diagnostics(torrent_hash: str) -> dict[str, Any]:
    """Best-effort connectivity signal for a torrent still waiting on metadata.

    `dht_nodes` / `connection_status` come from qBittorrent's *global*
    transfer state, so they say whether P2P traffic is getting out at all —
    independent of this one torrent's swarm. That matters on hosts that
    block torrent traffic at the network level (see the network-block-sl2
    memory: tracker HTTPS can be blocked while peer/DHT UDP traffic is fine,
    or vice versa) — `dht_nodes == 0` for a while is a real "network is
    blocking this" signal, not just "this torrent is slow".
    `num_seeds` / `num_leechs` are this torrent's own swarm as far as
    qBittorrent has discovered so far; they can legitimately still be 0 while
    DHT lookups are in flight.
    """
    def _get():
        client = _make_client()
        try:
            state = dict(client.sync_maindata().get("server_state", {}))
        except Exception:
            log.debug("get_swarm_diagnostics: could not fetch server_state", exc_info=True)
            state = {}
        info = client.torrents_info(torrent_hashes=torrent_hash)
        t = info[0] if info else None
        return {
            "dht_nodes": state.get("dht_nodes"),
            "connection_status": state.get("connection_status"),
            "num_seeds": getattr(t, "num_seeds", None) if t else None,
            "num_leechs": getattr(t, "num_leechs", None) if t else None,
        }

    return await asyncio.get_event_loop().run_in_executor(None, _get)


async def wait_for_metadata(
    torrent_hash: str,
    timeout: float = 60.0,
    poll: float = 2.0,
    on_progress: Callable[[float, dict[str, Any]], Awaitable[None]] | None = None,
    progress_interval: float = 15.0,
) -> list[dict[str, Any]]:
    """Poll get_files() until qBittorrent has the torrent's file list, or timeout.

    A .torrent file/URL embeds its file list, so get_files() returns it right
    away. A magnet link only carries the info-hash — the file list arrives
    later, once metadata is fetched from peers/DHT. That fetch needs the
    torrent to actually be resumed first (see resume_torrent's docstring) —
    a stopped torrent never attempts it at all, however long you wait, which
    is what actually caused every "no files found"/"no seeds found" case so
    far, not a slow or dead swarm.

    If `on_progress` is given, it's awaited roughly every `progress_interval`
    seconds with (elapsed_seconds, swarm_diagnostics) so a caller (e.g. the
    bot) can keep the user updated instead of staring at a frozen "Добавляю
    книгу…" for minutes. Returns the empty list if metadata never arrives
    within `timeout` — the caller decides what that means.
    """
    loop = asyncio.get_event_loop()
    start = loop.time()
    deadline = start + timeout
    last_report = start
    while True:
        files = await get_files(torrent_hash)
        if files:
            return files
        now = loop.time()
        if on_progress and now - last_report >= progress_interval:
            diagnostics = await get_swarm_diagnostics(torrent_hash)
            await on_progress(now - start, diagnostics)
            last_report = now
        if now >= deadline:
            return files
        await asyncio.sleep(poll)


async def get_files_status(torrent_hash: str) -> list[dict[str, Any]]:
    """Return list of {index, name, size, progress, priority} for a torrent.

    progress is 0.0-1.0, priority is qBittorrent's priority value
    (0=skip, 1=normal, ... 7=maximal). Used to render per-chapter
    download status in the bot's chapter list.
    """
    def _get():
        client = _make_client()
        files = client.torrents_files(torrent_hash=torrent_hash)
        return [
            {
                "index": i,
                "name": f.name,
                "size": f.size,
                "progress": float(f.progress),
                "priority": int(f.priority),
            }
            for i, f in enumerate(files)
        ]

    return await asyncio.get_event_loop().run_in_executor(None, _get)


async def get_torrent_speed(torrent_hash: str) -> int:
    """Return current download speed in bytes/sec for a torrent (0 if unknown)."""
    def _get():
        client = _make_client()
        info = client.torrents_info(torrent_hashes=torrent_hash)
        if info:
            return int(info[0].dlspeed)
        return 0

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


async def set_files_priority(torrent_hash: str, file_indices: list[int], priority: int) -> None:
    """Set download priority for several files in one call (see set_file_priority).

    Used right after a magnet's metadata resolves, to zero out every file at
    once — see resume_torrent's docstring for why that's needed at all.
    """
    if not file_indices:
        return

    def _set():
        client = _make_client()
        client.torrents_file_priority(
            torrent_hash=torrent_hash,
            file_ids=file_indices,
            priority=priority,
        )
        if priority > 0:
            client.torrents_resume(torrent_hashes=torrent_hash)

    await asyncio.get_event_loop().run_in_executor(None, _set)


async def resume_torrent(torrent_hash: str) -> None:
    """Resume a torrent added with stopped=True.

    Needed specifically to let a magnet fetch its metadata at all: a torrent
    stays completely inert while stopped in this qBittorrent version — no
    peer connections, no DHT lookups, nothing — which looks exactly like a
    dead/blocked swarm from the outside (confirmed live: Ubuntu's official,
    massively-seeded release ISO sat at 0 peers for 30s+ added
    stopped=True, then resolved metadata in ~4s the moment it was resumed).
    A .torrent file/URL doesn't strictly need this — its metadata is already
    embedded — but resuming it too is harmless and keeps the caller's logic
    uniform across source types; every file defaults to priority 1 (Normal)
    once metadata is known regardless of source type, so the caller must
    zero out unwanted files (see set_files_priority) right after, or a
    resumed torrent will download everything, not just the warmup head.
    """
    def _resume():
        client = _make_client()
        client.torrents_resume(torrent_hashes=torrent_hash)

    await asyncio.get_event_loop().run_in_executor(None, _resume)


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
