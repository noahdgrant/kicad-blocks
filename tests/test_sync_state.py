"""Tests for the sync-state lock file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kicad_blocks.sync_state import (
    BlockState,
    LockFile,
    LockFileError,
    read_lock,
    write_lock,
)


def _example_lock() -> LockFile:
    return LockFile(
        plugin_version="0.0.1",
        blocks={
            "mcu": BlockState(
                source="../source/source.kicad_pcb",
                source_pcb_hash="sha256:abc",
                applied_block_hash="sha256:def",
                anchor_refdes="ANCHOR1",
                sheet="sheets/mcu.kicad_sch",
            ),
        },
    )


def test_write_lock_creates_json_with_expected_shape(tmp_path: Path) -> None:
    """write_lock serializes the LockFile to disk in a stable shape."""
    lock = _example_lock()
    path = tmp_path / "proj.kicad-blocks.lock.json"

    write_lock(path, lock)

    data = json.loads(path.read_text())
    assert data["schema_version"] == 1
    assert data["plugin_version"] == "0.0.1"
    mcu = data["blocks"]["mcu"]
    assert mcu["source"] == "../source/source.kicad_pcb"
    assert mcu["source_pcb_hash"] == "sha256:abc"
    assert mcu["applied_block_hash"] == "sha256:def"
    assert mcu["anchor_refdes"] == "ANCHOR1"
    assert mcu["sheet"] == "sheets/mcu.kicad_sch"


def test_write_lock_round_trips_through_read_lock(tmp_path: Path) -> None:
    """A LockFile written by write_lock loads back equal."""
    lock = _example_lock()
    path = tmp_path / "lock.json"

    write_lock(path, lock)
    loaded = read_lock(path)

    assert loaded == lock


def test_read_lock_missing_file_raises_lockfileerror(tmp_path: Path) -> None:
    """A missing lock file surfaces a LockFileError naming the path."""
    missing = tmp_path / "nope.lock.json"
    with pytest.raises(LockFileError, match="not found"):
        read_lock(missing)


def test_read_lock_rejects_future_schema_version(tmp_path: Path) -> None:
    """A lock file from a newer schema is refused with a clear message."""
    path = tmp_path / "future.lock.json"
    path.write_text(
        json.dumps({"schema_version": 999, "plugin_version": "0.0.1", "blocks": {}})
    )
    with pytest.raises(LockFileError, match="schema version"):
        read_lock(path)


def test_read_lock_rejects_malformed_json(tmp_path: Path) -> None:
    """An unparseable lock file produces a LockFileError, not a JSONDecodeError."""
    path = tmp_path / "broken.lock.json"
    path.write_text("{not json")
    with pytest.raises(LockFileError, match="parse"):
        read_lock(path)
