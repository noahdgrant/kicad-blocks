from click.testing import CliRunner

from kicad_blocks import __version__
from kicad_blocks.cli import main


def test_version_flag_prints_version() -> None:
    """`--version` should print the package version and exit cleanly."""
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_includes_description() -> None:
    """`--help` should include the top-level command description."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "schematic sheets" in result.output.lower()
