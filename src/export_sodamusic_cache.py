#!/usr/bin/env python3
"""
Export SodaMusic (汽水音乐) LunaCacheV2 files with recovered song names.

The script is intentionally read-only for the cache directory. It parses
SodaMusic's msgpackr/LMDB-like entries.db file, exports decodable cache media
with recovered song names, and writes manifest.csv/json. It refuses to claim
success for encrypted cache files that local media tools cannot decode.
"""

from __future__ import annotations

import argparse
import csv
import json
import mmap
import os
import platform
import re
import shutil
import subprocess
import struct
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field, fields as dataclass_fields
from pathlib import Path
from typing import Any


def default_cache_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/SodaMusic/LunaCacheV2"
    if os.name == "nt":
        appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
        return appdata / "SodaMusic/LunaCacheV2"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "SodaMusic/LunaCacheV2"


def default_sodamusic_app_path() -> Path:
    if sys.platform == "darwin":
        return Path("/Applications/汽水音乐.app")
    if os.name == "nt":
        local_appdata = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return local_appdata / "Programs/SodaMusic"
    return Path("/opt/SodaMusic")


DEFAULT_CACHE_DIR = default_cache_dir()
DEFAULT_SODAMUSIC_APP = default_sodamusic_app_path()
RECORD_MARKER = b"\xd4\x72\x40"
DEFAULT_MP3_BITRATE_KBPS = 192
DECODE_TEST_SECONDS = 1
IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 5
MAX_COVER_DOWNLOAD_ATTEMPTS = 4
MAX_COVER_BYTES = 5 * 1024 * 1024
IMAGE_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://music.douyin.com/",
}
ALLOWED_EXPORT_FORMATS = {"playable", "mp3", "flac", "original"}
QUALITY_RANK = {
    "lossless": 60,
    "hi_res": 50,
    "spatial": 40,
    "highest": 30,
    "higher": 20,
    "medium": 10,
}


