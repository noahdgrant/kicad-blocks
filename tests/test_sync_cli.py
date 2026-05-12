"""Tests for the ``sync`` CLI subcommand and the lock JSON sidecar."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from click.testing import CliRunner

from kicad_blocks.cli import main

FIXTURE_REUSE = Path(__file__).parent / "fixtures" / "reuse"
SOURCE_PCB = FIXTURE_REUSE / "source" / "source.kicad_pcb"
TARGET_FIXTURE_DIR = FIXTURE_REUSE / "target"


def _stage_target_project(tmp_path: Path) -> tuple[Path, Path]:
    """Copy the target fixture into a writable scratch dir, return (config, target)."""
    scratch_target = tmp_path / "target"
    scratch_target.mkdir()
    shutil.copy(TARGET_FIXTURE_DIR / "target.kicad_pcb", scratch_target / "target.kicad_pcb")
    config_text = (TARGET_FIXTURE_DIR / "kicad-blocks.toml").read_text()
    config_text = config_text.replace('"../source/source.kicad_pcb"', f'"{SOURCE_PCB}"')
    config_path = scratch_target / "kicad-blocks.toml"
    config_path.write_text(config_text)
    return config_path, scratch_target / "target.kicad_pcb"


def test_reuse_writes_lock_json_sidecar(tmp_path: Path) -> None:
    """A successful reuse leaves a ``<project>.kicad-blocks.lock.json`` beside the config."""
    config_path, _ = _stage_target_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["reuse", "--config", str(config_path)])
    assert result.exit_code == 0, result.output

    lock_path = config_path.parent / "reuse-target.kicad-blocks.lock.json"
    assert lock_path.exists()
    data = json.loads(lock_path.read_text())
    assert data["schema_version"] == 1
    mcu = data["blocks"]["mcu"]
    assert mcu["anchor_refdes"] == "ANCHOR1"
    assert mcu["sheet"] == "sheets/mcu.kicad_sch"
    assert mcu["source_pcb_hash"].startswith("sha256:")
    assert mcu["applied_block_hash"].startswith("sha256:")


def test_reuse_dry_run_does_not_write_lock(tmp_path: Path) -> None:
    """Dry-run is a preview — no lock file should be produced."""
    config_path, _ = _stage_target_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["reuse", "--config", str(config_path), "--dry-run"])
    assert result.exit_code == 0, result.output
    lock_path = config_path.parent / "reuse-target.kicad-blocks.lock.json"
    assert not lock_path.exists()


def test_sync_dry_run_after_apply_reports_no_changes(tmp_path: Path) -> None:
    """After a fresh reuse, sync --dry-run shows the block is in sync."""
    config_path, _ = _stage_target_project(tmp_path)
    runner = CliRunner()
    apply_result = runner.invoke(main, ["reuse", "--config", str(config_path)])
    assert apply_result.exit_code == 0, apply_result.output

    sync_result = runner.invoke(main, ["sync", "--config", str(config_path), "--dry-run"])
    assert sync_result.exit_code == 0, sync_result.output
    assert "no changes" in sync_result.output.lower()


def test_sync_dry_run_before_apply_reports_moved_footprints(tmp_path: Path) -> None:
    """Before any reuse — but with a lock from a prior run — sync shows moves."""
    config_path, _ = _stage_target_project(tmp_path)
    runner = CliRunner()
    # First reuse establishes the lock and applies the block.
    runner.invoke(main, ["reuse", "--config", str(config_path)])

    # Now move R10 in the target to simulate a hand-edit / drift.
    target_path = config_path.parent / "target.kicad_pcb"
    text = target_path.read_text()
    target_path.write_text(text.replace("(at 210.0 195.0 90.0)", "(at 250.0 250.0 0)"))

    sync_result = runner.invoke(main, ["sync", "--config", str(config_path), "--dry-run"])
    assert sync_result.exit_code == 0, sync_result.output
    assert "moved footprints" in sync_result.output.lower()
    assert "R10" in sync_result.output


def test_sync_dry_run_without_lock_errors_with_hint(tmp_path: Path) -> None:
    """No lock file → clear error suggesting `reuse` first."""
    config_path, _ = _stage_target_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["sync", "--config", str(config_path), "--dry-run"])
    assert result.exit_code != 0
    assert "lock file" in result.output.lower()
    assert "reuse" in result.output.lower()


def test_sync_without_dry_run_is_not_yet_implemented(tmp_path: Path) -> None:
    """The apply path lands in slice 8 — until then it exits with a clear notice."""
    config_path, _ = _stage_target_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["reuse", "--config", str(config_path)])

    result = runner.invoke(main, ["sync", "--config", str(config_path)])
    assert result.exit_code != 0
    assert "slice 8" in result.output.lower() or "not implemented" in result.output.lower()
