# Plan: agent-first dev workflow for Sol via `solx` CLI

## Context

Today `sol-skill` covers `/scratch` renewal and Slurm/module conventions. It does not address the live developer loop on Sol — starting an interactive job, tunneling a port back to the laptop, and surviving the network in between. The current workaround requires the user to manually:

1. SSH into Sol → start `interactive` or `vscode`
2. Note the compute node hostname and the server port
3. Open a second terminal on the laptop and craft an `ssh -L … -J …` chain

Two terminals, manual state copying, easy to misread (e.g., the user just ran `ssh -L` from the compute node by mistake because their prompt looked the same).

This plan introduces **`solx`**, a Python CLI that becomes the primary product of this repo. The agent skill is reframed as ancillary — it teaches an AI assistant how to drive `solx` (and how to fall back to manual SSH if `solx` is not installed). `solx` is fully usable on its own, with or without an agent. The user's mental model — "I want to work on Sol today, from my laptop" — becomes one command.

Vision constraints from the user (verbatim intent):

- Agent-first CLI; follow Python ecosystem standards.
- Skill stays light; `solx` lives outside `skills/sol-skill/`.
- `solx` is a prerequisite for the dev-workflow part of the skill, but **optional** — manual SSH fallback documented.
- Sol-side config supports multiple named profiles (gpu/debug/etc.).
- Output personalized with `$(whoami)` / `$USER`, not `<asurite>` placeholders.
- Strong situational awareness in the skill — detect whether `solx` is installed, branch accordingly.
- **Security**: published skill must not snoop `~/.ssh/config` or `~/.ssh/known_hosts`. First-time setup must be explicit.

---

## Stages

Work ships in three phases. **Stage 1 and Stage 2 run in parallel** — the skill update is independent of `solx` existing, so it lands first or alongside the CLI rather than waiting on it. This file is the master plan; each stage has a dedicated sub-plan with its own success criteria and testing checklist.

### Stage 1 — Skill manual-SSH path (situational-aware)

Ship the skill changes that work *without* `solx` installed. The skill gains a "Sessions and Tunneling" section that branches on `command -v solx`; with `solx` absent, it walks the user (or agent) through the manual `ssh -L … -J …` chain. After Stage 1, the workflow is documented and usable immediately — `solx` is purely additive from here on.

Deliverables: `references/sessions.md` (manual flow, with `$(whoami)` substitution baked in), `SKILL.md` updated with the detection branch, `references/solx.md` as a stub pointing at Stage 3.

→ Sub-plan: [stage-1-skill.md](stage-1-skill.md)

### Stage 2 — `solx` CLI package (parallel track)

Build the `solx/` package independently of the skill. No skill dependency yet — `solx` is usable from the terminal alone for any human user. Verification is local to `solx/` (`uv run pytest`, plus the Sol- and laptop-side smoke checklists in the sub-plan).

Deliverables: full `solx/` tree per the layout below, installable via `uv tool install`, every mutating subcommand supports `--dry-run`.

→ Sub-plan: [stage-2-solx.md](stage-2-solx.md)

### Stage 3 — Skill ↔ `solx` integration

Once Stage 2 is usable end-to-end, fill in `references/solx.md` with the one-command flow and tighten `SKILL.md`'s detection branch to prefer `solx` when present. Stage 1's manual fallback stays as the second branch — it never goes away.

Deliverables: completed `references/solx.md`, root `README.md` reframed to lead with `solx` as the primary product.

→ Sub-plan: [stage-3-integration.md](stage-3-integration.md)

---

## Repo layout (new)

