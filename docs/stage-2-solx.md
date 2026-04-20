# Stage 2 — `solx` CLI package

Sub-plan of [PLAN.md](PLAN.md). This stage runs **in parallel** with Stage 1; it does not touch `skills/sol-skill/`.

## Two-step rollout

The "one-magic-command from laptop" vision is genuinely uncertain — it threads laptop-side ssh-client behavior, ControlMaster, Duo, and queue-wait races. To de-risk, Stage 2 ships in two PRs:

| Stage | Scope | Status |
| --- | --- | --- |
| **2a** | `solx` is useful on Sol. User SSHes to Sol manually (per Stage 1 docs), then runs `solx` from there. | next up |
| **2b** | `solx up`/`down`/`forward`/`info` from the laptop — the one-command vision. | deferred |

Stage 2a leans on Stage 1: `references/sessions.md` already documents the laptop → Sol hop end-to-end, so users get a complete loop the day 2a ships. Stage 2b is purely additive — it collapses the manual hop into one command once we've proven the Sol-side primitives.

This sub-plan covers **Stage 2a in detail** and sketches Stage 2b at the end.

---

## Stage 2a — Sol-only `solx`

### Scope

Build the Sol-side half of `solx`: profile config, `session start/info/stop`, `config init/show`. Installable on Sol via `uv tool install`. The user reaches Sol manually (per `skills/sol-skill/references/sessions.md`) and runs `solx` from a login or compute node.

In scope: `solx/` package containing only the modules and commands needed for Sol-side operation.

Out of scope: any laptop-side command (`init`, `up`, `down`, `forward`, `info`), the `~/.config/solx/laptop.toml` schema, ssh-chain construction (`ssh.py`), ControlMaster/OAuth plumbing, root `README.md` reframing, anything under `skills/sol-skill/`.

### Deliverables (Stage 2a)

| Path | Action | Notes |
| --- | --- | --- |
| `solx/pyproject.toml` | NEW | Typer + Rich; entry point `solx = "solx.cli:app"`; stdlib `tomllib` (Python ≥ 3.11). |
| `solx/src/solx/__init__.py` | NEW | Version constant. |
| `solx/src/solx/__main__.py` | NEW | `python -m solx` entry. |
| `solx/src/solx/cli.py` | NEW | Typer root. Sol-side subcommands wired up; laptop-side commands present as **stubs that exit 2 with a "Stage 2b — see `sessions.md`" redirect**. |
| `solx/src/solx/side.py` | NEW | `detect()` reads `hostname -a`; returns `"sol"` if it matches `*.sol.rc.asu.edu`, else `"not-sol"`. Login-vs-compute distinction deferred. |
| `solx/src/solx/config.py` | NEW | Load `~/.config/solx/profiles.toml`; resolve `[shared]` + profile merge. |
| `solx/src/solx/session.py` | NEW | `~/.local/share/solx/session.json` r/w; stale-session detection. |
| `solx/src/solx/sol_cmds.py` | NEW | `session start/info/stop`, `config init/show`. Wraps `/usr/local/bin/vscode` for `kind=vscode`. |
| `solx/tests/` | NEW | pytest with subprocess mocked. Sol-side coverage only. |
| `solx/README.md` | NEW | Lead with "SSH to Sol first (see `skills/sol-skill/references/sessions.md`), then `solx`." Install + Sol-side examples. |

Notably **not** in this PR: `ssh.py`, `laptop_cmds.py`, `~/.config/solx/laptop.toml` schema. They land in Stage 2b.

### Implementation notes (Stage 2a)

