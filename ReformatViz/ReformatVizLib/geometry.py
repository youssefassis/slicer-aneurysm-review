# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""Geometry behind the oblique reformatting, in RAS world coordinates.

The reviewer looks at an aneurysm along a slice that always *contains* the aneurysm axis
(the P1 ostium to P2 dome direction) and spins that slice around the axis. The functions
here produce the vectors and angles that arrangement needs; the caller hands them to
Slicer's reformat logic.
"""

import math

import numpy as np

# np.finfo(...).resolution, not .eps: these are squared-norm comparisons on millimetre
# coordinates, where the tighter machine epsilon would not catch a degenerate vector.
EPS = np.finfo(np.float64).resolution

#: Used when there is no aneurysm to look at, or when P1 and P2 coincide.
DEFAULT_AXIS = np.array([0.0, 1.0, 0.0])

#: Below this separation (mm) P1 and P2 give no usable direction.
MIN_AXIS_LENGTH = 1e-8


def rotate2Z(v):
    """Return a rotation matrix whose third row is the unit ``v``, i.e. it maps ``v`` to +Z.

    The first two rows are an arbitrary but deterministic orthonormal completion. Returns
    the identity for a degenerate ``v``, so the caller always gets a usable frame.
    """
    v = np.array(v)
    tn = v @ v  # tiny bit faster that np.dot(v,v)
    if tn < EPS:
        return np.eye(3, dtype=np.float64)

    vn = v / math.sqrt(tn)
    R1 = np.array([-vn[1], vn[0], 0])
    tn = R1 @ R1

    if tn < EPS:
        return np.eye(3, dtype=np.float64)

    # sqrt(tn) is sqrt(vn[0]**2 + vn[1]**2), which is the same quantity as sqrt(1 - vn[2]**2)
    # but without the cancellation that costs that form nearly all its precision when the
    # axis is close to vertical.
    horizontal = math.sqrt(tn)
    R1 = R1 / horizontal
    R0 = [vn[0] * vn[2] / horizontal, vn[1] * vn[2] / horizontal, -horizontal]

    return np.array([R0, R1, vn], dtype=np.float64)


def aneurysm_frame(P1, P2):
    """Return ``(axis, origin, Rz)`` for the aneurysm running from ``P1`` to ``P2``.

    ``axis`` is the unit P1->P2 direction (``DEFAULT_AXIS`` if the two points coincide),
    ``origin`` their midpoint, and ``Rz`` the matrix that takes ``axis`` onto +Z.
    """
    A, B = np.asarray(P1, dtype=np.float64), np.asarray(P2, dtype=np.float64)
    axis = B - A
    length = np.linalg.norm(axis)
    axis = axis / length if length > MIN_AXIS_LENGTH else DEFAULT_AXIS.copy()
    return axis, (A + B) / 2, rotate2Z(axis)


def default_frame():
    """Return the ``(axis, origin, Rz)`` used when no detection is selected."""
    return DEFAULT_AXIS.copy(), np.zeros(3), rotate2Z(DEFAULT_AXIS)


def slice_normal(Rz, angle_deg):
    """Return the slice normal ``angle_deg`` around the aneurysm axis.

    The result is perpendicular to the axis that ``Rz`` maps to +Z, which is what keeps the
    aneurysm axis inside the slice at every angle.
    """
    angle = math.radians(angle_deg)
    return Rz.T @ np.array([math.cos(angle), math.sin(angle), 0.0])


def in_plane_roll_deg(slice_x, slice_y, axis):
    """Return the in-plane rotation (degrees) that points ``axis`` along the slice +Y.

    Without this the aneurysm would appear to tumble as the reviewer rotates the view;
    with it, it stays upright.
    """
    ca = np.asarray(slice_y) @ axis
    sa = np.asarray(slice_x) @ axis
    return math.degrees(math.atan2(-sa, ca))


def camera_position(origin, normal, distance):
    """Return the camera position looking at ``origin`` along ``normal`` from ``distance``."""
    return np.asarray(origin, dtype=np.float64) - distance * np.asarray(normal, dtype=np.float64)


def volume_bounds(ijk_to_ras, dimensions):
    """Return ``(center, size)`` of the RAS box enclosing a whole volume.

    ``ijk_to_ras`` is a 4x4 array-like. Used to place and size the crop ROI, which is
    axis-aligned in RAS, so ``size`` is the bounding box of the voxel box: a tilted volume
    needs a *larger* ROI than its own side lengths, not a smaller one. Taking the diagonal
    corner-to-corner difference instead would collapse the ROI as the volume tilts.
    """
    m = np.asarray(ijk_to_ras, dtype=np.float64)
    dims = np.asarray(dimensions, dtype=np.float64)
    center = (m @ np.append(dims / 2.0, 1.0))[:3]
    size = np.abs(m[:3, :3]) @ dims
    return center, size
