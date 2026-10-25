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

from pathlib import Path

from ReformatVizLib import jsonio

#: Bumped whenever the shape of the review file changes. Readers must check it.
FORMAT_VERSION = 1

#: The reviewer has not decided about this candidate yet. Blocks sign-off.
UNREVIEWED = "unreviewed"
#: It is a true finding.
CONFIRMED = "confirmed"
#: It is not one; ``reason`` says what it is instead.
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
    """Add a finding the detector missed, and return its key.

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


def add_candidates(review, keys):
    """Add undecided entries for candidates the review does not know about, and return them.

    A detection file that gained candidates after the review was started — a detector rerun
    over the same case — must not leave them silently unreviewable, and must not quietly
    disappear from a case someone is about to sign off either.
    """
    _require_open(review)
    unknown = tuple(key for key in keys if key not in review["detections"])
    for key in unknown:
        review["detections"][key] = _new_entry()
    return unknown


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


def validate(review):
    """Return ``review`` if it is one this module can work with, else raise.

    Called on everything read from disk. A review that fails this is not partially loaded:
    guessing at a half-understood clinical record is worse than refusing it.
    """
    if not isinstance(review, dict):
        raise ReviewError(f"a review is an object, not {type(review).__name__}")

    version = review.get("formatVersion")
    if version != FORMAT_VERSION:
        raise ReviewError(
            f"review format version {version!r}, but this is ReformatViz {FORMAT_VERSION}"
        )

    for section in ("study", "source", "review", "detections"):
        if not isinstance(review.get(section), dict):
            raise ReviewError(f"the review has no '{section}' object")
    if not isinstance(review.get("events"), list):
        raise ReviewError("the review has no 'events' list")

    header = review["review"]
    if header.get("state") not in CASE_STATES:
        raise ReviewError(f"case state {header.get('state')!r} is not one of {CASE_STATES}")
    if not header.get("reviewer"):
        raise ReviewError("the review does not say who made it")

    for key, e in review["detections"].items():
        _validate_entry(key, e)

    undecided = undecided_keys(review)
    if header["state"] == SIGNED_OFF and undecided:
        raise ReviewError(
            f"the review is signed off but {len(undecided)} candidates are undecided: "
            f"{', '.join(undecided)}"
        )
    return review


def _validate_entry(key, e):
    if not isinstance(e, dict):
        raise ReviewError(f"candidate '{key}' is {type(e).__name__}, expected an object")
    if e.get("state") not in DETECTION_STATES:
        raise ReviewError(f"candidate '{key}' has state {e.get('state')!r}")
    if e.get("reason") is not None and e["reason"] not in REJECTION_REASONS:
        raise ReviewError(f"candidate '{key}' has reason {e['reason']!r}")
    if e.get("likert") is not None and e["likert"] not in LIKERT_SCORES:
        raise ReviewError(f"candidate '{key}' has Likert score {e['likert']!r}")
    if e.get("addedByReviewer") and not (e.get("P1") and e.get("P2")):
        raise ReviewError(f"candidate '{key}' was added by the reviewer but has no positions")


def load(path):
    """Read a review file, or raise :class:`ReviewError` / ``OSError``."""
    return validate(jsonio.read_json(path))


def save(path, review):
    """Write ``review`` to ``path``. Atomic — see :func:`jsonio.write_json`."""
    jsonio.write_json(path, review)


def matches_source(review, digest):
    """Whether ``review`` was made against a detection file with this digest."""
    return review["source"].get("sha256") == digest


def describe_load_failure(path, error):
    """Return the message shown to the reviewer when :func:`load` fails.

    The case is not opened at all in this situation, which the message has to say: silently
    starting a fresh review would overwrite the one that could not be read.
    """
    return ("This case has a review file that cannot be read, so the case has not been "
            f"opened. Nothing has been written to it.\n\n{Path(path).name}: {error}")


def describe_save_failure(path, error):
    """Return the message shown to the reviewer when :func:`save` fails."""
    return ("Saving the review failed — the verdicts recorded so far are still in the module "
            "but are not on disk. The previous review file has been left unchanged."
            f"\n\n{Path(path).name}: {error}")


def describe_source_mismatch(review):
    """Return the warning shown when the detection file changed under an existing review."""
    return ("The detection file has changed since this review was started, so the candidates "
            "on screen may not be the ones that were reviewed. The existing verdicts have "
            f"been loaded unchanged.\n\nReview started {review['review']['startedAt']} by "
            f"{review['review']['reviewer']}.")


#: What every file :func:`file_name` produces starts with. The review is written into the
#: case's own directory, beside the detection file it is about, so whatever picks that
#: detection file has to be able to tell the two apart — see :func:`is_review_file`.
FILE_PREFIX = "review_"


def file_name(reviewer):
    """Return the review file name for ``reviewer``, inside the case's own directory.

    Reviews are per reviewer as well as per case, so a second reviewer cannot silently
    continue someone else's read. There is one reviewer today; the naming costs nothing now
    and is cheaper than discovering the collision with real cases in the directory.
    """
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in reviewer)
    return f"{FILE_PREFIX}{safe or 'unknown'}.json"


def is_review_file(path):
    """Whether ``path`` is a review file this module wrote.

    Reviews land in the same directory as the detections they are about and share their
    ``.json`` suffix, so a case whose detection file sorts after ``review_`` would otherwise
    be opened against the module's own output.
    """
    return Path(path).name.startswith(FILE_PREFIX)
