# ReformatViz — aneurysm review for 3D Slicer

> Mirrored from the original repository at
> [gitlab.inria.fr/yassis/slicer-reformat-aneurysm](https://gitlab.inria.fr/yassis/slicer-reformat-aneurysm).

A [3D Slicer](https://www.slicer.org/) extension for reviewing candidate intracranial
aneurysms on head angiography. Given a volume and a JSON file of candidate detections, it
reformats the slice view so it **always contains the aneurysm axis** — the reviewer spins the
view around that axis instead of hunting for an oblique plane by hand — and records a verdict,
a Likert score, and any aneurysm the detector missed.

The module appears in Slicer as **Reformat Aneurysm**, under the *Aneurysms* category. It is
developed against **Slicer 5.12.3** and drives only modules that ship with Slicer, so there
are no extension dependencies.

## Install

Plain Python — no build step.

```bash
# macOS; adjust for your Slicer install
/Applications/Slicer.app/Contents/MacOS/Slicer --additional-module-paths ReformatViz
```

To install it permanently instead: *Edit → Application Settings → Modules*, add the inner
`ReformatViz/` directory (the one containing `ReformatViz.py`) to **Additional module paths**,
then restart Slicer.

## Try it

`data/sample_case/` is a synthetic phantom angiogram with five candidates, three of them real,
so the module can be exercised without a patient volume to hand. Point *Inputs → Select* at
that directory; [`data/README.md`](data/README.md) says which candidates are the real ones.

## Use

1. **Select** a case directory. The first `*.nii.gz` in it is loaded as the volume and the
   first `*.json` that is not a review file as the detections. Anything else already in the
   scene is left alone.
2. Click a row in the table to review that candidate. The slice view reformats onto its axis;
   **Angle** spins the view around it, **Thickness** sets the slab thickness for the
   maximum-intensity projection.
3. Record a verdict — **Yes**, **No** (with a reason), **Uncertain** or **Not assessable** —
   plus a **Likert** score and a note. **Add** places two points (ostium, then dome) to record
   an aneurysm the detector missed.
4. **Sign off** once every candidate has been decided. Nothing needs saving: the review is
   written to `review_<reviewer>.json` beside the case as you work, and resumed when you open
   the case again.

## Documentation

| | |
| --- | --- |
| [user guide](docs/user-guide.md) | what every control does, in the reviewer's terms |
| [detection format](docs/detection-format.md) | the JSON the module reads — read this if you generate detection files |
| [review format](docs/review-format.md) | the JSON the module writes: verdicts, sign-off, audit trail |
| [architecture](docs/architecture.md) | how the code is split, the reformatting maths |
| [development](docs/development.md) | the two Python environments, test loop, open decisions |

## Develop

```bash
python -m pip install -r requirements-dev.txt
python -m pytest      # the Slicer-free core: ~260 tests, well under a second
ruff check .          # lint, and the Slicer-free import boundary
```

Most of the code is testable without launching Slicer: `ReformatViz/ReformatVizLib/` holds the
geometry, the detection file format and the display maths, and may not import `slicer`, `vtk`
or `qt`; `ReformatViz/ReformatViz.py` holds the Slicer and Qt glue.

## Credits

Developed by Youssef Assis, maintained by Erwan Kerrien
([Loria](https://www.loria.fr/), Université de Lorraine, CNRS, Inria).

Licensed under [Apache 2.0](LICENSE).
