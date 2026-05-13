"""Text-mode and JSON-mode output for the CLI subcommands.

The reporter is the seam through which CLI commands turn domain values into
output. Two parallel renderers live here:

- ``format_*`` functions return strings for the default ``--format text`` mode.
- ``json_*`` functions return JSON-safe ``dict`` fragments. The CLI assembles
  those into a single envelope (:func:`json_envelope`) so JSON consumers see
  one document per command invocation.

The JSON envelope is versioned (``schema_version: 1``) and shared by success
and failure cases — a consumer parses one stream and reads ``ok`` to branch.
"""

from __future__ import annotations

import json
from typing import Any

from kicad_blocks.block import ApplyPlan, PlannedPlacement
from kicad_blocks.config import ConfigError
from kicad_blocks.diff import BlockDiff
from kicad_blocks.kicad_io import (
    Footprint,
    GraphicPlacement,
    TrackPlacement,
    ViaPlacement,
    ZonePlacement,
)

SCHEMA_VERSION = 1


# ---- text renderers -------------------------------------------------------


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

    The columns are intentionally fixed-width so a human can grep the output.
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
    """Render an :class:`ApplyPlan` as a human-readable summary."""
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
    if plan.zones:
        verb = "zones (to append)" if dry_run else "zones (appended)"
        lines.append(f"  {verb}:")
        for z in plan.zones:
            layer_span = "/".join(z.layers) if z.layers else "(no layers)"
            net = z.net_name or "(unconnected)"
            lines.append(f"    {net:<10} {layer_span}")
    if plan.graphics:
        verb = "graphics (to append)" if dry_run else "graphics (appended)"
        lines.append(f"  {verb}:")
        for g in plan.graphics:
            lines.append(f"    {g.layer}")
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
    if plan.excluded_zones:
        lines.append("  warning: zones not copied (outline outside the block hull):")
        for z in plan.excluded_zones:
            net = z.net_name or "(unconnected)"
            layer_span = "/".join(z.layers) if z.layers else "(no layers)"
            lines.append(f"    - {net:<10} {layer_span}")
    if plan.excluded_graphics:
        lines.append("  warning: graphics not copied (outside the block hull):")
        for g in plan.excluded_graphics:
            lines.append(f"    - {g.layer}")
    if plan.layer_mismatch:
        lines.append("  warning: layer stackup differs (allow_layer_mismatch override in effect):")
        for note in plan.layer_mismatch:
            lines.append(f"    {note}")
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


def format_block_diff(block_name: str, diff: BlockDiff) -> str:
    """Render a :class:`BlockDiff` in a code-review style summary."""
    lines: list[str] = [f"diff: block '{block_name}'"]
    if diff.is_empty:
        lines.append("  (no changes)")
        return "\n".join(lines)

    if diff.moved_footprints:
        lines.append("  moved footprints:")
        for m in diff.moved_footprints:
            frm = f"({m.from_position[0]:.3f}, {m.from_position[1]:.3f}) {m.from_rotation:g}°"
            to = f"({m.to_position[0]:.3f}, {m.to_position[1]:.3f}) {m.to_rotation:g}°"
            lines.append(f"    {m.target_reference:<8} {frm} → {to}")
    if diff.added_footprints:
        lines.append("  added footprints (in source, no target counterpart):")
        for a in diff.added_footprints:
            lines.append(f"    {a.source_reference:<8} {a.symbol_uuid}")
    if diff.removed_footprints:
        lines.append("  removed footprints (in target, no longer in source):")
        for r in diff.removed_footprints:
            lines.append(f"    {r.target_reference:<8} {r.symbol_uuid}")
    if diff.added_tracks:
        lines.append("  added tracks:")
        for t in diff.added_tracks:
            lines.append(
                f"    {t.net_name:<10} {t.layer:<6} "
                f"({t.start[0]:.3f}, {t.start[1]:.3f}) → "
                f"({t.end[0]:.3f}, {t.end[1]:.3f})"
            )
    if diff.removed_tracks:
        lines.append("  removed tracks:")
        for t in diff.removed_tracks:
            lines.append(
                f"    {t.net_name:<10} {t.layer:<6} "
                f"({t.start[0]:.3f}, {t.start[1]:.3f}) → "
                f"({t.end[0]:.3f}, {t.end[1]:.3f})"
            )
    if diff.renamed_nets:
        lines.append("  renamed nets:")
        for n in diff.renamed_nets:
            lines.append(f"    {n.source_net} → {n.target_net}")
    return "\n".join(lines)


# ---- JSON renderers -------------------------------------------------------


def json_envelope(command: str, *, ok: bool, **payload: Any) -> str:  # noqa: ANN401
    """Serialize the standard JSON envelope for a command invocation.

    Every JSON-mode output ends with one of these — success and failure share
    the same shape so a consumer can parse blindly and branch on ``ok``.
    """
    body: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "command": command, "ok": ok}
    body.update(payload)
    return json.dumps(body)


def json_config_errors(errors: list[ConfigError]) -> list[dict[str, Any]]:
    """Render a list of config errors as JSON-safe dicts."""
    return [
        {
            "path": str(err.path),
            "message": err.message,
            "line": err.line,
            "column": err.column,
            "key_path": err.key_path,
        }
        for err in errors
    ]


