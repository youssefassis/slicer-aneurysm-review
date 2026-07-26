# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""What the reviewer decided about each candidate, and whether the case is finished.

This is deliberately *not* part of the detection record. The detection file is the detector's
output; a triage review has to be able to say "these are the candidates I was shown" long
after the fact, which is impossible if the review overwrites its own input. See
``docs/review-format.md`` for the file this builds.

Two properties are the reason this module exists at all:

*Undecided is a state.* A candidate the reviewer never opened must not read as one they
looked at and rejected — in triage that difference is the difference between a negative case
and an unfinished one.

*A case is signed off explicitly, and only when every candidate has been decided.* Whatever
acts on the review downstream needs to know it is looking at a finished read.

A review is a plain ``dict``, like a detection, so there is one representation and no
conversion layer. Every mutation takes the timestamp to record as ``at`` rather than reading
the clock itself: the caller owns the clock, and the tests can then be exact.
"""

#: Bumped whenever the shape of the review file changes. Readers must check it.
FORMAT_VERSION = 1

#: The reviewer has not decided about this candidate yet. Blocks sign-off.
UNREVIEWED = "unreviewed"
#: It is an aneurysm.
CONFIRMED = "confirmed"
#: It is not an aneurysm; ``reason`` says what it is instead.
REJECTED = "rejected"
#: The reviewer cannot call it either way and wants a second opinion.
UNCERTAIN = "uncertain"
#: The image cannot answer the question — coverage, motion, contrast timing.
NOT_ASSESSABLE = "not_assessable"

DETECTION_STATES = (UNREVIEWED, CONFIRMED, REJECTED, UNCERTAIN, NOT_ASSESSABLE)

#: States that count as a decision. Everything except :data:`UNREVIEWED`.
DECIDED_STATES = tuple(state for state in DETECTION_STATES if state != UNREVIEWED)

#: What a rejected candidate turned out to be. Provisional vocabulary — it is the field that
#: turns "the detector has a 40% false-positive rate" into something actionable, so it wants
#: confirming with the reviewers before real cases are read.
REJECTION_REASONS = ("infundibulum", "vessel_loop", "artifact", "other_pathology",
                     "duplicate", "other")

#: Likert values the review table offers. ``None`` means not scored.
LIKERT_SCORES = (1, 2, 3, 4, 5)

#: No candidate has been decided yet.
RECEIVED = "received"
#: Some candidate has been decided; the case is not finished.
IN_REVIEW = "in_review"
#: Every candidate is decided and the reviewer has signed the case off.
SIGNED_OFF = "signed_off"

CASE_STATES = (RECEIVED, IN_REVIEW, SIGNED_OFF)


class ReviewError(ValueError):
    """A review was asked for something it cannot represent, or is signed off."""


def new(study, source, reviewer, tool, at, keys=()):
    """Return an empty review of the candidates named by ``keys``.

    ``study`` identifies what was reviewed and ``source`` where the candidates came from;
    both are stored as given, so the caller decides how much provenance it can supply. Every
    candidate starts :data:`UNREVIEWED`.
    """
    return {
        "formatVersion": FORMAT_VERSION,
        "study": dict(study),
        "source": dict(source),
        "review": {
            "reviewer": reviewer,
            "tool": tool,
            "startedAt": at,
            "signedOffAt": None,
            "state": RECEIVED,
        },
        "detections": {key: _new_entry() for key in keys},
        "events": [],
    }


def _new_entry(added=False, p1=None, p2=None):
    entry = {
        "state": UNREVIEWED,
        "reason": None,
        "likert": None,
        "note": "",
        "viewAngle": 0,
        "decidedAt": None,
    }
    if added:
        # Kept in the same map as the detector's candidates so that one lookup answers "what
        # was decided about this", and so a reviewer's own finding cannot be dropped by a
        # reader that only knows about detector output.
        entry["addedByReviewer"] = True
        entry["P1"] = list(p1)
        entry["P2"] = list(p2)
    return entry


def entry(review, key):
    """Return the review entry for candidate ``key``, or raise :class:`ReviewError`."""
    try:
        return review["detections"][key]
    except KeyError:
        raise ReviewError(f"the review has no candidate '{key}'") from None


def candidates(review):
    """Return the candidate keys, in insertion order.

    Not called ``keys`` because ``review.keys(r)`` reads like a dict method and is not one.
    """
    return tuple(review["detections"])


def _require_open(review):
    if review["review"]["state"] == SIGNED_OFF:
        raise ReviewError("the case is signed off; reopen it before changing a verdict")


def _record(review, at, what, **details):
    """Append to the event log. The log is append-only: it is the audit trail."""
    review["events"].append({"at": at, "what": what, **details})
    if review["review"]["state"] == RECEIVED:
        review["review"]["state"] = IN_REVIEW


def set_state(review, key, state, at, reason=None, note=None):
    """Record the reviewer's verdict on one candidate.

    ``reason`` belongs to :data:`REJECTED` only, and must be one of
    :data:`REJECTION_REASONS`. Passing ``note`` replaces the note; passing ``None`` leaves it.
    """
    _require_open(review)
    if state not in DETECTION_STATES:
        raise ReviewError(f"'{state}' is not one of {', '.join(DETECTION_STATES)}")
    if reason is not None:
        if state != REJECTED:
            raise ReviewError(f"a reason only applies to a {REJECTED} candidate, not {state}")
        if reason not in REJECTION_REASONS:
            raise ReviewError(f"'{reason}' is not one of {', '.join(REJECTION_REASONS)}")

    target = entry(review, key)
    target["state"] = state
    target["reason"] = reason
    target["decidedAt"] = None if state == UNREVIEWED else at
    if note is not None:
        target["note"] = note
    _record(review, at, "state", key=key, state=state, reason=reason)
    return review


def set_likert(review, key, score, at):
    """Record the reviewer's confidence score, or ``None`` for not scored."""
    _require_open(review)
    if score is not None and score not in LIKERT_SCORES:
        raise ReviewError(f"a Likert score is one of {LIKERT_SCORES} or None, not {score!r}")
    entry(review, key)["likert"] = score
    _record(review, at, "likert", key=key, likert=score)
    return review


