# The detection file format

This is the contract between ReformatViz and whatever produces the candidate detections
(a detector model, a conversion script, a previous review session). Changing it is a
breaking change for those producers, so changes belong in `CHANGELOG.md`.

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
| `viewAngle` | no | number (degrees) | Reviewer's saved rotation about the aneurysm axis, `-180`–`180`. Defaults to `0` |
| `aneurysm` | no | boolean | Reviewer's verdict. Defaults to `false` |
| `Likert` | no | integer `1`–`5` | Reviewer's confidence score. **Absent means not scored** — there is no `0` |
| `added` | no | `1` | Present only on detections the reviewer added by hand; never written by a detector |

A producer normally supplies only `P1`, `P2` and `confidence`; everything else is either
derived on load or filled in by the reviewer.

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

`Likert` and `aneurysm` are *not* validated: a nonsensical value there is ignored by the
combo box and the checkbox rather than crashing.

Note the asymmetry: unknown extra fields are **preserved**, not rejected — they survive a
load/save round trip untouched, so a producer can carry its own metadata through a review.

## What `Save` writes

The saved file is the same shape, with two differences from the input:

1. **Keys are replaced** by the detection's position in the review table — `"0"`, `"1"`,
   `"2"`, … in most-confident-first order. Producer key names do not survive.
2. **Every derived field is now present**, because load fills them in.

Consequently a saved file is valid input, and re-loading it gives the same detections in the
same order — but the original key names are gone after one round trip. If those names matter
to you, keep them inside the detection object as an extra field, where they are preserved.

## Ordering

Detections are sorted by `confidence` descending on load, and the table, the saved keys, and
the Nth control point of the `Ostium`/`Dome`/`Confidences` markups nodes all follow that same
order.

Detections added by hand go to the end of the table regardless of confidence, and stay there
across a save and reload. They carry `confidence` 1 only because there is no detector score to
record for them — which is also why the **Confidence (%)** column shows them as `100`.
