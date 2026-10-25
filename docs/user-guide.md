# User guide

The module is **Candidate Review**, under *Annotation*. It reviews a list of candidate
detections — aneurysms on angiography, for instance — one at a time, on a slice view angled so
the candidate is never foreshortened.

> Screenshots still to be added — `ReformatViz.png` in the repository root is the extension
> wizard's placeholder icon, not a picture of the module.

## Loading a case

**Select** (under *Inputs → Input Patient*) opens a directory chooser. From the directory you pick, the module takes:

- the **first** `*.nii.gz` in alphabetical order as the volume, and
- the **first** `*.json` in alphabetical order as the detections.

So one directory per case, with one volume and one detection file, is the arrangement it
expects. If the detection file cannot be read, a dialog says why and the volume still loads
with an empty table. See [`detection-format.md`](detection-format.md) for the file format.

Opening a case also opens its **review** — `review_<your name>.json`, written in the same
directory. If one is already there it is resumed, so a case can be left and picked up later.
Nothing you do is written into the detection file; that stays the detector's.

Opening the module leaves the rest of the Slicer scene alone; loading a case replaces only the
previous case's volume and markups. You do not need to save: every verdict is written to the
review as you make it.

To practise on something first, select `data/sample_case/` in the repository: a synthetic case
with five candidates, three of which are aneurysms. Which three, and what each of the others
is, is listed in [`../data/README.md`](../data/README.md) — so it can also be used to check
that the module is behaving before a real case is opened.

## Reviewing a candidate

The table lists candidates, most confident first:

| Column | Meaning |
| --- | --- |
| Confidence (%) | The detector's score. Sorts the table |
| Diameter (mm) | Twice the sphere radius, derived from the base–tip separation |
| Verdict | Your verdict: **—** (not looked at yet), **Yes**, **No**, **Uncertain** (you want a second opinion), **Not assessable** (the image cannot answer it) |
| Not because | Why you rejected it. Only available on a **No** |
| Likert | Your confidence, 1–5. `-` means not scored |
| Note | Anything worth recording about this candidate |

**—** is a real value, not an empty one: a candidate you have not looked at is not the same as
one you looked at and rejected, and the case cannot be signed off while any row still shows it.

**Click a row** to review that candidate. Everything then focuses on it: only its sphere,
line and markups are shown, the slice view reformats onto it, and the 3D camera re-centres.

With a row selected:

- **Angle** (−180°…180°, under *Cut planes*) spins the view around the candidate's axis. The axis stays
  in the slice plane at every angle, so the candidate is never foreshortened, and it stays
  upright rather than tumbling as you rotate. Your angle is remembered per candidate and
  saved with it.
- **Thickness** sets the slab thickness in mm for the maximum-intensity projection on the
  Red slice — thicker shows more vessel at once, thinner isolates the neck.
- **Center view** re-aligns the 3D view with the current slice, keeping its distance.

**Unselect all** returns to showing every candidate at once; **Clean** clears the screen. Your
verdicts are already on disk, so **Clean** does not discard them — reopening the case brings
them back.

## Adding a missed finding

1. **Add** (under *Detections*) starts placement.
2. Click the **base**, then the **tip** — for an aneurysm, the ostium where it meets the
   parent vessel, then the dome. A sphere previews between them and follows the points while you drag either one.
3. **OK** commits it — it joins the end of the table, already recorded as **Yes**, since you
   placed it because it is one. **Cancel** discards it.

Your own findings are marked as yours in the review and kept apart from the detector's, and they
come back when the case is reopened.

## Display controls

**3D Volume**

| Control | Effect |
| --- | --- |
| Visibility | Shows or hides the 3D volume rendering |
| Translucent | Renders only a thin shell at the threshold, so the vessel surface no longer hides the slice behind it |
| Opacity, Threshold | The intensity above which tissue becomes visible, and how solidly |
| Colour | Single colour for the volume rendering |
| Crop ROI | Restricts rendering to a box — around the selected candidate at four times its radius, or the whole volume if nothing is selected |
| Show ROI | Shows the crop box handles so you can adjust it |

**Detections** — independent visibility, colour and opacity for **Spheres**, **Main Axis**
(the base-to-tip line), **Confidence** labels and **Markups** (the base/tip points). With a row selected these apply to
that candidate only; with nothing selected, to all of them.

Under *Cut planes*, **Visibility** toggles the Red slice in the 3D view.

## Finishing a case

Every verdict, score, note and view angle is written to `review_<your name>.json` as you work.
There is nothing to remember to save, and a crash costs at most the row you were on.

**Sign off** declares the case finished — that is what tells anyone reading the review that
they are looking at a complete read rather than one still in progress. It is refused while any
candidate is still **—**, and says how many are left. The heading above the table tracks the
same thing: *Output — 2 of 3 decided*.

A signed-off case is locked. Pressing **Reopen** asks why and records the answer, so an
amendment is part of the record rather than a silent edit; the original sign-off stays in the
file with its timestamp.

Who the review is attributed to is your OS user name. To set it explicitly, in the Python
console:

```python
slicer.app.userSettings().setValue("ReformatViz/Reviewer", "ykerrien")
```

See [`review-format.md`](review-format.md) for what the file contains.
