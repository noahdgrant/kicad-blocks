"""Tests for the ``transform`` module.

The transform module is the pure-math backbone of anchor-relative placement:
rotation + translation in 2D, with composition and inversion. Round-trips for
axis-aligned rotations must be exact (KiCAD layouts overwhelmingly use 0/90/
180/270 angles, and we don't want floating-point grit accumulating across a
sync cycle).
"""

from __future__ import annotations

import math

import pytest

from kicad_blocks.transform import Transform


def _approx(p: tuple[float, float], q: tuple[float, float]) -> bool:
    return math.isclose(p[0], q[0], abs_tol=1e-9) and math.isclose(p[1], q[1], abs_tol=1e-9)


def test_identity_leaves_points_unchanged() -> None:
    """The identity transform is a no-op."""
    t = Transform.identity()
    assert t.apply((3.0, -7.5)) == (3.0, -7.5)
    assert t.apply_angle(45.0) == 45.0


def test_translation_shifts_points() -> None:
    """Pure translation shifts (x, y) by (tx, ty)."""
    t = Transform.translation(10.0, -2.0)
    assert t.apply((1.0, 1.0)) == (11.0, -1.0)
    # Translation does not rotate.
    assert t.apply_angle(30.0) == 30.0


def test_rotation_90_is_exact() -> None:
    """A 90° rotation around the origin sends (1, 0) → (0, 1) exactly."""
    t = Transform.rotation(90.0)
    assert t.apply((1.0, 0.0)) == (0.0, 1.0)
    assert t.apply((0.0, 1.0)) == (-1.0, 0.0)
    assert t.apply((-1.0, 0.0)) == (0.0, -1.0)
    assert t.apply((0.0, -1.0)) == (1.0, 0.0)


def test_rotation_180_is_exact() -> None:
    """A 180° rotation negates both axes exactly."""
    t = Transform.rotation(180.0)
    assert t.apply((3.0, -4.0)) == (-3.0, 4.0)


def test_rotation_270_is_exact() -> None:
    """A 270° rotation is the inverse of 90°."""
    t = Transform.rotation(270.0)
    assert t.apply((1.0, 0.0)) == (0.0, -1.0)


def test_rotation_round_trip_axis_aligned() -> None:
    """Four 90° rotations return to identity exactly (no float drift)."""
    p = (3.0, -7.0)
    t = Transform.rotation(90.0)
    q = t.apply(t.apply(t.apply(t.apply(p))))
    assert q == p


def test_rotation_arbitrary_angle_uses_radians_internally() -> None:
    """An arbitrary angle still works via standard trig."""
    t = Transform.rotation(45.0)
    x, y = t.apply((1.0, 0.0))
    assert math.isclose(x, math.sqrt(2) / 2, abs_tol=1e-9)
    assert math.isclose(y, math.sqrt(2) / 2, abs_tol=1e-9)


def test_compose_applies_other_then_self() -> None:
    """``self.compose(other)`` returns the transform meaning "apply other, then self"."""
    rotate90 = Transform.rotation(90.0)
    translate = Transform.translation(10.0, 0.0)
    # First rotate (1,0) → (0,1), then translate → (10,1)
    composed = translate.compose(rotate90)
    assert composed.apply((1.0, 0.0)) == (10.0, 1.0)


def test_compose_is_associative_on_angle_sums() -> None:
    """Composing two rotations sums their angles modulo 360."""
    a = Transform.rotation(120.0)
    b = Transform.rotation(150.0)
    composed = b.compose(a)
    # Net angle is 270°, which is axis-aligned → exact.
    assert composed.apply((1.0, 0.0)) == (0.0, -1.0)


def test_inverse_undoes_apply() -> None:
    """Applying a transform then its inverse returns the original point."""
    t = Transform.rotation(37.0).compose(Transform.translation(5.0, -3.0))
    inv = t.inverse()
    p = (2.5, 8.0)
    forward = t.apply(p)
    back = inv.apply(forward)
    assert _approx(back, p)


def test_inverse_of_axis_aligned_round_trips_exactly() -> None:
    """For 90° rotations, inverse round-trip is exact (no float error)."""
    t = Transform.rotation(90.0).compose(Transform.translation(10.0, -2.0))
    inv = t.inverse()
    p = (3.0, 4.0)
    assert inv.apply(t.apply(p)) == p


def test_apply_angle_adds_rotation() -> None:
    """Applying the transform to an angle adds the transform's rotation."""
    t = Transform.rotation(90.0)
    assert t.apply_angle(45.0) == 135.0
    # Wraps into [0, 360).
    assert t.apply_angle(300.0) == 30.0


def test_from_anchors_zero_rotation_is_translation() -> None:
    """Source and target anchors with zero rotation give pure translation."""
    t = Transform.from_anchors(
        source=(10.0, 20.0),
        source_angle=0.0,
        target=(100.0, 200.0),
        target_angle=0.0,
    )
    # A source point one unit right of the source anchor lands one unit right of the target anchor.
    assert t.apply((11.0, 20.0)) == (101.0, 200.0)


def test_from_anchors_rotates_around_source_anchor() -> None:
    """When the target anchor is rotated 90°, source offsets rotate too."""
    t = Transform.from_anchors(
        source=(10.0, 20.0),
        source_angle=0.0,
        target=(0.0, 0.0),
        target_angle=90.0,
    )
    # Source point (11, 20) is +1 in x relative to source anchor.
    # After 90° rotation around target anchor (0,0), it should land at (0, 1).
    assert t.apply((11.0, 20.0)) == (0.0, 1.0)


def test_from_anchors_preserves_anchor() -> None:
    """The source anchor always maps to the target anchor, whatever the angles."""
    for src_angle in (0.0, 90.0, 180.0, 270.0):
        for tgt_angle in (0.0, 90.0, 180.0, 270.0):
            t = Transform.from_anchors(
                source=(50.0, 75.0),
                source_angle=src_angle,
                target=(200.0, 300.0),
                target_angle=tgt_angle,
            )
            assert t.apply((50.0, 75.0)) == (200.0, 300.0), (src_angle, tgt_angle)


def test_apply_returns_tuple_not_list() -> None:
    """The API is tuple-in, tuple-out so callers can pattern-destructure safely."""
    t = Transform.identity()
    result = t.apply((1.0, 2.0))
    assert isinstance(result, tuple)


@pytest.mark.parametrize("angle", [0.0, 90.0, 180.0, 270.0])
def test_compose_with_identity_is_noop(angle: float) -> None:
    """Composing with identity on either side is a no-op."""
    t = Transform.rotation(angle)
    ident = Transform.identity()
    left = t.compose(ident)
    right = ident.compose(t)
    p = (5.0, -3.0)
    assert left.apply(p) == t.apply(p)
    assert right.apply(p) == t.apply(p)
