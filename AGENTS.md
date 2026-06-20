# AGENTS.md — SodaMusic Cache Export

This file is intended for AI coding agents working on the SodaMusic Cache Export project. It assumes no prior knowledge of the project.

## Project overview

SodaMusic Cache Export is a read-only exporter for the 汽水音乐 (SodaMusic) macOS local cache directory `LunaCacheV2`. The project parses SodaMusic's local `entries.db` index, identifies cached `.bin` media payloads, decrypts CENC-protected MP4/M4A cache files offline using SodaMusic's own native `device.node` helper, and writes playable audio files with recovered metadata, cover art, and lyrics.

Important constraints:

- The tool is read-only with respect to the cache directory. It never modifies `LunaCacheV2`.
- It only exports media already present in the local cache. It does not call SodaMusic media APIs, sign requests, or download remote files.
- Offline CENC decryption requires SodaMusic's local `device.node` and a working `node` executable.

Default macOS cache path:

```text
/Users/<user>/Library/Application Support/SodaMusic/LunaCacheV2
```

Default output path:

```text
/Users/<user>/Music/SodaMusic Export
```

## Technology stack

- **Language:** Python 3 (tested with `python3`; `python` fallback in launchers)
- **Web UI:** React + Next.js 15 static export, Tailwind CSS v4, Framer Motion, Phosphor Icons
- **Frontend build:** Source lives in `web/`; build output is copied to `src/web/` and served by the Python HTTP server
- **HTTP server:** `http.server.ThreadingHTTPServer` with a custom `BaseHTTPRequestHandler`
- **Crypto:** `pycryptodome` for AES-CTR CENC decryption
- **Metadata:** `mutagen` for embedding tags, cover art, and lyrics
- **External tools:**
  - `node` — required to load SodaMusic `device.node` for `spade` decoding
  - `ffmpeg` / `ffprobe` — required for MP3/FLAC export and strict output matching; optional otherwise
  - macOS `afconvert` — fallback audio decoder verification
  - macOS `osascript` / `swiftc` — used by the optional playback recorder
- **Recorder fallback:** `src/capture_sodamusic_audio.swift` uses `ScreenCaptureKit` to capture SodaMusic app audio

## Project structure

```text
src/
  export_sodamusic_cache.py      Core exporter: parsing, decryption, transcoding, manifests
  analyze_sodamusic_cache.py     Cache/index analyzer, selection/batch-target generators
  target_sodamusic_cache.py      One-command target workflow wrapper
  watch_sodamusic_cache.py       Polls cache until a target track/quality appears
  batch_target_sodamusic_cache.py Batch target workflow runner
  sodamusic_export_web.py        Local web UI server (127.0.0.1:8765 by default)
  start_sodamusic_export.py      Cross-platform launcher with dependency checks
  runtime_dependencies.py        Dependency check/install helpers
  record_sodamusic_playback.py   macOS fallback audio recorder
  capture_sodamusic_audio.swift  ScreenCaptureKit recorder source
  web/                           Next.js 15 frontend source
    app/                         App Router pages and components
    lib/                         API client, formatting, constants
    hooks/                       React hooks for polling and state
    package.json                 Node.js dependencies and build scripts
  src/web/                       Built frontend output (served by Python server)
    index.html
    _next/                       Next.js static assets
scripts/
  start.sh                       POSIX shell launcher
  start.command                  macOS double-click launcher
  start.bat                      Windows CMD launcher
  start.ps1                      Windows PowerShell launcher
tests/
  test_exporter.py               Unit tests for exporter, analyzer, watcher, target, batch, web, launcher, runtime deps
examples/sample-export/          Sample exported audio, manifest.csv, manifest.json
docs/local-cache-protocol.md     Detailed notes on the local cache format and export flow
requirements.txt                 Python dependencies: pycryptodome, mutagen
```

## Module divisions

### Core export logic

`export_sodamusic_cache.py` is the source of truth. It provides:

- `MsgpackrReader` — custom MessagePack reader for the msgpackr record-extension marker `\xd4\x72\x40` used in `entries.db`
- `parse_entries()` — scans `entries.db` and returns record dicts
- `source_candidate()` / `source_rows()` — matches records to local `.bin` payloads and ranks candidates by size/quality
- `sniff_extension()` — detects `flac`, `mp3`, `m4a`, `mp4` from file headers
- MP4 box parsing for CENC (`stsz`, `stsc`, `stco`, `co64`, `senc`, `tenc`, `sinf/frma`)
- `decrypt_cenc_mp4()` — AES-CTR decryption and restoration of original sample format
- `decode_spade()` / `decode_spades()` — calls SodaMusic `device.node` via `node` to derive AES keys
- `transcode_to_mp3()` / `transcode_to_flac()` / `can_decode_audio()` / `probe_audio_output()` — ffmpeg/afconvert integration
- `write_mp4_metadata()` / `write_mp3_metadata()` / `write_flac_metadata()` — mutagen-based tagging with cover/lyrics
- `export_records()` / `write_manifests()` — orchestrates export and writes `manifest.json`/`manifest.csv`

