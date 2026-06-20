"""Tests for previously untested utility functions across all source modules."""

from __future__ import annotations

import csv
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import analyze_sodamusic_cache as analyzer  # noqa: E402
import batch_target_sodamusic_cache as batch_target  # noqa: E402
import export_sodamusic_cache as exporter  # noqa: E402
import runtime_dependencies as deps  # noqa: E402
import target_sodamusic_cache as target_cli  # noqa: E402
import watch_sodamusic_cache as watcher  # noqa: E402
from helpers import pack  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _record(overrides: dict | None = None) -> dict:
    base = {
        "chunkId": "cache-1",
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
                                "codec_type": "aac",
                                "size": 40,
                                "quality": "highest",
                                "bitrate": 260000,
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
    if overrides:
        base.update(overrides)
    return base


def _write_fake_m4a(path: Path) -> None:
    path.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 28)


# ---------------------------------------------------------------------------
# exporter: format_lrc_time, krc_to_lrc, normalize_lyrics
# ---------------------------------------------------------------------------

class LyricFormatTests(unittest.TestCase):
    def test_format_lrc_time_zero(self) -> None:
        self.assertEqual("00:00.00", exporter.format_lrc_time(0))

    def test_format_lrc_time_large(self) -> None:
        self.assertEqual("02:03.45", exporter.format_lrc_time(123456))

    def test_krc_to_lrc_basic(self) -> None:
        krc = "[1000,500]<0,500,0>Hello\n[2500,500]<0,500,0>World"
        result = exporter.krc_to_lrc(krc)
        self.assertEqual("[00:01.00]Hello\n[00:02.50]World", result)

    def test_krc_to_lrc_skips_empty_lines(self) -> None:
        krc = "line1\n\nline2\n"
        result = exporter.krc_to_lrc(krc)
        self.assertEqual("line1\nline2", result)

    def test_krc_to_lrc_handles_crlf(self) -> None:
        krc = "[1000,500]<0,500,0>Hello\r\n[2500,500]<0,500,0>World"
        result = exporter.krc_to_lrc(krc)
        self.assertEqual("[00:01.00]Hello\n[00:02.50]World", result)

    def test_krc_to_lrc_non_timestamped_lines(self) -> None:
        krc = "plain text\n[1000,500]<0,500,0>Timed"
        result = exporter.krc_to_lrc(krc)
        self.assertEqual("plain text\n[00:01.00]Timed", result)

    def test_normalize_lyrics_krc_type(self) -> None:
        content = "[1000,500]<0,500,0>Hello"
        result = exporter.normalize_lyrics(content, lyric_type="krc")
        self.assertEqual("[00:01.00]Hello", result)

    def test_normalize_lyrics_auto_detect_krc(self) -> None:
        content = "[1000,500]<0,500,0>Hello"
        result = exporter.normalize_lyrics(content)
        self.assertEqual("[00:01.00]Hello", result)

    def test_normalize_lyrics_plain_lrc(self) -> None:
        content = "[00:01.00]Hello\n[00:02.50]World"
        result = exporter.normalize_lyrics(content)
        self.assertEqual("[00:01.00]Hello\n[00:02.50]World", result)

    def test_normalize_lyrics_empty(self) -> None:
        self.assertEqual("", exporter.normalize_lyrics(""))
        self.assertEqual("", exporter.normalize_lyrics("  \n  "))


# ---------------------------------------------------------------------------
# exporter: lyrics_from_value, cover_urls_from_value, image_url_root, etc.
# ---------------------------------------------------------------------------

class MetadataExtractionTests(unittest.TestCase):
    def test_lyrics_from_value_string(self) -> None:
        self.assertEqual("[00:01.00]Hello", exporter.lyrics_from_value("[1000,500]<0,500,0>Hello"))

    def test_lyrics_from_value_dict_with_content(self) -> None:
        value = {"content": "[1000,500]<0,500,0>Hi", "type": "krc"}
        self.assertEqual("[00:01.00]Hi", exporter.lyrics_from_value(value))

    def test_lyrics_from_value_dict_empty(self) -> None:
        self.assertEqual("", exporter.lyrics_from_value({}))

    def test_lyrics_from_value_non_dict_non_str(self) -> None:
        self.assertEqual("", exporter.lyrics_from_value(None))
        self.assertEqual("", exporter.lyrics_from_value(42))

    def test_extract_lyrics_from_record(self) -> None:
        record = _record()
        lyrics = exporter.extract_lyrics(record)
        self.assertEqual("[00:01.00]Hello\n[00:02.50]World", lyrics)

    def test_extract_lyrics_returns_empty_for_no_lyrics(self) -> None:
        record = _record()
        record["info"]["mediaDetail"].pop("lyrics", None)
        self.assertEqual("", exporter.extract_lyrics(record))

    def test_cover_urls_from_value_string(self) -> None:
        urls = exporter.cover_urls_from_value("https://example.com/cover.jpg")
        self.assertEqual(("https://example.com/cover.jpg",), urls)

    def test_cover_urls_from_value_string_not_url(self) -> None:
        self.assertEqual((), exporter.cover_urls_from_value("not-a-url"))

    def test_cover_urls_from_value_dict_with_urls(self) -> None:
        value = {"uri": "cover/test", "urls": ["https://img.example/"]}
        urls = exporter.cover_urls_from_value(value)
        self.assertTrue(any("300x300" in u for u in urls))
        self.assertTrue(any("noop" in u for u in urls))

    def test_cover_urls_from_value_dict_empty(self) -> None:
        self.assertEqual((), exporter.cover_urls_from_value({}))

    def test_cover_urls_from_value_dict_with_direct_url(self) -> None:
        value = {"url": "https://direct.example.com/image.jpg"}
        urls = exporter.cover_urls_from_value(value)
        self.assertEqual(("https://direct.example.com/image.jpg",), urls)

    def test_cover_urls_from_value_with_template_prefix(self) -> None:
        value = {"uri": "cover/test", "urls": ["https://img.example/"], "template_prefix": "240x240"}
        urls = exporter.cover_urls_from_value(value)
        self.assertTrue(any("240x240" in u for u in urls))

    def test_extract_cover_urls_from_record(self) -> None:
        record = _record()
        urls = exporter.extract_cover_urls(record)
        self.assertTrue(len(urls) > 0)

    def test_extract_cover_urls_empty_record(self) -> None:
        self.assertEqual((), exporter.extract_cover_urls({}))

    def test_image_url_root_uri_absolute(self) -> None:
        self.assertEqual(
            "https://other.com/img",
            exporter.image_url_root("https://base.com", "https://other.com/img"),
        )

    def test_image_url_root_uri_relative(self) -> None:
        self.assertEqual(
            "https://base.com/cover/test",
            exporter.image_url_root("https://base.com", "cover/test"),
        )

    def test_image_url_root_uri_empty(self) -> None:
        self.assertEqual("https://base.com", exporter.image_url_root("https://base.com", ""))

    def test_image_url_root_uri_starts_slash(self) -> None:
        self.assertEqual(
            "https://base.comtest",
            exporter.image_url_root("https://base.com", "/test"),
        )

    def test_first_dict_returns_first_dict(self) -> None:
        self.assertEqual({"a": 1}, exporter.first_dict(None, {"a": 1}, {"b": 2}))

    def test_first_dict_returns_empty_when_none(self) -> None:
        self.assertEqual({}, exporter.first_dict(None, "string", 42))

    def test_unique_strings(self) -> None:
        result = exporter.unique_strings([" a ", "b", "a", " c "])
        self.assertEqual(("a", "b", "c"), result)

    def test_unique_strings_skips_empty(self) -> None:
        result = exporter.unique_strings(["", "  ", "a"])
        self.assertEqual(("a",), result)


