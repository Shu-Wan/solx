# Roadmap: optional `solx` CLI

Forward-looking design doc for **`solx`**, an optional one-command
CLI that would collapse the laptop ↔ Sol dev loop (allocate compute
node + tunnel localhost ports back) into a single invocation. Not
part of any released version. The skill (shipped) covers the manual
path; `solx` would be an additive convenience layer.

End-user docs: [`../README.md`](../README.md),
[`../skills/sol-skill/SKILL.md`](../skills/sol-skill/SKILL.md).
Contributor / harness docs: [`../DEVELOPMENT.md`](../DEVELOPMENT.md),
[`coverage.md`](coverage.md). Released history:
[`../CHANGELOG.md`](../CHANGELOG.md).

## Stages

| Stage | Outcome |
|---|---|
| 1 — Skill manual-SSH path | Shipped in v0.1.0 (see CHANGELOG). |
| 2 — `solx` CLI package | `solx/` standalone package, `uv tool install`-able, every mutating subcommand supports `--dry-run`. Designed below. |
| 3 — Skill ↔ `solx` integration | Add `command -v solx` detection branch into SKILL.md; populate `references/solx.md` with the one-command flow. Depends on Stage 2. |

## Why a `solx` CLI

The current dev loop on Sol from a laptop, even with the v0.1.0
skill teaching the right commands:

1. SSH into Sol → start `interactive` (or `vscode`).
2. Note the compute node hostname and the server port.
3. Open a second terminal on the laptop and craft an `ssh -L … -J …`
   chain.

Two terminals, manual state copying, easy to misread (e.g., the user
runs `ssh -L` from the compute node by mistake because the prompt
looks the same). Open OnDemand handles the casual notebook case
without any of this fiddliness; `solx` would handle the
terminal-driven power-user case by collapsing all three steps into
`solx up <profile>` from the laptop.

## Vision constraints

Carry-over from earlier planning. Still valid:

- Agent-first CLI; follow Python ecosystem standards.
- Skill stays light; `solx` lives outside `skills/sol-skill/`.
- `solx` is a prerequisite for the dev-workflow part of the skill,
  but **optional** — manual SSH fallback documented and supported in
  perpetuity.
- Sol-side config supports multiple named profiles (gpu/debug/etc.).
- Output personalized with `$(whoami)` / `$USER`, never `<asurite>`
  placeholders.
- Strong situational awareness in the skill — once `solx` ships, the
  skill detects it and branches accordingly.
- **Security**: published skill must not snoop `~/.ssh/config` or
  `~/.ssh/known_hosts`. First-time setup must be explicit.

## Repo layout (target after Stages 2 + 3)

```text
sol-skill/
├── README.md                       # would gain a solx install + intro section
├── docs/
├── skills/sol-skill/
│   ├── SKILL.md                    # would gain `command -v solx` detection branch (Stage 3)
│   ├── scripts/sol_renew.py
│   └── references/
│       ├── module.md, scratch.md, sharing.md, slurm.md, sessions.md  # already shipped
│       └── solx.md                 # NEW (Stage 3) — solx-driven workflow
└── solx/                           # NEW (Stage 2) — the CLI package
    ├── pyproject.toml
    ├── README.md
    ├── src/solx/
    │   ├── __init__.py
    │   ├── __main__.py             # `python -m solx`
    │   ├── cli.py                  # Typer root + dispatch by side
    │   ├── side.py                 # detect sol vs laptop (sacctmgr-based)
    │   ├── config.py               # TOML profile loading
    │   ├── session.py              # session.json read/write
    │   ├── ssh.py                  # build ssh -L -J commands; no ~/.ssh/* reads
    │   ├── sol_cmds.py             # session start/info/stop, config init
    │   └── laptop_cmds.py          # init, up/down/forward/info
    └── tests/                      # pytest, mock subprocess
```

Distribution: **`uv tool install solx`** (path or git URL — same
repo, since the skill and CLI version together).

CLI stack: **Typer + Rich**. Add **Textual** only if a specific
subcommand grows real TUI needs (e.g., a session picker with live job
status); default to simple Typer prompts.

## `solx` design

### Side detection (`solx where`)

Use the same SLURM-side signals the v0.1.0 skill teaches (see
`SKILL.md` "Detecting the Environment"):

1. `command -v sacctmgr` — empty → not on a Slurm cluster → must be
   laptop side.
2. `sacctmgr -n show cluster format=cluster` — `sol` → Sol;
   anything else → wrong cluster, exit non-zero with a clear
   message.
3. `$SLURM_JOB_ID` — set → already inside an allocation (warn:
   `solx session start` from inside an allocation is almost always a
   mistake).

Subcommands relevant to the wrong side print a clear redirect (no-op,
exit 2) instead of failing obscurely.

