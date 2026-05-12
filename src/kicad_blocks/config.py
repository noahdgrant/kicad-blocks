"""TOML loader and typed schema for ``kicad-blocks.toml``.

The loader returns a typed :class:`Config` for a well-formed file, or raises
:class:`InvalidConfigError` carrying a list of :class:`ConfigError` entries that
each point at a file + line (and, where we can, column + dotted key path).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True)
class ConfigError:
    """A single problem encountered while loading a config file."""

    path: Path
    message: str
    line: int | None = None
    column: int | None = None
    key_path: str | None = None


class InvalidConfigError(Exception):
    """Raised when a config cannot be loaded or fails validation.

    The structured ``errors`` attribute is what the reporter consumes; the
    string form is a fallback for stack traces.
    """

    def __init__(self, errors: list[ConfigError]) -> None:
        """Initialize with a non-empty list of structured errors."""
        super().__init__("; ".join(err.message for err in errors))
        self.errors = errors


@dataclass(frozen=True)
class BlockSpec:
    """Declaration of a single reusable block.

    Attributes:
        name: The block's logical name (the table key in ``[blocks.<name>]``).
        sheet: Path to the shared ``.kicad_sch`` file, relative to the
            project directory (i.e. the directory containing the config).
    """

    name: str
    sheet: Path


def _empty_blocks() -> dict[str, BlockSpec]:
    return {}


@dataclass(frozen=True)
class Config:
    """A loaded ``kicad-blocks.toml``."""

    config_path: Path
    project_dir: Path
    project: str
    sources: tuple[Path, ...]
    blocks: dict[str, BlockSpec] = field(default_factory=_empty_blocks)


def load_config(path: Path) -> Config:
    """Load and validate a ``kicad-blocks.toml``.

    Args:
        path: Path to the config file.

    Returns:
        The typed :class:`Config`.

    Raises:
        InvalidConfigError: If the file is missing, unparseable, or fails
            schema validation. The exception's ``errors`` attribute holds the
            structured details.
    """
    if not path.exists():
        raise InvalidConfigError([ConfigError(path=path, message=f"Config file not found: {path}")])

    raw = path.read_text()
    try:
        data: dict[str, Any] = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        line, column = _extract_line_column(exc, raw)
        raise InvalidConfigError(
            [ConfigError(path=path, message=str(exc), line=line, column=column)]
        ) from exc

    return _validate(path, raw, data)


def _validate(path: Path, raw: str, data: dict[str, Any]) -> Config:
    """Type-check the parsed dict and produce a :class:`Config` or raise."""
    errors: list[ConfigError] = []

    project_raw: object = data.get("project")
    project: str = project_raw if isinstance(project_raw, str) else ""
    if not isinstance(project_raw, str):
        errors.append(
            ConfigError(
                path=path,
                message="'project' is required and must be a string",
                line=_find_line(raw, "project"),
                key_path="project",
            )
        )

    sources_raw: object = data.get("sources")
    sources: tuple[Path, ...] = ()
    if not isinstance(sources_raw, list):
        errors.append(
            ConfigError(
                path=path,
                message="'sources' is required and must be a list of PCB paths",
                line=_find_line(raw, "sources"),
                key_path="sources",
            )
        )
    else:
        sources_list = cast(list[object], sources_raw)
        bad_indices = [i for i, item in enumerate(sources_list) if not isinstance(item, str)]
        if bad_indices:
            errors.append(
                ConfigError(
                    path=path,
                    message=f"'sources' entries must be strings (bad indices: {bad_indices})",
                    line=_find_line(raw, "sources"),
                    key_path="sources",
                )
            )
        else:
            sources = tuple(Path(s) for s in sources_list if isinstance(s, str))

    blocks: dict[str, BlockSpec] = {}
    blocks_raw: object = data.get("blocks", {})
    if blocks_raw and not isinstance(blocks_raw, dict):
        errors.append(
            ConfigError(
                path=path,
                message="'blocks' must be a table",
                line=_find_line(raw, "blocks"),
                key_path="blocks",
            )
        )
    elif isinstance(blocks_raw, dict):
        blocks_dict = cast(dict[str, object], blocks_raw)
        for name, block_data in blocks_dict.items():
            key_path = f"blocks.{name}"
            if not isinstance(block_data, dict):
                errors.append(
                    ConfigError(
                        path=path,
                        message=f"'{key_path}' must be a table",
                        line=_find_section_line(raw, key_path),
                        key_path=key_path,
                    )
                )
                continue
            block_dict = cast(dict[str, object], block_data)
            sheet: object = block_dict.get("sheet")
            if not isinstance(sheet, str):
                errors.append(
                    ConfigError(
                        path=path,
                        message=f"'{key_path}.sheet' is required and must be a string",
                        line=_find_section_line(raw, key_path),
                        key_path=key_path,
                    )
                )
                continue
            blocks[name] = BlockSpec(name=name, sheet=Path(sheet))

    if errors:
        raise InvalidConfigError(errors)

    return Config(
        config_path=path,
        project_dir=path.parent,
        project=project,
        sources=sources,
        blocks=blocks,
    )


def _extract_line_column(exc: tomllib.TOMLDecodeError, raw: str) -> tuple[int | None, int | None]:
    """Best-effort line/column extraction from a TOMLDecodeError.

    Python 3.13 exposes ``lineno``/``colno`` attributes; on 3.11/3.12 we parse
    the message, and fall back to the last line for "end of document" errors so
    the reporter still has something to anchor on.
    """
    line: int | None = getattr(exc, "lineno", None)
    column: int | None = getattr(exc, "colno", None)
    msg = str(exc)
    if line is None:
        match = re.search(r"line (\d+)", msg)
        if match:
            line = int(match.group(1))
    if column is None:
        match = re.search(r"column (\d+)", msg)
        if match:
            column = int(match.group(1))
    if line is None and "end of document" in msg:
        line = len(raw.splitlines()) or 1
    return line, column


def _find_line(raw: str, key: str) -> int | None:
    """Find the first line in ``raw`` that defines a top-level key."""
    for i, line in enumerate(raw.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith((f"{key} =", f"{key}=")):
            return i
    return None


def _find_section_line(raw: str, key_path: str) -> int | None:
    """Find the line of a ``[section.subsection]`` header."""
    target = f"[{key_path}]"
    for i, line in enumerate(raw.splitlines(), 1):
        if line.strip() == target:
            return i
    return None
