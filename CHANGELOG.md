# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow
[Semantic Versioning](https://semver.org/).

Note that the **detection file format is not versioned**, and a change to it is a breaking
change for whatever produces those files. See `docs/detection-format.md`.

## [Unreleased]

## [0.1.0] - 2026-09-20

### Added

- **A review file, autosaved.** Verdicts, Likert scores, notes and view angles are written to
  `review_<reviewer>.json` in the case's directory as the reviewer works, and resumed when the
  case is opened again. The detection file is the detector's output and is no longer written
  to. Format documented in `docs/review-format.md`.
- **Undecided is a state.** The verdict is `—` / Yes / No / Uncertain / Not assessable instead
  of a checkbox, so a candidate nobody has looked at is no longer indistinguishable from one
  that was examined and rejected — which also means a half-reviewed case can no longer read as
  a negative one.
- **A rejection reason and a free-text note per candidate**, so a false-positive rate can be
  broken down into what the candidates actually were.
- **Sign off**, replacing **Save**: it declares a case finished, is refused while any candidate
  is undecided, and locks the verdicts. Reopening a signed-off case asks for a reason and
  records it, leaving the original sign-off in the file.
- **Provenance in every review**: who reviewed, which build of the module and of Slicer, when
  the case was opened and signed off, and the SHA-256 of the detection file it was reviewed
  against, so a detector rerun under an existing review is noticed instead of silently changing
  what the reviewer is held to have seen. The reviewer is the OS user name unless the
  `ReformatViz/Reviewer` setting says otherwise.
- **An append-only event log** in the review: every verdict, score, note, addition, sign-off and
  reopen with its timestamp, so a verdict that was changed leaves both decisions on record.
- **A sample case**, `data/sample_case/`: a synthetic phantom angiogram and five candidate
  detections, three of them aneurysms, so the module can be tried without a patient volume.
  Regenerated deterministically by `tools/make_sample_data.py`; described in `data/README.md`.
- Apache-2.0 licence and this changelog.
- `ReformatVizLib`, a Slicer-free package holding the geometry, detection I/O and display
  maths, so that logic can be tested without launching Slicer.
- `tests/`, a pytest suite covering `ReformatVizLib` (runs in seconds, no Slicer needed).
- `ReformatVizTest`, the in-Slicer self-test that `slicer_add_python_unittest` has always
  expected but which was never written.
- Documentation under `docs/`: user guide, detection file format, architecture, development.
- CI running the Slicer-free tests and the linter on every push, on both remotes: GitHub
  Actions (`.github/workflows/checks.yml`) and GitLab (`.gitlab-ci.yml`).

### Fixed

- The crop ROI was sized from the volume's corner-to-corner diagonal and centred on the world
  origin, so any tilted or off-centre volume was silently clipped: a 20° gantry tilt lost half
  the field of view, and a 45° rotation collapsed the ROI to zero width.
- Dragging the volume threshold to either end of the scalar range built an opacity ramp whose
  control points were out of order, which `vtkPiecewiseFunction` cannot represent; the ramp is
  now clamped to the range.
- A detection whose `P1`/`P2` was the wrong shape, non-numeric, `null` or `NaN` passed
  validation and then raised an uncaught `TypeError`, leaving the reviewer with a console
  traceback and an empty table instead of the dialog naming the entry. Values are now checked,
  not just key presence — see `docs/detection-format.md`.
- Saving truncated the target file when serialisation failed part-way through. Since the
  reviewer normally saves over the file they loaded, that cost them the session's verdicts;
  the write now goes to a temporary file and is renamed into place.
- Detection files are read and written as UTF-8 instead of the platform's locale encoding, so a
  file carrying a non-ASCII producer field no longer fails to load on Windows.
- An aneurysm whose dome sat directly below its ostium got the identity as its rotation frame,
  which maps the axis onto `-Z` rather than `+Z`, so the rotation slider turned the view the
  opposite way for those detections alone.
- A nearly vertical aneurysm axis produced a rotation frame that was not orthonormal, because
  the first row was scaled by a cancellation-prone `sqrt(1 - vz²)`; the slice normal handed to
  Slicer was then not a unit vector.
- Dragging an annotation point when only one of the two was placed read a control point that
  did not exist, so uninitialised memory became the preview sphere's centre and radius.
- Toggling the crop box before a volume was loaded asked the volume-rendering logic for the
  display node of a null volume.
- **Clean** left the volume-rendering display node and its volume property node behind in the
  scene, and the opacity and threshold sliders went on writing into the property of the volume
  that had just been removed.
- Detections the reviewer added by hand jumped to the top of the table after a save and reload,
  because they carry `confidence` 1; they now stay at the end, where the reviewer left them.
- A failed save reported nothing to the reviewer; it now shows the same kind of dialog a failed
  load does, and says the existing file was left unchanged.
- Pressing **OK** with fewer than two annotation points placed did nothing at all and left
  **Add** greyed out; it now says what is missing.
- Closing the scene from the File menu left the widget describing the scene that had just been
  removed: the table still listed the old patient's detections and the reviewer could go on
  scoring rows whose volume, spheres and lines were gone, until **Clean** or the next patient
  load. The module now observes scene close and resets its own state to match.
- Closing the scene from the File menu left the widget holding the three shared markups nodes
  after they had been removed, so ostia, domes and confidence labels silently stopped appearing;
  they are now recreated when needed.
- The slice-thickness slider and **Center camera** assumed the Red slice node and a 3D view
  exist, which is not true in a scene that has just been closed, nor in a headless session.
- The in-Slicer test asserted a slice origin that Slicer cannot honour in a scene with no
  volume, so `ctest -R ReformatViz` had never passed; it now builds a synthetic volume and
  checks every detection.
- Dome markups ignored the blue channel of the chosen colour (`SetColor(r, g, g)`).
- Clicking a table row ran the selection handler once per existing row, because the signal
  was reconnected on every row insertion.
- A detection file whose *later* entries lacked `P1`/`P2` passed validation and then crashed
  with an uncaught `KeyError`; every entry is now checked before use.
- Rejecting a detection file reported "No valid detection file found" with no reason; the
  dialog now names the file and the actual cause.

### Changed

- Non-ASCII text in the files the module writes is stored literally rather than
  `\uXXXX`-escaped, so a reviewer's note is legible to whoever opens the file.
- `detections.load` records the key each detection was filed under as `sourceKey`, which is
  what the review file is keyed by — see `docs/detection-format.md`.
- Annotation spheres are tessellated at 32×32 rather than 100×100 — a tenth of the geometry per
  detection, and a tenth of the work per mouse move while an annotation point is dragged, with
  no visible difference at this scale.
- Selecting a table row reformats the Red slice once instead of twice, and loading a volume
  builds the opacity ramp once instead of four times.

### Removed

- **The detection-file save path.** The module no longer writes the file it reviews: verdicts go
  to the review file instead. `detections.save`, its failure message and `set_likert_score` are
  gone with it, and `aneurysm` / `Likert` / `viewAngle` / `added` in a detection file are now
  legacy fields that are read but never written — see `docs/detection-format.md`.
- `getSphereCenterAndRadius`, plus the unused `logging` and `copy.deepcopy` imports.
- The parameter-node scaffolding inherited from the module template. This module keeps no
  parameter node, so `initializeParameterNode` was an empty `return` and `enter`, `exit` and
  `onSceneStartClose` were no-ops built around commented-out template code.
