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
| 2 — `solx` CLI (Sol-only) | `solx/` package, installable on Sol via `uv tool install`. Covers daily Sol use: jobs, interactive allocation, scratch renewal, config. Shipped as solx v0.3.0 (agent-friendly output, verb-aware job-id resolution, sharded `keep`). Behavior: [`solx.md`](solx.md). | ✅ shipped |
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
   list/start/stop/jump/time`) for related operations; flags for
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

## Command surface, config, and behavior → `solx.md`

The full command surface, config schema, job-id resolution, agent-output
contract, and the `keep` mechanism live in the user manual
[`solx.md`](solx.md) — the **single source of truth** for what `solx` does.
Contributor/architecture notes are in
[`../solx/DEVELOPMENT.md`](../solx/DEVELOPMENT.md). This roadmap stays focused
on *why* and *what's next*; it deliberately does not restate the API (so it
can't drift out of sync with the implementation).

## Security model

`solx` is Sol-only by design, so the security surface is small:

- Never read `~/.ssh/*`. The CLI doesn't invoke `ssh` at all in this
  release.
- The single config (`$XDG_CONFIG_HOME/solx/config.toml`) is created
  with mode 0600.
- `solx keep` only touches files under directories the user has
  declared in `[keep]`. Mutates `atime`/`mtime` only — never reads,
  moves, or deletes content.
- Destructive commands (`job stop`, `keep`) prompt for confirmation
  by default; `-y` skips the prompt for scripts; `-n` / `--dry-run`
  prints the planned action without executing (and without
  prompting). `-y` and `-n` are mutually exclusive.
- `job start` also has a `--dry-run` mode, but its purpose is
  different — it prints the underlying `salloc` argv so the user can
  preview the allocation request. No prompt either way (starting an
  allocation isn't destructive in the data-loss sense).

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
- **Default jobid resolution**: verb-aware — argument > `$SLURM_JOB_ID`
  (inside an allocation) > `squeue`, where `time`/`jump` auto-pick the most
  recent and `stop` refuses to guess (exit 2). Full rules in
  [`solx.md`](solx.md).
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

- **Stage 2 (shipped, solx v0.3.0)** — the `solx/` package: Sol-only CLI
  with agent-friendly output, verb-aware job-id resolution, and a
  file-level-sharded `keep`. Behavior is documented in
  [`solx.md`](solx.md); contributor notes in
  [`../solx/DEVELOPMENT.md`](../solx/DEVELOPMENT.md). Skill files untouched.
  (The pre-implementation contract `stage-2-solx.md` has been retired now
  that `solx.md` is the living manual.)
- **Stage 3+** — skill ↔ `solx` integration, only after the user greenlights.
