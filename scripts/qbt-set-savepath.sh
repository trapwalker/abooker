#!/bin/sh
# Runs at qBittorrent container startup (linuxserver custom-cont-init.d).
# Points qBittorrent's default save path at $QBT_SAVE_PATH so files it
# downloads land inside the volume abooker-web/bot can also see — required
# for the symlink-based cache handoff in cache/manager.link_or_copy_from_qbt.

SAVE_PATH="${QBT_SAVE_PATH:-/data/_qbt_downloads}"
CONF="/config/qBittorrent/qBittorrent.conf"

mkdir -p "$SAVE_PATH" "$(dirname "$CONF")"

if [ -f "$CONF" ] && grep -q '^Downloads\\SavePath=' "$CONF"; then
    sed -i "s#^Downloads\\\\SavePath=.*#Downloads\\\\SavePath=${SAVE_PATH}/#" "$CONF"
elif [ -f "$CONF" ] && grep -q '^\[Preferences\]' "$CONF"; then
    sed -i "/^\[Preferences\]/a Downloads\\\\SavePath=${SAVE_PATH}/" "$CONF"
elif [ -f "$CONF" ]; then
    printf '\n[Preferences]\nDownloads\\SavePath=%s/\n' "$SAVE_PATH" >> "$CONF"
else
    printf '[Preferences]\nDownloads\\SavePath=%s/\n' "$SAVE_PATH" > "$CONF"
fi
