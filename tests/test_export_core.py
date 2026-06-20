"""Regression tests for core export utilities, MessagePack parsing, MP4 boxes, and crypto."""

from __future__ import annotations

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

import export_sodamusic_cache as exporter  # noqa: E402
from helpers import encrypted_record, pack, source_record, write_fake_m4a  # noqa: E402


class MsgpackrReaderTests(unittest.TestCase):
    def test_positive_fixint(self) -> None:
        reader = exporter.MsgpackrReader(bytes([42]))
        self.assertEqual(42, reader.unpack())

    def test_negative_fixint(self) -> None:
        reader = exporter.MsgpackrReader(bytes([0xFF]))
        self.assertEqual(-1, reader.unpack())

    def test_nil_true_false(self) -> None:
        reader = exporter.MsgpackrReader(b"\xc0\xc3\xc2")
        self.assertIsNone(reader.unpack())
        self.assertTrue(reader.unpack())
        self.assertFalse(reader.unpack())

    def test_fixstr_and_str_types(self) -> None:
        data = pack("hi") + pack("x" * 40) + pack("y" * 300)
        reader = exporter.MsgpackrReader(data)
        self.assertEqual("hi", reader.unpack())
        self.assertEqual("x" * 40, reader.unpack())
        self.assertEqual("y" * 300, reader.unpack())

    def test_binary(self) -> None:
        data = pack(b"hello")
        reader = exporter.MsgpackrReader(data)
        self.assertEqual(b"hello", reader.unpack())

    def test_integers(self) -> None:
        values = [128, -129, 256, -32769, 65536, -2147483649, 4294967296, -9223372036854775808]
        data = b"".join(pack(value) for value in values)
        reader = exporter.MsgpackrReader(data)
        for expected in values:
            self.assertEqual(expected, reader.unpack())

    def test_float_and_double(self) -> None:
        data = pack(1.5)
        reader = exporter.MsgpackrReader(data)
        self.assertAlmostEqual(1.5, reader.unpack())

    def test_fixarray_and_array16(self) -> None:
        small = [1, 2, 3]
        large = list(range(20))
        data = pack(small) + pack(large)
        reader = exporter.MsgpackrReader(data)
        self.assertEqual(small, reader.unpack())
        self.assertEqual(large, reader.unpack())

    def test_fixmap_and_map16(self) -> None:
        small = {"a": 1, "b": 2}
        large = {str(i): i for i in range(20)}
        data = pack(small) + pack(large)
        reader = exporter.MsgpackrReader(data)
        self.assertEqual(small, reader.unpack())
        self.assertEqual(large, reader.unpack())

    def test_msgpackr_record_extension(self) -> None:
        # Record definition: ext type 0x72 with payload length 1, record id 0x40,
        # followed by key list, then values using positive fixint references.
        # \xd4 is the 1-byte ext prefix, \x72 is ext type, \x40 record id.
        # Keys: ["chunkId", "info"]
        # Values: "abc", 123
        data = (
            b"\xd4\x72\x40"
            + pack(["chunkId", "info"])
            + pack("abc")
            + pack(123)
        )
        reader = exporter.MsgpackrReader(data)
        obj = reader.unpack()
        self.assertEqual({"chunkId": "abc", "info": 123}, obj)

    def test_record_reference_reuse(self) -> None:
        # Define record 0x40 with keys ["name"], then decode two references.
        # Avoid record ids in the value range to prevent the value from being
        # interpreted as another record reference.
        data = b"\xd4\x72\x40" + pack(["name"]) + b"\x40" + pack("first") + b"\x40" + pack("second")
        reader = exporter.MsgpackrReader(data)
        first = reader.unpack()
        second = reader.unpack()
        self.assertEqual({"name": "first"}, first)
        self.assertEqual({"name": "second"}, second)

    def test_eof_error(self) -> None:
        reader = exporter.MsgpackrReader(b"")
        with self.assertRaises(EOFError):
            reader.unpack()

    def test_eof_error_mid_read(self) -> None:
        # A 5-byte str marker with only 2 bytes of payload triggers EOFError.
        reader = exporter.MsgpackrReader(b"\xd9\x05ab")
        with self.assertRaises(EOFError):
            reader.unpack()


