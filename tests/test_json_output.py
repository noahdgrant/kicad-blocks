"""Tests for ``--format json`` across every subcommand.

Each command renders a JSON envelope with a stable ``schema_version: 1`` field
on stdout. Exit codes match the text-mode behaviour; errors travel as JSON in
the same envelope shape so a consumer can parse one stream and read both
success and failure cases.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from kicad_blocks.cli import main

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal"
CONFIG = FIXTURE_DIR / "kicad-blocks.toml"
FIXTURE_REUSE = Path(__file__).parent / "fixtures" / "reuse"
SOURCE_PCB = FIXTURE_REUSE / "source" / "source.kicad_pcb"
TARGET_FIXTURE_DIR = FIXTURE_REUSE / "target"


def _assert_envelope(payload: Any, command: str, *, ok: bool) -> None:
    """Common shape: schema_version=1, command set, ok matches."""
    assert payload["schema_version"] == 1
    assert payload["command"] == command
    assert payload["ok"] is ok


def _stage_target_project(tmp_path: Path) -> tuple[Path, Path]:
    scratch_target = tmp_path / "target"
    scratch_target.mkdir()
    shutil.copy(TARGET_FIXTURE_DIR / "target.kicad_pcb", scratch_target / "target.kicad_pcb")
    config_text = (TARGET_FIXTURE_DIR / "kicad-blocks.toml").read_text()
    config_text = config_text.replace('"../source/source.kicad_pcb"', f'"{SOURCE_PCB}"')
    config_path = scratch_target / "kicad-blocks.toml"
    config_path.write_text(config_text)
    return config_path, scratch_target / "target.kicad_pcb"


# ---- validate -------------------------------------------------------------


def test_validate_json_happy_path() -> None:
    """``validate --format json`` emits an ok envelope with the config path."""
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--config", str(CONFIG), "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    _assert_envelope(payload, "validate", ok=True)
    assert payload["config"] == str(CONFIG)


def test_validate_json_reports_runtime_problems(tmp_path: Path) -> None:
    """Runtime problems (missing sheet) surface in ``errors`` with exit code 1."""
    bad_config = tmp_path / "kicad-blocks.toml"
    bad_config.write_text(
        f'project = "minimal"\n'
        f'sources = ["{FIXTURE_DIR / "minimal.kicad_pcb"}"]\n\n'
        f'[blocks.mcu]\nsheet = "sheets/missing.kicad_sch"\n'
    )
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--config", str(bad_config), "--format", "json"])
    assert result.exit_code != 0
    payload = json.loads(result.output)
    _assert_envelope(payload, "validate", ok=False)
    errors = payload["errors"]
    assert errors
    assert any("missing.kicad_sch" in err["message"] for err in errors)


def test_validate_json_reports_config_errors(tmp_path: Path) -> None:
    """A bad TOML config surfaces structured config errors with path/line metadata."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text('sources = ["x.kicad_pcb"]\n')  # missing required 'project'
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--config", str(bad), "--format", "json"])
    assert result.exit_code != 0
    payload = json.loads(result.output)
    _assert_envelope(payload, "validate", ok=False)
    errors = payload["errors"]
    assert any("project" in err["message"] for err in errors)


def test_validate_json_missing_config_file(tmp_path: Path) -> None:
    """A nonexistent config emits a JSON error rather than a text one."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["validate", "--config", str(tmp_path / "nope.toml"), "--format", "json"],
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    _assert_envelope(payload, "validate", ok=False)
    assert any("not found" in err["message"].lower() for err in payload["errors"])


# ---- list-block -----------------------------------------------------------


def test_list_block_json_emits_footprints() -> None:
    """``list-block --format json`` returns the matching footprints as objects."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "list-block",
            "--config",
            str(CONFIG),
            "--sheet",
            "sheets/mcu.kicad_sch",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    _assert_envelope(payload, "list-block", ok=True)
    assert payload["sheet"] == "sheets/mcu.kicad_sch"
    refs = {fp["reference"] for fp in payload["footprints"]}
    assert {"R1", "R2"} <= refs
    sample = payload["footprints"][0]
    assert {"reference", "uuid", "symbol_uuid", "layer", "position", "rotation"} <= set(sample)
    assert isinstance(sample["position"], list)
    assert len(sample["position"]) == 2