```text
sol-skill/
├── README.md                       # MODIFY — document solx alongside sol_renew
├── docs/                           # working/helper docs (this PLAN.md, design notes); not shipped with the skill
├── skills/sol-skill/
│   ├── SKILL.md                    # MODIFY — add "Sessions & Tunneling" section
│   ├── scripts/sol_renew.py        # unchanged
│   └── references/
│       ├── module.md, scratch.md, sharing.md, slurm.md  # unchanged
│       ├── sessions.md             # NEW — manual SSH fallback (no solx)
│       └── solx.md                 # NEW — solx-driven workflow
└── solx/                           # NEW — the CLI package, separate from the skill
    ├── pyproject.toml
    ├── README.md
    ├── src/solx/
    │   ├── __init__.py
    │   ├── __main__.py             # `python -m solx`
    │   ├── cli.py                  # Typer root + dispatch by side
    │   ├── side.py                 # detect sol vs laptop (hostname)
    │   ├── config.py               # TOML profile loading
    │   ├── session.py              # session.json read/write
    │   ├── ssh.py                  # build ssh -L -J commands; no ~/.ssh/* reads
    │   ├── sol_cmds.py             # session start/info/stop, config init
    │   └── laptop_cmds.py          # init, up/down/forward/info
    └── tests/                      # pytest, mock subprocess
```

Distribution: **`uv tool install solx`** (from a path or git URL — same repo, since the skill is ancillary to the CLI and they should version together). Standard ecosystem entry-point — no symlink dance, no PATH guesswork. The existing `sol_renew.py` PEP 723 pattern stays appropriate for a one-file utility; `solx` is multi-file with subcommands and a TOML config layer, so a real package is the right shape.

CLI stack: **Typer + Rich**. Add **Textual** only if a specific subcommand grows real TUI needs (e.g., a session picker with live job status) — defer until then; default to simple Typer prompts.

Framing: the README and repo presentation lead with `solx`. The agent skill becomes a short companion piece that says "use `solx` when present; here's the manual SSH chain otherwise." `solx` works standalone for any human user, with or without an agent.

---

## `solx` design

### Side detection (`solx where`)

Reuse the SKILL.md hostname pattern: `hostname -a` matching `*.sol.rc.asu.edu` → Sol mode; otherwise laptop mode. Subcommands relevant to the wrong side print a clear redirect (no-op, exit 2) instead of failing obscurely.

### Config (multi-profile, user-editable)

**Sol side**: `~/.config/solx/profiles.toml`

```toml
[shared]
# Common sbatch/srun options applied to every profile below.
# Scalars (partition, qos, time) are overridden by per-profile values;
# lists (forward, srun_args) are appended — shared first, then profile.
# This is the place to put options you'd otherwise repeat in every profile.
qos = "public"
srun_args = [
  "--mail-type=TIME_LIMIT_90,END,FAIL",   # email at 90% time used, on completion, on failure
  "--mail-user=swan16@asu.edu",
]

[default]
kind = "vscode"        # vscode | bare | sbatch-script
partition = "lightwork"
time = "1-0"
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

`solx config init` drops a starter file with these three profiles commented as examples. User edits freely.

**Laptop side**: `~/.config/solx/laptop.toml`

```toml
host = "sol.asu.edu"     # what to ssh to. User picks during `solx init`.
user = "swan16"          # filled from `whoami` if not overridden during init
default_profile = "default"

