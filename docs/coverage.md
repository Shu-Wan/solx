# Test coverage — sol-skill

What this skill is verified to do, what's documented without
automated verification, and what's a known gap. The eval harness
requires manual orchestration today, so this document is updated by
hand before each release.

**Version:** v0.4.0 (see [`../CHANGELOG.md`](../CHANGELOG.md))
**Last verified:** v0.4.0 restructured the skill around `solx`. The
`solx` CLI is covered by its own unit suite (`solx/tests/`, 187 tests
passing) including an end-to-end real-touch renewal test; the
skill-level L1/L2/L3 evals for the new `solx`-driven flows are **pending
re-run on Sol** and are marked 🟡 below. Rows for unchanged behaviors
carry over from the v0.3.0 verification (2026-05-28).

## Status legend

A traffic-light system. The emoji carries the signal so the table
scans visually; the label after gives context.

| Status | Meaning |
|---|---|
| 🟢 tested | Covered by the eval harness and currently passing for this release |
| 🟡 documented | Described in the skill; no automated test yet (works in routine use, not formally probed) |
| 🔴 gap | Known limitation — the skill does not cover this case |
| ⚪ roadmap | Planned for a later release; not promised by this version |

## Coverage by skill section

Sections below mirror the structure of
[`../skills/sol-skill/SKILL.md`](../skills/sol-skill/SKILL.md) so
each behavior sits next to where it's described. Adding a behavior
to the skill should mean adding a row here in the same group.

### Cross-cutting conventions

| Behavior | Status | Notes |
|---|---|---|
| Substitutes real `whoami` in examples; never emits `<asurite>` | 🟢 tested | Verified across all 3 iter-2/iter-4 evals |
| Refuses to read `~/.ssh/config` or `~/.ssh/known_hosts` | 🟡 documented | Negative assertion; not yet probed |
| Always previews destructive or long-running ops with `--dry-run` | 🟡 documented | |
| Never proposes `sudo` | 🟡 documented | Cross-cutting negative assertion |

### `solx` — install it first

| Behavior | Status | Notes |
|---|---|---|
| Detects `solx` (`command -v solx`) and prompts to install when missing | 🟡 documented | New in v0.4.0; skill eval pending |
| Prefers `solx` for job/scratch work; raw Slurm only as a fallback | 🟡 documented | New in v0.4.0; skill eval pending |
| `solx` exits 2 off-Sol (wrong-side guard) | 🟢 tested | `solx/tests/` (`require_sol` / `side`) |
| Drives the `solx job` lifecycle (start/list/time/jump/stop) | 🟢 tested (CLI) | `solx/tests/test_jobs.py`; skill-teaching eval pending |
| Verb-aware job-id resolution (most-recent for time/jump; stop refuses to guess) | 🟢 tested | `solx/tests/test_slurm.py`, `test_jobs.py` |
| Destructive-confirm contract (`-y`/`-n`, non-interactive refuse, exit 2) | 🟢 tested | `solx/tests/test_jobs.py`, `test_keep.py` |
| Agent output: JSON off a TTY, results on stdout / diagnostics on stderr | 🟢 tested | `solx/tests/test_output.py`, `test_jobs.py`, `test_keep.py` |

### Detecting the Environment

| Behavior | Status | Notes |
|---|---|---|
| Distinguishes laptop from Sol via `command -v sacctmgr` (cheap negative check) | 🟢 tested | Verified iter-5 P1 |
| Uses `sacctmgr -n show cluster format=cluster` for canonical cluster identity | 🟢 tested | Verified iter-5 P1 |
| Distinguishes Sol login node from compute node via `$SLURM_JOB_ID` | 🟢 tested | Verified iter-5 P1 |

### Filesystem and Storage

