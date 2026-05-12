"""Text-mode output for the CLI subcommands.

The reporter is the seam through which CLI commands turn domain values into
human-readable lines. ``--format json`` lives in a later slice (issue #12); for
now the JSON shaping happens inside each formatter via a stable internal
structure so it's easy to plug a renderer in later.
"""

from __future__ import annotations

from kicad_blocks.block import ApplyPlan
from kicad_blocks.config import ConfigError
from kicad_blocks.kicad_io import Footprint


def format_config_errors(errors: list[ConfigError]) -> str:
    """Render a list of config errors as a multi-line string.

    The shape is ``<path>:<line>:<column>: <message>`` so the output plays well
    with editor "jump to file location" parsers, mirroring the conventions of
    compilers and most linters.
    """
    lines: list[str] = []
    for err in errors:
        location = str(err.path)
        if err.line is not None:
            location += f":{err.line}"
            if err.column is not None:
                location += f":{err.column}"
        prefix = f"{location}: error:"
        suffix = err.message
        if err.key_path:
            suffix = f"[{err.key_path}] {suffix}"
        lines.append(f"{prefix} {suffix}")
    return "\n".join(lines)


def format_validate_ok(config_label: str) -> str:
    """Single-line success report from ``validate``."""
    return f"ok: {config_label}"


def format_validate_problems(problems: list[str]) -> str:
    """Multi-line failure report for ``validate`` runtime problems.

    Used for non-config issues — missing PCBs, unparseable PCBs, undeclared
    sheets, etc. Each problem is already a complete sentence supplied by the
    caller; the reporter just prefixes them with ``error:``.
    """
    return "\n".join(f"error: {problem}" for problem in problems)


def format_footprint_list(footprints: list[Footprint]) -> str:
    """Render a list of footprints in a compact, scannable table.

    The columns are intentionally fixed-width so a human can grep the output;
    if a future slice needs JSON, ``list-block --format json`` will render
    from the same input.
    """
    if not footprints:
        return "(no footprints)"
    rows: list[str] = []
    rows.append(f"{'REF':<8} {'UUID':<38} {'LAYER':<6} {'POS':<22} {'ROT':>6}")
    for fp in footprints:
        pos = f"({fp.position[0]:.3f}, {fp.position[1]:.3f})"
        ref = fp.reference or "?"
        uuid = fp.symbol_uuid or "-"
        rows.append(f"{ref:<8} {uuid:<38} {fp.layer:<6} {pos:<22} {fp.rotation:>6.1f}")
    return "\n".join(rows)


def format_apply_plan(plan: ApplyPlan, *, dry_run: bool) -> str:
    """Render an :class:`ApplyPlan` as a human-readable summary.

    Used by ``reuse --dry-run`` to show what would change, and by the apply
    path to log what was just written. The format is intentionally close to a
    code-review diff: anchor identification, then a per-footprint table, then
    tracks/vias, then warnings.
    """
    lines: list[str] = []
    header = "plan" if dry_run else "applied"
    lines.append(
        f"{header}: block on sheet '{plan.sheet}', anchor "
        f"{plan.source_anchor_ref} → {plan.target_anchor_ref} "
        f"(rotation {plan.transform_angle_deg:g}°)"
    )
    if plan.placements:
        lines.append(f"  {'REF':<10} {'UUID':<38} {'FROM':<22} {'TO':<22} {'ROT':>7}")
        for p in plan.placements:
            frm = f"({p.source_position[0]:.3f}, {p.source_position[1]:.3f})"
            to = f"({p.target_position[0]:.3f}, {p.target_position[1]:.3f})"
            ref = f"{p.source_reference}→{p.target_reference}"
            lines.append(
                f"  {ref:<10} {p.symbol_uuid:<38} {frm:<22} {to:<22} {p.target_rotation:>7.1f}"
            )
    else:
        lines.append("  (no footprints to move)")
    if plan.tracks:
        verb = "tracks (to append)" if dry_run else "tracks (appended)"
        lines.append(f"  {verb}:")
        for t in plan.tracks:
            lines.append(
                f"    {t.net_name:<10} {t.layer:<6} "
                f"({t.start[0]:.3f}, {t.start[1]:.3f}) → "
                f"({t.end[0]:.3f}, {t.end[1]:.3f}) w={t.width:g}"
            )
    if plan.vias:
        verb = "vias (to append)" if dry_run else "vias (appended)"
        lines.append(f"  {verb}:")
        for v in plan.vias:
            layer_span = "/".join(v.layers)
            lines.append(
                f"    {v.net_name:<10} {layer_span:<14} "
                f"({v.position[0]:.3f}, {v.position[1]:.3f}) "
                f"size={v.size:g} drill={v.drill:g}"
            )
    if plan.excluded_tracks:
        lines.append("  warning: tracks not copied (endpoint outside the block):")
        for t in plan.excluded_tracks:
            lines.append(
                f"    - {t.net:<10} ({t.start[0]:.3f}, {t.start[1]:.3f}) → "
                f"({t.end[0]:.3f}, {t.end[1]:.3f})"
            )
    if plan.excluded_vias:
        lines.append("  warning: vias not copied (position outside the block):")
        for v in plan.excluded_vias:
            lines.append(f"    - {v.net:<10} ({v.position[0]:.3f}, {v.position[1]:.3f})")
    if plan.unmatched_source:
        lines.append("  warning: source footprints with no target counterpart:")
        for sym in plan.unmatched_source:
            lines.append(f"    - {sym}")
    if plan.unresolved_nets:
        lines.append(
            "  warning: source net(s) with no resolvable target "
            "(declare overrides under [blocks.<name>.net_map]):"
        )
        for net in plan.unresolved_nets:
            lines.append(f"    - {net}")
    return "\n".join(lines)
