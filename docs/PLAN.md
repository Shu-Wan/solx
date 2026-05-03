# Roadmap: Sol-first `solx` CLI

Forward-looking design doc for **`solx`**, a Python CLI for working
on ASU's **Sol** supercomputer. Not part of any released version
yet. The agent skill (shipped) covers the manual path; `solx` is an
additive convenience layer for terminal-driven work on Sol.

End-user docs: [`../README.md`](../README.md),
[`../skills/sol-skill/SKILL.md`](../skills/sol-skill/SKILL.md).
Contributor / harness docs: [`../DEVELOPMENT.md`](../DEVELOPMENT.md),
[`coverage.md`](coverage.md). Released history:
[`../CHANGELOG.md`](../CHANGELOG.md).

## Pivot — what changed and why

Earlier drafts of this plan envisioned a "one magic command from the
laptop" — `solx up <profile>` would SSH to Sol, allocate, open
tunnels, and drop the user into a shell, in one shot. That vision
threads laptop-side ssh-client behavior, ControlMaster, Duo, and
Slurm queue-wait races, none of which are unit-testable and all of
which need real laptop ↔ Sol round-trips to validate.

Rather than ship a brittle one-command flow, we are **deferring the
laptop side entirely** until the design is more mature. `solx` is
now a **Sol-first CLI**: the user reaches Sol the manual way (per the
already-shipped `references/sessions.md`), then runs `solx` from
there. Everything `solx` does — list jobs, start an interactive
allocation, drop into a shell on the compute node, cancel, query
remaining time, keep `/scratch` files alive — happens on Sol.

The laptop side is not cancelled, just **deferred for further
discussion**. It returns when the Sol-side primitives are stable,
the design has been re-thought from scratch, and the user has
greenlit it.

## Stages

| Stage | Outcome | Status |
|---|---|---|
| 1 — Skill manual-SSH path | Shipped in v0.2.0 (see CHANGELOG). | ✅ shipped |
| 2 — `solx` CLI (Sol-only) | `solx/` package, installable on Sol via `uv tool install`. Covers daily Sol use: jobs, interactive allocation, scratch renewal, config. See [`stage-2-solx.md`](stage-2-solx.md). | 🟡 in progress |
| 3 — Skill ↔ `solx` integration | Skill detects `solx` and teaches the CLI flow alongside the manual fallback. **Deferred until Stage 2 is mature and the user gives the greenlight.** See [`stage-3-integration.md`](stage-3-integration.md). | ⚪ deferred |

## Design principles

These are the load-bearing constraints for `solx`. Every decision
below derives from them.

1. **Sol-first.** The CLI is meant to be run *on* Sol after a manual
   SSH. No laptop side, no ssh-chain construction, no `~/.ssh/*`
   reads. If you want one-command magic from your laptop, that's a
   separate (deferred) conversation.
2. **Intuitive and not disruptive.** Verbs read like Sol-native
   commands. `solx job list`, `solx job start`, `solx keep`. No
   surprising side effects. Mutating commands support `--dry-run`.
3. **Common CLI conventions.** Noun-verb command groups (`solx job
   list/start/stop/shell/time`) for related operations; flags for
   leaf commands (`solx keep --dry-run`). Shell completions provided
   for bash, zsh, fish.
4. **Read config, don't infer.** A single TOML config under
   `$XDG_CONFIG_HOME/solx/config.toml` declares everything. No
   environment-variable trickery, no hidden discovery, no scanning
   `~/.ssh/*`.
5. **Slurm is the source of truth, not us.** No persistent
   `session.json` to go stale. The CLI queries `squeue` whenever it
   needs job state.
6. **General, not personal.** The starter config ships with
   placeholders, never with the maintainer's username baked in.

## Repo layout (target)

```text
sol-skill/
├── README.md                       # primary skill README, lightly touched
├── docs/
│   ├── PLAN.md                     # this file
│   ├── stage-2-solx.md             # Sol-only CLI sub-plan
│   ├── stage-3-integration.md      # deferred sub-plan
│   ├── solx.md                     # user manual (rewritten in PR #2)
│   ├── solx-smoke.md               # smoke checklist (rewritten in PR #2)
│   ├── coverage.md
│   └── name.md
├── skills/sol-skill/               # untouched — the skill ships with sol_renew.py
│   ├── SKILL.md
│   ├── scripts/sol_renew.py
│   └── references/
└── solx/                           # CLI package — rewritten in PR #2
    ├── pyproject.toml
    ├── README.md
    ├── src/solx/
    │   ├── __init__.py
    │   ├── __main__.py
    │   ├── cli.py                  # Typer root
    │   ├── config.py               # XDG TOML loader
    │   ├── side.py                 # Sol-vs-not-Sol guard
    │   ├── slurm.py                # squeue/scancel/sbatch wrappers; jobid resolution
    │   ├── jobs.py                 # `solx job *`
    │   ├── keep.py                 # `solx keep`
    │   └── init.py                 # `solx init`
    └── tests/
```