# ---------------------------------------------------------------------------
# exporter: track_identity, video_items, selected_video_item, is_encrypted, record_spade
# ---------------------------------------------------------------------------

class RecordInspectionTests(unittest.TestCase):
    def test_track_identity_full_record(self) -> None:
        track_id, title, artists, album, duration = exporter.track_identity(_record())
        self.assertEqual("track-1", track_id)
        self.assertEqual("Song", title)
        self.assertEqual("Artist", artists)
        self.assertEqual("Album", album)
        self.assertEqual(1234, duration)

    def test_track_identity_fallback_to_chunkid(self) -> None:
        record = _record()
        record["info"] = {}
        record["track"] = {}
        track_id, title, artists, album, duration = exporter.track_identity(record)
        self.assertEqual("cache-1", track_id)
        self.assertTrue(title.startswith("track-"))

    def test_video_items_returns_list(self) -> None:
        items = exporter.video_items(_record())
        self.assertEqual(1, len(items))
        self.assertIn("video_meta", items[0])

    def test_video_items_empty_record(self) -> None:
        self.assertEqual([], exporter.video_items({}))

    def test_selected_video_item_matches_size(self) -> None:
        record = _record()
        item = exporter.selected_video_item(record, source_size=40)
        self.assertEqual(40, item.get("video_meta", {}).get("size"))

    def test_selected_video_item_matches_quality(self) -> None:
        record = _record()
        item = exporter.selected_video_item(record, source_size=None)
        self.assertEqual("highest", item.get("video_meta", {}).get("quality"))

    def test_selected_video_item_empty_record(self) -> None:
        self.assertEqual({}, exporter.selected_video_item({}, source_size=None))

    def test_first_video_meta(self) -> None:
        meta = exporter.first_video_meta(_record())
        self.assertEqual("m4a", meta.get("vtype"))

    def test_first_video_meta_empty_record(self) -> None:
        self.assertEqual({}, exporter.first_video_meta({}))

    def test_is_encrypted_true(self) -> None:
        self.assertTrue(exporter.is_encrypted(_record()))

    def test_is_encrypted_false(self) -> None:
        record = _record()
        record["info"]["mediaDetail"]["video_model"]["video_list"][0]["encrypt_info"]["encrypt"] = False
        self.assertFalse(exporter.is_encrypted(record))

    def test_is_encrypted_fallback_to_resourceid(self) -> None:
        record = {"resourceId": "audio_encrypt_something"}
        self.assertTrue(exporter.is_encrypted(record))

    def test_is_encrypted_empty_record(self) -> None:
        self.assertFalse(exporter.is_encrypted({}))

    def test_record_spade_from_info(self) -> None:
        self.assertEqual("test-spade", exporter.record_spade(_record()))

    def test_record_spade_from_encrypt_info(self) -> None:
        record = _record()
        record["info"]["spade"] = ""
        record["info"]["mediaDetail"]["video_model"]["video_list"][0]["encrypt_info"]["spade_a"] = "from-encrypt"
        self.assertEqual("from-encrypt", exporter.record_spade(record))

    def test_record_spade_empty(self) -> None:
        record = _record()
        record["info"]["spade"] = ""
        record["info"]["mediaDetail"]["video_model"]["video_list"][0]["encrypt_info"].pop("spade_a", None)
        self.assertEqual("", exporter.record_spade(record))


# ---------------------------------------------------------------------------
# exporter: quality_rank, export_extension, looks_like_mp3_frame
# ---------------------------------------------------------------------------

class FormatDetectionTests(unittest.TestCase):
    def test_quality_rank_flac_override(self) -> None:
        self.assertEqual(60, exporter.quality_rank("highest", "flac"))

    def test_quality_rank_flac_extension_override(self) -> None:
        self.assertEqual(60, exporter.quality_rank("highest", "", "flac"))

    def test_quality_rank_known_qualities(self) -> None:
        self.assertEqual(30, exporter.quality_rank("highest"))
        self.assertEqual(20, exporter.quality_rank("higher"))
        self.assertEqual(10, exporter.quality_rank("medium"))
        self.assertEqual(60, exporter.quality_rank("lossless"))
        self.assertEqual(50, exporter.quality_rank("hi_res"))
        self.assertEqual(0, exporter.quality_rank("unknown"))

    def test_export_extension_flac(self) -> None:
        self.assertEqual("flac", exporter.export_extension("m4a", "flac"))

    def test_export_extension_mp3(self) -> None:
        self.assertEqual("mp3", exporter.export_extension("m4a", "mp3"))

    def test_export_extension_original(self) -> None:
        self.assertEqual("m4a", exporter.export_extension("m4a", "original"))
        self.assertEqual("flac", exporter.export_extension("flac", "original"))
        self.assertEqual("mp4", exporter.export_extension("mp4", "original"))

    def test_export_extension_playable_mp4_to_m4a(self) -> None:
        self.assertEqual("m4a", exporter.export_extension("mp4", "playable"))

    def test_export_extension_playable_passthrough(self) -> None:
        self.assertEqual("m4a", exporter.export_extension("m4a", "playable"))
        self.assertEqual("mp3", exporter.export_extension("mp3", "playable"))
        self.assertEqual("flac", exporter.export_extension("flac", "playable"))

    def test_looks_like_mp3_frame(self) -> None:
        self.assertTrue(exporter.looks_like_mp3_frame(b"\xff\xfb"))
        self.assertTrue(exporter.looks_like_mp3_frame(b"\xff\xfa"))
        self.assertTrue(exporter.looks_like_mp3_frame(b"\xff\xf3"))
        self.assertFalse(exporter.looks_like_mp3_frame(b"\xff\x00"))
        self.assertFalse(exporter.looks_like_mp3_frame(b"\x00\xfb"))
        self.assertFalse(exporter.looks_like_mp3_frame(b"\xff"))
        self.assertFalse(exporter.looks_like_mp3_frame(b""))


