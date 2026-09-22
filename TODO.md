# Tool Feeder — To-Do

Living backlog of features and bugs in this repo's own code/config/docs.
Machine-specific deployment/hardware status (which printer has which fix,
physical checks needed, etc.) belongs in `.claude/CONTEXT.md`'s running log
instead, not here. Link to a GitHub issue (`#N`) instead of writing detail
here once one exists.

## Features

- [ ] Add linting to the codebase, similar to what Spoolman does, to make
      the code more readable and manageable. Spoolman uses **Ruff only**
      (linter + formatter, no Black/mypy/isort/Flake8) via `[tool.ruff]` in
      `pyproject.toml`: `line-length = 120`, `target-version = "py310"`,
      `[tool.ruff.lint] select = ["ALL"]` with a curated ignore list, plus
      `[tool.ruff.lint.per-file-ignores]` relaxing rules for
      tests/migrations/scripts. For Tool_feeder: add a `pyproject.toml`
      covering `src/screen/` and `src/sync/`, pick a starting ignore list
      (KlipperScreen's `Panel` subclassing and Klipper macro conventions may
      need their own per-file relaxations), and decide whether to enforce it
      in CI or just document `ruff check`/`ruff format` as a dev step.
- [ ] Possibly bring tool mapping and load/unload controls to Fluidd/Mainsail
      too, not just KlipperScreen — currently `SET_TOOLMAP`/load/unload are
      only reachable via KlipperScreen panels or the console. Exploratory —
      not scoped yet (macro-based UI panel? plugin? just documented console
      macros for now?).
