"""Translate a :class:`PanelizeSpec` into a KiKit ``panelize`` preset dict.

KiKit's ``panelize`` command consumes a JSON preset describing the panel
geometry: layout grid, tab placement, cut style, framing, fiducials, and source
selection. We surface a small TOML schema (``[panelize]`` in
``kicad-blocks.toml``) and translate it into the equivalent preset on demand.

This module is intentionally pure: it takes a :class:`PanelizeSpec` and returns
a plain ``dict`` ready for ``json.dumps``. The CLI is responsible for picking
where to write that dict.
"""

from __future__ import annotations

from typing import Any

from kicad_blocks.config import (
    OUTLINE_FRAME,
    OUTLINE_NONE,
    OUTLINE_TIGHTFRAME,
    SEPARATION_MOUSE_BITES,
    SEPARATION_TABS,
    PanelizeSpec,
)

_DEFAULT_TAB_WIDTH = "5mm"
_DEFAULT_FRAME_WIDTH = "5mm"
_DEFAULT_FRAME_SPACE = "2mm"
_DEFAULT_MOUSEBITE_DRILL = "0.5mm"
_DEFAULT_MOUSEBITE_SPACING = "0.8mm"
_DEFAULT_MOUSEBITE_OFFSET = "0.25mm"
_DEFAULT_FIDUCIAL_HOFFSET = "5mm"
_DEFAULT_FIDUCIAL_VOFFSET = "2.5mm"


def build_kikit_preset(spec: PanelizeSpec) -> dict[str, dict[str, Any]]:
    """Return a KiKit ``panelize`` preset dict equivalent to ``spec``.

    The result is a plain mapping suitable for ``json.dumps`` and contains every
    top-level section KiKit's panelize command expects, so missing-default
    surprises never reach the user.

    Args:
        spec: The validated panelize declaration.

    Returns:
        A preset dict with keys ``layout``, ``tabs``, ``cuts``, ``framing``,
        ``fiducials``, and ``source``.
    """
    return {
        "layout": {
            "type": "grid",
            "rows": 1,
            "cols": len(spec.modules),
            "hspace": spec.spacing,
            "vspace": spec.spacing,
        },
        "tabs": {
            "type": "fixed",
            "hwidth": _DEFAULT_TAB_WIDTH,
            "vwidth": _DEFAULT_TAB_WIDTH,
        },
        "cuts": _cuts_section(spec.separation),
        "framing": _framing_section(spec.outline),
        "fiducials": _fiducials_section(fiducials=spec.fiducials),
        "source": {"type": "auto"},
    }


def _cuts_section(separation: str) -> dict[str, Any]:
    """Return the ``cuts`` block for ``separation``."""
    if separation == SEPARATION_MOUSE_BITES:
        return {
            "type": "mousebites",
            "drill": _DEFAULT_MOUSEBITE_DRILL,
            "spacing": _DEFAULT_MOUSEBITE_SPACING,
            "offset": _DEFAULT_MOUSEBITE_OFFSET,
        }
    if separation == SEPARATION_TABS:
        return {"type": "vcuts"}
    raise ValueError(f"unsupported separation: {separation!r}")


def _framing_section(outline: str) -> dict[str, Any]:
    """Return the ``framing`` block for ``outline``."""
    if outline == OUTLINE_NONE:
        return {"type": "none"}
    if outline in (OUTLINE_FRAME, OUTLINE_TIGHTFRAME):
        return {
            "type": outline,
            "width": _DEFAULT_FRAME_WIDTH,
            "space": _DEFAULT_FRAME_SPACE,
        }
    raise ValueError(f"unsupported outline: {outline!r}")


def _fiducials_section(*, fiducials: bool) -> dict[str, Any]:
    """Return the ``fiducials`` block; ``type=none`` when disabled."""
    if not fiducials:
        return {"type": "none"}
    return {
        "type": "3fid",
        "hoffset": _DEFAULT_FIDUCIAL_HOFFSET,
        "voffset": _DEFAULT_FIDUCIAL_VOFFSET,
    }
