"""Tests for the ``scaffold`` CLI subcommand and module."""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from kiutils.board import Board
from kiutils.schematic import Schematic

from kicad_blocks.cli import main
from kicad_blocks.config import load_config
from kicad_blocks.scaffold import ScaffoldError, scaffold_project


def _make_sheet(path: Path, uuid: str) -> None:
    """Write a minimal valid ``.kicad_sch`` at ``path`` for the scaffold to reference."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "(kicad_sch (version 20240108) (generator eeschema)\n"
        f'  (uuid "{uuid}")\n'
        '  (paper "A4")\n'
        "  (lib_symbols)\n"
        ")\n"
    )


def test_scaffold_creates_expected_files(tmp_path: Path) -> None:
    """``scaffold_project`` writes the four expected files under ``<base_dir>/<name>``."""
    sheet = tmp_path / "shared" / "mcu.kicad_sch"
    _make_sheet(sheet, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    project_dir = scaffold_project("widget", [sheet], base_dir=tmp_path)

    assert project_dir == tmp_path / "widget"
    assert (project_dir / "widget.kicad_pro").is_file()
    assert (project_dir / "widget.kicad_sch").is_file()
    assert (project_dir / "widget.kicad_pcb").is_file()
    assert (project_dir / "kicad-blocks.toml").is_file()


def test_scaffold_kicad_pro_is_valid_json(tmp_path: Path) -> None:
    """The generated ``.kicad_pro`` is parseable JSON with a ``meta`` block."""
    sheet = tmp_path / "shared" / "mcu.kicad_sch"
    _make_sheet(sheet, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    project_dir = scaffold_project("widget", [sheet], base_dir=tmp_path)

    data = json.loads((project_dir / "widget.kicad_pro").read_text())
    assert "meta" in data
    assert data["meta"].get("filename") == "widget.kicad_pro"


def test_scaffold_root_schematic_references_each_sheet(tmp_path: Path) -> None:
    """Root ``.kicad_sch`` declares hierarchical sheet refs for every provided sheet."""
    s1 = tmp_path / "shared" / "mcu.kicad_sch"
    s2 = tmp_path / "shared" / "power.kicad_sch"
    _make_sheet(s1, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    _make_sheet(s2, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    project_dir = scaffold_project("widget", [s1, s2], base_dir=tmp_path)

    schematic = Schematic.from_file(str(project_dir / "widget.kicad_sch"))
    sheet_files = {s.fileName.value for s in schematic.sheets}
    assert sheet_files == {"../shared/mcu.kicad_sch", "../shared/power.kicad_sch"}


def test_scaffold_pcb_parses_with_placeholder_outline(tmp_path: Path) -> None:
    """Generated ``.kicad_pcb`` parses via kiutils and has a rectangle on ``Edge.Cuts``."""
    sheet = tmp_path / "shared" / "mcu.kicad_sch"
    _make_sheet(sheet, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    project_dir = scaffold_project("widget", [sheet], base_dir=tmp_path)

    board = Board.from_file(str(project_dir / "widget.kicad_pcb"))
    on_edge_cuts = [g for g in board.graphicItems if getattr(g, "layer", "") == "Edge.Cuts"]
    assert on_edge_cuts, "expected a placeholder outline on Edge.Cuts"


def test_scaffold_config_loads_and_lists_sheets(tmp_path: Path) -> None:
    """Generated ``kicad-blocks.toml`` is valid and pre-populates ``[blocks.<name>]`` per sheet."""
    s1 = tmp_path / "shared" / "mcu.kicad_sch"
    s2 = tmp_path / "shared" / "power.kicad_sch"
    _make_sheet(s1, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    _make_sheet(s2, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    project_dir = scaffold_project("widget", [s1, s2], base_dir=tmp_path)

    config = load_config(project_dir / "kicad-blocks.toml")
    assert config.project == "widget"
    sheet_paths = {str(b.sheet) for b in config.blocks.values()}
    assert sheet_paths == {"../shared/mcu.kicad_sch", "../shared/power.kicad_sch"}


def test_scaffold_config_includes_anchor_placeholder_comments(tmp_path: Path) -> None:
    """Generated config carries commented anchor/source placeholders to guide the user."""
    sheet = tmp_path / "shared" / "mcu.kicad_sch"
    _make_sheet(sheet, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    project_dir = scaffold_project("widget", [sheet], base_dir=tmp_path)

    text = (project_dir / "kicad-blocks.toml").read_text()
    assert "# anchor" in text
    assert "# source" in text


def test_scaffold_refuses_to_overwrite_existing_dir(tmp_path: Path) -> None:
    """Without ``force=True``, scaffold refuses to write into an existing directory."""
    sheet = tmp_path / "shared" / "mcu.kicad_sch"
    _make_sheet(sheet, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "stale.txt").write_text("old")

    with pytest.raises(ScaffoldError):
        scaffold_project("widget", [sheet], base_dir=tmp_path)

    assert (tmp_path / "widget" / "stale.txt").exists()


def test_scaffold_force_writes_into_existing_dir(tmp_path: Path) -> None:
    """``force=True`` writes into the existing directory (without erasing unrelated files)."""
    sheet = tmp_path / "shared" / "mcu.kicad_sch"
    _make_sheet(sheet, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    (tmp_path / "widget").mkdir()

    scaffold_project("widget", [sheet], base_dir=tmp_path, force=True)

    assert (tmp_path / "widget" / "widget.kicad_pro").is_file()


def test_scaffold_rejects_missing_sheet(tmp_path: Path) -> None:
    """A nonexistent sheet path is rejected before any files are written."""
    missing = tmp_path / "shared" / "nope.kicad_sch"

    with pytest.raises(ScaffoldError):
        scaffold_project("widget", [missing], base_dir=tmp_path)

    assert not (tmp_path / "widget").exists()


def test_scaffold_cli_happy_path(tmp_path: Path) -> None:
    """The ``scaffold`` CLI exits 0 and writes the expected files."""
    sheet = tmp_path / "shared" / "mcu.kicad_sch"
    _make_sheet(sheet, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "scaffold",
            "--name",
            "widget",
            "--dir",
            str(tmp_path),
            "--sheet",
            str(sheet),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "widget" / "widget.kicad_pro").is_file()
    assert (tmp_path / "widget" / "widget.kicad_sch").is_file()
    assert (tmp_path / "widget" / "widget.kicad_pcb").is_file()
    assert (tmp_path / "widget" / "kicad-blocks.toml").is_file()


def test_scaffold_cli_refuses_overwrite_without_force(tmp_path: Path) -> None:
    """The CLI refuses to write into an existing project directory without ``--force``."""
    sheet = tmp_path / "shared" / "mcu.kicad_sch"
    _make_sheet(sheet, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    (tmp_path / "widget").mkdir()

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "scaffold",
            "--name",
            "widget",
            "--dir",
            str(tmp_path),
            "--sheet",
            str(sheet),
        ],
    )

    assert result.exit_code != 0
    assert "force" in result.output.lower() or "exists" in result.output.lower()


def test_scaffold_cli_force_overwrites(tmp_path: Path) -> None:
    """The CLI writes into an existing dir when ``--force`` is passed."""
    sheet = tmp_path / "shared" / "mcu.kicad_sch"
    _make_sheet(sheet, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    (tmp_path / "widget").mkdir()

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "scaffold",
            "--name",
            "widget",
            "--dir",
            str(tmp_path),
            "--sheet",
            str(sheet),
            "--force",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "widget" / "widget.kicad_pro").is_file()


def test_scaffold_cli_requires_at_least_one_sheet(tmp_path: Path) -> None:
    """Invoking ``scaffold`` without any ``--sheet`` exits non-zero with a helpful message."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["scaffold", "--name", "widget", "--dir", str(tmp_path)],
    )

    assert result.exit_code != 0
