"""Tests for the ``sync`` CLI subcommand and the lock JSON sidecar."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
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


def test_sync_reports_no_changes_when_in_sync(tmp_path: Path) -> None:
    """Right after a reuse, ``sync`` (no flags) reports no changes and skips the prompt."""
    config_path, _ = _stage_target_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["reuse", "--config", str(config_path)])

    result = runner.invoke(main, ["sync", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "no changes" in result.output.lower()


def test_sync_force_applies_when_target_unchanged_and_source_modified(tmp_path: Path) -> None:
    """Source-only changes flow into the target on ``sync --force`` with no conflict."""
    config_path, target_path = _stage_target_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["reuse", "--config", str(config_path)])

    # Mutate the source's R1 to (105, 55) so the planned R10 target position shifts.
    scratch_source = tmp_path / "source.kicad_pcb"
    shutil.copy(SOURCE_PCB, scratch_source)
    text = scratch_source.read_text()
    text = text.replace("(at 100 50 0)", "(at 105 55 0)")
    scratch_source.write_text(text)
    config_text = config_path.read_text()
    config_text = config_text.replace(f'"{SOURCE_PCB}"', f'"{scratch_source}"')
    config_path.write_text(config_text)

    result = runner.invoke(main, ["sync", "--config", str(config_path), "--force"])

    assert result.exit_code == 0, result.output
    after = load_pcb(target_path)
    by_ref = {fp.reference: fp for fp in after.footprints}
    # New source R1 at (105, 55, 0) → with the 90° anchor transform target R10 ends up offset.
    assert by_ref["R10"].position == (205.0, 200.0)


def test_sync_force_updates_lock_after_apply(tmp_path: Path) -> None:
    """A successful sync apply refreshes ``applied_block_hash`` in the lock."""
    config_path, _target_path = _stage_target_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["reuse", "--config", str(config_path)])
    lock_path = config_path.parent / "reuse-target.kicad-blocks.lock.json"
    initial_hash = json.loads(lock_path.read_text())["blocks"]["mcu"]["applied_block_hash"]

    # Modify source so the apply produces a different applied state.
    scratch_source = tmp_path / "source.kicad_pcb"
    shutil.copy(SOURCE_PCB, scratch_source)
    scratch_source.write_text(scratch_source.read_text().replace("(at 100 50 0)", "(at 105 55 0)"))
    config_text = config_path.read_text()
    config_path.write_text(config_text.replace(f'"{SOURCE_PCB}"', f'"{scratch_source}"'))

    result = runner.invoke(main, ["sync", "--config", str(config_path), "--force"])
    assert result.exit_code == 0, result.output

    refreshed_hash = json.loads(lock_path.read_text())["blocks"]["mcu"]["applied_block_hash"]
    assert refreshed_hash != initial_hash


def test_sync_prompts_y_n_without_force(tmp_path: Path) -> None:
    """Without ``--force``, the apply path prompts; ``y`` confirms and applies."""
    config_path, target_path = _stage_target_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["reuse", "--config", str(config_path)])

    scratch_source = tmp_path / "source.kicad_pcb"
    shutil.copy(SOURCE_PCB, scratch_source)
    scratch_source.write_text(scratch_source.read_text().replace("(at 100 50 0)", "(at 105 55 0)"))
    config_path.write_text(
        config_path.read_text().replace(f'"{SOURCE_PCB}"', f'"{scratch_source}"')
    )

    result = runner.invoke(main, ["sync", "--config", str(config_path)], input="y\n")

    assert result.exit_code == 0, result.output
    after = load_pcb(target_path)
    by_ref = {fp.reference: fp for fp in after.footprints}
    assert by_ref["R10"].position == (205.0, 200.0)


def test_sync_aborts_on_n(tmp_path: Path) -> None:
    """Without ``--force``, answering ``n`` to the prompt leaves the target untouched."""
    config_path, target_path = _stage_target_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["reuse", "--config", str(config_path)])
    after_reuse = target_path.read_bytes()

    scratch_source = tmp_path / "source.kicad_pcb"
    shutil.copy(SOURCE_PCB, scratch_source)
    scratch_source.write_text(scratch_source.read_text().replace("(at 100 50 0)", "(at 105 55 0)"))
    config_path.write_text(
        config_path.read_text().replace(f'"{SOURCE_PCB}"', f'"{scratch_source}"')
    )

    result = runner.invoke(main, ["sync", "--config", str(config_path)], input="n\n")

    assert result.exit_code != 0
    assert target_path.read_bytes() == after_reuse


def test_sync_refuses_when_target_hand_edited(tmp_path: Path) -> None:
    """A hand-edit inside the block region (hash mismatch) blocks ``sync`` without ``--force``."""
    config_path, target_path = _stage_target_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["reuse", "--config", str(config_path)])
    after_reuse = target_path.read_bytes()

    # Simulate a hand-edit: nudge R10 inside the block region.
    text = target_path.read_text()
    target_path.write_text(text.replace("(at 210.0 195.0 90.0)", "(at 250.0 250.0 0)"))

    result = runner.invoke(main, ["sync", "--config", str(config_path)])

    assert result.exit_code != 0
    assert "conflict" in result.output.lower() or "hand-edit" in result.output.lower()
    # Target is not rewritten — both the byte-stream and the lock are preserved.
    assert "force" in result.output.lower()
    assert target_path.read_bytes() != after_reuse  # the user's hand-edit is still in place
    # No partial apply on top of the hand-edit either.
    by_ref = {fp.reference: fp for fp in load_pcb(target_path).footprints}
    assert by_ref["R10"].position == (250.0, 250.0)


def test_sync_force_overrides_hand_edit_conflict(tmp_path: Path) -> None:
    """With ``--force``, a hand-edited target gets re-asserted from the source."""
    config_path, target_path = _stage_target_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["reuse", "--config", str(config_path)])

    text = target_path.read_text()
    target_path.write_text(text.replace("(at 210.0 195.0 90.0)", "(at 250.0 250.0 0)"))

    result = runner.invoke(main, ["sync", "--config", str(config_path), "--force"])

    assert result.exit_code == 0, result.output
    after = load_pcb(target_path)
    by_ref = {fp.reference: fp for fp in after.footprints}
    # R10 is restored to where the source dictates.
    assert by_ref["R10"].position == (210.0, 195.0)
    assert by_ref["R10"].rotation == 90.0


def test_sync_force_preserves_items_outside_block_region(tmp_path: Path) -> None:
    """A footprint outside the block sheet is unchanged through a real forced apply."""
    config_path, target_path = _stage_target_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["reuse", "--config", str(config_path)])

    # C2 sits on the root sheet, outside the mcu block. Capture its current state.
    before = load_pcb(target_path)
    c2_before = next(fp for fp in before.footprints if fp.reference == "C2")

    # Drive a real apply by mutating the source.
    scratch_source = tmp_path / "source.kicad_pcb"
    shutil.copy(SOURCE_PCB, scratch_source)
    scratch_source.write_text(scratch_source.read_text().replace("(at 100 50 0)", "(at 105 55 0)"))
    config_path.write_text(
        config_path.read_text().replace(f'"{SOURCE_PCB}"', f'"{scratch_source}"')
    )

    result = runner.invoke(main, ["sync", "--config", str(config_path), "--force"])
    assert result.exit_code == 0, result.output

    after = load_pcb(target_path)
    # The in-block footprint moved (proves a real apply happened) but C2 is untouched.
    by_ref = {fp.reference: fp for fp in after.footprints}
    assert by_ref["R10"].position == (205.0, 200.0)
    c2_after = next(fp for fp in after.footprints if fp.reference == "C2")
    assert c2_after.position == c2_before.position
    assert c2_after.rotation == c2_before.rotation
    assert c2_after.layer == c2_before.layer
    assert c2_after.sheet_file == c2_before.sheet_file


def test_sync_rolls_back_on_simulated_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-write failure during the atomic rename leaves the target byte-identical."""
    config_path, target_path = _stage_target_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["reuse", "--config", str(config_path)])
    after_reuse = target_path.read_bytes()

    # Force a conflict-free apply by modifying the source.
    scratch_source = tmp_path / "source.kicad_pcb"
    shutil.copy(SOURCE_PCB, scratch_source)
    scratch_source.write_text(scratch_source.read_text().replace("(at 100 50 0)", "(at 105 55 0)"))
    config_path.write_text(
        config_path.read_text().replace(f'"{SOURCE_PCB}"', f'"{scratch_source}"')
    )

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated mid-write failure")

    monkeypatch.setattr(os, "replace", boom)

    result = runner.invoke(main, ["sync", "--config", str(config_path), "--force"])

    assert result.exit_code != 0
    assert target_path.read_bytes() == after_reuse
    # No temp scraps left in the target's directory.
    leftovers = [p.name for p in target_path.parent.iterdir() if not p.name.endswith(".lock.json")]
    assert sorted(leftovers) == ["kicad-blocks.toml", "target.kicad_pcb"]
