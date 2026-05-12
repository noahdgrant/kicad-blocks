"""Net name mapping between a source block and a target PCB.

Auto-matches source nets to target nets by identical name; hierarchical paths
that differ only in a leading slash (``/PWR/+3V3`` vs ``PWR/+3V3``) are
normalized so they match. Users declare overrides per block in
``[blocks.<name>.net_map]`` for names that diverge between projects. Any
source net that fails both auto-match and override resolution is returned to
the caller as ``unresolved``; the caller surfaces these in ``--dry-run`` and
refuses the actual apply.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class NetMap:
    """Resolved source-net → target-net mapping for one block.

    Attributes:
        mapping: Source net name → target net name. Identity for auto-matched
            entries, explicit values for overrides. Names that don't appear as
            keys are either out-of-block (the caller decides) or unresolved
            (returned separately by :func:`build`).
    """

    mapping: Mapping[str, str]

    def lookup(self, source_net: str) -> str:
        """Return the target net name corresponding to ``source_net``.

        Falls back to the input verbatim when ``source_net`` has no entry — a
        no-op for nets outside the block. Callers ensure they only look up
        in-block nets (the unresolved list has already validated those).

        Args:
            source_net: The net name as it appears in the source PCB.

        Returns:
            The mapped target net name, or ``source_net`` unchanged when no
            mapping exists.
        """
        return self.mapping.get(source_net, source_net)


def build(
    *,
    source_nets: Iterable[str],
    target_nets: Iterable[str],
    overrides: Mapping[str, str] | None = None,
) -> tuple[NetMap, list[str]]:
    """Build a :class:`NetMap` from source/target net lists plus user overrides.

    Resolution order, per source net:

    1. If the source name has an override entry, the override's target name is
       looked up in ``target_nets`` (with slash normalization). If that target
       exists, the source is mapped to the target's original spelling. If it
       does not, the source is unresolved — overrides do not silently invent
       nets that the target board lacks.
    2. Otherwise, attempt an auto-match by normalized name (leading slashes
       stripped).
    3. Otherwise, the source name is unresolved.

    Args:
        source_nets: Net names referenced by the source block's footprints.
            Duplicates are tolerated; each unique name appears once in the
            output.
        target_nets: Net names present in the target PCB. Used to verify both
            auto-matches and override destinations.
        overrides: Source-name → target-name pairs from the config's
            ``[blocks.<name>.net_map]`` table.

    Returns:
        Tuple ``(NetMap, unresolved_sorted)`` where ``unresolved_sorted`` is a
        sorted list of source-net names with no resolvable target. The list is
        sorted for deterministic dry-run output and reviewable diffs.
    """
    overrides_map: Mapping[str, str] = overrides or {}

    target_by_normalized: dict[str, str] = {}
    for net in target_nets:
        target_by_normalized.setdefault(_normalize(net), net)

    overrides_by_normalized: dict[str, str] = {
        _normalize(src): tgt for src, tgt in overrides_map.items()
    }

    mapping: dict[str, str] = {}
    unresolved: set[str] = set()
    seen: set[str] = set()
    for source in source_nets:
        if source in seen:
            continue
        seen.add(source)

        normalized_source = _normalize(source)

        override_target = overrides_by_normalized.get(normalized_source)
        if override_target is not None:
            resolved = target_by_normalized.get(_normalize(override_target))
            if resolved is None:
                unresolved.add(source)
                continue
            mapping[source] = resolved
            continue

        auto_match = target_by_normalized.get(normalized_source)
        if auto_match is not None:
            mapping[source] = auto_match
            continue

        unresolved.add(source)

    return NetMap(mapping=mapping), sorted(unresolved)


def _normalize(name: str) -> str:
    """Strip leading slashes for hierarchical net path comparison.

    KiCAD writes hierarchical-net names with a leading slash when the net
    originates inside a sub-sheet (``/PWR/+3V3``) and without when it's at the
    root (``+3V3``). Normalizing both forms means a config written with one
    notation matches a PCB written with the other.
    """
    return name.lstrip("/")
