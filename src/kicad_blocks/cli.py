"""Click entry points for the ``kicad-blocks`` CLI.

Each subcommand is a thin shell: load config → call into the domain core →
hand the result to the reporter. Business logic stays out of this file.
"""

from __future__ import annotations

import json
import math
from importlib.metadata import version
from pathlib import Path

import click

from kicad_blocks import block as block_module
from kicad_blocks.block import ApplyError, ApplyPlan, footprints_in_sheet, plan_apply
from kicad_blocks.config import BlockSpec, Config, InvalidConfigError, load_config
from kicad_blocks.diff import compute_diff
from kicad_blocks.kicad_io import Footprint, KicadIoError, Pcb, apply_placements, load_pcb
from kicad_blocks.kikit_config import build_kikit_preset
from kicad_blocks.reporter import (
    format_apply_plan,
    format_block_diff,
    format_config_errors,
    format_footprint_list,
    format_validate_ok,
    format_validate_problems,
)
from kicad_blocks.scaffold import ScaffoldError, scaffold_project
from kicad_blocks.sync_state import (
    BlockState,
    LockFile,
    LockFileError,
    hash_applied_block,
    hash_file,
    hash_target_block_state,
    lock_path_for,
    read_lock,
    write_lock,
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


@main.command()
@_CONFIG_OPTION
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the planned placements without modifying the target PCB.",
)
def reuse(config_path: Path, dry_run: bool) -> None:
    """Apply source-PCB layouts to the target PCB by anchor-relative placement.

    For each ``[blocks.<name>]`` entry that declares both ``source`` and
    ``anchor``, opens the source PCB, finds the target anchor by refdes,
    transforms the source-block footprints into the target's frame, and
    rewrites their positions. ``--dry-run`` prints the plan without writing.
    """
    try:
        config = load_config(config_path)
    except InvalidConfigError as exc:
        click.echo(format_config_errors(exc.errors))
        raise SystemExit(1) from None

    if config.target is None:
        click.echo("error: 'target' is not set in the config; reuse requires a target PCB")
        raise SystemExit(1)

    actionable = [b for b in config.blocks.values() if b.source and b.anchor]
    if not actionable:
        click.echo(
            "error: no blocks have both 'source' and 'anchor' set; "
            "reuse needs at least one such block to act on"
        )
        raise SystemExit(1)

    target_path = _resolve(config, config.target)
    plans: list[tuple[BlockSpec, ApplyPlan]] = []
    for spec in actionable:
        try:
            plan = _plan_block(config, spec, target_path)
        except (KicadIoError, ApplyError) as exc:
            click.echo(f"error: {exc}")
            raise SystemExit(1) from None
        plans.append((spec, plan))
        click.echo(format_apply_plan(plan, dry_run=dry_run))

    if dry_run:
        return

    blocked = [(spec, plan) for spec, plan in plans if plan.unresolved_nets]
    if blocked:
        for spec, plan in blocked:
            click.echo(
                f"error: block '{spec.name}' has unresolved net(s) "
                f"{list(plan.unresolved_nets)} — declare overrides in "
                f"[blocks.{spec.name}.net_map] or rename in the target PCB"
            )
        raise SystemExit(1)

    all_placements = [p.placement for _, plan in plans for p in plan.placements]
    all_tracks = [t for _, plan in plans for t in plan.tracks]
    all_vias = [v for _, plan in plans for v in plan.vias]
    all_zones = [z for _, plan in plans for z in plan.zones]
    all_graphics = [g for _, plan in plans for g in plan.graphics]
    if not (all_placements or all_tracks or all_vias or all_zones or all_graphics):
        return
    try:
        apply_placements(
            target_path,
            all_placements,
            tracks=all_tracks,
            vias=all_vias,
            zones=all_zones,
            graphics=all_graphics,
        )
    except KicadIoError as exc:
        click.echo(f"error: {exc}")
        raise SystemExit(1) from None

    _write_lock(config, plans)


