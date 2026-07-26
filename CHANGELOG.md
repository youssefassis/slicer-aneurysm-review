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

- Dome markups ignored the blue channel of the chosen colour (`SetColor(r, g, g)`).
- Clicking a table row ran the selection handler once per existing row, because the signal
  was reconnected on every row insertion.
- A detection file whose *later* entries lacked `P1`/`P2` passed validation and then crashed
  with an uncaught `KeyError`; every entry is now checked before use.
- Rejecting a detection file reported "No valid detection file found" with no reason; the
  dialog now names the file and the actual cause.

### Removed

- `getSphereCenterAndRadius`, plus the unused `logging` and `copy.deepcopy` imports.
