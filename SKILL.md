---
name: sol-skill
description: Tips and conventions for working on ASU's Sol supercomputer. Use this skill when the agent is operating on Sol, submitting SLURM jobs, managing modules, or transferring data on the cluster.
---

# Sol skills

Official doc: <https://docs.rc.asu.edu/>.

That official doc is authoritative; these notes are just a cache.

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

| Location         | Purpose                      | Policy                          |
|------------------|------------------------------|---------------------------------|
| `/home/$USER`    | Config, small files          | Limited space, backed up        |
| `/scratch/$USER` | Large data, caches, outputs  | Layered deletion — see Sol docs |

Always place large data files, model caches, and outputs under
`/scratch/$USER`.

### Renewing the Scratch Timestamp

Sol deletes inactive `/scratch` files on a layered schedule and writes
per-stage CSV warnings into `$HOME`. The thresholds, CSV filenames,
and warning cadence are defined by ASU Research Computing; upstream is
defined by ASU Research Computing; the official doc is authoritative:
<https://docs.rc.asu.edu/scratch>.

Use `scripts/sol_renew.py` to refresh timestamps driven by those CSVs
and a user-maintained `.solkeep` keep-list. See
[references/scratch.md](references/scratch.md) for the CSV schema,
`.solkeep` syntax, and performance notes.

#### Default strategy

Do not bulk-touch `/scratch/$USER` (for example,
`find /scratch/$USER -exec touch {} +`). The default flow is driven by
two inputs:

1. `$HOME/.solkeep` — a gitignore-style file listing what to
   **keep** (matched paths are protected). Bare paths are treated as
   `path/**`.
2. Sol's CSV warnings in `$HOME`.

The script intersects the two: only directories that Sol has flagged
**and** that match `.solkeep` get touched. Nothing else is walked.
This keeps I/O bounded even when the inactive list has thousands of
rows.

#### Commands

The script is self-bootstrapping via `uv` (PEP 723 inline metadata in
the shebang). The system `python3` on Sol is generally older than
modern code expects — rely on `uv` instead (check `python3 --version`
if you need to confirm).

```shell
# Preview what would be touched (run this first)
$SKILL_DIR/scripts/sol_renew.py --dry-run -v

# Default: touch everything in .solkeep that appears in any CSV
$SKILL_DIR/scripts/sol_renew.py

# Only chase the most urgent bucket
$SKILL_DIR/scripts/sol_renew.py --stage pending

# Raise parallelism explicitly if the filesystem can handle it
$SKILL_DIR/scripts/sol_renew.py -j 16
```

#### Example `.solkeep`

Patterns are literal strings — no shell expansion — so write your real
username in place of `sparky`.

```gitignore
# keep project trees (bare path = recursive)
/scratch/sparky/my-project
/scratch/sparky/experiments
/scratch/sparky/datasets

# carve out stale build artifacts
!/scratch/sparky/my-project/**/__pycache__
!/scratch/sparky/my-project/**/.venv/**
```

#### Long-running behavior

A touch pass over a directory holding many small files on a shared
cluster filesystem can take a long time, with no per-file output —
progress is reported per-directory. Do not interpret a silent stretch
as a hang. A full pass over a large inactive list can legitimately
take tens of minutes. Use `-v` in a separate shell, or inspect the
child `find`/`touch` processes via `ps`, if you need a liveness
check.

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

- The system `python3` on Sol is typically older than modern code
  expects. Check with `python3 --version` before relying on it.
  Prefer [`uv`](https://docs.astral.sh/uv/) to manage interpreters and
  environments.
- Point the `uv` cache to `/scratch` so it does not fill `/home`:

  ```shell
  export UV_CACHE_DIR=/scratch/$USER/.cache/uv
  ```
- For small utility scripts, prefer PEP 723 inline metadata with a
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
