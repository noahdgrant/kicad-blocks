# kicad-blocks

Share schematic sheets across multiple KiCAD projects and reuse their PCB layouts.

`kicad-blocks` is a CLI that lets you treat hierarchical schematic sheets as the source of truth for a section of a board (MCU subsystem, power, etc.) and replay that section's layout across multiple PCB projects. One MCU sheet, many boards — modular dev panels, all-in-one dev boards, space-constrained production boards — without copy-paste or DNP gymnastics.

> Status: pre-alpha. The v0.1 PRD is tracked in this repo's issues.

## What it does

Given KiCAD projects that share hierarchical schematic sheets (KiCAD 6+ instance-data model), `kicad-blocks` provides four commands:

- **reuse** — copy a sheet's PCB layout (footprint placements, tracks, vias, zones, silkscreen) from a source PCB into a target PCB, anchored at a footprint refdes.
- **sync** — re-apply after the source layout changes, with a dry-run diff and interactive confirmation.
- **scaffold** — generate a new KiCAD project skeleton wired up to a chosen set of shared sheets.
- **panelize-config** — emit a [KiKit](https://github.com/yaqwsx/KiKit) JSON config to panelize a set of modular PCBs.

## Status

- KiCAD 9 only (uses the modern instance-data schematic model)
- Pure-Python S-expression parsing via [kiutils](https://github.com/mvnmgrx/kiutils)
- MIT licensed

## Install

Not yet published. Once v0.1 ships:

```sh
pipx install kicad-blocks
```

## License

MIT — see [LICENSE](./LICENSE).
