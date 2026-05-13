"""Read and write the ``<project>.kicad-blocks.lock.json`` sidecar.

The lock file records, per declared block, what was applied to the target PCB
on the most recent ``reuse`` or ``sync``. ``applied_block_hash`` is the canonical
fingerprint slice 8's apply path compares against the target's *current* block
region to detect hand-edits: a mismatch means someone has touched the block in
the target since the last apply, so we refuse to overwrite without ``--force``.

The file is committed to git so diff history is reviewable and reproducible
across machines. The schema is versioned (``schema_version: 1``) so future
shape changes do not silently break older or newer plugins.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from kicad_blocks.block import ApplyPlan, footprints_in_sheet
from kicad_blocks.kicad_io import Pcb

SCHEMA_VERSION = 1


class LockFileError(Exception):
    """Raised when a lock file cannot be read or has an incompatible schema."""


@dataclass(frozen=True)
class BlockState:
    """Per-block apply record.

    Attributes:
        source: The block's source-PCB path as it appears in the config (kept
            verbatim so review diffs are stable across machines with different
            absolute working directories).
        source_pcb_hash: SHA-256 of the source PCB file's content at the time
            of the apply, prefixed with ``sha256:``.
        applied_block_hash: SHA-256 of the canonical, target-frame block
            representation that was written to the target. Slice 8 will compare
            this against the target's current block region to detect hand-edits.
        anchor_refdes: Refdes of the anchor footprint in the target PCB.
        sheet: Path to the hierarchical sheet that scopes the block.
    """

    source: str
    source_pcb_hash: str
    applied_block_hash: str
    anchor_refdes: str
    sheet: str


@dataclass(frozen=True)
class LockFile:
    """The full ``<project>.kicad-blocks.lock.json`` payload.

    Attributes:
        plugin_version: Version of ``kicad-blocks`` that wrote the file.
        blocks: Per-block apply records, keyed by the block's logical name.
    """

    plugin_version: str
    blocks: dict[str, BlockState]


def lock_path_for(project_dir: Path, project_name: str) -> Path:
    """Return the conventional lock-file location for ``project_name``.

    The PRD names it ``<project>.kicad-blocks.lock.json``, placed next to the
    config file in the project directory.
    """
    return project_dir / f"{project_name}.kicad-blocks.lock.json"


def write_lock(path: Path, lock: LockFile) -> None:
    """Write ``lock`` to ``path`` in stable JSON.

    Keys are sorted so the diff is minimal across runs; trailing newline so
    ``git diff`` doesn't complain.
    """
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "plugin_version": lock.plugin_version,
        "blocks": {
            name: {
                "source": state.source,
                "source_pcb_hash": state.source_pcb_hash,
                "applied_block_hash": state.applied_block_hash,
                "anchor_refdes": state.anchor_refdes,
                "sheet": state.sheet,
            }
            for name, state in sorted(lock.blocks.items())
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_lock(path: Path) -> LockFile:
    """Load a lock file from ``path`` into a :class:`LockFile`.

    Args:
        path: Filesystem path to the lock file.

    Returns:
        The parsed :class:`LockFile`.

    Raises:
        LockFileError: If the file is missing, unparseable, has an unknown
            schema version, or has the wrong shape.
    """
    if not path.exists():
        msg = f"lock file not found: {path}"
        raise LockFileError(msg)
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        msg = f"failed to parse lock file {path}: {exc}"
        raise LockFileError(msg) from exc

    if not isinstance(data, dict):
        msg = f"lock file {path} is not a JSON object"
        raise LockFileError(msg)
    typed_data = cast("dict[str, object]", data)

    schema_version = typed_data.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        msg = (
            f"lock file {path} has unsupported schema version {schema_version!r}; "
            f"this build expects schema version {SCHEMA_VERSION}"
        )
        raise LockFileError(msg)

    plugin_version = typed_data.get("plugin_version")
    if not isinstance(plugin_version, str):
        msg = f"lock file {path} is missing a 'plugin_version' string"
        raise LockFileError(msg)

    blocks_raw = typed_data.get("blocks", {})
    if not isinstance(blocks_raw, dict):
        msg = f"lock file {path} has a non-object 'blocks' field"
        raise LockFileError(msg)
    blocks_dict = cast("dict[str, object]", blocks_raw)

    blocks: dict[str, BlockState] = {}
    for name, state_raw in blocks_dict.items():
        if not isinstance(state_raw, dict):
            msg = f"lock file {path}: block '{name}' must be an object"
            raise LockFileError(msg)
        state_dict = cast("dict[str, object]", state_raw)
        blocks[name] = BlockState(
            source=_require_str(state_dict, "source", path, name),
            source_pcb_hash=_require_str(state_dict, "source_pcb_hash", path, name),
            applied_block_hash=_require_str(state_dict, "applied_block_hash", path, name),
            anchor_refdes=_require_str(state_dict, "anchor_refdes", path, name),
            sheet=_require_str(state_dict, "sheet", path, name),
        )

    return LockFile(plugin_version=plugin_version, blocks=blocks)


def _require_str(data: dict[str, object], key: str, path: Path, block_name: str) -> str:
    """Pull a required string field out of ``data`` or raise a clear error."""
    value = data.get(key)
    if not isinstance(value, str):
        msg = f"lock file {path}: block '{block_name}' is missing string field '{key}'"
        raise LockFileError(msg)
    return value


def hash_file(path: Path) -> str:
    """Return ``sha256:<hex>`` of the bytes at ``path``."""
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


_PAD_TOLERANCE_MM = 1e-3


def hash_target_block_state(
    target_pcb: Pcb,
    sheet: str,
    anchor_ref: str,
    transform_angle_deg: float,
) -> str:
    """Return the canonical hash of the target's *current* block region.

    Mirrors :func:`hash_applied_block` shape so a value produced from the target
    PCB's current state can be compared byte-for-byte against the lock's
    ``applied_block_hash`` field. Mismatch → the user has hand-edited the block
    region since the last apply.

    ``transform_angle_deg`` is part of the canonical payload (it's recorded in
    the per-apply hash even though the placements are already in the target
    frame); pass the value of the freshly-computed plan's
    ``transform_angle_deg`` so an unmoved anchor reproduces the same hash.
    """
    block_fps = footprints_in_sheet(target_pcb, sheet)
    anchor = next((fp for fp in block_fps if fp.reference == anchor_ref), None)
    anchor_sym = anchor.symbol_uuid if anchor else None
    fp_tuples: list[tuple[str, float, float, float, str]] = sorted(
        (
            fp.symbol_uuid,
            round(fp.position[0], 6),
            round(fp.position[1], 6),
            round(fp.rotation, 6),
            fp.layer,
        )
        for fp in block_fps
        if fp.symbol_uuid is not None and fp.symbol_uuid != anchor_sym
    )
    pad_positions = _absolute_pad_positions(list(block_fps))
    tracks: list[tuple[str, str, float, float, float, float, float]] = []
    for track in target_pcb.tracks:
        if _near_any(track.start, pad_positions) and _near_any(track.end, pad_positions):
            tracks.append(
                (
                    track.layer,
                    track.net,
                    round(track.start[0], 6),
                    round(track.start[1], 6),
                    round(track.end[0], 6),
                    round(track.end[1], 6),
                    round(track.width, 6),
                )
            )
    tracks.sort()
    vias: list[tuple[str, float, float, float, float, tuple[str, ...]]] = []
    for via in target_pcb.vias:
        if _near_any(via.position, pad_positions):
            vias.append(
                (
                    via.net,
                    round(via.position[0], 6),
                    round(via.position[1], 6),
                    round(via.size, 6),
                    round(via.drill, 6),
                    tuple(via.layers),
                )
            )
    vias.sort()
    payload: dict[str, Any] = {
        "anchor": anchor_ref,
        "sheet": sheet,
        "transform_angle_deg": round(transform_angle_deg, 6),
        "footprints": fp_tuples,
        "tracks": tracks,
        "vias": vias,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _absolute_pad_positions(
    footprints: list[Any],
) -> list[tuple[float, float]]:
    """Return every pad's absolute world position across ``footprints``."""
    positions: list[tuple[float, float]] = []
    for fp in footprints:
        cos_r, sin_r = _cos_sin(fp.rotation)
        fx, fy = fp.position
        for pad in fp.pads:
            px, py = pad.position
            positions.append((fx + cos_r * px - sin_r * py, fy + sin_r * px + cos_r * py))
    return positions