class MsgpackrReader:
    """Small MessagePack reader with the msgpackr record extension SodaMusic uses."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.records: dict[int, list[str]] = {}

    def read(self, length: int) -> bytes:
        if self.pos + length > len(self.data):
            raise EOFError(f"unexpected end of data at {self.pos}")
        value = self.data[self.pos : self.pos + length]
        self.pos += length
        return value

    def unpack(self) -> Any:
        start = self.pos
        code = self.read(1)[0]

        # msgpackr record references are encoded as positive fixints after a
        # preceding ext type 0x72 record definition.
        if 0x40 <= code <= 0x7F and code in self.records:
            return {key: self.unpack() for key in self.records[code]}

        if code <= 0x7F:
            return code
        if code >= 0xE0:
            return code - 256
        if 0xA0 <= code <= 0xBF:
            return self.read(code & 0x1F).decode("utf-8", "replace")
        if 0x90 <= code <= 0x9F:
            return [self.unpack() for _ in range(code & 0x0F)]
        if 0x80 <= code <= 0x8F:
            return {self.unpack(): self.unpack() for _ in range(code & 0x0F)}

        if code == 0xC0:
            return None
        if code == 0xC1:
            return None
        if code == 0xC2:
            return False
        if code == 0xC3:
            return True
        if code == 0xC4:
            return self.read(self.read(1)[0])
        if code == 0xC5:
            return self.read(struct.unpack(">H", self.read(2))[0])
        if code == 0xC6:
            return self.read(struct.unpack(">I", self.read(4))[0])
        if code == 0xC7:
            return self.unpack_ext(self.read(1)[0])
        if code == 0xC8:
            return self.unpack_ext(struct.unpack(">H", self.read(2))[0])
        if code == 0xC9:
            return self.unpack_ext(struct.unpack(">I", self.read(4))[0])
        if code == 0xCA:
            return struct.unpack(">f", self.read(4))[0]
        if code == 0xCB:
            return struct.unpack(">d", self.read(8))[0]
        if code == 0xCC:
            return self.read(1)[0]
        if code == 0xCD:
            return struct.unpack(">H", self.read(2))[0]
        if code == 0xCE:
            return struct.unpack(">I", self.read(4))[0]
        if code == 0xCF:
            return struct.unpack(">Q", self.read(8))[0]
        if code == 0xD0:
            return struct.unpack(">b", self.read(1))[0]
        if code == 0xD1:
            return struct.unpack(">h", self.read(2))[0]
        if code == 0xD2:
            return struct.unpack(">i", self.read(4))[0]
        if code == 0xD3:
            return struct.unpack(">q", self.read(8))[0]
        if code in (0xD4, 0xD5, 0xD6, 0xD7, 0xD8):
            lengths = {0xD4: 1, 0xD5: 2, 0xD6: 4, 0xD7: 8, 0xD8: 16}
            result = self.unpack_ext(lengths[code])
            if result is None:
                return self.unpack()
            return result
        if code == 0xD9:
            return self.read(self.read(1)[0]).decode("utf-8", "replace")
        if code == 0xDA:
            return self.read(struct.unpack(">H", self.read(2))[0]).decode(
                "utf-8", "replace"
            )
        if code == 0xDB:
            return self.read(struct.unpack(">I", self.read(4))[0]).decode(
                "utf-8", "replace"
            )
        if code == 0xDC:
            return [self.unpack() for _ in range(struct.unpack(">H", self.read(2))[0])]
        if code == 0xDD:
            return [self.unpack() for _ in range(struct.unpack(">I", self.read(4))[0])]
        if code == 0xDE:
            return {
                self.unpack(): self.unpack()
                for _ in range(struct.unpack(">H", self.read(2))[0])
            }
        if code == 0xDF:
            return {
                self.unpack(): self.unpack()
                for _ in range(struct.unpack(">I", self.read(4))[0])
            }

        raise ValueError(f"unsupported msgpack code {code:#x} at offset {start}")

    def unpack_ext(self, payload_length: int) -> Any:
        ext_type = self.read(1)[0]
        payload = self.read(payload_length)

        if ext_type == 0x72:
            record_id = payload[0]
            keys = self.unpack()
            if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                raise ValueError(f"invalid msgpackr record definition {record_id}")
            self.records[record_id] = keys
            # Handle consecutive record definitions: keep processing while the
            # next byte is another ext marker for 0x72 (record definition).
            while self.pos < len(self.data):
                next_byte = self.data[self.pos]
                # Reference to this record - let unpack() handle it
                if 0x40 <= next_byte <= 0x7F:
                    return None
                # Another ext marker (0xC7..0xC9) - check if it's another 0x72 definition
                if next_byte in (0xC7, 0xC8, 0xC9):
                    # Peek ahead to see if it's another record definition
                    saved_pos = self.pos
                    try:
                        ext_code = self.data[self.pos]
                        self.pos += 1
                        if ext_code == 0xC7:
                            ext_len = self.data[self.pos]; self.pos += 1
                        elif ext_code == 0xC8:
                            ext_len = struct.unpack(">H", self.data[self.pos:self.pos+2])[0]; self.pos += 2
                        else:  # 0xC9
                            ext_len = struct.unpack(">I", self.data[self.pos:self.pos+4])[0]; self.pos += 4
                        inner_ext_type = self.data[self.pos]
                        if inner_ext_type == 0x72:
                            # It's another record definition - continue the loop
                            continue
                        else:
                            # Not a record definition - restore position and break
                            self.pos = saved_pos
                            break
                    except (IndexError, struct.error):
                        self.pos = saved_pos
                        break
                # Not a reference or another definition - read values inline
                break
            return {key: self.unpack() for key in keys}

        return {"__ext_type": ext_type, "__ext_data": payload.hex()}


@dataclass
class ExportRecord:
    source: Path
    output: Path | None
    track_id: str
    title: str
    artists: str
    album: str
    quality: str
    bitrate: int | None
    duration_ms: int | None
    cache_uuid: str
    resource_id: str
    extension: str
    source_extension: str
    indexed_extension: str
    indexed_codec_type: str
    output_format: str
    output_container: str
    output_codec_type: str
    output_sample_rate: int | None
    output_bits_per_sample: int | None
    output_probe_error: str
    output_matches_request: bool | None
    output_mismatch_reason: str
    source_size: int
    indexed_size: int | None
    encrypted: bool
    encryption_method: str
    index_key_id: str
    mp4_scheme: str
    mp4_key_id: str
    mp4_has_sample_encryption: bool
    decrypted: bool
    copied: bool
    skipped_reason: str
    cover_embedded: bool = False
    lyrics_embedded: bool = False
    metadata_error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "output": str(self.output) if self.output else "",
            "track_id": self.track_id,
            "title": self.title,
            "artists": self.artists,
            "album": self.album,
            "quality": self.quality,
            "bitrate": self.bitrate,
            "duration_ms": self.duration_ms,
            "cache_uuid": self.cache_uuid,
            "resource_id": self.resource_id,
            "extension": self.extension,
            "source_extension": self.source_extension,
            "indexed_extension": self.indexed_extension,
            "indexed_codec_type": self.indexed_codec_type,
            "output_format": self.output_format,
            "output_container": self.output_container,
            "output_codec_type": self.output_codec_type,
            "output_sample_rate": self.output_sample_rate,
            "output_bits_per_sample": self.output_bits_per_sample,
            "output_probe_error": self.output_probe_error,
            "output_matches_request": self.output_matches_request,
            "output_mismatch_reason": self.output_mismatch_reason,
            "source_size": self.source_size,
            "indexed_size": self.indexed_size,
            "encrypted": self.encrypted,
            "encryption_method": self.encryption_method,
            "index_key_id": self.index_key_id,
            "mp4_scheme": self.mp4_scheme,
            "mp4_key_id": self.mp4_key_id,
            "mp4_has_sample_encryption": self.mp4_has_sample_encryption,
            "decrypted": self.decrypted,
            "copied": self.copied,
            "skipped_reason": self.skipped_reason,
            "cover_embedded": self.cover_embedded,
            "lyrics_embedded": self.lyrics_embedded,
            "metadata_error": self.metadata_error,
        }


@dataclass(frozen=True)
class SourceCandidate:
    record: dict[str, Any]
    track_id: str
    title: str
    artists: str
    album: str
    duration_ms: int | None
    cache_uuid: str
    resource_id: str
    quality: str
    bitrate: int | None
    extension: str
    codec_type: str
    source_size: int
    encrypted: bool


@dataclass(frozen=True)
class CoverImage:
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class AudioProbeResult:
    container: str = ""
    codec_type: str = ""
    sample_rate: int | None = None
    bits_per_sample: int | None = None
    error: str = ""


@dataclass(frozen=True)
class AudioMetadata:
    title: str
    artists: str
    album: str
    lyrics: str
    cover_urls: tuple[str, ...]


@dataclass(frozen=True)
class MetadataWriteResult:
    cover_embedded: bool = False
    lyrics_embedded: bool = False
    error: str = ""


@dataclass
class ExportState:
    source: Path
    destination: Path
    working_source: Path
    source_extension: str
    media_encrypted: bool
    source_encrypted: bool
    decrypted: bool
    copied: bool
    skipped_reason: str
    temp_decrypted: Path | None = None
    metadata_result: MetadataWriteResult = field(default_factory=MetadataWriteResult)
    output_probe: AudioProbeResult = field(default_factory=AudioProbeResult)
    output_matches_request: bool | None = None
    output_mismatch_reason: str = ""


def parse_entries(entries_db: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with entries_db.open("rb") as fh:
        with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as data:
            for match in re.finditer(re.escape(RECORD_MARKER), data):
                try:
                    reader = MsgpackrReader(data[match.start():])
                    obj = reader.unpack()
                    if isinstance(obj, dict) and obj.get("chunkId") and obj.get("info"):
                        records.append(obj)
                except (EOFError, ValueError, struct.error):
                    continue
    return records


def find_track(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if value.get("type") == "track" and isinstance(value.get("track"), dict):
            return value["track"]
        if value.get("media_type") == "track" and "name" in value:
            return value
        for child in value.values():
            found = find_track(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_track(child)
            if found:
                return found
    return None


def compact_names(items: list[Any]) -> str:
    names: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("name") or item.get("simple_display_name")
        else:
            name = str(item)
        if name and name not in names:
            names.append(str(name))
    return ", ".join(names)


def safe_filename(value: str, fallback: str = "Unknown") -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def reserve_unique_path(path: Path, reserved: set[Path]) -> Path:
    candidate = unique_path(path)
    if candidate not in reserved:
        reserved.add(candidate)
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    parent = candidate.parent
    index = 2
    while True:
        next_candidate = parent / f"{stem} ({index}){suffix}"
        if next_candidate not in reserved and not next_candidate.exists():
            reserved.add(next_candidate)
            return next_candidate
        index += 1


def looks_like_mp3_frame(head: bytes) -> bool:
    return len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0


def sniff_extension(path: Path, indexed_vtype: str | None, codec: str | None) -> str:
    with path.open("rb") as fh:
        head = fh.read(32)

    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"ID3") or looks_like_mp3_frame(head):
        return "mp3"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        if codec == "flac":
            return "mp4"
        if indexed_vtype:
            return indexed_vtype.lower()
        return "m4a"

    return (indexed_vtype or "bin").lower()


def export_extension(source_extension: str, output_format: str) -> str:
    normalized_source = source_extension.lower()
    if output_format == "flac":
        return "flac"
    if output_format == "mp3":
        return "mp3"
    if output_format == "original":
        return normalized_source
    if normalized_source == "mp4":
        return "m4a"
    return normalized_source


def has_encrypted_mp4_sample_entry(path: Path) -> bool:
    with path.open("rb") as fh:
        head = fh.read(1024 * 1024)

    return (b"enca" in head or b"encv" in head) and b"sinf" in head


def iter_mp4_boxes(data: bytes, start: int, end: int) -> list[tuple[int, int, str, int]]:
    boxes: list[tuple[int, int, str, int]] = []
    offset = start
    while offset + 8 <= end:
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        box_type = data[offset + 4 : offset + 8].decode("latin1", "replace")
        header_size = 8
        if size == 1:
            if offset + 16 > end:
                break
            size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
            header_size = 16
        elif size == 0:
            size = end - offset

        if size < header_size or offset + size > end:
            break
        boxes.append((offset, size, box_type, header_size))
        offset += size
    return boxes


def mp4_child_start(
    box_type: str,
    offset: int,
    header_size: int,
) -> int | None:
    if box_type in {"moov", "trak", "mdia", "minf", "stbl", "sinf", "schi"}:
        return offset + header_size
    if box_type == "stsd":
        return offset + header_size + 8
    if box_type == "meta":
        return offset + header_size + 4
    if box_type in {"enca", "encv", "mp4a", "fLaC", "avc1", "hvc1"}:
        return offset + header_size + 28
    return None


def mp4_encryption_summary(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        data = fh.read(1024 * 1024)

    summary: dict[str, Any] = {
        "sample_entry": "",
        "original_format": "",
        "scheme": "",
        "key_id": "",
        "has_sample_encryption": False,
    }

    def walk(start: int, end: int) -> None:
        for offset, size, box_type, header_size in iter_mp4_boxes(data, start, end):
            payload_start = offset + header_size
            payload_end = offset + size
            if box_type in {"enca", "encv"}:
                summary["sample_entry"] = box_type
            elif box_type == "frma" and payload_start + 4 <= payload_end:
                summary["original_format"] = data[payload_start : payload_start + 4].decode(
                    "latin1", "replace"
                )
            elif box_type == "schm" and payload_start + 12 <= payload_end:
                summary["scheme"] = data[payload_start + 4 : payload_start + 8].decode(
                    "latin1", "replace"
                )
            elif box_type == "tenc" and payload_end - payload_start >= 20:
                summary["key_id"] = data[payload_end - 16 : payload_end].hex()
            elif box_type == "senc":
                summary["has_sample_encryption"] = True

            child_start = mp4_child_start(box_type, offset, header_size)
            if child_start is not None and child_start < payload_end:
                walk(child_start, payload_end)

    walk(0, len(data))
    return summary


def first_mp4_box(
    boxes: dict[str, list[tuple[int, int, int]]],
    box_type: str,
) -> tuple[int, int, int]:
    values = boxes.get(box_type) or []
    if not values:
        raise ValueError(f"MP4 box not found: {box_type}")
    return values[0]


def collect_mp4_boxes(data: bytes | bytearray) -> dict[str, list[tuple[int, int, int]]]:
    boxes: dict[str, list[tuple[int, int, int]]] = {}

    def walk(start: int, end: int) -> None:
        for offset, size, box_type, header_size in iter_mp4_boxes(data, start, end):
            boxes.setdefault(box_type, []).append((offset, size, header_size))
            child_start = mp4_child_start(box_type, offset, header_size)
            if child_start is not None and child_start < offset + size:
                walk(child_start, offset + size)

    walk(0, len(data))
    return boxes


def parse_stsz(data: bytes | bytearray, box: tuple[int, int, int]) -> list[int]:
    offset, _size, header_size = box
    payload = offset + header_size
    sample_size = struct.unpack(">I", data[payload + 4 : payload + 8])[0]
    sample_count = struct.unpack(">I", data[payload + 8 : payload + 12])[0]
    if sample_size:
        return [sample_size] * sample_count
    table_start = payload + 12
    return [
        struct.unpack(">I", data[table_start + index * 4 : table_start + index * 4 + 4])[0]
        for index in range(sample_count)
    ]


def parse_stco(data: bytes | bytearray, box: tuple[int, int, int]) -> list[int]:
    offset, _size, header_size = box
    payload = offset + header_size
    entry_count = struct.unpack(">I", data[payload + 4 : payload + 8])[0]
    table_start = payload + 8
    return [
        struct.unpack(">I", data[table_start + index * 4 : table_start + index * 4 + 4])[0]
        for index in range(entry_count)
    ]


def parse_co64(data: bytes | bytearray, box: tuple[int, int, int]) -> list[int]:
    offset, _size, header_size = box
    payload = offset + header_size
    entry_count = struct.unpack(">I", data[payload + 4 : payload + 8])[0]
    table_start = payload + 8
    return [
        struct.unpack(">Q", data[table_start + index * 8 : table_start + index * 8 + 8])[0]
        for index in range(entry_count)
    ]


def parse_stsc(data: bytes | bytearray, box: tuple[int, int, int]) -> list[tuple[int, int, int]]:
    offset, _size, header_size = box
    payload = offset + header_size
    entry_count = struct.unpack(">I", data[payload + 4 : payload + 8])[0]
    table_start = payload + 8
    return [
        struct.unpack(">III", data[table_start + index * 12 : table_start + index * 12 + 12])
        for index in range(entry_count)
    ]


def sample_offsets(
    sample_sizes: list[int],
    chunk_offsets: list[int],
    sample_to_chunk: list[tuple[int, int, int]],
) -> list[int]:
    offsets: list[int] = []
    sample_index = 0
    chunk_count = len(chunk_offsets)

    # Preprocess: build chunk_index -> stsc_entry lookup table
    # stsc entries have first_chunk in monotonic increasing order
    entry_for_chunk: dict[int, tuple[int, int, int]] = {}
    for i, entry in enumerate(sample_to_chunk):
        first_chunk = entry[0]
        end_chunk = sample_to_chunk[i + 1][0] if i + 1 < len(sample_to_chunk) else chunk_count + 1
        for c in range(first_chunk, end_chunk):
            entry_for_chunk[c] = entry

    for chunk_index, chunk_offset in enumerate(chunk_offsets, start=1):
        active_entry = entry_for_chunk.get(chunk_index)
        if active_entry is None:
            raise ValueError(f"no stsc entry for chunk {chunk_index}")

        position = chunk_offset
        samples_expected = active_entry[1]
        samples_added = 0
        for _ in range(samples_expected):
            if sample_index >= len(sample_sizes):
                break
            offsets.append(position)
            position += sample_sizes[sample_index]
            sample_index += 1
            samples_added += 1
        if samples_added != samples_expected:
            raise ValueError(
                f"stsc says {samples_expected} samples in chunk {chunk_index} "
                f"but only {samples_added} left"
            )

    if len(offsets) != len(sample_sizes):
        raise ValueError(f"sample offset count mismatch: {len(offsets)} != {len(sample_sizes)}")
    return offsets


def parse_senc(data: bytes | bytearray, box: tuple[int, int, int]) -> list[tuple[bytes, list[tuple[int, int]]]]:
    offset, size, header_size = box
    payload = offset + header_size
    payload_end = offset + size
    flags = int.from_bytes(data[payload : payload + 4], "big") & 0xFFFFFF
    sample_count = struct.unpack(">I", data[payload + 4 : payload + 8])[0]
    position = payload + 8
    samples: list[tuple[bytes, list[tuple[int, int]]]] = []

    for _ in range(sample_count):
        if position + 8 > payload_end:
            raise ValueError("truncated senc IV table")
        iv = bytes(data[position : position + 8])
        position += 8
        subsamples: list[tuple[int, int]] = []
        if flags & 0x02:
            if position + 2 > payload_end:
                raise ValueError("truncated senc subsample count")
            subsample_count = struct.unpack(">H", data[position : position + 2])[0]
            position += 2
            for _ in range(subsample_count):
                if position + 6 > payload_end:
                    raise ValueError("truncated senc subsample table")
                clear_size = struct.unpack(">H", data[position : position + 2])[0]
                encrypted_size = struct.unpack(">I", data[position + 2 : position + 6])[0]
                position += 6
                subsamples.append((clear_size, encrypted_size))
        samples.append((iv, subsamples))

    return samples


def encrypted_sample_entry_original_formats(data: bytes | bytearray) -> list[tuple[int, bytes]]:
    values: list[tuple[int, bytes]] = []

    def walk(start: int, end: int, encrypted_entry_offset: int | None = None) -> None:
        for offset, size, box_type, header_size in iter_mp4_boxes(data, start, end):
            payload_start = offset + header_size
            payload_end = offset + size
            next_encrypted_entry_offset = encrypted_entry_offset
            if box_type in {"enca", "encv"}:
                next_encrypted_entry_offset = offset
            elif box_type == "frma" and encrypted_entry_offset is not None and payload_start + 4 <= payload_end:
                values.append((encrypted_entry_offset, bytes(data[payload_start : payload_start + 4])))

            child_start = mp4_child_start(box_type, offset, header_size)
            if child_start is not None and child_start < payload_end:
                walk(child_start, payload_end, next_encrypted_entry_offset)

    walk(0, len(data))
    return values


def decrypt_cenc_mp4(source: Path, destination: Path, key_hex: str) -> None:
    try:
        from Crypto.Cipher import AES
    except ImportError as exc:
        raise RuntimeError("pycryptodome is required for offline CENC decryption") from exc

    key = bytes.fromhex(key_hex)
    if len(key) != 16:
        raise RuntimeError(f"decoded CENC key must be 16 bytes, got {len(key)}")

    data = bytearray(source.read_bytes())
    boxes = collect_mp4_boxes(data)
    sample_sizes = parse_stsz(data, first_mp4_box(boxes, "stsz"))
    chunk_offsets = (
        parse_stco(data, first_mp4_box(boxes, "stco"))
        if boxes.get("stco")
        else parse_co64(data, first_mp4_box(boxes, "co64"))
    )
    offsets = sample_offsets(sample_sizes, chunk_offsets, parse_stsc(data, first_mp4_box(boxes, "stsc")))
    encryption_samples = parse_senc(data, first_mp4_box(boxes, "senc"))
    if len(encryption_samples) != len(sample_sizes):
        raise RuntimeError(
            f"senc sample count mismatch: {len(encryption_samples)} != {len(sample_sizes)}"
        )

    for offset, size, (iv, subsamples) in zip(offsets, sample_sizes, encryption_samples):
        # CENC standard: IV is used as AES-CTR nonce, counter starts at 0
        cipher = AES.new(key, AES.MODE_CTR, nonce=iv, initial_value=0)
        if subsamples:
            position = offset
            for clear_size, encrypted_size in subsamples:
                position += clear_size
                encrypted = bytes(data[position : position + encrypted_size])
                data[position : position + encrypted_size] = cipher.decrypt(encrypted)
                position += encrypted_size
        else:
            encrypted = bytes(data[offset : offset + size])
            data[offset : offset + size] = cipher.decrypt(encrypted)

    original_formats = dict(encrypted_sample_entry_original_formats(data))
    for offset, _size, _header_size in boxes.get("enca", []):
        data[offset + 4 : offset + 8] = original_formats.get(offset, b"mp4a")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_output = destination.parent / f".{destination.stem}.tmp{destination.suffix}"
    temp_output.write_bytes(data)
    temp_output.replace(destination)


def sodamusic_app_candidates() -> list[Path]:
    if sys.platform == "darwin":
        return [
            Path("/Applications/汽水音乐.app"),
            Path.home() / "Applications/汽水音乐.app",
            Path("/Applications/SodaMusic.app"),
            Path.home() / "Applications/SodaMusic.app",
        ]
    if os.name == "nt":
        local_appdata = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return [
            local_appdata / "Programs/SodaMusic",
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "SodaMusic",
        ]
    return [Path("/opt/SodaMusic")]


def device_node_candidates(app_path: Path) -> list[Path]:
    arch = platform.machine().lower()
    unpacked_name = "app-arm64.asar.unpacked" if arch == "arm64" else "app-x64.asar.unpacked"
    if sys.platform == "darwin":
        return [
            app_path / "Contents/Resources" / unpacked_name / "device.node",
            app_path / "Contents/Resources/app.asar.unpacked/device.node",
            app_path / "device.node",
        ]
    return [
        app_path / "resources" / unpacked_name / "device.node",
        app_path / "resources/app.asar.unpacked/device.node",
        app_path / unpacked_name / "device.node",
        app_path / "device.node",
    ]


def default_device_node_path(app_path: Path | None = None) -> Path:
    app_paths = [app_path] if app_path is not None else sodamusic_app_candidates()
    candidates = [
        candidate
        for candidate_app_path in app_paths
        for candidate in device_node_candidates(candidate_app_path)
    ]
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


def resolve_device_node_path(value: Path | str | None = None) -> Path:
    if value is None or not str(value).strip():
        return default_device_node_path()
    path = Path(value).expanduser()
    if path.name == "device.node" or (path.exists() and path.is_file()):
        return path
    return default_device_node_path(path)


def decode_spade(spade: str, device_node: Path) -> str:
    script = """
