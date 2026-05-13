# kicad-blocks

Share schematic sheets across multiple KiCAD projects and reuse their PCB layouts.

`kicad-blocks` is a CLI that lets you treat hierarchical schematic sheets as the source of truth for a section of a board (MCU subsystem, power, etc.) and replay that section's layout across multiple PCB projects. One MCU sheet, many boards — modular dev panels, all-in-one dev boards, space-constrained production boards — without copy-paste or DNP gymnastics.

> Status: pre-alpha. The v0.1 PRD lives at [issue #1](https://github.com/noahdgrant/kicad-blocks/issues/1) and the implementation slices at issues [#2–#13](https://github.com/noahdgrant/kicad-blocks/milestone/1).

## What it does

Given KiCAD projects that share hierarchical schematic sheets (KiCAD 6+ instance-data model), `kicad-blocks` provides four commands:

- **reuse** — copy a sheet's PCB layout (footprint placements, tracks, vias, zones, silkscreen) from a source PCB into a target PCB, anchored at a footprint refdes. Pass `--dry-run` to preview the planned placements without writing.
- **sync** — re-apply after the source layout changes, with a dry-run diff and interactive confirmation.
- **scaffold** — generate a new KiCAD project skeleton wired up to a chosen set of shared sheets.
- **panelize-config** — emit a [KiKit](https://github.com/yaqwsx/KiKit) JSON config to panelize a set of modular PCBs.

After a successful `reuse`, the plugin writes a `<project>.kicad-blocks.lock.json` sidecar next to the config recording the source PCB hash, the applied block hash, the anchor refdes, and the plugin version. **Commit this file to git** — `sync` consults it to detect drift, and shipping it makes diffs reviewable across machines.

Every subcommand also accepts `--format json` for machine-readable output. The JSON envelope is versioned (`schema_version: 1`) and shared by success and failure cases, so a consumer can parse a single stream and branch on the top-level `ok` field. Errors travel as structured entries under `errors[]` on stdout, and exit codes match the default text mode.

## Status

- KiCAD 9 only (uses the modern instance-data schematic model)
- Pure-Python S-expression parsing via [kiutils](https://github.com/mvnmgrx/kiutils)
- MIT licensed

## Install

```sh
pipx install kicad-blocks
```

## Quick start: reuse and sync

Assume you have two KiCAD projects sharing a hierarchical sheet `sheets/mcu.kicad_sch`: a fully-routed `mcu-module/` project that owns the canonical layout, and a `dev-board/` project that wants the same MCU section dropped in.

1. **Scaffold the target project** (skip if you already have one):

   ```sh
   kicad-blocks scaffold --name dev-board --sheets ../sheets/mcu.kicad_sch
   ```

2. **Place the anchor footprint** in `dev-board/dev-board.kicad_pcb` — typically the MCU itself (e.g. `U1`) — at the position and rotation where you want the block to land. Save the board.

3. **Write `dev-board/kicad-blocks.toml`**:

   ```toml
   [[blocks]]
   source   = "../mcu-module/mcu-module.kicad_pcb"
   sheet    = "sheets/mcu.kicad_sch"
   anchor   = "U1"
   ```

4. **Validate the config** before touching the board:

   ```sh
   kicad-blocks validate
   ```

5. **Preview** the planned placement, then apply:

   ```sh
   kicad-blocks reuse --dry-run    # prints the items that would be placed
   kicad-blocks reuse               # writes dev-board.kicad_pcb atomically
   ```

   A `dev-board.kicad-blocks.lock.json` sidecar appears next to your config — commit it.

6. **Later, when the source layout changes**, re-sync:

   ```sh
   kicad-blocks sync --dry-run     # diff: added / removed / moved / net-rewired
   kicad-blocks sync                # prompts before overwriting
   ```

   If `dev-board.kicad_pcb` was hand-edited *inside* the block region since the last apply, `sync` refuses and tells you to pass `--force` once you've reconciled the changes.

For a panel of modular PCBs, declare the `[panelize]` section of `kicad-blocks.toml` and emit a KiKit config with `kicad-blocks panelize-config --out panel.json`, then run KiKit yourself.

## Reporting issues

Bug reports and feature requests welcome at the [issue tracker](https://github.com/noahdgrant/kicad-blocks/issues). Please include the KiCAD version, the `kicad-blocks` version, and a minimal reproducer where possible.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for development setup, the conventional commit format, and how to run the test/lint/typecheck stack locally.

## License

MIT — see [LICENSE](./LICENSE).
