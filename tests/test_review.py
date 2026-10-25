# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""The review record: verdicts, the undecided state, and the sign-off rule."""

import pytest
from ReformatVizLib import review

# The caller owns the clock, so the tests can be exact about what was recorded when.
START = "2026-07-26T09:00:00+00:00"
LATER = "2026-07-26T09:05:00+00:00"
LATEST = "2026-07-26T09:30:00+00:00"

STUDY = {"directory": "patient-004"}
SOURCE = {"detectionFile": "detections.json", "sha256": "abc123"}


def blank(*keys):
    return review.new(STUDY, SOURCE, reviewer="ykerrien", tool="ReformatViz 0.1.0",
                      at=START, keys=keys or ("c0", "c1", "c2"))


class TestNew:
    def test_every_candidate_starts_undecided(self):
        r = blank()
        assert review.undecided_keys(r) == ("c0", "c1", "c2")
        assert all(e["state"] == review.UNREVIEWED for e in r["detections"].values())

    def test_the_case_starts_received_with_nothing_signed_off(self):
        r = blank()
        assert r["review"]["state"] == review.RECEIVED
        assert r["review"]["signedOffAt"] is None
        assert r["events"] == []

    def test_carries_who_reviewed_with_what_and_from_when(self):
        # Provenance is the reason the file exists; none of it can be reconstructed later.
        r = blank()
        assert r["review"]["reviewer"] == "ykerrien"
        assert r["review"]["tool"] == "ReformatViz 0.1.0"
        assert r["review"]["startedAt"] == START
        assert r["source"]["sha256"] == "abc123"
        assert r["formatVersion"] == review.FORMAT_VERSION

    def test_keeps_the_candidate_order_it_was_given(self):
        assert review.candidates(blank("z", "a", "m")) == ("z", "a", "m")

    def test_copies_the_provenance_it_was_handed(self):
        study = {"directory": "patient-004"}
        r = review.new(study, SOURCE, "r", "t", START)
        study["directory"] = "somewhere-else"
        assert r["study"]["directory"] == "patient-004"


class TestUndecidedIsAState:
    def test_a_rejected_candidate_is_not_the_same_as_an_unopened_one(self):
        # The defect this module exists to fix: with a plain boolean verdict, "I looked and
        # it is not one" and "I never opened it" were the same saved record.
        r = blank()
        review.set_state(r, "c0", review.REJECTED, LATER, reason="infundibulum")
        assert review.entry(r, "c0")["state"] == review.REJECTED
        assert review.entry(r, "c1")["state"] == review.UNREVIEWED
        assert review.undecided_keys(r) == ("c1", "c2")

    def test_a_decided_candidate_records_when_it_was_decided(self):
        r = blank()
        review.set_state(r, "c0", review.CONFIRMED, LATER)
        assert review.entry(r, "c0")["decidedAt"] == LATER
        assert review.entry(r, "c1")["decidedAt"] is None

    def test_taking_a_verdict_back_makes_the_candidate_undecided_again(self):
        r = blank()
        review.set_state(r, "c0", review.CONFIRMED, LATER)
        review.set_state(r, "c0", review.UNREVIEWED, LATEST)
        assert review.entry(r, "c0")["decidedAt"] is None
        assert "c0" in review.undecided_keys(r)

    def test_counts_report_every_state(self):
        r = blank()
        review.set_state(r, "c0", review.CONFIRMED, LATER)
        review.set_state(r, "c1", review.UNCERTAIN, LATER)
        assert review.counts(r) == {review.UNREVIEWED: 1, review.CONFIRMED: 1,
                                    review.REJECTED: 0, review.UNCERTAIN: 1,
                                    review.NOT_ASSESSABLE: 0}


