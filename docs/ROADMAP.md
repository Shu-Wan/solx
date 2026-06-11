# Roadmap: the `solx` CLI

Forward-looking design doc for **`solx`**, a CLI for working
on ASU's **Sol** supercomputer. The Sol-side CLI and its skill
integration shipped in v0.4.0; v0.5.0 cut startup latency to the same
order as a raw SLURM call. The next focus is the **native single-binary
rewrite** (below); the **local-machine-side** design stays deferred.

End-user docs: [`../README.md`](../README.md),
[`../skills/sol-skill/SKILL.md`](../skills/sol-skill/SKILL.md).
Contributor / harness docs: [`../DEVELOPMENT.md`](../DEVELOPMENT.md),
[`coverage.md`](coverage.md). Released history:
[`../CHANGELOG.md`](../CHANGELOG.md).

## Stages

| Stage | Outcome | Status |
|---|---|---|
| 1 — Skill manual-SSH path | The agent skill (manual SSH, `sbatch`, scratch renewal). | ✅ shipped (v0.2.0) |
| 2 — `solx` CLI (Sol-only) | `solx/` package: jobs, interactive allocation, scratch renewal, config; CLI agent output. | ✅ shipped (v0.3.0) |
| 3 — Skill ↔ `solx` integration + distribution | Skill installs and drives `solx`; single-file install channel + CI releases; one version line; situational job awareness (#9). | ✅ shipped (v0.4.0) |
| 4 — Startup latency | Thin spine: stdlib `argparse` dispatch, `rich` only on human render paths, static completion scripts. A warm `solx job` read costs ~0.13s with the `.pyz` install — same order as raw `squeue`. | ✅ shipped (v0.5.0) |
| 5 — Native single binary | Rewrite `solx` as one native executable (Rust): cold-start immunity on the NFS home, no Python/`uv` runtime requirement, single-file install. | 🟡 in development (`v1.0-rust` branch, targets v1.0) |
| — Local-machine side | `solx up/down/forward`, ssh-chain construction. | ⏸ deferred |

Shipped-stage detail lives in [`../CHANGELOG.md`](../CHANGELOG.md).

## Startup latency — shipped in v0.5.0

On Sol's NFS home, `solx` used to pay a Python-startup tax a raw SLURM
binary doesn't (Typer/Click import ≈ 0.97s on every invocation, plus
`rich` pulled in even on `--json` runs), so the skill steered agents to
raw `squeue`/`scancel` for one-off reads. v0.5.0 removed that tax with a
**thin spine**:

- **stdlib `argparse` dispatch** (`solx/src/solx/main.py`, entry point
  `solx.main:main`). Importing the entry module costs nothing beyond the
  interpreter baseline; `--version`/`version` short-circuit before the
  parser tree is even built; command bodies (and their `rich`/`pathspec`
  dependency trees) import inside their handlers.
- **`rich` on human render paths only.** `Out` writes JSON and plain
  diagnostics straight to `sys.stdout`/`sys.stderr`; `rich.table` /
  `rich.prompt` import inside the table-render and prompt branches. A
  `--json` or piped run never loads `rich` at all.
- **Static completion scripts.** `solx completions <bash|zsh|fish>`
  renders the command surface into a fully static script
  (`solx/src/solx/_completions.py`) — completion never execs `solx`, so
  the first Tab of a session costs no interpreter start.

**Measured** (Sol compute node inside an allocation, 4 cores, NFS
`$HOME`, real Slurm 25.11.6; warm median seconds, n=9 after 1 warmup,
cold-ish first run in parentheses):

| command | raw squeue | v0.4.0 venv | v0.4.0 pyz (`~/.local/bin`) | v0.5.0 venv | v0.5.0 pyz (local `/tmp`) |
|---|---|---|---|---|---|
| `--version` | — | 1.137 (1.584) | 1.345 (1.390) | 0.281 (0.234) | **0.018** (0.019) |
| `job list` | 0.076 (0.741) | 2.500 (2.141) | 2.505 (1.537) | 1.020 (2.160) | **0.126** (0.123) |
| `job time` | 0.076 (0.071) | 1.251 (1.346) | 2.505 (2.505) | 0.945 (0.153) | **0.127** (0.116) |

raw squeue rows: `job list` = `squeue --me`; `job time` =
`squeue -h -j $SLURM_JOB_ID -o %L`. Caveats that keep the table honest:

- The `.pyz` column places the v0.5.0 artifact on node-local `/tmp` and
  the v0.4.0 one on NFS, so the raw 75× / 19.9× / 19.7× overstates code
  alone. Installed apples-to-apples on NFS `$HOME` (where `install.sh`
  writes it), v0.5.0 `.pyz` is ~0.10s / 0.39s / 0.31s — **13× / 6.4× /
  8.1×** over v0.4.0. Venv-to-venv on NFS: 4.0× / 2.5× / 1.3×. Node-local
  `/tmp` is the best case (`--version` ~0.02s).
- The remaining gap vs raw `squeue` is ~50ms: interpreter startup plus
  the `squeue` subprocess fork are all that's left.
- "Cold" is the first invocation in the benchmark process only — page
  cache on a shared node makes true cold unmeasurable, so treat cold
  numbers as cold-ish. The cluster controller showed sporadic ~2s
  `squeue` spikes, which the n=9 medians absorb.

`evals/runner/bench_solx_latency.sh` reproduces the solx-vs-raw
comparison on any Sol node; `evals/parity/` is the behavioral matrix
that verified the dispatch rewrite against captured v0.4.0 output.

**What remains, for v1.0:**

- **Stage 5 — the native single-binary rewrite (Rust).** A compiled
  `solx` removes the interpreter floor entirely and is immune to NFS
  cold starts: no Python or `uv` runtime requirement, one static file to
  install. In development on the `v1.0-rust` branch.
- **Actually removing the `~/.solkeep` fallback.** Its removal moved
  from 0.5.0 to **1.0.0** — `solx keep` keeps reading a legacy
  `~/.solkeep` (with a deprecation notice) through the 0.5.x line, so
  the migration window spans one more release.

## Out of scope (still)

- **Local-machine-side `solx`** (`up/down/forward/info`, ssh-chain
  construction) — deferred. The original "one magic command from the
  local machine" threaded ssh-client behavior, ControlMaster, Duo, and queue
  races, none of which are unit-testable. It returns only when the
  Sol-side primitives are stable, the design is re-thought from scratch,
  and the user greenlights it. `solx` stays a tool you run **on Sol**.
- **PyPI publication.** Install is via the `.pyz` channel or
  `uv tool install` from Git.

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
   startup-latency work exists because of this principle, and the
   native rewrite (Stage 5) continues it.

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

- **CLI framework**: stdlib `argparse` as of 0.5.0 (see
  [Startup latency](#startup-latency--shipped-in-v050)). `rich` is
  retained for human-facing tables and prompts only, imported only on
  those paths — agent (`--json`/piped) runs never load it. Textual
  deferred.
- **Completions**: static scripts generated from one description of the
  command surface (`solx/src/solx/_completions.py`) for bash, zsh, and
  fish; completion never execs `solx`. Both zsh install modes
  (eval/source and fpath autoload) are supported.
- **`~/.solkeep` removal**: **1.0.0**. Deprecated since 0.4.0; `solx
  keep` still reads it with a deprecation notice, and `solx config
  import-solkeep` migrates it.
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
