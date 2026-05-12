"""Tests for the ``kicad_io`` module."""

import os
import shutil
from pathlib import Path

import pytest

from kicad_blocks.kicad_io import (
    FootprintPlacement,
    GraphicPlacement,
    KicadIoError,
    TrackPlacement,
    ViaPlacement,
    ZonePlacement,
    apply_placements,
    load_pcb,
)
from kicad_blocks.transform import Transform

FIXTURE = Path(__file__).parent / "fixtures" / "minimal" / "minimal.kicad_pcb"
REUSE_TARGET = Path(__file__).parent / "fixtures" / "reuse" / "target" / "target.kicad_pcb"
REUSE_SOURCE = Path(__file__).parent / "fixtures" / "reuse" / "source" / "source.kicad_pcb"


def test_load_pcb_returns_typed_footprints() -> None:
    """``load_pcb`` should return a Pcb with typed footprints."""
    pcb = load_pcb(FIXTURE)

    assert pcb.path == FIXTURE
    refs = {fp.reference for fp in pcb.footprints}
    assert refs == {"R1", "R2", "C1"}


def test_footprint_carries_sheet_file_and_uuid() -> None:
    """Each footprint exposes its Sheetfile, symbol UUID, position, and layer."""
    pcb = load_pcb(FIXTURE)
    by_ref = {fp.reference: fp for fp in pcb.footprints}

    r1 = by_ref["R1"]
    assert r1.sheet_file == "sheets/mcu.kicad_sch"
    assert r1.symbol_uuid == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert r1.layer == "F.Cu"
    assert r1.position == (100.0, 50.0)
    assert r1.rotation == 0.0

    r2 = by_ref["R2"]
    assert r2.rotation == 90.0


def test_load_pcb_missing_file_raises() -> None:
    """A missing file path raises ``KicadIoError`` with the path in the message."""
    missing = Path("/nonexistent/does_not_exist.kicad_pcb")
    with pytest.raises(KicadIoError) as excinfo:
        load_pcb(missing)
    assert str(missing) in str(excinfo.value)


def test_load_pcb_unparseable_raises(tmp_path: Path) -> None:
    """A garbage file raises ``KicadIoError``."""
    bad = tmp_path / "bad.kicad_pcb"
    bad.write_text("this is not s-expressions")
    with pytest.raises(KicadIoError):
        load_pcb(bad)


def test_pcb_exposes_board_nets() -> None:
    """``Pcb.nets`` lists the named nets on the board (the ``""`` net is filtered)."""
    pcb = load_pcb(REUSE_TARGET)
    assert set(pcb.nets) == {"+3V3", "GND", "SIG"}


def test_footprint_carries_pad_nets() -> None:
    """Each footprint's pads contribute their net names to ``pad_nets``."""
    pcb = load_pcb(REUSE_TARGET)
    by_ref = {fp.reference: fp for fp in pcb.footprints}
    assert set(by_ref["R10"].pad_nets) == {"+3V3", "SIG"}
    assert set(by_ref["ANCHOR1"].pad_nets) == {"+3V3", "GND", "SIG"}


def test_apply_placements_updates_position_atomically(tmp_path: Path) -> None:
    """``apply_placements`` rewrites positions and is atomic (no temp leftovers)."""
    src = REUSE_TARGET
    dst = tmp_path / "target.kicad_pcb"
    shutil.copy(src, dst)

    apply_placements(
        dst,
        [
            FootprintPlacement(
                symbol_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                position=(150.0, 75.0),
                rotation=45.0,
                layer="F.Cu",
            )
        ],
    )

    after = load_pcb(dst)
    by_ref = {fp.reference: fp for fp in after.footprints}
    assert by_ref["R10"].position == (150.0, 75.0)
    assert by_ref["R10"].rotation == 45.0

    # No temp scraps left behind in the destination directory.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "target.kicad_pcb"]
    assert leftovers == []


