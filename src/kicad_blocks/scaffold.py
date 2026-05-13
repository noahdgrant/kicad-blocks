"""Generate a new KiCAD project skeleton wired up to shared sheets.

The scaffold writes four files into a fresh project directory:

* ``<name>.kicad_pro`` — minimal valid JSON project file
* ``<name>.kicad_sch`` — root schematic with hierarchical sheet refs
* ``<name>.kicad_pcb`` — empty board with a placeholder ``Edge.Cuts`` outline
* ``kicad-blocks.toml`` — initial config listing the chosen sheets

kiutils is used to construct the schematic and board so the output round-trips
through ``Schematic.from_file`` / ``Board.from_file`` — the same path the rest
of the codebase uses to read these files. The ``.kicad_pro`` is hand-built JSON:
kiutils does not model project files, and KiCAD fills in any missing keys on
first open anyway.
"""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false

from __future__ import annotations

import json
import os.path
import uuid as uuid_module
from collections.abc import Sequence
from pathlib import Path

from kiutils.board import Board
from kiutils.items.common import Position, Property
from kiutils.items.gritems import GrRect
from kiutils.items.schitems import HierarchicalSheet
from kiutils.schematic import Schematic


class ScaffoldError(Exception):
    """Raised when scaffold inputs are invalid or the target directory is in the way."""


def scaffold_project(
    name: str,
    sheets: Sequence[Path],
    *,
    base_dir: Path,
    force: bool = False,
) -> Path:
    """Generate a new KiCAD project skeleton at ``<base_dir>/<name>``.

    Args:
        name: The project's logical name. Becomes the file stem for
            ``<name>.kicad_pro``, ``<name>.kicad_sch``, and ``<name>.kicad_pcb``.
        sheets: Paths to the shared ``.kicad_sch`` files the project should
            reference. Each becomes a hierarchical sheet block in the root
            schematic and a ``[blocks.<sheet-stem>]`` entry in the config.
            Paths are resolved against the caller's current directory and
            stored in the generated files as paths relative to the project
            directory.
        base_dir: Directory the new project directory is created inside.
        force: Allow writing into ``<base_dir>/<name>`` even if it already
            exists. Existing files unrelated to the scaffold are left alone;
            only files the scaffold writes are overwritten.

    Returns:
        The absolute path to the new project directory.

    Raises:
        ScaffoldError: If ``sheets`` is empty, any sheet path does not exist,
            or ``<base_dir>/<name>`` exists and ``force`` is ``False``.
    """
    if not sheets:
        msg = "scaffold requires at least one --sheet path"
        raise ScaffoldError(msg)

    resolved_sheets: list[Path] = []
    for sheet in sheets:
        absolute = sheet if sheet.is_absolute() else Path.cwd() / sheet
        if not absolute.exists():
            msg = f"sheet file not found: {sheet}"
            raise ScaffoldError(msg)
        resolved_sheets.append(absolute.resolve())

    project_dir = (base_dir / name).resolve()
    if project_dir.exists() and not force:
        msg = (
            f"project directory already exists: {project_dir} "
            f"(re-run with --force to write into it)"
        )
        raise ScaffoldError(msg)

    project_dir.mkdir(parents=True, exist_ok=True)

    relative_sheets = [_relative_to(s, project_dir) for s in resolved_sheets]

    _write_kicad_pro(project_dir, name)
    _write_kicad_sch(project_dir, name, relative_sheets)
    _write_kicad_pcb(project_dir, name)
    _write_config(project_dir, name, relative_sheets)

    return project_dir


def _relative_to(target: Path, start: Path) -> Path:
    """Return ``target`` expressed relative to ``start`` using forward slashes.

    Uses ``os.path.relpath`` semantics so sheets outside ``start`` get a
    ``../`` prefix instead of raising. The result is normalized to forward
    slashes so the generated files match KiCAD's on-disk convention on every
    platform.
    """
    rel = os.path.relpath(target, start)
    return Path(rel.replace("\\", "/"))


def _write_kicad_pro(project_dir: Path, name: str) -> None:
    """Write a minimal valid ``.kicad_pro`` (JSON) into ``project_dir``."""
    data = {
        "board": {"design_settings": {"defaults": {}, "rules": {}}},
        "boards": [],
        "cvpcb": {"equivalence_files": []},
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": f"{name}.kicad_pro", "version": 1},
        "net_settings": {"classes": [], "meta": {"version": 3}},
        "pcbnew": {"last_paths": {}},
        "schematic": {},
        "sheets": [[str(uuid_module.uuid4()), "Root"]],
        "text_variables": {},
    }
    (project_dir / f"{name}.kicad_pro").write_text(json.dumps(data, indent=2) + "\n")


def _write_kicad_sch(project_dir: Path, name: str, sheets: Sequence[Path]) -> None:
    """Write the root schematic with one hierarchical sheet block per ``sheets`` entry."""
    schematic = Schematic.create_new()
    schematic.uuid = str(uuid_module.uuid4())

    for index, sheet in enumerate(sheets):
        block = HierarchicalSheet()
        block.position = Position(X=50.0 + 60.0 * index, Y=50.0)
        block.width = 50.0
        block.height = 30.0
        block.uuid = str(uuid_module.uuid4())
        block.sheetName = Property(key="Sheetname", value=sheet.stem)
        block.fileName = Property(key="Sheetfile", value=sheet.as_posix())
        schematic.sheets.append(block)

    (project_dir / f"{name}.kicad_sch").write_text(schematic.to_sexpr())


def _write_kicad_pcb(project_dir: Path, name: str) -> None:
    """Write an empty board with a placeholder rectangle on ``Edge.Cuts``."""
    board = Board.create_new()
    board.graphicItems.append(
        GrRect(
            start=Position(X=100.0, Y=80.0),
            end=Position(X=200.0, Y=180.0),
            layer="Edge.Cuts",
            width=0.1,
            tstamp=str(uuid_module.uuid4()),
        )
    )
    (project_dir / f"{name}.kicad_pcb").write_text(board.to_sexpr())


def _write_config(project_dir: Path, name: str, sheets: Sequence[Path]) -> None:
    """Write a starter ``kicad-blocks.toml`` listing each sheet under a commented stanza."""
    lines: list[str] = [
        f'project = "{name}"',
        f'sources = ["{name}.kicad_pcb"]',
        f'target = "{name}.kicad_pcb"',
        "",
    ]
    for sheet in sheets:
        block_name = sheet.stem
        lines.extend(
            [
                f"[blocks.{block_name}]",
                f'sheet = "{sheet.as_posix()}"',
                '# source = "../path/to/canonical-project.kicad_pcb"',
                f'# anchor = "U1"  # refdes of the anchor footprint in {name}.kicad_pcb',
                "",
            ]
        )
    (project_dir / "kicad-blocks.toml").write_text("\n".join(lines))
