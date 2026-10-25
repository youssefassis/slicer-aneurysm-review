# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""What the review table offers, and how its controls map onto the review record.

Each combo is a tuple of ``(label, value)`` pairs: the label is what the reviewer sees, the
value is what :mod:`ReformatVizLib.review` records. Keeping both here, beside the record they
must agree with, is what lets the tests check that every value the review accepts can be
picked and that nothing pickable is refused.
"""

from ReformatVizLib import review as rev

#: The verdict combo. A combo rather than a checkbox because a checkbox cannot say "not looked
#: at yet", which is the one thing a triage review must be able to say.
VERDICT_CHOICES = (
    ("—", rev.UNREVIEWED),
    ("Yes", rev.CONFIRMED),
    ("No", rev.REJECTED),
    ("Uncertain", rev.UNCERTAIN),
    ("Not assessable", rev.NOT_ASSESSABLE),
)

#: The reason combo, enabled only for a rejected candidate. ``None`` is "no reason given".
REASON_CHOICES = (
    ("—", None),
    ("Infundibulum", "infundibulum"),
    ("Vessel loop", "vessel_loop"),
    ("Artifact", "artifact"),
    ("Other pathology", "other_pathology"),
    ("Duplicate", "duplicate"),
    ("Other", "other"),
)

#: The Likert combo. ``None`` is "not scored"; there is no score of zero.
LIKERT_CHOICES = (("-", None), *((str(score), score) for score in rev.LIKERT_SCORES))


def choice_index(choices, value):
    """Return the combo index whose recorded value is ``value``, or 0 if none is."""
    for index, (_, choice) in enumerate(choices):
        if choice == value:
            return index
    return 0


def reason_enabled(state, editable=True):
    """Whether the reason combo can be used: only on an editable review, for a rejection."""
    return editable and state == rev.REJECTED