def json_runtime_errors(messages: list[str]) -> list[dict[str, Any]]:
    """Render a flat list of runtime error messages as JSON-safe dicts."""
    return [{"message": message} for message in messages]


def json_footprint(fp: Footprint) -> dict[str, Any]:
    """Render a footprint as a JSON-safe dict."""
    return {
        "reference": fp.reference,
        "uuid": fp.uuid,
        "symbol_uuid": fp.symbol_uuid,
        "sheet_file": fp.sheet_file,
        "layer": fp.layer,
        "position": [fp.position[0], fp.position[1]],
        "rotation": fp.rotation,
    }


def json_placement(p: PlannedPlacement) -> dict[str, Any]:
    """Render a planned footprint placement."""
    return {
        "symbol_uuid": p.symbol_uuid,
        "source_reference": p.source_reference,
        "target_reference": p.target_reference,
        "source_position": [p.source_position[0], p.source_position[1]],
        "source_rotation": p.source_rotation,
        "target_position": [p.target_position[0], p.target_position[1]],
        "target_rotation": p.target_rotation,
        "layer": p.layer,
    }


def _json_track(t: TrackPlacement) -> dict[str, Any]:
    return {
        "start": [t.start[0], t.start[1]],
        "end": [t.end[0], t.end[1]],
        "width": t.width,
        "layer": t.layer,
        "net_name": t.net_name,
    }


def _json_via(v: ViaPlacement) -> dict[str, Any]:
    return {
        "position": [v.position[0], v.position[1]],
        "size": v.size,
        "drill": v.drill,
        "layers": list(v.layers),
        "net_name": v.net_name,
    }


def _json_zone(z: ZonePlacement) -> dict[str, Any]:
    return {"net_name": z.net_name, "layers": list(z.layers)}


def _json_graphic(g: GraphicPlacement) -> dict[str, Any]:
    return {"layer": g.layer}


def json_apply_plan(block_name: str, plan: ApplyPlan) -> dict[str, Any]:
    """Render an :class:`ApplyPlan` as a JSON-safe dict per-block."""
    return {
        "name": block_name,
        "sheet": plan.sheet,
        "source_anchor": plan.source_anchor_ref,
        "target_anchor": plan.target_anchor_ref,
        "transform_angle_deg": plan.transform_angle_deg,
        "placements": [json_placement(p) for p in plan.placements],
        "tracks": [_json_track(t) for t in plan.tracks],
        "vias": [_json_via(v) for v in plan.vias],
        "zones": [_json_zone(z) for z in plan.zones],
        "graphics": [_json_graphic(g) for g in plan.graphics],
        "excluded_tracks": [
            {
                "start": [t.start[0], t.start[1]],
                "end": [t.end[0], t.end[1]],
                "width": t.width,
                "layer": t.layer,
                "net_name": t.net,
            }
            for t in plan.excluded_tracks
        ],
        "excluded_vias": [
            {
                "position": [v.position[0], v.position[1]],
                "size": v.size,
                "drill": v.drill,
                "layers": list(v.layers),
                "net_name": v.net,
            }
            for v in plan.excluded_vias
        ],
        "excluded_zones": [
            {"net_name": z.net_name, "layers": list(z.layers)} for z in plan.excluded_zones
        ],
        "excluded_graphics": [{"layer": g.layer} for g in plan.excluded_graphics],
        "unmatched_source": list(plan.unmatched_source),
        "unresolved_nets": list(plan.unresolved_nets),
        "layer_mismatch": list(plan.layer_mismatch),
    }


def json_block_diff(block_name: str, diff: BlockDiff) -> dict[str, Any]:
    """Render a :class:`BlockDiff` as a JSON-safe dict per-block."""
    return {
        "name": block_name,
        "diff": {
            "is_empty": diff.is_empty,
            "moved_footprints": [
                {
                    "symbol_uuid": m.symbol_uuid,
                    "target_reference": m.target_reference,
                    "from_position": [m.from_position[0], m.from_position[1]],
                    "to_position": [m.to_position[0], m.to_position[1]],
                    "from_rotation": m.from_rotation,
                    "to_rotation": m.to_rotation,
                }
                for m in diff.moved_footprints
            ],
            "added_footprints": [
                {"symbol_uuid": a.symbol_uuid, "source_reference": a.source_reference}
                for a in diff.added_footprints
            ],
            "removed_footprints": [
                {"symbol_uuid": r.symbol_uuid, "target_reference": r.target_reference}
                for r in diff.removed_footprints
            ],
            "added_tracks": [
                {
                    "start": [t.start[0], t.start[1]],
                    "end": [t.end[0], t.end[1]],
                    "width": t.width,
                    "layer": t.layer,
                    "net_name": t.net_name,
                }
                for t in diff.added_tracks
            ],
            "removed_tracks": [
                {
                    "start": [t.start[0], t.start[1]],
                    "end": [t.end[0], t.end[1]],
                    "width": t.width,
                    "layer": t.layer,
                    "net_name": t.net_name,
                }
                for t in diff.removed_tracks
            ],
            "renamed_nets": [
                {"source_net": n.source_net, "target_net": n.target_net} for n in diff.renamed_nets
            ],
        },
    }