def test_apply_placements_rolls_back_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the temp file write blows up, the original target is byte-identical."""
    dst = tmp_path / "target.kicad_pcb"
    shutil.copy(REUSE_TARGET, dst)
    original = dst.read_bytes()

    # Make ``os.replace`` blow up after the temp file is written but before
    # the atomic rename completes — the realistic failure mode for "the disk
    # ran out of space" or "permission denied on the final swap".
    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated mid-write failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(KicadIoError, match="Failed to write"):
        apply_placements(
            dst,
            [
                FootprintPlacement(
                    symbol_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    position=(1.0, 1.0),
                    rotation=0.0,
                    layer="F.Cu",
                )
            ],
        )

    assert dst.read_bytes() == original
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "target.kicad_pcb"]
    assert leftovers == []


def test_load_pcb_returns_tracks_with_net_names() -> None:
    """Track segments are exposed as typed :class:`Track` with net *names*, not numbers."""
    pcb = load_pcb(Path(__file__).parent / "fixtures" / "reuse" / "source" / "source.kicad_pcb")
    by_endpoints = {(t.start, t.end): t for t in pcb.tracks}
    in_block = by_endpoints[((100.75, 50.0), (105.0, 60.95))]
    assert in_block.layer == "F.Cu"
    assert in_block.width == 0.25
    assert in_block.net == "SIG"


def test_load_pcb_returns_vias_with_layer_span_and_net() -> None:
    """Vias carry their position, drill, diameter, layer span (resolved to names), and net."""
    pcb = load_pcb(Path(__file__).parent / "fixtures" / "reuse" / "source" / "source.kicad_pcb")
    by_position = {v.position: v for v in pcb.vias}
    sig_via = by_position[(100.75, 50.0)]
    assert sig_via.drill == 0.3
    assert sig_via.size == 0.6
    assert sig_via.layers == ("F.Cu", "B.Cu")
    assert sig_via.net == "SIG"


def test_apply_placements_appends_tracks_and_vias(tmp_path: Path) -> None:
    """``apply_placements`` accepts ``tracks`` and ``vias`` and appends them atomically."""
    dst = tmp_path / "target.kicad_pcb"
    shutil.copy(REUSE_TARGET, dst)

    apply_placements(
        dst,
        [],
        tracks=[
            TrackPlacement(
                start=(210.0, 195.75),
                end=(199.05, 200.0),
                width=0.25,
                layer="F.Cu",
                net_name="SIG",
            )
        ],
        vias=[
            ViaPlacement(
                position=(210.0, 195.75),
                size=0.6,
                drill=0.3,
                layers=("F.Cu", "B.Cu"),
                net_name="SIG",
            )
        ],
    )

    after = load_pcb(dst)
    assert any(t.start == (210.0, 195.75) and t.net == "SIG" for t in after.tracks)
    assert any(v.position == (210.0, 195.75) and v.net == "SIG" for v in after.vias)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "target.kicad_pcb"]
    assert leftovers == []


def test_apply_placements_rejects_unknown_net(tmp_path: Path) -> None:
    """Track/via net names absent from the target board's net table are rejected up front."""
    dst = tmp_path / "target.kicad_pcb"
    shutil.copy(REUSE_TARGET, dst)
    original = dst.read_bytes()

    with pytest.raises(KicadIoError, match="net"):
        apply_placements(
            dst,
            [],
            tracks=[
                TrackPlacement(
                    start=(0.0, 0.0),
                    end=(1.0, 1.0),
                    width=0.25,
                    layer="F.Cu",
                    net_name="MYSTERY",
                )
            ],
        )

    assert dst.read_bytes() == original


def test_load_pcb_exposes_layer_stackup() -> None:
    """``Pcb.layers`` carries the board's layer table in source order."""
    pcb = load_pcb(REUSE_SOURCE)
    names = [layer.name for layer in pcb.layers]
    assert "F.Cu" in names
    assert "B.Cu" in names
    assert "F.SilkS" in names
    # Each layer carries a type alongside its name.
    f_cu = next(layer for layer in pcb.layers if layer.name == "F.Cu")
    assert f_cu.type == "signal"


