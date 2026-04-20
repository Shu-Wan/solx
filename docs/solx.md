# `solx` — Sol-side session and config CLI

`solx` is a small Python CLI that wraps `sbatch`/`scancel` and tracks
a single active Slurm session in a JSON file under `$HOME`. Run it on
Sol — login or compute node — after SSHing in.

This release ships **Sol-side commands only**. The "one command from
laptop" workflow (`solx up`, `solx down`, etc.) is deferred; those
commands exist in `--help` but exit with a deferral message.

## Install (on Sol)

`solx` is published from the `solx/` subdirectory of the
[`sol-skills`](https://github.com/Shu-Wan/sol-skills) repo.

```shell
# Recommended: uv tool — isolated, on $PATH automatically.
uv tool install git+https://github.com/Shu-Wan/sol-skills.git#subdirectory=solx
solx --version
```

If `uv` isn't on your `$PATH` yet, install it from
[astral.sh/uv](https://docs.astral.sh/uv/) first. Sol's system
`python3` is older than what `solx` needs (Python ≥ 3.11); `uv tool
install` provisions its own interpreter, so you don't have to manage
one.

## Quick start

```shell
ssh swan16@sol.asu.edu                 # manual hop — laptop-side automation lands later
solx config init                       # writes ~/.config/solx/profiles.toml
$EDITOR ~/.config/solx/profiles.toml   # tune partitions, mail-user, etc.
solx config show                       # print resolved profiles ([shared] merged in)
solx session start debug               # submit sbatch, wait for the allocation, record session.json
solx session info                      # node, job_id, ports, started_at
# ... do work, optionally `srun --jobid=<id> --pty bash` to step into the allocation ...
solx session stop                      # scancel + clear session.json
```

A full lifecycle on the `htc` partition (the `debug` profile) takes
under two minutes.

## profiles.toml schema

`~/.config/solx/profiles.toml` defines named profiles, plus an
optional `[shared]` table whose keys apply to every profile.

```toml
[shared]
qos = "public"
srun_args = [
  "--mail-type=TIME_LIMIT_90,END,FAIL",
  "--mail-user=swan16@asu.edu",
]

[default]
kind = "bare"
partition = "lightwork"
time = "1-0"
forward = [8888]

[gpu]
kind = "bare"
partition = "general"
gres = "gpu:a100:1"
time = "0-4"
forward = [8888, 6006]
srun_args = ["--mem=64G", "--cpus-per-task=8"]

[debug]
kind = "bare"
partition = "htc"
time = "0-1"
forward = [8000, 8888]
```

### `[shared]` merge semantics

| Key kind | Behavior |
| --- | --- |
| Scalars (`partition`, `qos`, `time`, `gres`) | Profile value overrides the shared value. |
| Lists (`forward`, `srun_args`) | Concatenated: `[shared]` first, then profile. |

This lets a profile **extend** the shared baseline — e.g. inherit a
common `--mail-type` notification from `[shared]` while adding
`--mem=64G` per-profile.

### `srun_args` field

Despite the name, `srun_args` is forwarded verbatim to the underlying
**`sbatch`** invocation in this release (sbatch and srun share most
flags — `--mem`, `--cpus-per-task`, `--mail-type`, `--mail-user`,
etc.). Anything sbatch accepts as a flag goes here.

### `forward` field

A list of ports, recorded into `session.json` so a future laptop-side
`solx forward` command knows what to tunnel. In this release the
field is purely documentary — no tunnels are opened.

### `kind` field

Only `kind = "bare"` is implemented in this release. Any other value
returns exit 2 with a clear message; the field exists to leave room
for `vscode` and `sbatch-script` kinds without a config breaking
change later.

## Command reference

All commands exit `0` on success, `1` on failure (e.g. sbatch
errored, the allocation timed out), or `2` on conditional refusals
(wrong side, missing config, existing live session).

### `solx where`

Prints `sol mode (<short-hostname>)` or `not-sol mode (<host>)`.
Always safe to run.

### `solx config init [--force]`

Writes a starter `~/.config/solx/profiles.toml` with the three
profiles above. Refuses to overwrite an existing file unless
`--force`.

### `solx config show [--json]`

Loads `profiles.toml`, applies `[shared]`, and prints the resolved
view of every profile. Pass `--json` for a machine-readable dump.

### `solx session start [PROFILE] [--dry-run] [-- EXTRA ARGS]`

Submits the profile's sbatch command, polls `squeue` until the
allocation is `RUNNING`, then writes `~/.local/share/solx/session.json`
with `{profile, kind, job_id, node, ports, started_at}`.

- `PROFILE` defaults to `default`.
- `--dry-run` (or `-n`) prints the sbatch argv without submitting.
- Anything after `--` is appended to the sbatch command, after the
  profile's own `srun_args`. sbatch's last-flag-wins lets the CLI
  tail override profile defaults — e.g. `solx session start gpu --
  --mem=128G` runs with 128 GB instead of the profile's 64 GB while
  still inheriting `[shared]` mail flags.
- Polls every 2s up to 10 minutes. Bounded so a stuck queue surfaces
  instead of hanging indefinitely; `scancel <job_id>` to give up.

#### Stale-session handling

`session.json` already exists when you run `solx session start`?

| State of recorded job | What happens |
| --- | --- |
| Still in `squeue` | Refuses with exit 2; tells you to run `solx session info` or `solx session stop` first. |
| Not in `squeue` (job ended out-of-band) | Notes "stale session" and clears the file before submitting. |
| Malformed JSON | Notes "malformed", clears, and proceeds. |

You should never end up with two `solx`-managed allocations clobbering
each other.

### `solx session info [--json]`

Prints the contents of `session.json` as a Rich table, or as raw JSON
with `--json`. Returns exit 1 if there is no active session.

### `solx session stop`

`scancel`s the recorded job and removes `session.json`. Idempotent —
if there's no recorded session, prints a notice and exits 0.

## Limitations and what's deferred

**Single-session model.** `solx` tracks one allocation at a time.
Multi-session work is out of scope; nothing prevents you from running
`sbatch` directly alongside `solx`, but `solx`'s commands won't know
about those allocations.

**No "drop into a shell" command yet.** After `solx session start`
populates `session.json`, you join the allocation manually:

```shell
srun --jobid="$(solx session info --json | jq -r .job_id)" --pty bash
```

A future `solx session shell` will collapse this.

**`kind = "vscode"` and `kind = "sbatch-script"` are not yet wired.**
The starter config uses `kind = "bare"` for all three profiles. A
future release will add the wrappers; until then, run `vscode` or
`sbatch your-script.sbatch` directly.

**Laptop-side commands ship as stubs.** `solx init`, `solx up`, `solx
down`, `solx forward`, and `solx info` (top-level, distinct from
`solx session info`) all exit 2 with a deferral message. For the
laptop ↔ Sol manual flow (which gives you the same end state today),
see the "Sessions and Tunneling" reference that ships with the
`sol-skill` agent skill.

## Implementation notes

- Config: `~/.config/solx/profiles.toml` (or `$XDG_CONFIG_HOME/solx/profiles.toml`).
- State: `~/.local/share/solx/session.json` (or `$XDG_STATE_HOME/solx/session.json`).
- Side detection: `hostname -a` parsed for any token ending in
  `.sol.rc.asu.edu`. Falls back to `socket.getfqdn()`.
- `solx` never reads `~/.ssh/*`. The Sol-side flow doesn't invoke
  `ssh` at all — sbatch and squeue do their own thing inside the
  cluster.
- Default polling: every 2s, capped at 10 minutes. Override with the
  internal `wait_timeout` / `poll_interval` knobs (currently
  Python-only — no CLI flag yet).

## Testing and verification

Unit tests cover everything except the actual cluster round-trip:

```shell
cd solx
uv sync
uv run pytest
```

For the manual end-to-end smoke on Sol, see
[`solx-smoke.md`](solx-smoke.md).