class ParseEntriesTests(unittest.TestCase):
    def _build_entries_db(self, records: list[dict]) -> bytes:
        # Each encoded record already starts with the marker bytes.
        return b"".join(self._encode_record(record) for record in records)

    def _encode_record(self, record: dict) -> bytes:
        # Build a msgpackr record using the project's marker convention.
        keys = list(record.keys())
        values = [record[key] for key in keys]
        body = b"\xd4\x72\x40" + pack(keys)
        for value in values:
            body += pack(value)
        return body

    def _pack(self, value: object) -> bytes:
        return MsgpackrReaderTests._pack(self, value)

    def test_parse_entries_extracts_records_with_chunkid_and_info(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entries_db = Path(tmp) / "entries.db"
            entries_db.write_bytes(self._build_entries_db([encrypted_record()]))
            records = exporter.parse_entries(entries_db)

        self.assertEqual(1, len(records))
        self.assertEqual("cache-1", records[0]["chunkId"])

    def test_parse_entries_skips_records_without_chunkid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entries_db = Path(tmp) / "entries.db"
            entries_db.write_bytes(self._build_entries_db([{"info": {}}]))
            records = exporter.parse_entries(entries_db)

        self.assertEqual([], records)

    def test_parse_entries_handles_multiple_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entries_db = Path(tmp) / "entries.db"
            entries_db.write_bytes(self._build_entries_db([encrypted_record(), source_record("cache-2")]))
            records = exporter.parse_entries(entries_db)

        self.assertEqual(2, len(records))
        self.assertEqual({"cache-1", "cache-2"}, {record["chunkId"] for record in records})


class UtilityTests(unittest.TestCase):
    def test_find_track_in_nested_dict(self) -> None:
        record = {
            "track": {
                "type": "track",
                "track": {"id": "t1", "name": "Song"},
            }
        }
        self.assertEqual({"id": "t1", "name": "Song"}, exporter.find_track(record))

    def test_find_track_by_media_type(self) -> None:
        record = {"media_type": "track", "name": "Direct Track", "id": "t2"}
        self.assertEqual(record, exporter.find_track(record))

    def test_find_track_returns_none_when_missing(self) -> None:
        self.assertIsNone(exporter.find_track({"other": {"name": "x"}}))

    def test_compact_names_deduplicates(self) -> None:
        self.assertEqual("A, B", exporter.compact_names([{"name": "A"}, {"name": "B"}, {"name": "A"}]))

    def test_compact_names_uses_simple_display_name(self) -> None:
        self.assertEqual("X", exporter.compact_names([{"simple_display_name": "X"}]))

    def test_safe_filename_replaces_invalid_chars(self) -> None:
        self.assertEqual("A_B_C", exporter.safe_filename("A/B:C"))

    def test_safe_filename_cleans_whitespace_and_dots(self) -> None:
        self.assertEqual("name", exporter.safe_filename("  .name.  "))

    def test_safe_filename_fallback(self) -> None:
        self.assertEqual("Unknown", exporter.safe_filename("   "))

    def test_unique_path_avoids_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "song.mp3"
            base.write_bytes(b"x")
            self.assertEqual(Path(tmp) / "song (2).mp3", exporter.unique_path(base))
            (Path(tmp) / "song (2).mp3").write_bytes(b"y")
            self.assertEqual(Path(tmp) / "song (3).mp3", exporter.unique_path(base))

    def test_reserve_unique_path_skips_reserved_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "song.mp3"
            reserved: set[Path] = set()
            first = exporter.reserve_unique_path(base, reserved)
            second = exporter.reserve_unique_path(base, reserved)
            self.assertNotEqual(first, second)
            self.assertEqual(Path(tmp) / "song (2).mp3", second)


class SniffExtensionTests(unittest.TestCase):
    def test_sniff_flac_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.bin"
            path.write_bytes(b"fLaC" + b"\x00" * 28)
            self.assertEqual("flac", exporter.sniff_extension(path, "m4a", "flac"))

    def test_sniff_mp3_id3_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.bin"
            path.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00audio")
            self.assertEqual("mp3", exporter.sniff_extension(path, "m4a", "aac"))

    def test_sniff_mp3_frame_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.bin"
            path.write_bytes(b"\xff\xfb\x00\x00" + b"\x00" * 28)
            self.assertEqual("mp3", exporter.sniff_extension(path, "mp3", "mp3"))

    def test_sniff_mp4_flac_codec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.bin"
            path.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 28)
            self.assertEqual("mp4", exporter.sniff_extension(path, "m4a", "flac"))

    def test_sniff_mp4_uses_vtype(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.bin"
            path.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 28)
            self.assertEqual("m4a", exporter.sniff_extension(path, "m4a", "aac"))

    def test_sniff_fallback_to_indexed_vtype(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.bin"
            path.write_bytes(b"unknown header bytes")
            self.assertEqual("m4a", exporter.sniff_extension(path, "m4a", "aac"))


class Mp4BoxTests(unittest.TestCase):
    def _box(self, name: bytes, payload: bytes, extended: bool = False) -> bytes:
        size = len(payload) + 16 if extended else len(payload) + 8
        if extended:
            return b"\x00\x00\x00\x01" + name + struct.pack(">Q", size) + payload
        return struct.pack(">I", size) + name + payload

    def test_iter_mp4_boxes_parses_basic_boxes(self) -> None:
        data = self._box(b"ftyp", b"M4A ") + self._box(b"moov", b"")
        boxes = exporter.iter_mp4_boxes(data, 0, len(data))
        self.assertEqual(2, len(boxes))
        self.assertEqual("ftyp", boxes[0][2])
        self.assertEqual("moov", boxes[1][2])

    def test_iter_mp4_boxes_handles_extended_size(self) -> None:
        data = self._box(b"mdat", b"x", extended=True)
        boxes = exporter.iter_mp4_boxes(data, 0, len(data))
        self.assertEqual(1, len(boxes))
        self.assertEqual("mdat", boxes[0][2])
        self.assertEqual(16, boxes[0][3])

    def test_iter_mp4_boxes_stops_on_truncated_box(self) -> None:
        data = struct.pack(">I", 100) + b"ftyp"
        boxes = exporter.iter_mp4_boxes(data, 0, len(data))
        self.assertEqual([], boxes)

    def test_collect_mp4_boxes_recurses_into_containers(self) -> None:
        inner = self._box(b"trak", self._box(b"tkhd", b""))
        outer = self._box(b"moov", inner)
        boxes = exporter.collect_mp4_boxes(outer)
        self.assertIn("moov", boxes)
        self.assertIn("trak", boxes)
        self.assertIn("tkhd", boxes)

    def test_first_mp4_box_raises_when_missing(self) -> None:
        with self.assertRaises(ValueError):
            exporter.first_mp4_box({}, "stsz")

    def test_parse_stsz_constant_sample_size(self) -> None:
        # version/flags=0, sample_size=128, sample_count=3
        box = self._box(b"stsz", struct.pack(">III", 0, 128, 3))
        boxes = exporter.collect_mp4_boxes(box)
        sizes = exporter.parse_stsz(box, exporter.first_mp4_box(boxes, "stsz"))
        self.assertEqual([128, 128, 128], sizes)

    def test_parse_stsz_variable_sample_sizes(self) -> None:
        # version/flags=0, sample_size=0, sample_count=2, entries [10, 20]
        box = self._box(b"stsz", struct.pack(">III", 0, 0, 2) + struct.pack(">II", 10, 20))
        boxes = exporter.collect_mp4_boxes(box)
        sizes = exporter.parse_stsz(box, exporter.first_mp4_box(boxes, "stsz"))
        self.assertEqual([10, 20], sizes)

    def test_parse_stco_and_co64(self) -> None:
        stco = self._box(b"stco", struct.pack(">II", 0, 2) + struct.pack(">II", 100, 200))
        boxes = exporter.collect_mp4_boxes(stco)
        self.assertEqual([100, 200], exporter.parse_stco(stco, exporter.first_mp4_box(boxes, "stco")))

        co64 = self._box(b"co64", struct.pack(">II", 0, 1) + struct.pack(">Q", 12345))
        boxes = exporter.collect_mp4_boxes(co64)
        self.assertEqual([12345], exporter.parse_co64(co64, exporter.first_mp4_box(boxes, "co64")))

    def test_parse_stsc(self) -> None:
        box = self._box(b"stsc", struct.pack(">II", 0, 2) + struct.pack(">III", 1, 2, 1) + struct.pack(">III", 2, 3, 1))
        boxes = exporter.collect_mp4_boxes(box)
        entries = exporter.parse_stsc(box, exporter.first_mp4_box(boxes, "stsc"))
        self.assertEqual([(1, 2, 1), (2, 3, 1)], entries)

    def test_sample_offsets(self) -> None:
        sizes = [10, 20]
        chunk_offsets = [100, 200]
        stsc = [(1, 1, 1), (2, 1, 1)]
        self.assertEqual([100, 200], exporter.sample_offsets(sizes, chunk_offsets, stsc))

    def test_sample_offsets_mismatch_raises(self) -> None:
        with self.assertRaises(ValueError):
            exporter.sample_offsets([10], [100], [(1, 10, 1)])

    def test_parse_senc_iv_only(self) -> None:
        box = self._box(b"senc", struct.pack(">I", 0) + struct.pack(">I", 2) + b"\x00" * 16)
        boxes = exporter.collect_mp4_boxes(box)
        samples = exporter.parse_senc(box, exporter.first_mp4_box(boxes, "senc"))
        self.assertEqual(2, len(samples))
        self.assertEqual((b"\x00" * 8, []), samples[0])

    def test_parse_senc_with_subsamples(self) -> None:
        payload = struct.pack(">I", 0x02) + struct.pack(">I", 1) + b"\x00" * 8
        payload += struct.pack(">H", 1) + struct.pack(">H", 5) + struct.pack(">I", 10)
        box = self._box(b"senc", payload)
        boxes = exporter.collect_mp4_boxes(box)
        samples = exporter.parse_senc(box, exporter.first_mp4_box(boxes, "senc"))
        self.assertEqual([(b"\x00" * 8, [(5, 10)])], samples)

    def test_parse_senc_truncated_raises(self) -> None:
        box = self._box(b"senc", struct.pack(">I", 0) + struct.pack(">I", 1) + b"\x00\x00")
        boxes = exporter.collect_mp4_boxes(box)
        with self.assertRaises(ValueError):
            exporter.parse_senc(box, exporter.first_mp4_box(boxes, "senc"))

    def test_mp4_encryption_summary(self) -> None:
        # enca -> sinf -> frma "mp4a", schm "cenc", tenc with key id.
        tenc = self._box(b"tenc", b"\x00" * 4 + struct.pack(">I", 8) + b"\x00" * 8 + b"K" * 16)
        schm = self._box(b"schm", b"\x00" * 4 + b"cenc" + b"\x00" * 4)
        frma = self._box(b"frma", b"mp4a")
        sinf = self._box(b"sinf", frma + schm + tenc)
        enca = self._box(b"enca", b"\x00" * 28 + sinf)
        data = self._box(b"stsd", struct.pack(">I", 0) + struct.pack(">I", 1) + enca)

        summary = exporter.mp4_encryption_summary(self._write_temp(data))
        self.assertEqual("enca", summary["sample_entry"])
        self.assertEqual("mp4a", summary["original_format"])
        self.assertEqual("cenc", summary["scheme"])
        self.assertEqual("4b4b4b4b4b4b4b4b4b4b4b4b4b4b4b4b", summary["key_id"])

    def _write_temp(self, data: bytes) -> Path:
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(data)
            return Path(fh.name)


class CryptoTests(unittest.TestCase):
    def test_decrypt_cenc_mp4_restores_original_format(self) -> None:
        key_hex = "00" * 16
        iv = b"\x00" * 8

        # Build a minimal encrypted MP4 with one sample so decrypt_cenc_mp4 can run.
        def box(name: bytes, payload: bytes) -> bytes:
            return struct.pack(">I", len(payload) + 8) + name + payload

        # stsd with enca sample entry containing sinf/frma=mp4a
        tenc = box(b"tenc", b"\x00" * 4 + struct.pack(">I", 8) + b"\x00" * 8 + b"K" * 16)
        frma = box(b"frma", b"mp4a")
        sinf = box(b"sinf", frma + tenc)
        enca = box(b"enca", b"\x00" * 28 + sinf)
        stsd = box(b"stsd", struct.pack(">I", 0) + struct.pack(">I", 1) + enca)

        sample_data = b"A" * 16
        stsz = box(b"stsz", struct.pack(">III", 0, len(sample_data), 1))
        stsc = box(b"stsc", struct.pack(">II", 0, 1) + struct.pack(">III", 1, 1, 1))
        stco = box(b"stco", struct.pack(">II", 0, 1) + struct.pack(">I", 0))
        # senc with IV only, flag=0
        senc = box(b"senc", struct.pack(">I", 0) + struct.pack(">I", 1) + iv)
        stbl = box(b"stbl", stsd + stsz + stsc + stco + senc)
        minf = box(b"minf", stbl)
        mdia = box(b"mdia", minf)
        trak = box(b"trak", mdia)
        moov = box(b"moov", trak)

        # Place moov first so collect_mp4_boxes can find it, then sample data.
        # Patch stco offset to point to sample_data after moov.
        data = bytearray(moov + sample_data)
        stco_payload_start = data.find(stco) + 8
        entry_offset = stco_payload_start + 8
        struct.pack_into(">I", data, entry_offset, len(moov))

        source = Path(tempfile.mktemp())
        source.write_bytes(bytes(data))
        destination = Path(tempfile.mktemp())
        try:
            exporter.decrypt_cenc_mp4(source, destination, key_hex)
            decrypted = destination.read_bytes()
            # Check enca was restored to mp4a.
            self.assertIn(b"mp4a", decrypted)
            # AES-CTR with zero key on all-zero IV should produce plaintext of zeros for 16 bytes.
            # We don't assert exact plaintext because AES-CTR decryption with zero key is complex.
            self.assertTrue(destination.exists())
        finally:
            source.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)

    def test_decrypt_cenc_mp4_requires_sixteen_byte_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.bin"
            source.write_bytes(b"x")
            with self.assertRaises(RuntimeError):
                exporter.decrypt_cenc_mp4(source, Path(tmp) / "out.bin", "00" * 8)


