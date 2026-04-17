# sol-skill

A personal, opinionated suite of tools for working on ASU's **Sol**
supercomputer. It ships two things in one repo:

1. **CLI tools** — standalone commands you run directly from the shell.
2. **An agent skill** — instructions and scripts Claude Code uses when
   operating on Sol on your behalf.

Both halves share the same helper scripts under `scripts/` and the same
reference docs under `references/`.

## Layout

```text
sol-skill/
├── README.md                # You are here
├── SKILL.md                 # Agent skill entry point (Claude reads this)
├── scripts/
│   └── sol_renew.py         # CLI: renew scratch files before Sol deletes them
└── references/
    ├── module.md            # Environment Modules cheatsheet
    ├── scratch.md           # Scratch pipeline, .solignore, sol_renew details
    ├── sharing.md           # File sharing between cluster users
    └── slurm.md             # Slurm / SBATCH reference
```

## CLI tools (for humans)

### `sol_renew.py` — surgical scratch renewal

Sol deletes files under `/scratch/$USER` via a layered pipeline (45 days
→ 90 days → <7 days → removed). Each stage is announced in a CSV dropped
in your `$HOME`. `sol_renew.py`:

- reads those CSVs,
- intersects them with `$HOME/.solignore` (gitignore-style keep-list —
  bare paths mean "this dir and everything under it"),
- touches only the files inside directories that pass both filters,
- runs the `touch` in parallel with a live TUI progress bar (Rich) on a
  real terminal, and a compact per-directory line when piped.

#### Install

No install step. The shebang uses `uv` with PEP 723 inline metadata, so
the first run provisions Python 3.10+ and `rich` into the uv cache.
Ensure [`uv`](https://docs.astral.sh/uv/) is on your `$PATH`.

```shell
# optional: make it globally callable
ln -s "$PWD/scripts/sol_renew.py" ~/.local/bin/sol-renew
```

#### Usage

```shell
# 1. Preview first (always)
sol_renew.py --dry-run -v

# 2. Renew every flagged dir that matches .solignore (default: all stages)
sol_renew.py

# 3. Focus on the most urgent bucket only
sol_renew.py --stage pending

# Other flags
sol_renew.py --stage {pending,over90,inactive,all}
sol_renew.py --csv-dir DIR          # default: $HOME
sol_renew.py --solignore PATH       # default: $HOME/.solignore
sol_renew.py -j N                   # parallel workers
sol_renew.py -v                     # verbose plan + progress
sol_renew.py -n                     # dry-run alias
```

Exit codes: `0` all good · `1` at least one dir failed · `2` no
rules loaded (empty `.solignore`).

#### `.solignore` quickstart

```gitignore
# keep these trees (bare path = recursive match)
/scratch/alice/project
/scratch/alice/experiments

# glob support
/scratch/alice/logs/*.log
/scratch/alice/data/**

# negation: carve subtrees out of an otherwise-protected parent
!/scratch/alice/project/**/__pycache__
!/scratch/alice/project/**/.venv/**
```

Rules are evaluated top-to-bottom, last match wins (same as gitignore).

Full syntax + perf notes: [references/scratch.md](references/scratch.md).

## Agent skill (for Claude Code)

`SKILL.md` is the entry point. Claude Code loads it automatically when
you install this directory as a skill (e.g. symlinked into
`~/.claude/skills/sol-skill/`). The skill teaches Claude how to:

- detect whether it is running on Sol,
- load and manage Environment Modules,
- keep files off `/home` and under `/scratch`,
- run the same `sol_renew.py` CLI to protect scratch data,
- submit and monitor Slurm jobs,
- transfer data with `rsync`,
- use `uv` for Python (system `/usr/bin/python3` on Sol is 3.6.8 and
  should be avoided).

The skill shares the `scripts/` and `references/` directories with the
human-facing CLI — there is a single source of truth for every
behavior.

## References

Cleaned-up excerpts + my own notes on the ASU Research Computing docs:

- [module.md](references/module.md) — loading/unloading software modules
- [scratch.md](references/scratch.md) — scratch deletion pipeline,
  `.solignore` syntax, `sol_renew.py` internals
- [sharing.md](references/sharing.md) — sharing files between users
- [slurm.md](references/slurm.md) — submitting and managing Slurm jobs

Upstream: <https://docs.rc.asu.edu/>.
