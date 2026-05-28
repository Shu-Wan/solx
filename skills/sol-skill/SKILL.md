---
name: sol-skill
version: 0.2.1
description: Tips and conventions for working on ASU's Sol supercomputer. Use this skill when the agent is operating on Sol, submitting SLURM jobs, managing modules, or transferring data on the cluster.
license: MIT
---

# Sol skills

Official doc: <https://docs.rc.asu.edu/>.

That official doc is authoritative; these notes are just a cache.

## What this skill helps with

This skill teaches an AI coding assistant how to operate on ASU's Sol
supercomputer the way a careful human user would. Concretely, it
covers:

- **Detecting the environment** — recognizing whether the agent is
  running on a Sol login/compute node or on a laptop, and branching
  behavior accordingly.
- **Storage decisions** — putting datasets, model weights, and caches
  under `/scratch/$USER` and keeping `/home` for config; never asking
  for `sudo`.
- **Scratch retention** — refreshing files Sol has flagged for
  deletion, driven by Sol's own warning CSVs and a user-maintained
  `.solkeep` keep-list (via `scripts/sol_renew.py`); refusing to bulk
  `touch` `/scratch`.
- **Getting software onto Sol without sudo** — `module load` for what
  the cluster already provides, `uv` for Python interpreters and
  envs, R's `tinytex` for LaTeX, `~/.local`/`~/opt` for everything
  else.
- **SLURM jobs** — submitting and managing batch jobs (`sbatch`,
  `squeue`, `scancel`, `scontrol`) with correct partition/QOS/time
  headers; example serial, MPI, and job-array scripts; pointers to
  Sol's prebuilt SBATCH templates.
- **Status queries about you and your jobs** — translating "what's
  queued?", "what's my fairshare?", "what accounts can I use?",
  "why is my job pending?" into the right SLURM call (or Sol
  wrapper when it adds genuine value, e.g. `myfairshare`).
- **Reaching a Sol-side service from a laptop** — situational guide
  to running a Jupyter / dev server / MLflow UI / OAuth callback on
  a Sol compute node and using it from a laptop browser. Picks
  between Open OnDemand (lowest friction for casual notebook use)
  and the manual `ssh -L … -J …` chain (terminal workflows). The
  Sol-specific catch the skill teaches: compute nodes are not
  internet-reachable, so the SSH chain has to ProxyJump (`-J`)
  through the login node.
- **File sharing** — coordinating the `chmod`/`install`/`cp` dance
  needed to share files between Sol users via `/scratch`.
- **Data transfer** — `rsync`/`scp` between laptop and Sol.
- **VS Code** — auto-activating shell environments via
  `terminal.integrated.env.linux`.

Cross-cutting conventions enforced throughout:

- Always substitute `$(whoami)` for the username before showing
  examples — never emit `<asurite>` or other angle-bracket
  placeholders.
- Never read `~/.ssh/config` or `~/.ssh/known_hosts`. If the user has
  a custom Host alias, they will tell you.
- Always preview destructive or long-running operations with
  `--dry-run` (or the equivalent) before executing.

For how this skill is verified, see
[`docs/coverage.md`](../../docs/coverage.md) in the source repo.

## Detecting the Environment

Three SLURM-side signals, cheapest first. Ignore `hostname` for
this — aliases and custom `~/.ssh/config` entries make it noisier
than necessary, and SLURM gives you cleaner answers.

1. **No SLURM client → not on Sol.** If `command -v sacctmgr`
   returns nothing, the machine doesn't have a SLURM client at all,
   so it can't be a Sol login or compute node. This is the cheapest
   and most decisive negative check — stop here on a laptop.
2. **Which SLURM cluster.** `sacctmgr -n show cluster format=cluster`
   prints `sol` on any Sol login or compute node. A different value
   means a different SLURM cluster (Phoenix, etc.); empty output
   means SLURM is installed but the client isn't talking to a
   controller. SLURM's own view of cluster identity is canonical.
3. **Login vs compute node within Sol.** `$SLURM_JOB_ID` is set
   inside any allocation (you're on a compute node — don't `srun` /
   `sbatch` from here, don't run heavy work outside what the
   allocation reserved); unset means you're on a login node (free to
   submit jobs, but don't run heavy compute on the login node
   itself).

