from importlib.metadata import version

import click

_HELP = "Share schematic sheets across multiple KiCAD projects and reuse their PCB layouts."


@click.group(help=_HELP)
@click.version_option(version("kicad-blocks"), prog_name="kicad-blocks")
def main() -> None:
    """kicad-blocks CLI entry point."""


if __name__ == "__main__":
    main()
