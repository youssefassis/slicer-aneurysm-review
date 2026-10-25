# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""Display maths: colour conversion and the volume-rendering opacity ramp."""

#: The ramp always has this many control points, so it can be updated in place.
SCALAR_OPACITY_POINT_COUNT = 5

#: How far either side of the threshold the ramp takes to rise, in scalar units.
OPACITY_RAMP_MARGIN = 20

# vtkPiecewiseFunction node values are (x, y, midpoint, sharpness).
_MIDPOINT = 0.5
_SHARPNESS = 0.0


def to_rgb(color):
    """Convert a QColor to the 0-1 RGB triplet that VTK/MRML display nodes expect."""
    return (color.red() / 255, color.green() / 255, color.blue() / 255)


def is_visible(index, selected_index, checked):
    """Whether detection ``index``'s annotations should be shown.

    The reviewer's checkbox decides, but only for the detection they have selected: while one
    row is selected the others are hidden, so the candidate under review is not surrounded by
    its neighbours' spheres. ``selected_index`` below zero means no selection, which shows
    every detection.
    """
    return bool(checked) if index == selected_index or selected_index < 0 else False


def _clamp(value, low, high):
    return min(max(value, low), high)


def scalar_opacity_points(min_value, max_value, threshold, opacity, translucent,
                          margin=OPACITY_RAMP_MARGIN):
    """Return the opacity ramp control points for the volume rendering.

    The ramp is transparent below ``threshold - margin``, rises to ``opacity`` at
    ``threshold``, and holds a plateau above ``threshold + margin``. ``translucent`` zeroes
    that plateau, which leaves only a thin shell at the threshold — that is how the vessel
    surface is shown in 3D without occluding the reformatted slice behind it.

    The threshold slider reaches both ends of the scalar range, so the margin is clamped
    into ``[min_value, max_value]``: a control point outside the range would leave the node
    scalars non-ascending, which ``vtkPiecewiseFunction`` cannot represent.

    Returns ``SCALAR_OPACITY_POINT_COUNT`` lists of ``(x, y, midpoint, sharpness)``.
    """
    plateau = 0 if translucent else opacity
    ramp = [
        (min_value, 0),
        (_clamp(threshold - margin, min_value, max_value), 0),
        (_clamp(threshold, min_value, max_value), opacity),
        (_clamp(threshold + margin, min_value, max_value), plateau),
        (max_value, plateau),
    ]
    return [[x, y, _MIDPOINT, _SHARPNESS] for x, y in ramp]
