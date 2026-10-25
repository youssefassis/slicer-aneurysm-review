# Development

## The two environments

This is the thing to internalise before anything else.

|  | Slicer's Python | Your dev Python |
| --- | --- | --- |
| Runs | the module, in the GUI | `pytest`, `ruff` |
| Provides | numpy, VTK, Qt, CTK, `slicer` | the latest of what `requirements-dev.txt` lists |
| Managed by | the Slicer installer | you |
| Verified as | 3.12.10, numpy 2.4.6, VTK 9.6.2 (Slicer 5.12.3) | Python 3.11 or later |

Slicer's interpreter here is **newer** than a typical dev machine's, not older. Check any
install with:

```bash
/Applications/Slicer.app/Contents/bin/PythonSlicer -c \
  "import sys, numpy, vtk; print(sys.version, numpy.__version__, vtk.vtkVersion.GetVTKVersion())"
```

`requirements-dev.txt` gives minimum versions, not pins, so the tests run against the newest
numpy rather than the one Slicer ships. `ReformatVizLib` uses only long-stable numpy
(`asarray`, `dot`, `linalg.norm`), so the two agree in practice; if a test passes outside
Slicer and the same logic misbehaves inside it, check the core against Slicer's own
interpreter rather than yours:

```bash
/Applications/Slicer.app/Contents/bin/PythonSlicer -c \
  "import sys; sys.path.insert(0,'ReformatViz'); import ReformatVizLib.geometry"
```

You do **not** pip-install into Slicer. Extensions are loaded from disk, and Slicer supplies
their dependencies. That is why `pyproject.toml` declares no runtime dependencies and no
build backend — a dependency listed there would describe nothing real. `requirements-dev.txt` is only for
running the tests and linter outside Slicer.

It also means **reproducibility comes from pinning the Slicer version**, not from pinning
Python packages.

> **Pin the Slicer version.** Development and the in-Slicer test currently run against
> **Slicer 5.12.3** (revision 34627), which is what `README.md` names. What is still a
> decision rather than a fact: whether that is the version the lab commits to supporting, and
> therefore what to pin in the commented `slicer-tests` jobs in the CI files. Pick the
> release the lab actually reviews with; if it is not 5.12.x, re-check the interpreter and
> `requires-python`, because Slicer's Python moves between releases.

## The fast loop

Most work needs no Slicer at all:

```bash
python -m pip install -r requirements-dev.txt

python -m pytest                      # whole Slicer-free suite, well under a second
python -m pytest tests/test_geometry.py -q
python -m pytest -k "rotate2Z"        # one area
python -m pytest -k "slice_contains"  # the reformatting invariant
python -m pytest -x -vv               # stop at the first failure, verbose

ruff check .                          # lint + the import boundary
ruff check --fix .
```

`pytest` finds `ReformatVizLib` through `pythonpath = ["ReformatViz"]` in `pyproject.toml`,
which is the same layout Slicer sees — no install step.

### Are the tests actually testing anything?

A quick way to check a test has teeth: break the code on purpose and confirm the suite goes
red. Reverting the transpose in `geometry.slice_normal` (`Rz.T @ …` → `Rz @ …`), for instance,
should fail the perpendicularity tests. If a change to real logic leaves the suite green, the
test is decorative.

## The Slicer loop

For anything touching widgets, MRML nodes or the views:

```bash
# macOS; adjust for your install
/Applications/Slicer.app/Contents/MacOS/Slicer --additional-module-paths ReformatViz
```

Open `data/sample_case/` when you need a case to work on: a synthetic volume and five
candidates, described in [`data/README.md`](../data/README.md). Reviews of it are gitignored,
so a trial run leaves no changes behind. `tools/make_sample_data.py` regenerates it
deterministically from the constants at the top of that file — change the anatomy there, run
it, and commit both files together, because `tests/test_sample_data.py` compares them.

Then iterate without restarting: open the module, use the **Reload** button in *Developer
Tools*, or `Ctrl+R` / `Cmd+R`. Reloading destroys the widget, so `cleanup()` removes the nodes
this module put in the scene — the case you were looking at goes with it, anything else stays.

The Python console (*View → Python Console*) is the fastest way to poke at live state:

```python
w = slicer.modules.ReformatVizWidget
len(w.detections), w.selected_detection
w.candidateAxis, w.candidateOrigin
```

### The in-Slicer test

`ReformatVizTest` in `ReformatViz.py` covers what genuinely needs MRML. Run it from the
module's **Reload and Test** button, or from the command line the same way `ctest` does —
`slicer.testing.runUnitTest`, which runs each `test_*` method with a cleared scene:

