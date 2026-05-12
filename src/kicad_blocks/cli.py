"""Click entry points for the ``kicad-blocks`` CLI.

Each subcommand is a thin shell: load config → call into the domain core →
hand the result to the reporter. Business logic stays out of this file.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import click

from kicad_blocks import block as block_module
from kicad_blocks.config import InvalidConfigError, load_config
from kicad_blocks.kicad_io import Footprint, KicadIoError, load_pcb
from kicad_blocks.reporter import (
    format_config_errors,
    format_footprint_list,
    format_validate_ok,
    format_validate_problems,
)

_HELP = "Share schematic sheets across multiple KiCAD projects and reuse their PCB layouts."

_CONFIG_OPTION = click.option(
    "--config",
    "-c",
    "config_path",
    default="kicad-blocks.toml",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Path to kicad-blocks.toml (default: ./kicad-blocks.toml).",
    show_default=True,
)


@click.group(help=_HELP)
@click.version_option(version("kicad-blocks"), prog_name="kicad-blocks")
def main() -> None:
    """kicad-blocks CLI entry point."""


@main.command()
@_CONFIG_OPTION
def validate(config_path: Path) -> None:
    """Validate a kicad-blocks.toml and the files it references.

    Loads the config, opens every referenced PCB, and verifies every declared
    block's sheet file exists. Reports all problems without modifying anything.
    Exits non-zero on the first set of problems found.
    """
    try:
        config = load_config(config_path)
    except InvalidConfigError as exc:
        click.echo(format_config_errors(exc.errors), err=False)
        raise SystemExit(1) from None

    problems: list[str] = []

    for source in config.sources:
        pcb_path = source if source.is_absolute() else config.project_dir / source
        try:
            load_pcb(pcb_path)
        except KicadIoError as exc:
            problems.append(str(exc))

    for block_spec in config.blocks.values():
        sheet_path = (
            block_spec.sheet
            if block_spec.sheet.is_absolute()
            else config.project_dir / block_spec.sheet
        )
        if not sheet_path.exists():
            problems.append(
                f"block '{block_spec.name}' references missing sheet file: {sheet_path}"
            )

    if problems:
        click.echo(format_validate_problems(problems))
        raise SystemExit(1)

    click.echo(format_validate_ok(str(config_path)))


@main.command("list-block")
@_CONFIG_OPTION
@click.option(
    "--sheet",
    required=True,
    help="Sheet path (as recorded in the footprint's Sheetfile property).",
)
def list_block(config_path: Path, sheet: str) -> None:
    """List footprints belonging to a given sheet across configured source PCBs.

    Loads the config, opens every referenced PCB, collects footprints whose
    ``Sheetfile`` matches ``--sheet``, and prints them as a compact table.
    """
    try:
        config = load_config(config_path)
    except InvalidConfigError as exc:
        click.echo(format_config_errors(exc.errors))
        raise SystemExit(1) from None

    all_footprints: list[Footprint] = []
    for source in config.sources:
        pcb_path = source if source.is_absolute() else config.project_dir / source
        try:
            pcb = load_pcb(pcb_path)
        except KicadIoError as exc:
            click.echo(f"error: {exc}")
            raise SystemExit(1) from None
        all_footprints.extend(block_module.footprints_in_sheet(pcb, sheet))

    click.echo(format_footprint_list(all_footprints))


if __name__ == "__main__":
    main()
