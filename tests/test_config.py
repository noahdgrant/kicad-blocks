"""Tests for the ``config`` module."""

from pathlib import Path

import pytest

from kicad_blocks.config import (
    OUTLINE_FRAME,
    OUTLINE_TIGHTFRAME,
    SEPARATION_MOUSE_BITES,
    SEPARATION_TABS,
    ConfigError,
    InvalidConfigError,
    load_config,
)

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


def test_block_allow_layer_mismatch_defaults_to_false(tmp_path: Path) -> None:
    """A block without ``allow_layer_mismatch`` keeps the strict-stackup default."""
    config_path = tmp_path / "kicad-blocks.toml"
    config_path.write_text(
        'project = "x"\nsources = ["a.kicad_pcb"]\n\n[blocks.mcu]\nsheet = "sheets/mcu.kicad_sch"\n'
    )
    config = load_config(config_path)
    assert config.blocks["mcu"].allow_layer_mismatch is False


def test_block_allow_layer_mismatch_parses_true(tmp_path: Path) -> None:
    """``allow_layer_mismatch = true`` flows into the BlockSpec."""
    config_path = tmp_path / "kicad-blocks.toml"
    config_path.write_text(
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[blocks.mcu]\n"
        'sheet = "sheets/mcu.kicad_sch"\n'
        "allow_layer_mismatch = true\n"
    )
    config = load_config(config_path)
    assert config.blocks["mcu"].allow_layer_mismatch is True


def test_block_allow_layer_mismatch_wrong_type_reports_error(tmp_path: Path) -> None:
    """``allow_layer_mismatch`` must be a boolean."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text(
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[blocks.mcu]\n"
        'sheet = "sheets/mcu.kicad_sch"\n'
        'allow_layer_mismatch = "yes"\n'
    )
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    assert any("allow_layer_mismatch" in err.message for err in excinfo.value.errors)


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


def test_panelize_absent_by_default(tmp_path: Path) -> None:
    """A config without a ``[panelize]`` table loads with ``panelize=None``."""
    config_path = tmp_path / "kicad-blocks.toml"
    config_path.write_text('project = "x"\nsources = ["a.kicad_pcb"]\n')
    config = load_config(config_path)
    assert config.panelize is None


def test_panelize_minimal_uses_defaults(tmp_path: Path) -> None:
    """``[panelize]`` with only ``modules`` fills in the default spacing/outline."""
    config_path = tmp_path / "kicad-blocks.toml"
    config_path.write_text(
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[panelize]\n"
        'modules = ["../mcu/mcu.kicad_pcb", "../power/power.kicad_pcb"]\n'
    )
    config = load_config(config_path)
    assert config.panelize is not None
    assert config.panelize.modules == (
        Path("../mcu/mcu.kicad_pcb"),
        Path("../power/power.kicad_pcb"),
    )
    assert config.panelize.spacing == "2mm"
    assert config.panelize.separation == SEPARATION_TABS
    assert config.panelize.outline == OUTLINE_FRAME
    assert config.panelize.fiducials is False


def test_panelize_full_round_trip(tmp_path: Path) -> None:
    """Every panelize field round-trips when explicitly declared."""
    config_path = tmp_path / "kicad-blocks.toml"
    config_path.write_text(
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[panelize]\n"
        'modules = ["../mcu/mcu.kicad_pcb"]\n'
        'spacing = "3mm"\n'
        'separation = "mouse_bites"\n'
        'outline = "tightframe"\n'
        "fiducials = true\n"
    )
    config = load_config(config_path)
    assert config.panelize is not None
    assert config.panelize.spacing == "3mm"
    assert config.panelize.separation == SEPARATION_MOUSE_BITES
    assert config.panelize.outline == OUTLINE_TIGHTFRAME
    assert config.panelize.fiducials is True


def test_panelize_missing_modules_reports_error(tmp_path: Path) -> None:
    """``[panelize]`` without ``modules`` raises a structured error."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text('project = "x"\nsources = ["a.kicad_pcb"]\n\n[panelize]\nspacing = "2mm"\n')
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    assert any("panelize.modules" in (err.key_path or "") for err in excinfo.value.errors)


def test_panelize_empty_modules_reports_error(tmp_path: Path) -> None:
    """An empty ``modules`` list raises a structured error."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text('project = "x"\nsources = ["a.kicad_pcb"]\n\n[panelize]\nmodules = []\n')
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    assert any("at least one" in err.message for err in excinfo.value.errors)


def test_panelize_unknown_option_reports_error(tmp_path: Path) -> None:
    """Unrecognized panelize keys are a hard error so typos don't silently no-op."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text(
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[panelize]\n"
        'modules = ["a.kicad_pcb"]\n'
        'mystery = "what is this"\n'
    )
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    assert any("mystery" in err.message for err in excinfo.value.errors)


def test_panelize_bad_separation_reports_error(tmp_path: Path) -> None:
    """``separation`` must be one of the documented options."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text(
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[panelize]\n"
        'modules = ["a.kicad_pcb"]\n'
        'separation = "scoring"\n'
    )
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    assert any("separation" in err.message for err in excinfo.value.errors)


def test_panelize_bad_outline_reports_error(tmp_path: Path) -> None:
    """``outline`` must be one of the documented options."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text(
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[panelize]\n"
        'modules = ["a.kicad_pcb"]\n'
        'outline = "donut"\n'
    )
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    assert any("outline" in err.message for err in excinfo.value.errors)


def test_panelize_fiducials_wrong_type_reports_error(tmp_path: Path) -> None:
    """``fiducials`` must be a boolean."""
    bad = tmp_path / "kicad-blocks.toml"
    bad.write_text(
        'project = "x"\n'
        'sources = ["a.kicad_pcb"]\n'
        "\n[panelize]\n"
        'modules = ["a.kicad_pcb"]\n'
        'fiducials = "yes"\n'
    )
    with pytest.raises(InvalidConfigError) as excinfo:
        load_config(bad)
    assert any("fiducials" in err.message for err in excinfo.value.errors)
