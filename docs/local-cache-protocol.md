# SodaMusic Local Cache Protocol Notes

This document describes the local SodaMusic `LunaCacheV2` cache format that this
project can inspect and export. It is intentionally limited to cache files and
metadata that the official client has already written on this machine. It does
not describe SodaMusic media API signing, direct download URLs, or any server
side bypass.

## Local Files

Default macOS cache directory:

```text
/Users/yancy/Library/Application Support/SodaMusic/LunaCacheV2
```

Relevant files:

- `entries.db`: local index database. The exporter scans this file for msgpackr
  records.
- `<cacheUuid>.bin`: local media cache payload for one indexed resource.

The cache directory is treated as read-only. Exported files and manifests are
written elsewhere.

## Entry Encoding

`entries.db` stores msgpack/msgpackr-like records. The exporter finds records by
scanning for the msgpackr record marker:

```text
d4 72 40
```

The parser supports msgpackr record definitions with extension type `0x72`.
Parsed objects are accepted when they contain at least:

- `chunkId`: cache UUID, also the basename of `<cacheUuid>.bin`
- `info`: track/media metadata

Important nested metadata:

- `info.trackId`: stable track id used for grouping versions
- `info.quality`: resource quality for the current cache record
- `info.spade`: encrypted/delegated key material used by the local app helper
- `info.mediaDetail.video_model.video_list`: indexed media candidates
- `track.track.name`, `track.track.artists`, `track.track.album`: display
  metadata used for filenames and searching

## Indexed Candidates vs Cached Files

One track can have multiple indexed media candidates, but only some of them may
exist as local `.bin` files. The tool distinguishes:

- Indexed quality: present in `video_model.video_list`.
- Cached quality: a `.bin` exists and can be matched to a record.

For a cache record, the local file is:

```text
<cacheDir>/<chunkId>.bin
```

When `<chunkId>.bin` exists, the exporter selects the candidate metadata by:

1. matching `video_meta.size` to the actual local `.bin` size;
2. otherwise matching `video_meta.quality` to `info.quality`;
3. otherwise falling back to the first media candidate.

This matters because a track can show `lossless/flac` in the index while only an
AAC version is cached locally.

## Quality and Codec Labels

The project reports versions as:

```text
<quality>/<codecType>
```

Observed quality order used for selecting the best local candidate:

```text
lossless > hi_res > spatial > highest > higher > medium
```

FLAC candidates in the SodaMusic index can use:

```text
video_meta.vtype = mp4
video_meta.codec_type = flac
```

Those files are reported as `lossless/flac` with container extension `mp4`.

## File Type Detection

The `.bin` suffix is not trusted. The exporter sniffs the payload:

- `fLaC` header -> `flac`
- `ID3` or MP3 frame sync -> `mp3`
- MP4 `ftyp` box -> `m4a` or `mp4`
- indexed `codec_type=flac` inside MP4 -> `mp4`

The output extension follows the detected container, not the `.bin` filename.

## Encryption Metadata

Many local cache files are normal MP4/M4A containers with CENC AES-CTR sample
encryption. The index and MP4 boxes may expose:

- `encrypt_info.encrypt`
- `encrypt_info.encryption_method`, commonly `cenc-aes-ctr`
- `encrypt_info.kid`
- MP4 sample entry `enca`
- `sinf/frma`: original sample format such as `mp4a` or `fLaC`
- `sinf/schm`: scheme such as `cenc`
- `tenc`: default key id
- `senc`: per-sample IV/subsample encryption table
- `stsz`, `stsc`, `stco` or `co64`: sample size and offset tables

Renaming an encrypted `.bin` to `.m4a` or `.mp4` preserves the protected
container but does not make it playable.

## Spade Key Decoding

When the record carries `spade` material, the exporter asks SodaMusic's local
native helper to derive the 16-byte CENC key:

```text
/Applications/汽水音乐.app/Contents/Resources/app-arm64.asar.unpacked/device.node
```

