"""Tests for the ``reuse`` CLI subcommand."""

from __future__ import annotations

import shutil
from pathlib import Path

from click.testing import CliRunner

from kicad_blocks.cli import main
from kicad_blocks.kicad_io import load_pcb

FIXTURE_REUSE = Path(__file__).parent / "fixtures" / "reuse"
SOURCE_PCB = FIXTURE_REUSE / "source" / "source.kicad_pcb"
TARGET_FIXTURE_DIR = FIXTURE_REUSE / "target"


def _stage_target_project(tmp_path: Path) -> tuple[Path, Path]:
    """Copy the target fixture into a writable scratch dir, return (config, target)."""
    scratch_target = tmp_path / "target"
    scratch_target.mkdir()
    shutil.copy(TARGET_FIXTURE_DIR / "target.kicad_pcb", scratch_target / "target.kicad_pcb")
    config_text = (TARGET_FIXTURE_DIR / "kicad-blocks.toml").read_text()
    # Rewrite the source path to point at the on-disk fixture (the scratch dir
    # doesn't carry a sibling source/ directory).
    config_text = config_text.replace('"../source/source.kicad_pcb"', f'"{SOURCE_PCB}"')
    config_path = scratch_target / "kicad-blocks.toml"
    config_path.write_text(config_text)
    return config_path, scratch_target / "target.kicad_pcb"


def test_reuse_dry_run_does_not_write(tmp_path: Path) -> None:
    """``reuse --dry-run`` reports the plan and leaves the target untouched."""
    config_path, target_path = _stage_target_project(tmp_path)
    original = target_path.read_bytes()

    runner = CliRunner()
    result = runner.invoke(main, ["reuse", "--config", str(config_path), "--dry-run"])

    assert result.exit_code == 0, result.output
    # The plan mentions both moved footprints' target refdes.
    assert "R10" in result.output
    assert "R20" in result.output
    # File is byte-identical.
    assert target_path.read_bytes() == original


def test_reuse_applies_placements(tmp_path: Path) -> None:
    """``reuse`` (without --dry-run) writes the transformed positions to the target."""
    config_path, target_path = _stage_target_project(tmp_path)

    runner = CliRunner()
    result = runner.invoke(main, ["reuse", "--config", str(config_path)])

    assert result.exit_code == 0, result.output

    after = load_pcb(target_path)
    by_ref = {fp.reference: fp for fp in after.footprints}
    # Source R1 at (100, 50, 0); anchor offsets put it at (210, 195, 90) in target.
    assert by_ref["R10"].position == (210.0, 195.0)
    assert by_ref["R10"].rotation == 90.0


def test_reuse_reports_missing_anchor(tmp_path: Path) -> None:
    """An anchor refdes that doesn't exist in the target produces a clear error."""
    config_path, _ = _stage_target_project(tmp_path)
    text = config_path.read_text().replace('anchor = "ANCHOR1"', 'anchor = "NOPE"')
    config_path.write_text(text)

    runner = CliRunner()
    result = runner.invoke(main, ["reuse", "--config", str(config_path), "--dry-run"])

    assert result.exit_code != 0
    assert "anchor" in result.output.lower()


def test_reuse_requires_source_and_anchor(tmp_path: Path) -> None:
    """A block missing ``source`` or ``anchor`` is skipped with a clear message."""
    config_path, target_path = _stage_target_project(tmp_path)
    text = config_path.read_text().replace('anchor = "ANCHOR1"\n', "")
    config_path.write_text(text)
    original = target_path.read_bytes()

    runner = CliRunner()
    result = runner.invoke(main, ["reuse", "--config", str(config_path)])

    assert result.exit_code != 0
    assert "anchor" in result.output.lower()
    # Target is not modified when no block could be processed.
    assert target_path.read_bytes() == original


def test_reuse_requires_target_field(tmp_path: Path) -> None:
    """Without ``target`` in the config, ``reuse`` exits with a clear error."""
    config_path, _ = _stage_target_project(tmp_path)
    text = config_path.read_text().replace('target = "target.kicad_pcb"\n', "")
    config_path.write_text(text)

    runner = CliRunner()
    result = runner.invoke(main, ["reuse", "--config", str(config_path), "--dry-run"])

    assert result.exit_code != 0
    assert "target" in result.output.lower()