# ---------------------------------------------------------------------------
# exporter: mp4_child_start, has_encrypted_mp4_sample_entry
# ---------------------------------------------------------------------------

class Mp4ChildStartTests(unittest.TestCase):
    def test_container_boxes(self) -> None:
        self.assertEqual(8, exporter.mp4_child_start("moov", 0, 8))
        self.assertEqual(108, exporter.mp4_child_start("trak", 100, 8))
        self.assertEqual(208, exporter.mp4_child_start("mdia", 200, 8))
        self.assertEqual(8, exporter.mp4_child_start("minf", 0, 8))
        self.assertEqual(8, exporter.mp4_child_start("stbl", 0, 8))
        self.assertEqual(8, exporter.mp4_child_start("sinf", 0, 8))

    def test_stsd_box(self) -> None:
        self.assertEqual(16, exporter.mp4_child_start("stsd", 0, 8))

    def test_meta_box(self) -> None:
        self.assertEqual(12, exporter.mp4_child_start("meta", 0, 8))

    def test_sample_entry_boxes(self) -> None:
        self.assertEqual(36, exporter.mp4_child_start("enca", 0, 8))
        self.assertEqual(36, exporter.mp4_child_start("encv", 0, 8))
        self.assertEqual(36, exporter.mp4_child_start("mp4a", 0, 8))

    def test_unknown_box_returns_none(self) -> None:
        self.assertIsNone(exporter.mp4_child_start("ftyp", 0, 8))
        self.assertIsNone(exporter.mp4_child_start("mdat", 0, 8))


class HasEncryptedMp4SampleEntryTests(unittest.TestCase):
    def test_returns_true_for_enca_sinf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.m4a"
            path.write_bytes(b"\x00" * 100 + b"enca" + b"\x00" * 100 + b"sinf" + b"\x00" * 100)
            self.assertTrue(exporter.has_encrypted_mp4_sample_entry(path))

    def test_returns_true_for_encv_sinf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.mp4"
            path.write_bytes(b"\x00" * 100 + b"encv" + b"\x00" * 100 + b"sinf" + b"\x00" * 100)
            self.assertTrue(exporter.has_encrypted_mp4_sample_entry(path))

    def test_returns_false_without_sinf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.m4a"
            path.write_bytes(b"\x00" * 100 + b"enca" + b"\x00" * 200)
            self.assertFalse(exporter.has_encrypted_mp4_sample_entry(path))

    def test_returns_false_without_encryption_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.m4a"
            path.write_bytes(b"\x00" * 200)
            self.assertFalse(exporter.has_encrypted_mp4_sample_entry(path))


# ---------------------------------------------------------------------------
# exporter: compact_error, infer_image_mime_type, download_cover_image
# ---------------------------------------------------------------------------

class ErrorHandlingTests(unittest.TestCase):
    def test_compact_error_short(self) -> None:
        self.assertEqual("hello world", exporter.compact_error("  hello   world  "))

    def test_compact_error_truncates(self) -> None:
        long = "a" * 500
        result = exporter.compact_error(long, limit=100)
        self.assertEqual(100, len(result))
        self.assertTrue(result.endswith("..."))

    def test_compact_error_exact_limit(self) -> None:
        exact = "a" * 100
        self.assertEqual(exact, exporter.compact_error(exact, limit=100))


class InferImageMimeTypeTests(unittest.TestCase):
    def test_jpeg(self) -> None:
        self.assertEqual("image/jpeg", exporter.infer_image_mime_type(b"\xff\xd8\xff\xe0"))

    def test_png(self) -> None:
        self.assertEqual("image/png", exporter.infer_image_mime_type(b"\x89PNG\r\n\x1a\n"))

    def test_webp(self) -> None:
        self.assertEqual("image/webp", exporter.infer_image_mime_type(b"RIFF\x00\x00\x00\x00WEBP"))

    def test_content_type_fallback(self) -> None:
        self.assertEqual("image/jpeg", exporter.infer_image_mime_type(b"\x00", "image/jpeg"))

    def test_unknown_returns_empty(self) -> None:
        self.assertEqual("", exporter.infer_image_mime_type(b"\x00\x01\x02\x03"))


class DownloadCoverImageTests(unittest.TestCase):
    def test_download_cover_image_returns_empty_on_no_urls(self) -> None:
        image, error = exporter.download_cover_image(())
        self.assertIsNone(image)
        self.assertEqual("", error)

    @patch("export_sodamusic_cache.urllib.request.urlopen")
    def test_download_cover_image_success(self, mock_urlopen) -> None:
        image_data = b"\xff\xd8\xff\xe0" + b"\x00" * 20
        response = type(
            "Response",
            (),
            {
                "headers": {"Content-Type": "image/jpeg"},
                "read": lambda self, n: image_data,
            },
        )()
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = lambda *a: False
        mock_urlopen.return_value.read = lambda n: image_data
        mock_urlopen.return_value.headers = {"Content-Type": "image/jpeg"}

        exporter.COVER_IMAGE_CACHE.clear()
        image, error = exporter.download_cover_image(("https://example.com/cover.jpg",))
        self.assertIsNotNone(image)
        self.assertEqual("", error)


# ---------------------------------------------------------------------------
# exporter: source_candidate, record_indexed_size, candidate_sort_key
# ---------------------------------------------------------------------------