or the matching x64/app.asar fallback path.

The helper call is local:

```javascript
const device = require(deviceNode);
device.decodeSpade(spade)
```

The result must be a 32-character hex AES key. The exporter can batch decode
spades before exporting selected records.

## Offline CENC Decryption

For encrypted MP4/M4A cache media, the exporter:

1. parses `stsz`, `stsc`, `stco` or `co64` to locate sample byte ranges;
2. parses `senc` to read per-sample IVs and optional clear/encrypted subsample
   ranges;
3. decrypts encrypted bytes with AES-CTR using the decoded 16-byte key;
4. restores encrypted sample entries:
   - `enca` -> value from `sinf/frma`
   - fallback `enca` -> `mp4a`
5. writes the decrypted container to the output path.

The `frma` restore step is required for FLAC-in-MP4 cache files. Without it, a
decrypted FLAC payload can still be mislabeled as AAC and fail decoder checks.

## Export Selection Flow

To export a specific locally cached version:

1. Search the local index:

   ```bash
   python3 src/analyze_sodamusic_cache.py \
     --query "零几年听的情歌" \
     --artist "GG啵！" \
     --target lossless/flac \
     --suggest-watcher
   ```

2. Confirm the target indexed and cached labels. Cached versions are printed as
   `quality/codec:cacheUuid`. JSON reports also include each cached version's
   `resourceId`, `indexedSize`, and selected indexed codec/extension fields.
   The target wrapper and local web target search expose the matched target
   cache file details (`cacheUuid`, `resourceId`, source/indexed size,
   encryption and spade availability) before export.
   If the search matches one track, `--suggest-watcher` prints the next watcher
   command with `--require-indexed`, `--require-single-track`, and
   `--stable-seconds`.

3. Prefer the target wrapper for one-song exports. It waits for the local index
   and cache, then maps the target codec to the output format automatically:
   `lossless/flac` writes native FLAC, `highest/mp3` writes MP3, and AAC-like
   targets keep playable music-file output. Target MP3/FLAC exports also
   require output container/codec matching automatically; pass
   `--no-require-output-match` only for diagnostics.

   ```bash
   python3 src/target_sodamusic_cache.py \
     --track-id 7496424676761061403 \
     --target lossless/flac \
     --wait-index \
     --output-dir "/Users/yancy/Music/SodaMusic Export"
   ```

4. For a target list, use the batch wrapper. It accepts JSON arrays, JSON
   objects with `items` or `targets`, JSONL, or CSV. Each row can carry a
   selector plus the requested `target`.

   ```json
   {
     "items": [
       {"query": "零几年听的情歌", "artist": "GG啵！", "target": "lossless/flac"},
       {"trackId": "7496424676761061403", "target": "highest/aac"}
     ]
   }
   ```

   ```bash
   python3 src/batch_target_sodamusic_cache.py /path/to/targets.json \
     --output-dir "/Users/yancy/Music/SodaMusic Export"
   ```

   To check the list before starting any watcher/export process, use preflight
   mode:

   ```bash
   python3 src/batch_target_sodamusic_cache.py /path/to/targets.json \
     --preflight \
     --preflight-out /tmp/sodamusic-preflight.json
   ```

   The report is written as JSON plus a sibling CSV. Status values are
   `cached`, `indexed_not_cached`, `target_not_indexed`, `no_match`, and
   `multiple_tracks`. Exit code 0 means every row has a unique local indexed
   target quality; exit code 1 means at least one row needs a narrower selector
   or more local index/cache activity in the official client.

   The same target list can be generated from any analyzer filter:

   ```bash
   python3 src/analyze_sodamusic_cache.py \
     --query "零几年听的情歌" \
     --artist "GG啵！" \
     --target lossless/flac \
     --batch-target-out /tmp/sodamusic-targets.json
   ```

   The batch wrapper invokes the same one-song local target workflow for each
   row and does not fetch direct media URLs. It writes `batch-manifest.json` and
   `batch-manifest.csv` with each target command, exit code, elapsed time, and
   the exporter manifest rows observed after each item. Existing exporter
   manifests are ignored unless they are updated during that target's run.

