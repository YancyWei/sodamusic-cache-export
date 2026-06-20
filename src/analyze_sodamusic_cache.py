#!/usr/bin/env python3
"""Inspect local SodaMusic LunaCacheV2 index/cache state without downloading media."""

from __future__ import annotations

import argparse
import csv
import json
import shlex
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from export_sodamusic_cache import (
    DEFAULT_CACHE_DIR,
    candidate_sort_key,
    cached_candidates_for_track,
    compact_error,
    indexed_candidate_sort_key,
    is_encrypted,
    mp4_encryption_summary,
    parse_entries,
    record_indexed_size,
    record_spade,
    selected_video_item,
    sniff_extension,
    source_candidate,
    track_identity,
    video_items,
)


@dataclass(frozen=True)
class TrackFilter:
    query: str = ""
    track_id: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""


def media_label(value: dict[str, Any]) -> str:
    quality = str(value.get("quality") or "")
    codec = str(value.get("codecType") or value.get("codec_type") or "")
    extension = str(value.get("extension") or value.get("vtype") or "")
    parts = [part for part in (quality, codec or extension) if part]
    return "/".join(parts) if parts else "unknown"


def unique_media_labels(items: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for item in items:
        label = media_label(item)
        if label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


def cached_version_summaries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    versions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        label = media_label(item)
        cache_uuid = str(item.get("cacheUuid") or "")
        key = (label, cache_uuid)
        if key in seen:
            continue
        seen.add(key)
        versions.append(
            {
                "label": label,
                "cacheUuid": cache_uuid,
                "quality": str(item.get("quality") or ""),
                "codecType": str(item.get("codecType") or ""),
                "extension": str(item.get("detectedExtension") or item.get("extension") or ""),
                "bitrate": item.get("bitrate") if isinstance(item.get("bitrate"), int) else None,
                "sourceSize": item.get("sourceSize") if isinstance(item.get("sourceSize"), int) else None,
                "indexedSize": item.get("indexedSize") if isinstance(item.get("indexedSize"), int) else None,
                "resourceId": str(item.get("resourceId") or ""),
                "encrypted": bool(item.get("encrypted")),
            }
        )
    return versions


def normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def parse_target_label(target: str) -> tuple[str, str, str]:
    parts = [normalized(part) for part in str(target or "").split("/") if normalized(part)]
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return parts[0], parts[1], parts[2]


def query_terms(query: str) -> list[str]:
    return [term for term in normalized(query).split() if term]


def track_search_text(track: dict[str, Any]) -> str:
    return normalized(
        " ".join(
            str(track.get(key) or "")
            for key in ("trackId", "title", "artists", "album")
        )
    )


def field_contains(track: dict[str, Any], field: str, value: str) -> bool:
    needle = normalized(value)
    if not needle:
        return True
    return needle in normalized(track.get(field))


def track_matches_filter(track: dict[str, Any], track_filter: TrackFilter) -> bool:
    terms = query_terms(track_filter.query)
    if terms and not all(term in track_search_text(track) for term in terms):
        return False
    return (
        field_contains(track, "trackId", track_filter.track_id)
        and field_contains(track, "title", track_filter.title)
        and field_contains(track, "artists", track_filter.artist)
        and field_contains(track, "album", track_filter.album)
    )


def indexed_candidate_matches(
    item: dict[str, Any],
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
) -> bool:
    if quality and normalized(item.get("quality")) != quality:
        return False
    if codec and normalized(item.get("codecType")) != codec:
        return False
    if extension and normalized(item.get("extension")) != extension:
        return False
    return True


def filter_report(report: dict[str, Any], track_filter: TrackFilter) -> dict[str, Any]:
    if not any((
        track_filter.query,
        track_filter.track_id,
        track_filter.title,
        track_filter.artist,
        track_filter.album,
    )):
        return report
    filtered_items = [
        item for item in report["items"]
        if track_matches_filter(item, track_filter)
    ]
    filtered = dict(report)
    filtered["items"] = filtered_items
    filtered["tracks"] = len(filtered_items)
    filtered["cachedTracks"] = sum(1 for item in filtered_items if item["cachedFiles"])
    filtered["tracksWithUncachedBest"] = sum(
        1 for item in filtered_items
        if item["bestIndexed"] and not item["bestIndexed"].get("cached")
    )
    indexed_counter: Counter[str] = Counter()
    cached_counter: Counter[str] = Counter()
    encrypted_cache_files = 0
    spade_cache_files = 0
    for track in filtered_items:
        for item in track["indexedCandidates"]:
            indexed_counter[media_label(item)] += 1
        for item in track["cachedFiles"]:
            cached_counter[media_label(item)] += 1
            if item["encrypted"]:
                encrypted_cache_files += 1
            if item["hasSpade"]:
                spade_cache_files += 1
    filtered["indexedByQuality"] = dict(sorted(indexed_counter.items()))
    filtered["cachedByQuality"] = dict(sorted(cached_counter.items()))
    filtered["encryptedCacheFiles"] = encrypted_cache_files
    filtered["spadeCacheFiles"] = spade_cache_files
    filtered["filteredFromTracks"] = report["tracks"]
    return filtered


def cached_file_matches(
    item: dict[str, Any],
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
) -> bool:
    if quality and normalized(item.get("quality")) != quality:
        return False
    if codec and normalized(item.get("codecType")) != codec:
        return False
    if extension:
        detected_extension = normalized(item.get("detectedExtension") or item.get("extension"))
        indexed_extension = normalized(item.get("extension"))
        if extension not in {detected_extension, indexed_extension}:
            return False
    return True


def indexed_item_summary(item: dict[str, Any], cached_sizes: set[int]) -> dict[str, Any]:
    video_meta = item.get("video_meta") or {}
    encrypt_info = item.get("encrypt_info") or {}
    size = video_meta.get("size") if isinstance(video_meta.get("size"), int) else None
    codec_type = str(video_meta.get("codec_type") or "")
    return {
        "quality": str(video_meta.get("quality") or ""),
        "bitrate": video_meta.get("bitrate") if isinstance(video_meta.get("bitrate"), int) else None,
        "extension": "mp4" if codec_type == "flac" else str(video_meta.get("vtype") or ""),
        "codecType": codec_type,
        "indexedSize": size,
        "encrypted": bool(encrypt_info.get("encrypt")),
        "hasSpade": bool(encrypt_info.get("spade_a")),
        "cached": isinstance(size, int) and size in cached_sizes,
    }


def cache_file_summary(record: dict[str, Any], cache_dir: Path) -> dict[str, Any] | None:
    candidate = source_candidate(record, cache_dir)
    if not candidate:
        return None

    source = cache_dir / f"{candidate.cache_uuid}.bin"
    matched_item = selected_video_item(record, source_size=candidate.source_size)
    matched_meta = matched_item.get("video_meta") or {}
    indexed_size = record_indexed_size(record, matched_meta)
    summary: dict[str, Any] = {
        "cacheUuid": candidate.cache_uuid,
        "resourceId": candidate.resource_id,
        "path": str(source),
        "detectedExtension": candidate.extension,
        "codecType": candidate.codec_type,
        "quality": candidate.quality,
        "bitrate": candidate.bitrate,
        "sourceSize": candidate.source_size,
        "indexedSize": indexed_size,
        "indexedQuality": str(matched_meta.get("quality") or ""),
        "indexedCodecType": str(matched_meta.get("codec_type") or ""),
        "indexedExtension": "mp4" if matched_meta.get("codec_type") == "flac" else str(matched_meta.get("vtype") or ""),
        "encrypted": candidate.encrypted,
        "hasSpade": bool(record_spade(record, source_size=candidate.source_size)),
    }
    if candidate.extension in {"m4a", "mp4"}:
        try:
            mp4 = mp4_encryption_summary(source)
        except Exception as exc:
            summary["mp4Error"] = compact_error(str(exc))
        else:
            summary["mp4"] = {
                "scheme": mp4.get("scheme") or "",
                "sampleEntry": mp4.get("sample_entry") or "",
                "originalFormat": mp4.get("original_format") or "",
                "keyId": mp4.get("key_id") or "",
                "hasSampleEncryption": bool(mp4.get("has_sample_encryption")),
            }
    return summary


def analyze_records(records: list[dict[str, Any]], cache_dir: Path) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        track_id, _, _, _, _ = track_identity(record)
        grouped.setdefault(track_id, []).append(record)

    tracks: list[dict[str, Any]] = []
    indexed_counter: Counter[str] = Counter()
    cached_counter: Counter[str] = Counter()
    encrypted_cache_files = 0
    spade_cache_files = 0

    for track_id, group in grouped.items():
        _first_track_id, title, artists, album, duration_ms = track_identity(group[0])
        cached = cached_candidates_for_track(group, cache_dir)
        cached_sizes = {candidate.source_size for candidate in cached}
        indexed_items: list[dict[str, Any]] = []
        for record in group:
            for item in video_items(record):
                indexed_items.append(indexed_item_summary(item, cached_sizes))
        indexed_items.sort(key=lambda item: indexed_candidate_sort_key({"video_meta": {
            "quality": item["quality"],
            "codec_type": item["codecType"],
            "vtype": item["extension"],
            "bitrate": item["bitrate"],
            "size": item["indexedSize"],
        }}), reverse=True)

        cached_files: list[dict[str, Any]] = []
        for record in group:
            summary = cache_file_summary(record, cache_dir)
            if summary:
                cached_files.append(summary)
        cached_files.sort(
            key=lambda item: (
                item.get("quality") or "",
                item.get("bitrate") or 0,
                item.get("sourceSize") or 0,
            ),
            reverse=True,
        )

        best_cached = cached[0] if cached else None
        best_indexed = indexed_items[0] if indexed_items else None
        for item in indexed_items:
            indexed_counter[media_label(item)] += 1
        for item in cached_files:
            cached_counter[media_label(item)] += 1
            if item["encrypted"]:
                encrypted_cache_files += 1
            if item["hasSpade"]:
                spade_cache_files += 1

        tracks.append(
            {
                "trackId": track_id,
                "title": title,
                "artists": artists,
                "album": album,
                "durationMs": duration_ms,
                "indexedCandidates": indexed_items,
                "indexedLabels": unique_media_labels(indexed_items),
                "cachedFiles": cached_files,
                "cachedLabels": unique_media_labels(cached_files),
                "cachedVersions": cached_version_summaries(cached_files),
                "bestIndexed": best_indexed,
                "bestCached": {
                    "cacheUuid": best_cached.cache_uuid,
                    "quality": best_cached.quality,
                    "bitrate": best_cached.bitrate,
                    "extension": best_cached.extension,
                    "codecType": best_cached.codec_type,
                    "sourceSize": best_cached.source_size,
                    "encrypted": best_cached.encrypted,
                } if best_cached else None,
                "bestIndexedCached": bool(best_indexed and best_indexed.get("cached")),
            }
        )

    tracks.sort(key=lambda item: (str(item["artists"]).lower(), str(item["title"]).lower()))
    return {
        "cacheDir": str(cache_dir),
        "entries": len(records),
        "tracks": len(tracks),
        "cachedTracks": sum(1 for item in tracks if item["cachedFiles"]),
        "tracksWithUncachedBest": sum(
            1 for item in tracks
            if item["bestIndexed"] and not item["bestIndexed"].get("cached")
        ),
        "encryptedCacheFiles": encrypted_cache_files,
        "spadeCacheFiles": spade_cache_files,
        "indexedByQuality": dict(sorted(indexed_counter.items())),
        "cachedByQuality": dict(sorted(cached_counter.items())),
        "items": tracks,
    }


def write_csv_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "track_id",
                "title",
                "artists",
                "best_indexed",
                "best_indexed_cached",
                "best_cached",
                "cached_files",
                "indexed_candidates",
            ],
        )
        writer.writeheader()
        for item in report["items"]:
            writer.writerow(
                {
                    "track_id": item["trackId"],
                    "title": item["title"],
                    "artists": item["artists"],
                    "best_indexed": media_label(item["bestIndexed"] or {}),
                    "best_indexed_cached": item["bestIndexedCached"],
                    "best_cached": media_label(item["bestCached"] or {}),
                    "cached_files": "; ".join(item.get("cachedLabels") or []),
                    "indexed_candidates": "; ".join(item.get("indexedLabels") or []),
                }
            )