class SourceCandidateTests(unittest.TestCase):
    def test_source_candidate_with_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            source = cache_dir / "cache-1.bin"
            _write_fake_m4a(source)
            candidate = exporter.source_candidate(_record(), cache_dir)
            self.assertIsNotNone(candidate)
            self.assertEqual("cache-1", candidate.cache_uuid)
            self.assertEqual(40, candidate.source_size)

    def test_source_candidate_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = exporter.source_candidate(_record(), Path(tmp))
            self.assertIsNone(candidate)

    def test_source_candidate_empty_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = exporter.source_candidate({}, Path(tmp))
            self.assertIsNone(candidate)

    def test_record_indexed_size_from_video_meta(self) -> None:
        self.assertEqual(40, exporter.record_indexed_size(_record(), {"size": 40}))

    def test_record_indexed_size_fallback_to_record(self) -> None:
        self.assertEqual(50, exporter.record_indexed_size({"size": 50}, {}))

    def test_record_indexed_size_none(self) -> None:
        self.assertIsNone(exporter.record_indexed_size({}, {}))

    def test_candidate_sort_key(self) -> None:
        candidate = exporter.SourceCandidate(
            record={}, track_id="t", title="T", artists="A", album="",
            duration_ms=None, cache_uuid="c", resource_id="r",
            quality="highest", bitrate=260000, extension="m4a",
            codec_type="aac", source_size=40, encrypted=False,
        )
        key = exporter.candidate_sort_key(candidate)
        self.assertEqual((30, 260000, 40), key)

    def test_indexed_candidate_sort_key(self) -> None:
        item = {"video_meta": {"quality": "highest", "codec_type": "aac", "vtype": "m4a", "bitrate": 260000, "size": 40}}
        key = exporter.indexed_candidate_sort_key(item)
        self.assertEqual((30, 260000, 40), key)

    def test_candidate_summary_sort_key(self) -> None:
        summary = {"quality": "highest", "codecType": "aac", "extension": "m4a", "bitrate": 260000, "sourceSize": 40}
        key = exporter.candidate_summary_sort_key(summary)
        self.assertEqual((30, 260000, 40), key)


# ---------------------------------------------------------------------------
# exporter: load_selection_file, selected_records, probe_audio_output
# ---------------------------------------------------------------------------

class SelectionFileTests(unittest.TestCase):
    def test_load_selection_file_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "selection.json"
            path.write_text(json.dumps({"items": [{"cache_uuid": "c1", "format": "playable"}]}))
            items = exporter.load_selection_file(path)
            self.assertEqual([{"cache_uuid": "c1", "format": "playable"}], items)

    def test_load_selection_file_missing_file(self) -> None:
        with self.assertRaises((FileNotFoundError, ValueError)):
            exporter.load_selection_file(Path("/nonexistent/selection.json"))

    def test_load_selection_file_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("not json")
            with self.assertRaises(ValueError):
                exporter.load_selection_file(path)

    def test_selected_records_respects_format(self) -> None:
        records = [exporter.source_candidate(_record(), Path("/nonexistent"))]
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            _write_fake_m4a(cache_dir / "cache-1.bin")
            source_record = {
                "chunkId": "cache-1",
                "resourceId": "resource_F_highest",
                "size": 40,
                "info": {"trackId": "t", "quality": "highest", "spade": "", "mediaDetail": {"video_model": {"video_list": []}}},
                "track": {"type": "track", "track": {"id": "t", "name": "S", "artists": [{"name": "A"}], "album": {"name": ""}, "duration": 100}},
            }
            selected, missing = exporter.selected_records(
                [source_record],
                [{"cache_uuid": "cache-1", "format": "mp3"}],
            )
            self.assertEqual(1, len(selected))
            self.assertEqual("mp3", selected[0][1])


# ---------------------------------------------------------------------------
# exporter: probe_audio_output
# ---------------------------------------------------------------------------

class ProbeAudioOutputTests(unittest.TestCase):
    @patch("export_sodamusic_cache.shutil.which")
    def test_probe_returns_error_when_ffprobe_missing(self, mock_which) -> None:
        mock_which.return_value = None
        result = exporter.probe_audio_output(Path("/nonexistent"))
        self.assertEqual("ffprobe not found", result.error)

    @patch("export_sodamusic_cache.subprocess.run")
    @patch("export_sodamusic_cache.shutil.which")
    def test_probe_parses_json_output(self, mock_which, mock_run) -> None:
        mock_which.return_value = "/usr/bin/ffprobe"
        mock_run.return_value = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps({
                    "streams": [{"codec_name": "aac", "sample_rate": "44100", "bits_per_sample": "16"}],
                    "format": {"format_name": "mp4"},
                }),
                "stderr": "",
            },
        )()
        result = exporter.probe_audio_output(Path("/test.m4a"))
        self.assertEqual("mp4", result.container)
        self.assertEqual("aac", result.codec_type)
        self.assertEqual(44100, result.sample_rate)
        self.assertEqual(16, result.bits_per_sample)
        self.assertEqual("", result.error)

    @patch("export_sodamusic_cache.subprocess.run")
    @patch("export_sodamusic_cache.shutil.which")
    def test_probe_handles_nonzero_exit(self, mock_which, mock_run) -> None:
        mock_which.return_value = "/usr/bin/ffprobe"
        mock_run.return_value = type(
            "Result",
            (),
            {"returncode": 1, "stdout": "", "stderr": "probe error"},
        )()
        result = exporter.probe_audio_output(Path("/test.m4a"))
        self.assertEqual("probe error", result.error)

    @patch("export_sodamusic_cache.subprocess.run")
    @patch("export_sodamusic_cache.shutil.which")
    def test_probe_handles_invalid_json(self, mock_which, mock_run) -> None:
        mock_which.return_value = "/usr/bin/ffprobe"
        mock_run.return_value = type(
            "Result",
            (),
            {"returncode": 0, "stdout": "not-json", "stderr": ""},
        )()
        result = exporter.probe_audio_output(Path("/test.m4a"))
        self.assertIn("invalid JSON", result.error)


# ---------------------------------------------------------------------------
# exporter: can_decode_audio, transcode_to_mp3, transcode_to_flac
# ---------------------------------------------------------------------------

