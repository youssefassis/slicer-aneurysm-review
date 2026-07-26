# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""The detection file format: what is accepted, what is rejected, and what round-trips."""

import json
import math
from pathlib import Path

import pytest
from ReformatVizLib import detections as det

SAMPLE = Path(__file__).parent / "data" / "detections_sample.json"


def write(tmp_path, content):
    path = tmp_path / "detections.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


class TestAddMissingInfo:
    def test_derives_center_and_radius_from_the_two_points(self):
        d = det.add_missing_info({"P1": [0, 0, 0], "P2": [4, 0, 0]})
        assert d["center"] == [2, 0, 0]
        assert d["radius"] == 2

    def test_radius_is_half_the_separation_in_any_direction(self):
        d = det.add_missing_info({"P1": [1, 2, 3], "P2": [1, 2, 9]})
        assert d["radius"] == 3
        assert d["center"] == [1, 2, 6]

    def test_supplies_a_default_for_every_derived_key(self):
        d = det.add_missing_info({"P1": [0, 0, 0], "P2": [1, 0, 0]})
        assert set(det.DERIVED_KEYS) <= set(d)
        assert d["confidence"] == 1
        assert d["viewAngle"] == 0
        assert d["aneurysm"] is False

    def test_never_overwrites_what_the_producer_supplied(self):
        given = {"P1": [0, 0, 0], "P2": [4, 0, 0], "center": [9, 9, 9],
                 "radius": 99, "confidence": 0.5, "viewAngle": 42, "aneurysm": True}
        assert det.add_missing_info(dict(given)) == given

    def test_coincident_points_give_a_zero_radius_rather_than_failing(self):
        d = det.add_missing_info({"P1": [5, 5, 5], "P2": [5, 5, 5]})
        assert d["radius"] == 0
        assert d["center"] == [5, 5, 5]


class TestLikertScore:
    def test_index_zero_removes_the_key_rather_than_storing_zero(self):
        # An absent key means "not scored"; a stored 0 would be a score of zero.
        assert "Likert" not in det.set_likert_score({"Likert": 3}, det.LIKERT_UNSCORED)

    def test_stores_the_index_as_the_score(self):
        assert det.set_likert_score({}, 4)["Likert"] == 4

    def test_clearing_an_unscored_detection_is_harmless(self):
        assert det.set_likert_score({}, det.LIKERT_UNSCORED) == {}


class TestValidate:
    def test_accepts_the_sample_file(self):
        assert len(det.validate(json.loads(SAMPLE.read_text()))) == 3

    @pytest.mark.parametrize("data, expected", [
        ({}, "no detection"),
        (None, "no detection"),
        ([{"P1": [0, 0, 0], "P2": [1, 0, 0]}], "got list"),
        (42, "got int"),
        ({"a": 5}, "expected an object"),
        ({"a": "oops"}, "expected an object"),
        ({"a": {"P2": [1, 0, 0]}}, "'a' has no P1"),
        ({"a": {"P1": [0, 0, 0]}}, "'a' has no P2"),
        ({"a": {}}, "'a' has no P1 or P2"),
    ])
    def test_rejects_with_a_message_naming_the_problem(self, data, expected):
        with pytest.raises(det.DetectionFormatError, match=expected):
            det.validate(data)

    def test_a_single_bad_entry_rejects_the_whole_file(self):
        # Dropping a candidate silently would hide it from the reviewer entirely.
        data = {"good": {"P1": [0, 0, 0], "P2": [1, 0, 0]}, "bad": {"P1": [0, 0, 0]}}
        with pytest.raises(det.DetectionFormatError, match="'bad'"):
            det.validate(data)


class TestLoad:
    def test_returns_detections_most_confident_first(self, tmp_path):
        detections = det.load(SAMPLE)
        assert [d["confidence"] for d in detections] == [1, 0.91, 0.42]

    def test_every_returned_detection_is_complete(self):
        for d in det.load(SAMPLE):
            assert set(det.REQUIRED_KEYS) | set(det.DERIVED_KEYS) <= set(d)

    def test_preserves_reviewer_state_already_in_the_file(self):
        loaded = det.load(SAMPLE)
        scored = next(d for d in loaded if d["confidence"] == 0.91)
        assert scored["Likert"] == 4
        assert scored["aneurysm"] is True
        assert scored["viewAngle"] == 35

    def test_malformed_json_raises_a_valueerror_like_a_format_error(self, tmp_path):
        # The GUI catches ValueError for both, so json's error must stay in that family.
        path = write(tmp_path, '{"a": {"P1": [0,0,0]')
        with pytest.raises(ValueError):
            det.load(path)

    def test_a_missing_file_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            det.load(tmp_path / "absent.json")

    def test_failure_message_names_the_file_and_the_cause(self, tmp_path):
        path = write(tmp_path, "{}")
        try:
            det.load(path)
        except ValueError as e:
            message = det.describe_load_failure(path, e)
        assert "detections.json" in message
        assert "no detection" in message


class TestSave:
    def test_rekeys_by_table_position_not_by_the_producers_names(self, tmp_path):
        # Documented in docs/detection-format.md: input keys are not preserved.
        out = tmp_path / "out.json"
        det.save(out, det.load(SAMPLE))
        assert list(json.loads(out.read_text())) == ["0", "1", "2"]

    def test_round_trips_without_losing_or_reordering_detections(self, tmp_path):
        original = det.load(SAMPLE)
        out = tmp_path / "out.json"
        det.save(out, original)
        assert det.load(out) == original

    def test_reviewer_verdicts_survive_the_round_trip(self, tmp_path):
        detections = det.load(SAMPLE)
        det.set_likert_score(detections[0], 5)
        detections[0]["aneurysm"] = True
        out = tmp_path / "out.json"
        det.save(out, detections)
        assert det.load(out)[0]["Likert"] == 5
        assert det.load(out)[0]["aneurysm"] is True


class TestTableFormatting:
    def test_confidence_is_shown_as_a_percentage(self):
        assert det.confidence_percent({"confidence": 0.42}) == 42.0

    def test_diameter_is_twice_the_radius(self):
        assert det.diameter({"radius": 1.75}) == 3.5

    def test_long_decimals_are_rounded_for_display(self):
        assert det.confidence_percent({"confidence": 1 / 3}) == 33.333
        assert det.diameter({"radius": math.sqrt(2)}) == 2.828
