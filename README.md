# ReformatViz — aneurysm review for 3D Slicer

A [3D Slicer](https://www.slicer.org/) extension for reviewing candidate intracranial
aneurysms on head angiography. It takes a volume plus a JSON file of candidate detections
and, for each candidate, reformats the slice view so it **always contains the aneurysm
axis** — the reviewer spins the view around that axis instead of hunting for an oblique
plane by hand, then records a verdict and a Likert score.

The module appears in Slicer as **Reformat Aneurysm**, under the *Aneurysms* category.

## Install

The module is plain Python; no build step is needed to use it.

1. Clone this repository.
2. In Slicer: *Edit → Application Settings → Modules*, add the `ReformatViz/` directory
   (the inner one, containing `ReformatViz.py`) to **Additional module paths**.
3. Restart Slicer. The module is under *Aneurysms → Reformat Aneurysm*.

Or, without changing settings:

```bash
# macOS — adjust for your Slicer install
/Applications/Slicer.app/Contents/MacOS/Slicer --additional-module-paths ReformatViz
```

**Slicer version:** developed and tested against **Slicer 5.12.3** (which ships Python 3.12.10,
numpy 2.4.6, VTK 9.6.2). Whether that is the version the lab commits to supporting is still to
be decided — see `docs/development.md`. The module drives the `Reformat`, `Volume Rendering`,
`Markups` and `Models` modules, all of which ship with Slicer, so there are no extension
dependencies.

## Use

1. **Select** a directory (under *Inputs*). The first `*.nii.gz` in it is loaded as the volume
   and the first `*.json` as the detections. Opening the module clears the scene first.
2. Click a row in the table to review that candidate. The slice view reformats to contain
   its axis; **Angle** spins the view around it; **Thickness** sets the slab thickness for
   the maximum-intensity projection.
3. Record a verdict per candidate — **Yes**, **No** (with a reason), **Uncertain** or **Not
   assessable** — plus a **Likert** score and a note. **Add** places two points (ostium then
   dome) to record an aneurysm the detector missed.
4. **Sign off** when every candidate has been decided. Nothing needs saving: the review is
   written to `review_<reviewer>.json` in the case's directory as you work, and resumed when
   you open the case again.

The full workflow and what each display control does is in
[`docs/user-guide.md`](docs/user-guide.md). The detection file the module reads is specified in
[`docs/detection-format.md`](docs/detection-format.md) — read that if you generate the
detection files — and the review it writes in
[`docs/review-format.md`](docs/review-format.md).

## Develop

```bash
python -m pip install -r requirements-dev.txt
python -m pytest      # the Slicer-free core: fast, no Slicer needed
ruff check .          # also enforces the Slicer-free import boundary
```

The code is split so most of it can be tested without launching Slicer:
`ReformatViz/ReformatVizLib/` holds the geometry, the detection file format and the display
maths, and may not import `slicer`, `vtk` or `qt`; `ReformatViz/ReformatViz.py` holds the
Slicer and Qt glue. See [`docs/architecture.md`](docs/architecture.md) and
[`docs/development.md`](docs/development.md).

## Citing

If this software contributes to a publication, please cite it — see
[`CITATION.cff`](CITATION.cff).

## Authors

Developed by Youssef Assis, maintained by Erwan Kerrien
([Loria](https://www.loria.fr/), Université de Lorraine, CNRS, Inria).

## Licence

[Apache License 2.0](LICENSE).

## Before a first release

- **Confirm the copyright holder.** `CITATION.cff` and the source headers credit the authors
  rather than a legal entity. Loria is a joint unit, so the holder is likely Inria, CNRS
  and/or Université de Lorraine — confirm with the lab, then set it consistently.
- **Decide the supported Slicer version.** The code is developed and tested against 5.12.3;
  confirm that is what the lab reviews with, then pin the same version in CI.
- **Replace the placeholder extension metadata** in the top-level `CMakeLists.txt`: it still
  carries the wizard's "John Doe (AnyWare Corp.)" contributor and `example.com` URLs.
- **Add a real screenshot.** `ReformatViz.png` is still the extension wizard's placeholder
  icon, not a picture of the module.