- **CLI stack**: Typer + Rich. Defer Textual.
- **TOML**: stdlib `tomllib` only (no `tomli` dep). Python 3.11+ is enforced in `pyproject.toml`.
- **Side detection**: `hostname -a` matching `*.sol.rc.asu.edu` → `"sol"`; otherwise `"not-sol"`. On `not-sol`, every subcommand exits 2 with `solx is only useful on Sol in this release. SSH to Sol first — see skills/sol-skill/references/sessions.md.` No silent fallback.
- **`[shared]` merge semantics**: scalars (`partition`, `qos`, `time`) — profile overrides shared. Lists (`forward`, `srun_args`) — concatenate `[shared]` first, then profile. Lets a profile *extend* the shared baseline rather than replace it.
- **CLI passthrough**: anything after `--` on `solx session start` is appended to the underlying `srun` command, after profile `srun_args`. `[shared]` `srun_args` still apply unless the user re-specifies the same flag in the tail.
- **Stale-session detection**: `solx session start` checks for an existing `session.json`. If the recorded `job_id` is no longer in `squeue -j`, offer to clean up; if it *is* still queued/running, refuse with a message pointing at `solx session info`. No silent overwrite.
- **VSCode wrapper**: `kind=vscode` profiles invoke `/usr/local/bin/vscode` rather than reimplementing the tunnel logic. Preserves muscle memory and any future ASU-side changes.
- **`--dry-run`**: `solx session start --dry-run` prints the literal `srun`/`vscode` argv without executing. Snapshot-tested so flag changes are reviewed deliberately.
- **No laptop-side state**: Stage 2a never reads or writes `~/.config/solx/laptop.toml`, never invokes `ssh`, never reads `~/.ssh/*`. The security model is trivially upheld by construction in this stage.
- **Exit codes**: 0 ok / 1 failure / 2 conditional (wrong side, missing config, stale session present). Mirrors `skills/sol-skill/scripts/sol_renew.py`.

### Success criteria (Stage 2a)

1. **Installs on Sol**: `uv tool install ./solx` succeeds on a Sol login node; `solx --version` works in a fresh shell.
2. **Side guard works**: `solx where` returns `"sol mode"` on Sol; on a non-Sol shell every Sol-side subcommand exits 2 with the redirect message — no stack trace.
3. **Laptop-side stubs are honest**: `solx up`/`down`/`forward`/`info` exist in `--help` but exit 2 with "Stage 2b — use the manual flow in skills/sol-skill/references/sessions.md".
4. **`[shared]` merge correctness**: a profile that omits `qos` while `[shared]` provides `qos = "public"` resolves to `"public"`. A profile with `srun_args = ["--mem=64G"]` plus `[shared]` `srun_args = ["--mail-type=TIME_LIMIT_90,END,FAIL"]` resolves to both, in that order (shared first).
5. **Dry-run snapshots are stable**: `solx session start <profile> --dry-run` output is snapshot-tested per profile (default, gpu, debug). Diffs require explicit reviewer sign-off.
6. **Stale-session detection**: starting a session when an old `session.json` references a no-longer-queued job offers cleanup; when it references a still-running job, refuses and points at `solx session info`.
7. **Smoke on Sol passes**: the manual smoke checklist below completes against the `htc` partition with the `debug` profile in under 2 minutes.
8. **Tests pass**: `cd solx && uv run pytest -v` is green.

### Testing checklist (Stage 2a)

#### Unit tests (run anywhere — no Sol required)

```shell
cd solx && uv run pytest -v
```

Coverage targets:

- **Config parsing**: `[shared]` merge — scalars override, lists concatenate (shared-then-profile); unknown-key warning; missing-profile error.
- **Side detection**: faked `hostname -a` outputs for Sol login (`login02.sol.rc.asu.edu`), Sol compute (`sc010.sol.rc.asu.edu`), and not-Sol (anything else). All three branches covered.
- **`session start` argv construction**: snapshot-test the rendered `srun`/`vscode` argv per profile × `--dry-run` × `-- passthrough` combinations.
- **`session.json` round-trip**: write, read, parse; detect malformed JSON.
- **Stale-session logic**: mock `squeue -j` returning empty / returning the job → expected branch (offer cleanup vs refuse).
- **Wrong-side guard**: every subcommand on `not-sol` exits 2 with the redirect message.