```bash
MOD="$PWD/ReformatViz"
/Applications/Slicer.app/Contents/MacOS/Slicer --no-splash --testing \
  --additional-module-paths "$MOD" --python-code \
  "import slicer.testing; slicer.testing.runUnitTest(['$MOD'], 'ReformatViz.ReformatVizTest'); slicer.util.exit(0)"
```

The process exits non-zero if a test fails, so this is usable as a gate. Note `--testing`:
without it `errorDisplay`/`warningDisplay` open modal dialogs and the run blocks forever.

The cases cover the module loading, a detection file becoming scene nodes, the Red slice being
reformatted onto a selected detection, two placed points becoming a detection, the widget
resetting itself when the scene is closed, and the review paths: a verdict reaching the file
without being asked to, sign-off waiting for the last undecided candidate, a case resuming as
it was left, and an unreadable review never being overwritten.

The review cases build their own case directory — a volume saved by Slicer plus the sample
detection file — and call `load_detections` and `startReview` the way `load_files` does, since
the directory dialog cannot be opened from a test.

It reads `tests/data/detections_sample.json` from the source tree and skips those cases if
run from an installed extension where that file is absent. It builds its own small synthetic
volume, because Slicer will not move a slice in a scene with no background volume — which is
what made the original version of this test unpassable. Keep it thin — new cases belong in
`tests/` unless they need a scene.

## Building the packaged extension

Only needed to distribute the extension; not for development.

```bash
cmake -S . -B ../build -DSlicer_DIR=/path/to/Slicer-build/Slicer-build
cmake --build ../build
ctest --test-dir ../build -R ReformatViz     # runs ReformatVizTest inside Slicer
```

`ReformatVizLib`'s files are listed individually in `ReformatViz/CMakeLists.txt` under
`MODULE_PYTHON_SCRIPTS` so they are installed next to the module. **Add new library modules
to that list** — a file that is not listed will work from `--additional-module-paths` during
development and then be missing from the packaged extension, which is a nasty way to find
out.

## CI

The project has two remotes — GitHub and the original Inria GitLab — and both run the same two
checks, so a push cannot be green on one and red on the other:

| | |
| --- | --- |
| [`.github/workflows/checks.yml`](../.github/workflows/checks.yml) | GitHub Actions |
| [`.gitlab-ci.yml`](../.gitlab-ci.yml) | GitLab |

Each runs `ruff check .` and `python -m pytest` on Python 3.11, and nothing else. The
in-Slicer test is wired up on neither: booting Slicer takes minutes and a container image, and
gating every commit on that is how CI ends up switched off. Both files carry a commented
`slicer-tests` job recording what enabling it would take. **Change one file, change the
other** — the point of them is that they agree.

## Conventions

- One logical change per commit; `[TAG] imperative summary` with TAG one of `[FEAT] [FIX]
  [REFACTOR] [CHORE] [DOCS] [TEST] [PERF]`. Explain *why* in the body.
- Branch per task from `main`, prefixed `feat/ fix/ refactor/ chore/ docs/ test/`.
- `ruff check .` and `pytest` must pass before committing. Do not add lint exemptions
  without a comment giving the reason — the existing ones in `pyproject.toml` all have one.
- Behaviour change → update `CHANGELOG.md`. Detection format change → also
  `docs/detection-format.md`, and treat it as breaking for whatever writes those files.
- `ReformatVizLib` is snake_case (plain Python); the widget keeps Qt's camelCase for slots.
  Follow whichever side of the boundary you are on.
- Filesystem paths are `pathlib.Path`, not `os.path` strings. Qt hands over plain strings, so
  convert at the boundary (`Path(dialog_result)`) and convert back with `str()` only where Qt
  needs it again — `slicer.util.loadVolume` passes the name straight to Qt, which cannot
  convert a `Path`. Functions taking a path accept either, via `Path(path)`.

## Open decisions

These block a first tagged release and are the lab's calls, not something to fill in while
working on the code.

- **The copyright holder.** The source headers credit the authors rather than a legal entity.
  Loria is a joint unit, so the holder is likely Inria, CNRS and/or Université de Lorraine —
  confirm with the lab, then set it consistently across the headers.
- **The supported Slicer version.** The code is developed and tested against 5.12.3; confirm
  that is what the lab reviews with, then pin the same version in CI.
- **The extension metadata** in the top-level `CMakeLists.txt`, which still carries the
  wizard's "John Doe (AnyWare Corp.)" contributor and `example.com` URLs.
- **A real screenshot.** `ReformatViz.png` is still the extension wizard's placeholder icon,
  not a picture of the module.
- **How the software should be cited**, if at all. `CITATION.cff` was removed; making the
  software citable means adding it back, along with a DOI from an archive such as Zenodo, HAL
  or Software Heritage.
