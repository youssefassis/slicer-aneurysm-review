# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""The detection file format: what is accepted, what is rejected, and what round-trips."""

import json
import math
from pathlib import Path

import numpy as np
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


class TestFromPoints:
    def test_derives_the_rest_of_the_record_from_the_two_points(self):
        d = det.from_points([0, 0, 0], [4, 0, 0])
        assert d["center"] == [2, 0, 0]
        assert d["radius"] == 2
        assert d["viewAngle"] == 0

    def test_marks_the_detection_as_the_reviewer_s_own(self):
        # 'added' is what keeps a hand-placed detection at the end of the review table
        # instead of promoting it above every real candidate on its confidence of 1.
        d = det.from_points([0, 0, 0], [1, 0, 0])
        assert d["added"] == 1
        assert d["aneurysm"] == 1
        assert d["confidence"] == 1
        assert det.sort_for_review([d, {"confidence": 0.1, "P1": [0] * 3, "P2": [1, 0, 0]}])[-1] is d

    def test_positions_survive_a_json_round_trip(self):
        # They arrive as numpy arrays from the markups node, and numpy scalars are not
        # JSON-serialisable: saving the review would fail on the reviewer's own detection.
        d = det.from_points(np.array([1.5, 2.0, 3.0]), np.array([1.5, 2.0, 5.0]))
        assert json.loads(json.dumps(d))["P1"] == [1.5, 2.0, 3.0]

    def test_the_record_is_one_the_module_would_accept_from_a_file(self):
        d = det.from_points([0, 1, 2], [3, 4, 5])
        assert det.validate({"hand-placed": d}) == [d]


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
        # A list happens to contain the strings "P1" and "P2", so the key-presence check alone
        # let this through and it crashed later on `d["P1"]` with a list index TypeError.
        ({"a": ["P1", "P2"]}, "expected an object"),
    ])
    def test_rejects_with_a_message_naming_the_problem(self, data, expected):
        with pytest.raises(det.DetectionFormatError, match=expected):
            det.validate(data)

    @pytest.mark.parametrize("value", [
        "oops",                       # a string, not a position
        [0, 0],                       # too short
        [0, 0, 0, 0],                 # too long
        [0, 0, None],                 # a null coordinate
        [0, 0, "x"],                  # a non-numeric coordinate
        [0, 0, float("nan")],         # json accepts the NaN literal by default
        [0, 0, float("inf")],
        {"x": 0, "y": 0, "z": 0},     # an object instead of an array
        None,
    ])
    def test_rejects_a_malformed_position(self, value):
        # These used to escape validation and surface as a numpy TypeError or a broadcast
        # error, which the GUI does not catch — the reviewer got a console traceback and an
        # empty table instead of the dialog naming the entry.
        for name in ("P1", "P2"):
            data = {"a": {"P1": [0, 0, 0], "P2": [1, 0, 0]}}
            data["a"][name] = value
            with pytest.raises(det.DetectionFormatError, match=f"'a' has a malformed {name}"):
                det.validate(data)

    @pytest.mark.parametrize("name", ["confidence", "radius", "viewAngle"])
    @pytest.mark.parametrize("value", ["high", None, float("nan"), float("inf"), True, [1]])
    def test_rejects_a_non_numeric_number_field(self, name, value):
        data = {"a": {"P1": [0, 0, 0], "P2": [1, 0, 0], name: value}}
        with pytest.raises(det.DetectionFormatError, match=f"'a' has a non-numeric {name}"):
            det.validate(data)

    def test_rejects_a_malformed_center(self):
        data = {"a": {"P1": [0, 0, 0], "P2": [1, 0, 0], "center": [1, 2]}}
        with pytest.raises(det.DetectionFormatError, match="'a' has a malformed center"):
            det.validate(data)

    def test_accepts_the_number_types_a_producer_actually_writes(self):
        data = {"a": {"P1": [0, 0, 0], "P2": [1, 2, 3.5], "confidence": 1,
                      "radius": 2.5, "viewAngle": -180, "aneurysm": True, "Likert": 4}}
        assert len(det.validate(data)) == 1

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

    def test_hand_added_detections_load_after_the_detector_hits(self, tmp_path):
        # An added detection carries confidence 1 because there is no detector score to
        # record, so sorting on confidence alone promoted it above every real candidate on
        # reload — the opposite of where the reviewer left it.
        source = write(tmp_path, {
            "0": {"P1": [0, 0, 0], "P2": [1, 0, 0], "confidence": 0.9},
            "1": {"P1": [0, 0, 0], "P2": [2, 0, 0], "confidence": 1, "added": 1},
            "2": {"P1": [0, 0, 0], "P2": [3, 0, 0], "confidence": 0.4},
        })
        loaded = det.load(source)
        assert [d["confidence"] for d in loaded] == [0.9, 0.4, 1]
        assert loaded[-1].get("added") == 1

    def test_malformed_json_raises_a_valueerror_like_a_format_error(self, tmp_path):
        # The GUI catches ValueError for both, so json's error must stay in that family.
        path = write(tmp_path, '{"a": {"P1": [0,0,0]')
        with pytest.raises(ValueError):
            det.load(path)

    def test_a_missing_file_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            det.load(tmp_path / "absent.json")

    def test_a_file_holding_the_nan_literal_is_rejected(self, tmp_path):
        # json.load accepts the non-standard NaN and Infinity literals, and a NaN axis then
        # slips through aneurysm_frame as a "coincident points" fallback with a NaN origin.
        path = write(tmp_path, '{"a": {"P1": [NaN, 0, 0], "P2": [1, 0, 0]}}')
        with pytest.raises(det.DetectionFormatError, match="malformed P1"):
            det.load(path)

    def test_failure_message_names_the_file_and_the_cause(self, tmp_path):
        path = write(tmp_path, "{}")
        try:
            det.load(path)
        except ValueError as e:
            message = det.describe_load_failure(path, e)
        assert "detections.json" in message
        assert "no detection" in message