### Config (multi-profile, user-editable)

**Sol side**: `~/.config/solx/profiles.toml`

```toml
[shared]
# Common sbatch/srun options applied to every profile below.
# Scalars (partition, qos, time) are overridden by per-profile values;
# lists (forward, srun_args) are appended — shared first, then profile.
qos = "public"
srun_args = [
  "--mail-type=TIME_LIMIT_90,END,FAIL",
  "--mail-user=swan16@asu.edu",
]

[default]
# Lightweight default — matches the `interactive` wrapper's defaults
# (htc partition, public QOS) which the SKILL teaches as the right
# choice for short, exploratory work.
kind = "vscode"        # vscode | bare | sbatch-script
partition = "htc"
time = "0-4"
forward = [8888]

[gpu]
kind = "bare"
partition = "general"
gres = "gpu:a100:1"
time = "0-4"
forward = [8888, 6006]  # jupyter + tensorboard
srun_args = ["--mem=64G", "--cpus-per-task=8"]

[debug]
kind = "bare"
partition = "htc"
time = "0-1"
forward = [8000, 8888]
```

`solx config init` drops a starter file with these three profiles
commented as examples. User edits freely.

**Laptop side**: `~/.config/solx/laptop.toml`

```toml
host = "sol.asu.edu"     # what to ssh to. User picks during `solx init`.
user = "swan16"          # filled from `whoami` if not overridden during init
default_profile = "default"

[shared]
# Applied to every `solx up` / `down` / `forward` / `info` invocation.
ssh_args = ["-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3"]
```

No assumption about ssh aliases, no reading of `~/.ssh/*`. If the
user wants a custom alias (`Host mysol` in their ssh config), they
put `host = "mysol"` here.

### Argument passthrough

Profile fields `srun_args` and `ssh_args` are arrays passed verbatim
to `srun` / `ssh`. `solx` does not validate them — typos surface as
native srun/ssh errors, the right behavior for a thin wrapper.

`[shared]` is for options you'd otherwise repeat in every profile
(canonical example: mail notifications). CLI escape hatch: anything
after `--` on `solx up` or `solx session start` is appended to the
underlying srun command, overriding profile `srun_args` for that one
run:

```shell
solx up gpu -- --mem=128G --time=8:00:00
```

### Subcommands

#### Universal

- `solx where` — print side + relevant context
- `solx config show` — print resolved config
- `solx --version`, `solx --help`

#### Sol side (run after `ssh sol`)

- `solx session start [PROFILE]` — runs `srun`/`salloc` per profile,
  writes `session.json` with `{node, job_id, profile, ports,
  started_at, kind}`. For `kind=vscode`, wraps the existing
  `/usr/local/bin/vscode` so its tunnel + a session record both
  exist.
- `solx session info [--json]` — read `session.json`
- `solx session stop` — `scancel $job_id` + remove `session.json`
- `solx config init [--force]`

#### Laptop side

- `solx init` — first-time interactive setup (see Security below)
- `solx up [PROFILE]` — composite: SSH to Sol, runs `solx session
  start PROFILE` remotely, polls `~/.local/share/solx/session.json`
  until populated, then opens `ssh -L … -J … <user>@<host>
  <user>@<node>` for each forwarded port. Drops user into either a
  remote shell or just leaves tunnels open in foreground (user picks
  via `--shell` / `--background`).
- `solx down` — SSHes in, runs `solx session stop`
- `solx forward PORT [PORT…]` — adds extra tunnels to the running
  session (uses `ssh -O forward` against the existing ControlMaster
  socket)
- `solx info` — `ssh <host> solx session info --json` then
  pretty-print

### State sharing (no extra protocol)

Sol's `$HOME` is shared between login and compute nodes. The compute
node writes `~/.local/share/solx/session.json`; the login node sees
it; the laptop reads it via `ssh <host> cat
~/.local/share/solx/session.json`. No daemon, no socket, no extra
surface.

### `--dry-run` everywhere

`solx up --dry-run` prints the SSH commands it would run without
executing — addresses the "agent ran something I didn't expect"
concern, and gives the user a copy-paste fallback identical to the
manual route.

## Security model

**Never read `~/.ssh/config`, `~/.ssh/known_hosts`, or any key
material.** The CLI builds ssh commands from `solx config` only; the
user's ssh client handles auth, host-key verification, and
ControlMaster sockets natively.

**First-time setup (`solx init` on the laptop)**:

1. Prompts for SSH host (default `sol.asu.edu`).
2. Prompts for username (default `$(whoami)` — works for users whose
   laptop and Sol usernames match).
