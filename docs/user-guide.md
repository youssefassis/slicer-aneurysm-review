# User guide

The module is **Reformat Aneurysm**, under *Aneurysms*. It reviews a list of candidate
aneurysms one at a time, on a slice view angled so the aneurysm is never foreshortened.

> Screenshots still to be added — `ReformatViz.png` in the repository root is the extension
> wizard's placeholder icon, not a picture of the module.

## Loading a case

**Select** (under *Inputs → Input Patient*) opens a directory chooser. From the directory you pick, the module takes:

- the **first** `*.nii.gz` in alphabetical order as the volume, and
- the **first** `*.json` in alphabetical order as the detections.

So one directory per case, with one volume and one detection file, is the arrangement it
expects. If the detection file cannot be read, a dialog says why and the volume still loads
with an empty table. See [`detection-format.md`](detection-format.md) for the file format.

**Opening the module clears the whole Slicer scene**, and so does loading a case. Save your
work before switching cases — nothing is written to disk until you press **Save**.

## Reviewing a candidate

The table lists candidates, most confident first:

| Column | Meaning |
| --- | --- |
| Confidence (%) | The detector's score. Sorts the table |
| Diameter (mm) | Twice the sphere radius, derived from the ostium–dome separation |
| Aneurysm ? | Your verdict. Tick if this is a real aneurysm |
| Likert | Your confidence, 1–5. `-` means not scored |

**Click a row** to review that candidate. Everything then focuses on it: only its sphere,
line and markups are shown, the slice view reformats onto it, and the 3D camera re-centres.

With a row selected:

- **Angle** (−180°…180°, under *Cut planes*) spins the view around the aneurysm axis. The axis stays
  in the slice plane at every angle, so the aneurysm is never foreshortened, and it stays
  upright rather than tumbling as you rotate. Your angle is remembered per candidate and
  saved with it.
- **Thickness** sets the slab thickness in mm for the maximum-intensity projection on the
  Red slice — thicker shows more vessel at once, thinner isolates the neck.
- **Center view** re-aligns the 3D view with the current slice, keeping its distance.

**Unselect all** returns to showing every candidate at once; **Clean** discards everything
loaded, including unsaved verdicts.

## Adding a missed aneurysm

1. **Add** (under *Detections*) starts placement.
2. Click the **ostium** (where the aneurysm meets the parent vessel), then the **dome** (its
   far tip). A sphere previews between them and follows the points while you drag either one.
3. **OK** commits it — it joins the end of the table, already ticked as an aneurysm. **Cancel**
   discards it.

Added candidates are marked in the saved file, so they can be told apart from the detector's.

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
(the ostium-to-dome line), **Confidence** labels and **Markups** (the ostium/dome points). With a row selected these apply to
that candidate only; with nothing selected, to all of them.

Under *Cut planes*, **Visibility** toggles the Red slice in the 3D view.

## Saving

**Save** asks for a filename and writes every candidate with your verdicts, Likert scores and
saved view angles. There is no autosave and no prompt on exit — an unsaved review is lost when
the scene is cleared, when another case is loaded, or when Slicer closes.

A saved file can be loaded again to continue a review: verdicts, scores and view angles all
come back. Note that the keys in the saved file are renumbered by table position rather than
keeping the producer's names — see [`detection-format.md`](detection-format.md).
