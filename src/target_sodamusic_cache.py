#!/usr/bin/env python3
"""One-command local target workflow for SodaMusic cache export."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analyze_sodamusic_cache import (
    TrackFilter as AnalyzeTrackFilter,
    analyze_records,
    filter_report,
    indexed_candidate_matches,
    media_label,
    normalized,
    parse_target_label,
    print_track_matches,
)
from export_sodamusic_cache import DEFAULT_CACHE_DIR, parse_entries, resolve_device_node_path
from watch_sodamusic_cache import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_STABLE_SECONDS,
    TrackFilter,
    matching_cached_files,
)


EXIT_NO_MATCH = 2
EXIT_TARGET_NOT_INDEXED = 3
EXIT_MULTIPLE_TRACKS = 4


@dataclass(frozen=True)
class TargetConfig:
    cache_dir: Path
    output_dir: Path
    selection_out: Path
    query: str = ""
    track_id: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    target: str = ""
    quality: str = ""
    codec: str = ""
    extension: str = ""
    selection_format: str = "auto"
    export_when_found: bool = True
    stable_seconds: float = DEFAULT_STABLE_SECONDS
    interval: float = 3.0
    timeout: float = 0.0
    once: bool = False
    allow_size_mismatch: bool = False
    default_format: str = "auto"
    mp3_bitrate: int = 192
    device_node: Path | None = None
    raw_key: str = ""
    overwrite: bool = False
    verify_audio: bool = False
    require_output_match: bool | None = None
    export_dry_run: bool = False


WAITABLE_INDEX_STATUSES = {EXIT_NO_MATCH, EXIT_TARGET_NOT_INDEXED}


def resolve_target_filters(
    *,
    target: str = "",
    quality: str = "",
    codec: str = "",
    extension: str = "",
) -> tuple[str, str, str]:
    target_quality, target_codec, target_extension = parse_target_label(target)
    return (
        normalized(quality) or target_quality,
        normalized(codec) or target_codec,
        normalized(extension) or target_extension,
    )


def resolve_target_output_format(
    requested_format: str,
    *,
    target: str = "",
    codec: str = "",
) -> str:
    requested = normalized(requested_format) or "auto"
    if requested != "auto":
        return requested
    _target_quality, target_codec, _target_extension = parse_target_label(target)
    resolved_codec = normalized(codec) or target_codec
    if resolved_codec in {"flac", "mp3"}:
        return resolved_codec
    return "playable"


def should_require_output_match(requested: bool | None, *output_formats: str) -> bool:
    if requested is not None:
        return requested
    return any(normalized(output_format) in {"mp3", "flac"} for output_format in output_formats)


def matching_indexed_candidates(
    tracks: list[dict[str, Any]],
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
) -> list[dict[str, Any]]:
    return [
        item
        for track in tracks
        for item in track.get("indexedCandidates", [])
        if indexed_candidate_matches(item, quality=quality, codec=codec, extension=extension)
    ]


def tracks_with_matching_indexed_candidate(
    tracks: list[dict[str, Any]],
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
) -> list[dict[str, Any]]:
    return [
        track for track in tracks
        if matching_indexed_candidates([track], quality=quality, codec=codec, extension=extension)
    ]


def indexed_labels(tracks: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for track in tracks:
        for item in track.get("indexedCandidates", []):
            label = media_label(item)
            if label not in seen:
                labels.append(label)
                seen.add(label)
    return labels


def cached_labels(tracks: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for track in tracks:
        for item in track.get("cachedFiles", []):
            label = media_label(item)
            if label not in seen:
                labels.append(label)
                seen.add(label)
    return labels


def cache_match_detail(item: dict[str, Any]) -> str:
    cache_uuid = str(item.get("cacheUuid") or "")
    resource_id = str(item.get("resourceId") or "")
    source_size = item.get("sourceSize")
    indexed_size = item.get("indexedSize")
    size_part = ""
    if isinstance(source_size, int) and isinstance(indexed_size, int):
        size_part = f"{source_size}/{indexed_size} bytes"
    elif isinstance(source_size, int):
        size_part = f"{source_size} bytes"
    encrypted = "encrypted" if item.get("encrypted") else "plain"
    parts = [
        cache_uuid,
        f"resourceId={resource_id}" if resource_id else "",
        size_part,
        encrypted,
    ]
    return " | ".join(part for part in parts if part)


def analyze_target(
    cache_dir: Path,
    track_filter: TrackFilter,
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
) -> tuple[int, list[dict[str, Any]], str]:
    records = parse_entries(cache_dir / "entries.db")
    report = analyze_records(records, cache_dir)
    filtered = filter_report(
        report,
        AnalyzeTrackFilter(
            query=track_filter.query,
            track_id=track_filter.track_id,
            title=track_filter.title,
            artist=track_filter.artist,
            album=track_filter.album,
        ),
    )
    tracks = filtered["items"]
    if not tracks:
        return EXIT_NO_MATCH, tracks, "本地索引里还没有匹配歌曲；先在官方客户端里搜索或播放一次目标歌曲。"
    target_filtered_tracks: list[dict[str, Any]] = []
    if any((quality, codec, extension)):
        target_filtered_tracks = tracks_with_matching_indexed_candidate(
            tracks,
            quality=quality,
            codec=codec,
            extension=extension,
        )
        if len(tracks) > 1 and len(target_filtered_tracks) == 1:
            tracks = target_filtered_tracks
    if len(tracks) > 1:
        return (
            EXIT_MULTIPLE_TRACKS,
            tracks,
            f"匹配到 {len(tracks)} 首歌曲；请加 --track-id/--title/--artist/--album 缩小范围。",
        )
    if any((quality, codec, extension)) and not target_filtered_tracks:
        available = ", ".join(indexed_labels(tracks)) or "none"
        return EXIT_TARGET_NOT_INDEXED, tracks, f"目标品质不在本地索引里；当前索引品质: {available}"
    return 0, tracks, "目标歌曲和品质已在本地索引中。"


def build_watch_command(config: TargetConfig) -> list[str]:
    script = Path(__file__).resolve().with_name("watch_sodamusic_cache.py")
    selection_format = resolve_target_output_format(
        config.selection_format,
        target=config.target,
        codec=config.codec,
    )
    default_format = resolve_target_output_format(
        config.default_format,
        target=config.target,
        codec=config.codec,
    )
    require_output_match = should_require_output_match(
        config.require_output_match,
        selection_format,
        default_format,
    )
    command = [
        sys.executable,
        str(script),
        "--cache-dir",
        str(config.cache_dir),
        "--selection-out",
        str(config.selection_out),
        "--selection-format",
        selection_format,
        "--require-indexed",
        "--require-single-track",
        "--stable-seconds",
        f"{config.stable_seconds:g}",
        "--interval",
        f"{config.interval:g}",
    ]
    for flag, value in (
        ("--query", config.query),
        ("--track-id", config.track_id),
        ("--title", config.title),
        ("--artist", config.artist),
        ("--album", config.album),
        ("--target", config.target),
        ("--quality", config.quality),
        ("--codec", config.codec),
        ("--extension", config.extension),
    ):
        if value:
            command.extend([flag, value])
    if config.timeout > 0:
        command.extend(["--timeout", f"{config.timeout:g}"])
    if config.once:
        command.append("--once")
    if config.allow_size_mismatch:
        command.append("--allow-size-mismatch")
    if config.export_when_found:
        command.append("--export-when-found")
        command.extend(
            [
                "--output-dir",
                str(config.output_dir),
                "--default-format",
                default_format,
                "--mp3-bitrate",
                str(config.mp3_bitrate),
            ]
        )
        if config.device_node:
            command.extend(["--device-node", str(config.device_node)])
        if config.raw_key:
            command.extend(["--raw-key", config.raw_key])
        if config.overwrite:
            command.append("--overwrite")
        if config.verify_audio:
            command.append("--verify-audio")
        if require_output_match:
            command.append("--require-output-match")
        if config.export_dry_run:
            command.append("--export-dry-run")
    return command


def run_command(command: list[str], *, dry_run: bool = False) -> int:
    print(shlex.join(command), flush=True)
    if dry_run:
        return 0
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)


def print_target_context(
    tracks: list[dict[str, Any]],
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
) -> None:
    if not tracks:
        return
    track = tracks[0]
    print(
        f"目标: {track.get('artists') or 'Unknown Artist'} - "
        f"{track.get('title') or track.get('trackId')}",
        flush=True,
    )
    print(f"trackId: {track.get('trackId')}", flush=True)
    print(f"索引品质: {', '.join(indexed_labels(tracks)) or 'none'}", flush=True)
    print(f"已缓存品质: {', '.join(cached_labels(tracks)) or 'none'}", flush=True)
    cached_matches = matching_cached_files(
        tracks,
        quality=quality,
        codec=codec,
        extension=extension,
    )
    if cached_matches:
        uuids = ", ".join(str(item.get("cacheUuid") or "") for item in cached_matches)
        print(f"目标品质已缓存: {uuids}", flush=True)
        for item in cached_matches:
            print(f"目标缓存明细: {cache_match_detail(item)}", flush=True)


def wait_for_indexed_target(
    cache_dir: Path,
    track_filter: TrackFilter,
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
    wait_index: bool = False,
    once: bool = False,
    interval: float = 3.0,
    timeout: float = 0.0,
) -> tuple[int, list[dict[str, Any]], str]:
    deadline = time.monotonic() + timeout if timeout > 0 else None
    last_message = ""
    while True:
        status_code, tracks, message = analyze_target(
            cache_dir,
            track_filter,
            quality=quality,
            codec=codec,
            extension=extension,
        )
        if not wait_index or once or status_code not in WAITABLE_INDEX_STATUSES:
            return status_code, tracks, message
        if message != last_message:
            print(message, flush=True)
            last_message = message
        if deadline is not None and time.monotonic() >= deadline:
            return status_code, tracks, "等待本地索引目标超时。"
        time.sleep(max(0.5, interval))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Search one target in the local SodaMusic index, wait for the requested "
            "local cache quality, then optionally export it."
        )
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--selection-out", type=Path, help="Selection JSON path. Defaults to a temp file.")
    parser.add_argument("--query", default="", help="Search title, artists, album, and trackId.")
    parser.add_argument("--track-id", default="", help="Restrict to one indexed trackId.")
    parser.add_argument("--title", default="", help="Restrict title text.")
    parser.add_argument("--artist", default="", help="Restrict artist text.")
    parser.add_argument("--album", default="", help="Restrict album text.")
    parser.add_argument("--target", required=True, help="Target quality/codec[/extension], e.g. lossless/flac.")
    parser.add_argument("--quality", default="", help="Override target quality.")
    parser.add_argument("--codec", default="", help="Override target codec.")
    parser.add_argument("--extension", default="", help="Override target extension.")
    parser.add_argument(
        "--selection-format",
        choices=("auto", "playable", "mp3", "flac", "original"),
        default="auto",
        help=(
            "Per-item export format written into the selection file. "
            "auto maps target flac/mp3 codecs to native output."
        ),
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="Only wait and write the selection file; do not run the exporter.",
    )
    parser.add_argument("--stable-seconds", type=float, default=DEFAULT_STABLE_SECONDS)
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--timeout", type=float, default=0.0)
    parser.add_argument("--once", action="store_true", help="Analyze/watch once and exit if cache is not ready.")
    parser.add_argument(
        "--wait-index",
        action="store_true",
        help="Keep scanning until the target track and requested quality appear in the local index.",
    )
    parser.add_argument("--allow-size-mismatch", action="store_true")
    parser.add_argument("--default-format", choices=("auto", "playable", "mp3", "flac", "original"), default="auto")
    parser.add_argument("--mp3-bitrate", type=int, default=192)
    parser.add_argument("--device-node", type=Path, help="Path to SodaMusic device.node.")
    parser.add_argument("--raw-key", default="", help="Advanced fixed AES key as 32 hex characters.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--verify-audio", action="store_true")
    output_match_group = parser.add_mutually_exclusive_group()
    output_match_group.add_argument(
        "--require-output-match",
        dest="require_output_match",
        action="store_true",
        default=None,
        help="Fail the export if the probed output container/codec does not match the requested format.",
    )
    output_match_group.add_argument(
        "--no-require-output-match",
        dest="require_output_match",
        action="store_false",
        help="Do not automatically require output container/codec matching for target MP3/FLAC exports.",
    )
    parser.add_argument("--export-dry-run", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Print the watcher/export command without running it.")
    parser.add_argument("--list-matches", action="store_true", help="Print matching local-index rows before acting.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mp3_bitrate <= 0:
        raise SystemExit("--mp3-bitrate must be greater than 0")
    if args.stable_seconds < 0:
        raise SystemExit("--stable-seconds must be zero or greater")

    cache_dir = args.cache_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    selection_out = (
        args.selection_out.expanduser().resolve()
        if args.selection_out
        else Path(tempfile.gettempdir()) / "sodamusic-target-selection.json"
    )
    device_node = resolve_device_node_path(args.device_node).resolve() if args.device_node else None
    entries_db = cache_dir / "entries.db"
    if not entries_db.exists():
        raise SystemExit(f"entries.db not found: {entries_db}")

    quality, codec, extension = resolve_target_filters(
        target=args.target,
        quality=args.quality,
        codec=args.codec,
        extension=args.extension,
    )
    track_filter = TrackFilter(
        query=args.query.strip(),
        track_id=args.track_id.strip(),
        title=args.title.strip(),
        artist=args.artist.strip(),
        album=args.album.strip(),
    )

    status_code, tracks, message = wait_for_indexed_target(
        cache_dir,
        track_filter,
        quality=quality,
        codec=codec,
        extension=extension,
        wait_index=args.wait_index,
        once=args.once,
        interval=args.interval,
        timeout=args.timeout,
    )
    print(message, flush=True)
    if args.list_matches:
        records = parse_entries(entries_db)
        report = filter_report(
            analyze_records(records, cache_dir),
            AnalyzeTrackFilter(
                query=track_filter.query,
                track_id=track_filter.track_id,
                title=track_filter.title,
                artist=track_filter.artist,
                album=track_filter.album,
            ),
        )
        print_track_matches(report, limit=20)
    if status_code:
        return status_code

    print_target_context(tracks, quality=quality, codec=codec, extension=extension)
    config = TargetConfig(
        cache_dir=cache_dir,
        output_dir=output_dir,
        selection_out=selection_out,
        query=track_filter.query,
        track_id=str(tracks[0].get("trackId") or track_filter.track_id),
        title=track_filter.title,
        artist=track_filter.artist,
        album=track_filter.album,
        target=args.target,
        quality=normalized(args.quality),
        codec=normalized(args.codec),
        extension=normalized(args.extension),
        selection_format=args.selection_format,
        export_when_found=not args.no_export,
        stable_seconds=args.stable_seconds,
        interval=args.interval,
        timeout=args.timeout,
        once=args.once,
        allow_size_mismatch=args.allow_size_mismatch,
        default_format=args.default_format,
        mp3_bitrate=args.mp3_bitrate,
        device_node=device_node,
        raw_key=args.raw_key.strip().lower(),
        overwrite=args.overwrite,
        verify_audio=args.verify_audio,
        require_output_match=args.require_output_match,
        export_dry_run=args.export_dry_run,
    )
    return run_command(build_watch_command(config), dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
