# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""The JSON reading and writing both file formats rely on: encoding, atomicity, hashing."""

import hashlib
import json

import pytest
from ReformatVizLib import jsonio


class TestReadJson:
    def test_reads_a_utf8_file_whatever_the_platform_locale(self, tmp_path):
        # JSON is UTF-8 by definition, but open() without an encoding follows the locale, so
        # an accented producer field failed to load on a cp1252 Windows machine.
        path = tmp_path / "in.json"
        path.write_bytes(json.dumps({"note": "artère"}, ensure_ascii=False).encode("utf-8"))
        assert jsonio.read_json(path) == {"note": "artère"}

    def test_accepts_a_string_path(self, tmp_path):
        path = tmp_path / "in.json"
        path.write_text("{}")
        assert jsonio.read_json(str(path)) == {}

    def test_malformed_json_raises_a_value_error(self, tmp_path):
        path = tmp_path / "in.json"
        path.write_text("{not json")
        with pytest.raises(ValueError):
            jsonio.read_json(path)

    def test_a_missing_file_raises_an_os_error(self, tmp_path):
        with pytest.raises(OSError):
            jsonio.read_json(tmp_path / "absent.json")


class TestWriteJson:
    def test_round_trips_through_read_json(self, tmp_path):
        path = tmp_path / "out.json"
        jsonio.write_json(path, {"a": [1, 2, 3], "note": "artère"})
        assert jsonio.read_json(path) == {"a": [1, 2, 3], "note": "artère"}

    def test_writes_utf8_regardless_of_locale(self, tmp_path):
        path = tmp_path / "out.json"
        jsonio.write_json(path, {"note": "artère"})
        assert "artère" in path.read_bytes().decode("utf-8")

    def test_a_failed_write_leaves_the_previous_file_intact(self, tmp_path):
        # The whole point of the rename: a review is a record of a clinical decision, and
        # half a file is worse than the previous one.
        path = tmp_path / "out.json"
        jsonio.write_json(path, {"good": True})
        before = path.read_bytes()

        with pytest.raises(TypeError):
            jsonio.write_json(path, {"bad": {1, 2}})  # a set is not JSON

        assert path.read_bytes() == before

    def test_a_failed_write_leaves_no_temporary_file_behind(self, tmp_path):
        path = tmp_path / "out.json"
        jsonio.write_json(path, {"good": True})
        with pytest.raises(TypeError):
            jsonio.write_json(path, {"bad": {1, 2}})
        assert [p.name for p in tmp_path.iterdir()] == ["out.json"]

    def test_an_unwritable_directory_raises(self, tmp_path):
        with pytest.raises(OSError):
            jsonio.write_json(tmp_path / "absent-directory" / "out.json", {})


class TestSha256:
    def test_is_the_digest_of_the_bytes_on_disk(self, tmp_path):
        path = tmp_path / "f.json"
        path.write_bytes(b"some detections")
        assert jsonio.sha256(path) == hashlib.sha256(b"some detections").hexdigest()

    def test_changes_when_the_file_changes(self, tmp_path):
        # This is what lets a review notice its detection file was regenerated underneath it.
        path = tmp_path / "f.json"
        path.write_text("{}")
        before = jsonio.sha256(path)
        path.write_text('{"a": 1}')
        assert jsonio.sha256(path) != before

    def test_reads_a_file_larger_than_one_block(self, tmp_path):
        payload = b"x" * (3 * (1 << 16) + 17)
        path = tmp_path / "big.bin"
        path.write_bytes(payload)
        assert jsonio.sha256(path) == hashlib.sha256(payload).hexdigest()