5. If the target quality is already cached and you want a manual selection
   file, generate one:

   ```bash
   python3 src/analyze_sodamusic_cache.py \
     --track-id 7496424676761061403 \
     --target lossless/flac \
     --selection-format flac \
     --selection-out /tmp/sodamusic-target-selection.json
   ```

6. Export:

   ```bash
   python3 src/export_sodamusic_cache.py \
     --selection-file /tmp/sodamusic-target-selection.json \
     --format flac \
     --require-output-match \
     --output-dir "/Users/yancy/Music/SodaMusic Export"
   ```

If the target quality is indexed but not cached yet, run the watcher while using
the official client to cache/play the requested quality:

```bash
python3 src/watch_sodamusic_cache.py \
  --track-id 7496424676761061403 \
  --target lossless/flac \
  --require-indexed \
  --require-single-track \
  --stable-seconds 1 \
  --selection-format flac \
  --selection-out /tmp/sodamusic-target-selection.json \
  --export-when-found \
  --output-dir "/Users/yancy/Music/SodaMusic Export" \
  --default-format flac \
  --require-output-match
```

`--require-indexed` exits with code `3` when the track exists but the requested
quality/codec is not present in the local index. `--require-single-track` exits
with code `4` when the filters match more than one track. `--stable-seconds`
waits for matching cache file sizes to remain unchanged before selection/export,
which avoids reading a file while the official client is still writing it. The
watcher also waits until the local cache file size equals the selected
`video_meta.size`; `--allow-size-mismatch` disables that check for diagnostics.

The exporter performs the same size check even when it is run directly or
through the local web UI. A mismatched file is recorded in the manifest with a
`cache size mismatch` skip reason unless `--allow-size-mismatch` is passed.
Successful and skipped manifest rows also record `source_extension`,
`indexed_extension`, `indexed_codec_type`, and `output_format`, so a dry run can
show whether a target like `lossless/flac` is a FLAC codec inside an MP4 cache
container and what output mode would be used. Successful non-original exports
also include probed output fields (`output_container`, `output_codec_type`,
`output_sample_rate`, `output_bits_per_sample`, `output_probe_error`,
`output_matches_request`, and `output_mismatch_reason`) so the manifest can
confirm what was written to disk and whether it matches the requested output
mode. Passing `--require-output-match` makes that probe enforce the requested
format: a mismatched non-original export is removed and recorded with an
`output mismatch` skip reason. The local web UI exposes the same switch as
`输出匹配`.

## Verified Local Example

Observed on this machine:

```text
trackId: 7496424676761061403
track: GG啵！ - 零几年听的情歌
indexed: lossless/flac, hi_res/aac, spatial/aac, highest/aac, higher/aac, medium/aac
cached: spatial/aac, lossless/flac, highest/aac, hi_res/aac
lossless cacheUuid: 307adb3f-d44a-49e1-b8cf-da163dd6a7f9
container: mp4
mp4 scheme: cenc
original format: fLaC
encryption method: cenc-aes-ctr
```

Dry-run export verified that the local `lossless/flac` cache can be selected,
spade-decoded, CENC-decrypted, and written as playable audio. The `playable`
format keeps an audio-friendly MP4-family container; `--format flac` uses
`ffmpeg` to write a native `.flac` file from the same local cache.

## Current Limits

- The tool only exports media already present in local `LunaCacheV2`.
- Indexed-but-not-cached versions require the official client to create the
  cache file first.
- This project does not implement SodaMusic server API authentication, media URL
  signing, or remote media downloading.
- MP3 and native FLAC output require `ffmpeg`; playable-copy verification uses
  `ffmpeg` or macOS `afconvert` when available.
