"""Tests for the ``config`` module."""

from pathlib import Path

import pytest

from kicad_blocks.config import ConfigError, InvalidConfigError, load_config

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "minimal"


def test_load_config_returns_typed_config() -> None:
    """A valid config returns the typed Config object."""
    config = load_config(FIXTURE_DIR / "kicad-blocks.toml")

    assert config.project == "minimal"
    assert config.sources == (Path("minimal.kicad_pcb"),)
    assert set(config.blocks) == {"mcu"}
    assert config.blocks["mcu"].sheet == Path("sheets/mcu.kicad_sch")
    assert config.project_dir == FIXTURE_DIR


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    """A missing config file surfaces a clear error."""
    missing = tmp_path / "kicad-blocks.toml"
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(missing)
    errors = excinfo.value.errors
    assert len(errors) == 1
    assert "not found" in errors[0].message.lower()
    assert errors[0].path == missing


def test_toml_syntax_error_reports_line(tmp_path: Path) -> None:
    """TOML syntax errors surface with a line number."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text('project = "x"\nsources = [\n')
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    errors = excinfo.value.errors
    assert any(err.line is not None for err in errors)


def test_missing_required_project_key(tmp_path: Path) -> None:
    """A config missing the project key surfaces a structured error."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text('sources = ["a.kicad_pcb"]\n')
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    errors = excinfo.value.errors
    messages = [err.message for err in errors]
    assert any("project" in msg for msg in messages)


def test_wrong_type_for_sources(tmp_path: Path) -> None:
    """``sources`` must be a list of strings; otherwise we get a typed error."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text('project = "x"\nsources = "not-a-list.kicad_pcb"\n')
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    errors = excinfo.value.errors
    assert any("sources" in err.message for err in errors)
    assert any(err.line == 2 for err in errors)


def test_block_missing_sheet_key(tmp_path: Path) -> None:
    """A block table missing the sheet key surfaces with a key path."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text('project = "x"\nsources = ["a.kicad_pcb"]\n\n[blocks.mcu]\nfoo = "bar"\n')
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    errors = excinfo.value.errors
    assert any("blocks.mcu" in (err.key_path or "") for err in errors)
    assert any("sheet" in err.message for err in errors)


def test_config_error_is_dataclass_like() -> None:
    """ConfigError carries path, message, line, column, key_path."""
    err = ConfigError(path=Path("x"), message="bad", line=3, column=2, key_path="a.b")
    assert err.line == 3
    assert err.column == 2
    assert err.key_path == "a.b"


def test_block_source_and_anchor_are_optional(tmp_path: Path) -> None:
    """``source`` and ``anchor`` round-trip when present, default to ``None`` otherwise."""
    config_path = tmp_path / "kicad-blocks.toml"
    config_path.write_text(
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        'target = "a.kicad_pcb"\n'
        "\n[blocks.mcu]\n"
        'sheet = "sheets/mcu.kicad_sch"\n'
        'source = "../canonical/canonical.kicad_pcb"\n'
        'anchor = "U7"\n'
        "\n[blocks.power]\n"
        'sheet = "sheets/power.kicad_sch"\n'
    )
    config = load_config(config_path)
    assert config.target == Path("a.kicad_pcb")
    assert config.blocks["mcu"].source == Path("../canonical/canonical.kicad_pcb")
    assert config.blocks["mcu"].anchor == "U7"
    assert config.blocks["power"].source is None
    assert config.blocks["power"].anchor is None


def test_target_wrong_type_reports_error(tmp_path: Path) -> None:
    """``target`` must be a string; non-string values surface a typed error."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text('project = "x"\nsources = ["a.kicad_pcb"]\ntarget = 42\n')
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    assert any("target" in err.message for err in excinfo.value.errors)


def test_block_anchor_wrong_type_reports_error(tmp_path: Path) -> None:
    """``anchor`` must be a string."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text(
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[blocks.mcu]\n"
        'sheet = "sheets/mcu.kicad_sch"\n'
        "anchor = 7\n"
    )
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    assert any("anchor" in err.message for err in excinfo.value.errors)


def test_block_net_map_parses(tmp_path: Path) -> None:
    """``[blocks.<name>.net_map]`` parses into the BlockSpec's net_map mapping."""
    config_path = tmp_path / "kicad-blocks.toml"
    config_path.write_text(
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[blocks.mcu]\n"
        'sheet = "sheets/mcu.kicad_sch"\n'
        "\n[blocks.mcu.net_map]\n"
        '"+3v3_source" = "+3V3"\n'
        '"GND_OLD" = "GND"\n'
    )
    config = load_config(config_path)
    assert config.blocks["mcu"].net_map == {"+3v3_source": "+3V3", "GND_OLD": "GND"}


def test_block_net_map_defaults_to_empty(tmp_path: Path) -> None:
    """When ``net_map`` is absent the BlockSpec's net_map is an empty mapping."""
    config_path = tmp_path / "kicad-blocks.toml"
    config_path.write_text(
        'project = "x"\nsources = ["a.kicad_pcb"]\n\n[blocks.mcu]\nsheet = "sheets/mcu.kicad_sch"\n'
    )
    config = load_config(config_path)
    assert config.blocks["mcu"].net_map == {}


def test_block_net_map_wrong_type_reports_error(tmp_path: Path) -> None:
    """``net_map`` must be a table; a scalar value surfaces a typed error."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text(
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[blocks.mcu]\n"
        'sheet = "sheets/mcu.kicad_sch"\n'
        'net_map = "not-a-table"\n'
    )
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    assert any("net_map" in err.message for err in excinfo.value.errors)


def test_block_net_map_non_string_values_report_error(tmp_path: Path) -> None:
    """Each entry in ``net_map`` must be a string-to-string pair."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text(
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[blocks.mcu]\n"
        'sheet = "sheets/mcu.kicad_sch"\n'
        "\n[blocks.mcu.net_map]\n"
        '"+3v3_source" = 7\n'
    )
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    assert any("net_map" in err.message for err in excinfo.value.errors)
