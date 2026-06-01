# `solx` — user manual

`solx` is a Sol-first command-line tool for daily work on ASU's
[Sol supercomputer](https://docs.rc.asu.edu/). You SSH to Sol yourself, then
run `solx` from a login or compute node. It wraps the handful of Slurm
operations a terminal-driven user actually repeats — list jobs, request an
interactive allocation, attach a shell, cancel, check remaining time — plus
renewing `/scratch` files Sol has flagged for deletion.

It is built to be driven **both by a human at a terminal and by an AI agent**
in a non-interactive session (see [Output & agents](#output--agents)).

- Install & config: [`solx/README.md`](../solx/README.md)
- Roadmap & design decisions: [`PLAN.md`](PLAN.md)
- This file: what every command does, the defaults, and the conventions.

---

## Command surface at a glance

`solx` is a flat-ish CLI: noun-verb subgroups for related operations, and a
couple of top-level shortcuts where they earn it.

| Command | What it does | Destructive? |
|---|---|---|
| `solx init [-f]` | Write a starter `config.toml` (refuses to overwrite without `-f`). | writes a file |
| `solx job list` (alias `ls`) | List your Sol jobs. | no |
| `solx job start [TEMPLATE] [-n] [--timeout T] [-- …]` | Request an interactive allocation via `salloc --no-shell`. | starts a job |
| `solx job stop [JOBID] [-y] [-n]` | Cancel a job (`scancel`). | **yes** |
| `solx job jump [JOBID] [-q]` | Drop into a shell on the compute node (`srun --pty`). Also `solx jump`. | no |
| `solx job time [JOBID]` | Print remaining walltime (`D-HH:MM:SS`). | no |
| `solx keep [--stage S] [--csv-dir D] [-j N] [-y] [-n] [-v]` | Renew CSV-flagged scratch files filtered by `[keep]`. | **yes** |
| `solx config show [--json]` | Print the resolved config. | no |
| `solx config edit` | Open `config.toml` in `$EDITOR`. | no |
| `solx completions <bash\|zsh\|fish>` | Emit a shell-completion script. | no |
| `solx --version`, `--help` | — | no |

**Global output flags** (place *before* the subcommand): `--json` forces
machine-readable output, `--plain` forces human output. With neither, output
auto-detects (see below).

### Aliases

- The `job` subgroup is also reachable as `jobs` — `solx job list` ≡ `solx jobs list`.
- `list` is also `ls`.
- `solx jump` is shorthand for `solx job jump` — the verb you reach for most
  earns the top-level slot.

---

## A normal session

```shell
solx init                  # one-time: write ~/.config/solx/config.toml
solx config edit           # tune templates + [keep] paths
solx job start debug       # request an interactive allocation; prints the jobid
solx job list              # see it (RUNNING)
solx job time              # how much time is left
solx job jump              # attach a shell on the compute node
# … do work …
exit                       # back to the login node; the allocation stays alive
solx job stop              # cancel it (prompts; -y to skip)
solx keep --dry-run        # preview which scratch files would be renewed
solx keep                  # renew them (prompts)
```

---

## `job start` — request an allocation

```shell
solx job start              # uses default_template
solx job start gpu          # a named [jobs.gpu] template
solx job start gpu -n       # dry-run: print the salloc argv, submit nothing
solx job start gpu -- --mem=128G --time=8:00:00   # tail overrides the template
```

Under the hood it builds `salloc --no-shell -J solx-<template> -p … -t … …`
from your template, runs it, and **blocks until the queue grants the
allocation** (no polling). It parses the granted jobid from salloc's stderr
and returns; the allocation keeps running headless until you `solx job jump`
into it. `start_timeout` (config; `--timeout` overrides) caps the wait so a
stuck queue surfaces instead of hanging.

Anything after `--` is appended verbatim to `salloc` after the template's
`extra_args`; Slurm's last-flag-wins lets the tail override template defaults
for one run.

`job start` is for **interactive** allocations only. For real batch work, run
`sbatch your-script.sbatch` directly — `solx` deliberately doesn't wrap it.

---

## Job-id resolution — the defaults for `stop` / `jump` / `time`

The most-used convenience is *not having to type a jobid*. When you omit
`[JOBID]`, resolution is **verb-aware**. The conventions take inspiration from
`tmux` (a no-arg command acts on the obvious target; pick the most recent when
several exist; warn when you act on the session you're sitting in) but are
**adapted to Slurm** — cancelling a job is irreversible and attaching spends
real allocation time, so the verbs deliberately differ.

Shared first steps for every verb:

1. **An explicit `JOBID` argument always wins** (no `squeue` call).
2. Else if **`$SLURM_JOB_ID` is set** — you're *inside* an allocation on a
   compute node — that job is the target ("the current session").
3. Else `solx` queries `squeue -u $USER` and applies the per-verb policy below.

### `solx job time [JOBID]` — read-only, most permissive

| Situation (no arg) | Behavior |
|---|---|
| Inside an allocation | Use `$SLURM_JOB_ID`. No warning — reading your own job's walltime is the common case and must pipe cleanly. |
| 0 jobs | Exit 1, "no jobs found". |
| 1 job | Use it. |
| ≥2 jobs | **Auto-pick the most recent** (read-only is safe), note it to stderr. |

### `solx job jump [JOBID]` — attach, RUNNING-only

Only **RUNNING** jobs are attach candidates (you can't attach to a pending
allocation).

| Situation (no arg) | Behavior |
|---|---|
| Inside an allocation | Attach to `$SLURM_JOB_ID`, but **warn about nesting** — opening a nested `srun` step burns extra resources. It still proceeds (attach is non-destructive and Ctrl-D-recoverable); `-q/--quiet` silences the heads-up. |
| 0 running (maybe pending) | Exit 1, "no running job to attach to". |
| 1 running | Use it. |
| ≥2 running | **Auto-pick the most recent**, note it to stderr, attach. |

`jump` never errors on multiplicity — most-recent makes it decisive.

### `solx job stop [JOBID]` — cancel, never guesses

Cancelling the wrong job is unrecoverable, so `stop` is the deliberate
**divergence from "act on the most recent"**: it never auto-picks among
several distinct jobs.

| Situation (no arg) | Behavior |
|---|---|
| Inside an allocation | Target `$SLURM_JOB_ID` — but this is the job you're *inside*, so cancelling ends your session. You get a strengthened confirm: `Cancel job N (the one you're inside)?` |
| 0 jobs | Exit 1, "no jobs found". |
| 1 job | Use it (still prompts unless `-y`). |
| ≥2 jobs | **Print the candidates and exit 2** — "specify a JOBID". Never guesses. |

### "Most recent"

`solx` defines the most recent job as the one with the **highest job id**.
Slurm assigns ids monotonically, so the highest id is the newest submission —
which, for an allocation you just made with `solx job start`, is the one you
mean. (A timestamp-based definition using `squeue`'s `START_TIME` was
considered; job-id was chosen for determinism and zero timestamp-parsing — it
never misfires on a locale or a malformed `%S`.)

---

## Destructive-command confirmation contract

`solx job stop` and `solx keep` mutate state — cancel an allocation, or
`touch` mtimes under `/scratch`. Both follow:

| Flag | Behavior |
|---|---|
| (none) | Print what's about to happen, then prompt `… ? [y/N]` (default no). |
| `-y`, `--yes` | Skip the prompt and execute. For scripts. |
| `-n`, `--dry-run` | Print the plan and execute nothing. **No prompt.** |

- `-y` and `-n` together → exit 2 (mutually exclusive).
- **Non-interactive (no TTY on stdin) without `-y`/`-n` → refuse, exit 2**
  rather than hang on a prompt. The error tells you which flag to pass. This
  is the rule that makes `solx` safe to drive from an agent or a cron.

The other commands don't prompt (`init` has its own overwrite prompt).

---

## Output & agents

`solx` is designed so an **agent never has to know a flag exists** to get
parseable output (the principle behind
[issue #16](https://github.com/Shu-Wan/sol-skills/issues/16)):

- **Auto-detect**: when **stdout is not a TTY** (piped, captured, or run by an
  agent), data commands emit **JSON**; on a terminal they render Rich tables.
- **Force it**: the global `--json` / `--plain` flags override the
  auto-detect. They are *global*, so they go **before** the subcommand:
  `solx --json job list`, `solx --plain config show`. (`config show` also
  keeps a **local** `--json` — `solx config show --json` — as a documented
  convenience carried over from v0.2.x; it's the one command where the flag
  works in either position, and the local flag wins if both are given.)
- **Stream discipline**: the data result goes to **stdout** (clean JSON or the
  bare value, e.g. `solx job time` prints just the duration). All progress,
  notes, warnings, and errors go to **stderr**. So `solx job time` pipes a
  clean `D-HH:MM:SS`, and `solx job list | jq …` just works.
- **Exit codes** an agent can branch on:
  - `0` — success (including an auto-picked target; the note is on stderr).
  - `1` — operational failure or nothing to act on (no jobs, none running,
    Slurm error, interactive abort).
  - `2` — *under-specified or unconfirmed*: `stop` with ≥2 jobs and no jobid;
    a destructive command in a non-interactive session without `-y`/`-n`;
    `--json --plain` or `-y -n` together; or running off-Sol. The fix is
    always "re-invoke with a concrete jobid and/or `-y`".

Example agent-facing shapes:

```jsonc
// solx --json job list
[{ "job_id": "12345", "name": "solx-debug", "state": "RUNNING",
   "time_used": "0:05", "time_left": "0:55", "partition": "htc",
   "node_list": "sg045" }]

// solx --json job time
{ "jobid": "12345", "time_left": "00:54:37" }

// solx --json job stop          (≥2 jobs, ambiguous)  -> exit 2
{ "error": "multiple jobs running", "jobs": [ … ] }

// solx --json keep -n   (counts are exact; the lists are a capped sample)
{ "dry_run": true, "csv_dir": "/home/asurite", "stages": ["pending", …],
  "kept_count": 11134, "skipped_count": 0,
  "kept_truncated": true, "skipped_truncated": false,
  "kept": [ /* ≤100 */ ], "skipped": [ … ],
  "full_plan_path": "/tmp/solx-keep-plan-ab12cd.json" }
```

Sol's warning CSVs can flag thousands of directories, so `keep`'s JSON
**bounds its output**: exact `*_count` totals plus a capped (≤100) inline
sample with a `*_truncated` flag, rather than a multi-megabyte document. When
a list is truncated, the **complete** plan is spilled to a temp file and its
path returned as `full_plan_path` — so the response stays small while the full
detail is one `cat`/`jq` away (also printed to stderr in human mode).

---

## `keep` — renew flagged scratch files

`solx keep` renews `/scratch` files Sol has flagged for deletion. It is a port
of the skill's `sol_renew.py` (same mechanism), with the keep-list moved into
your config's `[keep]` block instead of a separate `~/.solkeep` file.

1. Reads Sol's warning CSVs from `--csv-dir` (default `$HOME`):
   `scratch-dirs-pending-removal.csv`, `scratch-dirs-over-90days.csv`,
   `scratch-dirs-inactive.csv`.
2. Filters the flagged directories through `[keep]` `include`/`exclude`
   (gitignore-style globs via `pathspec`).
3. Runs `touch -a -m -c` on the intersection — only directories that **both**
   appear in a CSV **and** match `[keep]`. It never walks `/scratch`
   wholesale, so it cannot be used to defeat the retention policy: there is
   nothing to do until Sol drops a warning CSV.

**Execution is file-level sharded** (mirrors `sol_renew.py` PR #18): a bounded
streaming pipeline over one worker pool enumerates a kept directory, splits
its files into batches, and `touch`es the batches across the pool. A single
huge directory fans out into many batches, so `-j` scales the parallelism of
the whole run — including its largest directory — not just the count of
directories. Enumeration prefers `fd` (or `rg`) when on `PATH` (both walk a
tree multithreaded) and falls back to `find`.

This is **metadata-heavy NFS I/O**. On Sol run it on a compute node or the DTN
(`ssh soldtn`), not a throttled login node.

| Flag | Meaning |
|---|---|
| `--stage {pending,over90,inactive,all}` | Which CSVs to read. Default `all`. |
| `--csv-dir DIR` | Where Sol drops the CSVs. Default `$HOME`. |
| `-j N`, `--jobs N` | Parallel workers. NFS is the bottleneck, so the default is conservative. |
| `-y` / `-n` / `-v` | Confirm contract (above) + verbose plan. |

If there's no `[keep]` block in your config, `solx keep` exits 2 and tells you
to run `solx config edit`.

---

## Where `solx` does *not* go

- **Laptop-side composites.** `solx up/down/forward` (ssh-chain construction,
  tunnels) are intentionally absent — that design needs more thought. SSH to
  Sol manually for now.
- **`sbatch`.** `solx job start` is interactive-only; real batch jobs use
  `sbatch` directly.
- **Off-Sol.** Every subcommand exits 2 on a non-Sol host with a redirect
  message. There's no `solx` value off-cluster.
