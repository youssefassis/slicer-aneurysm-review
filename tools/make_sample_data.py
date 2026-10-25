# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""Regenerate the sample case in ``data/sample_case/``.

The case is a synthetic angiogram — bright vessels on a dark background, the way a
subtracted or MR angiogram looks — plus the detection file a detector would produce for it.
No patient data is involved, which is why the result can be committed.

Both files come out of the constants below, so a candidate's ``P1``/``P2`` cannot drift away
from the sac drawn in the image: :data:`SACS` is what gets rasterised *and* what gets written
to the detection file. :data:`FALSE_POSITIVES` is written to the detection file only — the
reviewer is meant to look at those and reject them.

The output is deterministic: same constants, same bytes. Regenerating it should leave
``git status`` clean.

Run it with an interpreter that has numpy — the dev environment, or Slicer's:

    python tools/make_sample_data.py
    /Applications/Slicer.app/Contents/bin/PythonSlicer tools/make_sample_data.py

This is a development tool, not part of the extension: it is not installed by
``ReformatViz/CMakeLists.txt`` and nothing in ``ReformatViz/`` imports it.
"""

import gzip
import itertools
import json
import struct
from pathlib import Path

import numpy as np

#: Where the case is written, relative to the repository root.
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "sample_case"

VOLUME_NAME = "angio.nii.gz"
DETECTIONS_NAME = "detections.json"

#: Voxel size in mm, isotropic — around what a 3D-rotational angiogram is reconstructed at.
SPACING = 0.5

#: Volume extent in RAS millimetres: a 68 x 60 x 50 mm block around the circle of Willis.
#: Everything below is authored in these coordinates, which are also the coordinates the
#: detection file speaks (see docs/detection-format.md). Wide enough in x that the lateral
#: aneurysm keeps a margin of image around it — a candidate against the edge of the volume
#: cannot be reviewed, because the reformatted slice runs out of data beside it.
BOUNDS = ((-34.0, 34.0), (-30.0, 30.0), (-20.0, 30.0))

#: Intensities. Arbitrary units, like an angiogram that has been windowed: dark tissue,
#: bright contrast-filled lumen.
BACKGROUND = 40
VESSEL = 900

#: Peak-to-peak amplitude of the background texture, and how many voxels one grain spans.
#: Grainy rather than per-voxel noise: it looks like an acquisition and it compresses, which
#: keeps the committed volume small.
NOISE = 36
NOISE_GRAIN = 4

#: Fixed so the volume is reproducible byte for byte.
SEED = 20250727

#: Width in mm of the soft edge on every structure, so surfaces do not come out as stair
#: steps under maximum-intensity projection.
EDGE = 1.5 * SPACING

#: The arterial tree: ``(radius in mm, centre-line through RAS points)``. A crude circle of
#: Willis — enough that a reviewer is looking at a bifurcation rather than at a lone blob,
#: not an anatomical model.
VESSELS = (
    # Internal carotid arteries, rising through the siphon to the terminus.
    (2.0, ((16, -12, -16), (15, -8, -10), (14, -2, -6), (13, 3, -3), (13, 4.5, 1), (13, 5, 4))),
    (2.0, ((-16, -12, -16), (-15, -8, -10), (-14, -2, -6), (-13, 3, -3), (-13, 4.5, 1), (-13, 5, 4))),
    # Middle cerebral arteries: M1 laterally to the bifurcation, then two M2 branches. The
    # bifurcation sits well inside the volume so the aneurysm on the right one has image
    # around it.
    (1.6, ((13, 5, 4), (17, 4, 5), (21, 3.5, 6), (24, 3.5, 6.8))),
    (1.1, ((24, 3.5, 6.8), (25, 1.5, 12), (25.5, -1, 18))),
    (1.1, ((24, 3.5, 6.8), (25, 6, 10), (24.5, 9, 15))),
    (1.6, ((-13, 5, 4), (-17, 4, 5), (-21, 3.5, 6), (-24, 3.5, 6.8))),
    (1.1, ((-24, 3.5, 6.8), (-25, 1.5, 12), (-25.5, -1, 18))),
    (1.1, ((-24, 3.5, 6.8), (-25, 6, 10), (-24.5, 9, 15))),
    # Anterior cerebral arteries: A1 medially, the communicating artery across the midline,
    # then A2 climbing anteriorly.
    (1.3, ((13, 5, 4), (9, 9, 6), (4, 11, 7), (2, 11.5, 7.5))),
    (1.3, ((-13, 5, 4), (-9, 9, 6), (-4, 11, 7), (-2, 11.5, 7.5))),
    (0.8, ((2, 11.5, 7.5), (0, 12, 7.6), (-2, 11.5, 7.5))),
    (1.1, ((2, 11.5, 7.5), (2, 14, 12), (2.5, 15, 20), (2, 14, 26))),
    (1.1, ((-2, 11.5, 7.5), (-2, 14, 12), (-2.5, 15, 20), (-2, 14, 26))),
    # Vertebral arteries joining into the basilar, and the posterior cerebral arteries.
    (1.1, ((5, -21, -19.5), (2, -20, -19), (0, -19, -19))),
    (1.1, ((-5, -21, -19.5), (-2, -20, -19), (0, -19, -19))),
    (1.5, ((0, -19, -19), (0, -18, -12), (0.5, -16, -5), (0, -14, 0), (0, -13.5, 2))),
    (1.1, ((0, -13.5, 2), (5, -13, 4), (11, -11, 6), (16, -13, 8))),
    (1.1, ((0, -13.5, 2), (-5, -13, 4), (-11, -11, 6), (-16, -13, 8))),
)

#: The aneurysms. Each is drawn as a sac from its ostium to its dome *and* offered to the
#: reviewer as a candidate, so what the detection file claims is what the image shows.
#: ``note`` is for ``data/README.md``, not for the detection file.
#:
#: The sac is the sphere spanning ostium to dome — which is exactly the sphere the module
#: derives from ``P1``/``P2`` and draws in the views, so the overlay lands on the sac rather
#: than near it. A sac therefore only shows above its parent vessel if the ostium is on the
#: vessel *surface*, or at a bifurcation apex where there is no vessel in the way.
SACS = (
    {
        "ostium": (24.0, 3.5, 6.8),
        "dome": (29.0, 2.6, 9.0),
        "confidence": 0.93,
        "note": "right MCA bifurcation, 5.5 mm — the unmistakable one",
    },
    {
        "ostium": (0.0, 12.0, 8.0),
        "dome": (0.8, 15.7, 11.2),
        "confidence": 0.81,
        "note": "anterior communicating artery, 5 mm, pointing anterosuperiorly",
    },
    {
        "ostium": (-14.5, 6.0, 4.9),
        "dome": (-16.6, 7.4, 6.1),
        "confidence": 0.44,
        "note": "left ICA terminus, 2.8 mm — small enough to be a genuine judgement call",
    },
)

#: Candidates with nothing behind them, so the sample exercises a rejection as well as an
#: acceptance. Written to the detection file; never drawn.
FALSE_POSITIVES = (
    {
        "ostium": (0.4, -16.0, -5.0),
        "dome": (2.6, -14.6, -2.4),
        "confidence": 0.62,
        "note": "a bend in the basilar artery — vessel, not a sac",
    },
    {
        "ostium": (-20.8, -9.6, -7.0),
        "dome": (-19.2, -8.4, -5.0),
        "confidence": 0.19,
        "note": "the isolated blob: unconnected to any artery, so not an aneurysm",
    },
)

#: Drawn but not offered as a candidate: what the low-confidence false positive sits on.
BLOBS = ((1.5, (-20.0, -9.0, -6.0)),)


def _midpoint(a, b):
    return (np.asarray(a, dtype=float) + np.asarray(b, dtype=float)) / 2


def _grid():
    """Return the RAS coordinate of every voxel centre, and the volume's shape and origin."""
    axes = [np.arange(lo + SPACING / 2, hi, SPACING, dtype=np.float32) for lo, hi in BOUNDS]
    origin = tuple(float(axis[0]) for axis in axes)
    x, y, z = np.meshgrid(*axes, indexing="ij")
    return (x, y, z), tuple(len(axis) for axis in axes), origin


