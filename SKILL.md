---
name: sol-skill
description: Tips and conventions for working on ASU's Sol supercomputer. Use this skill when the agent is operating on Sol, submitting SLURM jobs, managing modules, or transferring data on the cluster.
---

# Sol skills

Official doc: <https://docs.rc.asu.edu/>.

This official doc is the source of truth.

## Detecting the Environment

Run `hostname -a` to determine whether you are on Sol or a local
machine. If the output resembles `sc001.sol.rc.asu.edu`, you are on
Sol.

## General Rules

1. Save datasets and caches under `/scratch`.
2. You do not have `sudo` privileges, so maintain a local environment under `/home/$USER/.local` or `/home/$USER/opt`.
3. Use `git` to keep code in sync between local and cluster.

## Modules

Sol uses the **Environment Modules** system to manage software.
Load, list, and unload modules before running any workload.

See [references/module.md](references/module.md) for commands and
naming conventions.

## Filesystem and Storage

Sol provides two main storage areas:

| Location   | Purpose                      | Policy                        |
|------------|------------------------------|-------------------------------|
| `/home/$USER`    | Config, small files          | Limited space, backed up      |
| `/scratch/$USER` | Large data, caches, outputs  | **180-day deletion policy**   |

Always place large data files, model caches, and outputs under
`/scratch/$USER`.

### Renewing the Scratch Timestamp

Sol uses a **layered deletion pipeline** (45 / 90 / <7 days). It also
writes per-stage CSVs into `$HOME` listing the flagged directories:

- `scratch-dirs-pending-removal.csv` — <7 days to deletion (most urgent)
- `scratch-dirs-over-90days.csv`     — past 90 days
- `scratch-dirs-inactive.csv`        — 45+ days (early warning)
- `scratch-dirs-removed.csv`         — already deleted (post-facto)

See [references/scratch.md](references/scratch.md) for the full
reference on pipeline stages, CSV schema, `.solignore` syntax, and
performance notes.

#### Default strategy

Do **not** blindly `touch -r` across `/scratch/$USER`. The default flow
is surgical and driven by two inputs:

1. `$HOME/.solignore` — a gitignore-style file listing what to
   **keep** (semantics are inverted from gitignore; matched paths are
   protected, not ignored). Bare paths are treated as `path/**`.
2. The Sol CSVs in `$HOME`.

The orchestrator intersects the two: only directories that Sol has
flagged **and** that match `.solignore` get touched. Nothing else is
walked. This keeps I/O bounded even when inactive.csv has thousands of
rows.

#### Commands

The script runs via `uv` (PEP 723 inline metadata in the shebang).
Prefer `uv`-managed Python on Sol; do not rely on `/usr/bin/python3`
(system python is 3.6.8).

```shell
# Preview what would be touched (always run this first)
$SKILL_DIR/scripts/sol_renew.py --dry-run -v

# Default: touch everything in .solignore that appears in any CSV
$SKILL_DIR/scripts/sol_renew.py

# Only chase the most urgent bucket
$SKILL_DIR/scripts/sol_renew.py --stage pending

# Bump parallelism on a slow NFS night
$SKILL_DIR/scripts/sol_renew.py -j 16
```

#### Example `.solignore`

```gitignore
# keep project trees (bare path = recursive)
# (patterns are literal — no shell expansion, so use your real username here)
/scratch/alice/my-project
/scratch/alice/experiments
/scratch/alice/datasets

# but don't revive stale build artifacts inside them
!/scratch/alice/my-project/**/__pycache__
!/scratch/alice/my-project/**/.venv/**
```

#### Do not cancel early (agent note)

`touch` over NFS on a directory with tens of thousands of small files
can take **minutes per directory** with no interleaved output — progress
is printed per-directory, not per-file. Agents running this script
must not interpret a silent stretch as a hang or kill the job. A full
run covering an inactive.csv with ~800 dirs can legitimately take
tens of minutes. If you truly need to check progress, run with `-v`
in a separate shell or inspect `lsof`/`ps` for the child `find`/`touch`
processes.

### Sharing Files

See [references/sharing.md](references/sharing.md) for the
step-by-step procedure to share files with other users on the
cluster.

## Submitting Jobs

Sol uses **Slurm** to manage jobs. Submit work via SBATCH scripts.

See [references/slurm.md](references/slurm.md) for submission
commands, example scripts (serial, MPI, job arrays),
troubleshooting, and exit codes.

## Transferring Data

Use `rsync` for efficient transfers between local and Sol:

```shell
rsync -avz ./local_dir/ $USER@sol.asu.edu:/scratch/$USER/remote_dir/
```

For large transfers, prefer `rsync --progress` or `scp -r`.

## Python

- System `/usr/bin/python3` is **3.6.8** — too old for most modern code.
  Don't rely on it. Use `uv` for every Python workflow on Sol.
- Use `uv` to manage Python environments on the cluster.
- Point the `uv` cache to `/scratch` to avoid filling `/home`:

  ```shell
  export UV_CACHE_DIR=/scratch/$USER/.cache/uv
  ```
- For small utility scripts, prefer PEP 723 inline metadata and a
  `#!/usr/bin/env -S uv run --script` shebang so the script
  self-bootstraps its interpreter (`scripts/sol_renew.py` uses this).

## LaTeX

Use R package `tinytex` to manage a local TeX Live installation

1. Find the latest R distribution with `module avail r-4`.
2. Use the R package `tinytex` to download a local TeX Live
   distribution under `~/.local/bin/latex`.
3. Install TeX packages on demand:

   ```shell
   tlmgr install <package>
   ```
4. If got `tlmgr is older than remote repository`, it means `tlmgr` needs to be updated.
   This is done through `tinytex::reinstall_tinytex()`.
   Load R module first, then run `Rscript -e "tinytex::reinstall_tinytex(repository = "illinois")"` to update the local
  TeX Live distribution.

## Working with VS Code

To auto-activate custom commands in VS Code, you can modify `terminal.integrated.env.linux`
and `VSCODE_PYTHON_ZSH_ACTIVATE` in your `settings.json`.

For example, to activate a Python virtual environment, add the following to your `settings.json`:

```json
{
  "terminal.integrated.env.linux": {
    "VSCODE_PYTHON_ZSH_ACTIVATE": "source .venv/bin/activate"
  }
}
```
