#!/usr/bin/env python3
"""
Local web UI for exporting SodaMusic cache files.

The server binds to 127.0.0.1 and shells out to export_sodamusic_cache.py so the
existing exporter remains the source of truth.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from analyze_sodamusic_cache import (
    TrackFilter as AnalyzeTrackFilter,
    analyze_records,
    cached_file_matches,
    filter_report,
    parse_target_label,
)
from batch_target_sodamusic_cache import (
    BatchOptions as BatchTargetOptions,
    BatchTarget,
    preflight_target as batch_preflight_target,
)
from export_sodamusic_cache import (
    ALLOWED_EXPORT_FORMATS,
    DEFAULT_CACHE_DIR,
    DEFAULT_MP3_BITRATE_KBPS,
    find_mp3_transcoder,
    parse_entries,
    resolve_device_node_path,
    source_rows,
)
from runtime_dependencies import ensure_runtime_dependencies, ensure_tool_path, which as runtime_which


ROOT = Path(__file__).resolve().parent
EXPORT_SCRIPT = ROOT / "export_sodamusic_cache.py"
TARGET_SCRIPT = ROOT / "target_sodamusic_cache.py"
STATIC_DIR = ROOT / "web"
DEFAULT_OUTPUT_DIR = Path.home() / "Music/SodaMusic Export"
ALLOWED_FORMATS = {"playable", "mp3", "flac", "original"}
WEB_API_VERSION = 6
PREFLIGHT_CACHE_TTL_SECONDS = 6

STATIC_MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".map": "application/json",
}

MAX_REQUEST_BODY_BYTES = 1 * 1024 * 1024


@dataclass
class ExportJob:
    id: str
    command: list[str]
    cache_dir: str
    output_dir: str
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    status: str = "running"
    returncode: int | None = None
    logs: list[str] = field(default_factory=list)
    error: str = ""
    process: subprocess.Popen[str] | None = None
    selection_file: Path | None = None


jobs: dict[str, ExportJob] = {}
jobs_lock = threading.Lock()
preflight_lock = threading.Lock()
preflight_cache: tuple[float, dict[str, Any]] | None = None


def job_phase_from_line(line: str) -> tuple[str, str] | None:
    if line.startswith("本地索引里还没有匹配歌曲") or line == "no matching track in local index yet":
        return "waiting-index", "等待本地索引出现"
    if line.startswith("目标品质不在本地索引里") or "requested quality is not in the local index" in line:
        return "waiting-index", "等待目标品质进入本地索引"
    if "target quality is indexed but not cached yet" in line:
        return "waiting-cache", "等待目标品质写入本地缓存"
    if line.startswith("matched cached item(s), waiting for cache size to match indexed size"):
        return "waiting-cache-complete", "等待缓存文件写完整"
    if line.startswith("matched cached item(s), waiting for file size to stay unchanged"):
        return "waiting-cache-stable", "等待缓存文件稳定"
    if line.startswith("Running exporter:"):
        return "exporting", "正在导出目标缓存"
    if line.startswith("Preparing records:") or line.startswith("Progress:"):
        return "exporting", "正在处理缓存"
    return None


def parse_job_metrics(logs: list[str]) -> dict[str, int | str]:
    total = 0
    current = 0
    exported = 0
    skipped = 0
    phase = ""
    message = ""

    for line in logs:
        parsed_phase = job_phase_from_line(line)
        if parsed_phase:
            phase, message = parsed_phase

        prepare = re.match(r"^Preparing records:\s+(\d+)", line)
        if prepare:
            total = int(prepare.group(1))

        progress = re.match(r"^Progress:\s+(\d+)/(\d+)\s+([\w-]+):", line)
        if progress:
            current = int(progress.group(1))
            total = int(progress.group(2))
            if progress.group(3) == "exported":
                exported += 1
            elif progress.group(3) == "skipped":
                skipped += 1

        exported_line = re.match(r"^Exported files:\s+(\d+)", line)
        if exported_line:
            exported = int(exported_line.group(1))

        skipped_line = re.match(r"^Skipped files:\s+(\d+)", line)
        if skipped_line:
            skipped = int(skipped_line.group(1))

    return {
        "total": total,
        "current": current,
        "exported": exported,
        "skipped": skipped,
        "phase": phase,
        "message": message,
    }


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        return {}
    if length > MAX_REQUEST_BODY_BYTES:
        raise ValueError("request body too large")
    body = handler.rfile.read(length)
    return json.loads(body.decode("utf-8"))


def as_path(value: Any, fallback: Path | None = None) -> Path:
    if isinstance(value, str) and value.strip():
        return Path(value).expanduser()
    if fallback is not None:
        return fallback
    raise ValueError("path is required")


def as_bool(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "on"}


def selected_source_items(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw_items = payload.get("selectedSources")
    if not isinstance(raw_items, list):
        return []

    items: list[dict[str, str]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        cache_uuid = str(raw_item.get("cacheUuid") or raw_item.get("cache_uuid") or "").strip()
        output_format = str(raw_item.get("format") or "playable").strip()
        if cache_uuid:
            items.append({"cache_uuid": cache_uuid, "format": output_format})
    return items


def has_source_selection(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("selectedSources"), list)


def requested_formats(payload: dict[str, Any]) -> set[str]:
    items = selected_source_items(payload)
    if items:
        return {item["format"] for item in items}
    return {str(payload.get("format") or "playable")}


def check_item(label: str, ok: bool, detail: str = "", path: Path | None = None) -> dict[str, Any]:
    return {
        "label": label,
        "ok": ok,
        "detail": detail,
        "path": str(path) if path else "",
    }


def scan_sources(cache_dir: Path) -> dict[str, Any]:
    entries_db = cache_dir / "entries.db"
    if not entries_db.exists():
        return {
            "rows": [],
            "total": 0,
            "exportable": 0,
            "uncachedHigher": 0,
            "indexedQualities": [],
            "cachedQualities": [],
            "error": f"缓存目录里没有 entries.db: {cache_dir}",
        }
    records = parse_entries(entries_db)
    rows = source_rows(records, cache_dir)
    report = analyze_records(records, cache_dir)
    return {
        "rows": rows,
        "total": len(rows),
        "exportable": sum(1 for row in rows if row.get("cacheUuid")),
        "uncachedHigher": sum(1 for row in rows if row.get("uncachedBest")),
        "indexedQualities": list((report.get("indexedByQuality") or {}).keys()),
        "cachedQualities": list((report.get("cachedByQuality") or {}).keys()),
        "error": "",
    }


def indexed_item_matches(
    item: dict[str, Any],
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
) -> bool:
    if quality and str(item.get("quality") or "").strip().lower() != quality:
        return False
    if codec and str(item.get("codecType") or "").strip().lower() != codec:
        return False
    if extension and str(item.get("extension") or "").strip().lower() != extension:
        return False
    return True


def target_track_summary(
    track: dict[str, Any],
    *,
    quality: str = "",
    codec: str = "",
    extension: str = "",
) -> dict[str, Any]:
    indexed_matches = [
        item for item in track.get("indexedCandidates", [])
        if indexed_item_matches(item, quality=quality, codec=codec, extension=extension)
    ]
    cached_matches = [
        item for item in track.get("cachedFiles", [])
        if cached_file_matches(item, quality=quality, codec=codec, extension=extension)
    ]
    target_indexed = bool(indexed_matches) if any((quality, codec, extension)) else False
    target_cached = bool(cached_matches) if any((quality, codec, extension)) else False
    target_cached_files = [
        {
            "cacheUuid": str(item.get("cacheUuid") or ""),
            "resourceId": str(item.get("resourceId") or ""),
            "quality": str(item.get("quality") or ""),
            "codecType": str(item.get("codecType") or ""),
            "extension": str(item.get("detectedExtension") or item.get("extension") or ""),
            "sourceSize": item.get("sourceSize") if isinstance(item.get("sourceSize"), int) else None,
            "indexedSize": item.get("indexedSize") if isinstance(item.get("indexedSize"), int) else None,
            "encrypted": bool(item.get("encrypted")),
            "hasSpade": bool(item.get("hasSpade")),
        }
        for item in cached_matches
    ]
    return {
        "trackId": str(track.get("trackId") or ""),
        "title": str(track.get("title") or ""),
        "artists": str(track.get("artists") or ""),
        "album": str(track.get("album") or ""),
        "durationMs": track.get("durationMs") if isinstance(track.get("durationMs"), int) else None,
        "indexedLabels": track.get("indexedLabels") or [],
        "cachedLabels": track.get("cachedLabels") or [],
        "targetIndexed": target_indexed,
        "targetCached": target_cached,
        "targetRank": 2 if target_cached else 1 if target_indexed else 0,
        "targetCacheUuids": [
            item["cacheUuid"]
            for item in target_cached_files
            if item["cacheUuid"]
        ],
        "targetCachedFiles": target_cached_files,
    }


def search_target_tracks(payload: dict[str, Any]) -> dict[str, Any]:
    cache_dir = as_path(payload.get("cacheDir"), DEFAULT_CACHE_DIR).resolve()
    entries_db = cache_dir / "entries.db"
    if not entries_db.exists():
        return {
            "matches": [],
            "total": 0,
            "error": f"缓存目录里没有 entries.db: {cache_dir}",
        }
    limit = int(payload.get("limit") or 20)
    limit = min(max(limit, 1), 50)
    quality, codec, extension = parse_target_label(str(payload.get("target") or ""))
    report = filter_report(
        analyze_records(parse_entries(entries_db), cache_dir),
        AnalyzeTrackFilter(
            query=str(payload.get("query") or "").strip(),
            track_id=str(payload.get("trackId") or payload.get("track_id") or "").strip(),
            title=str(payload.get("title") or "").strip(),
            artist=str(payload.get("artist") or "").strip(),
            album=str(payload.get("album") or "").strip(),
        ),
    )
    tracks = report.get("items") or []
    matches = [
        target_track_summary(
            track,
            quality=quality,
            codec=codec,
            extension=extension,
        )
        for track in tracks
    ]
    matches.sort(
        key=lambda match: (
            -int(match.get("targetRank") or 0),
            str(match.get("artists") or "").lower(),
            str(match.get("title") or "").lower(),
        ),
    )
    matches = matches[:limit]
    return {
        "matches": matches,
        "total": len(tracks),
        "limit": limit,
        "target": str(payload.get("target") or ""),
        "error": "",
    }


def batch_target_from_payload(item: dict[str, Any], index: int) -> BatchTarget:
    target = BatchTarget(
        query=str(item.get("query") or "").strip(),
        track_id=str(item.get("trackId") or item.get("track_id") or "").strip(),
        title=str(item.get("title") or "").strip(),
        artist=str(item.get("artist") or item.get("artists") or "").strip(),
        album=str(item.get("album") or "").strip(),
        target=str(item.get("target") or "").strip(),
    )
    if not target.target:
        raise ValueError(f"第 {index} 项缺少目标缓存版本")
    if not any((target.query, target.track_id, target.title, target.artist, target.album)):
        raise ValueError(f"第 {index} 项缺少歌曲选择条件")
    return target


def batch_preflight_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def preflight_batch_targets(payload: dict[str, Any]) -> dict[str, Any]:
    cache_dir = as_path(payload.get("cacheDir"), DEFAULT_CACHE_DIR).resolve()
    entries_db = cache_dir / "entries.db"
    if not entries_db.exists():
        return {
            "rows": [],
            "total": 0,
            "ok": False,
            "counts": {},
            "error": f"缓存目录里没有 entries.db: {cache_dir}",
        }
    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        return {
            "rows": [],
            "total": 0,
            "ok": False,
            "counts": {},
            "error": "请提供至少一个目标歌曲",
        }
    limit = int(payload.get("limit") or 200)
    limit = min(max(limit, 1), 500)
    targets = [
        batch_target_from_payload(item, index)
        for index, item in enumerate(raw_targets[:limit], start=1)
        if isinstance(item, dict)
    ]
    options = BatchTargetOptions(cache_dir=cache_dir)
    rows = [
        batch_preflight_target(index, target, options)
        for index, target in enumerate(targets, start=1)
    ]
    counts = batch_preflight_status_counts(rows)
    return {
        "rows": rows,
        "total": len(rows),
        "limit": limit,
        "truncated": len(raw_targets) > limit,
        "ok": all(row.get("status_code") == 0 for row in rows),
        "counts": counts,
        "error": "",
    }


def build_preflight_payload(*, force: bool = False) -> dict[str, Any]:
    global preflight_cache

    with preflight_lock:
        now = time.time()
        if not force and preflight_cache and now - preflight_cache[0] < PREFLIGHT_CACHE_TTL_SECONDS:
            return preflight_cache[1]

        cache_dir = DEFAULT_CACHE_DIR.expanduser().resolve()
        output_dir = DEFAULT_OUTPUT_DIR.expanduser().resolve()
        device_node = resolve_device_node_path().resolve()
        node_path = shutil_which("node")
        mp3_transcoder = find_mp3_transcoder()

        try:
            sources = scan_sources(cache_dir)
        except Exception as exc:
            sources = {
                "rows": [],
                "total": 0,
                "exportable": 0,
                "uncachedHigher": 0,
                "error": str(exc),
            }

        dependency_report = ensure_runtime_dependencies(auto_install=False)
        checks = [
            check_item(
                "缓存",
                (cache_dir / "entries.db").exists(),
                "已找到本机 SodaMusic 缓存索引" if (cache_dir / "entries.db").exists() else "未找到本机缓存索引",
                cache_dir,
            ),
            check_item(
                "解密模块",
                device_node.exists(),
                "已从 SodaMusic 应用内找到 device.node" if device_node.exists() else "未在已安装应用内找到 device.node",
                device_node,
            ),
            check_item(
                "Node",
                bool(node_path),
                "可加载 SodaMusic 解密模块" if node_path else "缺少 node，无法解码加密缓存",
                Path(node_path) if node_path else None,
            ),
            check_item(
                "转码",
                bool(mp3_transcoder),
                "可转码 MP3/FLAC" if mp3_transcoder else "未安装 ffmpeg，MP3/FLAC 选项会禁用",
                Path(mp3_transcoder) if mp3_transcoder else None,
            ),
        ]
        payload = {
            "apiVersion": WEB_API_VERSION,
            "ready": bool(sources["exportable"] and device_node.exists() and node_path),
            "cacheDir": str(cache_dir),
            "outputDir": str(output_dir),
            "deviceNode": str(device_node),
            "checks": checks,
            "warnings": dependency_report.warnings,
            "errors": dependency_report.errors,
            "sources": sources,
            "mp3Bitrate": DEFAULT_MP3_BITRATE_KBPS,
            "mp3TranscoderFound": bool(mp3_transcoder),
            "nodeFound": bool(node_path),
            "platform": sys.platform,
            "script": str(EXPORT_SCRIPT),
        }
        preflight_cache = (now, payload)
        return payload


def preflight_status_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    return {
        **{key: value for key, value in payload.items() if key != "sources"},
        "sources": {
            "total": sources.get("total", 0),
            "exportable": sources.get("exportable", 0),
            "uncachedHigher": sources.get("uncachedHigher", 0),
            "indexedQualities": sources.get("indexedQualities", []),
            "cachedQualities": sources.get("cachedQualities", []),
            "error": sources.get("error", ""),
        },
    }


def validate_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    cache_dir = as_path(payload.get("cacheDir"), DEFAULT_CACHE_DIR)
    output_dir = as_path(payload.get("outputDir"), DEFAULT_OUTPUT_DIR)
    raw_key = str(payload.get("rawKey") or "").strip()
    key_mode = str(payload.get("keyMode") or ("raw" if raw_key else "device"))
    output_format = str(payload.get("format") or "playable")
    formats = requested_formats(payload)
    source_items = selected_source_items(payload)
    limit_value = payload.get("limit")
    bitrate_value = payload.get("mp3Bitrate", DEFAULT_MP3_BITRATE_KBPS)

    if not (cache_dir / "entries.db").exists():
        errors.append(f"缓存目录里没有 entries.db: {cache_dir}")
    if not output_dir:
        errors.append("导出文件夹不能为空")
    if output_format not in ALLOWED_FORMATS:
        errors.append(f"未知导出格式: {output_format}")
    for item in source_items:
        if item["format"] not in ALLOWED_EXPORT_FORMATS:
            errors.append(f"未知导出格式: {item['format']}")
    if has_source_selection(payload) and not source_items:
        errors.append("请选择至少一个可导出的源")
    if formats & {"mp3", "flac"} and not find_mp3_transcoder():
        errors.append("导出 MP3/FLAC 需要安装 ffmpeg")
    if formats - {"original"} and key_mode == "raw":
        if not raw_key:
            errors.append("固定 key 不能为空")
        elif not re.fullmatch(r"[0-9a-fA-F]{32}", raw_key):
            errors.append("固定 key 必须是 32 位十六进制字符")
    if limit_value not in (None, ""):
        try:
            if int(limit_value) < 0:
                errors.append("限制数量不能小于 0")
        except (TypeError, ValueError):
            errors.append("限制数量必须是数字")
    try:
        if int(bitrate_value) <= 0:
            errors.append("MP3 码率必须大于 0")
    except (TypeError, ValueError):
        errors.append("MP3 码率必须是数字")
    return errors


def validate_target_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    cache_dir = as_path(payload.get("cacheDir"), DEFAULT_CACHE_DIR)
    output_dir = as_path(payload.get("outputDir"), DEFAULT_OUTPUT_DIR)
    target = str(payload.get("target") or "").strip()
    selectors = [
        str(payload.get("query") or "").strip(),
        str(payload.get("trackId") or payload.get("track_id") or "").strip(),
        str(payload.get("title") or "").strip(),
        str(payload.get("artist") or "").strip(),
        str(payload.get("album") or "").strip(),
    ]
    output_format = str(payload.get("format") or "auto")
    selection_format = str(payload.get("selectionFormat") or output_format)
    bitrate_value = payload.get("mp3Bitrate", DEFAULT_MP3_BITRATE_KBPS)
    stable_value = payload.get("stableSeconds", 1)
    interval_value = payload.get("interval", 3)
    timeout_value = payload.get("timeout", 0)

    if not (cache_dir / "entries.db").exists():
        errors.append(f"缓存目录里没有 entries.db: {cache_dir}")
    if not output_dir:
        errors.append("导出文件夹不能为空")
    if not any(selectors):
        errors.append("请输入目标歌曲关键词或 trackId")
    if not target:
        errors.append("请选择目标缓存版本")
    elif "/" not in target:
        errors.append("目标缓存版本格式应为 quality/codec")
    resolved_output_format = resolve_target_output_format(output_format, target)
    resolved_selection_format = resolve_target_output_format(selection_format, target)
    if output_format != "auto" and output_format not in ALLOWED_FORMATS:
        errors.append(f"未知导出格式: {output_format}")
    if selection_format != "auto" and selection_format not in ALLOWED_EXPORT_FORMATS:
        errors.append(f"未知导出格式: {selection_format}")
    if {resolved_output_format, resolved_selection_format} & {"mp3", "flac"} and not find_mp3_transcoder():
        errors.append("导出 MP3/FLAC 需要安装 ffmpeg")
    try:
        if int(bitrate_value) <= 0:
            errors.append("MP3 码率必须大于 0")
    except (TypeError, ValueError):
        errors.append("MP3 码率必须是数字")
    for label, value, minimum in (
        ("缓存稳定秒数", stable_value, 0.0),
        ("扫描间隔", interval_value, 0.5),
        ("等待超时", timeout_value, 0.0),
    ):
        try:
            if float(value) < minimum:
                errors.append(f"{label}不能小于 {minimum:g}")
        except (TypeError, ValueError):
            errors.append(f"{label}必须是数字")
    return errors


def write_selection_file(items: list[dict[str, str]]) -> Path:
    fd, path = tempfile.mkstemp(prefix="soda-selection-", suffix=".json")
    selection_file = Path(path)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"items": items}, fh, ensure_ascii=False)
    return selection_file


def create_temp_selection_path(prefix: str = "soda-target-selection-") -> Path:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".json")
    os.close(fd)
    selection_file = Path(path)
    selection_file.unlink(missing_ok=True)
    return selection_file


def parse_target_codec(target: str) -> str:
    parts = [str(part).strip().lower() for part in str(target or "").split("/") if str(part).strip()]
    return parts[1] if len(parts) >= 2 else ""


def resolve_target_output_format(requested_format: str, target: str) -> str:
    requested = str(requested_format or "auto").strip().lower()
    if requested != "auto":
        return requested
    codec = parse_target_codec(target)
    if codec in {"flac", "mp3"}:
        return codec
    return "playable"


def target_requires_output_match(payload: dict[str, Any], *output_formats: str) -> bool:
    if "requireOutputMatch" in payload:
        return as_bool(payload.get("requireOutputMatch"))
    return any(str(output_format or "").strip().lower() in {"mp3", "flac"} for output_format in output_formats)


def build_command(payload: dict[str, Any]) -> tuple[list[str], str, str, Path | None]:
    cache_dir = as_path(payload.get("cacheDir"), DEFAULT_CACHE_DIR).resolve()
    output_dir = as_path(payload.get("outputDir"), DEFAULT_OUTPUT_DIR).resolve()
    device_node = resolve_device_node_path(payload.get("deviceNode")).resolve()
    output_format = str(payload.get("format") or "playable")
    mp3_bitrate = int(payload.get("mp3Bitrate") or DEFAULT_MP3_BITRATE_KBPS)
    limit = int(payload.get("limit") or 0)
    raw_key = str(payload.get("rawKey") or "").strip().lower()
    key_mode = str(payload.get("keyMode") or ("raw" if raw_key else "device"))
    source_items = selected_source_items(payload)
    formats = requested_formats(payload)
    selection_file = write_selection_file(source_items) if has_source_selection(payload) else None

    command = [
        sys.executable,
        str(EXPORT_SCRIPT),
        "--cache-dir",
        str(cache_dir),
        "--output-dir",
        str(output_dir),
        "--format",
        output_format,
        "--mp3-bitrate",
        str(mp3_bitrate),
        "--progress",
    ]
    if selection_file:
        command.extend(["--selection-file", str(selection_file)])
    if formats - {"original"} and key_mode == "raw":
        command.extend(["--raw-key", raw_key])
    else:
        command.extend(["--device-node", str(device_node)])
    if as_bool(payload.get("dryRun")):
        command.append("--dry-run")
    if as_bool(payload.get("overwrite")):
        command.append("--overwrite")
    if as_bool(payload.get("verifyAudio")):
        command.append("--verify-audio")
    if as_bool(payload.get("requireOutputMatch")):
        command.append("--require-output-match")
    if limit > 0 and not selection_file:
        command.extend(["--limit", str(limit)])
    return command, str(cache_dir), str(output_dir), selection_file


def build_target_command(payload: dict[str, Any]) -> tuple[list[str], str, str, Path | None]:
    cache_dir = as_path(payload.get("cacheDir"), DEFAULT_CACHE_DIR).resolve()
    output_dir = as_path(payload.get("outputDir"), DEFAULT_OUTPUT_DIR).resolve()
    device_node = resolve_device_node_path(payload.get("deviceNode")).resolve()
    target = str(payload.get("target") or "").strip()
    output_format = resolve_target_output_format(str(payload.get("format") or "auto"), target)
    selection_format = resolve_target_output_format(str(payload.get("selectionFormat") or "auto"), target)
    mp3_bitrate = int(payload.get("mp3Bitrate") or DEFAULT_MP3_BITRATE_KBPS)
    selection_file = create_temp_selection_path()

    command = [
        sys.executable,
        str(TARGET_SCRIPT),
        "--cache-dir",
        str(cache_dir),
        "--output-dir",
        str(output_dir),
        "--selection-out",
        str(selection_file),
        "--target",
        target,
        "--selection-format",
        selection_format,
        "--default-format",
        output_format,
        "--mp3-bitrate",
        str(mp3_bitrate),
        "--device-node",
        str(device_node),
        "--stable-seconds",
        str(float(payload.get("stableSeconds", 1))),
        "--interval",
        str(float(payload.get("interval", 3))),
        "--wait-index",
    ]
    for flag, keys in (
        ("--query", ("query",)),
        ("--track-id", ("trackId", "track_id")),
        ("--title", ("title",)),
        ("--artist", ("artist",)),
        ("--album", ("album",)),
    ):
        value = next((str(payload.get(key) or "").strip() for key in keys if str(payload.get(key) or "").strip()), "")
        if value:
            command.extend([flag, value])
    timeout = float(payload.get("timeout") or 0)
    if timeout > 0:
        command.extend(["--timeout", str(timeout)])
    if as_bool(payload.get("once")):
        command.append("--once")
    if as_bool(payload.get("allowSizeMismatch")):
        command.append("--allow-size-mismatch")
    if as_bool(payload.get("dryRun")):
        command.append("--export-dry-run")
    if as_bool(payload.get("overwrite")):
        command.append("--overwrite")
    if as_bool(payload.get("verifyAudio")):
        command.append("--verify-audio")
    if target_requires_output_match(payload, output_format, selection_format):
        command.append("--require-output-match")
    return command, str(cache_dir), str(output_dir), selection_file


def job_snapshot(job: ExportJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "returncode": job.returncode,
        "cacheDir": job.cache_dir,
        "outputDir": job.output_dir,
        "startedAt": job.started_at,
        "finishedAt": job.finished_at,
        "logs": job.logs[-500:],
        "metrics": parse_job_metrics(job.logs),
        "error": job.error,
    }


COMPLETED_JOB_MAX_AGE_SECONDS = 3600  # 1 hour
COMPLETED_JOB_MAX_COUNT = 50


def cleanup_old_jobs() -> None:
    now = time.time()
    with jobs_lock:
        completed_jobs = [
            (jid, j)
            for jid, j in jobs.items()
            if j.status in ("completed", "failed") and j.finished_at is not None
        ]
        expired_ids = [
            jid
            for jid, j in completed_jobs
            if now - j.finished_at > COMPLETED_JOB_MAX_AGE_SECONDS
        ]
        for jid in expired_ids:
            del jobs[jid]
        if len(jobs) > COMPLETED_JOB_MAX_COUNT:
            completed_jobs.sort(key=lambda x: x[1].finished_at or 0)
            excess = len(jobs) - COMPLETED_JOB_MAX_COUNT
            for jid, _ in completed_jobs[:excess]:
                if jid in jobs:
                    del jobs[jid]


def run_job(job: ExportJob) -> None:
    try:
        process = subprocess.Popen(
            job.command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        with jobs_lock:
            job.process = process

        assert process.stdout is not None
        for line in process.stdout:
            with jobs_lock:
                job.logs.append(line.rstrip())

        returncode = process.wait()
        with jobs_lock:
            job.returncode = returncode
            job.status = "completed" if returncode == 0 else "failed"
            job.finished_at = time.time()
    except Exception as exc:
        with jobs_lock:
            job.status = "failed"
            job.error = str(exc)
            job.logs.append(traceback.format_exc())
            job.finished_at = time.time()
    finally:
        if job.selection_file:
            job.selection_file.unlink(missing_ok=True)
        cleanup_old_jobs()


def open_path(path: str, create: bool = False) -> bool:
    target = Path(path).expanduser()
    try:
        target = target.resolve()
    except (OSError, RuntimeError):
        return False

    allowed_roots = {DEFAULT_CACHE_DIR.resolve(), DEFAULT_OUTPUT_DIR.resolve()}
    if not any(target == root or target.is_relative_to(root) for root in allowed_roots):
        return False

    if create and not target.exists():
        target.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        return False
    if sys.platform == "darwin":
        subprocess.run(["open", str(target)], check=False)
    elif os.name == "nt":
        os.startfile(str(target))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(target)], check=False)
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "SodaMusicExportWeb/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        request_path = urlsplit(self.path).path
        if request_path == "/":
            self.serve_static("index.html")
            return
        if request_path == "/api/defaults":
            json_response(self, HTTPStatus.OK, build_preflight_payload())
            return
        if request_path == "/api/preflight":
            json_response(self, HTTPStatus.OK, build_preflight_payload(force="force=1" in self.path))
            return
        if request_path == "/api/preflight-status":
            payload = build_preflight_payload(force="force=1" in self.path)
            json_response(self, HTTPStatus.OK, preflight_status_payload(payload))
            return
        if request_path.startswith("/api/jobs/"):
            job_id = request_path.rsplit("/", 1)[-1]
            with jobs_lock:
                job = jobs.get(job_id)
                payload = job_snapshot(job) if job else None
            if not payload:
                json_response(self, HTTPStatus.NOT_FOUND, {"error": "job not found"})
                return
            json_response(self, HTTPStatus.OK, payload)
            return
        if request_path.startswith("/static/"):
            self.serve_static(request_path.removeprefix("/static/"))
            return
        if request_path.startswith("/_next/"):
            self.serve_static(request_path.lstrip("/"))
            return
        if request_path.startswith("/favicon") or request_path == "/manifest.json" or request_path == "/robots.txt":
            self.serve_static(request_path.removeprefix("/"))
            return
        self.serve_static("index.html")
        return

    def do_POST(self) -> None:
        try:
            payload = read_json_body(self)
        except json.JSONDecodeError:
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
            return

        if self.path == "/api/validate":
            json_response(self, HTTPStatus.OK, {"errors": validate_payload(payload)})
            return
        if self.path == "/api/sources":
            cache_dir = as_path(payload.get("cacheDir"), DEFAULT_CACHE_DIR).resolve()
            try:
                sources = scan_sources(cache_dir)
            except Exception as exc:
                json_response(
                    self,
                    HTTPStatus.BAD_REQUEST,
                    {"error": str(exc)},
                )
                return
            if sources["error"]:
                json_response(self, HTTPStatus.BAD_REQUEST, {"errors": [sources["error"]]})
                return
            json_response(self, HTTPStatus.OK, sources)
            return
        if self.path == "/api/target-search":
            try:
                result = search_target_tracks(payload)
            except Exception as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            if result["error"]:
                json_response(self, HTTPStatus.BAD_REQUEST, {"errors": [result["error"]]})
                return
            json_response(self, HTTPStatus.OK, result)
            return
        if self.path == "/api/batch-target-preflight":
            try:
                result = preflight_batch_targets(payload)
            except Exception as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            if result["error"]:
                json_response(self, HTTPStatus.BAD_REQUEST, {"errors": [result["error"]]})
                return
            json_response(self, HTTPStatus.OK, result)
            return
        if self.path == "/api/jobs":
            errors = validate_payload(payload)
            if errors:
                json_response(self, HTTPStatus.BAD_REQUEST, {"errors": errors})
                return
            command, cache_dir, output_dir, selection_file = build_command(payload)
            job = ExportJob(
                id=uuid.uuid4().hex,
                command=command,
                cache_dir=cache_dir,
                output_dir=output_dir,
                selection_file=selection_file,
            )
            with jobs_lock:
                jobs[job.id] = job
            thread = threading.Thread(target=run_job, args=(job,), daemon=True)
            thread.start()
            json_response(self, HTTPStatus.CREATED, job_snapshot(job))
            return
        if self.path == "/api/target-jobs":
            errors = validate_target_payload(payload)
            if errors:
                json_response(self, HTTPStatus.BAD_REQUEST, {"errors": errors})
                return
            command, cache_dir, output_dir, selection_file = build_target_command(payload)
            job = ExportJob(
                id=uuid.uuid4().hex,
                command=command,
                cache_dir=cache_dir,
                output_dir=output_dir,
                selection_file=selection_file,
            )
            with jobs_lock:
                jobs[job.id] = job
            thread = threading.Thread(target=run_job, args=(job,), daemon=True)
            thread.start()
            json_response(self, HTTPStatus.CREATED, job_snapshot(job))
            return
        if self.path == "/api/open":
            path = str(payload.get("path") or "")
            create = as_bool(payload.get("create"))
            if open_path(path, create=create):
                json_response(self, HTTPStatus.OK, {"opened": True})
            else:
                json_response(self, HTTPStatus.NOT_FOUND, {"error": f"path not found: {path}"})
            return
        json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

    def serve_static(self, relative: str) -> None:
        target = (STATIC_DIR / relative).resolve()
        static_root = STATIC_DIR.resolve()
        if static_root not in target.parents and target != static_root:
            json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not target.exists() or not target.is_file():
            json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        content_type = STATIC_MIME_TYPES.get(target.suffix, "text/html; charset=utf-8")
        text_response(self, HTTPStatus.OK, target.read_bytes(), content_type)


def shutil_which(command: str) -> str | None:
    return runtime_which(command)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local SodaMusic export web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser automatically.")
    args = parser.parse_args()

    ensure_tool_path()
    dependency_report = ensure_runtime_dependencies(auto_install=False)
    for item in dependency_report.installed:
        print(f"已安装: {item}", flush=True)
    for warning in dependency_report.warnings:
        print(f"提示: {warning}", flush=True)
    if not dependency_report.ok:
        for error in dependency_report.errors:
            print(error, file=sys.stderr, flush=True)
        if dependency_report.missing:
            print(f"缺失依赖: {', '.join(dependency_report.missing)}", file=sys.stderr, flush=True)
        return 1

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"SodaMusic export web UI: {url}", flush=True)
    print(f"Command: {shlex.join([sys.executable, str(Path(__file__).resolve()), '--port', str(args.port)])}", flush=True)
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
