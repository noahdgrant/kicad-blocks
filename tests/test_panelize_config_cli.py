"""Tests for the ``panelize-config`` CLI subcommand."""

import json
from pathlib import Path

from click.testing import CliRunner

from kicad_blocks.cli import main


def _write_config(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / "kicad-blocks.toml"
    config_path.write_text(body)
    return config_path


def test_panelize_config_writes_default_path(tmp_path: Path) -> None:
    """Without ``--out``, the JSON lands next to the config as ``panel.kikit.json``."""
    config_path = _write_config(
        tmp_path,
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[panelize]\n"
        'modules = ["mcu.kicad_pcb", "power.kicad_pcb"]\n',
    )
    runner = CliRunner()
    result = runner.invoke(main, ["panelize-config", "--config", str(config_path)])
    assert result.exit_code == 0, result.output

    out_path = tmp_path / "panel.kikit.json"
    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert data["layout"]["cols"] == 2
    assert data["cuts"]["type"] == "vcuts"


def test_panelize_config_respects_out_option(tmp_path: Path) -> None:
    """``--out`` writes to the given path instead of the default location."""
    config_path = _write_config(
        tmp_path,
        'project = "x"\nsources = ["a.kicad_pcb"]\n\n[panelize]\nmodules = ["mcu.kicad_pcb"]\n',
    )
    custom = tmp_path / "out" / "preset.json"
    custom.parent.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["panelize-config", "--config", str(config_path), "--out", str(custom)],
    )
    assert result.exit_code == 0, result.output
    assert custom.exists()


def test_panelize_config_emits_mouse_bites_variant(tmp_path: Path) -> None:
    """``separation = mouse_bites`` flows into the emitted preset."""
    config_path = _write_config(
        tmp_path,
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[panelize]\n"
        'modules = ["mcu.kicad_pcb"]\n'
        'separation = "mouse_bites"\n',
    )
    runner = CliRunner()
    result = runner.invoke(main, ["panelize-config", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / "panel.kikit.json").read_text())
    assert data["cuts"]["type"] == "mousebites"


def test_panelize_config_without_panelize_section_errors(tmp_path: Path) -> None:
    """A config with no ``[panelize]`` table is rejected with a clear error."""
    config_path = _write_config(tmp_path, 'project = "x"\nsources = ["a.kicad_pcb"]\n')
    runner = CliRunner()
    result = runner.invoke(main, ["panelize-config", "--config", str(config_path)])
    assert result.exit_code != 0
    assert "panelize" in result.output.lower()


def test_panelize_config_unknown_option_does_not_write_file(tmp_path: Path) -> None:
    """Validation errors surface *before* any JSON is written to disk."""
    config_path = _write_config(
        tmp_path,
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[panelize]\n"
        'modules = ["mcu.kicad_pcb"]\n'
        'mystery = "what is this"\n',
    )
    runner = CliRunner()
    result = runner.invoke(main, ["panelize-config", "--config", str(config_path)])
    assert result.exit_code != 0
    assert "mystery" in result.output
    assert not (tmp_path / "panel.kikit.json").exists()
