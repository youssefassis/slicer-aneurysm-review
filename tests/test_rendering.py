# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""Colour conversion and the volume-rendering opacity ramp."""

import pytest
from ReformatVizLib import rendering


class FakeQColor:
    """Stands in for a QColor, which only exists inside Slicer."""

    def __init__(self, r, g, b):
        self._rgb = (r, g, b)

    def red(self):
        return self._rgb[0]

    def green(self):
        return self._rgb[1]

    def blue(self):
        return self._rgb[2]


class TestToRGB:
    def test_scales_bytes_to_the_unit_range(self):
        assert rendering.to_rgb(FakeQColor(255, 128, 0)) == pytest.approx([1.0, 128 / 255, 0.0])

    def test_black_and_white_land_on_the_ends(self):
        assert rendering.to_rgb(FakeQColor(0, 0, 0)) == (0, 0, 0)
        assert rendering.to_rgb(FakeQColor(255, 255, 255)) == (1, 1, 1)

    def test_channels_are_not_transposed(self):
        # A (r, g, g) style slip here silently drops one channel of the reviewer's choice.
        assert rendering.to_rgb(FakeQColor(10, 20, 30)) == pytest.approx([10 / 255, 20 / 255, 30 / 255])


class TestScalarOpacityPoints:
    def ramp(self, **kwargs):
        params = dict(min_value=0, max_value=1000, threshold=300, opacity=0.8,
                      margin=20, translucent=False)
        params.update(kwargs)
        return rendering.scalar_opacity_points(**params)

    def test_has_the_fixed_number_of_control_points(self):
        # The ramp is updated in place, so the count must match what makeScalarOpacity built.
        assert len(self.ramp()) == rendering.SCALAR_OPACITY_POINT_COUNT

    def test_scalar_values_are_non_decreasing(self):
        xs = [p[0] for p in self.ramp()]
        assert xs == sorted(xs)

    def test_spans_the_full_scalar_range(self):
        points = self.ramp(min_value=-1024, max_value=3071)
        assert points[0][0] == -1024
        assert points[-1][0] == 3071

    def test_transparent_below_the_threshold_margin(self):
        points = self.ramp()
        assert points[0][1] == 0
        assert points[1][1] == 0
        assert points[1][0] == 280

    def test_reaches_the_requested_opacity_at_the_threshold(self):
        points = self.ramp(threshold=300, opacity=0.65)
        assert points[2][0] == 300
        assert points[2][1] == 0.65

    def test_opaque_plateau_above_the_threshold(self):
        points = self.ramp(opacity=0.8)
        assert points[3][1] == 0.8
        assert points[4][1] == 0.8

    def test_translucent_zeroes_the_plateau_leaving_a_shell(self):
        # This is how the vessel surface shows in 3D without hiding the slice behind it.
        points = self.ramp(translucent=True, opacity=0.8)
        assert points[2][1] == 0.8, "the threshold itself stays visible"
        assert points[3][1] == 0
        assert points[4][1] == 0

    def test_margin_sets_the_width_of_the_rise(self):
        points = self.ramp(threshold=500, margin=75)
        assert points[1][0] == 425
        assert points[3][0] == 575

    def test_every_point_carries_midpoint_and_sharpness(self):
        # vtkPiecewiseFunction.SetNodeValue rejects anything but 4 components.
        assert all(len(p) == 4 for p in self.ramp())
