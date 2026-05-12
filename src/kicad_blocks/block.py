"""Domain-core operations on a hierarchical-sheet block.

Two halves live here:

- The read side (``footprints_in_sheet``) — given a parsed PCB and a sheet
  path, return the footprints whose ``Sheetfile`` matches.
- The write-path planner (``plan_apply``) — given a source PCB, a target PCB,
  a sheet, and the target's anchor refdes, return a structured
  :class:`ApplyPlan` that says exactly which target footprints get moved where.

Slice 3 is footprint-only; tracks, vias, zones, and silkscreen all arrive in
later slices. The net check here is a stub per the issue: if any net referenced
by an in-block source footprint is missing from the target's net list, we fail
fast. Slice 4 replaces this with a real ``net_map``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kicad_blocks.kicad_io import Footprint, FootprintPlacement, Pcb
from kicad_blocks.transform import Transform


class ApplyError(Exception):
    """Raised when an apply cannot be planned (missing anchor, net mismatch, …)."""


@dataclass(frozen=True)
class PlannedPlacement:
    """A single planned mutation in an :class:`ApplyPlan`.

    Carries both source and target coordinates so dry-run output can show what
    moves to where; the ``placement`` field is what :func:`kicad_io.apply_placements`
    actually consumes.
    """

    symbol_uuid: str
    source_reference: str
    target_reference: str
    source_position: tuple[float, float]
    source_rotation: float
    target_position: tuple[float, float]
    target_rotation: float
    layer: str

    @property
    def placement(self) -> FootprintPlacement:
        """Return the kicad_io-level mutation this plan entry produces."""
        return FootprintPlacement(
            symbol_uuid=self.symbol_uuid,
            position=self.target_position,
            rotation=self.target_rotation,
            layer=self.layer,
        )


@dataclass(frozen=True)
class ApplyPlan:
    """The full plan produced by :func:`plan_apply`.

    Attributes:
        sheet: The sheet path used to scope the block.
        source_anchor_ref: Refdes of the source-side anchor (looked up by
            matching the target anchor's symbol UUID — may differ from
            ``target_anchor_ref``).
        target_anchor_ref: Refdes of the target-side anchor.
        transform_angle_deg: The net rotation the transform applies.
        placements: Footprint placements to write to the target, in source
            order. The anchor itself is excluded — it's already in place.
        unmatched_source: Symbol UUIDs of source-block footprints that had no
            counterpart in the target. Surfaced so dry-run can report them;
            the apply itself does not write anything for these.
    """

    sheet: str
    source_anchor_ref: str
    target_anchor_ref: str
    transform_angle_deg: float
    placements: tuple[PlannedPlacement, ...]
    unmatched_source: tuple[str, ...]


def footprints_in_sheet(pcb: Pcb, sheet: str | Path) -> list[Footprint]:
    """Return the footprints in ``pcb`` whose ``Sheetfile`` matches ``sheet``.

    Comparison uses POSIX-normalized paths so a config authored with backslashes
    matches a PCB that stores forward slashes (KiCAD always writes
    forward-slash paths, but configs are user-edited).

    Args:
        pcb: The loaded PCB.
        sheet: The hierarchical sheet file path, relative to the project root,
            as it appears in the footprint's ``Sheetfile`` property.

    Returns:
        Footprints that belong to ``sheet``, in source order.
    """
    needle = _normalize(str(sheet))
    return [fp for fp in pcb.footprints if fp.sheet_file and _normalize(fp.sheet_file) == needle]


def plan_apply(
    *,
    source_pcb: Pcb,
    target_pcb: Pcb,
    sheet: str | Path,
    anchor_ref: str,
) -> ApplyPlan:
    """Plan the footprint half of ``reuse`` for a single block.

    Steps:

    1. Find the target anchor footprint by refdes.
    2. Use its symbol UUID to find the source-side anchor.
    3. Build the affine transform that carries the source frame to the target
       anchor's frame.
    4. For each in-block source footprint (excluding the anchor), find the
       target footprint by symbol UUID and compute its new position/rotation.
    5. Verify every net referenced by an in-block source footprint exists in
       the target's net list.

    Args:
        source_pcb: The source PCB whose layout is canonical for the block.
        target_pcb: The target PCB; ``anchor_ref`` is its anchor refdes.
        sheet: The shared sheet path used to scope the block.
        anchor_ref: The anchor footprint's refdes in the target PCB.

    Returns:
        A structured :class:`ApplyPlan`.

    Raises:
        ApplyError: If the anchor can't be found, the source has no matching
            anchor symbol, or any in-block source net is missing from the
            target.
    """
    target_anchor = _find_by_reference(target_pcb, anchor_ref)
    if target_anchor is None:
        msg = f"anchor footprint '{anchor_ref}' not found in target PCB {target_pcb.path}"
        raise ApplyError(msg)
    if target_anchor.symbol_uuid is None:
        msg = (
            f"anchor footprint '{anchor_ref}' in target PCB {target_pcb.path} "
            f"has no symbol UUID — cannot cross-match with the source"
        )
        raise ApplyError(msg)

    source_block = footprints_in_sheet(source_pcb, sheet)
    if not source_block:
        msg = (
            f"no footprints found on sheet '{sheet}' in source PCB {source_pcb.path}; "
            f"check the sheet path matches the source's Sheetfile property"
        )
        raise ApplyError(msg)

    source_anchor = next(
        (fp for fp in source_block if fp.symbol_uuid == target_anchor.symbol_uuid),
        None,
    )
    if source_anchor is None:
        msg = (
            f"target anchor '{anchor_ref}' (symbol {target_anchor.symbol_uuid}) "
            f"has no counterpart on sheet '{sheet}' in source PCB {source_pcb.path}"
        )
        raise ApplyError(msg)

    _check_nets(source_block, target_pcb, sheet)

    transform = Transform.from_anchors(
        source=source_anchor.position,
        source_angle=source_anchor.rotation,
        target=target_anchor.position,
        target_angle=target_anchor.rotation,
    )

    target_by_symbol: dict[str, Footprint] = {
        fp.symbol_uuid: fp for fp in target_pcb.footprints if fp.symbol_uuid is not None
    }

    placements: list[PlannedPlacement] = []
    unmatched: list[str] = []
    for fp in source_block:
        if fp.symbol_uuid is None or fp.symbol_uuid == source_anchor.symbol_uuid:
            continue
        target_fp = target_by_symbol.get(fp.symbol_uuid)
        if target_fp is None:
            unmatched.append(fp.symbol_uuid)
            continue
        placements.append(
            PlannedPlacement(
                symbol_uuid=fp.symbol_uuid,
                source_reference=fp.reference,
                target_reference=target_fp.reference,
                source_position=fp.position,
                source_rotation=fp.rotation,
                target_position=transform.apply(fp.position),
                target_rotation=transform.apply_angle(fp.rotation),
                layer=fp.layer,
            )
        )

    return ApplyPlan(
        sheet=str(sheet),
        source_anchor_ref=source_anchor.reference,
        target_anchor_ref=target_anchor.reference,
        transform_angle_deg=transform.angle_deg,
        placements=tuple(placements),
        unmatched_source=tuple(unmatched),
    )


def _check_nets(source_block: list[Footprint], target_pcb: Pcb, sheet: str | Path) -> None:
    """Raise :class:`ApplyError` if any in-block source net is missing from target.

    This is the Slice 3 stub — Slice 4 replaces it with ``net_map`` that allows
    explicit overrides for mismatched names. For now: exact match by name.
    """
    target_nets = set(target_pcb.nets)
    missing: list[str] = []
    seen: set[str] = set()
    for fp in source_block:
        for net in fp.pad_nets:
            if net in seen:
                continue
            seen.add(net)
            if net not in target_nets:
                missing.append(net)
    if missing:
        msg = (
            f"net mismatch on sheet '{sheet}': source references net(s) "
            f"{sorted(missing)} that are not present in target PCB "
            f"{target_pcb.path}"
        )
        raise ApplyError(msg)


def _find_by_reference(pcb: Pcb, reference: str) -> Footprint | None:
    """Return the first footprint with ``reference``, or ``None``."""
    return next((fp for fp in pcb.footprints if fp.reference == reference), None)


def _normalize(path: str) -> str:
    """Lower-friction path comparison: forward slashes, no leading ``./``."""
    return path.replace("\\", "/").removeprefix("./")
