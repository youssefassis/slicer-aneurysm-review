# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""The reformatting geometry.

The property that matters clinically is in :class:`TestSliceContainsTheAneurysmAxis`: at
every rotation angle the reviewer can dial, the slice must still contain the aneurysm axis.
If that breaks, the reviewer is measuring a foreshortened aneurysm without being told.
"""

import math

import numpy as np
import pytest
from ReformatVizLib import geometry as geo

ANGLES = [-180, -90, -37.5, 0, 1, 45, 90, 179, 180]


class TestRotate2Z:
    def test_maps_the_vector_onto_plus_z(self):
        v = np.array([1.0, 2.0, 3.0])
        mapped = geo.rotate2Z(v) @ v
        assert mapped[2] == pytest.approx(np.linalg.norm(v))
        assert mapped[:2] == pytest.approx([0, 0])

    @pytest.mark.parametrize("v", [
        [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0],
        [1.0, 1.0, 1.0], [-3.0, 0.5, 2.0], [0.0, 0.0, -1.0],
    ])
    def test_is_a_rotation_matrix(self, v):
        R = geo.rotate2Z(v)
        assert R @ R.T == pytest.approx(np.eye(3))
        assert np.linalg.det(R) == pytest.approx(1.0)

    def test_third_row_is_the_unit_input(self):
        v = [0.0, 3.0, 4.0]
        assert geo.rotate2Z(v)[2] == pytest.approx([0, 0.6, 0.8])

    def test_scale_does_not_matter(self):
        assert geo.rotate2Z([1.0, 2.0, 3.0]) == pytest.approx(geo.rotate2Z([10.0, 20.0, 30.0]))

    @pytest.mark.parametrize("v", [
        [1e-7, 0.0, 1.0], [0.0, 1e-7, 1.0], [1e-7, 1e-7, 1.0], [1e-7, 0.0, -1.0],
    ])
    def test_stays_orthonormal_for_a_nearly_vertical_axis(self, v):
        # sqrt(1 - vz**2) keeps almost no precision when |vz| is close to 1, which left the
        # first row over 1% too long — so the "unit" slice normal handed to Slicer was not one.
        R = geo.rotate2Z(v)
        assert R @ R.T == pytest.approx(np.eye(3), abs=1e-12)
        assert np.linalg.det(R) == pytest.approx(1.0, abs=1e-12)
        assert np.linalg.norm(geo.slice_normal(R, 45)) == pytest.approx(1.0, abs=1e-12)

    def test_degenerate_vector_falls_back_to_identity(self):
        # A usable frame matters more than signalling the error: a zero-length axis means
        # P1 and P2 coincide, which prepare_slices already guards against.
        assert geo.rotate2Z([0.0, 0.0, 0.0]) == pytest.approx(np.eye(3))


class TestAneurysmFrame:
    def test_axis_is_the_unit_ostium_to_dome_direction(self):
        axis, _, _ = geo.aneurysm_frame([0, 0, 0], [0, 0, 5])
        assert axis == pytest.approx([0, 0, 1])

    def test_origin_is_the_midpoint(self):
        _, origin, _ = geo.aneurysm_frame([0, 0, 0], [4, 2, -6])
        assert origin == pytest.approx([2, 1, -3])

    def test_rz_matches_the_axis(self):
        axis, _, Rz = geo.aneurysm_frame([1, 1, 1], [2, 3, 5])
        assert Rz[2] == pytest.approx(axis)

    def test_coincident_points_fall_back_to_the_default_axis(self):
        axis, origin, _ = geo.aneurysm_frame([7, 7, 7], [7, 7, 7])
        assert axis == pytest.approx(geo.DEFAULT_AXIS)
        assert origin == pytest.approx([7, 7, 7])

    def test_does_not_mutate_its_arguments(self):
        P1, P2 = [0, 0, 0], [0, 0, 5]
        geo.aneurysm_frame(P1, P2)
        assert (P1, P2) == ([0, 0, 0], [0, 0, 5])

    def test_default_frame_sits_at_the_origin(self):
        axis, origin, Rz = geo.default_frame()
        assert axis == pytest.approx(geo.DEFAULT_AXIS)
        assert origin == pytest.approx([0, 0, 0])
        assert Rz[2] == pytest.approx(geo.DEFAULT_AXIS)


class TestSliceContainsTheAneurysmAxis:
    """The invariant the whole reformatting exists to hold."""

    @pytest.mark.parametrize("angle", ANGLES)
    @pytest.mark.parametrize("P1, P2", [
        ([0, 0, 0], [0, 0, 5]),
        ([0, 0, 0], [1, 0, 0]),
        ([-12.5, 18.0, 42.25], [-9.5, 22.0, 42.25]),
        ([3, -4, 12], [-7, 2, 0.5]),
    ])
    def test_normal_stays_perpendicular_to_the_axis_at_every_angle(self, P1, P2, angle):
        axis, _, Rz = geo.aneurysm_frame(P1, P2)
        normal = geo.slice_normal(Rz, angle)
        assert normal @ axis == pytest.approx(0, abs=1e-12)

    @pytest.mark.parametrize("angle", ANGLES)
    def test_normal_is_a_unit_vector(self, angle):
        _, _, Rz = geo.aneurysm_frame([0, 0, 0], [1, 2, 3])
        assert np.linalg.norm(geo.slice_normal(Rz, angle)) == pytest.approx(1.0)

    def test_the_angle_actually_turns_the_view(self):
        _, _, Rz = geo.aneurysm_frame([0, 0, 0], [0, 0, 1])
        assert geo.slice_normal(Rz, 0) @ geo.slice_normal(Rz, 90) == pytest.approx(0, abs=1e-12)
        assert geo.slice_normal(Rz, 0) @ geo.slice_normal(Rz, 180) == pytest.approx(-1)

    def test_a_full_turn_returns_to_the_start(self):
        _, _, Rz = geo.aneurysm_frame([0, 0, 0], [1, 1, 0])
        assert geo.slice_normal(Rz, 30) == pytest.approx(geo.slice_normal(Rz, 390))


class TestInPlaneRoll:
    def test_no_roll_needed_when_the_axis_already_points_along_slice_y(self):
        axis = np.array([0.0, 1.0, 0.0])
        assert geo.in_plane_roll_deg([1, 0, 0], [0, 1, 0], axis) == pytest.approx(0)

    def test_quarter_turn_when_the_axis_points_along_slice_x(self):
        axis = np.array([1.0, 0.0, 0.0])
        assert abs(geo.in_plane_roll_deg([1, 0, 0], [0, 1, 0], axis)) == pytest.approx(90)

    def test_sign_follows_which_side_the_axis_leans_to(self):
        slice_x, slice_y = [1, 0, 0], [0, 1, 0]
        leaning = 1 / math.sqrt(2)
        positive = geo.in_plane_roll_deg(slice_x, slice_y, np.array([leaning, leaning, 0]))
        negative = geo.in_plane_roll_deg(slice_x, slice_y, np.array([-leaning, leaning, 0]))
        assert positive == pytest.approx(-45)
        assert negative == pytest.approx(45)

    def test_rolling_by_the_result_puts_the_axis_on_slice_y(self):
        # Rotating the slice axes by -roll must align slice +Y with the aneurysm axis.
        axis = np.array([0.6, 0.8, 0.0])
        slice_x, slice_y = np.array([1.0, 0, 0]), np.array([0, 1.0, 0])
        roll = math.radians(geo.in_plane_roll_deg(slice_x, slice_y, axis))
        rolled_y = math.sin(-roll) * slice_x + math.cos(-roll) * slice_y
        assert rolled_y == pytest.approx(axis)


class TestCameraPosition:
    def test_sits_behind_the_target_along_the_normal(self):
        assert geo.camera_position([0, 0, 0], [0, 0, 1], 10) == pytest.approx([0, 0, -10])

    def test_keeps_the_requested_distance(self):
        origin, normal = np.array([5.0, -2.0, 3.0]), np.array([0.0, 1.0, 0.0])
        position = geo.camera_position(origin, normal, 42)
        assert np.linalg.norm(origin - position) == pytest.approx(42)

    def test_a_zero_distance_lands_on_the_target(self):
        assert geo.camera_position([1, 2, 3], [0, 1, 0], 0) == pytest.approx([1, 2, 3])


def bounds_from_corners(ijk_to_ras, dimensions):
    """Brute-force ``(center, size)`` from the eight corners of the voxel box."""
    m = np.asarray(ijk_to_ras, dtype=np.float64)
    x, y, z = dimensions
    corners = np.array([[i, j, k, 1.0]
                        for i in (0.0, float(x))
                        for j in (0.0, float(y))
                        for k in (0.0, float(z))])
    ras = (m @ corners.T).T[:, :3]
    low, high = ras.min(axis=0), ras.max(axis=0)
    return (low + high) / 2, high - low


def rotation_about_z(degrees):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    m = np.eye(4)
    m[:2, :2] = [[c, -s], [s, c]]
    return m


class TestVolumeBounds:
    def test_isotropic_millimetre_volume(self):
        center, size = geo.volume_bounds(np.eye(4), (256, 256, 100))
        assert size == pytest.approx([256, 256, 100])
        assert center == pytest.approx([128, 128, 50])

    def test_accounts_for_anisotropic_spacing(self):
        ijk_to_ras = np.diag([0.5, 0.5, 2.0, 1.0])
        _, size = geo.volume_bounds(ijk_to_ras, (100, 100, 50))
        assert size == pytest.approx([50, 50, 100])

    def test_size_is_positive_whatever_the_axis_directions(self):
        # LPS-to-RAS flips put negative entries on the diagonal; a ROI size must not be negative.
        ijk_to_ras = np.diag([-0.4, -0.4, 0.8, 1.0])
        _, size = geo.volume_bounds(ijk_to_ras, (512, 512, 200))
        assert size == pytest.approx([204.8, 204.8, 160])

    def test_translation_moves_the_centre_and_leaves_the_size_alone(self):
        m = np.eye(4)
        m[:3, 3] = [-120, 40, 7]
        center, size = geo.volume_bounds(m, (64, 64, 64))
        assert size == pytest.approx([64, 64, 64])
        assert center == pytest.approx([-88, 72, 39])

    @pytest.mark.parametrize("angle", [20, 45, -37.5])
    def test_a_tilted_volume_is_not_shrunk(self, angle):
        # A gantry-tilted head CTA. The size must stay the bounding box of the tilted voxel
        # box; anything smaller crops the volume rendering without telling the reviewer.
        m = rotation_about_z(angle) @ np.diag([0.4, 0.4, 0.5, 1.0])
        dims = (512, 512, 300)
        center, size = geo.volume_bounds(m, dims)
        expected_center, expected_size = bounds_from_corners(m, dims)
        assert size == pytest.approx(expected_size)
        assert center == pytest.approx(expected_center)

    def test_matches_the_corner_bounding_box_for_an_oblique_matrix(self):
        # Rotation, anisotropic spacing and an off-centre origin at once — the general case
        # the closed form has to agree with.
        m = np.eye(4)
        m[:3, :3] = geo.rotate2Z([1.0, 2.0, 3.0]) @ np.diag([0.35, 0.35, 0.6])
        m[:3, 3] = [-95.0, 12.5, 60.0]
        dims = (320, 320, 180)
        center, size = geo.volume_bounds(m, dims)
        expected_center, expected_size = bounds_from_corners(m, dims)
        assert size == pytest.approx(expected_size)
        assert center == pytest.approx(expected_center)