class TestSetState:
    def test_rejects_a_state_it_does_not_know(self):
        r = blank()
        with pytest.raises(review.ReviewError, match="not one of"):
            review.set_state(r, "c0", "probably", LATER)

    def test_leaves_the_candidate_untouched_when_it_refuses(self):
        r = blank()
        with pytest.raises(review.ReviewError):
            review.set_state(r, "c0", "probably", LATER)
        assert review.entry(r, "c0")["state"] == review.UNREVIEWED

    def test_a_reason_needs_to_be_in_the_vocabulary(self):
        r = blank()
        with pytest.raises(review.ReviewError, match="vessel_loop"):
            review.set_state(r, "c0", review.REJECTED, LATER, reason="looked odd")

    def test_a_reason_only_applies_to_a_rejection(self):
        # Otherwise "confirmed because artifact" is a representable, meaningless record.
        r = blank()
        with pytest.raises(review.ReviewError, match="only applies"):
            review.set_state(r, "c0", review.CONFIRMED, LATER, reason="artifact")

    def test_changing_the_verdict_clears_a_reason_that_no_longer_applies(self):
        r = blank()
        review.set_state(r, "c0", review.REJECTED, LATER, reason="artifact")
        review.set_state(r, "c0", review.CONFIRMED, LATEST)
        assert review.entry(r, "c0")["reason"] is None

    def test_a_note_can_be_recorded_with_the_verdict_and_survives_a_later_one(self):
        r = blank()
        review.set_state(r, "c0", review.REJECTED, LATER, reason="other", note="tortuous ICA")
        review.set_state(r, "c0", review.CONFIRMED, LATEST)
        assert review.entry(r, "c0")["note"] == "tortuous ICA"

    def test_an_unknown_candidate_is_refused_by_name(self):
        with pytest.raises(review.ReviewError, match="c9"):
            review.set_state(blank(), "c9", review.CONFIRMED, LATER)


class TestLikertAndNote:
    def test_records_a_score_in_range(self):
        r = blank()
        review.set_likert(r, "c0", 4, LATER)
        assert review.entry(r, "c0")["likert"] == 4

    def test_none_means_not_scored(self):
        r = blank()
        review.set_likert(r, "c0", 4, LATER)
        review.set_likert(r, "c0", None, LATEST)
        assert review.entry(r, "c0")["likert"] is None

    @pytest.mark.parametrize("score", [0, 6, -1, "3", 2.5])
    def test_refuses_a_score_outside_the_scale(self, score):
        with pytest.raises(review.ReviewError):
            review.set_likert(blank(), "c0", score, LATER)

    def test_a_note_can_be_set_on_its_own(self):
        r = blank()
        review.set_note(r, "c0", "check the coronal", LATER)
        assert review.entry(r, "c0")["note"] == "check the coronal"


class TestViewAngle:
    def test_remembers_where_the_reviewer_left_the_view(self):
        r = blank()
        review.set_view_angle(r, "c0", 35)
        assert review.entry(r, "c0")["viewAngle"] == 35

    def test_is_not_an_event(self):
        # It changes on every drag of the slider; the audit trail is for decisions.
        r = blank()
        review.set_view_angle(r, "c0", 35)
        assert r["events"] == []

    def test_does_not_count_as_deciding_the_candidate(self):
        r = blank()
        review.set_view_angle(r, "c0", 35)
        assert "c0" in review.undecided_keys(r)


class TestAddDetection:
    def test_a_finding_the_detector_missed_starts_confirmed(self):
        # The reviewer placed it because it is one; leaving it undecided would block sign-off
        # on their own finding.
        r = blank()
        key = review.add_detection(r, [1, 2, 3], [1, 2, 9], LATER)
        assert review.entry(r, key)["state"] == review.CONFIRMED
        assert review.entry(r, key)["decidedAt"] == LATER
        assert key not in review.undecided_keys(r)

    def test_carries_the_positions_so_it_can_be_drawn_again(self):
        r = blank()
        key = review.add_detection(r, [1, 2, 3], [1, 2, 9], LATER)
        assert review.entry(r, key)["P1"] == [1, 2, 3]
        assert review.entry(r, key)["P2"] == [1, 2, 9]
        assert review.entry(r, key)["addedByReviewer"] is True

    def test_is_told_apart_from_the_detector_s_candidates(self):
        r = blank()
        key = review.add_detection(r, [0, 0, 0], [0, 0, 2], LATER)
        assert review.added_keys(r) == (key,)

    def test_does_not_collide_with_a_detector_key_of_the_same_shape(self):
        r = blank("added_0", "c1")
        key = review.add_detection(r, [0, 0, 0], [0, 0, 2], LATER)
        assert key == "added_1"
        assert review.entry(r, "added_0")["state"] == review.UNREVIEWED

    def test_a_second_addition_gets_its_own_key(self):
        r = blank()
        first = review.add_detection(r, [0, 0, 0], [0, 0, 2], LATER)
        second = review.add_detection(r, [5, 0, 0], [5, 0, 2], LATEST)
        assert first != second
        assert review.added_keys(r) == (first, second)

    def test_refuses_a_key_that_is_already_taken(self):
        r = blank()
        with pytest.raises(review.ReviewError, match="already has"):
            review.add_detection(r, [0, 0, 0], [0, 0, 2], LATER, key="c1")


