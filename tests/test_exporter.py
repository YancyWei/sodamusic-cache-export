import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import export_sodamusic_cache as exporter  # noqa: E402
import analyze_sodamusic_cache as analyzer  # noqa: E402
import batch_target_sodamusic_cache as batch_target  # noqa: E402
import runtime_dependencies as deps  # noqa: E402
import sodamusic_export_web as web  # noqa: E402
import start_sodamusic_export as launcher  # noqa: E402
import target_sodamusic_cache as target_cli  # noqa: E402
import watch_sodamusic_cache as watcher  # noqa: E402


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
                }
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


class ExporterTests(unittest.TestCase):
    def test_extract_metadata_recovers_cover_urls_and_converts_krc_lyrics(self) -> None:
        metadata = exporter.audio_metadata_from_record(
            encrypted_record(),
            title="Song",
            artists="Artist",
            album="Album",
        )

        self.assertEqual("Song", metadata.title)
        self.assertEqual("Artist", metadata.artists)
        self.assertEqual("Album", metadata.album)
        self.assertEqual("[00:01.00]Hello\n[00:02.50]World", metadata.lyrics)
        self.assertIn("https://img.example/cover/test~300x300.image", metadata.cover_urls)

    def test_exported_playable_file_gets_metadata_when_copy_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            source = cache_dir / "cache-1.bin"
            source.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00audio".ljust(40, b"\0"))

            with (
                patch.object(exporter, "can_decode_audio", return_value=(True, "")),
                patch.object(
                    exporter,
                    "write_audio_metadata",
                    return_value=exporter.MetadataWriteResult(
                        cover_embedded=True,
                        lyrics_embedded=True,
                    ),
                ) as write_metadata,
                patch.object(
                    exporter,
                    "probe_audio_output",
                    return_value=exporter.AudioProbeResult(
                        container="mp3",
                        codec_type="mp3",
                        sample_rate=44100,
                        bits_per_sample=16,
                    ),
                ) as probe,
            ):
                row = exporter.build_export_record(
                    encrypted_record(),
                    cache_dir,
                    output_dir,
                    dry_run=False,
                    overwrite=True,
                    output_format="playable",
                    mp3_bitrate_kbps=exporter.DEFAULT_MP3_BITRATE_KBPS,
                    mp3_transcoder=None,
                    device_node=None,
                    fixed_key_hex=None,
                    decoded_spades={},
                    verify_audio=False,
                    allow_size_mismatch=False,
                    reserved_outputs=set(),
                )

        self.assertTrue(row.copied)
        self.assertTrue(row.cover_embedded)
        self.assertTrue(row.lyrics_embedded)
        self.assertEqual("", row.metadata_error)
        self.assertEqual("mp3", row.output_container)
        self.assertEqual("mp3", row.output_codec_type)
        self.assertEqual(44100, row.output_sample_rate)
        self.assertEqual(16, row.output_bits_per_sample)
        self.assertTrue(row.output_matches_request)
        self.assertEqual("", row.output_mismatch_reason)
        self.assertEqual("mp3", row.as_dict()["output_codec_type"])
        self.assertTrue(row.as_dict()["output_matches_request"])
        probe.assert_called_once()
        write_metadata.assert_called_once()
        self.assertEqual("mp3", write_metadata.call_args.args[1])

    def test_output_match_result_reports_requested_codec_mismatch(self) -> None:
        matches, reason = exporter.output_match_result(
            output_format="flac",
            extension="flac",
            probe=exporter.AudioProbeResult(container="mp4", codec_type="aac"),
        )

        self.assertFalse(matches)
        self.assertIn("expected flac/flac", reason)

    def test_export_strict_output_match_skips_and_removes_mismatched_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            source = cache_dir / "cache-1.bin"
            source.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00audio".ljust(40, b"\0"))

            def fake_transcode(_source: Path, destination: Path, *, transcoder: str) -> None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"not really flac")

            with (
                patch.object(exporter, "can_decode_audio", return_value=(True, "")),
                patch.object(exporter, "transcode_to_flac", side_effect=fake_transcode),
                patch.object(
                    exporter,
                    "probe_audio_output",
                    return_value=exporter.AudioProbeResult(container="mp4", codec_type="aac"),
                ),
                patch.object(exporter, "write_audio_metadata") as write_metadata,
            ):
                row = exporter.build_export_record(
                    encrypted_record(),
                    cache_dir,
                    output_dir,
                    dry_run=False,
                    overwrite=True,
                    output_format="flac",
                    mp3_bitrate_kbps=exporter.DEFAULT_MP3_BITRATE_KBPS,
                    mp3_transcoder="ffmpeg",
                    device_node=None,
                    fixed_key_hex=None,
                    decoded_spades={},
                    verify_audio=False,
                    allow_size_mismatch=False,
                    reserved_outputs=set(),
                    require_output_match=True,
                )

        self.assertFalse(row.copied)
        self.assertIsNone(row.output)
        self.assertEqual("mp4", row.output_container)
        self.assertEqual("aac", row.output_codec_type)
        self.assertFalse(row.output_matches_request)
        self.assertIn("output mismatch", row.skipped_reason)
        self.assertFalse((output_dir / "Artist - Song [highest].flac").exists())
        write_metadata.assert_not_called()

    def test_export_still_succeeds_when_metadata_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            source = cache_dir / "cache-1.bin"
            source.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00audio".ljust(40, b"\0"))

            with (
                patch.object(exporter, "can_decode_audio", return_value=(True, "")),
                patch.object(
                    exporter,
                    "write_audio_metadata",
                    return_value=exporter.MetadataWriteResult(error="metadata write failed"),
                ),
            ):
                row = exporter.build_export_record(
                    encrypted_record(),
                    cache_dir,
                    output_dir,
                    dry_run=False,
                    overwrite=True,
                    output_format="playable",
                    mp3_bitrate_kbps=exporter.DEFAULT_MP3_BITRATE_KBPS,
                    mp3_transcoder=None,
                    device_node=None,
                    fixed_key_hex=None,
                    decoded_spades={},
                    verify_audio=False,
                    allow_size_mismatch=False,
                    reserved_outputs=set(),
                )

        self.assertTrue(row.copied)
        self.assertEqual("metadata write failed", row.metadata_error)

    def test_export_skips_cache_file_when_size_differs_from_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            source = cache_dir / "cache-short.bin"
            source.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00audio")
            record = source_record("cache-short", quality="highest", size=40)

            with patch.object(exporter, "can_decode_audio", return_value=(True, "")):
                row = exporter.build_export_record(
                    record,
                    cache_dir,
                    output_dir,
                    dry_run=False,
                    overwrite=True,
                    output_format="playable",
                    mp3_bitrate_kbps=exporter.DEFAULT_MP3_BITRATE_KBPS,
                    mp3_transcoder=None,
                    device_node=None,
                    fixed_key_hex=None,
                    decoded_spades={},
                    verify_audio=False,
                    allow_size_mismatch=False,
                    reserved_outputs=set(),
                )

        self.assertFalse(row.copied)
        self.assertIn("cache size mismatch", row.skipped_reason)

    def test_export_allows_cache_size_mismatch_for_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            source = cache_dir / "cache-short.bin"
            source.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00audio")
            record = source_record("cache-short", quality="highest", size=40)
            record["info"]["mediaDetail"]["video_model"]["video_list"][1]["encrypt_info"] = {
                "encrypt": False
            }

            with (
                patch.object(exporter, "can_decode_audio", return_value=(True, "")),
                patch.object(exporter, "write_audio_metadata"),
            ):
                row = exporter.build_export_record(
                    record,
                    cache_dir,
                    output_dir,
                    dry_run=False,
                    overwrite=True,
                    output_format="playable",
                    mp3_bitrate_kbps=exporter.DEFAULT_MP3_BITRATE_KBPS,
                    mp3_transcoder=None,
                    device_node=None,
                    fixed_key_hex=None,
                    decoded_spades={},
                    verify_audio=False,
                    allow_size_mismatch=True,
                    reserved_outputs=set(),
                )

        self.assertTrue(row.copied)

    def test_export_size_guard_prefers_selected_video_meta_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            source = cache_dir / "cache-video-size.bin"
            source.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00audio".ljust(40, b"\0"))
            record = source_record("cache-video-size", quality="highest", size=40)
            record["size"] = 80
            record["info"]["mediaDetail"]["video_model"]["video_list"][1]["encrypt_info"] = {
                "encrypt": False
            }

            with (
                patch.object(exporter, "can_decode_audio", return_value=(True, "")),
                patch.object(exporter, "write_audio_metadata"),
            ):
                row = exporter.build_export_record(
                    record,
                    cache_dir,
                    output_dir,
                    dry_run=False,
                    overwrite=True,
                    output_format="playable",
                    mp3_bitrate_kbps=exporter.DEFAULT_MP3_BITRATE_KBPS,
                    mp3_transcoder=None,
                    device_node=None,
                    fixed_key_hex=None,
                    decoded_spades={},
                    verify_audio=False,
                    allow_size_mismatch=False,
                    reserved_outputs=set(),
                )

        self.assertTrue(row.copied)
        self.assertEqual(40, row.indexed_size)

    def test_playable_mp4_container_audio_exports_with_music_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            source = cache_dir / "cache-flac.bin"
            write_fake_m4a(source)
            record = source_record(
                "cache-flac",
                quality="lossless",
                bitrate=1500000,
                vtype="mp4",
                codec_type="flac",
            )
            record["info"]["mediaDetail"]["video_model"]["video_list"][1]["encrypt_info"] = {
                "encrypt": False
            }

            with (
                patch.object(exporter, "can_decode_audio", return_value=(True, "")),
                patch.object(exporter, "write_audio_metadata") as write_metadata,
            ):
                row = exporter.build_export_record(
                    record,
                    cache_dir,
                    output_dir,
                    dry_run=False,
                    overwrite=True,
                    output_format="playable",
                    mp3_bitrate_kbps=exporter.DEFAULT_MP3_BITRATE_KBPS,
                    mp3_transcoder=None,
                    device_node=None,
                    fixed_key_hex=None,
                    decoded_spades={},
                    verify_audio=False,
                    allow_size_mismatch=False,
                    reserved_outputs=set(),
                )

        self.assertTrue(row.copied)
        self.assertEqual("m4a", row.extension)
        self.assertEqual(".m4a", row.output.suffix)
        write_metadata.assert_called_once()
        self.assertEqual("m4a", write_metadata.call_args.args[1])

    def test_flac_format_transcodes_mp4_container_to_native_flac(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            source = cache_dir / "cache-flac.bin"
            write_fake_m4a(source)
            record = source_record(
                "cache-flac",
                quality="lossless",
                bitrate=1500000,
                vtype="mp4",
                codec_type="flac",
            )
            record["info"]["mediaDetail"]["video_model"]["video_list"][1]["encrypt_info"] = {
                "encrypt": False
            }

            def fake_transcode(_source: Path, destination: Path, *, transcoder: str) -> None:
                self.assertEqual("/usr/local/bin/ffmpeg", transcoder)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"fLaC")

            with (
                patch.object(exporter, "transcode_to_flac", side_effect=fake_transcode) as transcode,
                patch.object(exporter, "write_audio_metadata") as write_metadata,
            ):
                row = exporter.build_export_record(
                    record,
                    cache_dir,
                    output_dir,
                    dry_run=False,
                    overwrite=True,
                    output_format="flac",
                    mp3_bitrate_kbps=exporter.DEFAULT_MP3_BITRATE_KBPS,
                    mp3_transcoder="/usr/local/bin/ffmpeg",
                    device_node=None,
                    fixed_key_hex=None,
                    decoded_spades={},
                    verify_audio=False,
                    allow_size_mismatch=False,
                    reserved_outputs=set(),
                )

        self.assertTrue(row.copied)
        self.assertEqual("flac", row.extension)
        self.assertEqual(".flac", row.output.suffix)
        self.assertEqual("mp4", row.source_extension)
        self.assertEqual("mp4", row.indexed_extension)
        self.assertEqual("flac", row.indexed_codec_type)
        self.assertEqual("flac", row.output_format)
        self.assertEqual("flac", row.as_dict()["output_format"])
        transcode.assert_called_once()
        write_metadata.assert_called_once()
        self.assertEqual("flac", write_metadata.call_args.args[1])

    def test_original_mp4_container_audio_keeps_cache_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            source = cache_dir / "cache-flac.bin"
            write_fake_m4a(source)
            record = source_record(
                "cache-flac",
                quality="lossless",
                bitrate=1500000,
                vtype="mp4",
                codec_type="flac",
            )
            record["info"]["mediaDetail"]["video_model"]["video_list"][1]["encrypt_info"] = {
                "encrypt": False
            }

            row = exporter.build_export_record(
                record,
                cache_dir,
                output_dir,
                dry_run=False,
                overwrite=True,
                output_format="original",
                mp3_bitrate_kbps=exporter.DEFAULT_MP3_BITRATE_KBPS,
                mp3_transcoder=None,
                device_node=None,
                fixed_key_hex=None,
                decoded_spades={},
                verify_audio=False,
                allow_size_mismatch=False,
                reserved_outputs=set(),
            )

        self.assertTrue(row.copied)
        self.assertEqual("mp4", row.extension)
        self.assertEqual(".mp4", row.output.suffix)

    def test_decrypted_playable_output_must_decode_before_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            source = cache_dir / "cache-1.bin"
            write_fake_m4a(source)
            written_outputs: list[Path] = []

            def fake_decrypt(_source: Path, destination: Path, _key_hex: str) -> None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"not playable audio")
                written_outputs.append(destination)

            with (
                patch.object(exporter, "decrypt_cenc_mp4", side_effect=fake_decrypt),
                patch.object(exporter, "can_decode_audio", return_value=(False, "decoder rejected")),
            ):
                row = exporter.build_export_record(
                    encrypted_record(),
                    cache_dir,
                    output_dir,
                    dry_run=False,
                    overwrite=True,
                    output_format="playable",
                    mp3_bitrate_kbps=exporter.DEFAULT_MP3_BITRATE_KBPS,
                    mp3_transcoder=None,
                    device_node=None,
                    fixed_key_hex="00" * 16,
                    decoded_spades={},
                    verify_audio=False,
                    allow_size_mismatch=False,
                    reserved_outputs=set(),
                )

            self.assertFalse(row.copied)
            self.assertTrue(row.decrypted)
            self.assertIsNone(row.output)
            self.assertIn("audio decode failed", row.skipped_reason)
            self.assertEqual(1, len(written_outputs))
            self.assertFalse(written_outputs[0].exists())

    def test_encrypted_sample_entry_original_formats_reads_frma(self) -> None:
        def box(name: bytes, payload: bytes) -> bytes:
            return (len(payload) + 8).to_bytes(4, "big") + name + payload

        # Minimal nesting: enca -> sinf -> frma. The encrypted entry's child
        # boxes start after the standard 28-byte audio sample entry header.
        data = box(b"enca", b"\0" * 28 + box(b"sinf", box(b"frma", b"fLaC")))

        self.assertEqual([(0, b"fLaC")], exporter.encrypted_sample_entry_original_formats(data))

    def test_web_validation_allows_missing_device_node_in_device_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            errors = web.validate_payload(
                {
                    "cacheDir": str(cache_dir),
                    "outputDir": str(Path(tmp) / "out"),
                    "deviceNode": str(Path(tmp) / "missing-device.node"),
                    "keyMode": "device",
                    "format": "playable",
                    "mp3Bitrate": "192",
                }
            )

        self.assertEqual([], errors)

    def test_device_node_resolves_from_app_bundle_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "汽水音乐.app"
            device_node = app / "Contents/Resources/app.asar.unpacked/device.node"
            device_node.parent.mkdir(parents=True)
            device_node.write_bytes(b"")

            self.assertEqual(device_node, exporter.resolve_device_node_path(app))

    def test_web_preflight_returns_sources_and_detected_device_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            write_fake_m4a(cache_dir / "cache-highest.bin")
            records = [source_record("cache-highest", quality="highest", size=40, bitrate=260000)]
            device_node = Path(tmp) / "device.node"
            device_node.write_bytes(b"")
            original_cache = web.preflight_cache
            web.preflight_cache = None
            with (
                patch.object(web, "DEFAULT_CACHE_DIR", cache_dir),
                patch.object(web, "DEFAULT_OUTPUT_DIR", Path(tmp) / "out"),
                patch.object(web, "resolve_device_node_path", return_value=device_node),
                patch.object(web, "parse_entries", return_value=records),
                patch.object(web, "shutil_which", return_value="/usr/bin/node"),
            ):
                payload = web.build_preflight_payload(force=True)
            web.preflight_cache = original_cache

        self.assertEqual(1, payload["sources"]["exportable"])
        self.assertIn("highest/aac", payload["sources"]["cachedQualities"])
        self.assertIn("lossless/flac", payload["sources"]["indexedQualities"])
        self.assertEqual(device_node.resolve(), Path(payload["deviceNode"]).resolve())
        self.assertTrue(payload["ready"])

    def test_web_preflight_status_omits_large_source_rows(self) -> None:
        payload = {
            "apiVersion": web.WEB_API_VERSION,
            "cacheDir": "/cache",
            "sources": {
                "rows": [{"cacheUuid": "cache-a"}],
                "total": 1,
                "exportable": 1,
                "uncachedHigher": 0,
                "indexedQualities": ["lossless/flac"],
                "cachedQualities": ["highest/aac"],
                "error": "",
            },
        }

        status = web.preflight_status_payload(payload)

        self.assertEqual(1, status["sources"]["exportable"])
        self.assertEqual(["lossless/flac"], status["sources"]["indexedQualities"])
        self.assertEqual(["highest/aac"], status["sources"]["cachedQualities"])
        self.assertNotIn("rows", status["sources"])

    def test_web_validation_still_requires_valid_raw_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            errors = web.validate_payload(
                {
                    "cacheDir": str(cache_dir),
                    "outputDir": str(Path(tmp) / "out"),
                    "keyMode": "raw",
                    "rawKey": "",
                    "format": "playable",
                    "mp3Bitrate": "192",
                }
            )

        self.assertTrue(any("固定 key" in error for error in errors))

    def test_web_validation_requires_ffmpeg_for_mp3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            with patch.object(web, "find_mp3_transcoder", return_value=None):
                errors = web.validate_payload(
                    {
                        "cacheDir": str(cache_dir),
                        "outputDir": str(Path(tmp) / "out"),
                        "keyMode": "device",
                        "format": "mp3",
                        "mp3Bitrate": "192",
                    }
                )

        self.assertTrue(any("ffmpeg" in error for error in errors))

    def test_web_validation_requires_ffmpeg_for_flac(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            with patch.object(web, "find_mp3_transcoder", return_value=None):
                errors = web.validate_payload(
                    {
                        "cacheDir": str(cache_dir),
                        "outputDir": str(Path(tmp) / "out"),
                        "keyMode": "device",
                        "format": "flac",
                        "mp3Bitrate": "192",
                    }
                )

        self.assertTrue(any("ffmpeg" in error for error in errors))

    def test_source_rows_select_highest_cached_track_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            write_fake_m4a(cache_dir / "cache-highest.bin")
            write_fake_m4a(cache_dir / "cache-hires.bin")
            records = [
                source_record("cache-highest", quality="highest", size=40, bitrate=260000),
                source_record("cache-hires", quality="hi_res", size=80, bitrate=320000),
            ]

            rows = exporter.source_rows(records, cache_dir)

        self.assertEqual(1, len(rows))
        self.assertEqual("cache-hires", rows[0]["cacheUuid"])
        self.assertEqual("hi_res", rows[0]["quality"])
        self.assertEqual(2, len(rows[0]["cachedCandidates"]))

    def test_source_rows_reports_uncached_higher_candidate_without_selecting_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            write_fake_m4a(cache_dir / "cache-highest.bin")
            records = [source_record("cache-highest", quality="highest", size=40, bitrate=260000)]

            rows = exporter.source_rows(records, cache_dir)

        self.assertEqual("cache-highest", rows[0]["cacheUuid"])
        self.assertEqual("lossless", rows[0]["uncachedBest"]["quality"])
        self.assertEqual("flac", rows[0]["uncachedBest"]["codecType"])

    def test_cache_analyzer_reports_indexed_and_cached_quality_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            write_fake_m4a(cache_dir / "cache-highest.bin")
            report = analyzer.analyze_records(
                [source_record("cache-highest", quality="highest", size=40, bitrate=260000)],
                cache_dir,
            )

        self.assertEqual(1, report["tracks"])
        self.assertEqual(1, report["cachedTracks"])
        self.assertEqual(1, report["tracksWithUncachedBest"])
        self.assertIn("lossless/flac", report["indexedByQuality"])
        self.assertEqual({"highest/aac": 1}, report["cachedByQuality"])
        item = report["items"][0]
        self.assertEqual("lossless", item["bestIndexed"]["quality"])
        self.assertFalse(item["bestIndexedCached"])
        self.assertEqual("highest", item["bestCached"]["quality"])
        self.assertEqual(40, item["cachedFiles"][0]["indexedSize"])

    def test_cache_analyzer_selection_uses_only_matching_cached_quality(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            write_fake_m4a(cache_dir / "cache-aac.bin")
            write_fake_m4a(cache_dir / "cache-flac.bin")
            records = [
                source_record("cache-aac", quality="highest", size=40, bitrate=260000),
                source_record(
                    "cache-flac",
                    track_id="target-track",
                    quality="lossless",
                    size=40,
                    bitrate=1500000,
                    vtype="mp4",
                    codec_type="flac",
                ),
            ]
            report = analyzer.analyze_records(records, cache_dir)
            items = analyzer.selection_items_for_cached_quality(
                report,
                quality="lossless",
                codec="flac",
            )

        self.assertEqual([{"cache_uuid": "cache-flac", "format": "playable"}], items)

    def test_cache_analyzer_filter_report_recomputes_matching_track_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            write_fake_m4a(cache_dir / "cache-a.bin")
            write_fake_m4a(cache_dir / "cache-b.bin")
            records = [
                source_record("cache-a", track_id="track-a", quality="highest", size=40),
                source_record("cache-b", track_id="track-b", quality="lossless", size=40, codec_type="flac"),
            ]
            records[1]["track"]["track"]["name"] = "Filtered Song"
            report = analyzer.filter_report(
                analyzer.analyze_records(records, cache_dir),
                analyzer.TrackFilter(title="filtered"),
            )

        self.assertEqual(1, report["tracks"])
        self.assertEqual(2, report["filteredFromTracks"])
        self.assertEqual("Filtered Song", report["items"][0]["title"])
        self.assertEqual({"lossless/flac": 1}, report["cachedByQuality"])

    def test_cache_analyzer_track_summary_deduplicates_labels_and_lists_cache_uuids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            write_fake_m4a(cache_dir / "cache-highest.bin")
            write_fake_m4a(cache_dir / "cache-lossless.bin")
            records = [
                source_record("cache-highest", quality="highest", size=40, bitrate=260000),
                source_record(
                    "cache-lossless",
                    quality="lossless",
                    size=40,
                    bitrate=1500000,
                    vtype="mp4",
                    codec_type="flac",
                ),
            ]
            report = analyzer.analyze_records(records, cache_dir)

        track = report["items"][0]
        self.assertEqual(["lossless/flac", "highest/aac", "higher/aac"], track["indexedLabels"])
        self.assertEqual(["lossless/flac", "highest/aac"], track["cachedLabels"])
        self.assertEqual("resource_F_lossless", track["cachedFiles"][0]["resourceId"])
        self.assertEqual("lossless", track["cachedFiles"][0]["indexedQuality"])
        self.assertEqual("flac", track["cachedFiles"][0]["indexedCodecType"])
        self.assertEqual("mp4", track["cachedFiles"][0]["indexedExtension"])
        self.assertEqual(
            [
                ("lossless/flac", "cache-lossless", "resource_F_lossless", 40),
                ("highest/aac", "cache-highest", "resource_F_highest", 40),
            ],
            [
                (
                    item["label"],
                    item["cacheUuid"],
                    item["resourceId"],
                    item["indexedSize"],
                )
                for item in track["cachedVersions"]
            ],
        )

    def test_cache_analyzer_selection_respects_filtered_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            write_fake_m4a(cache_dir / "cache-a.bin")
            write_fake_m4a(cache_dir / "cache-b.bin")
            records = [
                source_record("cache-a", track_id="track-a", quality="highest", size=40),
                source_record("cache-b", track_id="track-b", quality="highest", size=40),
            ]
            records[1]["track"]["track"]["name"] = "Only This"
            report = analyzer.filter_report(
                analyzer.analyze_records(records, cache_dir),
                analyzer.TrackFilter(title="only"),
            )
            items = analyzer.selection_items_for_cached_quality(
                report,
                quality="highest",
                codec="aac",
            )

        self.assertEqual([{"cache_uuid": "cache-b", "format": "playable"}], items)

    def test_cache_analyzer_writes_batch_target_items_for_indexed_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            write_fake_m4a(cache_dir / "cache-a.bin")
            records = [
                source_record("cache-a", track_id="track-a", quality="highest", size=40),
                source_record("cache-b", track_id="track-b", quality="highest", size=40),
            ]
            records[0]["track"]["track"]["name"] = "Song A"
            records[1]["info"]["mediaDetail"]["video_model"]["video_list"] = [
                {
                    "video_meta": {
                        "vtype": "m4a",
                        "codec_type": "aac",
                        "size": 40,
                        "quality": "highest",
                    },
                    "encrypt_info": {"encrypt": True},
                }
            ]
            report = analyzer.analyze_records(records, cache_dir)
            items = analyzer.batch_target_items(report, target="lossless/flac")

        self.assertEqual(
            [
                {
                    "trackId": "track-a",
                    "title": "Song A",
                    "artist": "Artist",
                    "album": "Album",
                    "target": "lossless/flac",
                }
            ],
            items,
        )

    def test_cache_analyzer_batch_target_items_default_to_best_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            write_fake_m4a(cache_dir / "cache-a.bin")
            report = analyzer.analyze_records(
                [source_record("cache-a", track_id="track-a", quality="highest", size=40)],
                cache_dir,
            )
            items = analyzer.batch_target_items(report)

        self.assertEqual("track-a", items[0]["trackId"])
        self.assertEqual("lossless/flac", items[0]["target"])

    def test_cache_analyzer_watcher_command_uses_single_track_and_target_quality(self) -> None:
        command = analyzer.watcher_command_for_track(
            {"trackId": "track 1"},
            quality="lossless",
            codec="flac",
            selection_out=Path("/tmp/selection file.json"),
            output_dir=Path("/tmp/out dir"),
        )

        self.assertIn("src/watch_sodamusic_cache.py", command)
        self.assertIn("--track-id 'track 1'", command)
        self.assertIn("--target lossless/flac", command)
        self.assertIn("--require-indexed", command)
        self.assertIn("--require-single-track", command)
        self.assertIn("--stable-seconds 1", command)
        self.assertIn("'/tmp/selection file.json'", command)
        self.assertIn("--export-when-found", command)
        self.assertIn("'/tmp/out dir'", command)

    def test_cache_analyzer_parse_target_label_accepts_quality_codec_extension(self) -> None:
        self.assertEqual(("lossless", "flac", ""), analyzer.parse_target_label("lossless/flac"))
        self.assertEqual(("highest", "aac", "m4a"), analyzer.parse_target_label("highest/aac/m4a"))

    def test_cache_watcher_query_matches_track_metadata_terms(self) -> None:
        track = {
            "trackId": "track-1",
            "title": "Zero Year Love Song",
            "artists": "GG Bo",
            "album": "Demo Album",
        }

        self.assertTrue(analyzer.track_matches_filter(track, analyzer.TrackFilter(query="gg love")))
        self.assertTrue(analyzer.track_matches_filter(track, analyzer.TrackFilter(query="track-1 demo")))
        self.assertFalse(analyzer.track_matches_filter(track, analyzer.TrackFilter(query="missing love")))

    def test_cache_watcher_parse_target_label_accepts_quality_codec_extension(self) -> None:
        self.assertEqual(("lossless", "flac", ""), watcher.parse_target_label("lossless/flac"))
        self.assertEqual(("highest", "aac", "m4a"), watcher.parse_target_label("highest/aac/m4a"))

    def test_cache_watcher_precise_filter_combines_track_fields(self) -> None:
        tracks = [
            {
                "trackId": "target-id",
                "title": "Same Title",
                "artists": "Target Artist",
                "album": "Target Album",
            },
            {
                "trackId": "other-id",
                "title": "Same Title",
                "artists": "Other Artist",
                "album": "Other Album",
            },
        ]

        filtered = watcher.filter_tracks(
            tracks,
            watcher.TrackFilter(title="same", artist="target", track_id="target-id"),
        )

        self.assertEqual([tracks[0]], filtered)

    def test_cache_watcher_precise_filter_can_match_album_without_query(self) -> None:
        tracks = [
            {"trackId": "one", "title": "Song A", "artists": "Artist", "album": "First Album"},
            {"trackId": "two", "title": "Song B", "artists": "Artist", "album": "Second Album"},
        ]

        filtered = watcher.filter_tracks(tracks, watcher.TrackFilter(album="second"))

        self.assertEqual([tracks[1]], filtered)

    def test_cache_watcher_multiple_tracks_status_lists_refinement_hint(self) -> None:
        status = watcher.multiple_tracks_status(
            [
                {"trackId": "one", "title": "Song A", "artists": "Artist"},
                {"trackId": "two", "title": "Song B", "artists": "Artist"},
            ]
        )

        self.assertIn("matched 2 tracks", status)
        self.assertIn("--track-id", status)
        self.assertIn("Artist - Song A", status)
        self.assertIn("Artist - Song B", status)

    def test_cache_watcher_detects_matching_indexed_candidate(self) -> None:
        tracks = [
            {
                "indexedCandidates": [
                    {"quality": "highest", "codecType": "aac", "extension": "m4a"},
                    {"quality": "lossless", "codecType": "flac", "extension": "mp4"},
                ]
            }
        ]

        self.assertTrue(
            watcher.has_matching_indexed_candidate(
                tracks,
                quality="lossless",
                codec="flac",
            )
        )
        self.assertFalse(
            watcher.has_matching_indexed_candidate(
                tracks,
                quality="hi_res",
                codec="flac",
            )
        )

    def test_cache_watcher_matching_indexed_labels_filters_requested_version(self) -> None:
        tracks = [
            {
                "indexedCandidates": [
                    {"quality": "highest", "codecType": "aac", "extension": "m4a"},
                    {"quality": "lossless", "codecType": "flac", "extension": "mp4"},
                    {"quality": "lossless", "codecType": "flac", "extension": "mp4"},
                ]
            }
        ]

        self.assertEqual(
            ["lossless/flac"],
            watcher.matching_indexed_labels(tracks, quality="lossless", codec="flac"),
        )

    def test_cache_watcher_selection_uses_only_filtered_cached_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            write_fake_m4a(cache_dir / "cache-aac.bin")
            write_fake_m4a(cache_dir / "cache-flac.bin")
            records = [
                source_record("cache-aac", quality="highest", size=40, bitrate=260000),
                source_record(
                    "cache-flac",
                    track_id="target-track",
                    quality="lossless",
                    size=40,
                    bitrate=1500000,
                    vtype="mp4",
                    codec_type="flac",
                ),
            ]
            records[1]["track"]["track"]["name"] = "Target Song"
            report = analyzer.analyze_records(records, cache_dir)
            tracks = watcher.filter_tracks_by_query(report["items"], "target")
            items = watcher.selection_items_for_tracks(
                tracks,
                quality="lossless",
                codec="flac",
                output_format="original",
            )

        self.assertEqual([{"cache_uuid": "cache-flac", "format": "original"}], items)

    def test_cache_watcher_matching_cached_files_returns_best_per_track(self) -> None:
        tracks = [
            {
                "cachedFiles": [
                    {
                        "cacheUuid": "low",
                        "quality": "highest",
                        "codecType": "aac",
                        "bitrate": 128000,
                        "sourceSize": 20,
                        "indexedSize": 20,
                    },
                    {
                        "cacheUuid": "high",
                        "quality": "highest",
                        "codecType": "aac",
                        "bitrate": 260000,
                        "sourceSize": 40,
                        "indexedSize": 40,
                    },
                ]
            }
        ]

        matches = watcher.matching_cached_files(tracks, quality="highest", codec="aac")

        self.assertEqual(["high"], [item["cacheUuid"] for item in matches])

    def test_cache_watcher_complete_cache_uuids_requires_indexed_size_match(self) -> None:
        self.assertEqual(
            {"complete"},
            watcher.complete_cache_uuids(
                [
                    {"cacheUuid": "complete", "sourceSize": 40, "indexedSize": 40},
                    {"cacheUuid": "partial", "sourceSize": 20, "indexedSize": 40},
                    {"cacheUuid": "unknown", "sourceSize": 20},
                ]
            ),
        )

    def test_cache_watcher_filter_items_by_cache_uuid_keeps_complete_selection_only(self) -> None:
        items = [
            {"cache_uuid": "complete", "format": "playable"},
            {"cache_uuid": "partial", "format": "playable"},
        ]

        self.assertEqual(
            [{"cache_uuid": "complete", "format": "playable"}],
            watcher.filter_items_by_cache_uuid(items, {"complete"}),
        )

    def test_cache_watcher_old_cache_file_is_stable_on_first_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.bin"
            path.write_bytes(b"audio")
            os.utime(path, (100.0, 100.0))

            old = watcher.old_cache_uuids(
                [{"cacheUuid": "cache-1", "path": str(path)}],
                wall_now=105.0,
                stable_seconds=2.0,
            )
            stable, state = watcher.stable_cache_uuids(
                {"cache-1": 4},
                {},
                now=10.0,
                stable_seconds=2.0,
                already_stable=old,
            )

        self.assertEqual({"cache-1"}, old)
        self.assertEqual({"cache-1"}, stable)
        self.assertEqual((4, 8.0), state["cache-1"])

    def test_cache_watcher_size_change_resets_stability_window(self) -> None:
        stable, state = watcher.stable_cache_uuids(
            {"cache-1": 8},
            {"cache-1": (4, 1.0)},
            now=3.0,
            stable_seconds=2.0,
        )

        self.assertEqual(set(), stable)
        self.assertEqual((8, 3.0), state["cache-1"])

    def test_cache_watcher_builds_export_command_with_requested_options(self) -> None:
        command = watcher.build_export_command(
            cache_dir=Path("/cache"),
            output_dir=Path("/out"),
            selection_file=Path("/tmp/selection.json"),
            default_format="playable",
            mp3_bitrate=256,
            device_node=Path("/Applications/汽水音乐.app/Contents/Resources/app.asar.unpacked/device.node"),
            raw_key="00" * 16,
            overwrite=True,
            verify_audio=True,
            dry_run=True,
            allow_size_mismatch=True,
            require_output_match=True,
            progress=True,
        )

        self.assertEqual(sys.executable, command[0])
        self.assertIn("export_sodamusic_cache.py", command[1])
        self.assertIn("--cache-dir", command)
        self.assertIn("/cache", command)
        self.assertIn("--output-dir", command)
        self.assertIn("/out", command)
        self.assertIn("--selection-file", command)
        self.assertIn("/tmp/selection.json", command)
        self.assertIn("--format", command)
        self.assertIn("playable", command)
        self.assertIn("--mp3-bitrate", command)
        self.assertIn("256", command)
        self.assertIn("--device-node", command)
        self.assertIn("--raw-key", command)
        self.assertIn("00000000000000000000000000000000", command)
        self.assertIn("--overwrite", command)
        self.assertIn("--verify-audio", command)
        self.assertIn("--require-output-match", command)
        self.assertIn("--dry-run", command)
        self.assertIn("--allow-size-mismatch", command)
        self.assertIn("--progress", command)

    def test_cache_watcher_export_command_omits_unrequested_options(self) -> None:
        command = watcher.build_export_command(
            cache_dir=Path("/cache"),
            output_dir=Path("/out"),
            selection_file=Path("/tmp/selection.json"),
            default_format="original",
            mp3_bitrate=192,
            progress=False,
        )

        self.assertNotIn("--device-node", command)
        self.assertNotIn("--raw-key", command)
        self.assertNotIn("--overwrite", command)
        self.assertNotIn("--verify-audio", command)
        self.assertNotIn("--require-output-match", command)
        self.assertNotIn("--dry-run", command)
        self.assertNotIn("--allow-size-mismatch", command)
        self.assertNotIn("--progress", command)

    def test_target_cli_resolves_target_filters_with_explicit_overrides(self) -> None:
        self.assertEqual(
            ("lossless", "flac", ""),
            target_cli.resolve_target_filters(target="lossless/flac"),
        )
        self.assertEqual(
            ("highest", "flac", "mp4"),
            target_cli.resolve_target_filters(
                target="lossless/flac",
                quality="highest",
                extension="mp4",
            ),
        )

    def test_target_cli_auto_output_format_follows_target_codec(self) -> None:
        self.assertEqual(
            "flac",
            target_cli.resolve_target_output_format("auto", target="lossless/flac"),
        )
        self.assertEqual(
            "mp3",
            target_cli.resolve_target_output_format("auto", target="highest/mp3"),
        )
        self.assertEqual(
            "playable",
            target_cli.resolve_target_output_format("auto", target="highest/aac"),
        )
        self.assertEqual(
            "original",
            target_cli.resolve_target_output_format("original", target="lossless/flac"),
        )

    def test_target_cli_auto_output_match_follows_resolved_format(self) -> None:
        self.assertTrue(target_cli.should_require_output_match(None, "flac"))
        self.assertTrue(target_cli.should_require_output_match(None, "mp3"))
        self.assertFalse(target_cli.should_require_output_match(None, "playable"))
        self.assertFalse(target_cli.should_require_output_match(False, "flac"))
        self.assertTrue(target_cli.should_require_output_match(True, "playable"))

    def test_target_cli_analyze_target_requires_unique_indexed_quality(self) -> None:
        tracks = [
            {
                "trackId": "track-1",
                "title": "Song",
                "artists": "Artist",
                "album": "Album",
                "indexedCandidates": [{"quality": "highest", "codecType": "aac", "extension": "m4a"}],
                "cachedFiles": [],
            }
        ]

        with (
            patch.object(target_cli, "parse_entries", return_value=[]),
            patch.object(target_cli, "analyze_records", return_value={"items": tracks, "tracks": 1}),
            patch.object(target_cli, "filter_report", side_effect=lambda report, _track_filter: report),
        ):
            status, matched, message = target_cli.analyze_target(
                Path("/cache"),
                watcher.TrackFilter(query="song"),
                quality="lossless",
                codec="flac",
            )

        self.assertEqual(target_cli.EXIT_TARGET_NOT_INDEXED, status)
        self.assertEqual(tracks, matched)
        self.assertIn("highest/aac", message)

    def test_target_cli_analyze_target_uses_target_quality_to_disambiguate(self) -> None:
        tracks = [
            {
                "trackId": "aac-track",
                "title": "Song",
                "artists": "Artist",
                "album": "Album",
                "indexedCandidates": [{"quality": "highest", "codecType": "aac", "extension": "m4a"}],
                "cachedFiles": [],
            },
            {
                "trackId": "flac-track",
                "title": "Song",
                "artists": "Artist",
                "album": "Album",
                "indexedCandidates": [{"quality": "lossless", "codecType": "flac", "extension": "mp4"}],
                "cachedFiles": [],
            },
        ]

        with (
            patch.object(target_cli, "parse_entries", return_value=[]),
            patch.object(target_cli, "analyze_records", return_value={"items": tracks, "tracks": 2}),
            patch.object(target_cli, "filter_report", side_effect=lambda report, _track_filter: report),
        ):
            status, matched, message = target_cli.analyze_target(
                Path("/cache"),
                watcher.TrackFilter(query="song"),
                quality="lossless",
                codec="flac",
            )

        self.assertEqual(0, status)
        self.assertEqual(["flac-track"], [track["trackId"] for track in matched])
        self.assertIn("目标歌曲和品质", message)

    def test_target_cli_analyze_target_still_requires_selection_when_target_quality_is_ambiguous(self) -> None:
        tracks = [
            {
                "trackId": "flac-track-a",
                "title": "Song",
                "artists": "Artist",
                "album": "Album",
                "indexedCandidates": [{"quality": "lossless", "codecType": "flac", "extension": "mp4"}],
                "cachedFiles": [],
            },
            {
                "trackId": "flac-track-b",
                "title": "Song",
                "artists": "Artist",
                "album": "Album",
                "indexedCandidates": [{"quality": "lossless", "codecType": "flac", "extension": "mp4"}],
                "cachedFiles": [],
            },
        ]

        with (
            patch.object(target_cli, "parse_entries", return_value=[]),
            patch.object(target_cli, "analyze_records", return_value={"items": tracks, "tracks": 2}),
            patch.object(target_cli, "filter_report", side_effect=lambda report, _track_filter: report),
        ):
            status, matched, message = target_cli.analyze_target(
                Path("/cache"),
                watcher.TrackFilter(query="song"),
                quality="lossless",
                codec="flac",
            )

        self.assertEqual(target_cli.EXIT_MULTIPLE_TRACKS, status)
        self.assertEqual(tracks, matched)
        self.assertIn("匹配到 2 首歌曲", message)

    def test_target_cli_wait_index_retries_waitable_status_until_indexed(self) -> None:
        calls = [
            (target_cli.EXIT_NO_MATCH, [], "missing"),
            (
                0,
                [
                    {
                        "trackId": "track-1",
                        "title": "Song",
                        "artists": "Artist",
                        "indexedCandidates": [{"quality": "lossless", "codecType": "flac", "extension": "mp4"}],
                        "cachedFiles": [],
                    }
                ],
                "ready",
            ),
        ]

        with (
            patch.object(target_cli, "analyze_target", side_effect=calls),
            patch.object(target_cli.time, "sleep"),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                status, tracks, message = target_cli.wait_for_indexed_target(
                    Path("/cache"),
                    watcher.TrackFilter(query="song"),
                    quality="lossless",
                    codec="flac",
                    wait_index=True,
                    interval=0.5,
                )

        self.assertEqual(0, status)
        self.assertEqual("ready", message)
        self.assertEqual("track-1", tracks[0]["trackId"])

    def test_target_cli_wait_index_does_not_retry_once_mode(self) -> None:
        with patch.object(
            target_cli,
            "analyze_target",
            return_value=(target_cli.EXIT_NO_MATCH, [], "missing"),
        ) as analyze:
            status, tracks, message = target_cli.wait_for_indexed_target(
                Path("/cache"),
                watcher.TrackFilter(query="song"),
                quality="lossless",
                codec="flac",
                wait_index=True,
                once=True,
            )

        self.assertEqual(target_cli.EXIT_NO_MATCH, status)
        self.assertEqual([], tracks)
        self.assertEqual("missing", message)
        analyze.assert_called_once()

    def test_target_cli_prints_cached_target_details(self) -> None:
        tracks = [
            {
                "trackId": "track-1",
                "title": "Song",
                "artists": "Artist",
                "indexedCandidates": [{"quality": "lossless", "codecType": "flac", "extension": "mp4"}],
                "indexedLabels": ["lossless/flac"],
                "cachedLabels": ["lossless/flac"],
                "cachedFiles": [
                    {
                        "cacheUuid": "cache-flac",
                        "resourceId": "resource_F_lossless",
                        "quality": "lossless",
                        "codecType": "flac",
                        "extension": "mp4",
                        "sourceSize": 40,
                        "indexedSize": 40,
                        "encrypted": True,
                    }
                ],
            }
        ]

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            target_cli.print_target_context(tracks, quality="lossless", codec="flac")

        text = output.getvalue()
        self.assertIn("目标品质已缓存: cache-flac", text)
        self.assertIn("resourceId=resource_F_lossless", text)
        self.assertIn("40/40 bytes", text)
        self.assertIn("encrypted", text)

    def test_target_cli_builds_watcher_command_for_locked_track(self) -> None:
        command = target_cli.build_watch_command(
            target_cli.TargetConfig(
                cache_dir=Path("/cache"),
                output_dir=Path("/out"),
                selection_out=Path("/tmp/selection.json"),
                track_id="track-1",
                target="lossless/flac",
                selection_format="auto",
                stable_seconds=1,
                interval=2,
                timeout=30,
                export_when_found=True,
                default_format="auto",
                mp3_bitrate=256,
                device_node=Path("/app/device.node"),
                overwrite=True,
                verify_audio=True,
                export_dry_run=True,
            )
        )

        self.assertEqual(sys.executable, command[0])
        self.assertIn("watch_sodamusic_cache.py", command[1])
        self.assertIn("--cache-dir", command)
        self.assertIn("/cache", command)
        self.assertIn("--track-id", command)
        self.assertIn("track-1", command)
        self.assertIn("--target", command)
        self.assertIn("lossless/flac", command)
        self.assertEqual("flac", command[command.index("--selection-format") + 1])
        self.assertIn("--require-indexed", command)
        self.assertIn("--require-single-track", command)
        self.assertIn("--selection-out", command)
        self.assertIn("/tmp/selection.json", command)
        self.assertIn("--export-when-found", command)
        self.assertIn("--output-dir", command)
        self.assertIn("/out", command)
        self.assertEqual("flac", command[command.index("--default-format") + 1])
        self.assertIn("--device-node", command)
        self.assertIn("/app/device.node", command)
        self.assertIn("--overwrite", command)
        self.assertIn("--verify-audio", command)
        self.assertIn("--require-output-match", command)
        self.assertIn("--export-dry-run", command)

    def test_target_cli_can_disable_auto_output_match_for_flac_target(self) -> None:
        command = target_cli.build_watch_command(
            target_cli.TargetConfig(
                cache_dir=Path("/cache"),
                output_dir=Path("/out"),
                selection_out=Path("/tmp/selection.json"),
                track_id="track-1",
                target="lossless/flac",
                selection_format="auto",
                export_when_found=True,
                default_format="auto",
                require_output_match=False,
            )
        )

        self.assertEqual("flac", command[command.index("--selection-format") + 1])
        self.assertEqual("flac", command[command.index("--default-format") + 1])
        self.assertNotIn("--require-output-match", command)

    def test_batch_target_loads_json_items_and_builds_target_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target_list = Path(tmp) / "targets.json"
            target_list.write_text(
                """
                {
                  "items": [
                    {
                      "query": "Song",
                      "artist": "Artist",
                      "target": "lossless/flac",
                      "timeout": 12,
                      "requireOutputMatch": false
                    }
                  ]
                }
                """,
                encoding="utf-8",
            )
            targets = batch_target.load_targets(target_list)

        self.assertEqual(1, len(targets))
        self.assertEqual("Song", targets[0].query)
        self.assertEqual("lossless/flac", targets[0].target)
        self.assertFalse(targets[0].require_output_match)

        command = batch_target.target_command(
            targets[0],
            batch_target.BatchOptions(
                cache_dir=Path("/cache"),
                output_dir=Path("/out"),
                device_node=Path("/app/device.node"),
                timeout=30,
                dry_run=True,
            ),
        )

        self.assertEqual(sys.executable, command[0])
        self.assertIn("target_sodamusic_cache.py", command[1])
        self.assertIn("--query", command)
        self.assertIn("Song", command)
        self.assertIn("--artist", command)
        self.assertIn("Artist", command)
        self.assertIn("--target", command)
        self.assertIn("lossless/flac", command)
        self.assertIn("--timeout", command)
        self.assertIn("12", command)
        self.assertIn("--wait-index", command)
        self.assertIn("--no-require-output-match", command)
        self.assertIn("--device-node", command)
        self.assertIn("/app/device.node", command)
        self.assertIn("--dry-run", command)

    def test_batch_target_loads_csv_and_jsonl_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "targets.csv"
            csv_path.write_text(
                "trackId,target,outputDir\ntrack-1,highest/aac,/tmp/song-out\n",
                encoding="utf-8",
            )
            jsonl_path = Path(tmp) / "targets.jsonl"
            jsonl_path.write_text(
                '{"title":"Song","artist":"Artist","version":"lossless/flac"}\n',
                encoding="utf-8",
            )

            csv_targets = batch_target.load_targets(csv_path)
            jsonl_targets = batch_target.load_targets(jsonl_path)

        self.assertEqual("track-1", csv_targets[0].track_id)
        self.assertEqual("highest/aac", csv_targets[0].target)
        self.assertEqual(Path("/tmp/song-out"), csv_targets[0].output_dir)
        self.assertEqual("Song", jsonl_targets[0].title)
        self.assertEqual("lossless/flac", jsonl_targets[0].target)

    def test_batch_target_run_batch_stops_or_continues_on_errors(self) -> None:
        targets = [
            batch_target.BatchTarget(query="first", target="highest/aac"),
            batch_target.BatchTarget(query="second", target="highest/aac"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            options = batch_target.BatchOptions(
                cache_dir=Path("/cache"),
                output_dir=Path(tmp) / "out",
                batch_manifest=Path(tmp) / "batch-manifest.json",
            )

            with patch.object(batch_target.subprocess, "run") as run:
                run.side_effect = [
                    type("Result", (), {"returncode": 7})(),
                    type("Result", (), {"returncode": 0})(),
                ]
                with contextlib.redirect_stdout(io.StringIO()):
                    status = batch_target.run_batch(targets, options)

            self.assertEqual(batch_target.FAILED, status)
            self.assertEqual(2, run.call_count)
            manifest = json.loads(options.batch_manifest.read_text(encoding="utf-8"))
            self.assertEqual([7, 0], [row["returncode"] for row in manifest])

        with tempfile.TemporaryDirectory() as tmp:
            stop_options = batch_target.BatchOptions(
                cache_dir=Path("/cache"),
                output_dir=Path(tmp) / "out",
                batch_manifest=Path(tmp) / "batch-manifest.json",
                continue_on_error=False,
            )
            with patch.object(batch_target.subprocess, "run") as run:
                run.return_value = type("Result", (), {"returncode": 7})()
                with contextlib.redirect_stdout(io.StringIO()):
                    status = batch_target.run_batch(targets, stop_options)

        self.assertEqual(7, status)
        run.assert_called_once()

    def test_batch_target_manifest_includes_exporter_manifest_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "out"
            output_dir.mkdir()
            options = batch_target.BatchOptions(
                cache_dir=Path("/cache"),
                output_dir=output_dir,
                batch_manifest=Path(tmp) / "batch-manifest.json",
            )
            targets = [batch_target.BatchTarget(query="Song", target="highest/aac")]

            def write_manifest(*_args: object, **_kwargs: object) -> object:
                (output_dir / "manifest.json").write_text(
                    json.dumps([{"title": "Song", "copied": True, "skipped_reason": ""}]),
                    encoding="utf-8",
                )
                return type("Result", (), {"returncode": 0})()

            with patch.object(batch_target.subprocess, "run") as run:
                run.side_effect = write_manifest
                with contextlib.redirect_stdout(io.StringIO()):
                    status = batch_target.run_batch(targets, options)

            manifest = json.loads(options.batch_manifest.read_text(encoding="utf-8"))
            csv_exists = options.batch_manifest.with_suffix(".csv").exists()

        self.assertEqual(batch_target.SUCCESS, status)
        self.assertEqual("Song", manifest[0]["exports"][0]["title"])
        self.assertTrue(csv_exists)

    def test_batch_target_manifest_does_not_reuse_stale_exporter_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "out"
            output_dir.mkdir()
            manifest_path = output_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps([{"title": "Old Song", "copied": True, "skipped_reason": ""}]),
                encoding="utf-8",
            )
            stale_time = 1000.0
            os.utime(manifest_path, (stale_time, stale_time))
            options = batch_target.BatchOptions(
                cache_dir=Path("/cache"),
                output_dir=output_dir,
                batch_manifest=Path(tmp) / "batch-manifest.json",
            )
            targets = [batch_target.BatchTarget(query="Missing", target="highest/aac")]

            with (
                patch.object(batch_target.time, "time", return_value=stale_time + 100),
                patch.object(batch_target.subprocess, "run") as run,
            ):
                run.return_value = type("Result", (), {"returncode": 2})()
                with contextlib.redirect_stdout(io.StringIO()):
                    status = batch_target.run_batch(targets, options)

            manifest = json.loads(options.batch_manifest.read_text(encoding="utf-8"))

        self.assertEqual(batch_target.FAILED, status)
        self.assertEqual([], manifest[0]["exports"])

    def test_batch_target_preflight_reports_cached_and_missing_state(self) -> None:
        cached_file = {
            "cacheUuid": "cache-flac",
            "resourceId": "resource_F_lossless",
            "quality": "lossless",
            "codecType": "flac",
            "extension": "mp4",
            "sourceSize": 40,
            "indexedSize": 40,
            "encrypted": True,
        }
        cached_track = {
            "trackId": "track-1",
            "title": "Song",
            "artists": "Artist",
            "album": "Album",
            "indexedCandidates": [{"quality": "lossless", "codecType": "flac", "extension": "mp4"}],
            "cachedFiles": [cached_file],
        }
        targets = [
            batch_target.BatchTarget(query="Song", target="lossless/flac"),
            batch_target.BatchTarget(query="Missing", target="highest/aac"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "preflight.json"
            options = batch_target.BatchOptions(
                cache_dir=Path("/cache"),
                output_dir=Path(tmp) / "out",
                preflight_out=report_path,
            )
            with (
                patch.object(
                    batch_target,
                    "analyze_target",
                    side_effect=[
                        (0, [cached_track], "ready"),
                        (target_cli.EXIT_NO_MATCH, [], "missing"),
                    ],
                ),
                patch.object(
                    batch_target,
                    "matching_cached_files",
                    side_effect=lambda tracks, **_kwargs: tracks[0].get("cachedFiles", []) if tracks else [],
                ),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    status = batch_target.run_preflight(targets, options)

            report = json.loads(report_path.read_text(encoding="utf-8"))
            csv_exists = report_path.with_suffix(".csv").exists()

        self.assertEqual(batch_target.FAILED, status)
        self.assertTrue(csv_exists)
        self.assertEqual("cached", report[0]["status"])
        self.assertEqual(["cache-flac"], report[0]["target_cache_uuids"])
        self.assertEqual("flac", report[0]["target_filter"]["codec"])
        self.assertIn("resource_F_lossless", report[0]["target_cache_details"][0]["detail"])
        self.assertEqual("no_match", report[1]["status"])
        self.assertEqual(target_cli.EXIT_NO_MATCH, report[1]["status_code"])

    def test_batch_target_preflight_succeeds_for_indexed_not_cached(self) -> None:
        indexed_track = {
            "trackId": "track-1",
            "title": "Song",
            "artists": "Artist",
            "album": "Album",
            "indexedCandidates": [{"quality": "lossless", "codecType": "flac", "extension": "mp4"}],
            "cachedFiles": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "preflight.json"
            options = batch_target.BatchOptions(
                cache_dir=Path("/cache"),
                output_dir=Path(tmp) / "out",
                preflight_out=report_path,
            )
            with (
                patch.object(batch_target, "analyze_target", return_value=(0, [indexed_track], "indexed")),
                patch.object(batch_target, "matching_cached_files", return_value=[]),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    status = batch_target.run_preflight(
                        [batch_target.BatchTarget(query="Song", target="lossless/flac")],
                        options,
                    )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(batch_target.SUCCESS, status)
        self.assertEqual("indexed_not_cached", report[0]["status"])
        self.assertFalse(report[0]["target_cached"])

    def test_selected_records_respects_selection_order_and_format(self) -> None:
        records = [source_record("cache-a"), source_record("cache-b")]
        selected, missing = exporter.selected_records(
            records,
            [
                {"cache_uuid": "cache-b", "format": "mp3"},
                {"cache_uuid": "cache-a", "format": "flac"},
            ],
        )

        self.assertEqual([], missing)
        self.assertEqual(["cache-b", "cache-a"], [record["chunkId"] for record, _ in selected])
        self.assertEqual(["mp3", "flac"], [output_format for _, output_format in selected])

    def test_selected_records_reports_unknown_cache_uuid(self) -> None:
        selected, missing = exporter.selected_records(
            [source_record("cache-a")],
            [{"cache_uuid": "missing", "format": "playable"}],
        )

        self.assertEqual([], selected)
        self.assertEqual(["missing"], missing)

    def test_web_validation_requires_selection_when_source_selection_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            errors = web.validate_payload(
                {
                    "cacheDir": str(cache_dir),
                    "outputDir": str(Path(tmp) / "out"),
                    "keyMode": "device",
                    "selectedSources": [],
                    "mp3Bitrate": "192",
                }
            )

        self.assertTrue(any("至少一个" in error for error in errors))

    def test_web_target_validation_requires_selector_and_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            errors = web.validate_target_payload(
                {
                    "cacheDir": str(cache_dir),
                    "outputDir": str(Path(tmp) / "out"),
                    "target": "",
                    "format": "playable",
                    "mp3Bitrate": "192",
                }
            )

        self.assertTrue(any("目标歌曲" in error for error in errors))
        self.assertTrue(any("目标缓存版本" in error for error in errors))

    def test_web_batch_command_passes_required_output_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            device_node = Path(tmp) / "device.node"
            device_node.write_bytes(b"")
            with patch.object(web, "resolve_device_node_path", return_value=device_node):
                command, _cache_dir, _output_dir, selection_file = web.build_command(
                    {
                        "cacheDir": str(cache_dir),
                        "outputDir": str(output_dir),
                        "deviceNode": str(device_node),
                        "keyMode": "device",
                        "format": "flac",
                        "selectedSources": [{"cacheUuid": "cache-1", "format": "flac"}],
                        "mp3Bitrate": "192",
                        "requireOutputMatch": True,
                    }
                )

        self.assertIn("--require-output-match", command)
        self.assertIsNotNone(selection_file)

    def test_web_metrics_reports_waiting_phase_from_target_logs(self) -> None:
        metrics = web.parse_job_metrics(
            [
                "本地索引里还没有匹配歌曲；先在官方客户端里搜索或播放一次目标歌曲。",
                "matched 1 track(s), target quality is indexed but not cached yet (lossless/flac)",
                "matched cached item(s), waiting for file size to stay unchanged for 1s",
            ]
        )

        self.assertEqual("waiting-cache-stable", metrics["phase"])
        self.assertEqual("等待缓存文件稳定", metrics["message"])
        self.assertEqual(0, metrics["total"])
        self.assertEqual(0, metrics["current"])

    def test_web_metrics_reports_exporting_phase_with_progress(self) -> None:
        metrics = web.parse_job_metrics(
            [
                "Running exporter: python export_sodamusic_cache.py",
                "Preparing records: 1",
                "Progress: 1/1 dry-run: Artist - Song",
                "Exported files: 0",
            ]
        )

        self.assertEqual("exporting", metrics["phase"])
        self.assertEqual("正在处理缓存", metrics["message"])
        self.assertEqual(1, metrics["total"])
        self.assertEqual(1, metrics["current"])

    def test_web_target_search_reports_indexed_and_cached_target_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            write_fake_m4a(cache_dir / "cache-flac.bin")
            records = [
                source_record(
                    "cache-flac",
                    track_id="target-track",
                    quality="lossless",
                    size=40,
                    bitrate=1500000,
                    vtype="mp4",
                    codec_type="flac",
                )
            ]
            records[0]["track"]["track"]["name"] = "Target Song"
            with patch.object(web, "parse_entries", return_value=records):
                result = web.search_target_tracks(
                    {
                        "cacheDir": str(cache_dir),
                        "query": "target",
                        "target": "lossless/flac",
                    }
                )

        self.assertEqual(1, result["total"])
        self.assertEqual("target-track", result["matches"][0]["trackId"])
        self.assertTrue(result["matches"][0]["targetIndexed"])
        self.assertTrue(result["matches"][0]["targetCached"])
        self.assertEqual(["cache-flac"], result["matches"][0]["targetCacheUuids"])
        self.assertEqual("cache-flac", result["matches"][0]["targetCachedFiles"][0]["cacheUuid"])
        self.assertEqual("resource_F_lossless", result["matches"][0]["targetCachedFiles"][0]["resourceId"])
        self.assertEqual(40, result["matches"][0]["targetCachedFiles"][0]["sourceSize"])
        self.assertTrue(result["matches"][0]["targetCachedFiles"][0]["encrypted"])

    def test_web_target_search_orders_target_quality_matches_first(self) -> None:
        tracks = [
            {
                "trackId": "no-target",
                "title": "Song C",
                "artists": "Artist",
                "indexedLabels": ["highest/aac"],
                "cachedLabels": ["highest/aac"],
                "indexedCandidates": [{"quality": "highest", "codecType": "aac", "extension": "m4a"}],
                "cachedFiles": [{"quality": "highest", "codecType": "aac", "extension": "m4a"}],
            },
            {
                "trackId": "indexed-target",
                "title": "Song B",
                "artists": "Artist",
                "indexedLabels": ["lossless/flac"],
                "cachedLabels": [],
                "indexedCandidates": [{"quality": "lossless", "codecType": "flac", "extension": "mp4"}],
                "cachedFiles": [],
            },
            {
                "trackId": "cached-target",
                "title": "Song A",
                "artists": "Artist",
                "indexedLabels": ["lossless/flac"],
                "cachedLabels": ["lossless/flac"],
                "indexedCandidates": [{"quality": "lossless", "codecType": "flac", "extension": "mp4"}],
                "cachedFiles": [
                    {
                        "cacheUuid": "cache-flac",
                        "quality": "lossless",
                        "codecType": "flac",
                        "extension": "mp4",
                    }
                ],
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            with (
                patch.object(web, "parse_entries", return_value=[]),
                patch.object(web, "analyze_records", return_value={"items": tracks, "tracks": 3}),
                patch.object(web, "filter_report", side_effect=lambda report, _track_filter: report),
            ):
                result = web.search_target_tracks(
                    {
                        "cacheDir": str(cache_dir),
                        "query": "song",
                        "target": "lossless/flac",
                    }
                )

        self.assertEqual(["cached-target", "indexed-target", "no-target"], [
            match["trackId"] for match in result["matches"]
        ])
        self.assertEqual([2, 1, 0], [match["targetRank"] for match in result["matches"]])

    def test_web_batch_target_preflight_reports_target_cache_state(self) -> None:
        tracks = [
            {
                "trackId": "cached-target",
                "title": "Song A",
                "artists": "Artist",
                "album": "Album",
                "indexedLabels": ["lossless/flac"],
                "cachedLabels": ["lossless/flac"],
                "indexedCandidates": [{"quality": "lossless", "codecType": "flac", "extension": "mp4"}],
                "cachedFiles": [
                    {
                        "cacheUuid": "cache-flac",
                        "resourceId": "resource_F_lossless",
                        "quality": "lossless",
                        "codecType": "flac",
                        "extension": "mp4",
                        "sourceSize": 40,
                        "indexedSize": 40,
                        "encrypted": True,
                    }
                ],
            },
            {
                "trackId": "indexed-target",
                "title": "Song B",
                "artists": "Artist",
                "album": "Album",
                "indexedLabels": ["lossless/flac"],
                "cachedLabels": [],
                "indexedCandidates": [{"quality": "lossless", "codecType": "flac", "extension": "mp4"}],
                "cachedFiles": [],
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            def filter_by_track_id(report: dict, track_filter: object) -> dict:
                track_id = getattr(track_filter, "track_id", "")
                return {
                    **report,
                    "items": [
                        track
                        for track in report["items"]
                        if not track_id or track["trackId"] == track_id
                    ],
                }

            with (
                patch.object(target_cli, "parse_entries", return_value=[]),
                patch.object(target_cli, "analyze_records", return_value={"items": tracks, "tracks": 2}),
                patch.object(target_cli, "filter_report", side_effect=filter_by_track_id),
            ):
                result = web.preflight_batch_targets(
                    {
                        "cacheDir": str(cache_dir),
                        "targets": [
                            {"trackId": "cached-target", "target": "lossless/flac"},
                            {"trackId": "indexed-target", "target": "lossless/flac"},
                        ],
                    }
                )

        self.assertTrue(result["ok"])
        self.assertEqual({"cached": 1, "indexed_not_cached": 1}, result["counts"])
        self.assertEqual(["cache-flac"], result["rows"][0]["target_cache_uuids"])
        self.assertEqual("indexed_not_cached", result["rows"][1]["status"])

    def test_web_target_command_uses_target_workflow_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            device_node = Path(tmp) / "device.node"
            device_node.write_bytes(b"")
            with (
                patch.object(web, "resolve_device_node_path", return_value=device_node),
                patch.object(web, "create_temp_selection_path", return_value=Path("/tmp/target-selection.json")),
            ):
                command, command_cache_dir, command_output_dir, selection_file = web.build_target_command(
                    {
                        "cacheDir": str(cache_dir),
                        "outputDir": str(output_dir),
                        "deviceNode": str(device_node),
                        "trackId": "target-track",
                        "query": "零几年听的情歌",
                        "artist": "GG啵！",
                        "target": "lossless/flac",
                        "format": "auto",
                        "selectionFormat": "auto",
                        "mp3Bitrate": "192",
                        "stableSeconds": 1,
                        "interval": 3,
                        "timeout": 60,
                        "dryRun": True,
                        "overwrite": True,
                        "verifyAudio": True,
                    }
                )

        self.assertEqual(sys.executable, command[0])
        self.assertIn("target_sodamusic_cache.py", command[1])
        self.assertIn("--target", command)
        self.assertIn("lossless/flac", command)
        self.assertEqual("flac", command[command.index("--selection-format") + 1])
        self.assertEqual("flac", command[command.index("--default-format") + 1])
        self.assertIn("--track-id", command)
        self.assertIn("target-track", command)
        self.assertIn("--query", command)
        self.assertIn("零几年听的情歌", command)
        self.assertIn("--artist", command)
        self.assertIn("GG啵！", command)
        self.assertIn("--selection-out", command)
        self.assertIn("/tmp/target-selection.json", command)
        self.assertIn("--wait-index", command)
        self.assertIn("--export-dry-run", command)
        self.assertIn("--overwrite", command)
        self.assertIn("--verify-audio", command)
        self.assertIn("--require-output-match", command)
        self.assertEqual(str(cache_dir.resolve()), command_cache_dir)
        self.assertEqual(str(output_dir.resolve()), command_output_dir)
        self.assertEqual(Path("/tmp/target-selection.json"), selection_file)

    def test_web_target_command_can_disable_auto_output_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            device_node = Path(tmp) / "device.node"
            device_node.write_bytes(b"")
            with (
                patch.object(web, "resolve_device_node_path", return_value=device_node),
                patch.object(web, "create_temp_selection_path", return_value=Path("/tmp/target-selection.json")),
            ):
                command, _cache_dir, _output_dir, _selection_file = web.build_target_command(
                    {
                        "cacheDir": str(cache_dir),
                        "outputDir": str(output_dir),
                        "deviceNode": str(device_node),
                        "trackId": "target-track",
                        "target": "lossless/flac",
                        "format": "auto",
                        "selectionFormat": "auto",
                        "mp3Bitrate": "192",
                        "requireOutputMatch": False,
                    }
                )

        self.assertEqual("flac", command[command.index("--selection-format") + 1])
        self.assertEqual("flac", command[command.index("--default-format") + 1])
        self.assertNotIn("--require-output-match", command)

    def test_web_target_auto_format_keeps_aac_playable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            device_node = Path(tmp) / "device.node"
            device_node.write_bytes(b"")
            with (
                patch.object(web, "resolve_device_node_path", return_value=device_node),
                patch.object(web, "create_temp_selection_path", return_value=Path("/tmp/target-selection.json")),
            ):
                command, _cache_dir, _output_dir, _selection_file = web.build_target_command(
                    {
                        "cacheDir": str(cache_dir),
                        "outputDir": str(output_dir),
                        "deviceNode": str(device_node),
                        "trackId": "target-track",
                        "target": "highest/aac",
                        "format": "auto",
                        "selectionFormat": "auto",
                        "mp3Bitrate": "192",
                    }
                )

        self.assertEqual("playable", command[command.index("--selection-format") + 1])
        self.assertEqual("playable", command[command.index("--default-format") + 1])

    def test_web_open_path_can_create_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "new-output"
            with (
                patch.object(web.sys, "platform", "darwin"),
                patch.object(web.subprocess, "run") as run,
                patch.object(web, "DEFAULT_OUTPUT_DIR", Path(tmp)),
            ):
                opened = web.open_path(str(output_dir), create=True)
                self.assertTrue(opened)
                self.assertTrue(output_dir.exists())
                run.assert_called_once()

    def test_launcher_rejects_legacy_web_service_without_api_version(self) -> None:
        self.assertFalse(
            launcher.is_ready_response(
                "SodaMusic Cache Export",
                {"cacheDir": str(exporter.DEFAULT_CACHE_DIR)},
            )
        )

    def test_launcher_accepts_current_web_api_version(self) -> None:
        self.assertTrue(
            launcher.is_ready_response(
                "SodaMusic Cache Export",
                {
                    "cacheDir": str(exporter.DEFAULT_CACHE_DIR),
                    "apiVersion": launcher.REQUIRED_WEB_API_VERSION,
                    "sources": {},
                },
            )
        )

    def test_dependency_check_reports_missing_python_package_without_install(self) -> None:
        with patch.object(deps, "has_python_package", return_value=False):
            report = deps.ensure_runtime_dependencies(auto_install=False)

        self.assertFalse(report.ok)
        self.assertIn("pycryptodome", report.missing)
        self.assertIn("mutagen", report.missing)

    def test_dependency_which_uses_augmented_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool_dir = Path(tmp)
            tool = tool_dir / "demo-tool"
            tool.write_text("#!/bin/sh\n", encoding="utf-8")
            tool.chmod(0o755)
            with (
                patch.object(deps, "EXTRA_PATHS", (str(tool_dir),)),
                patch.dict(deps.os.environ, {"PATH": ""}),
            ):
                self.assertEqual(str(tool), deps.which("demo-tool"))

    def test_dependency_check_treats_ffmpeg_as_optional_without_auto_install(self) -> None:
        def fake_which(command: str):
            if command == "ffmpeg":
                return None
            return f"/usr/bin/{command}"

        with (
            patch.object(deps, "has_python_package", return_value=True),
            patch.object(deps, "which", side_effect=fake_which),
        ):
            report = deps.ensure_runtime_dependencies(auto_install=False)

        self.assertTrue(report.ok)
        self.assertNotIn("ffmpeg", report.missing)
        self.assertTrue(any("ffmpeg" in warning for warning in report.warnings))

    def test_dependency_check_installs_missing_ffmpeg_with_homebrew(self) -> None:
        completed = deps.subprocess.CompletedProcess(["brew"], 0)
        installed = False

        def fake_which(command: str):
            if command == "ffmpeg":
                return "/opt/homebrew/bin/ffmpeg" if installed else None
            if command == "brew":
                return "/opt/homebrew/bin/brew"
            return f"/usr/bin/{command}"

        def fake_brew_install(packages: list[str], *, capture_output: bool = True):
            nonlocal installed
            self.assertIn("ffmpeg", packages)
            installed = True
            return completed

        with (
            patch.object(deps.sys, "platform", "darwin"),
            patch.object(deps, "has_python_package", return_value=True),
            patch.object(deps, "which", side_effect=fake_which),
            patch.object(deps, "brew_install", side_effect=fake_brew_install) as install,
        ):
            report = deps.ensure_runtime_dependencies(auto_install=True)

        self.assertTrue(report.ok)
        install.assert_called_once()
        self.assertTrue(any("ffmpeg" in item for item in report.installed))
        self.assertFalse(any("ffmpeg" in warning for warning in report.warnings))

    def test_dependency_install_can_stream_output(self) -> None:
        completed = deps.subprocess.CompletedProcess(["pip"], 0)
        installed = False

        def has_package(_module_name: str) -> bool:
            return installed

        def install_requirements(*, capture_output: bool = True):
            nonlocal installed
            installed = True
            return completed

        with (
            patch.object(deps, "has_python_package", side_effect=has_package),
            patch.object(deps, "install_python_requirements", side_effect=install_requirements) as install,
            patch.object(deps, "which", return_value="/usr/bin/tool"),
        ):
            report = deps.ensure_runtime_dependencies(
                auto_install=True,
                show_install_output=True,
            )

        self.assertTrue(report.ok)
        install.assert_called_once_with(capture_output=False)

    def test_brew_install_disables_auto_update(self) -> None:
        completed = deps.subprocess.CompletedProcess(["brew"], 0)
        with (
            patch.object(deps, "which", return_value="/opt/homebrew/bin/brew"),
            patch.object(deps.subprocess, "run", return_value=completed) as run,
        ):
            deps.brew_install(["ffmpeg"])

        env = run.call_args.kwargs["env"]
        self.assertEqual("1", env["HOMEBREW_NO_AUTO_UPDATE"])


class MainEntryTests(unittest.TestCase):
    """Direct coverage for main() CLI entry points (arg parsing, validation, early exits, summaries)."""

    # ------------------------------------------------------------------
    # export_sodamusic_cache.main
    # ------------------------------------------------------------------
    def test_export_main_exits_when_entries_db_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            with (
                patch.object(sys, "argv", ["prog", "--cache-dir", str(cache_dir), "--dry-run"]),
                patch.object(exporter, "parse_entries"),
                patch.object(exporter, "selected_records", return_value=([], [])),
                patch.object(exporter, "prepare_decoded_spades", return_value={}),
                patch.object(exporter, "export_records", return_value=[]),
                patch.object(exporter, "write_manifests"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as cm:
                    exporter.main()
        self.assertIn("entries.db not found", str(cm.exception))

    def test_export_main_rejects_bad_mp3_bitrate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            with (
                patch.object(sys, "argv", ["prog", "--cache-dir", str(cache_dir), "--mp3-bitrate", "0"]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(SystemExit):
                    exporter.main()

    def test_export_main_rejects_bad_raw_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            with (
                patch.object(sys, "argv", ["prog", "--cache-dir", str(cache_dir), "--raw-key", "nothex"]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(SystemExit):
                    exporter.main()

    def test_export_main_dry_run_happy_path_prints_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            output_dir = Path(tmp) / "out"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            rows = [
                exporter.ExportRecord(
                    source=Path("s1.bin"),
                    output=Path("o1.m4a"),
                    track_id="t1",
                    title="Song",
                    artists="Artist",
                    album="Album",
                    quality="highest",
                    bitrate=256000,
                    duration_ms=120000,
                    cache_uuid="c1",
                    resource_id="r1",
                    extension="m4a",
                    source_extension="mp4",
                    indexed_extension="mp4",
                    indexed_codec_type="aac",
                    output_format="playable",
                    output_container="m4a",
                    output_codec_type="aac",
                    output_sample_rate=44100,
                    output_bits_per_sample=None,
                    output_probe_error="",
                    output_matches_request=True,
                    output_mismatch_reason="",
                    source_size=40,
                    indexed_size=40,
                    encrypted=True,
                    encryption_method="cenc",
                    index_key_id="",
                    mp4_scheme="",
                    mp4_key_id="",
                    mp4_has_sample_encryption=False,
                    decrypted=True,
                    copied=True,
                    skipped_reason="",
                )
            ]
            with (
                patch.object(sys, "argv", ["prog", "--cache-dir", str(cache_dir), "--output-dir", str(output_dir), "--dry-run"]),
                patch.object(exporter, "parse_entries", return_value=[{}]),
                patch.object(exporter, "selected_records", return_value=([( {}, "" )], [])),
                patch.object(exporter, "prepare_decoded_spades", return_value={}),
                patch.object(exporter, "export_records", return_value=rows),
                patch.object(exporter, "write_manifests"),
                contextlib.redirect_stdout(io.StringIO()) as buf,
            ):
                rc = exporter.main()
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("Parsed records:", out)
        self.assertIn("Exported files:", out)
        self.assertIn("Dry run: no audio files were copied.", out)

    # ------------------------------------------------------------------
    # watch_sodamusic_cache.main
    # ------------------------------------------------------------------
    def test_watch_main_rejects_bad_mp3_bitrate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            sel = Path(tmp) / "sel.json"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            with (
                patch.object(sys, "argv", ["prog", "--cache-dir", str(cache_dir), "--selection-out", str(sel), "--mp3-bitrate", "-1"]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(SystemExit):
                    watcher.main()

    def test_watch_main_rejects_bad_stable_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            sel = Path(tmp) / "sel.json"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            with (
                patch.object(sys, "argv", ["prog", "--cache-dir", str(cache_dir), "--selection-out", str(sel), "--stable-seconds", "-5"]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(SystemExit):
                    watcher.main()

    def test_watch_main_once_no_match_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            sel = Path(tmp) / "sel.json"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            with (
                patch.object(sys, "argv", ["prog", "--cache-dir", str(cache_dir), "--selection-out", str(sel), "--once"]),
                patch.object(watcher, "scan_once", return_value=([], [], {})),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                rc = watcher.main()
        self.assertEqual(rc, 2)

    # ------------------------------------------------------------------
    # target_sodamusic_cache.main
    # ------------------------------------------------------------------
    def test_target_main_rejects_bad_mp3_bitrate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            with (
                patch.object(sys, "argv", ["prog", "--cache-dir", str(cache_dir), "--target", "highest/aac", "--mp3-bitrate", "0"]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(SystemExit):
                    target_cli.main()

    def test_target_main_exits_when_entries_db_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            with (
                patch.object(sys, "argv", ["prog", "--cache-dir", str(cache_dir), "--target", "highest/aac"]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as cm:
                    target_cli.main()
        self.assertIn("entries.db not found", str(cm.exception))

    # ------------------------------------------------------------------
    # batch_target_sodamusic_cache.main
    # ------------------------------------------------------------------
    def test_batch_main_exits_when_target_list_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            with (
                patch.object(sys, "argv", ["prog", str(missing), "--cache-dir", str(cache_dir)]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as cm:
                    batch_target.main()
        self.assertIn("target list not found", str(cm.exception))

    def test_batch_main_dispatches_to_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lst = Path(tmp) / "targets.json"
            lst.write_text('[{"query":"Song","target":"highest/aac"}]', encoding="utf-8")
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            (cache_dir / "entries.db").write_bytes(b"")
            with (
                patch.object(sys, "argv", ["prog", str(lst), "--cache-dir", str(cache_dir), "--preflight"]),
                patch.object(batch_target, "load_targets", return_value=[batch_target.BatchTarget(query="Song", target="highest/aac")]),
                patch.object(batch_target, "run_preflight", return_value=0) as rp,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                rc = batch_target.main()
        self.assertEqual(rc, 0)
        rp.assert_called_once()

    # ------------------------------------------------------------------
    # start_sodamusic_export.main
    # ------------------------------------------------------------------
    def test_start_main_exits_when_web_script_missing(self) -> None:
        with (
            patch.object(launcher, "WEB_SCRIPT", Path("/non/existent/web.py")),
            patch.object(sys, "argv", ["prog"]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            rc = launcher.main()
        self.assertEqual(rc, 1)

    def test_start_main_reports_dependency_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Create a dummy web script so the missing-script guard does not trigger
            dummy_web = Path(tmp) / "sodamusic_export_web.py"
            dummy_web.write_text("# dummy\n", encoding="utf-8")
            with (
                patch.object(launcher, "WEB_SCRIPT", dummy_web),
                patch.object(sys, "argv", ["prog", "--skip-dependency-install"]),
                patch.object(launcher, "ensure_runtime_dependencies") as deps_mock,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                report = type("R", (), {"ok": False, "errors": ["bad dep"], "missing": ["foo"], "warnings": [], "installed": []})()
                deps_mock.return_value = report
                rc = launcher.main()
        self.assertEqual(rc, 1)


class MetadataWriteTests(unittest.TestCase):
    def test_write_mp4_metadata_signature(self) -> None:
        metadata = exporter.AudioMetadata(
            title="Test Title",
            artists="Test Artist",
            album="Test Album",
            lyrics="Test lyrics",
            cover_urls=(),
        )
        cover = exporter.CoverImage(data=b"\xff\xd8\xff\xe0" + b"\x00" * 100, mime_type="image/jpeg")

        self.assertTrue(callable(exporter.write_mp4_metadata))
        self.assertIsInstance(metadata, exporter.AudioMetadata)
        self.assertIsInstance(cover, exporter.CoverImage)

    def test_write_mp3_metadata_signature(self) -> None:
        metadata = exporter.AudioMetadata(
            title="Test Title",
            artists="Test Artist",
            album="Test Album",
            lyrics="Test lyrics",
            cover_urls=(),
        )
        cover = exporter.CoverImage(data=b"\xff\xd8\xff\xe0" + b"\x00" * 100, mime_type="image/jpeg")

        self.assertTrue(callable(exporter.write_mp3_metadata))
        self.assertIsInstance(metadata, exporter.AudioMetadata)
        self.assertIsInstance(cover, exporter.CoverImage)

    def test_write_flac_metadata_signature(self) -> None:
        metadata = exporter.AudioMetadata(
            title="Test Title",
            artists="Test Artist",
            album="Test Album",
            lyrics="Test lyrics",
            cover_urls=(),
        )
        cover = exporter.CoverImage(data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, mime_type="image/png")

        self.assertTrue(callable(exporter.write_flac_metadata))
        self.assertIsInstance(metadata, exporter.AudioMetadata)
        self.assertIsInstance(cover, exporter.CoverImage)

    def test_audio_metadata_fields(self) -> None:
        metadata = exporter.AudioMetadata(
            title="Title",
            artists="Artist",
            album="Album",
            lyrics="Lyrics",
            cover_urls=("url1", "url2"),
        )
        self.assertEqual("Title", metadata.title)
        self.assertEqual("Artist", metadata.artists)
        self.assertEqual("Album", metadata.album)
        self.assertEqual("Lyrics", metadata.lyrics)
        self.assertEqual(("url1", "url2"), metadata.cover_urls)

    def test_cover_image_fields(self) -> None:
        cover = exporter.CoverImage(data=b"\xff\xd8", mime_type="image/jpeg")
        self.assertEqual(b"\xff\xd8", cover.data)
        self.assertEqual("image/jpeg", cover.mime_type)


class MetadataWriteRoundtripTests(unittest.TestCase):
    def _make_metadata(self, **overrides: str) -> exporter.AudioMetadata:
        defaults = dict(title="Title", artists="Artist", album="Album", lyrics="[00:01.00]Hello", cover_urls=())
        defaults.update(overrides)
        return exporter.AudioMetadata(**defaults)

    def _make_cover(self, mime: str = "image/jpeg") -> exporter.CoverImage:
        if mime == "image/png":
            return exporter.CoverImage(data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, mime_type=mime)
        return exporter.CoverImage(data=b"\xff\xd8\xff\xe0" + b"\x00" * 40, mime_type=mime)

    def test_write_mp3_metadata_roundtrip(self) -> None:
        import struct as _struct
        from mutagen.id3 import ID3

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fh:
            fh.write(b"\x00" * 128)
            path = Path(fh.name)
        try:
            metadata = self._make_metadata()
            cover = self._make_cover()
            cover_ok, lyrics_ok = exporter.write_mp3_metadata(path, metadata, cover)
            self.assertTrue(cover_ok)
            self.assertTrue(lyrics_ok)

            tags = ID3(path)
            self.assertEqual(tags["TIT2"].text[0], "Title")
            self.assertEqual(tags["TPE1"].text[0], "Artist")
            self.assertEqual(tags["TALB"].text[0], "Album")
            uslt = tags.get("USLT::und") or tags.get("USLT")
            self.assertIsNotNone(uslt)
            self.assertIn("Hello", uslt.text)
            self.assertTrue(tags.getall("APIC"))
        finally:
            path.unlink(missing_ok=True)

    def test_write_mp3_metadata_no_cover_no_lyrics(self) -> None:
        from mutagen.id3 import ID3

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fh:
            fh.write(b"\x00" * 128)
            path = Path(fh.name)
        try:
            metadata = self._make_metadata(lyrics="")
            cover_ok, lyrics_ok = exporter.write_mp3_metadata(path, metadata, None)
            self.assertFalse(cover_ok)
            self.assertFalse(lyrics_ok)

            tags = ID3(path)
            self.assertEqual(tags["TIT2"].text[0], "Title")
            self.assertFalse(tags.getall("USLT"))
            self.assertFalse(tags.getall("APIC"))
        finally:
            path.unlink(missing_ok=True)

    def _minimal_flac(self, path: Path) -> None:
        import struct as _struct
        streaminfo = (
            _struct.pack(">HH", 4096, 4096)
            + b"\x00" * 6
            + _struct.pack(">I", 44100 << 12 | 1 << 9 | 15 << 4)
            + b"\x00" * 20
        )
        block_header = _struct.pack(">I", 0x80000000 | len(streaminfo))
        path.write_bytes(b"fLaC" + block_header + streaminfo)

    def test_write_flac_metadata_roundtrip(self) -> None:
        from mutagen.flac import FLAC

        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as fh:
            path = Path(fh.name)
        try:
            self._minimal_flac(path)
            metadata = self._make_metadata()
            cover = self._make_cover("image/png")
            cover_ok, lyrics_ok = exporter.write_flac_metadata(path, metadata, cover)
            self.assertTrue(cover_ok)
            self.assertTrue(lyrics_ok)

            audio = FLAC(path)
            self.assertEqual(audio["title"][0], "Title")
            self.assertEqual(audio["artist"][0], "Artist")
            self.assertEqual(audio["album"][0], "Album")
            self.assertIn("Hello", audio["lyrics"][0])
            self.assertTrue(audio.pictures)
        finally:
            path.unlink(missing_ok=True)

    def test_write_flac_metadata_no_cover(self) -> None:
        from mutagen.flac import FLAC

        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as fh:
            path = Path(fh.name)
        try:
            self._minimal_flac(path)
            metadata = self._make_metadata(lyrics="")
            cover_ok, lyrics_ok = exporter.write_flac_metadata(path, metadata, None)
            self.assertFalse(cover_ok)
            self.assertFalse(lyrics_ok)

            audio = FLAC(path)
            self.assertEqual(audio["title"][0], "Title")
            self.assertFalse(audio.pictures)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