def _cos_sin(angle_deg: float) -> tuple[float, float]:
    """Return ``(cos, sin)`` for ``angle_deg`` with axis-aligned angles exact."""
    a = angle_deg % 360.0
    if a == 0.0:
        return (1.0, 0.0)
    if a == 90.0:
        return (0.0, 1.0)
    if a == 180.0:
        return (-1.0, 0.0)
    if a == 270.0:
        return (0.0, -1.0)
    theta = math.radians(a)
    return (math.cos(theta), math.sin(theta))


def _near_any(point: tuple[float, float], candidates: list[tuple[float, float]]) -> bool:
    """Return ``True`` if ``point`` is within :data:`_PAD_TOLERANCE_MM` of any candidate."""
    px, py = point
    for cx, cy in candidates:
        if abs(px - cx) <= _PAD_TOLERANCE_MM and abs(py - cy) <= _PAD_TOLERANCE_MM:
            return True
    return False


def hash_applied_block(plan: ApplyPlan) -> str:
    """Return ``sha256:<hex>`` of the canonical, target-frame block representation.

    Hashes a stable JSON encoding of every footprint placement, track, and via
    that the plan would write. Coordinates are rounded to micrometre precision
    so float noise from successive transforms does not change the hash.

    The hash is what slice 8 compares against the target's current block region
    to detect hand-edits.
    """
    footprints = sorted(
        (
            p.symbol_uuid,
            round(p.target_position[0], 6),
            round(p.target_position[1], 6),
            round(p.target_rotation, 6),
            p.layer,
        )
        for p in plan.placements
    )
    tracks = sorted(
        (
            t.layer,
            t.net_name,
            round(t.start[0], 6),
            round(t.start[1], 6),
            round(t.end[0], 6),
            round(t.end[1], 6),
            round(t.width, 6),
        )
        for t in plan.tracks
    )
    vias = sorted(
        (
            v.net_name,
            round(v.position[0], 6),
            round(v.position[1], 6),
            round(v.size, 6),
            round(v.drill, 6),
            tuple(v.layers),
        )
        for v in plan.vias
    )
    payload: dict[str, Any] = {
        "anchor": plan.target_anchor_ref,
        "sheet": plan.sheet,
        "transform_angle_deg": round(plan.transform_angle_deg, 6),
        "footprints": footprints,
        "tracks": tracks,
        "vias": vias,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
