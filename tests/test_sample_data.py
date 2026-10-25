# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""The committed sample case: still loadable, still consistent with what generated it.

The case in ``data/sample_case/`` is what a new user opens first, so it failing to load is a
bad first impression of the module. These tests guard the two ways that happens: the detection
file drifting from ``tools/make_sample_data.py`` (edited by hand, or the tool changed without
regenerating), and a candidate sitting outside the volume, where the reformatted slice runs
out of image beside the aneurysm and there is nothing to review.

The volume's *content* is not re-rasterised here — that takes seconds, and the geometry it is
drawn from is the same constants this file checks the detection file against.
"""

import gzip
import struct
from pathlib import Path

import make_sample_data as gen
import numpy as np
import pytest
from ReformatVizLib import detections as det

CASE = Path(__file__).parent.parent / "data" / "sample_case"
VOLUME = CASE / gen.VOLUME_NAME
DETECTIONS = CASE / gen.DETECTIONS_NAME


def read_nifti_header(path):
    """Return the fields of ``path``'s NIfTI-1 header that this module cares about.

    A reader for what :func:`make_sample_data.write_volume` writes, independent of the writer:
    it re-reads the offsets from the standard rather than sharing the packing code, so a
    header written to the wrong offset fails here instead of round-tripping.
    """
    with gzip.open(path, "rb") as f:
        header = f.read(348)
    (sizeof_hdr,) = struct.unpack_from("<i", header, 0)
    dim = struct.unpack_from("<8h", header, 40)
    pixdim = struct.unpack_from("<8f", header, 76)
    srow = [struct.unpack_from("<4f", header, offset) for offset in (280, 296, 312)]
    return {
        "sizeof_hdr": sizeof_hdr,
        "magic": struct.unpack_from("<4s", header, 344)[0],
        "datatype": struct.unpack_from("<h", header, 70)[0],
        "bitpix": struct.unpack_from("<h", header, 72)[0],
        "vox_offset": struct.unpack_from("<f", header, 108)[0],
        "sform_code": struct.unpack_from("<h", header, 254)[0],
        "xyzt_units": struct.unpack_from("<B", header, 123)[0],
        "ndim": dim[0],
        "shape": dim[1:4],
        "spacing": pixdim[1:4],
        "origin": tuple(row[3] for row in srow),
        "direction": [row[:3] for row in srow],
    }


@pytest.fixture(scope="module")
def header():
    return read_nifti_header(VOLUME)


@pytest.fixture(scope="module")
def loaded():
    """The sample case as the module sees it, straight through the real loader."""
    return det.load(DETECTIONS)


class TestTheCaseIsPresent:
    def test_both_files_are_committed(self):
        assert VOLUME.is_file()
        assert DETECTIONS.is_file()

    def test_the_volume_stays_small_enough_to_keep_in_git(self):
        assert VOLUME.stat().st_size < 1_000_000


class TestTheDetectionFile:
    def test_loads_through_the_real_loader(self, loaded):
        assert len(loaded) == len(gen.SACS) + len(gen.FALSE_POSITIVES)

    def test_is_exactly_what_the_generator_produces(self):
        # Byte-for-byte, so a hand-edit of the sample or a change to the tool that was never
        # regenerated both show up here rather than in Slicer.
        assert DETECTIONS.read_text() == gen.dumps(gen.detection_file())

    def test_carries_only_the_fields_a_producer_supplies(self):
        for d in gen.detection_file().values():
            assert set(d) == {"P1", "P2", "confidence"}

    def test_gives_away_nothing_about_which_candidates_are_real(self):
        # The keys are the only free text in the file, and the reviewer sees the file.
        assert set(gen.detection_file()) == {f"candidate_{i}" for i in range(5)}

    def test_offers_both_aneurysms_and_false_positives(self):
        assert gen.SACS and gen.FALSE_POSITIVES

    def test_confidences_are_distinct_so_the_table_order_is_stable(self, loaded):
        confidences = [d["confidence"] for d in loaded]
        assert len(set(confidences)) == len(confidences)
        assert confidences == sorted(confidences, reverse=True)


class TestTheVolume:
    def test_is_a_single_file_nifti1_of_int16(self, header):
        assert header["sizeof_hdr"] == 348
        assert header["magic"] == b"n+1\0"
        assert header["datatype"] == 4  # DT_INT16
        assert header["bitpix"] == 16
        assert header["vox_offset"] == 352

    def test_declares_its_geometry_in_millimetres(self, header):
        assert header["ndim"] == 3
        assert header["sform_code"] == 1  # NIFTI_XFORM_SCANNER_ANAT
        assert header["xyzt_units"] == 2  # NIFTI_UNITS_MM
        assert header["spacing"] == pytest.approx((gen.SPACING,) * 3)

    def test_maps_voxels_to_the_ras_millimetres_the_detections_use(self, header):
        assert header["direction"] == pytest.approx(np.diag([gen.SPACING] * 3))
        for (low, _), origin in zip(gen.BOUNDS, header["origin"], strict=True):
            assert origin == pytest.approx(low + gen.SPACING / 2)

    def test_covers_the_extent_it_was_authored_in(self, header):
        for (low, high), size in zip(gen.BOUNDS, header["shape"], strict=True):
            assert size == round((high - low) / gen.SPACING)


class TestEveryCandidateCanBeReviewed:
    """A candidate is reviewable only if there is image around it, not merely inside it."""

    #: How much image a candidate needs beyond its own sphere, in mm. The reviewer spins the
    #: slice around the aneurysm axis, so a sac against the volume face has no surrounding
    #: anatomy to judge it against at some angles.
    MARGIN = 3.0

    def bounds_of(self, header):
        origin = np.array(header["origin"])
        return origin, origin + (np.array(header["shape"]) - 1) * gen.SPACING

    def test_every_sphere_sits_inside_the_volume_with_room_to_spare(self, loaded, header):
        low, high = self.bounds_of(header)
        for d in loaded:
            reach = d["radius"] + self.MARGIN
            assert np.all(np.array(d["center"]) - reach >= low), d[det.SOURCE_KEY]
            assert np.all(np.array(d["center"]) + reach <= high), d[det.SOURCE_KEY]

    def test_the_axis_of_every_candidate_has_a_length_to_reformat_around(self, loaded):
        # prepare_slices normalises P2 - P1; coincident points would leave no axis at all.
        for d in loaded:
            assert np.linalg.norm(np.array(d["P2"]) - d["P1"]) > 1.0

    def test_the_drawn_sacs_are_the_ones_the_file_offers(self, loaded):
        # The sphere the module derives from P1/P2 is the sphere the generator rasterised, so
        # the overlay lands on the sac. This is the join that makes the sample worth shipping.
        def sphere(ostium, dome):
            ostium, dome = np.array(ostium, dtype=float), np.array(dome, dtype=float)
            centre = tuple(np.round((ostium + dome) / 2, 6))
            return centre, round(float(np.linalg.norm(dome - ostium) / 2), 6)

        drawn = {sphere(s["ostium"], s["dome"]) for s in gen.SACS}
        offered = {sphere(d["P1"], d["P2"]) for d in loaded}
        assert drawn <= offered

        derived = {(tuple(np.round(d["center"], 6)), round(float(d["radius"]), 6)) for d in loaded}
        assert drawn <= derived