def _fill_capsule(fill, grid, a, b, radius):
    """Raise ``fill`` towards 1 inside the capsule of ``radius`` around segment ``a``-``b``.

    A capsule rather than a cylinder so consecutive segments of a centre-line join without a
    notch at the corner. The fraction is a soft ramp across :data:`EDGE`, which is what keeps
    the surfaces from looking like stair steps in the reformatted view.
    """
    x, y, z = grid
    a = np.asarray(a, dtype=np.float32)
    ab = np.asarray(b, dtype=np.float32) - a
    px, py, pz = x - a[0], y - a[1], z - a[2]

    # A sphere is drawn as a zero-length segment: the floor keeps the division defined, and
    # the numerator is zero there too, so the projection stays at ``a``.
    length2 = max(float(ab @ ab), 1e-12)
    t = np.clip((px * ab[0] + py * ab[1] + pz * ab[2]) / length2, 0.0, 1.0)

    distance = np.sqrt((px - t * ab[0]) ** 2 + (py - t * ab[1]) ** 2 + (pz - t * ab[2]) ** 2)
    np.maximum(fill, np.clip((radius + EDGE / 2 - distance) / EDGE, 0.0, 1.0), out=fill)


def _build_volume(grid, shape):
    """Return the phantom as ``int16``, indexed ``[i, j, k]``."""
    fill = np.zeros(shape, dtype=np.float32)

    for radius, centreline in VESSELS:
        for a, b in itertools.pairwise(centreline):
            _fill_capsule(fill, grid, a, b, radius)

    for sac in SACS:
        # The sac as a sphere on the ostium-to-dome axis, plus a short neck of the same
        # radius, which is what stops it reading as a ball floating beside the vessel.
        centre = _midpoint(sac["ostium"], sac["dome"])
        radius = float(np.linalg.norm(np.asarray(sac["dome"], dtype=float) - sac["ostium"]) / 2)
        _fill_capsule(fill, grid, centre, centre, radius)
        _fill_capsule(fill, grid, sac["ostium"], centre, radius * 0.55)

    for radius, centre in BLOBS:
        _fill_capsule(fill, grid, centre, centre, radius)

    volume = BACKGROUND + (VESSEL - BACKGROUND) * fill
    volume += _texture(shape)
    return np.rint(volume).astype(np.int16)


