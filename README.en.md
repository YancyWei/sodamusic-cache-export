# SodaMusic Cache Export

English | [简体中文](README.md)

[![Python 3](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg)](https://github.com/YancyWei/sodamusic-cache-export)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> A read-only exporter for playable audio files from local SodaMusic cache data.

SodaMusic Cache Export parses SodaMusic's local `LunaCacheV2` cache on macOS, identifies cached `.bin` media payloads, decrypts CENC-protected MP4/M4A cache files offline, and writes playable audio files with recovered metadata, cover art, and lyrics.

## Features

- Read-only access to the cache directory
- Exports only media already present in local cache
- Offline key derivation via SodaMusic's local `device.node`
- Original container export, MP3 transcoding, and FLAC output
- Metadata embedding for title, artists, album, cover art, and lyrics
- Local Web UI plus analysis, watch, target, and batch workflows
- `manifest.json` and `manifest.csv` output for auditability

## Repository layout

- `src/`: Python core scripts, web server, launcher, recorder fallback
- `web/`: Next.js 15 frontend source
- `tests/`: Python unit tests
- `docs/`: protocol and design documentation
- `examples/sample-export/`: sample manifest files

## Quick start

```bash
git clone https://github.com/YancyWei/sodamusic-cache-export.git
cd sodamusic-cache-export
python3 -m pip install -r requirements.txt
cd web
npm install
npm run build:web
cd ..
python3 src/start_sodamusic_export.py
```

The launcher serves the local UI at:

```text
http://127.0.0.1:8765
```

## Screenshot walkthrough

### 1. Browse cached tracks and export in bulk

- Check the top status card first to confirm the environment is ready and the exportable track count looks correct
- Pick the global output format: playable audio, MP3, or FLAC
- Leaving the selection count at zero and clicking `Export all` exports every currently exportable cached track
- To export only part of the list, select rows directly or narrow the list first with search and quality filters

![Browse cached tracks](docs/screenshots/guide-overview-tight.png)

### 2. Search a specific track and wait for cache availability

- Switch to `Search target track`
- Enter a song title or `trackId`, and optionally add an artist name
- Choose the desired target quality such as `lossless / flac`
- Use `Search matches` to confirm the target first; if it has not been cached locally yet, use `Wait and export`

![Search target track mode](docs/screenshots/guide-search-mode.png)

### 3. Advanced options

- `Dry run` performs parsing and preflight checks without writing output files
- `Verify audio` adds extra playback validation after export, useful when checking decryption or transcoding behavior
- These options can be toggled before running an export and do not require a separate save step

![Advanced options expanded](docs/screenshots/guide-advanced-options.png)

## Requirements

Required:

- Python 3
- `pycryptodome`
- `mutagen`

Required for offline decryption:

- `node`

Required for MP3 / FLAC export or strict verification:

- `ffmpeg`
- `ffprobe`

Optional on macOS:

- `afconvert`
- `osascript`
- `swiftc`

## Common commands

Analyze cache without exporting:

```bash
python3 src/analyze_sodamusic_cache.py \
  --json-out /tmp/sodamusic-cache-analysis.json \
  --csv-out /tmp/sodamusic-cache-analysis.csv
```

Export all playable cached files:

```bash
python3 src/export_sodamusic_cache.py
```

Export MP3:

```bash
python3 src/export_sodamusic_cache.py --format mp3
```

Export FLAC:

```bash
python3 src/export_sodamusic_cache.py --format flac --require-output-match
```

Wait for a target track and export automatically:

```bash
python3 src/target_sodamusic_cache.py \
  --query "零几年听的情歌" \
  --artist "GG啵！" \
  --target lossless/flac \
  --wait-index
```

## Tests

Python tests:

```bash
python3 -m pytest tests/
```

Frontend tests:

```bash
cd web
npm test
```

## Public repo hygiene

Current repo safeguards:

- `.gitignore` excludes `web/node_modules/`, `web/.next/`, `web/dist/`, `src/web/`, `.env*`, logs, and IDE files
- `examples/sample-export/` contains manifests only, not exported audio files
- Sample paths are anonymized as `/Users/<user>/...`

Remaining public-data caveat:

- `examples/sample-export/` still includes real song titles, album names, `track_id`, `resource_id`, and `cache_uuid`
- These are not credentials, but if you want a stricter public sample set, they should be further redacted

## Docs

- Local cache protocol notes: [docs/local-cache-protocol.md](docs/local-cache-protocol.md)
- Frontend design notes: [docs/design-taste-frontend/DESIGN.md](docs/design-taste-frontend/DESIGN.md)

## Disclaimer

- This tool reads local SodaMusic cache only and does not modify the cache
- It does not implement SodaMusic server auth, URL signing, or remote media downloads
- `device.node` is not shipped in this repository; it is loaded only from the user's local SodaMusic installation
- Use it only within lawful, personal-use boundaries

## License

[MIT](LICENSE)
