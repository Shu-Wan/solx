# Roadmap: the `solx` CLI

Forward-looking design doc for **`solx`**, a CLI for working
on ASU's **Sol** supercomputer. The Sol-side CLI and its skill
integration shipped in v0.4.0; v0.5.0 cut startup latency to the same
order as a raw SLURM call; v1.0 made `solx` a native single binary
(Rust). With the Sol-side CLI stable, the next focus is the
**local-machine (laptop) side** (below).

End-user docs: [`../README.md`](../README.md),
[`../skills/sol-skill/SKILL.md`](../skills/sol-skill/SKILL.md).
Contributor / harness docs: [`../DEVELOPMENT.md`](../DEVELOPMENT.md),
[`coverage.md`](coverage.md). Released history:
[`../CHANGELOG.md`](../CHANGELOG.md).

## Stages

| Stage | Outcome | Status |
|---|---|---|
| 1 — Skill manual-SSH path | The agent skill (manual SSH, `sbatch`, scratch renewal). | ✅ shipped (v0.2.0) |
| 2 — `solx` CLI (Sol-only) | The `solx` CLI: jobs, interactive allocation, scratch renewal, config; CLI agent output. | ✅ shipped (v0.3.0) |
| 3 — Skill ↔ `solx` integration + distribution | Skill installs and drives `solx`; single-file install channel + CI releases; one version line; situational job awareness (#9). | ✅ shipped (v0.4.0) |
| 4 — Startup latency | Thin spine: stdlib `argparse` dispatch, `rich` only on human render paths, static completion scripts. A warm `solx job` read costs ~0.13s with the `.pyz` install — same order as raw `squeue`. | ✅ shipped (v0.5.0) |
| 5 — Native single binary | Rewrite `solx` as one native executable (Rust): cold-start immunity on the NFS home, no Python/`uv` runtime requirement, prebuilt static binary. Retires the Python implementation. | ✅ shipped (v1.0.0) |
| 6 — Local-machine side | `solx up/down/forward/info`, ssh-chain construction from the laptop. | 🔜 next (design) |

Shipped-stage detail lives in [`../CHANGELOG.md`](../CHANGELOG.md).

## Startup latency — shipped in v0.5.0

v0.5.0 removed the Python-startup tax `solx` used to pay on Sol's NFS home
(a Typer/Click import on every call, plus `rich` even on `--json` runs):
a stdlib `argparse` thin spine that short-circuits `--version`, imports
command bodies lazily, and emits fully static completion scripts. That
brought a warm `solx job` read to the same order as raw `squeue`; the v1.0
native binary then removed the interpreter floor underneath it entirely.

**Measured** (Sol compute node, 4 cores, NFS `$HOME`, real Slurm 25.11.6;
warm median seconds, n=9, cold-ish first run in parentheses):

| command | raw squeue | v0.4.0 pyz (NFS) | v0.5.0 pyz (`/tmp`) |
|---|---|---|---|
| `--version` | — | 1.345 (1.390) | **0.018** (0.019) |
| `job list` | 0.076 (0.741) | 2.505 (1.537) | **0.126** (0.123) |
| `job time` | 0.076 (0.071) | 2.505 (2.505) | **0.127** (0.116) |

Installed apples-to-apples on NFS `$HOME`, v0.5.0 `.pyz` was ~0.10s /
0.39s / 0.31s — **13× / 6.4× / 8.1×** over v0.4.0; the residual over raw
`squeue` was ~50ms (interpreter start + the `squeue` fork).
`evals/runner/bench_solx_latency.sh` reproduces the comparison on any Sol
node.

## Native single binary — shipped in v1.0

v1.0 made `solx` a single native binary (Rust), so a command is one exec
with flat startup regardless of node load — no interpreter, no `uv`/Python
on the box. The command surface and output are unchanged, verified against
`evals/parity/`. Detail and the measured table are in the
[v1.0.0 changelog entry](../CHANGELOG.md).

## Next: the local-machine (laptop) side

The Sol-side loop is stable, so the next step is bringing it to where you
start — your laptop. The sketch is `solx up` / `down` / `forward` / `info`:
construct the SSH chain (ProxyJump through the login node), start or attach
an allocation, and forward a port to a compute-node service, all from the
local machine.

It's the next focus, not started: the design threads ssh-client behavior,
ControlMaster, Duo, and scheduler queue races — none of it unit-testable —
so it needs a from-scratch design and a maintainer greenlight before work
begins. Until then `solx` stays a tool you run **on Sol**, and the manual
`ssh -L … -J …` chain (see the skill) covers the laptop side.

## Out of scope (still)

- **Package-manager publication** (crates.io, PyPI, Homebrew). Install is
  the prebuilt binary from the GitHub Release.

## Design principles

These are the load-bearing constraints for `solx`. Every decision below
derives from them.

1. **Runs on Sol.** The CLI is meant to be run *on* Sol after a manual
   SSH. No local-machine side, no ssh-chain construction, no `~/.ssh/*` reads.
2. **Reduce recall and context switching.** The common path should not
   require memorizing Slurm flags or bouncing between a website portal
   and the terminal. Open OnDemand can stay browser-first; `solx` owns
   the terminal-native loop.
3. **CLI agent native.** Human-readable output on a TTY, JSON when
   piped or requested, stdout/stderr separation, meaningful exit codes,
   and no hidden prompts in non-interactive sessions.
4. **Intuitive and not disruptive.** Verbs read like Sol-native
   commands (`solx job list`, `solx job start`, `solx keep`). No
   surprising side effects. Mutating commands support `--dry-run`.
5. **Common CLI conventions.** Noun-verb command groups; flags for leaf
   commands. Shell completions for bash, zsh, fish.
6. **Read config, don't infer.** A single TOML config under
   `$XDG_CONFIG_HOME/solx/config.toml` declares everything. No
   environment-variable trickery, no scanning `~/.ssh/*`.
7. **Slurm is the source of truth, not us.** No persistent
   `session.json` to go stale. The CLI queries `squeue` whenever it
   needs job state.
8. **General, not personal.** The starter config ships with
   placeholders, never with the maintainer's username baked in.
9. **User experience over the tool.** The skill drives an agent on the
   user's behalf; where a raw SLURM call is faster and just as clear,
   prefer it. `solx` has to *earn* its place per task — the v0.5.0
   startup-latency work existed because of this principle, and the v1.0
   native binary continued it.

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

When local-machine-side work returns (deferred), a fresh security review of that
surface comes with it.

## Decisions confirmed

- **Implementation**: native binary in Rust as of v1.0 (`clap` command
  tree), replacing the v0.5.0 stdlib-`argparse` Python build. Plain
  aligned tables for human output; nothing emits color. The v0.5.0
  thin-spine work (see [Startup latency](#startup-latency--shipped-in-v050))
  is what the binary builds on.
- **Completions**: static scripts for bash, zsh, and fish, embedded in
  the binary (`solx/assets/`) and emitted by `solx completions`;
  completion never execs `solx`. Both zsh install modes (eval/source and
  fpath autoload) are supported.
- **`~/.solkeep` fallback**: removed in v1.0. Deprecated since 0.4.0 with
  the removal deferred to 1.0.0; `solx keep` now reads the keep-list only
  from the config `[keep]` block. `solx config import-solkeep` migrates a
  legacy file, and `--solkeep <file>` is an explicit per-run override.
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
