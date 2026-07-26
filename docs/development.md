# Development

## The two environments

This is the thing to internalise before anything else.

|  | Slicer's Python | Your dev Python |
| --- | --- | --- |
| Runs | the module, in the GUI | `pytest`, `ruff` |
| Provides | numpy, VTK, Qt, CTK, `slicer` | whatever `requirements-dev.txt` pins |
| Managed by | the Slicer installer | you |

You do **not** pip-install into Slicer. Extensions are loaded from disk, and Slicer supplies
their dependencies. That is why `pyproject.toml` declares no runtime dependencies and no
build backend — a pin there would describe nothing real. `requirements-dev.txt` is only for
running the tests and linter outside Slicer.

It also means **reproducibility comes from pinning the Slicer version**, not from pinning
Python packages.

> **Pin the Slicer version.** The supported version is not yet decided. Pick the release the
> lab actually reviews with, record it in `README.md`, and pin the same version in the
> commented `slicer-tests` job in `.gitlab-ci.yml`. Check its interpreter with
> `slicer.app.pythonVersion` in the Slicer Python console and set `requires-python` in
> `pyproject.toml` to match — it is likely older than your machine's Python.

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

Then iterate without restarting: open the module, use the **Reload** button in *Developer
Tools*, or `Ctrl+R` / `Cmd+R`. Note that reloading re-runs `__init__`, which clears the scene.

The Python console (*View → Python Console*) is the fastest way to poke at live state:

```python
w = slicer.modules.ReformatVizWidget
len(w.detections), w.selected_detection
w.aneAxis, w.aneOrigin
```

### The in-Slicer test

`ReformatVizTest` in `ReformatViz.py` covers what genuinely needs MRML. Run it from the
module's **Reload and Test** button, or:

```bash
Slicer --additional-module-paths ReformatViz --python-code \
  "slicer.util.selectModule('ReformatViz'); slicer.modules.ReformatVizWidget.parent.moduleTest().runTest()"
```

It reads `tests/data/detections_sample.json` from the source tree and skips those cases if
run from an installed extension where that file is absent. Keep it thin — new cases belong in
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
