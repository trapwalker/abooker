#!/bin/sh
# Runs at qBittorrent container startup (linuxserver custom-cont-init.d).
# Points qBittorrent's default save path at $QBT_SAVE_PATH so files it
# downloads land inside the volume abooker-web/bot can also see — required
# for the symlink-based cache handoff in cache/manager.link_or_copy_from_qbt.

SAVE_PATH="${QBT_SAVE_PATH:-/data/_qbt_downloads}"
CONF="/config/qBittorrent/qBittorrent.conf"

mkdir -p "$SAVE_PATH" "$(dirname "$CONF")"
# custom-cont-init.d scripts run as root before the image drops to
# PUID:PGID, so a freshly-created SAVE_PATH is root-owned 0755 — qBittorrent
# itself then runs as PUID:PGID and can't write into it at all. Every
# torrent silently errors out on file_open and gives up on the swarm
# entirely (no peers, no metadata, nothing) — this bit us for real on the
# sl2 VDS, see the network-block-sl2 memory.
chown "${PUID:-1000}:${PGID:-1000}" "$SAVE_PATH"

if [ -f "$CONF" ] && grep -q '^Downloads\\SavePath=' "$CONF"; then
    sed -i "s#^Downloads\\\\SavePath=.*#Downloads\\\\SavePath=${SAVE_PATH}/#" "$CONF"
elif [ -f "$CONF" ] && grep -q '^\[Preferences\]' "$CONF"; then
    sed -i "/^\[Preferences\]/a Downloads\\\\SavePath=${SAVE_PATH}/" "$CONF"
elif [ -f "$CONF" ]; then
    printf '\n[Preferences]\nDownloads\\SavePath=%s/\n' "$SAVE_PATH" >> "$CONF"
else
    printf '[Preferences]\nDownloads\\SavePath=%s/\n' "$SAVE_PATH" > "$CONF"
fi