class TestAddCandidates:
    def test_a_candidate_the_review_has_not_seen_is_added_undecided(self):
        # A detector rerun over the same case must not leave new candidates unreviewable.
        r = blank("c0")
        assert review.add_candidates(r, ["c0", "c1"]) == ("c1",)
        assert review.entry(r, "c1")["state"] == review.UNREVIEWED

    def test_leaves_the_verdicts_it_already_has_alone(self):
        r = blank("c0")
        review.set_state(r, "c0", review.CONFIRMED, LATER)
        review.add_candidates(r, ["c0", "c1"])
        assert review.entry(r, "c0")["state"] == review.CONFIRMED

    def test_a_new_candidate_reopens_the_question_of_completeness(self):
        r = blank("c0")
        review.set_state(r, "c0", review.CONFIRMED, LATER)
        assert review.can_sign_off(r)
        review.add_candidates(r, ["c0", "c1"])
        assert not review.can_sign_off(r)

    def test_a_signed_off_case_refuses_new_candidates(self):
        # Its verdicts are a finished record; the case has to be reopened deliberately.
        r = blank("c0")
        review.set_state(r, "c0", review.CONFIRMED, LATER)
        review.sign_off(r, LATEST)
        with pytest.raises(review.ReviewError, match="signed off"):
            review.add_candidates(r, ["c0", "c1"])


class TestSignOff:
    def decided(self):
        r = blank()
        review.set_state(r, "c0", review.CONFIRMED, LATER)
        review.set_state(r, "c1", review.REJECTED, LATER, reason="infundibulum")
        review.set_state(r, "c2", review.NOT_ASSESSABLE, LATER)
        return r

    def test_refuses_while_a_candidate_is_undecided(self):
        r = blank()
        review.set_state(r, "c0", review.CONFIRMED, LATER)
        assert not review.can_sign_off(r)
        with pytest.raises(review.ReviewError, match="c1, c2"):
            review.sign_off(r, LATEST)
        assert r["review"]["state"] != review.SIGNED_OFF

    def test_every_decided_state_counts_as_a_decision(self):
        # Uncertain and not-assessable are decisions: they must not hold a case open.
        r = self.decided()
        assert review.can_sign_off(r)
        review.sign_off(r, LATEST)
        assert r["review"]["state"] == review.SIGNED_OFF
        assert r["review"]["signedOffAt"] == LATEST

    def test_an_empty_case_can_be_signed_off(self):
        # A study the detector found nothing in is still a study someone has to sign off.
        r = review.new(STUDY, SOURCE, "r", "t", START, keys=())
        assert review.can_sign_off(r)
        review.sign_off(r, LATEST)
        assert r["review"]["state"] == review.SIGNED_OFF

    def test_signing_off_twice_is_refused(self):
        r = self.decided()
        review.sign_off(r, LATEST)
        with pytest.raises(review.ReviewError, match="already"):
            review.sign_off(r, LATEST)

    def test_a_signed_off_case_refuses_changes(self):
        r = self.decided()
        review.sign_off(r, LATEST)
        for change in (lambda: review.set_state(r, "c0", review.REJECTED, LATEST, reason="other"),
                       lambda: review.set_likert(r, "c0", 2, LATEST),
                       lambda: review.set_note(r, "c0", "x", LATEST),
                       lambda: review.add_detection(r, [0, 0, 0], [0, 0, 1], LATEST)):
            with pytest.raises(review.ReviewError, match="signed off"):
                change()

    def test_reopening_needs_a_reason_and_is_recorded(self):
        r = self.decided()
        review.sign_off(r, LATEST)
        with pytest.raises(review.ReviewError, match="reason"):
            review.reopen(r, LATEST, reason="")
        review.reopen(r, LATEST, reason="second read requested")
        assert r["review"]["state"] == review.IN_REVIEW
        assert r["review"]["signedOffAt"] is None
        assert r["events"][-1] == {"at": LATEST, "what": "reopened",
                                   "reason": "second read requested"}

    def test_the_earlier_sign_off_stays_in_the_log_after_reopening(self):
        # An amendment must not erase the fact that the case was once signed off.
        r = self.decided()
        review.sign_off(r, LATEST)
        review.reopen(r, LATEST, reason="second read requested")
        assert [e["what"] for e in r["events"]].count("signed_off") == 1

    def test_a_reopened_case_accepts_verdicts_again(self):
        r = self.decided()
        review.sign_off(r, LATEST)
        review.reopen(r, LATEST, reason="second read requested")
        review.set_state(r, "c0", review.REJECTED, LATEST, reason="duplicate")
        assert review.entry(r, "c0")["state"] == review.REJECTED

    def test_reopening_a_case_that_is_not_signed_off_is_refused(self):
        with pytest.raises(review.ReviewError, match="not signed off"):
            review.reopen(blank(), LATEST, reason="why not")


