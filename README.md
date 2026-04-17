# sol-skill

A suite of tools for working on ASU's **Sol** supercomputer.

- a **CLI** you run from the shell to keep your scratch data alive
- an **agent skill** that teaches an AI coding assistant how to operate
  on Sol on your behalf

Both share the same helper scripts (`scripts/`) and reference notes
(`references/`). The official doc for every policy and convention
referenced here is the ASU Research Computing site:
<https://docs.rc.asu.edu/>.

## Intended use

`sol_renew.py` exists to help you **extend the life of important files
you still actively need** — source trees, paper drafts, in-progress
datasets — when Sol's deletion pipeline would otherwise discard them
while you are busy with something else.

It is **not** a tool to bypass, defeat, or abuse Sol's scratch
retention policy. Scratch is a shared, finite resource; the policy
exists so every user gets a fair share. If a file does not deserve a
spot on `/scratch` anymore, let it go. Use `.solkeep` to describe
what matters, not what you happen to have.

Specifically:

- Do **not** keep-list directories you no longer work on — move them
  off `/scratch` (to `/home`, local archive, or cloud storage) or let
  them age out.
- Do **not** schedule `sol_renew.py` on an aggressive cron to keep
  everything alive indefinitely. Run it when Sol sends you a warning.
- Do **not** use it to sidestep your group's scratch quota. Quota and
  retention are separate systems; this tool only touches timestamps.

If you are unsure whether a file should stay on `/scratch`, contact
ASU Research Computing rather than touching it.

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
  Sol's own warning CSVs and (b) match your `.solkeep`. Always run
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
    ├── scratch.md           # Scratch pipeline, .solkeep, sol_renew details
    ├── sharing.md           # File sharing between cluster users
    └── slurm.md             # Slurm / SBATCH reference
```

## CLI: `sol_renew.py`

Sol deletes inactive files under `/scratch` on a layered schedule. Each
stage of the pipeline announces itself via a CSV file dropped in your
`$HOME` (filenames and thresholds per the [Sol scratch
docs](https://docs.rc.asu.edu/scratch)). `sol_renew.py`:

- reads those CSVs,
- intersects them with `$HOME/.solkeep` — a gitignore-style keep-list
  (matched paths are the ones to **keep**; bare paths mean "this
  directory and everything under it"),
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
sol_renew.py --solkeep PATH       # default: $HOME/.solkeep
sol_renew.py -j N                   # parallel workers
sol_renew.py -v                     # verbose plan + progress
sol_renew.py -n                     # alias for --dry-run
```

Exit codes: `0` all good · `1` at least one directory failed · `2` no
rules loaded (empty or missing `.solkeep`).

### `.solkeep` quickstart

Rules match against the **directory paths** listed in Sol's warning
CSVs, not against individual files — matching decides which whole
flagged directories get touched. Patterns are literal (no shell
expansion) so write your real username in place of `sparky` below.

```gitignore
# keep these trees (bare path = recursive match)
/scratch/sparky/my-project
/scratch/sparky/experiments

# glob support (matches a directory path; * does not cross /)
/scratch/sparky/runs/*
/scratch/sparky/data/**

# negation: carve subtrees out of an otherwise-protected parent
!/scratch/sparky/my-project/**/__pycache__
!/scratch/sparky/my-project/**/.venv/**
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
one place for every behavior.

## References

Cleaned-up notes on the ASU Research Computing docs, for quick lookup:

- [module.md](references/module.md) — loading/unloading software modules
- [scratch.md](references/scratch.md) — scratch deletion pipeline,
  `.solkeep` syntax, `sol_renew.py` internals
- [sharing.md](references/sharing.md) — sharing files between users
- [slurm.md](references/slurm.md) — submitting and managing Slurm jobs

Official doc (authoritative): <https://docs.rc.asu.edu/>.
