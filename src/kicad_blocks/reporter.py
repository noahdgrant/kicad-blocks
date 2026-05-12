"""Text-mode output for the validate and list-block commands.

The reporter is the seam through which CLI commands turn domain values into
human-readable lines. ``--format json`` lives in a later slice (issue #12); for
now the JSON shaping happens inside each formatter via a stable internal
structure so it's easy to plug a renderer in later.
"""

from __future__ import annotations

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
