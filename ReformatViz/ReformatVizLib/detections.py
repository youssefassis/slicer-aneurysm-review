# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""The detection record: its schema, its validation, and its JSON file format.

A detection is a plain ``dict`` — the same object the reviewer's edits are written back
into and the same object that gets serialised, so there is one representation and no
conversion layer to keep in step.

The file format is the contract with whatever produces the detections; it is documented in
``docs/detection-format.md``. Changing it is a breaking change for those producers.
"""

import contextlib
import json
import math
import os

import numpy as np

#: Positions every detection must carry: the ostium and the dome, in RAS millimetres.
REQUIRED_KEYS = ("P1", "P2")

#: Keys derived from P1/P2 when a producer omits them.
DERIVED_KEYS = ("confidence", "center", "radius", "viewAngle", "aneurysm")

#: Keys holding an ``[x, y, z]`` position, checked whenever they are present.
POSITION_KEYS = (*REQUIRED_KEYS, "center")

#: Keys holding a plain number, checked whenever they are present.
NUMBER_KEYS = ("confidence", "radius", "viewAngle")

#: Likert combo index meaning "not scored"; the key is absent rather than 0.
LIKERT_UNSCORED = 0


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


def set_likert_score(d, index):
    """Record a Likert combo index, removing the key entirely for "not scored"."""
    if index == LIKERT_UNSCORED:
        d.pop("Likert", None)
    else:
        d["Likert"] = index
    return d


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
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    detections = [add_missing_info(d) for d in validate(data)]
    return sort_for_review(detections)


def sort_for_review(detections):
    """Return ``detections`` in the order the review table shows them.

    Most confident first, but detections the reviewer added by hand go last whatever their
    confidence: they carry ``confidence`` 1 only because there is no detector score to
    record, and sorting on that alone would promote them above every real candidate.
    """
    return sorted(detections, key=lambda d: (d.get("added", 0), -d["confidence"]))


def save(path, detections):
    """Write ``detections`` to ``path``, keyed by their position in the review table.

    Note this is *not* the shape that was read: input keys are the producer's names, output
    keys are ``"0"``, ``"1"``, ... in table order. See ``docs/detection-format.md``.

    The write goes to a temporary file in the same directory and is renamed into place, so a
    failure part-way through leaves the previous file intact: the reviewer normally saves over
    the file they loaded, and a truncated one would cost them the whole session's verdicts.
    """
    temporary = f"{os.fspath(path)}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump({str(i): d for i, d in enumerate(detections)}, f, indent=2)
        os.replace(temporary, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.remove(temporary)
        raise


def describe_load_failure(path, error):
    """Return the message shown to the reviewer when :func:`load` fails."""
    return f"No valid detection file found.\n\n{os.path.basename(path)}: {error}"


def describe_save_failure(path, error):
    """Return the message shown to the reviewer when :func:`save` fails.

    The previous file is still intact — see :func:`save` — which is the useful thing to say
    to someone who has just tried to write over a review they care about.
    """
    return (f"Saving the detections failed. The existing file has been left unchanged."
            f"\n\n{os.path.basename(path)}: {error}")


def confidence_percent(d):
    """Return the confidence as the review table displays it."""
    return round(d["confidence"] * 100, 3)


def diameter(d):
    """Return the diameter in mm as the review table displays it."""
    return round(d["radius"] * 2, 3)