class CanDecodeAudioTests(unittest.TestCase):
    def test_mp3_always_decodable(self) -> None:
        ok, error = exporter.can_decode_audio(Path("/fake.mp3"), "mp3")
        self.assertTrue(ok)
        self.assertEqual("", error)

    @patch("export_sodamusic_cache.shutil.which")
    def test_no_decoder_found(self, mock_which) -> None:
        mock_which.return_value = None
        ok, error = exporter.can_decode_audio(Path("/fake.m4a"), "m4a")
        self.assertFalse(ok)
        self.assertIn("decoder not found", error)

    @patch("export_sodamusic_cache.subprocess.run")
    @patch("export_sodamusic_cache.shutil.which")
    def test_ffmpeg_success(self, mock_which, mock_run) -> None:
        mock_which.side_effect = lambda cmd: "/usr/bin/ffmpeg" if cmd == "ffmpeg" else None
        mock_run.return_value = type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        ok, error = exporter.can_decode_audio(Path("/fake.m4a"), "m4a")
        self.assertTrue(ok)
        self.assertEqual("", error)

    @patch("export_sodamusic_cache.subprocess.run")
    @patch("export_sodamusic_cache.shutil.which")
    def test_ffmpeg_failure(self, mock_which, mock_run) -> None:
        mock_which.side_effect = lambda cmd: "/usr/bin/ffmpeg" if cmd == "ffmpeg" else None
        mock_run.return_value = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "decode error"})()
        ok, error = exporter.can_decode_audio(Path("/fake.m4a"), "m4a")
        self.assertFalse(ok)
        self.assertIn("decode error", error)


class TranscodeToMp3Tests(unittest.TestCase):
    @patch("export_sodamusic_cache.subprocess.run")
    def test_transcode_unsupported_transcoder(self, mock_run) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.m4a"
            source.write_bytes(b"data")
            dest = Path(tmp) / "out.mp3"
            with self.assertRaises(RuntimeError) as ctx:
                exporter.transcode_to_mp3(source, dest, bitrate_kbps=192, transcoder="/usr/bin/unsupported")
            self.assertIn("unsupported transcoder", str(ctx.exception))

    @patch("export_sodamusic_cache.subprocess.run")
    @patch("export_sodamusic_cache.shutil.which")
    def test_transcode_ffmpeg_failure(self, mock_which, mock_run) -> None:
        mock_which.return_value = "/usr/bin/ffmpeg"
        mock_run.return_value = type("Result", (), {"returncode": 1, "stdout": "", "stderr": "transcode error"})()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.m4a"
            source.write_bytes(b"data")
            dest = Path(tmp) / "out.mp3"
            with self.assertRaises(RuntimeError) as ctx:
                exporter.transcode_to_mp3(source, dest, bitrate_kbps=192, transcoder="/usr/bin/ffmpeg")
            self.assertIn("transcode error", str(ctx.exception))


class TranscodeToFlacTests(unittest.TestCase):
    @patch("export_sodamusic_cache.subprocess.run")
    def test_transcode_unsupported_transcoder(self, mock_run) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.m4a"
            source.write_bytes(b"data")
            dest = Path(tmp) / "out.flac"
            with self.assertRaises(RuntimeError) as ctx:
                exporter.transcode_to_flac(source, dest, transcoder="/usr/bin/unsupported")
            self.assertIn("unsupported transcoder", str(ctx.exception))


# ---------------------------------------------------------------------------
# exporter: encode_record helper for parse_entries tests
# ---------------------------------------------------------------------------

class EncodeRecordHelper:
    """Minimal MessagePack encoder for test fixtures."""

    @classmethod
    def encode_record(cls, record: dict) -> bytes:
        keys = list(record.keys())
        values = [record[key] for key in keys]
        body = b"\xd4\x72\x40" + pack(keys)
        for value in values:
            body += pack(value)
        return body


class WriteManifestsTests(unittest.TestCase):
    def test_write_manifests_creates_json_and_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            records = [exporter.ExportRecord(
                source=Path("/src/cache-1.bin"),
                output=Path("/out/song.m4a"),
                track_id="t1",
                title="Song",
                artists="Artist",
                album="Album",
                quality="highest",
                bitrate=260000,
                duration_ms=1234,
                cache_uuid="cache-1",
                resource_id="resource_F_highest",
                extension="m4a",
                source_extension="m4a",
                indexed_extension="m4a",
                indexed_codec_type="aac",
                output_format="playable",
                output_container="mp4",
                output_codec_type="aac",
                output_sample_rate=44100,
                output_bits_per_sample=16,
                output_probe_error="",
                output_matches_request=True,
                output_mismatch_reason="",
                source_size=40,
                indexed_size=40,
                encrypted=False,
                encryption_method="",
                index_key_id="",
                mp4_scheme="",
                mp4_key_id="",
                mp4_has_sample_encryption=False,
                decrypted=False,
                copied=True,
                skipped_reason="",
                cover_embedded=True,
                lyrics_embedded=True,
            )]
            exporter.write_manifests(output_dir, records, dry_run=False)
            self.assertTrue((output_dir / "manifest.json").exists())
            self.assertTrue((output_dir / "manifest.csv").exists())
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(1, len(manifest))
            self.assertEqual("Song", manifest[0]["title"])


# ---------------------------------------------------------------------------
# analyzer: media_label, unique_media_labels, normalized, query_terms
# ---------------------------------------------------------------------------

class AnalyzeMediaLabelTests(unittest.TestCase):
    def test_media_label_full(self) -> None:
        self.assertEqual("highest/aac", analyzer.media_label({"quality": "highest", "codecType": "aac"}))

    def test_media_label_with_extension(self) -> None:
        self.assertEqual("lossless/mp4", analyzer.media_label({"quality": "lossless", "extension": "mp4"}))

    def test_media_label_fallback_to_vtype(self) -> None:
        self.assertEqual("highest/m4a", analyzer.media_label({"quality": "highest", "vtype": "m4a"}))

    def test_media_label_empty(self) -> None:
        self.assertEqual("unknown", analyzer.media_label({}))

    def test_unique_media_labels(self) -> None:
        items = [
            {"quality": "highest", "codecType": "aac"},
            {"quality": "highest", "codecType": "aac"},
            {"quality": "lossless", "extension": "mp4"},
        ]
        self.assertEqual(["highest/aac", "lossless/mp4"], analyzer.unique_media_labels(items))

    def test_normalized(self) -> None:
        self.assertEqual("hello", analyzer.normalized("  Hello  "))
        self.assertEqual("", analyzer.normalized(None))
        self.assertEqual("123", analyzer.normalized(123))

    def test_query_terms(self) -> None:
        self.assertEqual(["hello", "world"], analyzer.query_terms("  Hello  World  "))
        self.assertEqual([], analyzer.query_terms(""))
        self.assertEqual([], analyzer.query_terms("   "))


# ---------------------------------------------------------------------------
# analyzer: track_search_text, field_contains, track_matches_filter
# ---------------------------------------------------------------------------

