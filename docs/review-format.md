# The review file format

What a reviewer decided about one case. This is the module's **output**; the detection file
described in [`detection-format.md`](detection-format.md) is its **input**, and the module
never writes to it. A triage review has to be able to say *these are the candidates I was
shown*, which is impossible if the review overwrites the file it reviewed.

Implemented in `ReformatViz/ReformatVizLib/review.py`, pinned by `tests/test_review.py`.

## Where it lives

`review_<reviewer>.json`, in the case's own directory, beside the volume and the detection
file. The reviewer's name comes from the `ReformatViz/Reviewer` application setting, or the
OS user name if that is unset, and is reduced to `[A-Za-z0-9._-]` for the file name.

Per reviewer as well as per case, so a second reviewer cannot silently continue someone
else's read. There is one reviewer today; the naming is cheap now and awkward to retrofit.

## Versioning

`formatVersion` is `1`. **A reader must check it.** The module refuses a file whose version it
does not recognise rather than guessing — half-understanding a clinical record is worse than
declining to open it. Any change to the shape below bumps the version and goes in
`CHANGELOG.md`.

## Shape

```json
{
  "formatVersion": 1,
  "study":  { "directory": "patient-004" },
  "source": { "detectionFile": "detections.json",
              "sha256": "9f2b…",
              "volumeFile": "angio.nii.gz" },
  "review": { "reviewer": "ykerrien",
              "tool": "ReformatViz 0.1.0 (Slicer 5.12.3)",
              "startedAt": "2026-07-26T09:00:00+00:00",
              "signedOffAt": "2026-07-26T09:31:12+00:00",
              "state": "signed_off" },
  "detections": {
    "candidate_0": { "state": "confirmed", "reason": null, "likert": 5,
                     "note": "left MCA bifurcation", "viewAngle": 35,
                     "decidedAt": "2026-07-26T09:04:11+00:00" },
    "candidate_1": { "state": "rejected", "reason": "infundibulum", "likert": null,
                     "note": "", "viewAngle": 0,
                     "decidedAt": "2026-07-26T09:07:02+00:00" },
    "added_0":     { "state": "confirmed", "reason": null, "likert": 4, "note": "",
                     "viewAngle": 0, "decidedAt": "2026-07-26T09:20:44+00:00",
                     "addedByReviewer": true,
                     "P1": [-12.5, 18.0, 42.25], "P2": [-9.5, 22.0, 42.25] }
  },
  "events": [
    { "at": "2026-07-26T09:04:11+00:00", "what": "state", "key": "candidate_0",
      "state": "confirmed", "reason": null },
    { "at": "2026-07-26T09:31:12+00:00", "what": "signed_off" }
  ]
}
```

`detections` is keyed by **the producer's own keys** from the detection file, so a review
joins back to the candidates it was made about. Aneurysms the reviewer added by hand live in
the same map under generated `added_<n>` keys, carrying their positions so they can be drawn
again; a key that already exists is never reused.

Timestamps are ISO 8601 with an offset, in UTC as the module writes them.

## Fields

### `detections.<key>`

| Field | Type | Meaning |
| --- | --- | --- |
| `state` | see below | The verdict. **`unreviewed` is a real state** |
| `reason` | string or `null` | Why it is not an aneurysm. Only ever set on `rejected` |
| `likert` | `1`–`5` or `null` | Reviewer's confidence. `null` is not scored — there is no `0` |
| `note` | string | Free text; `""` when there is none |
| `viewAngle` | number | Rotation the reviewer left the view at, so the case reopens as they left it |
| `decidedAt` | timestamp or `null` | When the verdict was recorded; `null` while `unreviewed` |
| `addedByReviewer` | `true` | Present only on the reviewer's own findings |
| `P1`, `P2` | `[x, y, z]` | RAS millimetres. Present only on the reviewer's own findings |

**States.** `unreviewed` (not decided — blocks sign-off), `confirmed`, `rejected`,
`uncertain` (cannot call it; wants a second opinion), `not_assessable` (the image cannot
answer the question — coverage, motion, contrast timing).

**Rejection reasons.** `infundibulum`, `vessel_loop`, `artifact`, `other_pathology`,
`duplicate`, `other`. Provisional — this is the field that turns a false-positive rate into
something actionable, so confirm it with the reviewers before real cases are read.

### `review`

| Field | Meaning |
| --- | --- |
| `reviewer` | Who read the case. A review without one is refused |
| `tool` | Which build produced it — the review is only as traceable as this string |
| `startedAt` | When the case was opened |
| `signedOffAt` | When it was signed off; `null` until then |
| `state` | `received` (nothing decided), `in_review`, `signed_off` |

### `events`

Append-only. Every verdict, score, note and reviewer-added finding appends one entry, so a
verdict that was changed leaves both decisions in the record with their timestamps. `viewAngle`
does not: it is where the reviewer was looking, not something they decided.

## Guarantees

- **Sign-off requires every candidate to be decided.** `sign_off` refuses while any is
  `unreviewed` and names them, and `load` refuses a file that claims `signed_off` with
  undecided candidates — otherwise the rule holds only while the module happens to be open.
- **Amendment is recorded, not silent.** A signed-off case refuses changes; reopening it takes
  a reason and appends an event, leaving the earlier sign-off in the log.
- **A write cannot truncate the previous review.** It goes to `review_<reviewer>.json.tmp` and
  is renamed into place; see `jsonio.write_json`.
- **`source.sha256`** is the digest of the detection file the review was made against, so a
  detection file regenerated under an existing review is noticed rather than silently changing
  what the reviewer is held to have seen.

## Not in the format yet

- **`study` identifies a directory, not a study.** With DICOM ingest this becomes
  `StudyInstanceUID` / `SeriesInstanceUID` / accession, which is what actually binds a review
  to a patient. Until then the binding is the directory and the file digests, and that is the
  format's weakest point for prospective use.
- **No time on task.** Recording how long a candidate was looked at needs the dwell timing
  that belongs with the keyboard-driven review, and a field that is always `0` would be worse
  than none.
- **No patient identifiers.** Nothing here carries a name, an ID or a date of birth; whether
  the review file may is a decision that has not been made.