@main.command()
@_CONFIG_OPTION
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the diff between the current source and target without writing.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt and override hand-edit conflicts.",
)
def sync(config_path: Path, dry_run: bool, force: bool) -> None:
    """Bring the target's block region back in step with the source.

    Without ``--dry-run``: compute the structured diff for each block, refuse
    to apply if the target's current block-region hash diverges from the
    lock's ``applied_block_hash`` (unless ``--force``), prompt y/N for
    confirmation (unless ``--force``), then apply atomically. The lock is
    refreshed on success.

    With ``--dry-run``: print the diff only.
    """
    try:
        config = load_config(config_path)
    except InvalidConfigError as exc:
        click.echo(format_config_errors(exc.errors))
        raise SystemExit(1) from None

    if config.target is None:
        click.echo("error: 'target' is not set in the config; sync requires a target PCB")
        raise SystemExit(1)

    actionable = [b for b in config.blocks.values() if b.source and b.anchor]
    if not actionable:
        click.echo(
            "error: no blocks have both 'source' and 'anchor' set; "
            "sync needs at least one such block to act on"
        )
        raise SystemExit(1)

    lock_path = lock_path_for(config.project_dir, config.project)
    try:
        lock = read_lock(lock_path)
    except LockFileError as exc:
        click.echo(f"error: {exc}")
        click.echo("hint: run 'kicad-blocks reuse' first to establish a baseline lock file")
        raise SystemExit(1) from None

    target_path = _resolve(config, config.target)
    blocks: list[tuple[BlockSpec, ApplyPlan, Pcb]] = []
    diffs: list[tuple[BlockSpec, object]] = []
    conflicts: list[str] = []
    for spec in actionable:
        assert spec.source is not None
        assert spec.anchor is not None
        source_path = _resolve(config, spec.source)
        try:
            source_pcb = load_pcb(source_path)
            target_pcb = load_pcb(target_path)
        except KicadIoError as exc:
            click.echo(f"error: {exc}")
            raise SystemExit(1) from None

        diff = compute_diff(
            source_pcb=source_pcb,
            target_pcb=target_pcb,
            sheet=str(spec.sheet),
            anchor_ref=spec.anchor,
            net_overrides=spec.net_map,
        )
        diffs.append((spec, diff))
        click.echo(format_block_diff(spec.name, diff))

        if dry_run:
            continue

        try:
            plan = plan_apply(
                source_pcb=source_pcb,
                target_pcb=target_pcb,
                sheet=str(spec.sheet),
                anchor_ref=spec.anchor,
                net_overrides=spec.net_map,
                allow_layer_mismatch=spec.allow_layer_mismatch,
            )
        except (KicadIoError, ApplyError) as exc:
            click.echo(f"error: {exc}")
            raise SystemExit(1) from None

        prior_state = lock.blocks.get(spec.name)
        if prior_state is not None:
            current_hash = hash_target_block_state(
                target_pcb=target_pcb,
                sheet=str(spec.sheet),
                anchor_ref=spec.anchor,
                transform_angle_deg=plan.transform_angle_deg,
            )
            if current_hash != prior_state.applied_block_hash:
                conflicts.append(spec.name)

        blocks.append((spec, plan, target_pcb))

    if dry_run:
        return

    if conflicts and not force:
        names = ", ".join(f"'{name}'" for name in conflicts)
        click.echo(
            f"error: hand-edits detected in target block region(s): {names} — "
            f"re-run with --force to overwrite them with the source layout"
        )
        raise SystemExit(1)

    if all(_diff_is_empty(d) for _, d in diffs):
        click.echo("no changes to apply")
        return

    if any(plan.unresolved_nets for _, plan, _ in blocks):
        for spec, plan, _ in blocks:
            if plan.unresolved_nets:
                click.echo(
                    f"error: block '{spec.name}' has unresolved net(s) "
                    f"{list(plan.unresolved_nets)} — declare overrides in "
                    f"[blocks.{spec.name}.net_map] or rename in the target PCB"
                )
        raise SystemExit(1)

    if not force and not click.confirm("apply these changes to the target PCB?", default=False):
        click.echo("aborted; target left untouched")
        raise SystemExit(1)

    purge_pad_positions: list[tuple[float, float]] = []
    for spec, _, target_pcb in blocks:
        purge_pad_positions.extend(
            _absolute_pad_positions(footprints_in_sheet(target_pcb, str(spec.sheet)))
        )

    all_placements = [p.placement for _, plan, _ in blocks for p in plan.placements]
    all_tracks = [t for _, plan, _ in blocks for t in plan.tracks]
    all_vias = [v for _, plan, _ in blocks for v in plan.vias]
    all_zones = [z for _, plan, _ in blocks for z in plan.zones]
    all_graphics = [g for _, plan, _ in blocks for g in plan.graphics]
    try:
        apply_placements(
            target_path,
            all_placements,
            tracks=all_tracks,
            vias=all_vias,
            zones=all_zones,
            graphics=all_graphics,
            purge_in_block_pad_positions=purge_pad_positions,
        )
    except KicadIoError as exc:
        click.echo(f"error: {exc}")
        raise SystemExit(1) from None

    _write_lock(config, [(spec, plan) for spec, plan, _ in blocks])
    click.echo("sync applied; lock updated")


