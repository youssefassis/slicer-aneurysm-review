# Sample data

A case to try the module on, so that reviewing something does not depend on having access to
a real angiogram.

```
sample_case/
  angio.nii.gz     the volume  (136 x 120 x 100 at 0.5 mm, ~100 KiB)
  detections.json  five candidate detections
```

**It is synthetic.** No patient data is involved: the volume is a phantom drawn by
[`tools/make_sample_data.py`](../tools/make_sample_data.py) — bright vessels on a dark
background, the way a subtracted or MR angiogram looks — shaped into a crude circle of
Willis. It is enough to exercise the workflow and to see the reformatting behave. It is
**not** a substitute for real anatomy when judging how the module reads clinically, and the
sacs are spheres, so nothing about their shape is realistic.

## Open it

1. Launch Slicer with the module (see the [README](../README.md)) and go to
   *Aneurysms → Reformat Aneurysm*.
2. Under *Inputs*, click **Select** and choose `data/sample_case`. The volume and the
   detection file are both picked up from the directory.
3. Click a row in the table. The Red slice reformats onto that candidate's axis; **Angle**
   spins the view around it.

The [user guide](../docs/user-guide.md) covers the controls.

## What is in it

Five candidates, as a detector would hand them over: three aneurysms that are really drawn in
the image and two false positives that are not. Nothing in `detections.json` says which is
which — that is the point of it — so the answers are here.

| Candidate | Confidence | Diameter | What it is |
| --- | --- | --- | --- |
| `candidate_0` | 93 % | 5.5 mm | **Aneurysm.** Right MCA bifurcation — the unmistakable one |
| `candidate_1` | 81 % | 5.0 mm | **Aneurysm.** Anterior communicating artery, pointing anterosuperiorly |
| `candidate_2` | 62 % | 3.7 mm | **False positive.** A bend in the basilar artery: vessel, no sac |
| `candidate_3` | 44 % | 2.8 mm | **Aneurysm.** Left ICA terminus, small — a genuine judgement call |
| `candidate_4` | 19 % | 2.8 mm | **False positive.** An isolated blob, connected to no artery |

The diameter column is what the table shows, `2 × radius`, derived from the ostium–dome
separation — see [`docs/detection-format.md`](../docs/detection-format.md).

Each drawn sac **is** the sphere the module derives from `P1`/`P2`, so the sphere overlay
lands on the sac rather than near it. A candidate whose sphere hangs half in the dark is one
of the two false positives.

Reviewing all five and signing off writes `review_<you>.json` into `data/sample_case/`. That
file is ignored by git, so a trial review does not show up as a change. Delete it to start
the case over.

## Regenerate it

Both files come out of the constants at the top of `tools/make_sample_data.py`, so a
candidate cannot drift away from the sac it points at:

```bash
python tools/make_sample_data.py
```

The output is deterministic — regenerating without changing the constants leaves `git status`
clean. `tests/test_sample_data.py` checks the committed files against those constants, so the
two cannot fall out of step unnoticed.
