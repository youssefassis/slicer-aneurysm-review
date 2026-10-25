# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""Picking the volume and the detection file out of the directory the reviewer chose."""

from ReformatVizLib import case, review


def build(directory, *names):
    for name in names:
        (directory / name).write_text("{}")
    return directory


class TestFindFiles:
    def test_finds_the_volume_and_the_detections(self, tmp_path):
        build(tmp_path, "angio.nii.gz", "detections.json")
        volume, detections = case.find_files(tmp_path)
        assert volume == tmp_path / "angio.nii.gz"
        assert detections == tmp_path / "detections.json"

    def test_skips_the_review_the_module_wrote(self, tmp_path):
        # The bug this function exists for: the review is written beside the detections and
        # shares their suffix, so a detection file sorting after `review_` was opened against
        # the module's own output and the case was refused.
        build(tmp_path, "angio.nii.gz", "vessels.json", review.file_name("ykerrien"))
        _, detections = case.find_files(tmp_path)
        assert detections == tmp_path / "vessels.json"

    def test_skips_the_review_whatever_the_reviewer_is_called(self, tmp_path):
        build(tmp_path, "angio.nii.gz", "zzz.json", review.file_name("aaa"))
        _, detections = case.find_files(tmp_path)
        assert detections == tmp_path / "zzz.json"

    def test_a_directory_of_reviews_alone_has_no_detections(self, tmp_path):
        build(tmp_path, "angio.nii.gz", review.file_name("a"), review.file_name("b"))
        volume, detections = case.find_files(tmp_path)
        assert volume == tmp_path / "angio.nii.gz"
        assert detections is None

    def test_the_atomic_write_temporary_is_not_mistaken_for_detections(self, tmp_path):
        # jsonio.write_json leaves `<name>.json.tmp` behind if it is interrupted.
        build(tmp_path, "angio.nii.gz", review.file_name("a") + ".tmp")
        assert case.find_files(tmp_path)[1] is None

    def test_takes_the_first_of_several_in_sorted_order(self, tmp_path):
        build(tmp_path, "b.nii.gz", "a.nii.gz", "second.json", "first.json")
        volume, detections = case.find_files(tmp_path)
        assert volume == tmp_path / "a.nii.gz"
        assert detections == tmp_path / "first.json"

    def test_an_empty_directory_finds_nothing(self, tmp_path):
        assert case.find_files(tmp_path) == (None, None)

    def test_a_directory_with_no_volume_still_finds_the_detections(self, tmp_path):
        build(tmp_path, "detections.json")
        volume, detections = case.find_files(tmp_path)
        assert volume is None
        assert detections == tmp_path / "detections.json"

    def test_a_plain_nii_is_not_a_volume(self, tmp_path):
        # The pattern is deliberately the compressed form the pipeline produces.
        build(tmp_path, "angio.nii", "detections.json")
        assert case.find_files(tmp_path)[0] is None

    def test_accepts_a_string_path(self, tmp_path):
        build(tmp_path, "angio.nii.gz", "detections.json")
        assert case.find_files(str(tmp_path))[0] == tmp_path / "angio.nii.gz"

    def test_a_missing_directory_finds_nothing(self, tmp_path):
        # Path.glob on an absent directory yields nothing rather than raising; the reviewer
        # cannot reach this through the dialog, but nothing should explode if they do.
        assert case.find_files(tmp_path / "absent") == (None, None)


class TestDescriptions:
    def test_no_volume_names_the_directory_and_what_was_expected(self, tmp_path):
        message = case.describe_no_volume(tmp_path / "patient_004")
        assert "patient_004" in message
        assert case.VOLUME_PATTERN in message

    def test_no_detections_says_the_volume_did_load(self, tmp_path):
        message = case.describe_no_detections(tmp_path / "patient_004")
        assert "patient_004" in message
        assert "loaded" in message

    def test_no_detections_warns_that_nothing_is_recorded(self, tmp_path):
        # Without a detection file there is no review, so a hand-placed finding is lost on
        # the next load. Saying so is the whole point of warning rather than staying quiet.
        assert "not be saved" in case.describe_no_detections(tmp_path)


class TestIsReviewFile:
    def test_recognises_what_file_name_produces(self):
        assert review.is_review_file(review.file_name("ykerrien"))

    def test_recognises_it_whatever_the_reviewer_is_called(self):
        for reviewer in ("ykerrien", "Y. Kerrien", "", "élise", "a/b"):
            assert review.is_review_file(review.file_name(reviewer))

    def test_a_detection_file_is_not_one(self):
        for name in ("detections.json", "candidates.json", "myreview_x.json"):
            assert not review.is_review_file(name)

    def test_looks_at_the_name_not_the_path(self, tmp_path):
        assert review.is_review_file(tmp_path / "review_a.json")
        assert not review.is_review_file(tmp_path / "review_a" / "detections.json")