def test_list_block_json_unknown_sheet_returns_empty_list() -> None:
    """An unknown sheet still exits 0 and emits an empty ``footprints`` array."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "list-block",
            "--config",
            str(CONFIG),
            "--sheet",
            "sheets/nope.kicad_sch",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    _assert_envelope(payload, "list-block", ok=True)
    assert payload["footprints"] == []


# ---- reuse ---------------------------------------------------------------


def test_reuse_json_dry_run_emits_plan(tmp_path: Path) -> None:
    """``reuse --dry-run --format json`` returns a structured plan per block."""
    config_path, _ = _stage_target_project(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        main, ["reuse", "--config", str(config_path), "--dry-run", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    _assert_envelope(payload, "reuse", ok=True)
    assert payload["dry_run"] is True
    blocks = payload["blocks"]
    assert blocks
    block = blocks[0]
    assert block["name"] == "mcu"
    assert block["target_anchor"] == "ANCHOR1"
    assert block["transform_angle_deg"] == 90.0
    refs = {p["target_reference"] for p in block["placements"]}
    assert {"R10", "R20"} <= refs


def test_reuse_json_apply_emits_applied(tmp_path: Path) -> None:
    """A successful apply has ``dry_run: false`` and lists tracks/vias."""
    config_path, _ = _stage_target_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["reuse", "--config", str(config_path), "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    _assert_envelope(payload, "reuse", ok=True)
    assert payload["dry_run"] is False
    block = payload["blocks"][0]
    assert len(block["tracks"]) == 2
    assert len(block["vias"]) == 1


def test_reuse_json_missing_target_emits_error(tmp_path: Path) -> None:
    """Missing ``target`` field surfaces a JSON error on stdout."""
    config_path, _ = _stage_target_project(tmp_path)
    text = config_path.read_text().replace('target = "target.kicad_pcb"\n', "")
    config_path.write_text(text)

    runner = CliRunner()
    result = runner.invoke(
        main, ["reuse", "--config", str(config_path), "--dry-run", "--format", "json"]
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    _assert_envelope(payload, "reuse", ok=False)
    assert any("target" in err["message"].lower() for err in payload["errors"])


# ---- sync ----------------------------------------------------------------


def test_sync_json_dry_run_after_reuse_shows_no_changes(tmp_path: Path) -> None:
    """After a fresh reuse, sync --dry-run JSON has an empty diff per block."""
    config_path, _ = _stage_target_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["reuse", "--config", str(config_path)])

    result = runner.invoke(
        main, ["sync", "--config", str(config_path), "--dry-run", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    _assert_envelope(payload, "sync", ok=True)
    assert payload["dry_run"] is True
    assert payload["applied"] is False
    block = payload["blocks"][0]
    assert block["name"] == "mcu"
    assert block["diff"]["is_empty"] is True


def test_sync_json_dry_run_reports_moved_footprints(tmp_path: Path) -> None:
    """A target hand-edit shows up under ``diff.moved_footprints``."""
    config_path, target_path = _stage_target_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["reuse", "--config", str(config_path)])

    target_path.write_text(
        target_path.read_text().replace("(at 210.0 195.0 90.0)", "(at 250.0 250.0 0)")
    )

    result = runner.invoke(
        main, ["sync", "--config", str(config_path), "--dry-run", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    block = payload["blocks"][0]
    moved = block["diff"]["moved_footprints"]
    assert any(m["target_reference"] == "R10" for m in moved)


def test_sync_json_missing_lock_emits_error(tmp_path: Path) -> None:
    """No lock → JSON error envelope on stdout."""
    config_path, _ = _stage_target_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main, ["sync", "--config", str(config_path), "--dry-run", "--format", "json"]
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    _assert_envelope(payload, "sync", ok=False)
    assert any("lock" in err["message"].lower() for err in payload["errors"])


# ---- scaffold ------------------------------------------------------------


def test_scaffold_json_emits_project_dir(tmp_path: Path) -> None:
    """``scaffold --format json`` returns the new project directory."""
    sheet = tmp_path / "shared" / "mcu.kicad_sch"
    sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.write_text(
        "(kicad_sch (version 20240108) (generator eeschema)\n"
        '  (uuid "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")\n'
        '  (paper "A4")\n'
        "  (lib_symbols)\n"
        ")\n"
    )
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
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    _assert_envelope(payload, "scaffold", ok=True)
    assert payload["project_dir"].endswith("widget")


def test_scaffold_json_missing_sheet_emits_error(tmp_path: Path) -> None:
    """Scaffold validation errors surface as JSON on stdout."""
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
            str(tmp_path / "shared" / "nope.kicad_sch"),
            "--format",
            "json",
        ],
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    _assert_envelope(payload, "scaffold", ok=False)
    assert payload["errors"]


# ---- panelize-config -----------------------------------------------------


def test_panelize_config_json_emits_output_path(tmp_path: Path) -> None:
    """``panelize-config --format json`` returns the path to the written preset."""
    config_path = tmp_path / "kicad-blocks.toml"
    config_path.write_text(
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[panelize]\n"
        'modules = ["mcu.kicad_pcb", "power.kicad_pcb"]\n'
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["panelize-config", "--config", str(config_path), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    _assert_envelope(payload, "panelize-config", ok=True)
    assert payload["output_path"].endswith("panel.kikit.json")
    assert (tmp_path / "panel.kikit.json").exists()


def test_panelize_config_json_missing_section_emits_error(tmp_path: Path) -> None:
    """A config without ``[panelize]`` surfaces a JSON error on stdout."""
    config_path = tmp_path / "kicad-blocks.toml"
    config_path.write_text('project = "x"\nsources = ["a.kicad_pcb"]\n')
    runner = CliRunner()
    result = runner.invoke(
        main, ["panelize-config", "--config", str(config_path), "--format", "json"]
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    _assert_envelope(payload, "panelize-config", ok=False)
    assert any("panelize" in err["message"].lower() for err in payload["errors"])


# ---- default format -------------------------------------------------------


def test_default_format_remains_text() -> None:
    """Omitting ``--format`` retains the existing text behaviour."""
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--config", str(CONFIG)])
    assert result.exit_code == 0, result.output
    # Plain text, not JSON.
    assert "{" not in result.output.split("\n")[0]
    assert "ok" in result.output.lower()
