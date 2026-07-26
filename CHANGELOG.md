# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project has not been
released yet; once it is, versions follow [Semantic Versioning](https://semver.org/).

Note that the **detection file format is not versioned**, and a change to it is a breaking
change for whatever produces those files. See `docs/detection-format.md`.

## [Unreleased]

### Added

- Apache-2.0 licence, `CITATION.cff`, and this changelog.
- `ReformatVizLib`, a Slicer-free package holding the geometry, detection I/O and display
  maths, so that logic can be tested without launching Slicer.
- `tests/`, a pytest suite covering `ReformatVizLib` (runs in seconds, no Slicer needed).
- `ReformatVizTest`, the in-Slicer self-test that `slicer_add_python_unittest` has always
  expected but which was never written.
- Documentation under `docs/`: user guide, detection file format, architecture, development.
- GitLab CI running the Slicer-free tests and the linter on every push.

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
- Dome markups ignored the blue channel of the chosen colour (`SetColor(r, g, g)`).
- Clicking a table row ran the selection handler once per existing row, because the signal
  was reconnected on every row insertion.
- A detection file whose *later* entries lacked `P1`/`P2` passed validation and then crashed
  with an uncaught `KeyError`; every entry is now checked before use.
- Rejecting a detection file reported "No valid detection file found" with no reason; the
  dialog now names the file and the actual cause.

### Removed

- `getSphereCenterAndRadius`, plus the unused `logging` and `copy.deepcopy` imports.