## General Rules

1. Save datasets and caches under `/scratch`.
2. You do not have `sudo` privileges, so maintain a local environment under `/home/$USER/.local` or `/home/$USER/opt`.
3. Use `git` to keep code in sync between local and cluster.

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

#### Where to run it

A renewal is metadata-heavy I/O, not compute — but a touch pass over
tens of thousands of files is exactly the load Sol's **login nodes
throttle**. Check the environment first (see [Detecting the
Environment](#detecting-the-environment)), then branch:

- **On a compute node** (`$SLURM_JOB_ID` set) — run it directly; you
  already hold dedicated resources.
- **On a login node** (`$SLURM_JOB_ID` unset) — don't run the heavy
  pass here. Move it to one of, in rough order of convenience:
  - the **DTN**: `ssh soldtn '<cmd>'` (the `dtn` wrapper is literally
    `ssh soldtn`). It's tuned for I/O, isn't throttled, and has many
    cores — the best home for a large renewal.
  - a **compute node**: grab one with `interactive` and run it there.
  - a **batch job**: submit a short `htc` job whose payload is the
    renewal, for an unattended pass.

Match `-j` to where it actually runs: a 4-core compute node can't feed
more than a couple of workers, while the DTN has many. Because work is
sharded at the **file** level (the run enumerates kept directories,
then touches their files in batches across the pool), raising `-j`
speeds up even a single huge directory — not just a long list of small
ones. See [references/scratch.md](references/scratch.md) for the
non-interactive `uv`-on-`PATH` gotcha when invoking over `ssh soldtn`.

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

# Raise parallelism explicitly when running where the cores exist
$SKILL_DIR/scripts/sol_renew.py -j 16

# From a login node: run the heavy pass on the DTN instead (many cores,
# not throttled). Ensure ~/.local/bin is on PATH so the uv shebang resolves.
ssh soldtn 'export PATH=$HOME/.local/bin:$PATH; '"$SKILL_DIR"'/scripts/sol_renew.py -j 24'
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

A touch pass over many small files on a shared cluster filesystem can
take a long time, with no per-file output — progress is reported per
file-batch as each completes. Do not interpret a silent stretch as a
hang. A full pass over a large inactive list can legitimately take
tens of minutes; the tail is bounded by the largest single directory's
files divided across `-j` workers. Inspect the child `find`/`touch`
processes via `ps` if you need a liveness check.

### Sharing Files

See [references/sharing.md](references/sharing.md) for the
step-by-step procedure to share files with other users on the
cluster.

## Getting the Software You Need on Sol

Situation: you need a tool — a compiler, a Python interpreter, an R
package, a LaTeX distribution, a CLI — and the system `PATH` on Sol
either doesn't have it or has too old a version. You don't have
`sudo`. There are four non-sudo paths; pick the one that matches the
kind of software:

1. **Already on the cluster as a module.** Compilers, MPI stacks,
   Python distributions, R, CUDA, common applications — all live
   under the `module` system. No modules are loaded when a session
   starts, so `module load` them every session (or in every SBATCH
   script). See [references/module.md](references/module.md) for
   `avail` / `load` / `list` / `purge` and the naming schemes.

2. **Python — use `uv`.** The system `python3` on Sol is older than
   modern code expects. Don't fight it; use
   [`uv`](https://docs.astral.sh/uv/) to manage interpreters and
   environments instead.

   - Point `uv`'s cache at `/scratch` so it doesn't fill `/home`:

     ```shell
     export UV_CACHE_DIR=/scratch/$USER/.cache/uv
     ```
   - For one-file utility scripts, prefer the PEP 723 inline-metadata
     shebang `#!/usr/bin/env -S uv run --script` so the script
     self-bootstraps its interpreter and dependencies. The bundled
     `scripts/sol_renew.py` is the example.

3. **LaTeX — use R's `tinytex`.** Builds a per-user TeX Live tree
   under `~/.local/bin/latex`, no sudo:

   1. `module avail r-4` to find a current R, then `module load` it.
   2. Use the R package `tinytex` to install TeX Live locally.
   3. Install TeX packages on demand: `tlmgr install <pkg>`.
   4. If `tlmgr` complains "is older than remote repository", refresh
      the local TeX Live: load R, then
      `Rscript -e "tinytex::reinstall_tinytex(repository='illinois')"`.

4. **Anything else — install to `~/.local` or `~/opt`.** No `sudo`
   on Sol, so anything you build or download from source goes under
   your home directory. `~/.local/bin` should be on `PATH` by
   default; add it in `~/.bashrc` / `~/.zshrc` if not.

Across all four: never propose `sudo`. If a tool genuinely requires
root, file a ticket with ASU Research Computing rather than working
around it.

## Submitting Jobs

Sol uses **Slurm** to manage jobs. Submit work via SBATCH scripts or
the `interactive` wrapper for interactive shells.

**The `interactive` wrapper already defaults to `-p htc -q public
-c 1 -t 0-4`.** Bare `interactive` (no flags) gets you a 4-hour `htc`
shell — the right shape for most debug or "just need to check
something on a compute node" sessions. Override only when the
workload genuinely needs more (e.g., `interactive -p public -G a100:1`
for a GPU shell, `interactive -p public -c 16 --mem=64G` for heavy
CPU work).

**Match the partition to the workload size, not the request size.**
Sol's `htc` partition is the right home for short, lightweight,
debug-class work. Use `public` for real workloads that genuinely
need the larger nodes. If the user describes the work as "quick",
"debug", "lightweight", "just need to check", or specifies under an
hour with no GPU — that's an `htc` request. Don't default to
`public` in those cases: defaulting wastes capacity that someone
else is queued for.

**Don't write SBATCH scripts from scratch when a template fits.**
Sol ships ready-to-modify templates under
`/packages/public/sol-sbatch-templates/templates/` for serial, MPI
(`hpcx` / `intel` / `mpich` / `mvapich` / `openmpi` variants),
Python, Python multiprocessing, R, MATLAB, and rclone jobs. Start
from the closest match instead of inventing headers.

See [references/slurm.md](references/slurm.md) for submission
commands, example scripts (serial, MPI, job arrays),
troubleshooting, and exit codes; and
[references/sessions.md](references/sessions.md) for the
`interactive` wrapper variants by workload type (debug, public,
GPU).

## Asking the Cluster About Yourself and Your Jobs

Status questions ("what's queued?", "why is my job pending?", "what
accounts can I use?") almost always have a one-line SLURM answer.
**Prefer the SLURM command itself** — it's portable, stable, and the
same answer the wrappers compute. Reach for Sol's `my*` / `show*`
wrappers when their output formatting saves real work (`myjobs`,
`summary`), or when they encapsulate non-trivial calculation
(`myfairshare`).

| User question | Native SLURM | Sol wrapper (when useful) |
|---|---|---|
| What jobs do I have right now? | `squeue --me` | `myjobs` (priority/QOS/GPU columns), `sq` (sorted by priority), `summary` (state counts) |
| Tell me about job N | `scontrol show job N` | `thisjob N` adds a `squeue` row; `showjob N` also runs `seff` if the job finished |
| What's my historical job activity? | `sacct --user=$USER --starttime=YYYY-mm-dd` | `mysacct` (preset format) |
| What accounts and QOS can I submit under? | `sacctmgr -s show user $USER format=User,DefaultAccount,Account,QOS` | `myaccounts` (same call, shorter to type) |
| What's my fairshare / scheduling priority? | — | `myfairshare` |
| Why is my job stuck pending? | `squeue --me -t PD -O Reason` | `showlimited` (cluster-wide capacity holds by group/QOS) |
| Which partitions have free capacity? | `sinfo` (or `sinfo --Format=...`) | `showparts` (color-coded availability) |
| Which GPU nodes have free GPUs? | `scontrol show nodes` (parse `Gres` / `AllocTRES`) | `showgpus` (color-coded per-node) |
| How efficient was a finished job? | `seff <jobid>` | (no wrapper) |

## Using a Service That Runs on Sol, From Your Laptop

The canonical version of this situation: the user wants a Jupyter
notebook running on a Sol GPU and wants to open it in their laptop
browser. Same shape covers dev servers, MLflow UIs, TensorBoard,
OAuth callbacks — anything that listens on a localhost port on Sol
and that the user wants to reach from their own machine.

SSH port-forwarding is the underlying mechanism, but it isn't the
interesting part — what this skill actually adds is *which path is
right for the situation*. There are two:

1. **Open OnDemand — lowest friction for casual notebook use.** ASU
   Research Computing runs an OnDemand portal that launches Jupyter
   (and other interactive apps) on compute nodes through a web UI:
   no SSH, no tunnels, no terminal. For a one-off browser-based
   notebook on a GPU, this is the right answer. Find the portal URL
   on the official Sol docs (<https://docs.rc.asu.edu/>); confirm
   there or with the user rather than guessing.

2. **Manual SSH tunnel chain — always works, slightly fiddly.** Use
   this when the user needs a terminal-driven workflow, custom env
   vars in the compute-node shell, multiple ports forwarded,
   long-running scripts that wrap the allocation, or anything else
   OnDemand's UI doesn't expose. The Sol-specific catch: compute
   nodes are not internet-reachable, so the laptop can't ssh them
   directly — the chain has to ProxyJump (`-J`) through the login
   node. See [references/sessions.md](references/sessions.md) for
   the worked steps, multi-port stacking, OAuth reverse tunnels,
   and diagnostics.

**Personalize.** Substitute `whoami` into every command you show.
Never emit `<asurite>`. Don't read `~/.ssh/config` or
`~/.ssh/known_hosts` — if the user has a custom `Host` alias they
will tell you.

## Transferring Data

For routine transfers, `rsync -avz` between laptop and Sol's login
node is fine:

```shell
rsync -avz ./local_dir/ $USER@sol.asu.edu:/scratch/$USER/remote_dir/
```

**For large transfers, route through the Data Transfer Node.** Sol
has a dedicated DTN (`soldtn`) tuned for I/O-heavy transfers; pushing
multi-GB datasets through the login node is slower and risks tripping
login-node CPU/IO limits. From a Sol shell, `dtn` (literally
`ssh soldtn`) drops you onto it; from a laptop, target
`soldtn.asu.edu` directly:

```shell
rsync -avz --progress ./big_data/ $USER@soldtn.asu.edu:/scratch/$USER/data/
```

For very large transfers prefer `rsync --progress` (visibility) or
`scp -r` (when you don't need rsync's incremental update logic).

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

## Disclaimer

This is a **personal toolkit**, not an official ASU Research Computing
product. It is published in the hope that other Sol users find it
useful, with the following caveats:

- **Not affiliated with ASU Research Computing.** This skill is not
  endorsed, reviewed, or maintained by ASU. The official Sol
  documentation at <https://docs.rc.asu.edu/> is authoritative on
  every policy referenced here (storage retention, partitions, QOS,
  module names, scratch CSV schema). When this skill and the official
  docs disagree, the official docs win — please file an issue so this
  skill can be updated.
- **Conventions go stale.** Sol is a live system: modules get
  renamed, partitions retire, the scratch deletion pipeline can
  change its CSV format. The cached notes in `references/` are
  snapshots from a particular point in time. Verify against
  <https://docs.rc.asu.edu/> if a command does not behave as
  documented here.
- **Use cautiously.** The agent will sometimes propose commands that
  modify files, submit jobs, or open network connections. Always
  preview with `--dry-run` (or the equivalent for the tool in
  question) before executing, and review what the agent proposes
  before approving.
- **Limited test coverage.** This skill is exercised against a
  layered eval harness, but not every behavior is automatically
  verified — see [`docs/coverage.md`](../../docs/coverage.md) for the
  current matrix and known gaps.
- **No warranty.** The skill and its accompanying scripts are
  provided as-is. Review the code before running it on data you care
  about.

If you are unsure whether the agent's proposed action is appropriate
for your account or project on Sol, contact ASU Research Computing
directly rather than acting on the suggestion.
