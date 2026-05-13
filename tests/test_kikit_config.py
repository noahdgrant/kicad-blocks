"""Tests for ``kikit_config`` — translating PanelizeSpec → KiKit preset JSON."""

from pathlib import Path

from kicad_blocks.config import (
    OUTLINE_FRAME,
    OUTLINE_NONE,
    OUTLINE_TIGHTFRAME,
    SEPARATION_MOUSE_BITES,
    SEPARATION_TABS,
    PanelizeSpec,
)
from kicad_blocks.kikit_config import build_kikit_preset


def _spec(**overrides: object) -> PanelizeSpec:
    base: dict[str, object] = {
        "modules": (Path("../mcu/mcu.kicad_pcb"), Path("../power/power.kicad_pcb")),
        "spacing": "2mm",
        "separation": SEPARATION_TABS,
        "outline": OUTLINE_FRAME,
        "fiducials": False,
    }
    base.update(overrides)
    return PanelizeSpec(**base)  # type: ignore[arg-type]


def test_layout_is_single_row_grid_sized_to_module_count() -> None:
    """``layout.cols`` matches the module count; rows = 1."""
    preset = build_kikit_preset(_spec())
    assert preset["layout"]["type"] == "grid"
    assert preset["layout"]["rows"] == 1
    assert preset["layout"]["cols"] == 2


def test_spacing_flows_into_layout_hspace_and_vspace() -> None:
    """The spacing string is applied to both axes of the grid."""
    preset = build_kikit_preset(_spec(spacing="3mm"))
    assert preset["layout"]["hspace"] == "3mm"
    assert preset["layout"]["vspace"] == "3mm"


def test_tabs_separation_produces_vcuts() -> None:
    """``separation = tabs`` emits ``cuts.type = vcuts`` over fixed tabs."""
    preset = build_kikit_preset(_spec(separation=SEPARATION_TABS))
    assert preset["tabs"]["type"] == "fixed"
    assert preset["cuts"]["type"] == "vcuts"


def test_mouse_bites_separation_produces_mousebites_cuts() -> None:
    """``separation = mouse_bites`` emits ``cuts.type = mousebites`` with defaults."""
    preset = build_kikit_preset(_spec(separation=SEPARATION_MOUSE_BITES))
    assert preset["cuts"]["type"] == "mousebites"
    # KiKit requires drill / spacing / offset on mousebites — defaults should appear.
    assert "drill" in preset["cuts"]
    assert "spacing" in preset["cuts"]
    assert "offset" in preset["cuts"]


def test_outline_frame_renders_framing_with_width() -> None:
    """``outline = frame`` emits a framing block with width + space defaults."""
    preset = build_kikit_preset(_spec(outline=OUTLINE_FRAME))
    assert preset["framing"]["type"] == "frame"
    assert "width" in preset["framing"]
    assert "space" in preset["framing"]


def test_outline_tightframe_renders_tightframe() -> None:
    """``outline = tightframe`` emits ``framing.type = tightframe``."""
    preset = build_kikit_preset(_spec(outline=OUTLINE_TIGHTFRAME))
    assert preset["framing"]["type"] == "tightframe"


def test_outline_none_renders_no_framing() -> None:
    """``outline = none`` emits ``framing.type = none``."""
    preset = build_kikit_preset(_spec(outline=OUTLINE_NONE))
    assert preset["framing"]["type"] == "none"


def test_fiducials_true_emits_three_fiducials() -> None:
    """``fiducials = true`` selects the ``3fid`` preset on the frame."""
    preset = build_kikit_preset(_spec(fiducials=True))
    assert preset["fiducials"]["type"] == "3fid"


def test_fiducials_false_emits_none() -> None:
    """``fiducials = false`` selects ``type = none`` so the key is always present."""
    preset = build_kikit_preset(_spec(fiducials=False))
    assert preset["fiducials"]["type"] == "none"


def test_preset_has_all_kikit_top_level_sections() -> None:
    """The preset includes every section KiKit's ``panelize`` command consults."""
    preset = build_kikit_preset(_spec())
    for key in ("layout", "tabs", "cuts", "framing", "fiducials", "source"):
        assert key in preset, f"missing KiKit section: {key}"