| Behavior | Status | Notes |
|---|---|---|
| Recommends `/scratch/$USER` for datasets, caches, model weights | 🟢 tested | Verified iter-1: agent recommends `/scratch/$USER` for HF cache |
| Steers away from `/home` for large data | 🟢 tested | Verified iter-1 |
| `.solkeep` syntax (gitignore-style, `!` negation, `**` glob) | 🟢 tested | Verified iter-2 eval A: agent produces correct file with explanation |
| Refuses to bulk-touch `/scratch` (`find -exec touch`) | 🟡 documented | Negative assertion; not yet probed |
| `solx keep --dry-run` plan correctness | 🟢 tested | `solx/tests/test_keep.py`: dry-run plans without touching; JSON plan bounded |
| `solx keep` refreshes kept files (recursively) | 🟢 tested | `solx/tests/test_keep.py::test_keep_end_to_end_real_touch`: mtimes refresh across the tree |
| keep-list carve-outs honored at run time (`.venv`/`__pycache__` skipped, non-kept dirs skipped) | 🟢 tested | `solx/tests/test_keep.py` (end-to-end + `build_plan`) |
| `solx keep` warns but still works on a legacy `~/.solkeep` (support removed 0.5.0) | 🟢 tested | `solx/tests/test_keep.py::test_keep_solkeep_fallback_warns_deprecated` |
| `solx config import-solkeep` migrates `~/.solkeep` → `[keep]` | 🟢 tested | `solx/tests/test_init.py::test_import_solkeep_*` |
| File sharing procedure (`chmod` / `install` / `cp` between users) | 🟡 documented | |
| Scratch-quota-exceeded behavior | 🔴 gap | Would need a fault-injection mock |
| Concurrent `solx keep` runs | 🔴 gap | No locking; documented behavior is "don't" |

### Getting the Software You Need on Sol

| Behavior | Status | Notes |
|---|---|---|
| `module` commands (`avail`, `load`, `list`, `purge`, `ml`) | 🟡 documented | Mock `module` records args; eval pending |
| Python via `uv` with `UV_CACHE_DIR=/scratch` | 🟡 documented | Mentioned iter-1 alongside HF cache; not directly probed |
| PEP 723 inline-metadata shebang for self-bootstrapping scripts | 🟡 documented | Static review only |
| LaTeX via R `tinytex` (per-user TeX Live under `~/.local`) | 🟡 documented | |
| `tinytex` package install / `tlmgr` repo update path | 🔴 gap | Documented in skill, no automated eval |
| Install to `~/.local` / `~/opt` for everything else | 🟡 documented | |

### Submitting Jobs

| Behavior | Status | Notes |
|---|---|---|
| Picks `interactive` wrapper for interactive shells over raw `salloc` | 🟢 tested | Verified iter-4 eval B |
| Knows `interactive` defaults to `-p htc -q public -c 1 -t 0-4` (bare invocation works) | 🟡 documented | Added after reading `/usr/local/bin/interactive` source |
| Routes "lightweight / debug / quick" workloads to `htc` partition | 🟢 tested | Verified iter-4 eval B (rule promoted to SKILL.md from references) |
| Recommends `/packages/public/sol-sbatch-templates/` over writing SBATCH from scratch | 🟡 documented | Iter-5 P5: agent acknowledged templates exist but didn't name the specific subdir; skill gap to sharpen |
| SBATCH header generation (partition, QOS, time, GPU) | 🟢 tested | Verified iter-5 P5: complete OpenMPI script with correct partition/QOS, `srun --mpi=pmix`, `--export=NONE`, `/scratch` logs |
| Job lifecycle: `sbatch`, `squeue`, `scancel`, `scontrol update` | 🟡 documented | |
| Multi-node MPI (`srun --mpi=pmix`) suggestions | 🔴 gap | Not exercised end-to-end |

### Situation-Aware Job Management