def test_load_pcb_exposes_board_zones() -> None:
    """``Pcb.zones`` enumerates zones with net name, layers, and outline points."""
    pcb = load_pcb(REUSE_SOURCE)
    by_net = {z.net_name: z for z in pcb.zones}
    assert "GND" in by_net
    gnd_zone = by_net["GND"]
    assert gnd_zone.layers == ("F.Cu",)
    # The four polygon corners of the in-block zone.
    assert (98.0, 48.0) in gnd_zone.outline_points
    assert (112.0, 62.0) in gnd_zone.outline_points


def test_load_pcb_exposes_board_graphics() -> None:
    """``Pcb.graphics`` enumerates board-level gr_* items with layer and points."""
    pcb = load_pcb(REUSE_SOURCE)
    text_items = [g for g in pcb.graphics if any(p == (105.0, 55.0) for p in g.points)]
    assert text_items, "expected a graphic at the in-block 'MCU' label position"
    assert text_items[0].layer == "F.SilkS"


def test_apply_placements_appends_zones_with_resolved_net(tmp_path: Path) -> None:
    """``apply_placements`` deep-copies the source zone and writes it with the resolved net."""
    src = REUSE_SOURCE
    dst = tmp_path / "target.kicad_pcb"
    shutil.copy(REUSE_TARGET, dst)

    source_pcb = load_pcb(src)
    gnd_zone = next(z for z in source_pcb.zones if z.net_name == "GND")

    apply_placements(
        dst,
        [],
        zones=[
            ZonePlacement(
                source_raw=gnd_zone.raw,
                transform=Transform.identity(),
                net_name="GND",
                layers=("F.Cu",),
            )
        ],
    )

    after_text = dst.read_text()
    assert '(net_name "GND")' in after_text
    # The outline polygon was preserved.
    assert "98" in after_text and "112" in after_text


def test_apply_placements_transforms_graphic_endpoints(tmp_path: Path) -> None:
    """``apply_placements`` deep-copies a source graphic and rewrites its coordinates."""
    src = REUSE_SOURCE
    dst = tmp_path / "target.kicad_pcb"
    shutil.copy(REUSE_TARGET, dst)

    source_pcb = load_pcb(src)
    in_block_line = next(
        g for g in source_pcb.graphics if (98.0, 48.0) in g.points and (112.0, 48.0) in g.points
    )

    apply_placements(
        dst,
        [],
        graphics=[
            GraphicPlacement(
                source_raw=in_block_line.raw,
                transform=Transform.translation(10.0, 20.0),
                layer="F.SilkS",
            )
        ],
    )

    after = load_pcb(dst)
    # After +10,+20 translation: endpoints (98,48)→(108,68) and (112,48)→(122,68).
    line = next(g for g in after.graphics if (108.0, 68.0) in g.points)
    assert (122.0, 68.0) in line.points
    assert line.layer == "F.SilkS"


def test_apply_placements_rejects_unknown_symbol(tmp_path: Path) -> None:
    """If any placement's symbol UUID is absent from the target, the file is untouched."""
    dst = tmp_path / "target.kicad_pcb"
    shutil.copy(REUSE_TARGET, dst)
    original = dst.read_bytes()

    with pytest.raises(KicadIoError, match="no target footprint matched"):
        apply_placements(
            dst,
            [
                FootprintPlacement(
                    symbol_uuid="ffffffff-ffff-ffff-ffff-ffffffffffff",
                    position=(1.0, 1.0),
                    rotation=0.0,
                    layer="F.Cu",
                )
            ],
        )

    assert dst.read_bytes() == original
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "target.kicad_pcb"]
    assert leftovers == []
