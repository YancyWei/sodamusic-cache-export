#!/usr/bin/env python3
"""Run local SodaMusic target exports from a JSON/JSONL/CSV target list."""

from __future__ import annotations

import argparse
import csv
import json
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from export_sodamusic_cache import DEFAULT_CACHE_DIR, resolve_device_node_path
from target_sodamusic_cache import (
    EXIT_MULTIPLE_TRACKS,
    EXIT_NO_MATCH,
    EXIT_TARGET_NOT_INDEXED,
    analyze_target,
    cache_match_detail,
    cached_labels,
    indexed_labels,
    matching_cached_files,
    resolve_target_filters,
)
from watch_sodamusic_cache import DEFAULT_OUTPUT_DIR, DEFAULT_STABLE_SECONDS, TrackFilter


SUCCESS = 0
FAILED = 1


@dataclass(frozen=True)
class BatchTarget:
    query: str = ""
    track_id: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    target: str = ""
    output_dir: Path | None = None
    timeout: float | None = None
    once: bool | None = None
    wait_index: bool | None = None
    no_export: bool | None = None
    overwrite: bool | None = None
    verify_audio: bool | None = None
    export_dry_run: bool | None = None
    allow_size_mismatch: bool | None = None
    require_output_match: bool | None = None
    selection_format: str = ""
    default_format: str = ""


@dataclass(frozen=True)
class BatchOptions:
    cache_dir: Path = DEFAULT_CACHE_DIR
    output_dir: Path = DEFAULT_OUTPUT_DIR
    device_node: Path | None = None
    raw_key: str = ""
    stable_seconds: float = DEFAULT_STABLE_SECONDS
    interval: float = 3.0
    timeout: float = 0.0
    once: bool = False
    wait_index: bool = True
    no_export: bool = False
    overwrite: bool = False
    verify_audio: bool = False
    export_dry_run: bool = False
    allow_size_mismatch: bool = False
    require_output_match: bool | None = None
    selection_format: str = "auto"
    default_format: str = "auto"
    mp3_bitrate: int = 192
    continue_on_error: bool = True
    dry_run: bool = False
    batch_manifest: Path | None = None
    preflight_out: Path | None = None


def normalized_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def item_value(item: dict[str, Any], *keys: str) -> str:
    by_normalized_key = {normalized_key(str(key)): value for key, value in item.items()}
    for key in keys:
        value = by_normalized_key.get(normalized_key(key))
        if value is not None:
            return str(value).strip()
    return ""


def item_bool(item: dict[str, Any], *keys: str) -> bool | None:
    raw = item_value(item, *keys)
    if raw == "":
        return None
    normalized = raw.lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"invalid boolean value for {'/'.join(keys)}: {raw}")


def item_float(item: dict[str, Any], *keys: str) -> float | None:
    raw = item_value(item, *keys)
    if raw == "":
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"invalid numeric value for {'/'.join(keys)}: {raw}") from exc


def target_from_item(item: dict[str, Any], index: int) -> BatchTarget:
    target = BatchTarget(
        query=item_value(item, "query", "keyword", "keywords", "search"),
        track_id=item_value(item, "track_id", "trackId", "id"),
        title=item_value(item, "title", "song", "name"),
        artist=item_value(item, "artist", "artists", "singer"),
        album=item_value(item, "album"),
        target=item_value(item, "target", "quality_codec", "qualityCodec", "version"),
        output_dir=Path(value).expanduser() if (value := item_value(item, "output_dir", "outputDir")) else None,
        timeout=item_float(item, "timeout"),
        once=item_bool(item, "once"),
        wait_index=item_bool(item, "wait_index", "waitIndex"),
        no_export=item_bool(item, "no_export", "noExport"),
        overwrite=item_bool(item, "overwrite"),
        verify_audio=item_bool(item, "verify_audio", "verifyAudio"),
        export_dry_run=item_bool(item, "export_dry_run", "exportDryRun"),
        allow_size_mismatch=item_bool(item, "allow_size_mismatch", "allowSizeMismatch"),
        require_output_match=item_bool(item, "require_output_match", "requireOutputMatch"),
        selection_format=item_value(item, "selection_format", "selectionFormat"),
        default_format=item_value(item, "default_format", "defaultFormat", "format"),
    )
    if not target.target:
        raise ValueError(f"target item {index} is missing target")
    if not any((target.query, target.track_id, target.title, target.artist, target.album)):
        raise ValueError(f"target item {index} is missing a selector")
    return target