| Behavior | Status | Notes |
|---|---|---|
| Checks `myfairshare` before submitting; backs off below ~0.05 (no scheduler spam) | 🟡 documented | New in v0.4.0 (issue #9); skill eval pending. `myfairshare` lookup itself 🟢 (iter-5 P3) |
| Tracks remaining wall-time (`solx job time` / `squeue -O TimeLeft`) and wraps up / hands off before expiry | 🟡 documented | New in v0.4.0 (issue #9); skill eval pending |
| Uses Sol wrappers directly (`myfairshare`/`myjobs`/`seff`/`showgpus`/…) rather than wrapping them | 🟢 tested | Status-query rows below verified iter-5 P2–P4 |

### Asking the Cluster About Yourself and Your Jobs

| Behavior | Status | Notes |
|---|---|---|
| "What jobs do I have?" → `squeue --me` (or `myjobs` / `sq` / `summary`) | 🟢 tested | Verified iter-5 P2 |
| "Tell me about job N" → `scontrol show job N` (or `thisjob` / `showjob`) | 🟢 tested | Verified iter-5 P2 |
| "Past jobs?" → `sacct --user=$USER --starttime=…` (or `mysacct`) | 🟢 tested | Verified iter-5 P2 |
| "What accounts/QOS can I use?" → `sacctmgr -s show user $USER` (or `myaccounts`) | 🟢 tested | Verified iter-5 P3 |
| "What's my fairshare?" → `myfairshare` | 🟢 tested | Verified iter-5 P3; wrapper does the priority math (live `DampeningFactor`) |
| "Why is my job pending?" → `squeue --me -t PD -O Reason` (and `showlimited` for cluster-wide holds) | 🟢 tested | Verified iter-5 P2 |
| "Which partitions have free capacity?" → `sinfo` (or `showparts`) | 🟢 tested | Verified iter-5 P4 |
| "Which GPU nodes are free?" → `scontrol show nodes` (or `showgpus`) | 🟢 tested | Verified iter-5 P4 |
| "How efficient was my job?" → `seff <jobid>` | 🟢 tested | Verified iter-5 P2 |

### Using a Service That Runs on Sol, From Your Laptop

| Behavior | Status | Notes |
|---|---|---|
| Recommends Open OnDemand for casual GPU / Jupyter use | 🟢 tested | Verified iter-4 eval C: agent leads with OnDemand |
| Builds correct `ssh -L … -J …` ProxyJump chain | 🟢 tested | Verified iter-4 eval C (compute nodes aren't internet-reachable, so `-J` is mandatory) |
| Binds compute-node services to `127.0.0.1`, not `0.0.0.0` | 🟡 documented | |
| Multi-port forwarding (stacked `-L`) | 🟡 documented | |
| OAuth callback reverse tunnel (`-R`) | 🟡 documented | |
| Tunnel diagnostics (port-in-use, ControlMaster, wrong-side `-L`) | 🟡 documented | |
| Laptop-side one-command (`solx up`/`down`, ssh-chain construction) | ⚪ roadmap | Deferred; `solx` is Sol-only today — see [`ROADMAP.md`](ROADMAP.md) |
| VS Code wrapper (`/usr/local/bin/vscode`) integration | 🔴 gap | Manual smoke only; wrapper itself maintained by ASU |

### Transferring Data

| Behavior | Status | Notes |
|---|---|---|
| `rsync` / `scp` between laptop and Sol login node | 🟡 documented | Text-only verification |
| Routes large transfers through `soldtn` (Sol Data Transfer Node) | 🟢 tested | Verified iter-5 P6: agent uses `soldtn.asu.edu`, `--progress --partial`, dry-run advice, compression tradeoff |

### Working with VS Code

| Behavior | Status | Notes |
|---|---|---|
| Auto-activates shell envs via `terminal.integrated.env.linux` | 🟡 documented | |

### Out of scope

| Behavior | Status | Notes |
|---|---|---|
| Behavior on non-Sol HPC clusters (Phoenix, others) | 🔴 gap | Skill is scoped to Sol; near-miss prompts should *not* trigger this skill |

## How this is tested

See [`../DEVELOPMENT.md`](../DEVELOPMENT.md) for the harness, the
mock Sol environment, and the release process. Eval prompts and
per-run outputs live in a private workspace, not in this repo.
