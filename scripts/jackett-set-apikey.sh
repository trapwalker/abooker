#!/bin/sh
# Runs at Jackett container startup (linuxserver custom-cont-init.d).
# Sets the API key from the JACKETT_KEY environment variable so the key
# can be defined once in .env and shared with abooker.

[ -z "$JACKETT_KEY" ] && exit 0

CONFIG_FILE="/config/Jackett/ServerConfig.json"

mkdir -p /config/Jackett

if [ -f "$CONFIG_FILE" ]; then
    sed -i "s/\"APIKey\": \"[^\"]*\"/\"APIKey\": \"${JACKETT_KEY}\"/" "$CONFIG_FILE"
else
    printf '{"APIKey": "%s"}\n' "${JACKETT_KEY}" > "$CONFIG_FILE"
fi

# This script runs as root (custom-cont-init.d, before the s6 su-exec to
# PUID/PGID) — without this chown, a freshly-created config file is
# root-owned and the Jackett process (running as PUID/PGID) can't write its
# own config back on startup ("Error writing config file").
chown -R "${PUID:-1000}:${PGID:-1000}" /config/Jackett
