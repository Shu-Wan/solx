# Roadmap: the `solx` CLI

Forward-looking design doc for **`solx`**, a CLI for working on ASU's
**Sol** supercomputer. solx is a native single binary that drives the
Sol-side loop - interactive Slurm jobs, scratch renewal, and one TOML
config. That loop is stable; the next focus is the **local-machine
(laptop) side** (below).

End-user docs: [`../README.md`](../README.md),
[`../skills/sol-skill/SKILL.md`](../skills/sol-skill/SKILL.md).
Contributor / harness docs: [`../DEVELOPMENT.md`](../DEVELOPMENT.md),
[`coverage.md`](coverage.md). Per-release history:
[`../CHANGELOG.md`](../CHANGELOG.md).

## What `solx` does today

- **Interactive jobs from templates** - `solx job start/list/time/stop`
  and `solx job jump` onto the compute node.
- **Scratch renewal** - `solx keep` renews only the `[keep]` directories
  Sol has actually flagged, never a blanket `touch`.
- **One TOML config** - `solx init` writes a starter; `solx config`
  shows/edits it.
- **A built-in cheat sheet** - `solx cheatsheet` prints the Sol quick
  reference (partition/QOS table, `solx` ↔ raw SLURM, wrappers, tunnels).
- **Built for CLI agents** - JSON off a TTY, results on stdout /
  diagnostics on stderr, meaningful exit codes, no hidden prompts; static
  shell completions for bash/zsh/fish.
- **A single static binary** - one exec, startup flat on the NFS home
  regardless of node load, with no Python, `uv`, or toolchain on the box.

The companion `sol-skill` teaches an agent when to reach for `solx` vs.
raw Slurm, and the rest of Sol's conventions.

## Next: the local-machine (laptop) side

The Sol-side loop is stable, so the next step is bringing it to where you
start - your laptop. The sketch is `solx up` / `down` / `forward` / `info`:
construct the SSH chain (ProxyJump through the login node), start or attach
an allocation, and forward a port to a compute-node service, all from the
local machine.

It's the next focus, not started: the design threads ssh-client behavior,
ControlMaster, Duo, and scheduler queue races - none of it unit-testable -
so it needs a from-scratch design and a maintainer greenlight before work
begins. Until then `solx` stays a tool you run **on Sol**, and the manual
`ssh -L ... -J ...` chain (see the skill) covers the laptop side.

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
   prefer it. `solx` has to *earn* its place per task - that's why it's a
   single native binary with startup on the order of a raw SLURM call.

## Command surface, config, and behavior -> `solx.md`

The full command surface, config schema, job-id resolution, agent-output
contract, and the `keep` mechanism live in the user manual
[`solx.md`](solx.md) - the **single source of truth** for what `solx` does.
Contributor/architecture notes are in
[`../solx/DEVELOPMENT.md`](../solx/DEVELOPMENT.md). This roadmap stays focused
on *why* and *what's next*; it deliberately does not restate the API.

## Security model

`solx` is Sol-only by design, so the security surface is small:

- Never read `~/.ssh/*`. The CLI doesn't invoke `ssh` at all.
- The single config (`$XDG_CONFIG_HOME/solx/config.toml`) is created
  with mode 0600.
- `solx keep` only touches files under directories the user has declared
  in `[keep]`. Mutates `atime`/`mtime` only - never reads, moves, or
  deletes content.
- Destructive commands (`job stop`, `keep`) prompt by default; `-y`
  skips the prompt for scripts; `-n` / `--dry-run` prints the planned
  action without executing. `-y` and `-n` are mutually exclusive.
- `job start --dry-run` prints the underlying `salloc` argv to preview
  the allocation request (no prompt - starting an allocation isn't
  destructive in the data-loss sense).

When local-machine-side work returns (deferred), a fresh security review of that
surface comes with it.

## Decisions confirmed

- **Implementation**: native binary in Rust (`clap` command tree). Plain
  aligned tables for human output; nothing emits color. Command bodies do
  no work until dispatched, so startup is a single exec.
- **Completions**: static scripts for bash, zsh, and fish, embedded in
  the binary (`solx/assets/`) and emitted by `solx completions`;
  completion never execs `solx`. Both zsh install modes (eval/source and
  fpath autoload) are supported.
- **`~/.solkeep`**: not used. The config `[keep]` block is the only
  keep-list source; `solx keep` never reads a `~/.solkeep`, and there is
  no `import-solkeep` command or `--solkeep` flag.
- **Config**: single TOML under `$XDG_CONFIG_HOME/solx/config.toml`. No
  multi-file split, no `[shared]` merge.
- **Glob library for `[keep]`**: `pathspec` (gitignore-style include +
  exclude).
- **State tracking**: none. `squeue -u $USER` is the source of truth.
- **Default jobid resolution**: verb-aware - argument > `$SLURM_JOB_ID` >
  `squeue`, where `time`/`jump` auto-pick the most recent and `stop`
  refuses to guess (exit 2). Full rules in [`solx.md`](solx.md).
- **Repo layout**: one repo - CLI under `solx/`, skill under
  `skills/sol-skill/`, on one version line.
- **`vscode` / `sbatch` wrappers**: out of scope. For VSCode, run
  `code tunnel` on a compute node; for batch, `sbatch` directly.
- **Skill subcommands** (`solx skill install/remove/...`): reserved, not
  implemented (the skill installs via agentskills.io installers). Revisit
  if it earns its place.
