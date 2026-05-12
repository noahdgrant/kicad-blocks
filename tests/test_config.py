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