### Analysis and target workflows

- `analyze_sodamusic_cache.py` inspects cache/index state without exporting. It generates JSON/CSV reports, selection files, batch-target lists, and watcher command suggestions.
- `target_sodamusic_cache.py` resolves a single target (e.g., `lossless/flac`), optionally waits for it to appear in the index/cache, then launches the watcher/exporter.
- `watch_sodamusic_cache.py` polls `entries.db` until a target track/quality is cached and stable, writes a selection JSON, and optionally runs the exporter.
- `batch_target_sodamusic_cache.py` runs the target workflow for many tracks from JSON/JSONL/CSV input and writes a batch manifest.

### Web UI and launcher

- `sodamusic_export_web.py` binds to `127.0.0.1:8765` by default, serves the built frontend from `src/web/`, exposes JSON endpoints, and shells out to the exporter/target scripts as background jobs. It serves `/_next/` static assets and falls back unknown paths to `index.html` for SPA behavior.
- `start_sodamusic_export.py` checks/installs dependencies, starts the web server, waits until `/api/preflight-status` reports readiness, and opens the browser. It reuses an already-running service on the same port.
- `runtime_dependencies.py` checks for Python packages (`pycryptodome`, `mutagen`) and external tools (`node`, `ffmpeg`). On macOS with Homebrew, it can auto-install missing packages.

### Recorder fallback

- `record_sodamusic_playback.py` and `capture_sodamusic_audio.swift` provide a macOS-only fallback that records SodaMusic app audio via `ScreenCaptureKit`. This is independent of the cache export flow.

## Build and test commands

Install Python dependencies manually:

```bash
python3 -m pip install -r requirements.txt
```

Run the unit tests:

```bash
python3 -m pytest tests/test_exporter.py
```

The test suite currently contains 86 tests and covers exporter core logic, analyzer, watcher, target CLI, batch target, web server request handling, runtime dependency checks, and launcher readiness logic. All external tool integrations (`node`, `ffmpeg`, `device.node`, network cover downloads) are mocked.

Build the frontend (requires Node.js):

```bash
cd web
npm install
npm run build:web
```

`npm run build:web` runs `next build` and copies the output from `web/dist` to `src/web/`.

Start the web UI through the launcher:

```bash
python3 src/start_sodamusic_export.py
```

Or use the platform scripts:

```bash
scripts/start.sh
scripts/start.command   # macOS double-click
scripts/start.bat       # Windows CMD
scripts/start.ps1       # Windows PowerShell
```

Override the default port:

```bash
SODAMUSIC_EXPORT_PORT=8876 python3 src/start_sodamusic_export.py
```

Start the web server directly (no automatic dependency installation):

```bash
python3 src/sodamusic_export_web.py
```

Then open:

```text
http://127.0.0.1:8765
```

## Common CLI usage

Analyze the local cache without exporting:

```bash
python3 src/analyze_sodamusic_cache.py \
  --json-out /tmp/sodamusic-cache-analysis.json \
  --csv-out /tmp/sodamusic-cache-analysis.csv
```

Export all playable cached files:

```bash
python3 src/export_sodamusic_cache.py
```

Dry run:

```bash
python3 src/export_sodamusic_cache.py --dry-run
```

Export as MP3 or native FLAC:

```bash
python3 src/export_sodamusic_cache.py --format mp3
python3 src/export_sodamusic_cache.py --format flac --require-output-match
```

Target one song/quality:

```bash
python3 src/target_sodamusic_cache.py \
  --query "零几年听的情歌" \
  --artist "GG啵！" \
  --target lossless/flac \
  --wait-index \
  --output-dir "/Users/<user>/Music/SodaMusic Export"
```

## Code style guidelines

- **Python version:** Use Python 3 type hints and `from __future__ import annotations` at the top of each module.
- **Imports:** Standard library first, then third-party, then project modules.
- **Naming:**
  - Modules: `snake_case.py`
  - Functions/variables: `snake_case`
  - Classes/dataclasses: `PascalCase`
  - Constants: `SCREAMING_SNAKE_CASE`
- **Path handling:** Use `pathlib.Path` rather than string paths.
- **Error handling:**
  - CLI entry points use `raise SystemExit("...")` for fatal input errors.
  - Many helpers return `(success: bool, error: str)` tuples rather than raising.
  - Export errors are captured in `skipped_reason` / `metadata_error` fields instead of crashing the batch.