class AnalyzeFilterTests(unittest.TestCase):
    def test_track_search_text(self) -> None:
        track = {"trackId": "t1", "title": "Song", "artists": "Artist", "album": "Album"}
        self.assertEqual("t1 song artist album", analyzer.track_search_text(track))

    def test_field_contains_match(self) -> None:
        track = {"title": "Hello World"}
        self.assertTrue(analyzer.field_contains(track, "title", "hello"))

    def test_field_contains_empty_needle(self) -> None:
        track = {"title": "Hello"}
        self.assertTrue(analyzer.field_contains(track, "title", ""))

    def test_field_contains_no_match(self) -> None:
        track = {"title": "Hello"}
        self.assertFalse(analyzer.field_contains(track, "title", "xyz"))

    def test_track_matches_filter_query(self) -> None:
        track = {"trackId": "t1", "title": "Song", "artists": "Artist", "album": "Album"}
        self.assertTrue(analyzer.track_matches_filter(track, analyzer.TrackFilter(query="song artist")))
        self.assertFalse(analyzer.track_matches_filter(track, analyzer.TrackFilter(query="missing")))

    def test_track_matches_filter_track_id(self) -> None:
        track = {"trackId": "t1", "title": "Song", "artists": "Artist", "album": "Album"}
        self.assertTrue(analyzer.track_matches_filter(track, analyzer.TrackFilter(track_id="t1")))
        self.assertFalse(analyzer.track_matches_filter(track, analyzer.TrackFilter(track_id="t2")))


# ---------------------------------------------------------------------------
# analyzer: cached_file_matches, cached_version_summaries
# ---------------------------------------------------------------------------

class AnalyzeCachedFileTests(unittest.TestCase):
    def test_cached_file_matches_quality(self) -> None:
        item = {"quality": "highest", "codecType": "aac"}
        self.assertTrue(analyzer.cached_file_matches(item, quality="highest"))
        self.assertFalse(analyzer.cached_file_matches(item, quality="lossless"))

    def test_cached_file_matches_codec(self) -> None:
        item = {"quality": "highest", "codecType": "aac"}
        self.assertTrue(analyzer.cached_file_matches(item, quality="highest", codec="aac"))
        self.assertFalse(analyzer.cached_file_matches(item, quality="highest", codec="flac"))

    def test_cached_file_matches_extension(self) -> None:
        item = {"quality": "highest", "extension": "m4a"}
        self.assertTrue(analyzer.cached_file_matches(item, extension="m4a"))
        self.assertFalse(analyzer.cached_file_matches(item, extension="flac"))

    def test_cached_file_matches_detected_extension(self) -> None:
        item = {"quality": "highest", "detectedExtension": "m4a"}
        self.assertTrue(analyzer.cached_file_matches(item, extension="m4a"))

    def test_cached_version_summaries(self) -> None:
        items = [
            {"quality": "highest", "codecType": "aac", "cacheUuid": "c1", "sourceSize": 40, "encrypted": True},
            {"quality": "lossless", "codecType": "flac", "cacheUuid": "c2", "sourceSize": 80, "encrypted": False},
        ]
        summaries = analyzer.cached_version_summaries(items)
        self.assertEqual(2, len(summaries))
        self.assertEqual("highest/aac", summaries[0]["label"])
        self.assertEqual("c1", summaries[0]["cacheUuid"])
        self.assertTrue(summaries[0]["encrypted"])


# ---------------------------------------------------------------------------
# analyzer: write_csv_report, write_selection_file, write_batch_target_file
# ---------------------------------------------------------------------------

