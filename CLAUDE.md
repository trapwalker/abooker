# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**abooker** started as a Python CLI tool that generates RSS feeds from a local
folder of audio files (still present, see below) and has grown into a full
self-hosted service: search audiobooks on torrent trackers via Jackett, add
them to a personal collection through a Telegram bot, and get a per-book RSS
feed whose chapters download lazily (on first request) via qBittorrent and
get cached to disk with automatic warmup-ahead and space-based eviction.

Full architecture, feature list, and environment variables: **README.md**.
Production deployment (VDS, systemd units, the shared-nginx vhost, Docker
compose for qbittorrent+jackett): **deploy/DEPLOY.md**.

## Setup & Running

```bash
# Install dependencies and set up venv
uv sync

# Run CLI
uv run abooker -d path/to/audiobook_folder --url=http://my_host/path/to/abooks/

# Run with all metadata options
uv run abooker -d path/to/audiobook_folder \
    --url=http://my_host/path/to/abooks/ \
    --title="Book Title" --author="Author Name"
```

There are no tests or linting configs currently in the project.

## Running the service

```bash
make install          # uv venv + editable install with [all] extras
make dev-web           # uvicorn --reload, port 8000
make dev-bot            # aiogram long polling
make dev                 # both, via make -j2
```

`docker compose up -d` runs everything (web, bot, qbittorrent, jackett) in
Docker for local testing. In production only qbittorrent+jackett are
Dockerized (`docker-compose.vds.yml`); web/bot run natively via systemd — see
deploy/DEPLOY.md for why (small, shared VDS).

## CLI Options

- `-d / --dir` (required): Path to the audiobook directory
- `-u / --url`: Base URL for serving audio files via HTTP
- `--rss`: Output RSS filename (default: `playlist.rss`)
- `--title`, `--author`, `--description`, `--image`, `--lang`, `--link`: Metadata
- `--no-local-settings`: Skip loading parent folder settings
- `-S / --save-local-settings`: Save settings to parent folder's `.abooker` file
- `--verbose`: Print debug info about loaded settings and discovered files

## Package Structure

```
src/abooker/
    __init__.py     — version
    cli.py          — click entry point (main())
    rss.py          — make_rss(): builds RSS 2.0 XML
    settings.py     — load_settings(), save_settings(), settings filenames
    files.py        — media type maps, file masks, iter_files(), filename_key(),
                       build_chapter_aliases(), mask_case_fix()
    format.py       — fmt_bytes()/fmt_duration(), shared by the bot's messages

    core/           — Settings (pydantic-settings), SQLAlchemy async ORM + lightweight
                       ALTER TABLE migrations, make_slug()/unique_slug()
    torrent/        — async qbittorrent-api wrapper; create_publication() (torrent →
                       DB records + RSS + publication.json)
    cache/          — warmup/eviction/disk-quota/background-download-finalize logic
    search/         — Jackett API wrapper (torznab search; indexer admin config needs
                       a session cookie, not the apikey — see deploy/DEPLOY.md)
    web/            — FastAPI app: /publications, /rss/{slug}, /file/{slug}/{path}
    bot/            — aiogram 3: search/list/status/auth/chapters handlers, keyboards

scripts/
    download.py             — yt-dlp example: download YouTube audio as MP3
    jackett-set-apikey.sh   — Jackett container init: pins JACKETT_KEY
    qbt-set-savepath.sh     — qBittorrent container init: default save path
                               matches the volume abooker-web/bot can also see
    qb/start.sh             — old standalone qBittorrent script, unrelated to the
                               current docker-compose setup (different image)
```

## Architecture

**Settings hierarchy** (higher overrides lower):
1. CLI arguments
2. Book settings (`.abook` YAML file inside the audiobook folder)
3. Local settings (`.abooker` or `.abook*` YAML file in the *parent* folder — shared across multiple books)
4. Defaults

**File discovery** (`files.iter_files`): Case-insensitive glob patterns find media files (aac, mp3, ogg, wma, wav, mp4), cover images (jpg, jpeg, png), and description files (readme.*, info.*, about.*, *.txt, *.md, etc.).

**Natural sort** (`files.filename_key`): Numeric-aware ordering so track01, track02... track10 sorts correctly.

**RSS generation** (`rss.make_rss`): RSS 2.0 with `xmlns:itunes` and `xmlns:googleplay` namespaces. Each audio file becomes an `<item>` with an `<enclosure>` tag. Image and description auto-populate from folder contents if not specified.

**URL construction**: File paths are URL-encoded with `urllib.parse.quote`. The base `--url` points to the *parent* of the audiobook folder (not the folder itself), and relative paths are built from there.

**Settings persistence**: After each run, book metadata is saved back to `.abook` in the book folder. Local settings only saved when `-S` flag is used.