- **Logging:** The project does not use the `logging` module. User-facing output is via `print(..., flush=True)`, often in Chinese for launcher/web/target scripts. Keep this convention; do not introduce `logging` unless explicitly requested.
- **Subprocess:** Prefer `subprocess.run(..., check=False)` and inspect return codes.
- **CSV output:** Write UTF-8 with BOM (`utf-8-sig`) for Excel compatibility.
- **JSON output:** Use `ensure_ascii=False` for Chinese metadata.
- **Web UI:** React + Next.js 15 static export (source in `web/`), Tailwind CSS v4, Framer Motion, Phosphor Icons. The Python server serves the built output copied to `src/web/`.

## Testing instructions

- Tests live in `tests/` (test_exporter.py, test_export_core.py, test_untested_utilities.py).
- The test files insert `src/` into `sys.path` manually.
- Tests use `unittest`, `tempfile.TemporaryDirectory()`, and `unittest.mock.patch` heavily.
- They mock external binaries (`node`, `ffmpeg`, `afconvert`, `swiftc`), network requests, and subprocess calls.
- When modifying the exporter, analyzer, watcher, target, batch, web, launcher, or runtime dependency modules, run the full suite:

```bash
python3 -m pytest tests/
```

- The current suite passes with 86 tests. New features should include tests that follow the existing mock-heavy, filesystem-isolated style.

## Security considerations

- **Read-only cache access:** Never write to `LunaCacheV2`. All output goes to a user-specified output directory.
- **Local-only decryption:** The tool loads SodaMusic's own `device.node` locally via `node`. It does not ship or redistribute that binary. The `device.node` path is resolved from the installed SodaMusic app bundle.
- **No network media downloads:** The project does not implement SodaMusic server authentication, URL signing, or remote media fetching. Cover art downloads use a short timeout and limited retries; do not broaden this behavior.
- **Local web server:** `sodamusic_export_web.py` binds to `127.0.0.1` by default. Do not change this to `0.0.0.0` without explicit user request.
- **Subprocess boundaries:** The web server builds CLI commands and runs them in subprocesses. Validate all user input on the server before passing it to shell commands; the existing validators in `sodamusic_export_web.py` must be kept in sync with CLI options.
- **Temporary files:** Selection files are written to temp space and unlinked after a web job finishes. Ensure this cleanup is preserved.
- **Raw key mode:** `--raw-key` accepts a 32-character hex AES key for diagnostics. This is an advanced option; the default should remain `device.node` key derivation.

## Runtime dependencies

`requirements.txt`:

```text
pycryptodome>=3.20
mutagen>=1.47
```

System tools:

- `node` — required for encrypted cache export
- `ffmpeg` / `ffprobe` — required for MP3/FLAC and strict output matching
- macOS `afconvert` — optional fallback for playable-copy verification
- macOS `osascript` / `swiftc` — optional, for playback recording

On macOS with Homebrew, the launcher can auto-install `node` and `ffmpeg`. Use `--skip-dependency-install` to check without installing.

## Key data formats

### Selection file

A JSON file passed via `--selection-file`:

```json
{
  "items": [
    {"cache_uuid": "<uuid>", "format": "playable"},
    {"cache_uuid": "<uuid>", "format": "flac"}
  ]
}
```

### Batch target file

Accepted as JSON array, JSON object with `items`/`targets`, JSONL, or CSV. Example:

```json
{
  "items": [
    {"query": "...", "artist": "...", "target": "lossless/flac"},
    {"trackId": "...", "target": "highest/aac"}
  ]
}
```

### Manifest

The exporter writes `manifest.json` and `manifest.csv` (UTF-8-BOM) in the output directory. Each row records source/output paths, track metadata, quality, cache UUID, encryption flags, CENC diagnostics, output probe results, embedded cover/lyrics status, and any skip reason.

## Deployment process

There is no compiled build or package step. Deployment is:

1. Ensure Python 3 is available.
2. Install `requirements.txt`.
3. Install `node` (required) and `ffmpeg` (for MP3/FLAC).
4. Run the launcher or web server directly.

The repository includes platform launch scripts so non-technical users can double-click to start the web UI on macOS (`start.command`) or Windows (`start.bat`/`start.ps1`).

## Notes for agents

- Do not introduce new build tools, bundlers, or frontend frameworks for the web UI unless explicitly requested.
- Keep business logic in the CLI scripts; the web server should remain a thin wrapper that validates input and shells out.
- When adding CLI flags, mirror them in `sodamusic_export_web.py` validators and command builders.
- Preserve the read-only cache invariant and the local-only operation model.
- Maintain the existing test style: mock external binaries, use temporary directories, and assert on manifest/output state.