const deviceNode = process.argv[1];
const spade = process.argv[2];
const device = require(deviceNode);
process.stdout.write(String(device.decodeSpade(spade)));
"""
    result = subprocess.run(
        ["node", "-e", script, str(device_node), spade],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = compact_error(result.stderr or result.stdout)
        raise RuntimeError(message or "device.node decodeSpade failed")
    key_hex = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", key_hex):
        raise RuntimeError(f"decodeSpade returned an unexpected key: {key_hex!r}")
    return key_hex.lower()


def decode_spades(spades: list[str], device_node: Path) -> dict[str, str]:
    unique_spades = list(dict.fromkeys(spade for spade in spades if spade))
    if not unique_spades:
        return {}

    script = """
const deviceNode = process.argv[1];
const spades = JSON.parse(process.argv[2]);
const device = require(deviceNode);
const result = {};
for (const spade of spades) {
  result[spade] = String(device.decodeSpade(spade));
}
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(
        ["node", "-e", script, str(device_node), json.dumps(unique_spades)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = compact_error(result.stderr or result.stdout)
        raise RuntimeError(message or "device.node batch decodeSpade failed")

    try:
        decoded = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("device.node batch decodeSpade returned invalid JSON") from exc

    keys: dict[str, str] = {}
    for spade, key_hex in decoded.items():
        if not re.fullmatch(r"[0-9a-fA-F]{32}", str(key_hex)):
            raise RuntimeError(f"decodeSpade returned an unexpected key: {key_hex!r}")
        keys[spade] = str(key_hex).lower()
    return keys


def find_mp3_transcoder() -> str | None:
    return shutil.which("ffmpeg")


def compact_error(value: str, limit: int = 400) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def run_media_command(command: list[str]) -> tuple[bool, str]:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        return True, ""
    return False, compact_error(result.stderr or result.stdout)


def probe_audio_output(path: Path) -> AudioProbeResult:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return AudioProbeResult(error="ffprobe not found")

    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "format=format_name:stream=codec_name,sample_rate,bits_per_raw_sample,bits_per_sample",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return AudioProbeResult(error=compact_error(result.stderr or result.stdout))
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return AudioProbeResult(error=f"ffprobe returned invalid JSON: {exc}")

    streams = payload.get("streams") if isinstance(payload, dict) else None
    stream = streams[0] if isinstance(streams, list) and streams and isinstance(streams[0], dict) else {}
    fmt = payload.get("format") if isinstance(payload, dict) and isinstance(payload.get("format"), dict) else {}

    def parse_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return AudioProbeResult(
        container=str(fmt.get("format_name") or ""),
        codec_type=str(stream.get("codec_name") or ""),
        sample_rate=parse_int(stream.get("sample_rate")),
        bits_per_sample=parse_int(stream.get("bits_per_raw_sample") or stream.get("bits_per_sample")),
    )


def output_match_result(
    *,
    output_format: str,
    extension: str,
    probe: AudioProbeResult,
) -> tuple[bool | None, str]:
    if output_format == "original":
        return None, ""
    if probe.error:
        return False, probe.error
    if not probe.codec_type:
        return False, "no audio codec detected"

    normalized_format = output_format.lower()
    normalized_extension = extension.lower()
    normalized_container = probe.container.lower()
    normalized_codec = probe.codec_type.lower()

    if normalized_format == "flac":
        if normalized_codec == "flac" and "flac" in normalized_container:
            return True, ""
        return False, f"expected flac/flac, got {probe.container or 'unknown'}/{probe.codec_type}"
    if normalized_format == "mp3":
        if normalized_codec == "mp3":
            return True, ""
        return False, f"expected mp3 codec, got {probe.codec_type}"
    if normalized_format == "playable":
        if normalized_extension == "flac" and normalized_codec != "flac":
            return False, f"expected flac codec, got {probe.codec_type}"
        if normalized_extension == "mp3" and normalized_codec != "mp3":
            return False, f"expected mp3 codec, got {probe.codec_type}"
        return True, ""
    return None, ""


def can_decode_audio(path: Path, extension: str) -> tuple[bool, str]:
    if extension == "mp3":
        return True, ""

    if ffmpeg := shutil.which("ffmpeg"):
        return run_media_command(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-t",
                str(DECODE_TEST_SECONDS),
                "-i",
                str(path),
                "-f",
                "null",
                "-",
            ]
        )

    if afconvert := shutil.which("afconvert"):
        with tempfile.NamedTemporaryFile(prefix="soda-decode-", suffix=".wav", delete=False) as fh:
            temp_output = Path(fh.name)
        try:
            return run_media_command(
                [
                    afconvert,
                    "--offset",
                    "0",
                    str(path),
                    str(temp_output),
                    "-f",
                    "WAVE",
                    "-d",
                    "LEI16",
                ]
            )
        finally:
            temp_output.unlink(missing_ok=True)

    return False, "audio decoder not found; install ffmpeg"


def transcode_to_mp3(
    source: Path,
    destination: Path,
    *,
    bitrate_kbps: int,
    transcoder: str,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_output = destination.parent / f".{destination.stem}.tmp.mp3"
    temp_output.unlink(missing_ok=True)

    transcoder_name = Path(transcoder).name
    if transcoder_name == "ffmpeg":
        command = [
            transcoder,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            f"{bitrate_kbps}k",
            str(temp_output),
        ]
    else:
        raise RuntimeError(f"unsupported transcoder: {transcoder}")

    try:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            message = compact_error(result.stderr or result.stdout)
            raise RuntimeError(message or f"{transcoder_name} exited {result.returncode}")
        if not temp_output.exists() or temp_output.stat().st_size == 0:
            raise RuntimeError(f"{transcoder_name} did not create an MP3 file")
        if sniff_extension(temp_output, "mp3", None) != "mp3":
            raise RuntimeError(f"{transcoder_name} output is not an MP3 file")
        temp_output.replace(destination)
    finally:
        temp_output.unlink(missing_ok=True)


def transcode_to_flac(
    source: Path,
    destination: Path,
    *,
    transcoder: str,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_output = destination.parent / f".{destination.stem}.tmp.flac"
    temp_output.unlink(missing_ok=True)

    transcoder_name = Path(transcoder).name
    if transcoder_name == "ffmpeg":
        command = [
            transcoder,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-codec:a",
            "flac",
            "-compression_level",
            "8",
            str(temp_output),
        ]
    else:
        raise RuntimeError(f"unsupported transcoder: {transcoder}")

    try:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            message = compact_error(result.stderr or result.stdout)
            raise RuntimeError(message or f"{transcoder_name} exited {result.returncode}")
        if not temp_output.exists() or temp_output.stat().st_size == 0:
            raise RuntimeError(f"{transcoder_name} did not create a FLAC file")
        if sniff_extension(temp_output, "flac", None) != "flac":
            raise RuntimeError(f"{transcoder_name} output is not a FLAC file")
        temp_output.replace(destination)
    finally:
        temp_output.unlink(missing_ok=True)


_cover_cache_lock = threading.Lock()
COVER_IMAGE_CACHE: dict[str, CoverImage | str] = {}


def unique_strings(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


def image_url_root(base_url: str, uri: str) -> str:
    base_url = base_url.strip()
    uri = uri.strip()
    if not uri:
        return base_url
    if uri.startswith(("http://", "https://")):
        return uri
    if base_url.endswith("/") or uri.startswith("/"):
        return f"{base_url}{uri.lstrip('/')}"
    return f"{base_url}/{uri}"


def cover_urls_from_value(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip().startswith(("http://", "https://")) else ()
    if not isinstance(value, dict):
        return ()

    direct_urls: list[str] = []
    for key in ("url", "cover_url", "download_url"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip().startswith(("http://", "https://")):
            direct_urls.append(raw.strip())

    raw_urls = value.get("urls") or value.get("url_list") or value.get("urlList") or []
    if isinstance(raw_urls, str):
        raw_urls = [raw_urls]
    if not isinstance(raw_urls, list):
        raw_urls = []

    uri = str(value.get("uri") or "").strip()
    template_prefix = str(value.get("template_prefix") or "").strip()
    roots = [
        image_url_root(str(raw_url), uri)
        for raw_url in raw_urls
        if isinstance(raw_url, str) and raw_url.strip().startswith(("http://", "https://"))
    ]

    candidates: list[str] = [*direct_urls]
    if uri:
        candidates.extend(f"{root}~300x300.image" for root in roots)
        candidates.extend(f"{root}~noop.image" for root in roots)
        if template_prefix:
            candidates.extend(f"{root}~{template_prefix}-image.image" for root in roots)
        candidates.extend(roots)
    else:
        candidates.extend(roots)

    return unique_strings(candidates)


def first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def extract_cover_urls(record: dict[str, Any]) -> tuple[str, ...]:
    info = record.get("info") or {}
    detail = info.get("mediaDetail") or {}
    playable = first_dict(detail.get("playable"))
    playable_track = first_dict(playable.get("track"))
    response = first_dict(detail.get("response"))
    response_track = first_dict(response.get("track"))
    track = find_track(record) or {}

    values = [
        playable.get("cover_url"),
        first_dict(playable.get("album")).get("url_cover"),
        first_dict(playable_track.get("album")).get("url_cover"),
        first_dict(response_track.get("album")).get("url_cover"),
        first_dict(track.get("album")).get("url_cover"),
    ]
    urls: list[str] = []
    for value in values:
        urls.extend(cover_urls_from_value(value))
    return unique_strings(urls)


def format_lrc_time(milliseconds: int) -> str:
    minutes = milliseconds // 60000
    seconds = (milliseconds % 60000) // 1000
    centiseconds = (milliseconds % 1000) // 10
    return f"{minutes:02}:{seconds:02}.{centiseconds:02}"


def krc_to_lrc(content: str) -> str:
    lines: list[str] = []
    for raw_line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^\[(\d+),\d+\](.*)$", line)
        if not match:
            lines.append(line)
            continue
        text = re.sub(r"<\d+,\d+,\d+>", "", match.group(2)).strip()
        if text:
            lines.append(f"[{format_lrc_time(int(match.group(1)))}]{text}")
    return "\n".join(lines)


def normalize_lyrics(content: str, lyric_type: str = "") -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ""
    if lyric_type.lower() == "krc" or re.search(r"(?m)^\[\d+,\d+\]", normalized):
        return krc_to_lrc(normalized)
    return normalized


def lyrics_from_value(value: Any) -> str:
    if isinstance(value, str):
        return normalize_lyrics(value)
    if not isinstance(value, dict):
        return ""
    for key in ("content", "lrc", "lyric", "original_lyric", "originalLyric"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return normalize_lyrics(raw, str(value.get("type") or ""))
    return ""


def extract_lyrics(record: dict[str, Any]) -> str:
    info = record.get("info") or {}
    detail = info.get("mediaDetail") or {}
    response = first_dict(detail.get("response"))
    for value in (detail.get("lyrics"), response.get("lyric")):
        lyrics = lyrics_from_value(value)
        if lyrics:
            return lyrics
    return ""


def audio_metadata_from_record(
    record: dict[str, Any],
    *,
    title: str,
    artists: str,
    album: str,
) -> AudioMetadata:
    return AudioMetadata(
        title=title,
        artists=artists,
        album=album,
        lyrics=extract_lyrics(record),
        cover_urls=extract_cover_urls(record),
    )


def infer_image_mime_type(data: bytes, content_type: str = "") -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    normalized_content_type = content_type.split(";", 1)[0].strip().lower()
    if normalized_content_type in {"image/jpeg", "image/png", "image/webp"}:
        return normalized_content_type
    return ""


def download_cover_image(urls: tuple[str, ...]) -> tuple[CoverImage | None, str]:
    last_error = ""
    for url in urls[:MAX_COVER_DOWNLOAD_ATTEMPTS]:
        with _cover_cache_lock:
            cached = COVER_IMAGE_CACHE.get(url)
        if isinstance(cached, CoverImage):
            return cached, ""
        if isinstance(cached, str):
            last_error = cached
            continue

        try:
            request = urllib.request.Request(url, headers=IMAGE_REQUEST_HEADERS)
            with urllib.request.urlopen(request, timeout=IMAGE_DOWNLOAD_TIMEOUT_SECONDS) as response:
                content_type = response.headers.get("Content-Type", "")
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > MAX_COVER_BYTES:
                            raise RuntimeError("image is larger than 5 MiB")
                    except ValueError:
                        pass
                data = response.read(MAX_COVER_BYTES + 1)
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            last_error = compact_error(str(exc))
            with _cover_cache_lock:
                COVER_IMAGE_CACHE[url] = last_error
            continue

        if len(data) > MAX_COVER_BYTES:
            last_error = "image is larger than 5 MiB"
            with _cover_cache_lock:
                COVER_IMAGE_CACHE[url] = last_error
            continue
        mime_type = infer_image_mime_type(data, content_type)
        if not mime_type:
            last_error = f"unsupported image response: {content_type or 'unknown content type'}"
            with _cover_cache_lock:
                COVER_IMAGE_CACHE[url] = last_error
            continue

        image = CoverImage(data=data, mime_type=mime_type)
        with _cover_cache_lock:
            COVER_IMAGE_CACHE[url] = image
        return image, ""
    return None, last_error


def write_mp4_metadata(path: Path, metadata: AudioMetadata, cover: CoverImage | None) -> tuple[bool, bool]:
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(path)
    audio["\xa9nam"] = [metadata.title]
    audio["\xa9ART"] = [metadata.artists]
    if metadata.album:
        audio["\xa9alb"] = [metadata.album]
    else:
        audio.pop("\xa9alb", None)
    lyrics_embedded = bool(metadata.lyrics)
    if lyrics_embedded:
        audio["\xa9lyr"] = [metadata.lyrics]
    else:
        audio.pop("\xa9lyr", None)

    cover_embedded = False
    if cover and cover.mime_type in {"image/jpeg", "image/png"}:
        image_format = (
            MP4Cover.FORMAT_PNG if cover.mime_type == "image/png" else MP4Cover.FORMAT_JPEG
        )
        audio["covr"] = [MP4Cover(cover.data, imageformat=image_format)]
        cover_embedded = True
    elif cover:
        audio.pop("covr", None)
    audio.save()
    return cover_embedded, lyrics_embedded


def write_mp3_metadata(path: Path, metadata: AudioMetadata, cover: CoverImage | None) -> tuple[bool, bool]:
    from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, USLT, ID3NoHeaderError

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    tags.setall("TIT2", [TIT2(encoding=3, text=metadata.title)])
    tags.setall("TPE1", [TPE1(encoding=3, text=metadata.artists)])
    if metadata.album:
        tags.setall("TALB", [TALB(encoding=3, text=metadata.album)])
    else:
        tags.delall("TALB")

    lyrics_embedded = bool(metadata.lyrics)
    tags.delall("USLT")
    if lyrics_embedded:
        tags.add(USLT(encoding=3, lang="und", desc="", text=metadata.lyrics))

    cover_embedded = bool(cover)
    tags.delall("APIC")
    if cover:
        tags.add(
            APIC(
                encoding=3,
                mime=cover.mime_type,
                type=3,
                desc="Cover",
                data=cover.data,
            )
        )

    tags.save(path, v2_version=3)
    return cover_embedded, lyrics_embedded


def write_flac_metadata(path: Path, metadata: AudioMetadata, cover: CoverImage | None) -> tuple[bool, bool]:
    from mutagen.flac import FLAC, Picture

    audio = FLAC(path)
    audio["title"] = [metadata.title]
    audio["artist"] = [metadata.artists]
    if metadata.album:
        audio["album"] = [metadata.album]
    elif "album" in audio:
        del audio["album"]

    lyrics_embedded = bool(metadata.lyrics)
    if lyrics_embedded:
        audio["lyrics"] = [metadata.lyrics]
    elif "lyrics" in audio:
        del audio["lyrics"]

    cover_embedded = bool(cover)
    audio.clear_pictures()
    if cover:
        picture = Picture()
        picture.type = 3
        picture.mime = cover.mime_type
        picture.desc = "Cover"
        picture.data = cover.data
        audio.add_picture(picture)

    audio.save()
    return cover_embedded, lyrics_embedded


def write_audio_metadata(path: Path, extension: str, metadata: AudioMetadata) -> MetadataWriteResult:
    cover: CoverImage | None = None
    cover_error = ""
    if metadata.cover_urls:
        cover, cover_error = download_cover_image(metadata.cover_urls)

    normalized_extension = extension.lower()
    try:
        if normalized_extension in {"m4a", "mp4"}:
            cover_embedded, lyrics_embedded = write_mp4_metadata(path, metadata, cover)
        elif normalized_extension == "mp3":
            cover_embedded, lyrics_embedded = write_mp3_metadata(path, metadata, cover)
        elif normalized_extension == "flac":
            cover_embedded, lyrics_embedded = write_flac_metadata(path, metadata, cover)
        else:
            return MetadataWriteResult(error=f"metadata tagging unsupported for .{extension}")
    except ImportError:
        return MetadataWriteResult(
            error="metadata tagging requires mutagen; run python3 -m pip install -r requirements.txt"
        )
    except Exception as exc:
        return MetadataWriteResult(error=f"metadata write failed: {compact_error(str(exc))}")

    errors: list[str] = []
    if metadata.cover_urls and not cover_embedded:
        if cover_error:
            errors.append(f"cover download failed: {cover_error}")
        elif cover:
            errors.append(f"cover format unsupported: {cover.mime_type}")
    return MetadataWriteResult(
        cover_embedded=cover_embedded,
        lyrics_embedded=lyrics_embedded,
        error="; ".join(errors),
    )


def video_items(record: dict[str, Any]) -> list[dict[str, Any]]:
    detail = (((record.get("info") or {}).get("mediaDetail") or {}).get("video_model") or {})
    video_list = detail.get("video_list") or []
    return [item for item in video_list if isinstance(item, dict)]


def track_identity(record: dict[str, Any]) -> tuple[str, str, str, str, int | None]:
    info = record.get("info") or {}
    track = find_track(record) or {}
    cache_uuid = str(record.get("chunkId") or "")
    track_id = str(info.get("trackId") or track.get("id") or cache_uuid)
    title = str(track.get("name") or f"track-{track_id or cache_uuid}")
    artists = compact_names(track.get("artists") or []) or "Unknown Artist"
    album_obj = track.get("album") if isinstance(track.get("album"), dict) else {}
    album = str(album_obj.get("name") or "")
    duration_ms = track.get("duration")
    return track_id, title, artists, album, duration_ms if isinstance(duration_ms, int) else None


def selected_video_item(
    record: dict[str, Any],
    *,
    source_size: int | None,
) -> dict[str, Any]:
    items = video_items(record)
    if not items:
        return {}

    info = record.get("info") or {}
    quality = str(info.get("quality") or "")
    if source_size:
        for item in items:
            video_meta = item.get("video_meta") or {}
            if video_meta.get("size") == source_size:
                return item
    if quality:
        for item in items:
            video_meta = item.get("video_meta") or {}
            if video_meta.get("quality") == quality:
                return item
    return items[0]


def first_video_meta(record: dict[str, Any]) -> dict[str, Any]:
    item = selected_video_item(record, source_size=None)
    if item:
        return item.get("video_meta") or {}
    return {}


def is_encrypted(record: dict[str, Any], source_size: int | None = None) -> bool:
    item = selected_video_item(record, source_size=source_size)
    if item:
        encrypt_info = item.get("encrypt_info") or {}
        return bool(encrypt_info.get("encrypt"))
    return "audio_encrypt" in str(record.get("resourceId", ""))


def record_spade(record: dict[str, Any], source_size: int | None = None) -> str:
    info = record.get("info") or {}
    item = selected_video_item(record, source_size=source_size)
    encrypt_info = item.get("encrypt_info") or {}
    return str(info.get("spade") or encrypt_info.get("spade_a") or "")


def quality_rank(quality: str, codec_type: str = "", extension: str = "") -> int:
    normalized_quality = quality.lower()
    normalized_codec = codec_type.lower()
    normalized_extension = extension.lower()
    if normalized_codec == "flac" or normalized_extension == "flac":
        return QUALITY_RANK["lossless"]
    return QUALITY_RANK.get(normalized_quality, 0)


def source_candidate(record: dict[str, Any], cache_dir: Path) -> SourceCandidate | None:
    cache_uuid = str(record.get("chunkId") or "")
    source = cache_dir / f"{cache_uuid}.bin"
    if not cache_uuid or not source.exists():
        return None

    source_size = source.stat().st_size
    item = selected_video_item(record, source_size=source_size)
    video_meta = item.get("video_meta") or {}
    info = record.get("info") or {}
    track_id, title, artists, album, duration_ms = track_identity(record)
    extension = sniff_extension(source, video_meta.get("vtype"), video_meta.get("codec_type"))
    quality = str(info.get("quality") or video_meta.get("quality") or "")
    bitrate = info.get("bitrate") or video_meta.get("bitrate")
    return SourceCandidate(
        record=record,
        track_id=track_id,
        title=title,
        artists=artists,
        album=album,
        duration_ms=duration_ms,
        cache_uuid=cache_uuid,
        resource_id=str(record.get("resourceId") or ""),
        quality=quality,
        bitrate=bitrate if isinstance(bitrate, int) else None,
        extension=extension,
        codec_type=str(video_meta.get("codec_type") or ""),
        source_size=source_size,
        encrypted=is_encrypted(record, source_size=source_size),
    )


def record_indexed_size(record: dict[str, Any], video_meta: dict[str, Any]) -> int | None:
    if isinstance(video_meta.get("size"), int):
        return video_meta["size"]
    return record.get("size") if isinstance(record.get("size"), int) else None


def candidate_sort_key(candidate: SourceCandidate) -> tuple[int, int, int]:
    return (
        quality_rank(candidate.quality, candidate.codec_type, candidate.extension),
        candidate.bitrate or 0,
        candidate.source_size,
    )


def indexed_candidate_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
    video_meta = item.get("video_meta") or {}
    return (
        quality_rank(
            str(video_meta.get("quality") or ""),
            str(video_meta.get("codec_type") or ""),
            str(video_meta.get("vtype") or ""),
        ),
        video_meta.get("bitrate") if isinstance(video_meta.get("bitrate"), int) else 0,
        video_meta.get("size") if isinstance(video_meta.get("size"), int) else 0,
    )


def candidate_summary_sort_key(summary: dict[str, Any]) -> tuple[int, int, int]:
    return (
        quality_rank(
            str(summary.get("quality") or ""),
            str(summary.get("codecType") or ""),
            str(summary.get("extension") or ""),
        ),
        summary.get("bitrate") if isinstance(summary.get("bitrate"), int) else 0,
        summary.get("sourceSize") if isinstance(summary.get("sourceSize"), int) else 0,
    )


def uncached_best_candidate(
    record: dict[str, Any],
    cached_best: SourceCandidate,
    cached_sizes: set[int],
) -> dict[str, Any] | None:
    cached_key = candidate_sort_key(cached_best)
    candidates: list[dict[str, Any]] = []
    for item in video_items(record):
        video_meta = item.get("video_meta") or {}
        size = video_meta.get("size")
        if isinstance(size, int) and size in cached_sizes:
            continue
        key = indexed_candidate_sort_key(item)
        if key <= cached_key:
            continue
        candidates.append(item)
    if not candidates:
        return None
    best = max(candidates, key=indexed_candidate_sort_key)
    video_meta = best.get("video_meta") or {}
    encrypt_info = best.get("encrypt_info") or {}
    return {
        "quality": str(video_meta.get("quality") or ""),
        "bitrate": video_meta.get("bitrate") if isinstance(video_meta.get("bitrate"), int) else None,
        "extension": "mp4" if video_meta.get("codec_type") == "flac" else str(video_meta.get("vtype") or ""),
        "codecType": str(video_meta.get("codec_type") or ""),
        "sourceSize": video_meta.get("size") if isinstance(video_meta.get("size"), int) else None,
        "encrypted": bool(encrypt_info.get("encrypt")),
    }


def cached_candidates_for_track(records: list[dict[str, Any]], cache_dir: Path) -> list[SourceCandidate]:
    candidates = [candidate for record in records if (candidate := source_candidate(record, cache_dir))]
    return sorted(candidates, key=candidate_sort_key, reverse=True)


def source_rows(records: list[dict[str, Any]], cache_dir: Path) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        track_id, _, _, _, _ = track_identity(record)
        grouped.setdefault(track_id, []).append(record)

    rows: list[dict[str, Any]] = []
    for track_id, group in grouped.items():
        cached_candidates = cached_candidates_for_track(group, cache_dir)
        if not cached_candidates:
            first_track_id, title, artists, album, duration_ms = track_identity(group[0])
            rows.append(
                {
                    "trackId": first_track_id,
                    "title": title,
                    "artists": artists,
                    "album": album,
                    "durationMs": duration_ms,
                    "cacheUuid": "",
                    "quality": "",
                    "bitrate": None,
                    "extension": "",
                    "codecType": "",
                    "sourceSize": 0,
                    "encrypted": is_encrypted(group[0]),
                    "selected": False,
                    "defaultFormat": "playable",
                    "cachedCandidates": [],
                    "uncachedBest": None,
                }
            )
            continue

        best = cached_candidates[0]
        uncached_best = None
        cached_sizes = {candidate.source_size for candidate in cached_candidates}
        for record in group:
            record_uncached = uncached_best_candidate(record, best, cached_sizes)
            if record_uncached and (
                not uncached_best
                or candidate_summary_sort_key(record_uncached) > candidate_summary_sort_key(uncached_best)
            ):
                uncached_best = record_uncached

        rows.append(
            {
                "trackId": track_id,
                "title": best.title,
                "artists": best.artists,
                "album": best.album,
                "durationMs": best.duration_ms,
                "cacheUuid": best.cache_uuid,
                "quality": best.quality,
                "bitrate": best.bitrate,
                "extension": best.extension,
                "codecType": best.codec_type,
                "sourceSize": best.source_size,
                "encrypted": best.encrypted,
                "selected": True,
                "defaultFormat": "playable",
                "cachedCandidates": [
                    {
                        "cacheUuid": candidate.cache_uuid,
                        "quality": candidate.quality,
                        "bitrate": candidate.bitrate,
                        "extension": candidate.extension,
                        "codecType": candidate.codec_type,
                        "sourceSize": candidate.source_size,
                        "encrypted": candidate.encrypted,
                    }
                    for candidate in cached_candidates
                ],
                "uncachedBest": uncached_best,
            }
        )

    return sorted(rows, key=lambda row: (str(row["artists"]).lower(), str(row["title"]).lower()))


def _resolve_source_state(
    state: ExportState,
    record: dict[str, Any],
    *,
    dry_run: bool,
    output_format: str,
    mp3_bitrate_kbps: int,
    mp3_transcoder: str | None,
    device_node: Path | None,
    fixed_key_hex: str | None,
    decoded_spades: dict[str, str],
    verify_audio: bool,
) -> None:
    source_size = state.source.stat().st_size if state.source.exists() else 0

    if state.media_encrypted and state.source_extension in {"m4a", "mp4"} and output_format != "original":
        spade = record_spade(record, source_size=source_size or None)
        if not fixed_key_hex and not spade:
            state.skipped_reason = "encrypted media is missing spade key material"
        elif not fixed_key_hex and not device_node:
            state.skipped_reason = "SodaMusic device.node not found; cannot decode spade key"
        elif not fixed_key_hex and not shutil.which("node"):
            state.skipped_reason = "node executable not found; cannot load SodaMusic device.node"
        else:
            try:
                key_hex = fixed_key_hex or decoded_spades.get(spade) or decode_spade(spade, device_node)
                if output_format == "playable" and state.source_extension == "m4a" and not dry_run:
                    decrypt_cenc_mp4(state.source, state.destination, key_hex)
                    state.working_source = state.destination
                    state.copied = True
                else:
                    with tempfile.NamedTemporaryFile(
                        prefix="soda-decrypted-", suffix=f".{state.source_extension}", delete=False
                    ) as fh:
                        state.temp_decrypted = Path(fh.name)
                    state.temp_decrypted.unlink(missing_ok=True)
                    decrypt_cenc_mp4(state.source, state.temp_decrypted, key_hex)
                    state.working_source = state.temp_decrypted
                state.media_encrypted = False
                state.decrypted = True
            except Exception as exc:
                if state.temp_decrypted:
                    state.temp_decrypted.unlink(missing_ok=True)
                state.skipped_reason = f"offline decrypt failed: {compact_error(str(exc))}"


def _transcode_or_copy(
    state: ExportState,
    *,
    dry_run: bool,
    output_format: str,
    mp3_bitrate_kbps: int,
    mp3_transcoder: str | None,
    verify_audio: bool,
    output_dir: Path,
) -> None:
    source_size = state.source.stat().st_size if state.source.exists() else 0

    try:
        if state.skipped_reason:
            pass
        elif output_format == "playable":
            if dry_run:
                state.skipped_reason = "dry run"
            elif state.copied:
                if state.decrypted or verify_audio:
                    decodable, decode_error = can_decode_audio(state.working_source, state.source_extension)
                    if not decodable:
                        state.working_source.unlink(missing_ok=True)
                        state.copied = False
                        state.skipped_reason = f"audio decode failed: {decode_error}"
            else:
                decodable, decode_error = can_decode_audio(state.working_source, state.source_extension)
                if not decodable:
                    if state.media_encrypted:
                        state.skipped_reason = (
                            "encrypted media cannot be decoded to playable audio"
                            + (f": {decode_error}" if decode_error else "")
                        )
                    else:
                        state.skipped_reason = f"audio decode failed: {decode_error}"
                else:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(state.working_source, state.destination)
                    state.copied = True
        elif output_format == "mp3":
            if state.source_extension == "mp3":
                if dry_run:
                    state.skipped_reason = "dry run"
                else:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(state.working_source, state.destination)
                    state.copied = True
            elif state.media_encrypted:
                decodable, decode_error = can_decode_audio(state.working_source, state.source_extension)
                if decodable and dry_run:
                    state.skipped_reason = "dry run"
                elif decodable and mp3_transcoder:
                    try:
                        transcode_to_mp3(
                            state.working_source,
                            state.destination,
                            bitrate_kbps=mp3_bitrate_kbps,
                            transcoder=mp3_transcoder,
                        )
                    except RuntimeError as exc:
                        state.skipped_reason = f"mp3 transcode failed: {exc}"
                    else:
                        state.copied = True
                elif decodable:
                    state.skipped_reason = "mp3 transcoder not found; install ffmpeg"
                else:
                    state.skipped_reason = (
                        "encrypted media cannot be decoded to playable audio"
                        + (f": {decode_error}" if decode_error else "")
                    )
            elif not mp3_transcoder:
                state.skipped_reason = "mp3 transcoder not found; install ffmpeg"
            elif dry_run:
                state.skipped_reason = "dry run"
            else:
                try:
                    transcode_to_mp3(
                        state.working_source,
                        state.destination,
                        bitrate_kbps=mp3_bitrate_kbps,
                        transcoder=mp3_transcoder,
                    )
                except RuntimeError as exc:
                    state.skipped_reason = f"mp3 transcode failed: {exc}"
                else:
                    state.copied = True
        elif output_format == "flac":
            if state.source_extension == "flac":
                if dry_run:
                    state.skipped_reason = "dry run"
                else:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(state.working_source, state.destination)
                    state.copied = True
            elif state.media_encrypted:
                decodable, decode_error = can_decode_audio(state.working_source, state.source_extension)
                if decodable and dry_run:
                    state.skipped_reason = "dry run"
                elif decodable and mp3_transcoder:
                    try:
                        transcode_to_flac(
                            state.working_source,
                            state.destination,
                            transcoder=mp3_transcoder,
                        )
                    except RuntimeError as exc:
                        state.skipped_reason = f"flac transcode failed: {exc}"
                    else:
                        state.copied = True
                elif decodable:
                    state.skipped_reason = "flac transcoder not found; install ffmpeg"
                else:
                    state.skipped_reason = (
                        "encrypted media cannot be decoded to playable audio"
                        + (f": {decode_error}" if decode_error else "")
                    )
            elif not mp3_transcoder:
                state.skipped_reason = "flac transcoder not found; install ffmpeg"
            elif dry_run:
                state.skipped_reason = "dry run"
            else:
                try:
                    transcode_to_flac(
                        state.working_source,
                        state.destination,
                        transcoder=mp3_transcoder,
                    )
                except RuntimeError as exc:
                    state.skipped_reason = f"flac transcode failed: {exc}"
                else:
                    state.copied = True
        else:
            if dry_run:
                state.skipped_reason = "dry run"
            else:
                output_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(state.source, state.destination)
                state.copied = True
    finally:
        if state.temp_decrypted:
            state.temp_decrypted.unlink(missing_ok=True)


def _finalize_output(
    state: ExportState,
    record: dict[str, Any],
    *,
    dry_run: bool,
    output_format: str,
    require_output_match: bool,
    title: str,
    artists: str,
    album: str,
) -> None:
    if state.copied and not dry_run and output_format != "original":
        state.output_probe = probe_audio_output(state.destination)
        state.output_matches_request, state.output_mismatch_reason = output_match_result(
            output_format=output_format,
            extension=state.destination.suffix.lstrip("."),
            probe=state.output_probe,
        )
        if require_output_match and state.output_matches_request is False:
            state.destination.unlink(missing_ok=True)
            state.copied = False
            state.skipped_reason = f"output mismatch: {state.output_mismatch_reason}"
        else:
            state.metadata_result = write_audio_metadata(
                state.destination,
                state.destination.suffix.lstrip("."),
                audio_metadata_from_record(
                    record,
                    title=title,
                    artists=artists,
                    album=album,
                ),
            )


def build_export_record(
    record: dict[str, Any],
    cache_dir: Path,
    output_dir: Path,
    *,
    dry_run: bool,
    overwrite: bool,
    output_format: str,
    mp3_bitrate_kbps: int,
    mp3_transcoder: str | None,
    device_node: Path | None,
    fixed_key_hex: str | None,
    decoded_spades: dict[str, str],
    verify_audio: bool,
    allow_size_mismatch: bool,
    reserved_outputs: set[Path],
    require_output_match: bool = False,
) -> ExportRecord:
    info = record.get("info") or {}
    track = find_track(record) or {}

    cache_uuid = str(record.get("chunkId") or "")
    source = cache_dir / f"{cache_uuid}.bin"
    source_size = source.stat().st_size if source.exists() else 0
    video_item = selected_video_item(record, source_size=source_size or None)
    video_meta = video_item.get("video_meta") or {}
    encrypt_info = video_item.get("encrypt_info") or {}
    title = str(track.get("name") or f"track-{info.get('trackId') or cache_uuid}")
    artists = compact_names(track.get("artists") or []) or "Unknown Artist"
    album_obj = track.get("album") if isinstance(track.get("album"), dict) else {}
    album = str(album_obj.get("name") or "")
    quality = str(info.get("quality") or video_meta.get("quality") or "")
    bitrate = info.get("bitrate") or video_meta.get("bitrate")
    duration_ms = track.get("duration")
    indexed_size = record_indexed_size(record, video_meta)
    indexed_codec_type = str(video_meta.get("codec_type") or "")
    indexed_extension = "mp4" if indexed_codec_type == "flac" else str(video_meta.get("vtype") or "")

    if not source.exists():
        return ExportRecord(
            source=source,
            output=None,
            track_id=str(info.get("trackId") or track.get("id") or ""),
            title=title,
            artists=artists,
            album=album,
            quality=quality,
            bitrate=bitrate if isinstance(bitrate, int) else None,
            duration_ms=duration_ms if isinstance(duration_ms, int) else None,
            cache_uuid=cache_uuid,
            resource_id=str(record.get("resourceId") or ""),
            extension=export_extension("", output_format) if output_format in {"mp3", "flac"} else "",
            source_extension="",
            indexed_extension=indexed_extension,
            indexed_codec_type=indexed_codec_type,
            output_format=output_format,
            output_container="",
            output_codec_type="",
            output_sample_rate=None,
            output_bits_per_sample=None,
            output_probe_error="",
            output_matches_request=None,
            output_mismatch_reason="",
            source_size=0,
            indexed_size=indexed_size,
            encrypted=is_encrypted(record),
            encryption_method=str(encrypt_info.get("encryption_method") or ""),
            index_key_id=str(encrypt_info.get("kid") or ""),
            mp4_scheme="",
            mp4_key_id="",
            mp4_has_sample_encryption=False,
            decrypted=False,
            copied=False,
            skipped_reason="cache file missing",
        )

    source_extension = sniff_extension(source, video_meta.get("vtype"), video_meta.get("codec_type"))
    media_encrypted = is_encrypted(record, source_size=source_size or None)
    mp4_encryption = {
        "scheme": "",
        "key_id": "",
        "has_sample_encryption": False,
    }
    if source_extension in {"m4a", "mp4"}:
        mp4_encryption = mp4_encryption_summary(source)
        media_encrypted = media_encrypted or has_encrypted_mp4_sample_entry(source)
    source_encrypted = media_encrypted

    extension = export_extension(source_extension, output_format)
    quality_suffix = f" [{quality}]" if quality else ""
    base_name = safe_filename(f"{artists} - {title}{quality_suffix}")
    destination = output_dir / f"{base_name}.{extension}"
    if overwrite:
        reserved_outputs.add(destination)
    else:
        destination = reserve_unique_path(destination, reserved_outputs)

    state = ExportState(
        source=source,
        destination=destination,
        working_source=source,
        source_extension=source_extension,
        media_encrypted=media_encrypted,
        source_encrypted=source_encrypted,
        decrypted=False,
        copied=False,
        skipped_reason="",
    )

    if (
        not allow_size_mismatch
        and isinstance(indexed_size, int)
        and source_size != indexed_size
    ):
        state.skipped_reason = f"cache size mismatch: source {source_size} != indexed {indexed_size}"

    _resolve_source_state(
        state,
        record,
        dry_run=dry_run,
        output_format=output_format,
        mp3_bitrate_kbps=mp3_bitrate_kbps,
        mp3_transcoder=mp3_transcoder,
        device_node=device_node,
        fixed_key_hex=fixed_key_hex,
        decoded_spades=decoded_spades,
        verify_audio=verify_audio,
    )

    _transcode_or_copy(
        state,
        dry_run=dry_run,
        output_format=output_format,
        mp3_bitrate_kbps=mp3_bitrate_kbps,
        mp3_transcoder=mp3_transcoder,
        verify_audio=verify_audio,
        output_dir=output_dir,
    )

    _finalize_output(
        state,
        record,
        dry_run=dry_run,
        output_format=output_format,
        require_output_match=require_output_match,
        title=title,
        artists=artists,
        album=album,
    )

    return ExportRecord(
        source=source,
        output=destination if state.copied or state.skipped_reason == "dry run" else None,
        track_id=str(info.get("trackId") or track.get("id") or ""),
        title=title,
        artists=artists,
        album=album,
        quality=quality,
        bitrate=bitrate if isinstance(bitrate, int) else None,
        duration_ms=duration_ms if isinstance(duration_ms, int) else None,
        cache_uuid=cache_uuid,
        resource_id=str(record.get("resourceId") or ""),
        extension=extension,
        source_extension=source_extension,
        indexed_extension=indexed_extension,
        indexed_codec_type=indexed_codec_type,
        output_format=output_format,
        output_container=state.output_probe.container,
        output_codec_type=state.output_probe.codec_type,
        output_sample_rate=state.output_probe.sample_rate,
        output_bits_per_sample=state.output_probe.bits_per_sample,
        output_probe_error=state.output_probe.error,
        output_matches_request=state.output_matches_request,
        output_mismatch_reason=state.output_mismatch_reason,
        source_size=source_size,
        indexed_size=indexed_size,
        encrypted=source_encrypted,
        encryption_method=str(encrypt_info.get("encryption_method") or ""),
        index_key_id=str(encrypt_info.get("kid") or ""),
        mp4_scheme=str(mp4_encryption.get("scheme") or ""),
        mp4_key_id=str(mp4_encryption.get("key_id") or ""),
        mp4_has_sample_encryption=bool(mp4_encryption.get("has_sample_encryption")),
        decrypted=state.decrypted,
        copied=state.copied,
        skipped_reason=state.skipped_reason,
        cover_embedded=state.metadata_result.cover_embedded,
        lyrics_embedded=state.metadata_result.lyrics_embedded,
        metadata_error=state.metadata_result.error,
    )


def write_manifests(output_dir: Path, rows: list[ExportRecord], dry_run: bool) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_json = output_dir / ("manifest.dry-run.json" if dry_run else "manifest.json")
    manifest_csv = output_dir / ("manifest.dry-run.csv" if dry_run else "manifest.csv")

    data = [row.as_dict() for row in rows]
    manifest_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = list(data[0].keys()) if data else [f.name for f in dataclass_fields(ExportRecord)]
    with manifest_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)


def load_selection_file(selection_file: Path) -> list[dict[str, str]]:
    try:
        payload = json.loads(selection_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid selection JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("selection file must contain an items array")

    items: list[dict[str, str]] = []
    for index, item in enumerate(payload["items"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"selection item {index} must be an object")
        cache_uuid = str(item.get("cache_uuid") or item.get("cacheUuid") or "").strip()
        output_format = str(item.get("format") or "playable").strip()
        if not cache_uuid:
            raise ValueError(f"selection item {index} is missing cache_uuid")
        if output_format not in ALLOWED_EXPORT_FORMATS:
            raise ValueError(
                f"selection item {index} has unsupported format: {output_format}"
            )
        items.append({"cache_uuid": cache_uuid, "format": output_format})
    return items


def selected_records(
    records: list[dict[str, Any]],
    selection_items: list[dict[str, str]] | None,
) -> tuple[list[tuple[dict[str, Any], str]], list[str]]:
    if selection_items is None:
        return [(record, "") for record in records], []

    by_uuid = {str(record.get("chunkId") or ""): record for record in records}
    selected: list[tuple[dict[str, Any], str]] = []
    missing: list[str] = []
    for item in selection_items:
        cache_uuid = item["cache_uuid"]
        record = by_uuid.get(cache_uuid)
        if not record:
            missing.append(cache_uuid)
            continue
        selected.append((record, item["format"]))
    return selected, missing


def prepare_decoded_spades(
    records: list[dict[str, Any]],
    cache_dir: Path,
    *,
    output_formats: set[str],
    raw_key: str,
    device_node: Path | None,
    progress: bool,
) -> dict[str, str]:
    if output_formats <= {"original"} or raw_key or not device_node or not shutil.which("node"):
        return {}

    spades: list[str] = []
    for record in records:
        cache_uuid = str(record.get("chunkId") or "")
        source = cache_dir / f"{cache_uuid}.bin"
        source_size = source.stat().st_size if source.exists() else None
        spade = record_spade(record, source_size=source_size)
        if spade:
            spades.append(spade)
    try:
        return decode_spades(spades, device_node)
    except RuntimeError as exc:
        if progress:
            print(
                f"Warning: batch spade decode failed; falling back to per-record decode: {compact_error(str(exc))}",
                file=sys.stderr,
                flush=True,
            )
        return {}


def export_records(
    selected: list[tuple[dict[str, Any], str]],
    cache_dir: Path,
    output_dir: Path,
    *,
    dry_run: bool,
    overwrite: bool,
    default_format: str,
    mp3_bitrate_kbps: int,
    mp3_transcoder: str | None,
    device_node: Path | None,
    fixed_key_hex: str | None,
    decoded_spades: dict[str, str],
    verify_audio: bool,
    allow_size_mismatch: bool,
    require_output_match: bool,
    progress: bool,
) -> list[ExportRecord]:
    reserved_outputs: set[Path] = set()
    rows: list[ExportRecord] = []
    for index, (record, selected_format) in enumerate(selected, start=1):
        output_format = selected_format or default_format
        row = build_export_record(
            record,
            cache_dir,
            output_dir,
            dry_run=dry_run,
            overwrite=overwrite,
            output_format=output_format,
            mp3_bitrate_kbps=mp3_bitrate_kbps,
            mp3_transcoder=mp3_transcoder if output_format in {"mp3", "flac"} else None,
            device_node=device_node,
            fixed_key_hex=fixed_key_hex,
            decoded_spades=decoded_spades,
            verify_audio=verify_audio,
            allow_size_mismatch=allow_size_mismatch,
            reserved_outputs=reserved_outputs,
            require_output_match=require_output_match,
        )
        rows.append(row)
        if progress:
            if row.copied:
                state = "exported"
            elif row.skipped_reason == "dry run":
                state = "dry-run"
            elif row.skipped_reason:
                state = "skipped"
            else:
                state = "processed"
            name = compact_error(f"{row.artists} - {row.title}", limit=120)
            print(f"Progress: {index}/{len(selected)} {state}: {name}", flush=True)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export playable SodaMusic LunaCacheV2 cache audio with recovered names."
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.home() / "Music/SodaMusic Export",
    )
    parser.add_argument("--dry-run", action="store_true", help="Only write manifests.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite matching output names.")
    parser.add_argument("--limit", type=int, default=0, help="Export at most N records.")
    parser.add_argument(
        "--selection-file",
        type=Path,
        help="JSON file with selected cache_uuid values and per-item export formats.",
    )
    parser.add_argument(
        "--format",
        choices=("playable", "mp3", "flac", "original"),
        default="playable",
        help=(
            "playable writes audio-friendly files; mp3/flac transcode "
            "decodable audio; original copies cache media even when it may not play."
        ),
    )
    parser.add_argument(
        "--mp3-bitrate",
        type=int,
        default=DEFAULT_MP3_BITRATE_KBPS,
        help="MP3 bitrate in kbps when --format mp3 is used.",
    )
    parser.add_argument(
        "--device-node",
        type=Path,
        default=default_device_node_path(),
        help="Path to SodaMusic's device.node used to decode cached spade keys.",
    )
    parser.add_argument(
        "--raw-key",
        default="",
        help=(
            "Advanced: fixed 16-byte AES key as 32 hex characters. "
            "Normally leave this empty and use --device-node."
        ),
    )
    parser.add_argument(
        "--verify-audio",
        action="store_true",
        help="Run a decoder check for each exported playable file. Slower but stricter.",
    )
    parser.add_argument(
        "--require-output-match",
        action="store_true",
        help="Skip exported files whose probed container/codec does not match the requested format.",
    )
    parser.add_argument(
        "--allow-size-mismatch",
        action="store_true",
        help="Allow export when cache file size differs from the indexed media size.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Print one progress line per parsed cache record.",
    )
    args = parser.parse_args()
    if args.mp3_bitrate <= 0:
        parser.error("--mp3-bitrate must be greater than 0")
    raw_key = args.raw_key.strip().lower()
    if raw_key and not re.fullmatch(r"[0-9a-f]{32}", raw_key):
        parser.error("--raw-key must be 32 hexadecimal characters")

    cache_dir = args.cache_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    entries_db = cache_dir / "entries.db"
    device_node = resolve_device_node_path(args.device_node).resolve()
    if not device_node.exists():
        device_node = None

    if not entries_db.exists():
        raise SystemExit(f"entries.db not found: {entries_db}")

    records = parse_entries(entries_db)
    if args.limit > 0:
        records = records[: args.limit]

    selection_items = None
    if args.selection_file:
        try:
            selection_items = load_selection_file(args.selection_file.expanduser().resolve())
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    selected, missing_cache_uuids = selected_records(records, selection_items)
    if missing_cache_uuids:
        missing = ", ".join(missing_cache_uuids[:10])
        suffix = "" if len(missing_cache_uuids) <= 10 else f" (+{len(missing_cache_uuids) - 10} more)"
        raise SystemExit(f"selection file references unknown cache_uuid values: {missing}{suffix}")

    output_formats = {selected_format or args.format for _, selected_format in selected}
    transcoding_formats = {"mp3", "flac"}
    mp3_transcoder = find_mp3_transcoder() if output_formats & transcoding_formats else None
    if args.progress:
        print(f"Preparing records: {len(selected)}", flush=True)

    decoded_spades = prepare_decoded_spades(
        [record for record, _ in selected],
        cache_dir,
        output_formats=output_formats,
        raw_key=raw_key,
        device_node=device_node,
        progress=args.progress,
    )

    rows = export_records(
        selected,
        cache_dir,
        output_dir,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        default_format=args.format,
        mp3_bitrate_kbps=args.mp3_bitrate,
        mp3_transcoder=mp3_transcoder,
        device_node=device_node,
        fixed_key_hex=raw_key or None,
        decoded_spades=decoded_spades,
        verify_audio=args.verify_audio,
        allow_size_mismatch=args.allow_size_mismatch,
        require_output_match=args.require_output_match,
        progress=args.progress,
    )
    write_manifests(output_dir, rows, args.dry_run)

    copied = sum(1 for row in rows if row.copied)
    decrypted = sum(1 for row in rows if row.decrypted)
    missing = sum(1 for row in rows if row.skipped_reason == "cache file missing")
    encrypted = sum(1 for row in rows if row.encrypted)
    skipped = sum(1 for row in rows if row.skipped_reason and row.skipped_reason != "dry run")
    print(f"Parsed records: {len(records)}")
    print(f"Exported files: {copied}")
    print(f"Offline decrypted files: {decrypted}")
    print(f"Missing cache files: {missing}")
    print(f"Encrypted media files: {encrypted}")
    print(f"Skipped files: {skipped}")
    if output_formats & transcoding_formats:
        print(f"Audio transcoder: {mp3_transcoder or 'not found'}")
    if output_formats - {"original"} and raw_key:
        print("CENC key source: raw key")
    if output_formats - {"original"}:
        print(f"SodaMusic device.node: {device_node or 'not found'}")
    print(f"Output directory: {output_dir}")
    if args.dry_run:
        print("Dry run: no audio files were copied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