[shared]
# Applied to every `solx up` / `down` / `forward` / `info` invocation.
ssh_args = ["-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3"]
```

No assumption about ssh aliases, no reading of `~/.ssh/*`. If the user wants to use a custom alias (`Host mysol` in their ssh config), they put `host = "mysol"` here.

### Argument passthrough

Profile fields `srun_args` and `ssh_args` are arrays passed verbatim to `srun` / `ssh`. `solx` does not validate them — typos surface as native srun/ssh errors, which is the right behavior for a thin wrapper.

The `[shared]` table in either config is the place to put **common sbatch/srun options** (or ssh options) that you'd otherwise repeat in every profile. The canonical example is mail notifications — most users want the same notification policy across `default`, `gpu`, and `debug`, so `--mail-type=TIME_LIMIT_90,END,FAIL` and `--mail-user=...` belong in `[shared]`, not in each profile. Same idea on the laptop side for `ssh -o ServerAliveInterval=...`.

CLI escape hatch: anything after `--` on a `solx up` or `solx session start` invocation is appended to the underlying srun command, overriding profile `srun_args` for that one run (`[shared]` `srun_args` still apply unless explicitly displaced by the same flag):

```shell
solx up gpu -- --mem=128G --time=8:00:00
```

### Subcommands

#### Universal

- `solx where` — print side + relevant context (compute node, or laptop hostname)
- `solx config show` — print resolved config
- `solx --version`, `solx --help`

#### Sol side (run after `ssh sol`)

- `solx session start [PROFILE]` — runs `srun`/`salloc` per profile, writes session.json with `{node, job_id, profile, ports, started_at, kind}`. For `kind=vscode`, wraps the existing `/usr/local/bin/vscode` so its tunnel + a session record both exist.
- `solx session info [--json]` — read session.json
- `solx session stop` — `scancel $job_id` + remove session.json
- `solx config init [--force]`

#### Laptop side

- `solx init` — first-time interactive setup (see Security)
- `solx up [PROFILE]` — composite: SSH to Sol, runs `solx session start PROFILE` remotely, polls `~/.local/share/solx/session.json` until populated, then opens `ssh -L … -J … <user>@<host> <user>@<node>` for each forwarded port. Drops user into either a remote shell or just leaves tunnels open in foreground (user picks via `--shell` / `--background`).
- `solx down` — SSHes in, runs `solx session stop`
- `solx forward PORT [PORT…]` — adds extra tunnels to the running session (uses `ssh -O forward` against the existing ControlMaster socket)
- `solx info` — `ssh <host> solx session info --json` then pretty-print

### State sharing (no extra protocol)

Sol's `$HOME` is shared between login and compute nodes. The compute node writes `~/.local/share/solx/session.json`; the login node sees it; the laptop reads it via `ssh <host> cat ~/.local/share/solx/session.json`. No daemon, no socket, no extra surface.

### `--dry-run` everywhere

`solx up --dry-run` prints the SSH commands it would run without executing — addresses the "agent ran something I didn't expect" concern, and gives the user a copy-paste fallback identical to the manual route.

---

## Security model

**Never read `~/.ssh/config`, `~/.ssh/known_hosts`, or any key material.** The CLI builds ssh commands from `solx config` only; the user's ssh client handles auth, host-key verification, and ControlMaster sockets natively.

**First-time setup (`solx init` on the laptop)**:

1. Prompts for SSH host (default `sol.asu.edu`).
2. Prompts for username (default `$(whoami)` — works for users whose laptop and Sol usernames match).
3. Runs `ssh -o BatchMode=yes -o ConnectTimeout=5 <user>@<host> hostname` to test reachability. Reports clearly if it fails (likely: "no key set up; you'll be prompted for password + Duo on first real connection — that's expected").
4. **Suggests** (does not write) a `~/.ssh/config` snippet for ControlMaster speedup. User copies it in if they want.
5. Writes `~/.config/solx/laptop.toml` with mode 0600.

**No secrets in state**: `session.json` contains node, job_id, profile name, port numbers, timestamp. Nothing requiring protection beyond the standard `$HOME` permissions Sol already enforces.

**Auth flow**: Duo + password (or key) handled by the user's `ssh` invocations transparently. `solx up` may prompt 2–3 times during a single command (one for the control connection, possibly more if ControlMaster isn't set up). Document this; recommend ControlMaster.

**Agent safety**: every command that mutates remote state (`up`, `down`, `forward`) supports `--dry-run` and prints the underlying SSH command. The skill instructs the agent to dry-run first when the user hasn't explicitly approved the action.

---

## Skill updates (situational awareness)

`skills/sol-skill/SKILL.md` gains a new section, **Sessions & Tunneling**, that branches on `solx` availability:

```markdown
## Sessions and Tunneling

To work on Sol from a laptop, you need (a) a running Slurm job and
(b) SSH tunnels back to the laptop for any localhost server (Jupyter,
dev server, OAuth callback).

**Detect the tooling first.** Run `command -v solx` on the side you're
operating from.

- If `solx` is available → see [references/solx.md](references/solx.md)
  (one-command flow).
- If not → see [references/sessions.md](references/sessions.md)
  (manual `ssh -L … -J …`).

**Always personalize examples.** Substitute `$(whoami)` for the
username — never emit `<asurite>` or other placeholders to the user.
```

`references/solx.md` — full `solx` workflow doc (config profiles, examples, troubleshooting).
`references/sessions.md` — pure-SSH manual flow with `whoami` substitution baked in. Pulls forward the working examples from this conversation (chained tunnel, `-J` ProxyJump, OAuth callback, port-already-in-use diagnosis).

---

## Files to create / modify

| Path | Action | Notes |
| --- | --- | --- |
| `solx/pyproject.toml` | NEW | Typer + Rich; entry point `solx = "solx.cli:app"`; uses stdlib `tomllib` (Python 3.11+) |
| `solx/src/solx/cli.py` | NEW | Typer root, dispatches by `side.detect()` |
| `solx/src/solx/side.py` | NEW | `detect()` reads `hostname`, returns `"sol"` or `"laptop"` |
| `solx/src/solx/config.py` | NEW | Load/save TOML; profile resolution |
| `solx/src/solx/session.py` | NEW | `~/.local/share/solx/session.json` r/w |
| `solx/src/solx/ssh.py` | NEW | Build `ssh -L -J …` commands; never reads `~/.ssh/*` |
| `solx/src/solx/sol_cmds.py` | NEW | `session start/info/stop`, wraps `/usr/local/bin/vscode` for `kind=vscode` |
| `solx/src/solx/laptop_cmds.py` | NEW | `init`, `up`, `down`, `forward`, `info` |
| `solx/tests/` | NEW | pytest with subprocess mocked; covers config parse, profile resolution, ssh-command construction, side detection |
| `solx/README.md` | NEW | Install, first-run, security notes, examples |
| `skills/sol-skill/SKILL.md` | MODIFY | Add "Sessions and Tunneling" section with conditional branch |
| `skills/sol-skill/references/solx.md` | NEW | solx-driven workflow doc |
| `skills/sol-skill/references/sessions.md` | NEW | Manual SSH fallback doc (consolidates this conversation's working commands) |
| `README.md` (root) | MODIFY | Reframe to lead with `solx` as the primary product; demote sol-skill agent skill to a "companion" section; updated layout tree |

Reuse from existing code: the `rich` patterns and CLI ergonomics from `skills/sol-skill/scripts/sol_renew.py:1-100` (PEP 723 shebang, argparse layout, exit-code conventions). Mirror those choices in `solx` even though it's a real package — same exit codes (0 ok / 1 failure / 2 conditional), same "preview first" `--dry-run` ethos.

---

## Testing plan

Detailed checklists live with each sub-plan, alongside that stage's success criteria. High-level shape:

| Stage | Side(s) | Where the checklist lives |
| --- | --- | --- |
| 1 | Skill (no `solx` required) | [stage-1-skill.md](stage-1-skill.md#testing-checklist) |
| 2 | Sol + laptop (`solx` standalone) | [stage-2-solx.md](stage-2-solx.md#testing-checklist) |
| 3 | Skill + `solx` integrated | [stage-3-integration.md](stage-3-integration.md#testing-checklist) |

A change in any sub-plan that affects shared design (config schema, side detection, security model) should be reflected back here so this master plan doesn't drift.

---

## Decisions confirmed

- **CLI framework**: Typer + Rich. Textual reserved for any subcommand that genuinely needs TUI; not adopted up-front.
- **`solx up` default**: drops user into a remote shell on the compute node after tunnels are open (matches `vscode`/`interactive` mental model). Add `--no-shell` for tunnels-only and `--background` for ControlMaster-detached operation when explicitly requested.
- **Repo**: same repo, framed as a CLI-primary project with the skill as an ancillary. Install via `uv tool install git+https://github.com/Shu-Wan/sol-skills.git#subdirectory=solx`. Repo rename is out of scope for this plan.
- **VSCode tunnel integration**: `solx session start` with `kind=vscode` wraps `/usr/local/bin/vscode` rather than reimplementing it. Preserves existing muscle memory and any future ASU changes to the wrapper.