def set_note(review, key, note, at):
    """Record free text about one candidate."""
    _require_open(review)
    entry(review, key)["note"] = note
    _record(review, at, "note", key=key)
    return review


def set_view_angle(review, key, angle):
    """Remember the rotation the reviewer left the view at.

    Not an event: this is where the reviewer was looking, not something they decided, and it
    changes on every drag of the slider.
    """
    entry(review, key)["viewAngle"] = angle
    return review


def add_detection(review, p1, p2, at, key=None):
    """Add an aneurysm the detector missed, and return its key.

    It starts :data:`CONFIRMED`: the reviewer placed it precisely because it is one, and
    leaving it undecided would block sign-off on the reviewer's own finding.
    """
    _require_open(review)
    key = key or _unused_key(review)
    if key in review["detections"]:
        raise ReviewError(f"the review already has a candidate '{key}'")
    review["detections"][key] = _new_entry(added=True, p1=p1, p2=p2)
    review["detections"][key]["state"] = CONFIRMED
    review["detections"][key]["decidedAt"] = at
    _record(review, at, "added", key=key)
    return key


def _unused_key(review):
    """Return a key for a reviewer-added candidate that no detector key can collide with."""
    index = 0
    while f"added_{index}" in review["detections"]:
        index += 1
    return f"added_{index}"


def added_keys(review):
    """Return the keys of the candidates the reviewer added, in insertion order."""
    return tuple(key for key, e in review["detections"].items() if e.get("addedByReviewer"))


def undecided_keys(review):
    """Return the keys still :data:`UNREVIEWED` — exactly what blocks sign-off."""
    return tuple(key for key, e in review["detections"].items() if e["state"] == UNREVIEWED)


def counts(review):
    """Return the number of candidates in each state, for the progress the reviewer sees."""
    tally = dict.fromkeys(DETECTION_STATES, 0)
    for e in review["detections"].values():
        tally[e["state"]] += 1
    return tally


def can_sign_off(review):
    """Whether every candidate has been decided and the case is not signed off already."""
    return not undecided_keys(review) and review["review"]["state"] != SIGNED_OFF


def sign_off(review, at):
    """Mark the case finished, or raise :class:`ReviewError` saying what is in the way."""
    if review["review"]["state"] == SIGNED_OFF:
        raise ReviewError("the case is already signed off")
    undecided = undecided_keys(review)
    if undecided:
        raise ReviewError(
            f"{len(undecided)} of {len(review['detections'])} candidates are still "
            f"undecided: {', '.join(undecided)}"
        )
    review["review"]["state"] = SIGNED_OFF
    review["review"]["signedOffAt"] = at
    _record(review, at, "signed_off")
    return review


def reopen(review, at, reason):
    """Reopen a signed-off case so a verdict can be amended.

    Amendment is a recorded act rather than a silent edit: the previous sign-off stays in the
    event log with its timestamp, and this adds the reason it was reopened.
    """
    if review["review"]["state"] != SIGNED_OFF:
        raise ReviewError("the case is not signed off")
    if not reason:
        raise ReviewError("reopening a signed-off case needs a reason")
    review["review"]["state"] = IN_REVIEW
    review["review"]["signedOffAt"] = None
    _record(review, at, "reopened", reason=reason)
    return review


def describe_progress(review):
    """Return the one-line progress the reviewer reads while working through a case."""
    total = len(review["detections"])
    undecided = len(undecided_keys(review))
    state = review["review"]["state"]
    if state == SIGNED_OFF:
        return f"Signed off — {total} candidate{'' if total == 1 else 's'} reviewed"
    return f"{total - undecided} of {total} decided"
