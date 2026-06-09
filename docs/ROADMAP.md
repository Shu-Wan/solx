# Roadmap: the `solx` CLI

Forward-looking design doc for **`solx`**, a Python CLI for working
on ASU's **Sol** supercomputer. The Sol-side CLI and its skill
integration shipped in v0.4.0. The next focus is **cutting `solx`'s
startup latency** (below); the **laptop-side** design stays deferred.

End-user docs: [`../README.md`](../README.md),
[`../skills/sol-skill/SKILL.md`](../skills/sol-skill/SKILL.md).
Contributor / harness docs: [`../DEVELOPMENT.md`](../DEVELOPMENT.md),
[`coverage.md`](coverage.md). Released history:
[`../CHANGELOG.md`](../CHANGELOG.md).

## Stages

| Stage | Outcome | Status |
|---|---|---|
| 1 — Skill manual-SSH path | The agent skill (manual SSH, `sbatch`, scratch renewal). | ✅ shipped (v0.2.0) |
| 2 — `solx` CLI (Sol-only) | `solx/` package: jobs, interactive allocation, scratch renewal, config; agent-friendly output. | ✅ shipped (v0.3.0) |
| 3 — Skill ↔ `solx` integration + distribution | Skill installs and drives `solx`; single-file install channel + CI releases; one version line; situational job awareness (#9). | ✅ shipped (v0.4.0) |
| 4 — Startup latency | Get a `solx job` command close to a raw SLURM call so the skill can prefer `solx` without a UX penalty. | ⚪ planned (v0.5.0) |
| — Laptop side | `solx up/down/forward`, ssh-chain construction. | ⏸ deferred |

Shipped-stage detail lives in [`../CHANGELOG.md`](../CHANGELOG.md).

## Next: cut `solx` startup latency

`solx` is the user's convenience tool, but on Sol's NFS home it pays a
Python-startup tax that a raw SLURM binary doesn't. Because the agent
skill must put **user experience first**, it currently steers an agent to
raw `squeue`/`scancel` for one-off reads (see SKILL.md, "`solx` vs raw
SLURM") and reserves `solx` for the multi-step lifecycle and renewal.
Closing the latency gap is the next goal — a `solx job` command should
cost on the order of a raw SLURM call, so the skill no longer has to
choose between ergonomics and speed.

**Measured** (`evals/runner/bench_solx_latency.sh`, Sol compute node,
median of 7, warm):

| Command | Time |
|---|---|
| `squeue --me` (raw) | ~0.05s |
| `solx job list` | ~1.7s |
| `solx job time` | ~1.0s |
| `solx --version` (startup floor) | ~0.66s |

**Why it's slow** (`python -X importtime`):

- **Typer/Click import ≈ 0.97s** — the dominant cost, paid by *every*
  invocation because `cli.py` builds the Typer app at import time. This
  is most of the ~0.66s floor.
- **`rich` is imported on every *actual* command.** `cli.py` already
  defers its imports (the 0.3.3 work that made `--version` / `--help` /
  completions fast), but running a command still pulls `rich` two ways:
  `output.py`'s `Out.auto` constructs `rich.Console` objects for
  stdout+stderr *even in `--json` mode*, and `jobs.py` / `keep.py` /
  `init.py` import `rich.prompt` / `rich.table` at module scope. So
  `solx job list` pays `rich` even when an agent passes `--json` and
  never renders a table.
- **NFS amplification** — each module file is a network round-trip. The
  `.pyz` collapses the file-open storm into one zip open, but still
  parses the zip directory and pays the Typer/`rich` import cost, so it
  helps cold-start more than warm-start.

**Possible solutions** (rough order of value vs. effort):

1. **Keep `rich` off the agent path.** Have `Out` write JSON and plain
   diagnostics straight to `sys.stdout`/`sys.stderr` without constructing
   a `rich.Console`, and lazy-import `rich.table` / `rich.prompt` inside
   the human-render and prompt branches. Then `--json` and
   non-interactive runs never import `rich` at all. Moderate (touches the
   `Out` abstraction and the `init.py` Confirm/Prompt test seams), high
   value for the agent path.
2. **Shrink the Typer cost on the hot path** — the biggest lever and the
   hardest. Either a fast pre-dispatch that handles the common leaf
   commands (`job list/time`, `--version`) with `argparse` and only
   imports Typer for help/completions, or migrate the CLI off Typer to
   `click`/`argparse` outright. Must preserve the command surface,
   aliases, and completion behavior.
3. **Keep the `.pyz` the default install.** Already recommended; it
   removes the per-file NFS round-trips. Keep the precompiled bytecode in
   sync with the shebang interpreter (it is).
4. **Rejected for now: a resident daemon.** A long-lived `solx` server
   the thin client talks to would amortize import cost, but it adds a
   lifecycle, a socket, and stale-state risk that conflicts with the
   "Slurm is the source of truth, no persistent state" principle below.

**Goal:** a warm `solx job list` in the low hundreds of milliseconds —
close enough to raw `squeue` that preferring `solx` carries no UX
penalty. Targeted for **v0.5.0** (alongside dropping `~/.solkeep`).

## Out of scope (still)

- **Laptop-side `solx`** (`up/down/forward/info`, ssh-chain
  construction) — deferred. The original "one magic command from the
  laptop" threaded ssh-client behavior, ControlMaster, Duo, and queue
  races, none of which are unit-testable. It returns only when the
  Sol-side primitives are stable, the design is re-thought from scratch,
  and the user greenlights it. `solx` stays a tool you run **on Sol**.
- **PyPI publication.** Install is via the `.pyz` channel or
  `uv tool install` from Git.

## Design principles

These are the load-bearing constraints for `solx`. Every decision below
derives from them.

1. **Runs on Sol.** The CLI is meant to be run *on* Sol after a manual
   SSH. No laptop side, no ssh-chain construction, no `~/.ssh/*` reads.
2. **Intuitive and not disruptive.** Verbs read like Sol-native
   commands (`solx job list`, `solx job start`, `solx keep`). No
   surprising side effects. Mutating commands support `--dry-run`.
3. **Common CLI conventions.** Noun-verb command groups; flags for leaf
   commands. Shell completions for bash, zsh, fish.
4. **Read config, don't infer.** A single TOML config under
   `$XDG_CONFIG_HOME/solx/config.toml` declares everything. No
   environment-variable trickery, no scanning `~/.ssh/*`.
5. **Slurm is the source of truth, not us.** No persistent
   `session.json` to go stale. The CLI queries `squeue` whenever it
   needs job state.
6. **General, not personal.** The starter config ships with
   placeholders, never with the maintainer's username baked in.
7. **User experience over the tool.** The skill drives an agent on the
   user's behalf; where a raw SLURM call is faster and just as clear,
   prefer it. `solx` has to *earn* its place per task — which is why the
   startup-latency work above matters.

## Command surface, config, and behavior → `solx.md`

The full command surface, config schema, job-id resolution, agent-output
contract, and the `keep` mechanism live in the user manual
[`solx.md`](solx.md) — the **single source of truth** for what `solx` does.
Contributor/architecture notes are in
[`../solx/DEVELOPMENT.md`](../solx/DEVELOPMENT.md). This roadmap stays focused
on *why* and *what's next*; it deliberately does not restate the API.

## Security model

`solx` is Sol-only by design, so the security surface is small:

- Never read `~/.ssh/*`. The CLI doesn't invoke `ssh` at all.
- The single config (`$XDG_CONFIG_HOME/solx/config.toml`) is created
  with mode 0600.
- `solx keep` only touches files under directories the user has declared
  in `[keep]`. Mutates `atime`/`mtime` only — never reads, moves, or
  deletes content.
- Destructive commands (`job stop`, `keep`) prompt by default; `-y`
  skips the prompt for scripts; `-n` / `--dry-run` prints the planned
  action without executing. `-y` and `-n` are mutually exclusive.
- `job start --dry-run` prints the underlying `salloc` argv to preview
  the allocation request (no prompt — starting an allocation isn't
  destructive in the data-loss sense).

When laptop-side work returns (deferred), a fresh security review of that
surface comes with it.

## Decisions confirmed

- **CLI framework**: Typer + Rich today — but Typer's import cost is the
  main startup-latency lever, so it is **under review** (see
  [Next](#next-cut-solx-startup-latency)). Textual deferred.
- **Config**: single TOML under `$XDG_CONFIG_HOME/solx/config.toml`. No
  multi-file split, no `[shared]` merge.
- **Glob library for `[keep]`**: `pathspec` (gitignore-style include +
  exclude).
- **State tracking**: none. `squeue -u $USER` is the source of truth.
- **Default jobid resolution**: verb-aware — argument > `$SLURM_JOB_ID` >
  `squeue`, where `time`/`jump` auto-pick the most recent and `stop`
  refuses to guess (exit 2). Full rules in [`solx.md`](solx.md).
- **Repo layout**: same repo, CLI under `solx/`, skill under
  `skills/sol-skill/`, one version line. Repo renamed `sol-skills` →
  `solx` at v0.4.0; the name `solx` was kept (short, unique, evokes Sol).
- **`vscode` / `sbatch` wrappers**: out of scope. For VSCode, run
  `code tunnel` on a compute node; for batch, `sbatch` directly.
- **Skill subcommands** (`solx skill install/remove/...`): reserved, not
  implemented as of v0.4.0 (the skill installs via agentskills.io
  installers). Revisit if it earns its place.
