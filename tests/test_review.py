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
        # it is not an aneurysm" and "I never opened it" were the same saved record.
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
    def test_an_aneurysm_the_detector_missed_starts_confirmed(self):
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
