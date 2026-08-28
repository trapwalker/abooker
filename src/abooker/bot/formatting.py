"""Shared message text for the two book-creation flows (handlers/default.py
for a direct magnet/.torrent/URL, handlers/commands.py's cb_publish_idx for
picking a /search result) so they can't drift out of sync on the same text,
plus the status badge also used by /list and the chapters "« К книге" view.
"""
from abooker.core.config import settings
from abooker.core.models import Publication, PublicationStatus

STATUS_LABEL = {
    PublicationStatus.pending_metadata: "⏳ ждём метаданные",
    PublicationStatus.warming_up: "🔥 прогревается",
    PublicationStatus.ready: None,  # normal state, no badge needed
}


def status_line(pub: Publication) -> str:
    label = STATUS_LABEL.get(pub.status)
    return f"\n{label}" if label else ""


def format_metadata_progress(elapsed: float, diag: dict) -> str:
    lines = [f"Добавляю книгу… жду метаданные раздачи ({elapsed:.0f} с)"]
    dht_nodes = diag.get("dht_nodes")
    if dht_nodes is not None:
        lines.append(f"DHT-узлов видно: {dht_nodes}")
    seeds, leechs = diag.get("num_seeds"), diag.get("num_leechs")
    if seeds is not None:
        lines.append(f"Сидов/личей у раздачи: {seeds}/{leechs}")
    return "\n".join(lines)


def format_ready_text(pub: Publication, is_new: bool) -> str:
    """Text for the status message once the publication is usable — i.e.
    metadata resolved and RSS actually exists on disk. Includes the RSS link
    directly (no button/extra tap needed to get it)."""
    rss_url = f"{settings.WEB_BASE_URL}/rss/{pub.slug}"
    prefix = "Книга добавлена" if is_new else "Книга уже есть, добавлена к вашим"
    return (
        f"{prefix}: <b>{pub.title}</b>\n"
        f"RSS: <code>{rss_url}</code>\n"
        f"Файлов: {pub.total_files}{status_line(pub)}"
    )
