# Stage 2 — Sol-only `solx` CLI

Sub-plan of [PLAN.md](PLAN.md). This stage builds the `solx` CLI as
a **Sol-first** tool: the user reaches Sol manually (per Stage 1's
`references/sessions.md`), then runs `solx` from a login or compute
node. All laptop-side work is deferred for further design discussion
([PLAN.md §"Pivot"](PLAN.md#pivot--what-changed-and-why)).

This stage does **not** touch `skills/sol-skill/`. The skill keeps
shipping `sol_renew.py` and `.solkeep`-driven scratch renewal
unchanged. From the skill's point of view, `solx` does not exist —
that integration is Stage 3 and is deferred until the user
greenlights it.

## Scope

In scope:

- `solx/` package — full rewrite of the existing Stage 2a code
  against the new command surface.
- `docs/solx.md` — user manual rewritten for the new surface.
- `docs/solx-smoke.md` — smoke checklist rewritten around `solx job
  start`.
- `solx/README.md` — minimal, links to `docs/solx.md`.

Out of scope:

- Anything under `skills/sol-skill/` — `SKILL.md`, `sol_renew.py`,
  `.solkeep` syntax, references — all stay as shipped in v0.2.1.
- Root `README.md` — primarily the skill's README, untouched.
- `solx skill install/remove/list/update` subcommands — verbs
  reserved for the eventual surface, **not implemented in this
  stage**. They return with Stage 3.
- Laptop-side everything — `solx up/down/forward/info`, `solx init`
  on a laptop, `~/.config/solx/laptop.toml`, ssh-chain
  construction, ControlMaster, `~/.ssh/*` reads. Deferred.

## Command surface

Noun-verb groups for related operations; flags for leaf commands.
Defaults wherever a sensible default exists.

**Aliases**:

- The `job` subgroup is also reachable as `jobs`, and `list` is also
  reachable as `ls`. So `solx job list`, `solx jobs list`, `solx job
  ls`, and `solx jobs ls` are all equivalent.
- `jump` is also exposed as a top-level shortcut — `solx jump
  [JOBID]` is identical to `solx job jump [JOBID]`. It's the verb
  used most often, so it earns the shortcut.

| Command | What it does | Underlying |
|---|---|---|
| `solx init` | First-run: write a starter `config.toml` | — |
| `solx job list` (alias `ls`) | Print my Sol jobs as a Rich table | `squeue -u $USER` |
| `solx job start [TEMPLATE]` | Submit an interactive allocation, wait for `RUNNING`, print the jobid | `salloc --no-shell` (Slurm ≥ 22.05; Sol runs 25.x) |
| `solx job stop [JOBID]` | Cancel a job | `scancel` |
| `solx job jump [JOBID]` (also `solx jump`) | Drop into `default_shell` on the job's compute node | `srun --jobid=… --pty $shell` |
| `solx job time [JOBID]` | Print time remaining in Slurm `D-HH:MM:SS` format | `squeue -h -j … -O TimeLeft` |
| `solx keep [--stage S] [--csv-dir DIR] [-j N] [-n] [-v]` | Renew CSV-flagged scratch files filtered by `[keep]` | `touch -a -m -c` |
| `solx config show [--json]` | Print resolved config | — |
| `solx config edit` | Open `config.toml` in `$EDITOR` | — |
| `solx completions <bash\|zsh\|fish>` | Emit shell completion script | Typer built-in |
| `solx --version`, `--help` | — | — |

`solx where` from Stage 2a is dropped — Sol-only is implicit; a
not-Sol guard fires from each subcommand instead. The `session`
subgroup, the `[shared]` config merge, and all laptop-side stubs are
removed.

### Default jobid resolution

When `[JOBID]` is omitted from `stop` / `jump` / `time`:

1. Argument given → use it.
2. Else `$SLURM_JOB_ID` set → use it (compute-node default).
3. Else query `squeue -u $USER`:
   - 0 jobs → exit 1, "no jobs found".
   - 1 job → use it.
   - ≥2 jobs → print a Rich table of all jobs, exit 2 with "specify
     a jobid: `solx job stop <jobid>`". Non-interactive on purpose
     — confirmation prompts make cancel-the-wrong-job too easy.

`solx job start` does not take `[JOBID]`; its argument is a
`[TEMPLATE]` name that defaults to `default_template` from config.

### Allocation mechanism

`solx job start` uses `salloc --no-shell` (Slurm 22.05+; Sol runs
25.x). salloc waits for the queue, then exits with the allocation
left running in the background. We parse the jobid from salloc's
stderr (`salloc: Granted job allocation N`) and return it.

The earlier prototype used `sbatch --parsable --wrap='sleep
infinity'`; we switch to `salloc --no-shell` because:

- Native Slurm primitive — no `sleep` workaround, no CPU time
  billed to a sleeper process, clean `seff` output.
- `--parsable` isn't needed because we capture the jobid from
  stderr rather than stdout.
- Same end state: a running allocation the user attaches to via
  `solx job jump` (which wraps `srun --jobid=… --pty $shell`).

The blocking-during-queue-wait behavior is intentional — `solx job
start` is meant to return only when the allocation is RUNNING and
ready for `solx job jump`. `start_timeout` (config, with
`--timeout` override) caps the wait so a stuck queue surfaces
instead of hanging.

## Configuration

Single file under `$XDG_CONFIG_HOME/solx/config.toml` (fallback
`~/.config/solx/config.toml`). Created mode 0600.

```toml
default_shell = "zsh"
default_template = "default"
start_timeout = "10m"          # cap on `job start` polling; --timeout overrides

[jobs.default]
partition = "lightwork"
time = "1-0"
qos = "public"

[jobs.debug]
partition = "htc"
time = "0-1"

# Scratch paths to keep alive when Sol flags them in a warning CSV
# *and* `solx keep` runs. Replace `sparky` with your ASURITE.
# Examples (uncomment + edit):
# [keep]
# include = ["/scratch/sparky/your-project", "/scratch/sparky/experiments/**"]
# exclude = ["**/__pycache__", "**/.venv"]
```

Schema:

| Key | Type | Required | Notes |
|---|---|---|---|
| `default_shell` | string | yes | Used by `solx job jump` when dropping into the compute node. |
| `default_template` | string | yes | Template name used when `solx job start` has no argument. |
| `start_timeout` | string (e.g. `"10m"`) | no, default `"10m"` | Polling cap for `job start`; `--timeout` flag overrides. |
| `[jobs.<name>]` | table | yes (≥1) | Job templates. Each table is a slurm flag set. |
| `[jobs.<name>].partition` | string | yes | `-p` |
| `[jobs.<name>].time` | string | yes | `-t` |
| `[jobs.<name>].qos` | string | no | `-q` |
| `[jobs.<name>].gres` | string | no | `--gres` |
| `[jobs.<name>].extra_args` | array of strings | no | Verbatim sbatch flags (e.g. `["--mem=64G", "--mail-type=END"]`). |
| `[keep]` | table | no | Scratch renewal config; absent = `solx keep` is a no-op. |
| `[keep].include` | array of glob strings | yes when `[keep]` present | Recursive globs (`**` supported via `pathspec`). |
| `[keep].exclude` | array of glob strings | no | Carve-outs from `include`. |

No `[shared]` merge. Each `[jobs.<name>]` is self-contained — repeat
flags across templates if needed. Simpler config is the trade.

CLI passthrough: anything after `--` on `solx job start` is appended
to the underlying sbatch command after `extra_args`. Sbatch's
last-flag-wins lets the tail override template defaults for one
run.

## Source layout (`solx/src/solx/`)

| File | Action | Notes |
|---|---|---|
| `__init__.py` | KEEP | Version constant. |
| `__main__.py` | KEEP | `python -m solx`. |
| `cli.py` | REWRITE | Typer root with `job` subgroup, top-level `keep`, `init`, `config` subgroup, `completions`. Drop `where`, `session` group, all laptop stubs. |
| `config.py` | REWRITE | Load `$XDG_CONFIG_HOME/solx/config.toml`. New schema (above). No `[shared]` merge. |
| `side.py` | KEEP, simplify | Internal Sol-vs-not-Sol guard; no longer a top-level command. |
| `slurm.py` | NEW | Thin `squeue`/`scancel`/`sbatch`/`srun` wrappers; `Job` dataclass; `resolve_jobid()` per the rules above. |
| `jobs.py` | NEW | `list`, `start`, `stop`, `jump`, `time` command bodies. |
| `keep.py` | NEW | Port of `sol_renew.py`. Reads Sol's warning CSVs from `--csv-dir`, intersects flagged paths with `[keep]` include/exclude (via `pathspec`), `touch -a -m -c` on the intersection. Mirrors `sol_renew.py`'s flag surface (`--stage`, `--csv-dir`, `-j`, `-n`, `-v`); drops `--solkeep` since `[keep]` lives in the main config. |
| `init.py` | NEW | First-run: write starter config (no `whoami` substitution; placeholders only). |
| `session.py` | DELETE | No more `session.json`; `squeue` is the source of truth. |
| `sol_cmds.py` | DELETE | Logic split across `jobs.py` / `keep.py` / `init.py`. |

`pyproject.toml` adds `pathspec` to runtime deps. Python ≥ 3.11
unchanged.

### Scratch renewal mechanism

`solx keep` is a port of the existing `sol_renew.py` script. The
mechanism is unchanged — only the keep-list source moves.

1. Reads Sol's warning CSVs from `--csv-dir` (default `$HOME`):
   - `scratch-dirs-pending-removal.csv` (most urgent)
   - `scratch-dirs-over-90days.csv`
   - `scratch-dirs-inactive.csv`
2. Filters the flagged directories through `[keep]` include/exclude
   from `config.toml` (via `pathspec`). Replaces `sol_renew.py`'s
   `~/.solkeep` filter — same matching semantics, lives in the main
   config now.
3. `touch -a -m -c` only the directories that **both** appear in a
   CSV and match `[keep]`. Never walks `/scratch` wholesale.

Flag surface mirrors `sol_renew.py`:

| Flag | Meaning |
|---|---|
| `--stage {pending,over90,inactive,all}` | Default `all`. Limits which CSVs are read. |
| `--csv-dir DIR` | Default `$HOME`. Where Sol drops the warning CSVs. |
| `-j N`, `--jobs N` | Default `min(8, ncpu//4)`. Parallel workers — NFS is the bottleneck so the default is conservative. |
| `-n`, `--dry-run` | Print the plan without touching anything. |
| `-v`, `--verbose` | Verbose plan + progress. |

Dropped vs `sol_renew.py`: `--solkeep PATH` (the keep list now lives
in `[keep]` in the main config, not a separate file).

This preserves `sol_renew.py`'s ethical posture: only touches files
Sol has explicitly flagged. `solx keep` cannot be used to bypass
the scratch-retention policy by keeping arbitrary files alive on a
cron — there's nothing to do until Sol drops a warning CSV.

## State tracking — none

`squeue -u $USER` is queried each invocation. No persistent state
file, no stale-session edge cases. Rationale: `squeue` is fast on a
login node, and "what jobs do I have" is a question Slurm already
answers authoritatively.

`start_timeout` exists as a failsafe so `solx job start` never hangs
forever on a stuck queue. Default 10 min; `--timeout` flag overrides
per-run.

## Success criteria

1. **Installs on Sol**: `uv tool install ./solx` succeeds; `solx
   --version` works in a fresh shell.
2. **Sol guard**: every subcommand exits 2 with a clear "solx is
   Sol-only — SSH to Sol first" message on a non-Sol host. No stack
   trace.
3. **Config round-trip**: `solx init` writes a starter config; `solx
   config show` prints it back; `solx config edit` opens it in
   `$EDITOR`.
4. **`solx job list`** prints a Rich table matching `squeue -u
   $USER` content (jobid, name, state, time, time-left, partition).
5. **`solx job start <template> --dry-run`** prints the literal
   sbatch argv without submitting; structure is snapshot-tested per
   template.
6. **`solx job start <template>`** lifecycle on `htc`/`debug`
   completes in <2 min: submit → poll → print jobid. `solx job
   shell` drops into `default_shell` on the compute node. `solx job
   time` prints remaining. `solx job stop` cancels.
7. **Default-jobid resolution**: on a compute node, `solx job time`
   (no arg) uses `$SLURM_JOB_ID`. On a login node with one job, no
   arg → that job. With ≥2 jobs, no arg → table + exit 2.
8. **`solx keep`** mirrors `sol_renew.py`: reads Sol's warning CSVs
   from `--csv-dir`, intersects flagged dirs with `[keep]`
   include/exclude, `touch -a -m -c` on the intersection. Flag
   surface (`--stage`, `--csv-dir`, `-j`, `-n`, `-v`) matches
   `sol_renew.py` verbatim except the dropped `--solkeep`. With
   `--dry-run`, prints the plan and touches nothing.
9. **No `[keep]` block** → `solx keep` exits 2 with "no `[keep]`
   block in config; run `solx config edit` to add one".
10. **`solx completions zsh`** emits a script that, when sourced,
    autocompletes subcommands and template names.
11. **Alias coverage**: `solx jobs list`, `solx jobs ls`, `solx job
    ls`, and `solx jump` all dispatch to the right command (covered
    by `typer.testing.CliRunner` tests).
12. **Tests pass**: `cd solx && uv run pytest -v` is green.
13. **Skill untouched**: `git diff main..HEAD -- skills/` is empty.

## Testing

### Unit tests (run anywhere — no Sol required)

```shell
cd solx && uv run pytest -v
```

Coverage targets:

- **Config parsing**: required keys, type errors, missing
  `default_template`, `[keep]` include/exclude merge, glob
  compilation via `pathspec`.
- **Side detection**: faked `hostname -a` outputs for Sol login,
  Sol compute, not-Sol. All three branches.
- **Argv construction**: snapshot-test the rendered `sbatch` argv
  per template × `--dry-run` × `-- passthrough` combinations.
- **Default jobid resolution**: arg / env / single-job / multi-job
  branches with mocked `squeue`.
- **`keep` glob matching**: include∖exclude correctness on a synthetic
  scratch tree fixture.
- **`solx job list`** rendering against a faked `squeue` output
  (1 job, multiple jobs, no jobs, queued vs running).
- **CLI dispatch**: every subcommand wired correctly via
  `typer.testing.CliRunner`.

### Manual smoke on Sol (after `ssh sparky@sol.asu.edu`, with your ASURITE)

Tight on purpose — `htc`/`debug` queues in seconds.

1. Install: `uv tool install ./solx` from a checkout, or
   `uv tool install git+...#subdirectory=solx`. `solx --version`.
2. `solx init` — writes starter config; `solx config show` prints
   it.
3. `$EDITOR ~/.config/solx/config.toml` — fill in a real `[keep]`
   include path you actually own.
4. `solx job start debug --dry-run` — prints sbatch argv.
5. `solx job start debug` — submits, polls, prints jobid in <30s.
6. `solx job list` — table includes the new job, state RUNNING.
7. `solx job time` — prints remaining (no arg, login node, one job
   → uses it).
8. `solx job jump` — drops into `default_shell` on the compute
   node. `exit` returns to login.
9. `solx job stop` — `scancel`s; `solx job list` shows the job
   gone.
10. `solx keep --dry-run` — prints paths that would be touched.
11. `solx keep` — touches mtimes; `stat` on a sample file shows
    updated mtime.
12. Wrong-side guard: same commands on a laptop → all exit 2 with
    the redirect message. `solx --version` and `solx --help` work
    anywhere.

## Sequencing within Stage 2

PR #1 (this branch, `docs/sol-first-pivot`): docs only — `PLAN.md`,
`stage-2-solx.md`, `stage-3-integration.md`. Locks the contract.

PR #2 (`feat/sol-first-cli`, future): `solx/` rewrite + `docs/solx.md`
+ `docs/solx-smoke.md` + `solx/README.md`, against this contract.
Smoke on Sol before merge. Skill files untouched.

PR #5 (the existing Stage 2a PR, `feat/stage-2a-solx-sol-only`): left
open during PR #1 and PR #2; closed after PR #2 lands, with a comment
pointing at the new direction.
