# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""The review table's choices, and their agreement with the review record."""

import pytest
from ReformatVizLib import review as rev
from ReformatVizLib import table


def values(choices):
    return [value for _, value in choices]


class TestTheCombosAgreeWithTheRecord:
    # A value the record accepts but no combo offers cannot be recorded; one a combo offers
    # but the record refuses answers the reviewer with an error dialog.

    def test_every_verdict_state_is_offered_once(self):
        assert sorted(values(table.VERDICT_CHOICES)) == sorted(rev.DETECTION_STATES)

    def test_every_rejection_reason_is_offered_once_after_no_reason(self):
        assert values(table.REASON_CHOICES) == [None, *rev.REJECTION_REASONS]

    def test_every_likert_score_is_offered_once_after_unscored(self):
        assert values(table.LIKERT_CHOICES) == [None, *rev.LIKERT_SCORES]

    @pytest.mark.parametrize("choices", [table.VERDICT_CHOICES, table.REASON_CHOICES, table.LIKERT_CHOICES])
    def test_labels_are_distinct(self, choices):
        labels = [label for label, _ in choices]
        assert len(set(labels)) == len(labels)

    def test_the_first_verdict_is_not_looked_at_yet(self):
        # A new row opens on index 0, so that must be the state a new entry records.
        assert table.VERDICT_CHOICES[0][1] == rev.UNREVIEWED


class TestChoiceIndex:
    def test_finds_the_row_recording_the_value(self):
        assert table.choice_index(table.VERDICT_CHOICES, rev.REJECTED) == 2
        assert table.choice_index(table.REASON_CHOICES, "vessel_loop") == 2

    def test_a_likert_score_lands_on_its_own_row(self):
        for score in rev.LIKERT_SCORES:
            assert table.LIKERT_CHOICES[table.choice_index(table.LIKERT_CHOICES, score)][1] == score

    def test_none_is_the_first_row(self):
        assert table.choice_index(table.REASON_CHOICES, None) == 0
        assert table.choice_index(table.LIKERT_CHOICES, None) == 0

    def test_an_unknown_value_falls_back_to_the_first_row(self):
        assert table.choice_index(table.REASON_CHOICES, "not a reason") == 0


class TestReasonEnabled:
    def test_only_a_rejection_takes_a_reason(self):
        assert table.reason_enabled(rev.REJECTED)
        for state in rev.DETECTION_STATES:
            if state != rev.REJECTED:
                assert not table.reason_enabled(state)

    def test_a_locked_review_takes_no_reason(self):
        assert not table.reason_enabled(rev.REJECTED, editable=False)

    def test_a_row_without_a_verdict_takes_no_reason(self):
        assert not table.reason_enabled(None)
