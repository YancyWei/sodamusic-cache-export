#!/usr/bin/env python3
"""Wait until a target SodaMusic track/quality exists in the local cache."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from analyze_sodamusic_cache import (
    TrackFilter,
    analyze_records,
    cached_file_matches,
    field_contains,
    indexed_candidate_matches,
    media_label,
    normalized,
    parse_target_label,
    query_terms,
    track_matches_filter,
    track_search_text,
    write_selection_file,
)
from export_sodamusic_cache import DEFAULT_CACHE_DIR, parse_entries


DEFAULT_OUTPUT_DIR = Path.home() / "Music/SodaMusic Export"
DEFAULT_STABLE_SECONDS = 1.0


def filter_tracks(
    tracks: list[dict[str, Any]],
    track_filter: TrackFilter,
) -> list[dict[str, Any]]:
    return [track for track in tracks if track_matches_filter(track, track_filter)]


def filter_tracks_by_query(
    tracks: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    return filter_tracks(tracks, TrackFilter(query=query))


def matching_cached_files(
    tracks: list[dict[str, Any]],
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
    best_per_track: bool = True,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for track in tracks:
        matches = [
            item for item in track.get("cachedFiles", [])
            if cached_file_matches(
                item,
                quality=quality,
                codec=codec,
                extension=extension,
            )
        ]
        matches.sort(
            key=lambda item: (item.get("bitrate") or 0, item.get("sourceSize") or 0),
            reverse=True,
        )
        selected = matches[:1] if best_per_track else matches
        for match in selected:
            cache_uuid = str(match.get("cacheUuid") or "")
            if cache_uuid and cache_uuid not in seen:
                seen.add(cache_uuid)
                results.append(match)
    return results


def selection_items_for_tracks(
    tracks: list[dict[str, Any]],
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
    output_format: str = "playable",
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for match in matching_cached_files(
        tracks,
        quality=quality,
        codec=codec,
        extension=extension,
    ):
        cache_uuid = str(match.get("cacheUuid") or "")
        if cache_uuid:
            items.append({"cache_uuid": cache_uuid, "format": output_format})
    return items


def cache_size_state(items: list[dict[str, Any]]) -> dict[str, int]:
    state: dict[str, int] = {}
    for item in items:
        cache_uuid = str(item.get("cacheUuid") or "")
        size = item.get("sourceSize")
        if cache_uuid and isinstance(size, int):
            state[cache_uuid] = size
    return state


def complete_cache_uuids(items: list[dict[str, Any]]) -> set[str]:
    complete: set[str] = set()
    for item in items:
        cache_uuid = str(item.get("cacheUuid") or "")
        source_size = item.get("sourceSize")
        indexed_size = item.get("indexedSize")
        if (
            cache_uuid
            and isinstance(source_size, int)
            and isinstance(indexed_size, int)
            and source_size == indexed_size
        ):
            complete.add(cache_uuid)
    return complete


def filter_items_by_cache_uuid(items: list[dict[str, str]], cache_uuids: set[str]) -> list[dict[str, str]]:
    return [item for item in items if item["cache_uuid"] in cache_uuids]


def old_cache_uuids(
    items: list[dict[str, Any]],
    *,
    wall_now: float,
    stable_seconds: float,
) -> set[str]:
    if stable_seconds <= 0:
        return set()
    old: set[str] = set()
    for item in items:
        cache_uuid = str(item.get("cacheUuid") or "")
        path = str(item.get("path") or "")
        if not cache_uuid or not path:
            continue
        try:
            mtime = Path(path).stat().st_mtime
        except OSError:
            continue
        if wall_now - mtime >= stable_seconds:
            old.add(cache_uuid)
    return old


def stable_cache_uuids(
    current_sizes: dict[str, int],
    previous: dict[str, tuple[int, float]],
    *,
    now: float,
    stable_seconds: float,
    already_stable: set[str] | None = None,
) -> tuple[set[str], dict[str, tuple[int, float]]]:
    already_stable = already_stable or set()
    next_state: dict[str, tuple[int, float]] = {}
    stable: set[str] = set()
    for cache_uuid, size in current_sizes.items():
        previous_size, first_seen = previous.get(cache_uuid, (size, now))
        if previous_size != size:
            first_seen = now
        if cache_uuid in already_stable:
            first_seen = now - stable_seconds
        next_state[cache_uuid] = (size, first_seen)
        if now - first_seen >= stable_seconds:
            stable.add(cache_uuid)
    return stable, next_state


def has_matching_indexed_candidate(
    tracks: list[dict[str, Any]],
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
) -> bool:
    return any(
        indexed_candidate_matches(
            item,
            quality=quality,
            codec=codec,
            extension=extension,
        )
        for track in tracks
        for item in track.get("indexedCandidates", [])
    )


def matching_indexed_labels(
    tracks: list[dict[str, Any]],
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
) -> list[str]:
    labels: set[str] = set()
    for track in tracks:
        for item in track.get("indexedCandidates", []):
            if not indexed_candidate_matches(
                item,
                quality=quality,
                codec=codec,
                extension=extension,
            ):
                continue
            labels.add(media_label(item))
    return sorted(labels)


def describe_tracks(tracks: list[dict[str, Any]], limit: int = 3) -> str:
    names = [
        f"{track.get('artists') or 'Unknown Artist'} - {track.get('title') or track.get('trackId')}"
        for track in tracks[:limit]
    ]
    if len(tracks) > limit:
        names.append(f"... and {len(tracks) - limit} more")
    return "; ".join(names)


def multiple_tracks_status(tracks: list[dict[str, Any]]) -> str:
    return (
        f"matched {len(tracks)} tracks; refine --track-id/--title/--artist/--album "
        f"to select one target ({describe_tracks(tracks)})"
    )


def scan_once(
    cache_dir: Path,
    *,
    query: str = "",
    track_filter: TrackFilter | None = None,
    quality: str = "",
    codec: str = "",
    extension: str = "",
    output_format: str = "playable",
) -> tuple[list[dict[str, str]], list[dict[str, Any]], dict[str, Any]]:
    records = parse_entries(cache_dir / "entries.db")
    report = analyze_records(records, cache_dir)
    effective_filter = track_filter or TrackFilter(query=query)
    tracks = filter_tracks(report["items"], effective_filter)
    items = selection_items_for_tracks(
        tracks,
        quality=quality,
        codec=codec,
        extension=extension,
        output_format=output_format,
    )
    return items, tracks, report


def build_export_command(
    *,
    cache_dir: Path,
    output_dir: Path,
    selection_file: Path,
    default_format: str,
    mp3_bitrate: int,
    device_node: Path | None = None,
    raw_key: str = "",
    overwrite: bool = False,
    verify_audio: bool = False,
    dry_run: bool = False,
    allow_size_mismatch: bool = False,
    require_output_match: bool = False,
    progress: bool = True,
) -> list[str]:
    script = Path(__file__).resolve().with_name("export_sodamusic_cache.py")
    command = [
        sys.executable,
        str(script),
        "--cache-dir",
        str(cache_dir),
        "--output-dir",
        str(output_dir),
        "--selection-file",
        str(selection_file),
        "--format",
        default_format,
        "--mp3-bitrate",
        str(mp3_bitrate),
    ]
    if device_node:
        command.extend(["--device-node", str(device_node)])
    if raw_key:
        command.extend(["--raw-key", raw_key])
    if overwrite:
        command.append("--overwrite")
    if verify_audio:
        command.append("--verify-audio")
    if require_output_match:
        command.append("--require-output-match")
    if dry_run:
        command.append("--dry-run")
    if allow_size_mismatch:
        command.append("--allow-size-mismatch")
    if progress:
        command.append("--progress")
    return command


def run_export_command(command: list[str]) -> int:
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Watch the local SodaMusic cache and write an exporter selection file "
            "once a matching cached track/quality appears."
        )
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--query",
        default="",
        help="Track search terms matched against title, artists, album, and trackId.",
    )
    parser.add_argument("--track-id", default="", help="Restrict matches to a specific indexed trackId.")
    parser.add_argument("--title", default="", help="Restrict matches to title containing this text.")
    parser.add_argument("--artist", default="", help="Restrict matches to artist text containing this text.")
    parser.add_argument("--album", default="", help="Restrict matches to album text containing this text.")
    parser.add_argument("--quality", default="", help="Required cached quality, e.g. lossless.")
    parser.add_argument("--codec", default="", help="Required cached codec, e.g. flac or aac.")
    parser.add_argument("--extension", default="", help="Required cached detected/indexed extension.")
    parser.add_argument(
        "--target",
        default="",
        help="Shortcut for --quality/--codec[/--extension], e.g. lossless/flac.",
    )
    parser.add_argument(
        "--require-indexed",
        action="store_true",
        help="Exit with code 3 if matching tracks do not advertise the requested indexed quality.",
    )
    parser.add_argument(
        "--require-single-track",
        action="store_true",
        help="Exit with code 4 if filters match more than one track.",
    )
    parser.add_argument(
        "--stable-seconds",
        type=float,
        default=DEFAULT_STABLE_SECONDS,
        help="Wait until matching cache file sizes stay unchanged for N seconds. Use 0 to disable.",
    )
    parser.add_argument(
        "--allow-size-mismatch",
        action="store_true",
        help="Allow export even when cache file size differs from the indexed media size.",
    )
    parser.add_argument(
        "--selection-out",
        type=Path,
        required=True,
        help="Write the matching exporter selection file here.",
    )
    parser.add_argument(
        "--selection-format",
        choices=("playable", "mp3", "flac", "original"),
        default="playable",
        help="Export format to write into the selection file.",
    )
    parser.add_argument(
        "--export-when-found",
        action="store_true",
        help="Run the local exporter immediately after a matching cache file is found.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Exporter output directory when --export-when-found is used.",
    )
    parser.add_argument(
        "--default-format",
        choices=("playable", "mp3", "flac", "original"),
        default="playable",
        help="Exporter default format when a selection item does not specify one.",
    )
    parser.add_argument(
        "--mp3-bitrate",
        type=int,
        default=192,
        help="Exporter MP3 bitrate in kbps when MP3 output is requested.",
    )
    parser.add_argument(
        "--device-node",
        type=Path,
        help="Path to SodaMusic's device.node for cached spade decoding.",
    )
    parser.add_argument(
        "--raw-key",
        default="",
        help="Advanced: fixed 16-byte AES key as 32 hex characters for local cache decryption.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Pass --overwrite to the exporter when --export-when-found is used.",
    )
    parser.add_argument(
        "--verify-audio",
        action="store_true",
        help="Pass --verify-audio to the exporter when --export-when-found is used.",
    )
    parser.add_argument(
        "--require-output-match",
        action="store_true",
        help="Pass --require-output-match to the exporter when --export-when-found is used.",
    )
    parser.add_argument(
        "--export-dry-run",
        action="store_true",
        help="Pass --dry-run to the exporter when --export-when-found is used.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=3.0,
        help="Seconds between scans while watching.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.0,
        help="Stop after N seconds. Use 0 to wait until interrupted.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Scan once and exit instead of waiting.",
    )
    args = parser.parse_args()
    if args.mp3_bitrate <= 0:
        parser.error("--mp3-bitrate must be greater than 0")
    if args.stable_seconds < 0:
        parser.error("--stable-seconds must be zero or greater")

    cache_dir = args.cache_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    selection_file = args.selection_out.expanduser().resolve()
    device_node = args.device_node.expanduser().resolve() if args.device_node else None
    entries_db = cache_dir / "entries.db"
    if not entries_db.exists():
        raise SystemExit(f"entries.db not found: {entries_db}")

    target_quality, target_codec, target_extension = parse_target_label(args.target)
    quality = normalized(args.quality) or target_quality
    codec = normalized(args.codec) or target_codec
    extension = normalized(args.extension) or target_extension
    track_filter = TrackFilter(
        query=args.query.strip(),
        track_id=args.track_id.strip(),
        title=args.title.strip(),
        artist=args.artist.strip(),
        album=args.album.strip(),
    )
    interval = max(0.5, args.interval)
    deadline = time.monotonic() + args.timeout if args.timeout > 0 else None
    last_status = ""
    previous_sizes: dict[str, tuple[int, float]] = {}

    while True:
        now = time.monotonic()
        try:
            items, tracks, _report = scan_once(
                cache_dir,
                track_filter=track_filter,
                quality=quality,
                codec=codec,
                extension=extension,
                output_format=args.selection_format,
            )
        except Exception as exc:
            status = f"scan failed: {exc}"
        else:
            if args.require_single_track and len(tracks) > 1:
                status = multiple_tracks_status(tracks)
                print(status, flush=True)
                return 4
            if items:
                matched_files = matching_cached_files(
                    tracks,
                    quality=quality,
                    codec=codec,
                    extension=extension,
                )
                if not args.allow_size_mismatch:
                    complete_items = filter_items_by_cache_uuid(items, complete_cache_uuids(matched_files))
                    if len(complete_items) != len(items):
                        status = "matched cached item(s), waiting for cache size to match indexed size"
                        if status != last_status:
                            print(status, flush=True)
                            last_status = status
                        if args.once:
                            return 2
                        if deadline is not None and time.monotonic() >= deadline:
                            print("timed out waiting for complete matching local cache", file=sys.stderr)
                            return 2
                        time.sleep(interval)
                        continue
                    items = complete_items
                if args.stable_seconds > 0:
                    stable_uuids, previous_sizes = stable_cache_uuids(
                        cache_size_state(matched_files),
                        previous_sizes,
                        now=now,
                        stable_seconds=args.stable_seconds,
                        already_stable=old_cache_uuids(
                            matched_files,
                            wall_now=time.time(),
                            stable_seconds=args.stable_seconds,
                        ),
                    )
                    stable_items = filter_items_by_cache_uuid(items, stable_uuids)
                    if len(stable_items) != len(items):
                        status = (
                            f"matched cached item(s), waiting for file size to stay unchanged "
                            f"for {args.stable_seconds:g}s"
                        )
                        if status != last_status:
                            print(status, flush=True)
                            last_status = status
                        if args.once:
                            return 2
                        if deadline is not None and time.monotonic() >= deadline:
                            print("timed out waiting for stable matching local cache", file=sys.stderr)
                            return 2
                        time.sleep(interval)
                        continue
                    items = stable_items
                write_selection_file(selection_file, items)
                print(f"Matched cached items: {len(items)}")
                print(f"Selection file: {selection_file}")
                print(f"Tracks: {describe_tracks(tracks)}")
                if not args.export_when_found:
                    return 0
                command = build_export_command(
                    cache_dir=cache_dir,
                    output_dir=output_dir,
                    selection_file=selection_file,
                    default_format=args.default_format,
                    mp3_bitrate=args.mp3_bitrate,
                    device_node=device_node,
                    raw_key=args.raw_key.strip().lower(),
                    overwrite=args.overwrite,
                    verify_audio=args.verify_audio,
                    require_output_match=args.require_output_match,
                    dry_run=args.export_dry_run,
                    allow_size_mismatch=args.allow_size_mismatch,
                    progress=True,
                )
                print(f"Running exporter: {' '.join(command)}", flush=True)
                return run_export_command(command)
            if tracks:
                indexed_labels = matching_indexed_labels(
                    tracks,
                    quality=quality,
                    codec=codec,
                    extension=extension,
                )
                indexed = ", ".join(indexed_labels)
                if indexed:
                    status = (
                        f"matched {len(tracks)} track(s), target quality is indexed but not cached yet "
                        f"({indexed})"
                    )
                else:
                    all_indexed = sorted({
                        media_label(item)
                        for track in tracks
                        for item in track.get("indexedCandidates", [])
                    })
                    available = ", ".join(all_indexed) if all_indexed else "none"
                    status = (
                        f"matched {len(tracks)} track(s), but requested quality is not in the local index "
                        f"(available indexed: {available})"
                    )
                    if args.require_indexed:
                        print(status, flush=True)
                        return 3
            else:
                status = "no matching track in local index yet"

        if status != last_status:
            print(status, flush=True)
            last_status = status
        if args.once:
            return 2
        if deadline is not None and time.monotonic() >= deadline:
            print("timed out waiting for matching local cache", file=sys.stderr)
            return 2
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