3. Runs `ssh -o BatchMode=yes -o ConnectTimeout=5 <user>@<host>
   hostname` to test reachability. Reports clearly if it fails
   (likely: "no key set up; you'll be prompted for password + Duo on
   first real connection — that's expected").
4. **Suggests** (does not write) a `~/.ssh/config` snippet for
   ControlMaster speedup. User copies it in if they want.
5. Writes `~/.config/solx/laptop.toml` with mode 0600.

**No secrets in state**: `session.json` contains node, job_id,
profile name, port numbers, timestamp. Nothing requiring protection
beyond the standard `$HOME` permissions Sol already enforces.

**Auth flow**: Duo + password (or key) handled by the user's `ssh`
invocations transparently. `solx up` may prompt 2–3 times during a
single command. Document this; recommend ControlMaster.

**Agent safety**: every command that mutates remote state (`up`,
`down`, `forward`) supports `--dry-run` and prints the underlying
SSH command. The skill instructs the agent to dry-run first when the
user hasn't explicitly approved the action.

## Stage 3: when `solx` ships, the skill needs this

Once Stage 2 is usable end-to-end, the SKILL's "Using a Service That
Runs on Sol, From Your Laptop" section gains a third path: detect
`solx` on the laptop side, prefer the one-command flow if present,
fall back to the manual SSH chain if not. Concretely:

- Add a `command -v solx` check to the section opener.
- Add a new `references/solx.md` walking through `solx init`, `solx
  up <profile>`, `solx forward`, and `solx down` with worked
  examples.
- Update `docs/coverage.md` to add a `solx`-detection-branch row in
  the Sessions section (would flip the existing ⚪ roadmap row to
  🟡 documented or 🟢 tested).
- Bump the skill version (frontmatter `version`) and add a
  `CHANGELOG.md` entry describing the new branch.

## Files to create / modify (Stages 2 + 3)

| Path | Stage | Action |
|---|---|---|
| `solx/pyproject.toml` | 2 | NEW — Typer + Rich; entry point `solx = "solx.cli:app"`; uses stdlib `tomllib` (Python 3.11+) |
| `solx/src/solx/cli.py` | 2 | NEW — Typer root, dispatches by `side.detect()` |
| `solx/src/solx/side.py` | 2 | NEW — `detect()` uses `sacctmgr` and `$SLURM_JOB_ID`, returns `"sol-login"` / `"sol-compute"` / `"laptop"` |
| `solx/src/solx/config.py` | 2 | NEW — Load/save TOML; profile resolution |
| `solx/src/solx/session.py` | 2 | NEW — `~/.local/share/solx/session.json` r/w |
| `solx/src/solx/ssh.py` | 2 | NEW — Build `ssh -L -J …` commands; never reads `~/.ssh/*` |
| `solx/src/solx/sol_cmds.py` | 2 | NEW — `session start/info/stop`, wraps `/usr/local/bin/vscode` for `kind=vscode` |
| `solx/src/solx/laptop_cmds.py` | 2 | NEW — `init`, `up`, `down`, `forward`, `info` |
| `solx/tests/` | 2 | NEW — pytest with subprocess mocked; covers config parse, profile resolution, ssh-command construction, side detection |
| `solx/README.md` | 2 | NEW — install, first-run, security notes, examples |
| `skills/sol-skill/SKILL.md` | 3 | MODIFY — add `command -v solx` branch to "Using a Service…" section |
| `skills/sol-skill/references/solx.md` | 3 | NEW — `solx`-driven workflow walkthrough |
| `README.md` (root) | 3 | MODIFY — mention `solx` install path alongside the skill install |
| `docs/coverage.md` | 3 | MODIFY — add solx-related rows |
| `CHANGELOG.md` | 3 | MODIFY — describe the new branch + skill version bump |

Reuse from existing code: the patterns from `skills/sol-skill/scripts/sol_renew.py` (PEP 723 shebang, argparse/Rich layout, exit-code conventions). Mirror those choices in `solx` even though it's a real package — same exit codes (`0` ok / `1` failure / `2` conditional), same "preview first" `--dry-run` ethos.

## Decisions confirmed

- **CLI framework**: Typer + Rich. Textual reserved for any subcommand that genuinely needs TUI; not adopted up-front.
- **`solx up` default**: drops user into a remote shell on the compute node after tunnels are open (matches `vscode`/`interactive` mental model). Add `--no-shell` for tunnels-only and `--background` for ControlMaster-detached operation.
- **Repo**: same repo, framed as a skill-primary project with `solx` as a CLI add-on. Install `solx` via `uv tool install git+https://github.com/Shu-Wan/sol-skills.git#subdirectory=solx`. Repo rename out of scope.
- **VSCode tunnel integration**: `solx session start` with `kind=vscode` wraps `/usr/local/bin/vscode` rather than reimplementing it. Preserves muscle memory and any future ASU changes to the wrapper.
