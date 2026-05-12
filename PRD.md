# PRD: kicad-blocks v0.1 — Shared-Sheet Layout Reuse for KiCAD

## Problem Statement

I maintain an embedded product and need to ship multiple variants of the PCB from a common set of subsystem designs — an MCU section, a power section, an LED section, and so on. Concretely, I expect to produce at least six boards from these subsystems:

1. A standalone MCU module PCB (small, with inter-module connectors)
2. A standalone power module PCB
3. A standalone LED module PCB
4. A modular dev assembly: boards 1–3 panelized together
5. An all-in-one dev board: all three subsystems on a single PCB without inter-module connectors
6. A space-constrained production board: MCU + power subsystems only, optimized layout, no dev-only circuitry

KiCAD's current model only goes part of the way. Hierarchical schematic sheets stored as standalone files can already be referenced from multiple projects (instance data lives in the parent project, so the same `mcu.kicad_sch` can be U1 in one project and U7 in another with no file conflict). That solves the schematic side. **The layout side has no equivalent.** If I want the MCU section to use the same fanout, decoupling, crystal placement, and signal routing across boards 1, 5, and 6, today I either re-route by hand three times or copy-paste files and accept the drift that follows.

DNP (Do Not Populate) doesn't solve this because:
- Production board is physically space-constrained — I can't carry dev-only footprints.
- Dev and production need genuinely different layouts (board outlines, layer counts, component placement).
- The modular dev case (#4) is a panel of separate PCBs, not a single layout.

Altium Designer solves this with "device sheets" + "snippets" + multi-board projects, but the licensing cost is significant for a small team. There is no equivalent in the KiCAD ecosystem; the closest prior art is the `replicate_layout` plugin, which only operates within a single project.

## Solution

`kicad-blocks` is an open-source Python CLI that extends KiCAD's shared-sheet pattern from the schematic into the PCB. It treats a hierarchical sheet as the source of truth for a section of layout: footprints belonging to that sheet, the tracks/vias/zones/silkscreen that connect them, and their geometric arrangement relative to an anchor footprint. The user lays the section out once in a source PCB, then **reuses** it in any number of target PCBs that reference the same sheet.

From the user's perspective, the workflow looks like this:

1. Author shared hierarchical sheets once (`sheets/mcu.kicad_sch`, etc.) — this is plain KiCAD, no plugin involvement.
2. For each board, create a KiCAD project whose root schematic references the sheets it needs. Hand-author this, or use `kicad-blocks scaffold` to generate the project skeleton.
3. Lay out one board (the "source") fully — call it the canonical placement for those sheets.
4. In a target board, place a single anchor footprint from the sheet where you want the section to land. Add a `kicad-blocks.toml` config declaring `source = "../mcu-module/mcu-module.kicad_pcb"`, the sheet, and the anchor refdes.
5. Run `kicad-blocks reuse`. The plugin extracts the sheet's block from the source, transforms it to the anchor's frame, maps nets, and places footprints + tracks/vias/zones into the target.
6. When the source layout changes, run `kicad-blocks sync` — get a dry-run diff first, confirm, and re-apply.

For board #4 (modular panel), `kicad-blocks panelize-config` emits a KiKit JSON config from the per-project section of the same TOML, and the user runs KiKit themselves.

The plugin is **CLI-first** (a GUI action plugin can come later, sharing the same core). It uses pure-Python S-expression parsing via `kiutils` — no running KiCAD instance is required, which makes the plugin trivial to script, test, and run in CI.

## User Stories

### Core: reuse

1. As a hardware engineer, I want to extract the layout of one hierarchical sheet from a source PCB into a normalized, anchor-relative form, so that I can apply it to other PCBs.
2. As a hardware engineer, I want to apply that extracted layout into a target PCB at a chosen anchor footprint, so that the target board has the same footprint placements, rotations, layers, and connecting tracks as the source.
3. As a hardware engineer, I want footprint matching to be driven by stable symbol UUIDs from the shared schematic sheet, so that mismatched reference designators between projects don't break the reuse.
4. As a hardware engineer, I want tracks, vias, copper zones, and footprint-attached silkscreen/fab/courtyard graphics to all travel with the block, so that the reused section is functionally complete.
5. As a hardware engineer, I want a strict-boundary policy where only items fully inside the sheet's footprint group are managed, so that I don't accidentally clobber routing that crosses out of the block in the target board.
6. As a hardware engineer, I want the apply step to position the block by treating one footprint as the anchor — its position and rotation in the target define the frame — so that I can drop the block exactly where I want it.
7. As a hardware engineer, I want net names to auto-map between source and target by matching identical names, so that the common case (rails like `+3V3`, `GND` named the same in both) needs no configuration.
8. As a hardware engineer, I want to override net mappings explicitly in config when names differ between projects, so that I can still reuse layouts when naming conventions diverge.
9. As a hardware engineer, I want unresolved net mismatches to surface as a clear error before any modification, so that I never apply a half-mapped block to a real board.
10. As a hardware engineer, I want the source PCB's layer stackup to match the target's, or the apply step to refuse and tell me what differs, so that a 2-layer block doesn't silently corrupt a 4-layer target.
11. As a hardware engineer, I want `kicad-blocks reuse` to fail fast and atomically — either the entire block is applied or the target file is untouched — so that a partial apply never leaves a board in an inconsistent state.

### Sync

12. As a hardware engineer, I want `kicad-blocks sync --dry-run` to compare the latest source block against the last-applied snapshot, so that I can see exactly what would change before committing.
13. As a hardware engineer, I want the dry-run output to list added, removed, moved, and net-rewired items separately, so that the diff is reviewable in the same way as a code diff.
14. As a hardware engineer, I want `kicad-blocks sync` (without `--dry-run`) to prompt for confirmation before applying, so that I don't overwrite hand-tuned routing inside the block by accident.
15. As a hardware engineer, I want `kicad-blocks sync --force` to apply without prompting, so that automated re-syncs work in CI or batch scripts.
16. As a hardware engineer, I want the plugin to record sync state — the source PCB hash, the applied block hash, the anchor refdes, and the plugin version — in a sidecar JSON file committed to git, so that diff history is reviewable and reproducible across machines.
17. As a hardware engineer, I want hand-edits to the target board *outside* the block region to be preserved across syncs, so that I can keep target-specific routing untouched.
18. As a hardware engineer, I want sync to refuse if the target appears to have been hand-edited *inside* the block region since the last apply, unless I pass `--force`, so that I don't silently lose work.

### Scaffold

19. As a hardware engineer, I want to scaffold a new KiCAD project from a chosen set of shared sheet paths, so that I can start a new board variant without hand-wiring the project file.
20. As a hardware engineer, I want the scaffolded project to include a root `.kicad_sch` that already references the chosen shared sheets, so that opening the project in KiCAD shows a working hierarchical design.
21. As a hardware engineer, I want the scaffolded project to include a blank `.kicad_pcb` with a placeholder board outline, so that I can immediately start placing the anchor footprints for reuse.
22. As a hardware engineer, I want the scaffolded project to include a starter `kicad-blocks.toml` populated with the sheets I chose, so that I don't have to write the config from scratch.
23. As a hardware engineer, I want the scaffolded project to open in KiCAD without errors, so that the generated artifacts are trustworthy.

### Panelize

24. As a hardware engineer, I want to declare a panel of multiple module PCBs in `kicad-blocks.toml` (which PCBs, spacing, mouse bites or tabs, board outline strategy), so that the panel definition lives with the project, version-controlled.
25. As a hardware engineer, I want `kicad-blocks panelize-config` to emit a KiKit-compatible JSON config from that declaration, so that I can run KiKit directly without translating settings by hand.
26. As a hardware engineer, I want the plugin to *not* invoke KiKit itself, so that my project isn't pinned to a particular KiKit version through this tool.

### Config and ergonomics

27. As a hardware engineer, I want all per-project settings (source PCBs, sheets, anchors, net mappings, panelize plan) in a single `kicad-blocks.toml` file next to the `.kicad_pro`, so that the configuration is discoverable and diffable.
28. As a hardware engineer, I want config validation errors to point to the file and line, so that I can fix mistakes quickly.
29. As a hardware engineer, I want `kicad-blocks validate` to check the config and report all problems without modifying any files, so that I can pre-flight-check a config before running any apply step.
30. As a hardware engineer, I want machine-readable output (`--format json`) for every command, so that I can wire the tool into custom scripts and CI.
31. As a hardware engineer, I want clear, actionable error messages with concrete next steps, so that diagnosing a failed reuse doesn't require reading the source code.

### Distribution and OSS

32. As a KiCAD user, I want to install the plugin with `pipx install kicad-blocks`, so that I can adopt it without cloning a repo.
33. As a KiCAD user, I want the plugin to be listed in the KiCAD Plugin & Content Manager once a GUI shell exists, so that I can discover it from inside KiCAD.
34. As a KiCAD user, I want the plugin to be MIT-licensed, so that I can use it in commercial product development without obligation.
35. As a contributor, I want a clear README, contribution guide, and decent test coverage on the domain core, so that I can submit changes confidently.

## Implementation Decisions

### Foundation and dependencies

- **KiCAD 9 only**, leveraging the post-v6 instance-data model where reference designators and unit data live in the parent project, not the shared sheet file. Earlier versions are out of scope.
- **Pure-Python S-expression parsing** via [kiutils](https://github.com/mvnmgrx/kiutils). No running KiCAD instance is required at runtime. This makes the plugin trivially testable in CI without xvfb and decouples our release cycle from KiCAD's IPC API stability.
- **Python CLI**, packaged for PyPI. Click for command routing. Pydantic (or dataclasses with a custom validator) for config typing and error reporting.
- **MIT license**.

### Module structure

The plugin is organized into deep modules with clear boundaries:

**Foundation**
- `kicad_io` — thin layer over kiutils for reading/writing `.kicad_pcb`, `.kicad_sch`, `.kicad_pro`. Surfaces a typed model and isolates any file-format churn behind one boundary.

**Domain core (pure, no I/O)**
- `block` — the heart of the plugin. Provides `extract(pcb, sheet) -> Block` (extracts an anchor-relative normalized representation of footprints + tracks/vias/zones/silkscreen belonging strictly to one sheet) and `apply(block, pcb, anchor, net_map) -> PCB` (applies that representation to a target PCB at a given anchor footprint with net rewriting).
- `net_map` — given source and target net lists plus a user-override table, produces a `NetMap` and a list of unresolved nets. Auto-matches by name; fails on remaining mismatches unless overridden.
- `diff` — compares a `Block` against the matching region of a target PCB; produces a structured diff (added/removed/moved/net-rewired items) for the sync dry-run.
- `transform` — 2D affine math for repositioning items relative to an anchor (rotation + translation).

**Side-effectful adapters**
- `config` — load and validate `kicad-blocks.toml`; return typed config or a list of errors with file/line.
- `sync_state` — read/write the sidecar `<project>.kicad-blocks.lock.json` recording source PCB content hash, applied block hash, anchor refdes, plugin version.
- `scaffold` — given a list of shared sheet paths and a project name, write a new project directory: `.kicad_pro`, root `.kicad_sch` referencing the sheets, blank `.kicad_pcb` with a placeholder outline, starter `kicad-blocks.toml`.
- `kikit_config` — translate the panelize section of `kicad-blocks.toml` into a KiKit JSON config. No subprocess.

**Shell**
- `cli` — Click subcommands (`reuse`, `sync`, `scaffold`, `panelize-config`, `validate`). Each subcommand is a thin wrapper: load config, call domain core, render result. No business logic.
- `reporter` — pretty-print diffs, errors, dry-run plans. Terminal renderer + `--format json` for scripting.

### Key semantic decisions

- **Sheet membership** is determined by the `Sheetfile`/sheet-path property carried on each footprint by KiCAD. A footprint belongs to a block if its sheet path matches the configured sheet for that block.
- **Footprint identity across projects** is the symbol UUID stored in the shared `.kicad_sch` file. This is stable across projects because instance data (refdes) lives in the parent project, not the shared sheet.
- **Anchor**: the user nominates a footprint refdes in the target PCB; its position and rotation define the frame into which the block is placed.
- **Strict boundary**: only items fully contained within the block (i.e., all endpoints touch footprints whose sheet matches the block) are managed. Tracks/zones that exit the block are the user's responsibility in the target.
- **Net mapping**: auto-match by name. Mismatches surface in a dry-run report; the user declares overrides in config. Unresolved nets block any apply.
- **Sync conflict**: dry-run + interactive confirmation is the default. `--force` skips the prompt. State is persisted as a sidecar JSON file committed to git.
- **Atomic writes**: any command that modifies a `.kicad_pcb` writes to a temp file and renames on success; partial failure leaves the original untouched.
- **Layer stackup mismatch** between source and target is detected up front and refuses the apply unless explicitly acknowledged in config.

### CLI surface (v0.1)

- `kicad-blocks reuse [--config PATH] [--dry-run] [--force]`
- `kicad-blocks sync [--config PATH] [--dry-run] [--force]`
- `kicad-blocks scaffold --name NAME --sheets SHEET... [--dir DIR]`
- `kicad-blocks panelize-config [--config PATH] [--out PATH]`
- `kicad-blocks validate [--config PATH]`
- Every command supports `--format {text,json}` for output.

### Config format

A TOML file (`kicad-blocks.toml`) lives next to each project's `.kicad_pro`. Declares:
- Source PCBs and which sheets they're canonical for
- Per-block anchor refdes in the target
- Net mapping overrides
- Optional panelize section (modules, spacing, separation style, board outline strategy) consumed by `panelize-config`

The state file (`<project>.kicad-blocks.lock.json`) is generated and managed by the plugin; it should be committed to git.

### Distribution

- PyPI (`pipx install kicad-blocks`)
- GitHub release tarballs
- KiCAD Plugin & Content Manager submission deferred until a GUI shell exists (post-v0.1)

## Testing Decisions

### What makes a good test for this project

Tests should exercise observable, external behavior: given input PCB/sheet files and a config, the output PCB file (or returned `Block` / `Diff` data structure) has the expected shape. We should not test internal helper functions, private methods, or implementation-specific intermediate state. Test files are committed `.kicad_pcb` / `.kicad_sch` / `.kicad_pro` fixtures that can be opened in KiCAD by a maintainer to inspect or extend.

Tests should fail in human-readable ways: a diff in normalized form (e.g., footprint position deltas in mm) rather than raw S-expression dumps.

### Modules to test in v0.1

- **`block` (extract + apply)** — the highest-leverage surface. Fixtures: a small synthetic source PCB with one hierarchical sheet (a couple of footprints, a track or two, a zone, some silk); test that `extract` returns a `Block` with the expected normalized contents, and that `apply` produces a target PCB matching a golden file.
- **`diff`** — given two `Block`s (or a `Block` and a target PCB region), assert the structured diff identifies added/removed/moved/net-rewired items correctly across the canonical scenarios: identical, footprint moved, footprint removed, track added, net renamed.
- **Supporting pure functions: `net_map`, `transform`, `config`** — unit tests with the obvious edge cases:
  - `net_map`: case sensitivity, leading slashes in hierarchical net paths, overlapping override entries, unresolved nets
  - `transform`: rotation conventions (which direction is positive in KiCAD), composition of rotation + translation, identity, 180° special case
  - `config`: missing fields, type mismatches, unknown keys, error messages include file/line
- **`scaffold` smoke test** — end-to-end: invoke `scaffold` to generate a project, then invoke `kicad-cli` (or load with kiutils) to verify the generated `.kicad_pro` / `.kicad_sch` / `.kicad_pcb` parses without errors. This is the one test in v0.1 that needs a real KiCAD-related toolchain to be present.

### Out of scope for v0.1 tests

- Performance benchmarks
- GUI / IPC API tests
- Multi-version KiCAD compatibility matrix
- Property-based testing (worth revisiting once the v0.1 corpus exists)

### Prior art and conventions

- pytest as the runner
- Synthetic fixture files committed under `tests/fixtures/`, each project minimal but human-inspectable in KiCAD
- Golden-file assertions on output PCBs use normalized comparisons (parse via kiutils, compare structured fields) rather than raw text diffs, since KiCAD's S-expression writer is whitespace/order-sensitive
- One test per scenario, named for the behavior under test (`test_extract_preserves_track_layer`, `test_apply_rejects_layer_stackup_mismatch`)

## Out of Scope

- **GUI action plugin in pcbnew.** The CLI is the v0.1 surface. A GUI shell that shares the same core is a v0.2+ target.
- **KiCAD 8 or earlier.** The instance-data model is the foundation; pre-v6 versions are incompatible. v0.1 targets v9 only.
- **PCB layout reuse for items that are not anchored to a hierarchical sheet.** If you want to reuse a free-standing region of layout, you need to put those items in a sheet first.
- **Schematic-side electrical modifications beyond scaffold.** The plugin will not rewire root-level nets in existing projects, modify shared sheet contents, or add/remove components from a sheet.
- **Multi-board electrical connectivity validation.** The plugin does not check that signals leaving one module's connector actually land on another module's connector. That belongs to a system-level design tool.
- **Variant management of fitted/not-fitted components.** KiCAD's DNP feature already handles this; `kicad-blocks` is for the orthogonal "different layouts from the same source sheet" problem.
- **Invoking KiKit directly for panelize.** We emit KiKit config; the user runs KiKit. Avoids a hard version dependency.
- **Tracks/zones that cross the block boundary.** Strict-boundary policy: anything not fully inside the block is the user's problem in the target. A future "stub-and-leave" mode can be added if there's demand.
- **Linked footprint placement (live link from source to target).** Reuse is a discrete operation, not a live binding. Sync re-applies on demand.
- **Custom net classes / design rules synchronization.** v0.1 propagates only physical layout items; net classes are managed per project by KiCAD's existing mechanisms.

## Further Notes

- **Prior art to reference**: the `replicate_layout` plugin by MitjaNemec handles within-project layout replication and informs the matching algorithm. KiKit informs the panelize integration. kiutils provides the file-format substrate.
- **Sustainability**: as a pure-Python tool with a small surface area, the maintenance burden across KiCAD versions is dominated by kiutils's release cadence. We pin a kiutils minimum version and bump deliberately.
- **Future roadmap signals to watch**: KiCAD project itself has discussed first-class "design blocks" / cross-project layout reuse on their roadmap. If KiCAD 10 or 11 lands this natively, `kicad-blocks` may become a migration helper rather than a long-lived tool. Worth a heads-up to the KiCAD developers before v0.1 ships.
- **Telemetry**: none. The tool runs entirely offline.
- **Versioning**: SemVer. v0.x while the config format and CLI surface are unstable; v1.0 once they're locked.
- **Implementation lead**: Noah Grant. Code review by Claude.
