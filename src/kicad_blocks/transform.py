"""2D affine math for anchor-relative placement.

The :class:`Transform` represents ``point' = R(angle_deg) * point + (tx, ty)``
— rotation around the origin followed by translation. The PRD's anchor model
maps cleanly onto this: place the source frame's origin at the source anchor,
rotate by ``target_angle - source_angle``, then translate to the target anchor.
:meth:`Transform.from_anchors` is that recipe.

Axis-aligned rotations (multiples of 90°) are special-cased so round-trips are
bit-exact. KiCAD layouts overwhelmingly use those four angles and we don't want
float drift accumulating across sync cycles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Transform:
    """A 2D affine transform: rotation about the origin, then translation.

    Attributes:
        angle_deg: Rotation angle in degrees, normalized to ``[0, 360)``.
        tx: Translation along the x-axis, applied after rotation.
        ty: Translation along the y-axis, applied after rotation.
    """

    angle_deg: float
    tx: float
    ty: float

    @classmethod
    def identity(cls) -> Transform:
        """Return the identity transform."""
        return cls(angle_deg=0.0, tx=0.0, ty=0.0)

    @classmethod
    def translation(cls, tx: float, ty: float) -> Transform:
        """Return a pure-translation transform."""
        return cls(angle_deg=0.0, tx=tx, ty=ty)

    @classmethod
    def rotation(cls, angle_deg: float) -> Transform:
        """Return a pure-rotation transform around the origin."""
        return cls(angle_deg=_normalize_angle(angle_deg), tx=0.0, ty=0.0)

    @classmethod
    def from_anchors(
        cls,
        *,
        source: tuple[float, float],
        source_angle: float,
        target: tuple[float, float],
        target_angle: float,
    ) -> Transform:
        """Build the transform that carries the source frame onto the target frame.

        The composition is: translate the source anchor to the origin, rotate
        by ``target_angle - source_angle``, then translate to the target anchor.
        Equivalently::

            target_pt = R(delta) * (source_pt - source_anchor) + target_anchor

        Args:
            source: Position of the anchor in the source PCB.
            source_angle: Rotation of the anchor in the source PCB.
            target: Position of the anchor in the target PCB.
            target_angle: Rotation of the anchor in the target PCB.

        Returns:
            The composed transform.
        """
        delta = target_angle - source_angle
        # Build T = Translation(target) ∘ Rotation(delta) ∘ Translation(-source).
        # Translation(-source) has angle=0, so composing with Rotation(delta)
        # yields a transform whose rotation is ``delta`` and translation is
        # ``R(delta) * -source``. Composing with Translation(target) then adds
        # ``target`` to the translation component.
        rx, ry = _rotate(-source[0], -source[1], delta)
        return cls(
            angle_deg=_normalize_angle(delta),
            tx=rx + target[0],
            ty=ry + target[1],
        )

    def apply(self, point: tuple[float, float]) -> tuple[float, float]:
        """Apply this transform to ``point`` and return the transformed coordinates."""
        x, y = point
        rx, ry = _rotate(x, y, self.angle_deg)
        return (rx + self.tx, ry + self.ty)

    def apply_angle(self, angle_deg: float) -> float:
        """Apply this transform's rotation to an angle. Translations don't rotate angles."""
        return _normalize_angle(angle_deg + self.angle_deg)

    def compose(self, other: Transform) -> Transform:
        """Return ``self ∘ other``: apply ``other`` first, then ``self``.

        Mathematically::

            (self ∘ other)(p) = R_s * (R_o * p + t_o) + t_s
                              = (R_s R_o) * p + (R_s * t_o + t_s)
        """
        rx, ry = _rotate(other.tx, other.ty, self.angle_deg)
        return Transform(
            angle_deg=_normalize_angle(self.angle_deg + other.angle_deg),
            tx=rx + self.tx,
            ty=ry + self.ty,
        )

    def inverse(self) -> Transform:
        """Return the inverse transform.

        For ``T(p) = R*p + t``, the inverse is ``R^-1 * (p - t)`` which equals
        ``R^-1 * p - R^-1 * t``. ``R^-1`` is rotation by ``-angle_deg``.
        """
        inv_angle = -self.angle_deg
        rx, ry = _rotate(-self.tx, -self.ty, inv_angle)
        return Transform(angle_deg=_normalize_angle(inv_angle), tx=rx, ty=ry)


def _normalize_angle(angle_deg: float) -> float:
    """Wrap ``angle_deg`` into ``[0, 360)`` while preserving exactness for multiples of 90."""
    a = angle_deg % 360.0
    # ``-0.0 % 360.0`` is ``0.0``, but be defensive for FP corner cases.
    if a == 360.0:
        return 0.0
    return a


def _rotate(x: float, y: float, angle_deg: float) -> tuple[float, float]:
    """Rotate (x, y) by ``angle_deg`` around the origin.

    Multiples of 90° are special-cased so the result is bit-exact (no
    ``cos(pi/2)`` returning ``6e-17``). Everything else uses standard trig.
    """
    a = _normalize_angle(angle_deg)
    if a == 0.0:
        return (x, y)
    if a == 90.0:
        return (-y, x)
    if a == 180.0:
        return (-x, -y)
    if a == 270.0:
        return (y, -x)
    theta = math.radians(a)
    c = math.cos(theta)
    s = math.sin(theta)
    return (c * x - s * y, s * x + c * y)
