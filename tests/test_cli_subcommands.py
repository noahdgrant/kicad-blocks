"""Tests for the ``validate`` and ``list-block`` CLI subcommands."""

from pathlib import Path

from click.testing import CliRunner

from kicad_blocks.cli import main

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal"
CONFIG = FIXTURE_DIR / "kicad-blocks.toml"


def test_validate_happy_path() -> None:
    """``validate`` returns 0 and prints an ok line on a valid config."""
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--config", str(CONFIG)])
    assert result.exit_code == 0, result.output
    assert "ok" in result.output.lower()


def test_validate_missing_config(tmp_path: Path) -> None:
    """``validate`` exits non-zero with a clear error on a missing config."""
    missing = tmp_path / "kicad-blocks.toml"
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--config", str(missing)])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_validate_reports_missing_pcb(tmp_path: Path) -> None:
    """A config that references a nonexistent PCB triggers an error."""
    bad_config = tmp_path / "kicad-blocks.toml"
    bad_config.write_text(
        'project = "x"\nsources = ["nope.kicad_pcb"]\n\n[blocks.mcu]\nsheet = "mcu.kicad_sch"\n'
    )
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--config", str(bad_config)])
    assert result.exit_code != 0
    assert "nope.kicad_pcb" in result.output


def test_validate_reports_undeclared_sheet(tmp_path: Path) -> None:
    """A block whose sheet file does not exist is flagged."""
    bad_config = tmp_path / "kicad-blocks.toml"
    bad_config.write_text(
        f'project = "minimal"\n'
        f'sources = ["{FIXTURE_DIR / "minimal.kicad_pcb"}"]\n\n'
        f'[blocks.mcu]\nsheet = "sheets/missing.kicad_sch"\n'
    )
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--config", str(bad_config)])
    assert result.exit_code != 0
    assert "missing.kicad_sch" in result.output


def test_list_block_prints_footprints_on_sheet() -> None:
    """``list-block --sheet`` prints refdes + UUID + position."""
    runner = CliRunner()
    result = runner.invoke(
        main, ["list-block", "--config", str(CONFIG), "--sheet", "sheets/mcu.kicad_sch"]
    )
    assert result.exit_code == 0, result.output
    assert "R1" in result.output
    assert "R2" in result.output
    assert "C1" not in result.output
    assert "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" in result.output


def test_list_block_unknown_sheet_returns_empty() -> None:
    """An unknown sheet name exits 0 but reports no footprints."""
    runner = CliRunner()
    result = runner.invoke(
        main, ["list-block", "--config", str(CONFIG), "--sheet", "sheets/nope.kicad_sch"]
    )
    assert result.exit_code == 0
    assert "no footprints" in result.output.lower()