def _texture(shape):
    """Return grainy background texture of the shape given.

    Drawn at one value per :data:`NOISE_GRAIN` voxels and repeated, so it reads as acquisition
    texture and still compresses — per-voxel noise would multiply the size of the committed
    volume.
    """
    rng = np.random.default_rng(SEED)
    coarse = tuple(-(-n // NOISE_GRAIN) for n in shape)
    grain = rng.uniform(-NOISE / 2, NOISE / 2, size=coarse).astype(np.float32)
    for axis in range(grain.ndim):
        grain = np.repeat(grain, NOISE_GRAIN, axis=axis)
    return grain[: shape[0], : shape[1], : shape[2]]


def _nifti_header(shape, origin):
    """Return a 348-byte NIfTI-1 header for an ``int16`` volume with an identity direction.

    Hand-packed rather than written with nibabel, which the project does not depend on and
    would not use anywhere else. Fields are placed at the offsets fixed by the NIfTI-1
    standard (nifti1.h); everything not set stays zero, which is its documented default.

    The ``sform`` is a plain diagonal voxel-to-RAS mapping, so voxel ``(i, j, k)`` sits at
    ``origin + (i, j, k) * SPACING`` in the RAS millimetres the detection file uses.
    """
    header = bytearray(348)
    struct.pack_into("<i", header, 0, 348)
    struct.pack_into("<c", header, 38, b"r")
    struct.pack_into("<8h", header, 40, 3, *shape, 1, 1, 1, 1)
    struct.pack_into("<h", header, 70, 4)  # datatype: DT_INT16
    struct.pack_into("<h", header, 72, 16)  # bitpix
    struct.pack_into("<8f", header, 76, 1.0, SPACING, SPACING, SPACING, 0.0, 0.0, 0.0, 0.0)
    struct.pack_into("<f", header, 108, 352.0)  # vox_offset: header + 4 extension bytes
    struct.pack_into("<f", header, 112, 1.0)  # scl_slope
    struct.pack_into("<B", header, 123, 2)  # xyzt_units: NIFTI_UNITS_MM
    struct.pack_into("<80s", header, 148, b"ReformatViz synthetic sample case")
    struct.pack_into("<h", header, 254, 1)  # sform_code: NIFTI_XFORM_SCANNER_ANAT
    struct.pack_into("<4f", header, 280, SPACING, 0.0, 0.0, origin[0])
    struct.pack_into("<4f", header, 296, 0.0, SPACING, 0.0, origin[1])
    struct.pack_into("<4f", header, 312, 0.0, 0.0, SPACING, origin[2])
    struct.pack_into("<4s", header, 344, b"n+1\0")
    return bytes(header)


def write_volume(path, volume, origin):
    """Write ``volume`` to ``path`` as a gzipped single-file NIfTI-1.

    ``mtime=0`` because the default stamps the current time into the gzip header, which would
    make every regeneration a new blob in git for no change in content.
    """
    payload = (
        _nifti_header(volume.shape, origin)
        + bytes(4)  # extension flags: none
        + volume.astype("<i2").tobytes(order="F")  # NIfTI stores the first axis fastest
    )
    with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as f:
        f.write(payload)


def detection_file():
    """Return the detection file's content: every candidate, most confident first.

    Only the three fields a producer is expected to supply — ``P1``, ``P2`` and
    ``confidence``. Everything else the module needs is derived on load, and the keys are
    deliberately uninformative, so nothing here tells the reviewer which candidates are the
    real ones.
    """
    candidates = sorted(SACS + FALSE_POSITIVES, key=lambda c: -c["confidence"])
    return {
        f"candidate_{i}": {
            "P1": list(c["ostium"]),
            "P2": list(c["dome"]),
            "confidence": c["confidence"],
        }
        for i, c in enumerate(candidates)
    }


#: One detection, formatted with its positions on one line each. ``json.dumps(indent=2)``
#: would split every coordinate across three lines, and the file is meant to be read as a
#: producer's template — ``tests/data/detections_sample.json`` reads this way too.
_ENTRY = '  "{key}": {{\n    "P1": {P1},\n    "P2": {P2},\n    "confidence": {confidence}\n  }}'


def dumps(detections):
    """Return ``detections`` as the text of a detection file."""
    entries = (
        _ENTRY.format(key=key, **{field: json.dumps(value) for field, value in d.items()})
        for key, d in detections.items()
    )
    return "{\n" + ",\n".join(entries) + "\n}\n"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    grid, shape, origin = _grid()

    volume_path = OUT_DIR / VOLUME_NAME
    write_volume(volume_path, _build_volume(grid, shape), origin)

    detections_path = OUT_DIR / DETECTIONS_NAME
    detections_path.write_text(dumps(detection_file()))

    print(
        f"{volume_path}: {shape[0]}x{shape[1]}x{shape[2]} at {SPACING} mm, {volume_path.stat().st_size / 1024:.0f} KiB"
    )
    print(f"{detections_path}: {len(detection_file())} candidates")


if __name__ == "__main__":
    main()
