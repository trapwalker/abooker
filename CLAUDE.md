# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**abooker** is a Python CLI tool that generates RSS feeds for audiobooks, making local audio files subscribable as podcasts. It scans a directory of audio files, builds a valid RSS 2.0 feed (with iTunes and Google Play podcast extensions), and writes it to a `playlist.rss` file.

The project is being extended with additional services: Telegram bot, torrent downloader, web server with lazy-loading API.

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
    files.py        — media type maps, file masks, iter_files(), filename_key(), mask_case_fix()

scripts/
    download.py     — yt-dlp example: download YouTube audio as MP3
    qb/start.sh     — Docker qBittorrent container startup
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