Distribution: **`uv tool install`** from the same repo (the CLI and
skill version together long-term, even though Stage 3 is deferred).

CLI stack: **Typer + Rich**. Defer Textual until a subcommand
genuinely needs TUI.

## What `solx` does, top level

| Command | What it does |
|---|---|
| `solx init` | First-run: write a starter `config.toml` |
| `solx job list` (alias `ls`) | List my Sol jobs |
| `solx job start [TEMPLATE]` | Start an interactive allocation (`salloc --no-shell`) from a config template |
| `solx job stop [JOBID]` | Cancel a job |
| `solx job jump [JOBID]` (also `solx jump`) | Drop into a shell on the job's compute node |
| `solx job time [JOBID]` | Remaining time (Slurm `D-HH:MM:SS`) |
| `solx keep [--stage S] [--csv-dir D] [-j N] [-n] [-v]` | Renew CSV-flagged scratch files filtered by `[keep]` (port of `sol_renew.py`) |
| `solx config show` / `edit` | Inspect / edit the single TOML config |
| `solx completions <shell>` | Emit shell completions |
| `solx --version` / `--help` | — |

Defaults and aliases:

- The `job` subgroup is also reachable as `jobs`, and `list` is also
  reachable as `ls`. `solx job list`, `solx jobs list`, `solx job ls`,
  and `solx jobs ls` are all equivalent.
- `jump` is also reachable at the top level — `solx jump [JOBID]` is
  shorthand for `solx job jump [JOBID]`. (It's the verb you reach for
  most; the shortcut earns its keep.)
- Anywhere a `[JOBID]` is omitted, `solx` resolves it: `$SLURM_JOB_ID`
  on a compute node, the user's only running job on a login node, or
  a Rich table of all jobs (with exit 2) when ambiguous.
- `solx job start` defaults to the `default_template` config key.
- `solx job time` prints in Slurm's `D-HH:MM:SS` format, matching
  `squeue -O TimeLeft`.

Full surface details, config schema, and the rewrite plan against
the existing `solx/` source tree live in
[`stage-2-solx.md`](stage-2-solx.md).

## Security model

`solx` is Sol-only by design, so the security surface is small:

- Never read `~/.ssh/*`. The CLI doesn't invoke `ssh` at all in this
  release.
- The single config (`$XDG_CONFIG_HOME/solx/config.toml`) is created
  with mode 0600.
- `solx keep` only touches files under directories the user has
  declared in `[keep]`. Mutates `atime`/`mtime` only — never reads,
  moves, or deletes content.
- Mutating commands (`job start`, `job stop`, `keep`) print the
  underlying Slurm/`touch` invocations they would run with
  `--dry-run` first.

When laptop-side work returns (deferred), a fresh security review of
that surface comes with it. Nothing in this stage commits us to a
specific laptop-side design.

## Decisions confirmed

- **CLI framework**: Typer + Rich. Defer Textual.
- **Config**: single TOML under `$XDG_CONFIG_HOME/solx/config.toml`.
  No multi-file split, no `[shared]` merge — one config, easy to
  read.
- **Glob library for `[keep]`**: `pathspec` (mature; handles
  `include` + `exclude` arrays similar to Ruff's config style).
- **State tracking**: none. `squeue -u $USER` is the source of truth.
  No `session.json`, no stale-state class of bugs. Cost: one `squeue`
  call per command — fine on a login node.
- **Default jobid resolution**: argument > `$SLURM_JOB_ID` (compute
  node) > sole running job (login node) > Rich table + exit 2 when
  ambiguous.
- **Repo layout**: same repo, CLI under `solx/`, skill under
  `skills/sol-skill/` — they ship together long-term but Stage 3
  integration is deferred. The skill currently makes no reference to
  `solx` and continues to teach `sol_renew.py` for scratch renewal.
- **`vscode` / `sbatch` wrappers**: out of scope. `solx` is for
  interactive jobs; for VSCode, run `code tunnel` directly on a
  compute node. For batch work, `sbatch your-script.sbatch` directly.
- **Skill subcommands** (`solx skill install/remove/...`): reserved
  in the eventual surface, **not implemented in Stage 2**. They
  return with Stage 3.

## What ships when

- **PR #1 (this branch)** — pivot the planning docs (`PLAN.md`,
  `stage-2-solx.md`, `stage-3-integration.md`). No code change. Locks
  the contract.
- **PR #2** — rewrite the `solx/` package against `stage-2-solx.md`,
  rewrite `docs/solx.md` and `docs/solx-smoke.md`, smoke-test on
  Sol. Skill files untouched.
- **PR #3+** — Stage 3 work, only after the user greenlights.
