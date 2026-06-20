"""Shared test helpers for the SodaMusic cache export test suite."""

from __future__ import annotations

import struct
from pathlib import Path


def pack(value: object) -> bytes:
    """Minimal MessagePack encoder for test fixtures."""
    if value is None:
        return b"\xc0"
    if isinstance(value, bool):
        return b"\xc3" if value else b"\xc2"
    if isinstance(value, int):
        if 0 <= value <= 127:
            return bytes([value])
        if -32 <= value < 0:
            return bytes([256 + value])
        if 0 <= value <= 255:
            return b"\xcc" + bytes([value])
        if -128 <= value < 0:
            return b"\xd0" + struct.pack("b", value)
        if 0 <= value <= 65535:
            return b"\xcd" + struct.pack(">H", value)
        if -32768 <= value < 0:
            return b"\xd1" + struct.pack(">h", value)
        if 0 <= value <= 4294967295:
            return b"\xce" + struct.pack(">I", value)
        if -2147483648 <= value < 0:
            return b"\xd2" + struct.pack(">i", value)
        if 0 <= value <= 18446744073709551615:
            return b"\xcf" + struct.pack(">Q", value)
        return b"\xd3" + struct.pack(">q", value)
    if isinstance(value, float):
        return b"\xcb" + struct.pack(">d", value)
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        length = len(encoded)
        if length <= 31:
            return bytes([0xA0 | length]) + encoded
        if length <= 255:
            return b"\xd9" + bytes([length]) + encoded
        if length <= 65535:
            return b"\xda" + struct.pack(">H", length) + encoded
        return b"\xdb" + struct.pack(">I", length) + encoded
    if isinstance(value, bytes):
        length = len(value)
        if length <= 255:
            return b"\xc4" + bytes([length]) + value
        if length <= 65535:
            return b"\xc5" + struct.pack(">H", length) + value
        return b"\xc6" + struct.pack(">I", length) + value
    if isinstance(value, list):
        length = len(value)
        if length <= 15:
            header = bytes([0x90 | length])
        elif length <= 65535:
            header = b"\xdc" + struct.pack(">H", length)
        else:
            header = b"\xdd" + struct.pack(">I", length)
        return header + b"".join(pack(item) for item in value)
    if isinstance(value, dict):
        length = len(value)
        if length <= 15:
            header = bytes([0x80 | length])
        elif length <= 65535:
            header = b"\xde" + struct.pack(">H", length)
        else:
            header = b"\xdf" + struct.pack(">I", length)
        payload = b""
        for key, val in value.items():
            payload += pack(key) + pack(val)
        return header + payload
    raise TypeError(f"unsupported type: {type(value)}")


def encrypted_record(cache_uuid: str = "cache-1") -> dict:
    return {
        "chunkId": cache_uuid,
        "resourceId": "resource_F_highest",
        "size": 40,
        "info": {
            "trackId": "track-1",
            "quality": "highest",
            "spade": "test-spade",
            "mediaDetail": {
                "lyrics": {
                    "content": "[1000,500]<0,500,0>Hello\n[2500,500]<0,500,0>World",
                    "type": "krc",
                },
                "playable": {
                    "cover_url": {
                        "uri": "cover/test",
                        "urls": ["https://img.example/"],
                    }
                },
                "video_model": {
                    "video_list": [
                        {
                            "video_meta": {
                                "vtype": "m4a",
                                "size": 40,
                                "quality": "highest",
                            },
                            "encrypt_info": {
                                "encrypt": True,
                                "encryption_method": "cenc",
                                "kid": "test-kid",
                            },
                        }
                    ]
                },
            },
        },
        "track": {
            "type": "track",
            "track": {
                "id": "track-1",
                "name": "Song",
                "artists": [{"name": "Artist"}],
                "album": {"name": "Album"},
                "duration": 1234,
            },
        },
    }


def source_record(
    cache_uuid: str,
    *,
    track_id: str = "track-1",
    quality: str = "highest",
    size: int = 40,
    bitrate: int = 260000,
    vtype: str = "m4a",
    codec_type: str = "aac",
) -> dict:
    record = encrypted_record(cache_uuid)
    record["resourceId"] = f"resource_F_{quality}"
    record["size"] = size
    record["info"]["trackId"] = track_id
    record["info"]["quality"] = quality
    record["info"]["mediaDetail"]["video_model"]["video_list"] = [
        {
            "video_meta": {
                "vtype": "m4a",
                "codec_type": "aac",
                "size": 20,
                "quality": "higher",
                "bitrate": 132000,
            },
            "encrypt_info": {"encrypt": True},
        },
        {
            "video_meta": {
                "vtype": vtype,
                "codec_type": codec_type,
                "size": size,
                "quality": quality,
                "bitrate": bitrate,
            },
            "encrypt_info": {"encrypt": True},
        },
        {
            "video_meta": {
                "vtype": "mp4",
                "codec_type": "flac",
                "size": 400,
                "quality": "lossless",
                "bitrate": 1500000,
            },
            "encrypt_info": {"encrypt": True},
        },
    ]
    record["track"]["track"]["id"] = track_id
    return record


def write_fake_m4a(path: Path) -> None:
    path.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 28)


def write_fake_mp3(path: Path) -> None:
    path.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00audio".ljust(40, b"\0"))


def write_fake_flac(path: Path) -> None:
    path.write_bytes(b"fLaC" + b"\x00" * 36)