#### Manual smoke on Sol (run after `ssh swan16@sol.asu.edu`)

Scoped tight on purpose — the `debug` profile on `htc` queues in seconds, so a full lifecycle takes <2 min. This is the only step that touches the real cluster.

1. **Install**:
   ```shell
   uv tool install git+https://github.com/Shu-Wan/sol-skills.git#subdirectory=solx
   solx --version
   ```
2. **Side detection**:
   ```shell
   solx where                       # → "sol mode (login02)"
   ```
3. **Config init + show**:
   ```shell
   solx config init
   solx config show                 # resolved view: [shared] keys merged into each profile
   ```
4. **Dry-run before live run**:
   ```shell
   solx session start debug --dry-run
   # printed srun line includes profile srun_args AND [shared] mail-type
   ```
5. **Live lifecycle on `htc`**:
   ```shell
   solx session start debug
   solx session info --json         # node, job_id, ports populated
   squeue -u $(whoami)              # job RUNNING
   solx session stop
   squeue -u $(whoami)              # job gone; session.json removed
   ```
6. **Stale-session handling**:
   ```shell
   # Manually scancel a running solx job, leaving session.json behind
   solx session start debug         # should offer to clean up the orphan, then proceed
   solx session stop
   ```
7. **Wrong-side guard** (run on a laptop, not Sol):
   ```shell
   solx where                       # → "not-sol — see skills/sol-skill/references/sessions.md"; exit 2
   solx session info                # same exit 2 + redirect
   ```

VSCode-wrapper validation and the `gpu` profile are deliberately deferred to Stage 2b's broader smoke pass — they need a longer-queueing partition and an active GPU allocation, which adds flakiness without proving anything Stage 2a doesn't already cover.

---

## Stage 2b — Laptop-side `solx` (deferred)

Once 2a is in users' hands and the Sol-side primitives are stable, Stage 2b adds the laptop side: `solx init`, `solx up/down/forward/info`. This is the "one magic command from laptop" vision.

### Pulled out of Stage 2a

- `solx/src/solx/laptop_cmds.py` — `init`, `up`, `down`, `forward`, `info`.
- `solx/src/solx/ssh.py` — building `ssh -L -J …` commands without reading `~/.ssh/*`.
- `~/.config/solx/laptop.toml` schema and `solx init` interactive setup.
- ControlMaster opportunistic use (`-o ControlPath=...`).
- OAuth callback `-R` reverse-tunnel ergonomics.
- Bounded-poll/backoff for queue-wait races between `up` issuing `srun` and the compute node populating `session.json`.

### Why deferred

- **Flaky surface**: every item above touches Duo, ssh-client behavior, ControlMaster sockets, or Slurm queue races. None of these are unit-testable; all need real round-trips against Sol to validate.
- **No user gap**: Stage 1's `references/sessions.md` documents the manual hop end-to-end. Users have a working laptop-side flow without 2b.
- **Validation cost**: Stage 2b can't be CI-tested — it needs a real laptop ↔ real Sol round-trip with a real Duo prompt. Deferring means we earn that cost only once the Sol-side primitives are proven, not while they're still moving.

### What Stage 2b will need

A separate sub-plan (`docs/stage-2b-solx-laptop.md`) when the time comes. Likely shape: extend the layout in [PLAN.md §"Repo layout"](PLAN.md#repo-layout-new) with `laptop_cmds.py` + `ssh.py`, add the laptop-side success criteria and smoke checklist from the previous version of this file, and require a real-Sol smoke pass before merging.

---

## Out of scope for both 2a and 2b

- Any change under `skills/sol-skill/` — that's Stage 1 (already shipped) and Stage 3.
- Root `README.md` reframing — Stage 3.
- Renaming the repo, publishing to PyPI, or any release-engineering work.