class AnalyzeWriteTests(unittest.TestCase):
    def test_write_csv_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.csv"
            report = {
                "items": [
                    {
                        "trackId": "t1",
                        "title": "Song",
                        "artists": "Artist",
                        "bestIndexed": {"quality": "highest", "codecType": "aac"},
                        "bestIndexedCached": True,
                        "bestCached": {"quality": "highest", "codecType": "aac"},
                        "cachedLabels": ["highest/aac"],
                        "indexedLabels": ["highest/aac"],
                    }
                ]
            }
            analyzer.write_csv_report(path, report)
            self.assertTrue(path.exists())
            with path.open(encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
            self.assertEqual(1, len(rows))
            self.assertEqual("t1", rows[0]["track_id"])

    def test_write_selection_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "selection.json"
            items = [{"cache_uuid": "c1", "format": "playable"}]
            analyzer.write_selection_file(path, items)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(items, payload["items"])

    def test_write_batch_target_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "targets.json"
            items = [{"trackId": "t1", "target": "lossless/flac"}]
            analyzer.write_batch_target_file(path, items)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(items, payload["items"])


# ---------------------------------------------------------------------------
# watcher: cache_size_state, indexed_candidate_matches, describe_tracks
# ---------------------------------------------------------------------------

class WatcherCacheSizeStateTests(unittest.TestCase):
    def test_cache_size_state(self) -> None:
        items = [
            {"cacheUuid": "c1", "sourceSize": 40},
            {"cacheUuid": "c2", "sourceSize": 80},
            {"cacheUuid": "c3"},
        ]
        self.assertEqual({"c1": 40, "c2": 80}, watcher.cache_size_state(items))

    def test_cache_size_state_empty(self) -> None:
        self.assertEqual({}, watcher.cache_size_state([]))


class WatcherIndexedCandidateMatchesTests(unittest.TestCase):
    def test_indexed_candidate_matches(self) -> None:
        item = {"quality": "highest", "codecType": "aac", "extension": "m4a"}
        self.assertTrue(watcher.indexed_candidate_matches(item, quality="highest", codec="aac"))
        self.assertFalse(watcher.indexed_candidate_matches(item, quality="lossless", codec="flac"))

    def test_indexed_candidate_matches_empty_filters(self) -> None:
        item = {"quality": "highest", "codecType": "aac", "extension": "m4a"}
        self.assertTrue(watcher.indexed_candidate_matches(item))


class WatcherDescribeTracksTests(unittest.TestCase):
    def test_describe_tracks_single(self) -> None:
        tracks = [{"trackId": "t1", "title": "Song", "artists": "Artist"}]
        result = watcher.describe_tracks(tracks)
        self.assertIn("Artist - Song", result)

    def test_describe_tracks_multiple(self) -> None:
        tracks = [
            {"trackId": "t1", "title": "Song A", "artists": "Artist A"},
            {"trackId": "t2", "title": "Song B", "artists": "Artist B"},
        ]
        result = watcher.describe_tracks(tracks)
        self.assertIn("Artist A - Song A", result)
        self.assertIn("Artist B - Song B", result)


# ---------------------------------------------------------------------------
# batch_target: normalized_key, item_value, item_bool, item_float, target_from_item
# ---------------------------------------------------------------------------

class BatchNormalizedKeyTests(unittest.TestCase):
    def test_normalized_key(self) -> None:
        self.assertEqual("hello123", batch_target.normalized_key("Hello-123!"))
        self.assertEqual("", batch_target.normalized_key("!!!"))


class BatchItemValueTests(unittest.TestCase):
    def test_item_value_direct(self) -> None:
        self.assertEqual("Song", batch_target.item_value({"query": "Song"}, "query"))

    def test_item_value_alias(self) -> None:
        self.assertEqual("Song", batch_target.item_value({"keyword": "Song"}, "query", "keyword"))

    def test_item_value_missing(self) -> None:
        self.assertEqual("", batch_target.item_value({}, "query"))

    def test_item_bool_true(self) -> None:
        self.assertTrue(batch_target.item_bool({"overwrite": "true"}, "overwrite"))
        self.assertTrue(batch_target.item_bool({"overwrite": "1"}, "overwrite"))
        self.assertTrue(batch_target.item_bool({"overwrite": "yes"}, "overwrite"))

    def test_item_bool_false(self) -> None:
        self.assertFalse(batch_target.item_bool({"overwrite": "false"}, "overwrite"))
        self.assertFalse(batch_target.item_bool({"overwrite": "0"}, "overwrite"))

    def test_item_bool_none(self) -> None:
        self.assertIsNone(batch_target.item_bool({}, "overwrite"))

    def test_item_bool_invalid(self) -> None:
        with self.assertRaises(ValueError):
            batch_target.item_bool({"overwrite": "maybe"}, "overwrite")

    def test_item_float(self) -> None:
        self.assertAlmostEqual(12.5, batch_target.item_float({"timeout": "12.5"}, "timeout"))

    def test_item_float_none(self) -> None:
        self.assertIsNone(batch_target.item_float({}, "timeout"))

    def test_item_float_invalid(self) -> None:
        with self.assertRaises(ValueError):
            batch_target.item_float({"timeout": "abc"}, "timeout")


class BatchTargetFromItemTests(unittest.TestCase):
    def test_target_from_item_basic(self) -> None:
        item = {"query": "Song", "target": "lossless/flac"}
        target = batch_target.target_from_item(item, 1)
        self.assertEqual("Song", target.query)
        self.assertEqual("lossless/flac", target.target)

    def test_target_from_item_with_aliases(self) -> None:
        item = {"keyword": "Song", "version": "highest/aac", "artist": "Artist"}
        target = batch_target.target_from_item(item, 1)
        self.assertEqual("Song", target.query)
        self.assertEqual("highest/aac", target.target)
        self.assertEqual("Artist", target.artist)

    def test_target_from_item_missing_target(self) -> None:
        with self.assertRaises(ValueError):
            batch_target.target_from_item({"query": "Song"}, 1)

    def test_target_from_item_missing_selector(self) -> None:
        with self.assertRaises(ValueError):
            batch_target.target_from_item({"target": "lossless/flac"}, 1)

    def test_target_from_item_with_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            item = {"query": "Song", "target": "lossless/flac", "outputDir": tmp}
            target = batch_target.target_from_item(item, 1)
            self.assertEqual(Path(tmp), target.output_dir)


# ---------------------------------------------------------------------------
# batch_target: target_summary, target_label, preflight_status, target_output_dir
# ---------------------------------------------------------------------------

class BatchTargetSummaryTests(unittest.TestCase):
    def test_target_summary(self) -> None:
        bt = batch_target.BatchTarget(query="Song", track_id="t1", title="T", artist="A", album="Al", target="lossless/flac")
        summary = batch_target.target_summary(bt)
        self.assertEqual("Song", summary["query"])
        self.assertEqual("t1", summary["track_id"])
        self.assertEqual("lossless/flac", summary["target"])

    def test_target_label(self) -> None:
        bt = batch_target.BatchTarget(track_id="t1", query="Song")
        self.assertEqual("t1", batch_target.target_label(bt))

    def test_target_label_fallback(self) -> None:
        bt = batch_target.BatchTarget(query="Song")
        self.assertEqual("Song", batch_target.target_label(bt))

    def test_preflight_status_cached(self) -> None:
        self.assertEqual("cached", batch_target.preflight_status(0, target_cached=True))

    def test_preflight_status_indexed_not_cached(self) -> None:
        self.assertEqual("indexed_not_cached", batch_target.preflight_status(0, target_cached=False))

    def test_preflight_status_no_match(self) -> None:
        self.assertEqual("no_match", batch_target.preflight_status(target_cli.EXIT_NO_MATCH, target_cached=False))

    def test_preflight_status_target_not_indexed(self) -> None:
        self.assertEqual("target_not_indexed", batch_target.preflight_status(target_cli.EXIT_TARGET_NOT_INDEXED, target_cached=False))

    def test_preflight_status_multiple_tracks(self) -> None:
        self.assertEqual("multiple_tracks", batch_target.preflight_status(target_cli.EXIT_MULTIPLE_TRACKS, target_cached=False))

    def test_preflight_status_error(self) -> None:
        self.assertEqual("error", batch_target.preflight_status(99, target_cached=False))

    def test_target_output_dir(self) -> None:
        bt = batch_target.BatchTarget(output_dir=Path("/target"))
        opts = batch_target.BatchOptions(output_dir=Path("/default"))
        self.assertEqual(Path("/target"), batch_target.target_output_dir(bt, opts))

    def test_target_output_dir_fallback(self) -> None:
        bt = batch_target.BatchTarget()
        opts = batch_target.BatchOptions(output_dir=Path("/default"))
        self.assertEqual(Path("/default"), batch_target.target_output_dir(bt, opts))


# ---------------------------------------------------------------------------
# batch_target: exporter_manifest_path, read_export_manifest
# ---------------------------------------------------------------------------

class BatchManifestTests(unittest.TestCase):
    def test_exporter_manifest_path_dry_run(self) -> None:
        self.assertEqual(
            Path("/out/manifest.dry-run.json"),
            batch_target.exporter_manifest_path(Path("/out"), dry_run=True),
        )

    def test_exporter_manifest_path_normal(self) -> None:
        self.assertEqual(
            Path("/out/manifest.json"),
            batch_target.exporter_manifest_path(Path("/out"), dry_run=False),
        )

    def test_read_export_manifest_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = batch_target.read_export_manifest(Path(tmp), dry_run=False)
            self.assertEqual([], result)

    def test_read_export_manifest_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps([{"title": "Song"}]))
            result = batch_target.read_export_manifest(Path(tmp), dry_run=False)
            self.assertEqual([{"title": "Song"}], result)

    def test_read_export_manifest_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps([{"title": "Old"}]))
            os.utime(manifest_path, (100.0, 100.0))
            result = batch_target.read_export_manifest(Path(tmp), dry_run=False, not_before=200.0)
            self.assertEqual([], result)

    def test_read_export_manifest_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text("not json")
            result = batch_target.read_export_manifest(Path(tmp), dry_run=False)
            self.assertEqual([], result)