class TestSourceKey:
    def test_load_records_the_key_the_producer_filed_each_detection_under(self, tmp_path):
        # The review file is keyed by this, so a review can be joined back to the candidates
        # it was made about. Nothing else in the input identifies a detection.
        path = write(tmp_path, {"cand_7": {"P1": [0, 0, 0], "P2": [2, 0, 0]}})
        assert det.load(path)[0][det.SOURCE_KEY] == "cand_7"

    def test_every_detection_gets_its_own_key(self, tmp_path):
        keys = {d[det.SOURCE_KEY] for d in det.load(SAMPLE)}
        assert len(keys) == len(det.load(SAMPLE))

    def test_a_producer_value_in_that_field_is_overwritten_not_trusted(self, tmp_path):
        # It is derived, and a producer claiming a key that is not its own would break the
        # join silently.
        path = write(tmp_path, {"real": {"P1": [0, 0, 0], "P2": [2, 0, 0],
                                        det.SOURCE_KEY: "something-else"}})
        assert det.load(path)[0][det.SOURCE_KEY] == "real"

    def test_a_malformed_entry_still_reports_its_own_name(self, tmp_path):
        # The key is attached after validation, so a non-object entry gets the dialog naming
        # it rather than a TypeError from the assignment.
        path = write(tmp_path, {"broken": 5})
        with pytest.raises(det.DetectionFormatError, match="broken"):
            det.load(path)


class TestTableFormatting:
    def test_confidence_is_shown_as_a_percentage(self):
        assert det.confidence_percent({"confidence": 0.42}) == 42.0

    def test_diameter_is_twice_the_radius(self):
        assert det.diameter({"radius": 1.75}) == 3.5

    def test_long_decimals_are_rounded_for_display(self):
        assert det.confidence_percent({"confidence": 1 / 3}) == 33.333
        assert det.diameter({"radius": math.sqrt(2)}) == 2.828
