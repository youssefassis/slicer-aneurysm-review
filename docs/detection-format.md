# The detection file format

This is the contract between ReformatViz and whatever produces the candidate detections (a
detector model, a conversion script). Changing it is a breaking change for those producers, so
changes belong in `CHANGELOG.md`.

**The module only reads this file.** What the reviewer decides is written to a separate review
file — [`review-format.md`](review-format.md) — so that a review can still say which candidates
it was made about, and so a detector's output is never edited by the tool reviewing it.

The format is implemented in `ReformatViz/ReformatVizLib/detections.py` and pinned by
`tests/test_detections.py`. `tests/data/detections_sample.json` is a valid example.

> **The format carries no version field.** There is no way for a reader to tell which
> revision of this document a given file follows. Adding a `formatVersion` key is
> recommended before the format changes for the first time.

## Shape

A JSON object whose keys name the detections and whose values are the detections. The key
text is **not** used by the module — only the values are read, so any naming scheme works.

```json
{
  "candidate_0": {
    "P1": [-12.5, 18.0, 42.25],
    "P2": [-9.5, 22.0, 42.25],
    "confidence": 0.42
  }
}
```

## Fields

All positions are **RAS world coordinates in millimetres**, matching what Slicer reports for
a markup point. They are *not* voxel indices.

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `P1` | **yes** | `[x, y, z]` | The ostium — where the aneurysm meets the parent vessel |
| `P2` | **yes** | `[x, y, z]` | The dome — the far tip of the aneurysm |
| `confidence` | no | number `0`–`1` | Detector score. Displayed as a percentage; sorts the table. Defaults to `1` |
| `center` | no | `[x, y, z]` | Sphere centre. Defaults to the midpoint of `P1`/`P2` |
| `radius` | no | number (mm) | Sphere radius. Defaults to half the `P1`–`P2` distance |
| `viewAngle` | no | number (degrees) | Legacy: a rotation about the aneurysm axis, `-180`–`180`. Defaults to `0` |
| `aneurysm` | no | boolean | Legacy: a verdict. Defaults to `false`. See *The reviewer's fields* |
| `Likert` | no | integer `1`–`5` | Legacy: a confidence score |
| `added` | no | `1` | Legacy: marked a detection the reviewer added by hand |

A producer supplies `P1`, `P2` and `confidence`; everything else is derived on load, and the
four legacy fields exist only because files written by earlier versions of this module have
them.

### `sourceKey`

`load` records the key each detection was filed under as `sourceKey` on the detection itself.
It is **derived** — a value a producer puts there is overwritten, since a detection claiming a
key that is not its own would break the join silently. It is what the review file
([`review-format.md`](review-format.md)) is keyed by, and the only thing in the input that
identifies a candidate.

`radius` is the *display* radius of the sphere drawn in the views. It is derived from the
P1–P2 separation rather than measured from the image, so it indicates the candidate's extent
rather than a validated diameter. The **Diameter (mm)** column is exactly `2 × radius`.

## Validation

A file is rejected outright, with a dialog naming the offending entry, if:

- it is not a JSON object (a top-level array is rejected),
- it is empty,
- any value is not an object,
- **any** entry is missing `P1` or `P2`,
- any `P1`, `P2` or `center` is not three finite numbers — the wrong length, a `null` or
  string coordinate, or one of JSON's non-standard `NaN`/`Infinity` literals, or
- any `confidence`, `radius` or `viewAngle` present is not a finite number (a `true`/`false`
  counts as non-numeric here).

Every entry is checked, not just the first, and values are checked rather than just key
presence. One bad entry rejects the whole file: silently dropping a candidate would hide it
from the reviewer with no indication.

`Likert` and `aneurysm` are *not* validated: nothing reads them any more, so a nonsensical
value there is ignored rather than rejected.

Note the asymmetry: unknown extra fields are **preserved**, not rejected — a producer can carry
its own metadata alongside a detection and the module will leave it alone.

## The reviewer's fields

`aneurysm`, `Likert` and `viewAngle` are accepted because files written by earlier versions of
this module carry them. They are **not** where a review is recorded any more, and the module
does not write them back: the review file is the record, and it can express things this file
cannot — that a candidate has not been looked at yet, why one was rejected, and who decided
when. A file that still carries them is loaded without complaint and they are ignored.

## Ordering

Detections are sorted by `confidence` descending on load, and the table and the Nth control
point of the `Ostium`/`Dome`/`Confidences` markups nodes follow that same order.

Detections the reviewer adds go to the end of the table regardless of confidence. They carry
`confidence` 1 only because there is no detector score to record for them — which is also why
the **Confidence (%)** column shows them as `100`.
