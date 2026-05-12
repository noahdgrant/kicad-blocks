# Contributing to kicad-blocks

Thanks for your interest in contributing. This guide covers the development workflow and our commit conventions.

## Development setup

`kicad-blocks` is managed with [uv](https://docs.astral.sh/uv/). With uv installed:

```sh
git clone https://github.com/noahdgrant/kicad-blocks.git
cd kicad-blocks
uv sync
```

`uv sync` creates a `.venv/` and installs runtime + dev dependencies from `uv.lock`. You can either activate the venv (`source .venv/bin/activate`) or prefix every command with `uv run`.

## Running the tooling

```sh
uv run kicad-blocks --version       # smoke-test the CLI
uv run pytest                       # tests
uv run ruff check                   # lint
uv run ruff format                  # auto-format (or --check for CI parity)
uv run pyright                      # type check
```

CI runs all four of these on every push and PR. Keep them green locally before pushing.

## Conventional Commits

Every PR title must follow the [Conventional Commits](https://www.conventionalcommits.org/) format. A GitHub Action validates this on every PR.

### Format

```
<type>(<optional scope>): <description>

[optional body]

[optional footer(s)]
```

### Accepted types

| Type | When to use |
|------|-------------|
| `build` | Changes affecting the build system or external dependencies |
| `chore` | Other changes that don't modify src or test files |
| `ci` | Changes to CI configuration files and scripts |
| `docs` | Documentation-only changes |
| `feat` | A new feature |
| `fix` | A bug fix |
| `perf` | Code change that improves performance |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `style` | Formatting, whitespace, etc. (no behavior change) |
| `test` | Adding or correcting tests |
| `revert` | Reverts a previous commit |

### Breaking changes

Append `!` after the type (and scope, if present) to signal a breaking change:

```
feat!: rename `--source` flag to `--source-pcb`
```

### Examples

```
feat(reuse): extract footprints by sheet UUID
fix(config): accept absolute paths in source PCBs
docs: clarify net-mapping precedence
chore: bump kiutils to 1.4
```

## Pull requests

- Keep PRs focused on a single concern.
- The PR title is the commit title that will land on `main` (squash-merge with conventional title).
- Make sure CI is green before requesting review.
- Reference the issue you're closing in the PR body (e.g. `Closes: #42`).

## Labels

Issues and PRs use prefixed labels:

- `type:*` — conventional commit type (one per issue)
- `area:*` — area of the codebase (`pcb`, `schematic`, `config`, `cli`, `release`, `packaging`, `kikit`)
- `command:*` — which CLI subcommand the change touches

## Reporting issues

Bug reports and feature requests are welcome — open an [issue](https://github.com/noahdgrant/kicad-blocks/issues). Please include the KiCAD version, the kicad-blocks version, and a minimal reproducer where possible.