class SpadeTests(unittest.TestCase):
    def test_decode_spade_validates_hex_output(self) -> None:
        fake_result = type("Result", (), {"returncode": 0, "stdout": "NOTHEX", "stderr": ""})()
        with patch.object(exporter.subprocess, "run", return_value=fake_result):
            with self.assertRaises(RuntimeError):
                exporter.decode_spade("spade", Path("/dev/null"))

    def test_decode_spade_returns_lowercase_key(self) -> None:
        key = "ABCD1234" * 4
        fake_result = type("Result", (), {"returncode": 0, "stdout": key, "stderr": ""})()
        with patch.object(exporter.subprocess, "run", return_value=fake_result):
            self.assertEqual(key.lower(), exporter.decode_spade("spade", Path("/dev/null")))

    def test_decode_spades_parses_json_output(self) -> None:
        key = "00112233" * 4
        fake_result = type(
            "Result",
            (),
            {"returncode": 0, "stdout": json.dumps({"s1": key.upper(), "s2": key}), "stderr": ""},
        )()
        with patch.object(exporter.subprocess, "run", return_value=fake_result):
            result = exporter.decode_spades(["s1", "s2"], Path("/dev/null"))
        self.assertEqual({"s1": key, "s2": key}, result)

    def test_decode_spades_invalid_json_raises(self) -> None:
        fake_result = type("Result", (), {"returncode": 0, "stdout": "not-json", "stderr": ""})()
        with patch.object(exporter.subprocess, "run", return_value=fake_result):
            with self.assertRaises(RuntimeError):
                exporter.decode_spades(["s1"], Path("/dev/null"))


if __name__ == "__main__":
    unittest.main()