@main.command()
@click.option(
    "--name",
    required=True,
    help="Project name. Used for the generated file stems and the config 'project' key.",
)
@click.option(
    "--sheet",
    "sheets",
    multiple=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Path to a shared .kicad_sch to wire up. Pass --sheet repeatedly to include several.",
)
@click.option(
    "--dir",
    "base_dir",
    default=".",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to create the new project directory inside (default: current directory).",
    show_default=True,
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Write into the project directory even if it already exists.",
)
def scaffold(name: str, sheets: tuple[Path, ...], base_dir: Path, force: bool) -> None:
    """Generate a new KiCAD project skeleton wired up to a set of shared sheets.

    Writes a ``.kicad_pro``, root ``.kicad_sch`` (with hierarchical sheet refs),
    empty ``.kicad_pcb`` with a placeholder ``Edge.Cuts`` outline, and a starter
    ``kicad-blocks.toml`` under ``<dir>/<name>/``.
    """
    try:
        project_dir = scaffold_project(name, list(sheets), base_dir=base_dir, force=force)
    except ScaffoldError as exc:
        click.echo(f"error: {exc}")
        raise SystemExit(1) from None
    click.echo(f"scaffolded project at {project_dir}")


@main.command("panelize-config")
@_CONFIG_OPTION
@click.option(
    "--out",
    "out_path",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Path to write the KiKit preset JSON (default: panel.kikit.json next to the config).",
)
def panelize_config(config_path: Path, out_path: Path | None) -> None:
    """Emit a KiKit ``panelize`` preset from the project config's ``[panelize]`` table.

    Reads ``kicad-blocks.toml``, translates the ``[panelize]`` declaration into
    a KiKit-compatible JSON preset, and writes it to ``--out`` (or
    ``panel.kikit.json`` next to the config). The user runs KiKit themselves —
    we never invoke a subprocess.
    """
    try:
        config = load_config(config_path)
    except InvalidConfigError as exc:
        click.echo(format_config_errors(exc.errors))
        raise SystemExit(1) from None

    if config.panelize is None:
        click.echo("error: no [panelize] table in config; declare one to use panelize-config")
        raise SystemExit(1)

    preset = build_kikit_preset(config.panelize)
    destination = out_path if out_path is not None else config.project_dir / "panel.kikit.json"
    destination.write_text(json.dumps(preset, indent=2) + "\n")
    click.echo(f"wrote KiKit preset to {destination}")


def _write_lock(config: Config, plans: list[tuple[BlockSpec, ApplyPlan]]) -> None:
    """Persist the per-block apply record next to the config."""
    plugin_ver = version("kicad-blocks")
    blocks: dict[str, BlockState] = {}
    for spec, plan in plans:
        assert spec.source is not None
        source_path = _resolve(config, spec.source)
        blocks[spec.name] = BlockState(
            source=str(spec.source),
            source_pcb_hash=hash_file(source_path),
            applied_block_hash=hash_applied_block(plan),
            anchor_refdes=plan.target_anchor_ref,
            sheet=str(spec.sheet),
        )
    write_lock(
        lock_path_for(config.project_dir, config.project),
        LockFile(plugin_version=plugin_ver, blocks=blocks),
    )


def _plan_block(config: Config, spec: BlockSpec, target_path: Path) -> ApplyPlan:
    """Open the source/target PCBs and return the apply plan for ``spec``."""
    assert spec.source is not None  # filtered upstream
    assert spec.anchor is not None
    source_path = _resolve(config, spec.source)
    source_pcb = load_pcb(source_path)
    target_pcb = load_pcb(target_path)
    return plan_apply(
        source_pcb=source_pcb,
        target_pcb=target_pcb,
        sheet=str(spec.sheet),
        anchor_ref=spec.anchor,
        net_overrides=spec.net_map,
        allow_layer_mismatch=spec.allow_layer_mismatch,
    )


def _resolve(config: Config, path: Path) -> Path:
    """Resolve ``path`` against the config's project directory if relative."""
    return path if path.is_absolute() else (config.project_dir / path)


def _diff_is_empty(diff: object) -> bool:
    """Return ``True`` when ``diff`` has no entries in any category."""
    return bool(getattr(diff, "is_empty", False))


def _absolute_pad_positions(footprints: list[Footprint]) -> list[tuple[float, float]]:
    """Return every pad's absolute world position across ``footprints``."""
    positions: list[tuple[float, float]] = []
    for fp in footprints:
        a = fp.rotation % 360.0
        if a == 0.0:
            cos_r, sin_r = 1.0, 0.0
        elif a == 90.0:
            cos_r, sin_r = 0.0, 1.0
        elif a == 180.0:
            cos_r, sin_r = -1.0, 0.0
        elif a == 270.0:
            cos_r, sin_r = 0.0, -1.0
        else:
            theta = math.radians(a)
            cos_r, sin_r = math.cos(theta), math.sin(theta)
        fx, fy = fp.position
        for pad in fp.pads:
            px, py = pad.position
            positions.append((fx + cos_r * px - sin_r * py, fy + sin_r * px + cos_r * py))
    return positions


if __name__ == "__main__":
    main()