def selection_items_for_cached_quality(
    report: dict[str, Any],
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
    output_format: str = "playable",
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for track in report["items"]:
        matches = [
            item for item in track["cachedFiles"]
            if cached_file_matches(
                item,
                quality=quality,
                codec=codec,
                extension=extension,
            )
        ]
        if not matches:
            continue
        matches.sort(
            key=lambda item: (item.get("bitrate") or 0, item.get("sourceSize") or 0),
            reverse=True,
        )
        cache_uuid = str(matches[0].get("cacheUuid") or "")
        if cache_uuid and cache_uuid not in seen:
            seen.add(cache_uuid)
            items.append({"cache_uuid": cache_uuid, "format": output_format})
    return items


def write_selection_file(path: Path, items: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")


def batch_target_items(
    report: dict[str, Any],
    *,
    target: str = "",
    require_indexed: bool = True,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    target_quality, target_codec, target_extension = parse_target_label(target)
    for track in report["items"]:
        resolved_target = target
        if not resolved_target:
            resolved_target = media_label(track.get("bestIndexed") or {})
        if not resolved_target or resolved_target == "unknown":
            continue
        if require_indexed and any((target_quality, target_codec, target_extension)):
            indexed_matches = [
                item for item in track.get("indexedCandidates", [])
                if cached_file_matches(
                    item,
                    quality=target_quality,
                    codec=target_codec,
                    extension=target_extension,
                )
            ]
            if not indexed_matches:
                continue
        items.append(
            {
                "trackId": str(track.get("trackId") or ""),
                "title": str(track.get("title") or ""),
                "artist": str(track.get("artists") or ""),
                "album": str(track.get("album") or ""),
                "target": resolved_target,
            }
        )
    return [item for item in items if item["trackId"] and item["target"]]


def write_batch_target_file(path: Path, items: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")


def print_summary(report: dict[str, Any]) -> None:
    print(f"Cache directory: {report['cacheDir']}")
    print(f"Parsed entries: {report['entries']}")
    print(f"Tracks: {report['tracks']}")
    if "filteredFromTracks" in report:
        print(f"Filtered from tracks: {report['filteredFromTracks']}")
    print(f"Tracks with local cache: {report['cachedTracks']}")
    print(f"Tracks whose best indexed quality is not cached: {report['tracksWithUncachedBest']}")
    print(f"Encrypted cache files: {report['encryptedCacheFiles']}")
    print(f"Cache files with spade material: {report['spadeCacheFiles']}")
    print("Indexed qualities:")
    for label, count in report["indexedByQuality"].items():
        print(f"  {label}: {count}")
    print("Cached qualities:")
    for label, count in report["cachedByQuality"].items():
        print(f"  {label}: {count}")


def print_track_matches(report: dict[str, Any], limit: int) -> None:
    if not report["items"]:
        print("No matching tracks.")
        return
    print("Matching tracks:")
    rows = report["items"][:limit] if limit > 0 else report["items"]
    for track in rows:
        indexed = ", ".join(track.get("indexedLabels") or []) or "none"
        cached_versions = track.get("cachedVersions") or []
        cached = ", ".join(
            f"{item['label']}:{item['cacheUuid']}"
            for item in cached_versions
        ) or "none"
        print(
            f"  {track['trackId']} | {track['artists']} - {track['title']} | "
            f"indexed: {indexed} | cached: {cached}"
        )
    if limit > 0 and len(report["items"]) > limit:
        print(f"  ... {len(report['items']) - limit} more")


def watcher_command_for_track(
    track: dict[str, Any],
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
    selection_out: Path | None = None,
    output_dir: Path | None = None,
    export_when_found: bool = True,
) -> str:
    command = [
        sys.executable,
        "src/watch_sodamusic_cache.py",
        "--track-id",
        str(track["trackId"]),
    ]
    if quality and codec and not extension:
        command.extend(["--target", f"{quality}/{codec}"])
    elif quality or codec or extension:
        target_parts = [part for part in (quality, codec, extension) if part]
        if len(target_parts) >= 2:
            command.extend(["--target", "/".join(target_parts)])
        elif quality:
            command.extend(["--quality", quality])
        if codec and len(target_parts) < 2:
            command.extend(["--codec", codec])
    if extension:
        if not (quality and codec):
            command.extend(["--extension", extension])
    command.extend(["--require-indexed", "--require-single-track", "--stable-seconds", "1"])
    command.extend(["--selection-out", str(selection_out or Path("/tmp/sodamusic-target-selection.json"))])
    if export_when_found:
        command.append("--export-when-found")
        command.extend(["--output-dir", str(output_dir or Path.home() / "Music/SodaMusic Export")])
    return " ".join(shlex.quote(part) for part in command)


def print_watcher_command(
    report: dict[str, Any],
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
    selection_out: Path | None = None,
    output_dir: Path | None = None,
) -> None:
    if len(report["items"]) != 1 or not any((quality, codec, extension)):
        return
    print("Suggested watcher command:")
    print(
        watcher_command_for_track(
            report["items"][0],
            quality=quality,
            codec=codec,
            extension=extension,
            selection_out=selection_out,
            output_dir=output_dir,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze local SodaMusic cache/index protocol state."
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--query", default="", help="Search title, artists, album, and trackId.")
    parser.add_argument("--track-id", default="", help="Restrict output to trackId containing this text.")
    parser.add_argument("--title", default="", help="Restrict output to title containing this text.")
    parser.add_argument("--artist", default="", help="Restrict output to artist text containing this text.")
    parser.add_argument("--album", default="", help="Restrict output to album text containing this text.")
    parser.add_argument(
        "--match-limit",
        type=int,
        default=20,
        help="Maximum matching tracks to print in the console summary. Use 0 for all.",
    )
    parser.add_argument("--json-out", type=Path, help="Write full JSON report.")
    parser.add_argument("--csv-out", type=Path, help="Write compact CSV report.")
    parser.add_argument(
        "--selection-out",
        type=Path,
        help="Write an exporter selection file for locally cached files matching the filters.",
    )
    parser.add_argument(
        "--batch-target-out",
        type=Path,
        help="Write a batch target JSON list for src/batch_target_sodamusic_cache.py.",
    )
    parser.add_argument("--quality", default="", help="Filter cached files by quality, e.g. lossless.")
    parser.add_argument("--codec", default="", help="Filter cached files by codec, e.g. flac or aac.")
    parser.add_argument("--extension", default="", help="Filter cached files by detected/indexed extension.")
    parser.add_argument(
        "--target",
        default="",
        help="Shortcut for --quality/--codec[/--extension], e.g. lossless/flac.",
    )
    parser.add_argument(
        "--suggest-watcher",
        action="store_true",
        help="Print a watcher command when the filters match exactly one track and a target quality/codec is supplied.",
    )
    parser.add_argument(
        "--suggest-selection-out",
        type=Path,
        help="Selection file path to put in the suggested watcher command.",
    )
    parser.add_argument(
        "--suggest-output-dir",
        type=Path,
        help="Output directory to put in the suggested watcher command.",
    )
    parser.add_argument(
        "--selection-format",
        choices=("playable", "mp3", "flac", "original"),
        default="playable",
        help="Export format to write into --selection-out.",
    )
    parser.add_argument(
        "--allow-unindexed-batch-targets",
        action="store_true",
        help="Include filtered tracks in --batch-target-out even when --target is not indexed for them.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Analyze at most N parsed entries.")
    args = parser.parse_args()

    cache_dir = args.cache_dir.expanduser().resolve()
    entries_db = cache_dir / "entries.db"
    if not entries_db.exists():
        raise SystemExit(f"entries.db not found: {entries_db}")
    records = parse_entries(entries_db)
    if args.limit > 0:
        records = records[: args.limit]
    report = analyze_records(records, cache_dir)
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
    report = filter_report(report, track_filter)

    print_summary(report)
    if any((track_filter.query, track_filter.track_id, track_filter.title, track_filter.artist, track_filter.album)):
        print_track_matches(report, args.match_limit)
    if args.suggest_watcher:
        print_watcher_command(
            report,
            quality=quality,
            codec=codec,
            extension=extension,
            selection_out=args.suggest_selection_out,
            output_dir=args.suggest_output_dir,
        )
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON report: {args.json_out}")
    if args.csv_out:
        write_csv_report(args.csv_out, report)
        print(f"CSV report: {args.csv_out}")
    if args.selection_out:
        items = selection_items_for_cached_quality(
            report,
            quality=quality,
            codec=codec,
            extension=extension,
            output_format=args.selection_format,
        )
        write_selection_file(args.selection_out, items)
        print(f"Selection items: {len(items)}")
        print(f"Selection file: {args.selection_out}")
    if args.batch_target_out:
        batch_items = batch_target_items(
            report,
            target=args.target.strip(),
            require_indexed=not args.allow_unindexed_batch_targets,
        )
        write_batch_target_file(args.batch_target_out, batch_items)
        print(f"Batch target items: {len(batch_items)}")
        print(f"Batch target file: {args.batch_target_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