class TestEvents:
    def test_the_first_decision_moves_the_case_into_review(self):
        r = blank()
        review.set_state(r, "c0", review.CONFIRMED, LATER)
        assert r["review"]["state"] == review.IN_REVIEW

    def test_every_decision_is_appended_in_order_with_its_timestamp(self):
        r = blank()
        review.set_state(r, "c0", review.CONFIRMED, LATER)
        review.set_likert(r, "c0", 5, LATEST)
        assert [(e["at"], e["what"]) for e in r["events"]] == [
            (LATER, "state"), (LATEST, "likert")]

    def test_a_verdict_that_was_changed_leaves_both_decisions_in_the_log(self):
        # The audit trail is what the verdict was and when, not only what it ended up as.
        r = blank()
        review.set_state(r, "c0", review.CONFIRMED, LATER)
        review.set_state(r, "c0", review.REJECTED, LATEST, reason="artifact")
        assert [e["state"] for e in r["events"]] == [review.CONFIRMED, review.REJECTED]


class TestFile:
    def signed(self, tmp_path):
        r = blank("c0", "c1")
        review.set_state(r, "c0", review.CONFIRMED, LATER, note="left MCA bifurcation")
        review.set_likert(r, "c0", 5, LATER)
        review.set_state(r, "c1", review.REJECTED, LATER, reason="infundibulum")
        review.add_detection(r, [1, 2, 3], [1, 2, 9], LATER)
        review.sign_off(r, LATEST)
        return r

    def test_round_trips_through_a_file(self, tmp_path):
        r = self.signed(tmp_path)
        path = tmp_path / review.file_name("ykerrien")
        review.save(path, r)
        assert review.load(path) == r

    def test_the_file_is_named_after_the_reviewer(self):
        assert review.file_name("ykerrien") == "review_ykerrien.json"

    @pytest.mark.parametrize("reviewer, expected", [
        ("Dr O'Neill", "review_Dr_O_Neill.json"),
        ("domain\\user", "review_domain_user.json"),
        ("../escape", "review_.._escape.json"),
        ("", "review_unknown.json"),
    ])
    def test_a_reviewer_name_cannot_reach_outside_the_case_directory(self, reviewer, expected):
        # The name comes from the OS or a setting, so it is not necessarily a safe file name.
        assert review.file_name(reviewer) == expected
        assert "/" not in review.file_name(reviewer)

    def test_a_review_from_a_future_format_is_refused_rather_than_guessed_at(self, tmp_path):
        r = blank("c0")
        r["formatVersion"] = review.FORMAT_VERSION + 1
        path = tmp_path / "review.json"
        review.save(path, r)
        with pytest.raises(review.ReviewError, match="format version"):
            review.load(path)

    @pytest.mark.parametrize("section", ["study", "source", "review", "detections", "events"])
    def test_a_review_missing_a_section_is_refused(self, tmp_path, section):
        r = blank("c0")
        del r[section]
        path = tmp_path / "review.json"
        review.save(path, r)
        with pytest.raises(review.ReviewError, match=section):
            review.load(path)

    def test_a_review_that_does_not_say_who_made_it_is_refused(self, tmp_path):
        r = blank("c0")
        r["review"]["reviewer"] = ""
        path = tmp_path / "review.json"
        review.save(path, r)
        with pytest.raises(review.ReviewError, match="who made it"):
            review.load(path)

    @pytest.mark.parametrize("field, value", [
        ("state", "probably"),
        ("reason", "looked odd"),
        ("likert", 9),
    ])
    def test_a_candidate_with_a_value_outside_the_vocabulary_is_refused(self, tmp_path,
                                                                       field, value):
        r = blank("c0")
        r["detections"]["c0"][field] = value
        path = tmp_path / "review.json"
        review.save(path, r)
        with pytest.raises(review.ReviewError, match="c0"):
            review.load(path)

    def test_a_reviewer_added_candidate_without_positions_is_refused(self, tmp_path):
        # It cannot be drawn again, so the review would silently lose a finding.
        r = blank("c0")
        review.add_detection(r, [0, 0, 0], [0, 0, 2], LATER, key="added_0")
        del r["detections"]["added_0"]["P1"]
        path = tmp_path / "review.json"
        review.save(path, r)
        with pytest.raises(review.ReviewError, match="positions"):
            review.load(path)

    def test_a_signed_off_review_with_undecided_candidates_is_refused(self, tmp_path):
        # The sign-off rule has to hold for a file someone edited by hand too, or the
        # guarantee downstream relies on is only enforced while the module happens to be open.
        r = blank("c0", "c1")
        review.set_state(r, "c0", review.CONFIRMED, LATER)
        r["review"]["state"] = review.SIGNED_OFF
        path = tmp_path / "review.json"
        review.save(path, r)
        with pytest.raises(review.ReviewError, match="undecided"):
            review.load(path)

    def test_a_failed_save_leaves_the_previous_review_intact(self, tmp_path):
        path = tmp_path / "review.json"
        review.save(path, blank("c0"))
        before = path.read_bytes()
        with pytest.raises(TypeError):
            review.save(path, {"formatVersion": {1, 2}})
        assert path.read_bytes() == before

    def test_notes_are_stored_as_text_a_human_can_read(self, tmp_path):
        r = blank("c0")
        review.set_note(r, "c0", "artère communicante antérieure", LATER)
        path = tmp_path / "review.json"
        review.save(path, r)
        assert "artère communicante antérieure" in path.read_text(encoding="utf-8")

    def test_recognises_the_detection_file_it_was_made_against(self):
        r = blank("c0")
        assert review.matches_source(r, "abc123")
        assert not review.matches_source(r, "def456")

    def test_the_failure_messages_name_the_file(self, tmp_path):
        path = tmp_path / "review_ykerrien.json"
        assert "review_ykerrien.json" in review.describe_load_failure(path, "boom")
        assert "review_ykerrien.json" in review.describe_save_failure(path, "boom")
        assert "not been opened" in review.describe_load_failure(path, "boom")
        assert "left unchanged" in review.describe_save_failure(path, "boom")

    def test_the_mismatch_warning_says_who_reviewed_and_when(self, tmp_path):
        message = review.describe_source_mismatch(blank("c0"))
        assert "ykerrien" in message
        assert START in message


class TestProgress:
    def test_reports_how_many_are_decided(self):
        r = blank()
        review.set_state(r, "c0", review.CONFIRMED, LATER)
        assert review.describe_progress(r) == "1 of 3 decided"

    def test_says_so_once_the_case_is_signed_off(self):
        r = blank("c0")
        review.set_state(r, "c0", review.CONFIRMED, LATER)
        review.sign_off(r, LATEST)
        assert review.describe_progress(r) == "Signed off — 1 candidate reviewed"