# ---------------------------------------------------------------------------
# batch_target: track_summary, cache_summary
# ---------------------------------------------------------------------------

class BatchTrackCacheSummaryTests(unittest.TestCase):
    def test_track_summary(self) -> None:
        track = {
            "trackId": "t1",
            "title": "Song",
            "artists": "Artist",
            "album": "Album",
            "indexedCandidates": [{"quality": "highest", "codecType": "aac", "extension": "m4a"}],
            "cachedFiles": [{"quality": "highest", "codecType": "aac", "extension": "m4a"}],
        }
        summary = batch_target.track_summary(track)
        self.assertEqual("t1", summary["trackId"])
        self.assertIn("highest/aac", summary["indexedQualities"])
        self.assertIn("highest/aac", summary["cachedQualities"])

    def test_cache_summary(self) -> None:
        item = {
            "cacheUuid": "c1",
            "resourceId": "r1",
            "quality": "highest",
            "codecType": "aac",
            "detectedExtension": "m4a",
            "sourceSize": 40,
            "indexedSize": 40,
            "encrypted": True,
        }
        summary = batch_target.cache_summary(item)
        self.assertEqual("c1", summary["cacheUuid"])
        self.assertEqual("r1", summary["resourceId"])
        self.assertEqual(40, summary["sourceSize"])
        self.assertTrue(summary["encrypted"])


# ---------------------------------------------------------------------------
# target_sodamusic_cache: indexed_candidate_matches, matching_indexed_candidates
# ---------------------------------------------------------------------------

class TargetIndexedCandidateMatchesTests(unittest.TestCase):
    def test_indexed_candidate_matches(self) -> None:
        item = {"quality": "highest", "codecType": "aac", "extension": "m4a"}
        self.assertTrue(target_cli.indexed_candidate_matches(item, quality="highest", codec="aac"))
        self.assertFalse(target_cli.indexed_candidate_matches(item, quality="lossless", codec="flac"))

    def test_indexed_candidate_matches_empty_filters(self) -> None:
        item = {"quality": "highest", "codecType": "aac", "extension": "m4a"}
        self.assertTrue(target_cli.indexed_candidate_matches(item))


class TargetMatchingIndexedCandidatesTests(unittest.TestCase):
    def test_matching_indexed_candidates(self) -> None:
        tracks = [
            {"indexedCandidates": [
                {"quality": "highest", "codecType": "aac", "extension": "m4a"},
                {"quality": "lossless", "codecType": "flac", "extension": "mp4"},
            ]},
            {"indexedCandidates": [
                {"quality": "highest", "codecType": "aac", "extension": "m4a"},
            ]},
        ]
        matches = target_cli.matching_indexed_candidates(tracks, quality="lossless", codec="flac")
        self.assertEqual(1, len(matches))

    def test_matching_indexed_candidates_empty(self) -> None:
        tracks = [{"indexedCandidates": [{"quality": "highest", "codecType": "aac", "extension": "m4a"}]}]
        matches = target_cli.matching_indexed_candidates(tracks, quality="lossless", codec="flac")
        self.assertEqual(0, len(matches))


# ---------------------------------------------------------------------------
# target_sodamusic_cache: indexed_labels, cached_labels
# ---------------------------------------------------------------------------

class TargetLabelTests(unittest.TestCase):
    def test_indexed_labels(self) -> None:
        tracks = [
            {"indexedCandidates": [
                {"quality": "highest", "codecType": "aac", "extension": "m4a"},
                {"quality": "lossless", "codecType": "flac", "extension": "mp4"},
            ]},
        ]
        labels = target_cli.indexed_labels(tracks)
        self.assertIn("highest/aac", labels)
        self.assertIn("lossless/flac", labels)

    def test_cached_labels(self) -> None:
        tracks = [
            {"cachedFiles": [
                {"quality": "highest", "codecType": "aac", "extension": "m4a"},
            ]},
        ]
        labels = target_cli.cached_labels(tracks)
        self.assertIn("highest/aac", labels)

    def test_cache_match_detail(self) -> None:
        item = {
            "cacheUuid": "c1",
            "resourceId": "r1",
            "quality": "highest",
            "codecType": "aac",
            "sourceSize": 40,
            "indexedSize": 40,
            "encrypted": True,
        }
        detail = target_cli.cache_match_detail(item)
        self.assertIn("c1", detail)
        self.assertIn("40/40", detail)
        self.assertIn("encrypted", detail)


# ---------------------------------------------------------------------------
# runtime_dependencies: compact_command_output, ensure_tool_path
# ---------------------------------------------------------------------------

class RuntimeDepsTests(unittest.TestCase):
    def test_compact_command_output_short(self) -> None:
        result = deps.subprocess.CompletedProcess([], 0, stdout="hello", stderr="")
        self.assertEqual("hello", deps.compact_command_output(result))

    def test_compact_command_output_truncates(self) -> None:
        long = "a" * 1000
        result = deps.subprocess.CompletedProcess([], 0, stdout=long, stderr="")
        output = deps.compact_command_output(result)
        self.assertTrue(len(output) <= 800)
        self.assertTrue(output.endswith("..."))

    def test_ensure_tool_path_adds_homebrew(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool_dir = Path(tmp) / "bin"
            tool_dir.mkdir()
            tool = tool_dir / "demo-tool"
            tool.write_text("#!/bin/sh\n", encoding="utf-8")
            tool.chmod(0o755)
            with patch.dict(deps.os.environ, {"PATH": ""}):
                deps.ensure_tool_path()
                path = deps.os.environ.get("PATH", "")
                self.assertTrue(
                    any(d in path for d in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"])
                )


if __name__ == "__main__":
    unittest.main()
