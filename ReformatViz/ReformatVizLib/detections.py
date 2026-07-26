# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""The detection record: its schema, its validation, and how it is read.

A detection is a plain ``dict``, so there is one representation and no conversion layer to
keep in step.

This module only ever **reads**. The file is the detector's output and the module does not
write to it; what the reviewer decides goes to the review file instead, which is the only way
a review can still say which candidates it was made about. See ``ReformatVizLib/review.py``.

The file format is the contract with whatever produces the detections; it is documented in
``docs/detection-format.md``. Changing it is a breaking change for those producers.
"""

import math
from pathlib import Path

import numpy as np

from ReformatVizLib import jsonio

#: Positions every detection must carry: the ostium and the dome, in RAS millimetres.
REQUIRED_KEYS = ("P1", "P2")

#: Keys derived from P1/P2 when a producer omits them.
DERIVED_KEYS = ("confidence", "center", "radius", "viewAngle", "aneurysm")

#: Keys holding an ``[x, y, z]`` position, checked whenever they are present.
POSITION_KEYS = (*REQUIRED_KEYS, "center")

#: Keys holding a plain number, checked whenever they are present.
NUMBER_KEYS = ("confidence", "radius", "viewAngle")

#: The Likert combo's first row, meaning "not scored". The review records ``None`` for it;
#: there is no score of zero.
LIKERT_UNSCORED = 0

#: Where :func:`load` records the key the producer filed a detection under. Derived, so any
#: value a producer puts there is overwritten. The review file is keyed by it, which is what
#: lets a review be joined back to the candidates it was made about.
SOURCE_KEY = "sourceKey"


class DetectionFormatError(ValueError):
    """A detection file could not be understood. The message names the offending entry."""


def add_missing_info(d):
    """Fill in every key derivable from ``P1``/``P2``, in place, and return ``d``.

    After this, the rest of the module may assume all of :data:`DERIVED_KEYS` exist.
    """
    P1 = np.array(d["P1"])
    P2 = np.array(d["P2"])

    if "confidence" not in d:
        d["confidence"] = 1
    if "center" not in d:
        d["center"] = ((P1 + P2) / 2).tolist()
    if "radius" not in d:
        d["radius"] = np.linalg.norm(P1 - P2) / 2
    if "viewAngle" not in d:
        d["viewAngle"] = 0
    if "aneurysm" not in d:
        d["aneurysm"] = False

    return d


def from_points(p1, p2):
    """Return a complete detection for an ostium and a dome the reviewer placed by hand.

    ``added`` marks it as the reviewer's own: it keeps the detection at the end of the review
    table whatever its confidence (see :func:`sort_for_review`) and is written back out with
    the file. The positions are converted to plain lists, because they arrive as numpy arrays
    from the markups node and have to survive :func:`save`.
    """
    return add_missing_info({
        "P1": np.asarray(p1).tolist(),
        "P2": np.asarray(p2).tolist(),
        "aneurysm": 1,
        "added": 1,
    })


def _is_number(value):
    """True for a finite number. ``bool`` is excluded: ``True`` is an ``int`` subclass."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_position(value):
    """True for an ``[x, y, z]`` of finite numbers."""
    return (isinstance(value, (list, tuple)) and len(value) == 3
            and all(_is_number(c) for c in value))


def _validate_entry(key, d):
    """Raise :class:`DetectionFormatError` unless ``d`` is a detection the module can use.

    Values are checked, not just key presence: everything here is fed straight to numpy or
    to a VTK source, where a string coordinate or a NaN becomes a traceback in the Python
    console rather than the dialog the reviewer is meant to see.
    """
    if not isinstance(d, dict):
        raise DetectionFormatError(f"detection '{key}' is {type(d).__name__}, expected an object")

    missing = [k for k in REQUIRED_KEYS if k not in d]
    if missing:
        raise DetectionFormatError(f"detection '{key}' has no {' or '.join(missing)} position")

    for name in POSITION_KEYS:
        if name in d and not _is_position(d[name]):
            raise DetectionFormatError(
                f"detection '{key}' has a malformed {name}: expected three finite numbers"
            )
    for name in NUMBER_KEYS:
        if name in d and not _is_number(d[name]):
            raise DetectionFormatError(f"detection '{key}' has a non-numeric {name}")


def validate(data):
    """Return the detections in ``data``, or raise :class:`DetectionFormatError`.

    Every entry is checked, not just the first: :func:`add_missing_info` derives
    ``center``/``radius`` from ``P1``/``P2`` and would fail on a partially valid file.
    A single bad entry rejects the whole file — silently dropping a candidate aneurysm
    from a review list is worse than refusing to open it.
    """
    if not data:
        raise DetectionFormatError("file holds no detection")
    if not hasattr(data, "items"):
        raise DetectionFormatError(
            f"expected a JSON object mapping names to detections, got {type(data).__name__}"
        )
    for key, d in data.items():
        _validate_entry(key, d)
    return list(data.values())


def load(path):
    """Read ``path`` and return complete detections, most confident first.

    Raises :class:`DetectionFormatError` (a ``ValueError``, as is ``json.JSONDecodeError``)
    for malformed content, and ``OSError`` if the file cannot be read. Callers in the GUI
    turn those into a dialog; the message is meant to be shown to the reviewer.
    """
    data = jsonio.read_json(path)
    detections = validate(data)
    # After validate, so that a non-object entry still gets the message naming it rather
    # than a TypeError from the assignment below.
    for key, d in data.items():
        d[SOURCE_KEY] = key
    return sort_for_review([add_missing_info(d) for d in detections])


def sort_for_review(detections):
    """Return ``detections`` in the order the review table shows them.

    Most confident first, but detections the reviewer added by hand go last whatever their
    confidence: they carry ``confidence`` 1 only because there is no detector score to
    record, and sorting on that alone would promote them above every real candidate.
    """
    return sorted(detections, key=lambda d: (d.get("added", 0), -d["confidence"]))


def describe_load_failure(path, error):
    """Return the message shown to the reviewer when :func:`load` fails."""
    return f"No valid detection file found.\n\n{Path(path).name}: {error}"


def confidence_percent(d):
    """Return the confidence as the review table displays it."""
    return round(d["confidence"] * 100, 3)


def diameter(d):
    """Return the diameter in mm as the review table displays it."""
    return round(d["radius"] * 2, 3)
