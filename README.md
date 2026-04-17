# sol-skill

A suite of tools for working on ASU's **Sol** supercomputer.

- a **CLI** you run from the shell to keep your scratch data alive
- an **agent skill** that teaches an AI coding assistant how to operate
  on Sol on your behalf

Both share the same helper scripts (`scripts/`) and reference notes
(`references/`). The upstream source of truth for every policy and
convention referenced here is the ASU Research Computing docs:
<https://docs.rc.asu.edu/>.

## Who is this for

- **Sol users** who want a safer, auditable way to keep files on
  `/scratch` from being auto-deleted, driven by Sol's own warning emails
  and CSV lists.
- Users who are comfortable on the command line and already work in a
  Linux/HPC environment.
- Users who let an AI assistant help them with cluster work and want
  that assistant to follow project conventions consistently.

If you do not run code on Sol, this repo will not be useful to you.

## Assumptions and risks

Read these before installing.

- **Python-first.** The main tool (`sol_renew.py`) is a Python script.
  It uses [`uv`](https://docs.astral.sh/uv/) via an inline-metadata
  shebang to provision its own interpreter + dependencies, so you do
  not need to manage a virtualenv — but you do need `uv` on your
  `$PATH`. System `python3` on HPC systems is frequently older than
  modern code expects; this repo avoids it on purpose.
- **The renewal CLI changes file timestamps** (`atime` + `mtime`) on
  files under `/scratch`. It never deletes, moves, or reads file
  contents. It only walks directories that **both** (a) appear in
  Sol's own warning CSVs and (b) match your `.solignore`. Always run
  `--dry-run` once to verify the plan before the real pass.
- **Sol's deletion policy is set by ASU Research Computing, not this
  tool.** The policy (thresholds, CSV filenames, warning cadence) is
  documented at <https://docs.rc.asu.edu/scratch>. If upstream changes
  the CSV names or schema, this tool will need to follow. Don't treat
  this repo as authoritative — upstream docs are.
- **HPC shared filesystems can be slow.** The renewal CLI walks each
  flagged directory and touches every file in it. On a shared
  filesystem with millions of small files this can take a long time.
  See "Performance notes" in `references/scratch.md` before scaling
  parallelism up.
- **No warranty.** This is a personal toolkit that I find useful;
  published in case others find it useful too. Review the code before
  running it on data you care about.

## Layout

```text
sol-skill/
├── README.md                # You are here
├── SKILL.md                 # Agent skill entry point
├── scripts/
│   └── sol_renew.py         # CLI: renew scratch files flagged by Sol
└── references/
    ├── module.md            # Environment Modules cheatsheet
    ├── scratch.md           # Scratch pipeline, .solignore, sol_renew details
    ├── sharing.md           # File sharing between cluster users
    └── slurm.md             # Slurm / SBATCH reference
```

## CLI: `sol_renew.py`

Sol deletes inactive files under `/scratch` on a layered schedule. Each
stage of the pipeline announces itself via a CSV file dropped in your
`$HOME` (filenames and thresholds per the [Sol scratch
docs](https://docs.rc.asu.edu/scratch)). `sol_renew.py`:

- reads those CSVs,
- intersects them with `$HOME/.solignore` — a gitignore-style keep-list
  (inverted semantics: matched paths are **protected**, not ignored;
  bare paths mean "this directory and everything under it"),
- runs `touch -a -m -c` only on files inside the flagged-and-protected
  directories.

It never walks `/scratch` wholesale.

### Install

No install step. The shebang is:

```shell
#!/usr/bin/env -S uv run --script
```

On first run `uv` provisions an interpreter + `rich` from the inline
PEP 723 metadata and caches them. Ensure `uv` is on your `$PATH`.

Optional: symlink the script so it is globally callable.

```shell
ln -s "$PWD/scripts/sol_renew.py" ~/.local/bin/sol-renew
```

### Usage

```shell
# 1. Preview first. Always.
sol_renew.py --dry-run -v

# 2. Run for real. Default: all non-removed stages.
sol_renew.py

# 3. Focus on the most urgent bucket.
sol_renew.py --stage pending

# Other flags
sol_renew.py --stage {pending,over90,inactive,all}
sol_renew.py --csv-dir DIR          # default: $HOME
sol_renew.py --solignore PATH       # default: $HOME/.solignore
sol_renew.py -j N                   # parallel workers
sol_renew.py -v                     # verbose plan + progress
sol_renew.py -n                     # alias for --dry-run
```

Exit codes: `0` all good · `1` at least one directory failed · `2` no
rules loaded (empty or missing `.solignore`).

### `.solignore` quickstart

Patterns are literal strings — **no shell expansion** — so write your
real username in place of `alice` below.

```gitignore
# keep these trees (bare path = recursive match)
/scratch/alice/my-project
/scratch/alice/experiments

# glob support
/scratch/alice/logs/*.log
/scratch/alice/data/**

# negation: carve subtrees out of an otherwise-protected parent
!/scratch/alice/my-project/**/__pycache__
!/scratch/alice/my-project/**/.venv/**
```

Rules are evaluated top-to-bottom, last match wins (same as gitignore).

Full syntax + performance notes: [references/scratch.md](references/scratch.md).

## Agent skill

`SKILL.md` is the entry point an AI coding assistant reads when this
directory is installed as a skill. It tells the assistant how to detect
whether it is on Sol, how to manage Environment Modules, where to keep
data, how to submit Slurm jobs, and how to use `sol_renew.py` to keep
scratch data alive.

The skill shares `scripts/` and `references/` with the human CLI —
one source of truth for every behavior.

## References

Cleaned-up notes on the ASU Research Computing docs, for quick lookup:

- [module.md](references/module.md) — loading/unloading software modules
- [scratch.md](references/scratch.md) — scratch deletion pipeline,
  `.solignore` syntax, `sol_renew.py` internals
- [sharing.md](references/sharing.md) — sharing files between users
- [slurm.md](references/slurm.md) — submitting and managing Slurm jobs

Upstream (source of truth): <https://docs.rc.asu.edu/>.