def load_json_targets(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("items") or payload.get("targets") or []
    if not isinstance(payload, list):
        raise ValueError("JSON target list must be an array or contain an items/targets array")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError("each JSON target list item must be an object")
    return list(payload)


def load_jsonl_targets(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        item = json.loads(stripped)
        if not isinstance(item, dict):
            raise ValueError(f"JSONL line {line_number} must be an object")
        items.append(item)
    return items


def load_csv_targets(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def load_targets(path: Path) -> list[BatchTarget]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        raw_items = load_csv_targets(path)
    elif suffix in {".jsonl", ".ndjson"}:
        raw_items = load_jsonl_targets(path)
    else:
        raw_items = load_json_targets(path)
    return [target_from_item(item, index) for index, item in enumerate(raw_items, start=1)]


def target_command(target: BatchTarget, options: BatchOptions) -> list[str]:
    script = Path(__file__).resolve().with_name("target_sodamusic_cache.py")
    command = [
        sys.executable,
        str(script),
        "--cache-dir",
        str(options.cache_dir),
        "--output-dir",
        str(target.output_dir or options.output_dir),
        "--target",
        target.target,
        "--stable-seconds",
        f"{options.stable_seconds:g}",
        "--interval",
        f"{options.interval:g}",
        "--default-format",
        target.default_format or options.default_format,
        "--selection-format",
        target.selection_format or options.selection_format,
        "--mp3-bitrate",
        str(options.mp3_bitrate),
    ]
    for flag, value in (
        ("--query", target.query),
        ("--track-id", target.track_id),
        ("--title", target.title),
        ("--artist", target.artist),
        ("--album", target.album),
    ):
        if value:
            command.extend([flag, value])
    timeout = target.timeout if target.timeout is not None else options.timeout
    wait_index = target.wait_index if target.wait_index is not None else options.wait_index
    once = target.once if target.once is not None else options.once
    no_export = target.no_export if target.no_export is not None else options.no_export
    allow_size_mismatch = (
        target.allow_size_mismatch
        if target.allow_size_mismatch is not None
        else options.allow_size_mismatch
    )
    overwrite = target.overwrite if target.overwrite is not None else options.overwrite
    verify_audio = target.verify_audio if target.verify_audio is not None else options.verify_audio
    export_dry_run = (
        target.export_dry_run
        if target.export_dry_run is not None
        else options.export_dry_run
    )

    if timeout:
        command.extend(["--timeout", f"{timeout:g}"])
    if wait_index:
        command.append("--wait-index")
    if once:
        command.append("--once")
    if no_export:
        command.append("--no-export")
    if allow_size_mismatch:
        command.append("--allow-size-mismatch")
    if overwrite:
        command.append("--overwrite")
    if verify_audio:
        command.append("--verify-audio")
    if export_dry_run:
        command.append("--export-dry-run")
    require_output_match = (
        target.require_output_match
        if target.require_output_match is not None
        else options.require_output_match
    )
    if require_output_match is True:
        command.append("--require-output-match")
    elif require_output_match is False:
        command.append("--no-require-output-match")
    if options.device_node:
        command.extend(["--device-node", str(options.device_node)])
    if options.raw_key:
        command.extend(["--raw-key", options.raw_key])
    if options.dry_run:
        command.append("--dry-run")
    return command


def target_output_dir(target: BatchTarget, options: BatchOptions) -> Path:
    return target.output_dir or options.output_dir


def exporter_manifest_path(output_dir: Path, *, dry_run: bool) -> Path:
    return output_dir / ("manifest.dry-run.json" if dry_run else "manifest.json")


def read_export_manifest(
    output_dir: Path,
    *,
    dry_run: bool,
    not_before: float | None = None,
) -> list[dict[str, Any]]:
    manifest_path = exporter_manifest_path(output_dir, dry_run=dry_run)
    if not manifest_path.exists():
        return []
    if not_before is not None:
        try:
            if manifest_path.stat().st_mtime < not_before:
                return []
        except OSError:
            return []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def target_summary(target: BatchTarget) -> dict[str, Any]:
    return {
        "query": target.query,
        "track_id": target.track_id,
        "title": target.title,
        "artist": target.artist,
        "album": target.album,
        "target": target.target,
    }


def target_track_filter(target: BatchTarget) -> TrackFilter:
    return TrackFilter(
        query=target.query,
        track_id=target.track_id,
        title=target.title,
        artist=target.artist,
        album=target.album,
    )


def target_label(target: BatchTarget) -> str:
    return target.track_id or target.query or target.title or target.artist or target.album


def preflight_status(status_code: int, *, target_cached: bool) -> str:
    if status_code == SUCCESS:
        return "cached" if target_cached else "indexed_not_cached"
    if status_code == EXIT_NO_MATCH:
        return "no_match"
    if status_code == EXIT_TARGET_NOT_INDEXED:
        return "target_not_indexed"
    if status_code == EXIT_MULTIPLE_TRACKS:
        return "multiple_tracks"
    return "error"


def track_summary(track: dict[str, Any]) -> dict[str, Any]:
    return {
        "trackId": str(track.get("trackId") or ""),
        "title": str(track.get("title") or ""),
        "artists": str(track.get("artists") or ""),
        "album": str(track.get("album") or ""),
        "indexedQualities": indexed_labels([track]),
        "cachedQualities": cached_labels([track]),
    }


def cache_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "cacheUuid": str(item.get("cacheUuid") or ""),
        "resourceId": str(item.get("resourceId") or ""),
        "quality": str(item.get("quality") or ""),
        "codecType": str(item.get("codecType") or ""),
        "extension": str(item.get("detectedExtension") or item.get("extension") or ""),
        "sourceSize": item.get("sourceSize") if isinstance(item.get("sourceSize"), int) else None,
        "indexedSize": item.get("indexedSize") if isinstance(item.get("indexedSize"), int) else None,
        "encrypted": bool(item.get("encrypted")),
        "detail": cache_match_detail(item),
    }


def preflight_target(index: int, target: BatchTarget, options: BatchOptions) -> dict[str, Any]:
    quality, codec, extension = resolve_target_filters(target=target.target)
    status_code, tracks, message = analyze_target(
        options.cache_dir,
        target_track_filter(target),
        quality=quality,
        codec=codec,
        extension=extension,
    )
    cached_matches = matching_cached_files(
        tracks,
        quality=quality,
        codec=codec,
        extension=extension,
    )
    return {
        "index": index,
        "target": target_summary(target),
        "target_filter": {
            "quality": quality,
            "codec": codec,
            "extension": extension,
        },
        "status_code": status_code,
        "status": preflight_status(status_code, target_cached=bool(cached_matches)),
        "message": message,
        "track_count": len(tracks),
        "matches": [track_summary(track) for track in tracks],
        "indexed_qualities": indexed_labels(tracks),
        "cached_qualities": cached_labels(tracks),
        "target_cached": bool(cached_matches),
        "target_cache_uuids": [
            str(item.get("cacheUuid") or "") for item in cached_matches if item.get("cacheUuid")
        ],
        "target_cache_details": [cache_summary(item) for item in cached_matches],
    }


def write_preflight_report(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = path.with_suffix(".csv")
    fieldnames = [
        "index",
        "target",
        "selector",
        "status",
        "status_code",
        "track_count",
        "target_cached",
        "track_ids",
        "indexed_qualities",
        "cached_qualities",
        "target_cache_uuids",
        "message",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            target_info = row.get("target") if isinstance(row.get("target"), dict) else {}
            matches = row.get("matches") if isinstance(row.get("matches"), list) else []
            selector = (
                target_info.get("track_id")
                or target_info.get("query")
                or target_info.get("title")
                or target_info.get("artist")
                or ""
            )
            writer.writerow(
                {
                    "index": row.get("index"),
                    "target": target_info.get("target") or "",
                    "selector": selector,
                    "status": row.get("status") or "",
                    "status_code": row.get("status_code"),
                    "track_count": row.get("track_count"),
                    "target_cached": row.get("target_cached"),
                    "track_ids": ",".join(str(item.get("trackId") or "") for item in matches),
                    "indexed_qualities": ",".join(row.get("indexed_qualities") or []),
                    "cached_qualities": ",".join(row.get("cached_qualities") or []),
                    "target_cache_uuids": ",".join(row.get("target_cache_uuids") or []),
                    "message": row.get("message") or "",
                }
            )


def write_batch_manifest(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = path.with_suffix(".csv")
    fieldnames = [
        "index",
        "target",
        "selector",
        "returncode",
        "elapsed_seconds",
        "output_dir",
        "command",
        "exported_count",
        "skipped_count",
        "manifest_path",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            exports = row.get("exports") if isinstance(row.get("exports"), list) else []
            target_info = row.get("target") if isinstance(row.get("target"), dict) else {}
            selector = (
                target_info.get("track_id")
                or target_info.get("query")
                or target_info.get("title")
                or target_info.get("artist")
                or ""
            )
            writer.writerow(
                {
                    "index": row.get("index"),
                    "target": target_info.get("target") or "",
                    "selector": selector,
                    "returncode": row.get("returncode"),
                    "elapsed_seconds": row.get("elapsed_seconds"),
                    "output_dir": row.get("output_dir") or "",
                    "command": row.get("command") or "",
                    "exported_count": sum(1 for item in exports if item.get("copied")),
                    "skipped_count": sum(1 for item in exports if item.get("skipped_reason")),
                    "manifest_path": row.get("manifest_path") or "",
                }
            )


def run_batch(targets: list[BatchTarget], options: BatchOptions) -> int:
    failures = 0
    batch_rows: list[dict[str, Any]] = []
    batch_manifest = options.batch_manifest or options.output_dir / "batch-manifest.json"
    for index, target in enumerate(targets, start=1):
        command = target_command(target, options)
        label = target_label(target)
        output_dir = target_output_dir(target, options)
        export_dry_run = (
            target.export_dry_run
            if target.export_dry_run is not None
            else options.export_dry_run
        )
        print(f"[{index}/{len(targets)}] {label} -> {target.target}", flush=True)
        print(shlex.join(command), flush=True)
        started = time.monotonic()
        wall_started = time.time()
        if options.dry_run:
            returncode = 0
        else:
            returncode = subprocess.run(command, check=False).returncode
        elapsed = time.monotonic() - started
        exports = [] if options.dry_run else read_export_manifest(
            output_dir,
            dry_run=export_dry_run,
            not_before=wall_started,
        )
        row = {
            "index": index,
            "target": target_summary(target),
            "returncode": returncode,
            "elapsed_seconds": round(elapsed, 3),
            "output_dir": str(output_dir),
            "command": command,
            "manifest_path": str(exporter_manifest_path(output_dir, dry_run=export_dry_run)),
            "exports": exports,
        }
        batch_rows.append(row)
        write_batch_manifest(batch_manifest, batch_rows)
        print(f"[{index}/{len(targets)}] exit={returncode} elapsed={elapsed:.1f}s", flush=True)
        if returncode:
            failures += 1
            if not options.continue_on_error:
                return returncode
    return SUCCESS if failures == 0 else FAILED


def run_preflight(targets: list[BatchTarget], options: BatchOptions) -> int:
    rows: list[dict[str, Any]] = []
    report_path = options.preflight_out or options.output_dir / "batch-preflight.json"
    for index, target in enumerate(targets, start=1):
        row = preflight_target(index, target, options)
        rows.append(row)
        write_preflight_report(report_path, rows)
        cache_hint = ""
        if row["target_cache_uuids"]:
            cache_hint = f" cache={','.join(row['target_cache_uuids'])}"
        print(
            f"[{index}/{len(targets)}] {target_label(target)} -> {target.target}: "
            f"{row['status']} ({row['message']}){cache_hint}",
            flush=True,
        )
    failures = sum(1 for row in rows if row.get("status_code") != SUCCESS)
    print(f"Preflight report: {report_path}", flush=True)
    print(f"Preflight CSV: {report_path.with_suffix('.csv')}", flush=True)
    return SUCCESS if failures == 0 else FAILED


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run local SodaMusic target exports from a JSON/JSONL/CSV target list."
    )
    parser.add_argument("target_list", type=Path, help="JSON/JSONL/CSV target list.")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--device-node", type=Path, help="Path to SodaMusic device.node.")
    parser.add_argument("--raw-key", default="", help="Advanced fixed AES key as 32 hex characters.")
    parser.add_argument("--stable-seconds", type=float, default=DEFAULT_STABLE_SECONDS)
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--timeout", type=float, default=0.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-wait-index", action="store_true")
    parser.add_argument("--no-export", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--verify-audio", action="store_true")
    parser.add_argument("--export-dry-run", action="store_true")
    parser.add_argument("--allow-size-mismatch", action="store_true")
    output_match_group = parser.add_mutually_exclusive_group()
    output_match_group.add_argument("--require-output-match", dest="require_output_match", action="store_true", default=None)
    output_match_group.add_argument("--no-require-output-match", dest="require_output_match", action="store_false")
    parser.add_argument("--selection-format", choices=("auto", "playable", "mp3", "flac", "original"), default="auto")
    parser.add_argument("--default-format", choices=("auto", "playable", "mp3", "flac", "original"), default="auto")
    parser.add_argument("--mp3-bitrate", type=int, default=192)
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Print per-target commands without running them.")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Only inspect the local index/cache for each target and write a preflight report.",
    )
    parser.add_argument(
        "--preflight-out",
        type=Path,
        help="Write preflight report JSON here. Defaults to output-dir/batch-preflight.json.",
    )
    parser.add_argument(
        "--batch-manifest",
        type=Path,
        help="Write batch manifest JSON here. Defaults to output-dir/batch-manifest.json.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mp3_bitrate <= 0:
        raise SystemExit("--mp3-bitrate must be greater than 0")
    if args.stable_seconds < 0:
        raise SystemExit("--stable-seconds must be zero or greater")
    if args.interval < 0.5:
        raise SystemExit("--interval must be at least 0.5")
    if args.timeout < 0:
        raise SystemExit("--timeout must be zero or greater")

    target_list = args.target_list.expanduser().resolve()
    if not target_list.exists():
        raise SystemExit(f"target list not found: {target_list}")
    cache_dir = args.cache_dir.expanduser().resolve()
    entries_db = cache_dir / "entries.db"
    if not entries_db.exists():
        raise SystemExit(f"entries.db not found: {entries_db}")
    device_node = resolve_device_node_path(args.device_node).resolve() if args.device_node else None
    targets = load_targets(target_list)
    if not targets:
        raise SystemExit("target list is empty")

    options = BatchOptions(
        cache_dir=cache_dir,
        output_dir=args.output_dir.expanduser().resolve(),
        device_node=device_node,
        raw_key=args.raw_key.strip().lower(),
        stable_seconds=args.stable_seconds,
        interval=args.interval,
        timeout=args.timeout,
        once=args.once,
        wait_index=not args.no_wait_index,
        no_export=args.no_export,
        overwrite=args.overwrite,
        verify_audio=args.verify_audio,
        export_dry_run=args.export_dry_run,
        allow_size_mismatch=args.allow_size_mismatch,
        require_output_match=args.require_output_match,
        selection_format=args.selection_format,
        default_format=args.default_format,
        mp3_bitrate=args.mp3_bitrate,
        continue_on_error=not args.stop_on_error,
        dry_run=args.dry_run,
        batch_manifest=args.batch_manifest.expanduser().resolve() if args.batch_manifest else None,
        preflight_out=args.preflight_out.expanduser().resolve() if args.preflight_out else None,
    )
    if args.preflight:
        return run_preflight(targets, options)
    return run_batch(targets, options)


if __name__ == "__main__":
    raise SystemExit(main())
