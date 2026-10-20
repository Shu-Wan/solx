# 🤖 `sol-agent` - user manual

`sol-agent` keeps one Claude Code session running on Sol through preemption, job time limits and
maintenance windows, and reachable from claude.ai/code or your phone (Remote Control). It can also
start Sol's own VS Code tunnel on demand and hand you the GitHub device code.

It ships with `solx` but is separate from it: `solx` is a compiled CLI for interactive jobs and scratch
renewal; `sol-agent` is a set of Bash scripts that run as ordinary Slurm jobs. `solx agent` is the front
door - it installs and updates the scripts and forwards commands to them. Internals, measurements and
the failure matrix are in [`sol-agent/DESIGN.md`](../sol-agent/DESIGN.md).

## What it is, and what it is not

Two layers:

1. **A supervisor (no Claude involved).** Two 1-CPU / 2 GB jobs - one active, one warm standby on a
   different node when the first is already running - plus a 5-minute scrontab watchdog and 20-second heartbeats on NFS. When the active job
   dies the standby takes the lease and resumes the same conversation (`claude --resume`). Claude cannot
   do this for itself, because it dies with its job.
2. **Tools Claude calls:** `status`, `logs`, `msg`, `tunnel`. The always-on session uses them, and so does
   any normal Claude session through the [`sol-agent` skill](../skills/sol-agent/SKILL.md).

It is not a scheduler for your compute work. The session is a small coordinator: it submits real jobs
with `sbatch` and watches them. Heavy work, datasets and large outputs belong in Slurm jobs and
`/scratch/$USER`.

## Requirements

On a Sol login node: `claude` (Claude Code), `tmux`, `jq`, Slurm. Nothing to build.
The login node only submits and checks jobs; the agent itself never runs there.

## Install

```shell
solx agent install            # writes ~/.solx/agent (or $SOL_AGENT_ROOT); -n previews
```

The folder is hidden, like other tools' per-user data (`ls -a ~/.solx`). It holds the scripts, `config.env`, the
workspace, state and logs (about 12 MB); `solx`'s own config stays in `~/.config/solx`.

No `solx`? Clone the repo and copy the folder (the skill is installed separately, below):

```shell
git clone --depth 1 https://github.com/Shu-Wan/solx.git /tmp/solx-src
mkdir -p ~/.solx && cp -r /tmp/solx-src/sol-agent ~/.solx/agent
```

Optional - let Claude sessions know how to drive it:

```shell
gh skill install Shu-Wan/solx sol-agent
```

`solx agent install` does not touch `~/.claude`, so the skill is a separate, explicit step.

## Start it

1. **Trust the workspace, once.** Claude stores "I trust this folder" per directory, and a headless
   session cannot click through the prompt:
   ```shell
   cd ~/.solx/agent/workspace && claude     # accept the trust prompt, then /exit
   ```
   `sol-agent status` shows `workspace trust: OK` afterwards. The directory is `WORKDIR` in
   `config.env`; it must stay the same across restarts because Claude finds a conversation by directory.
2. **Pick the permission mode.** `up` copies the mode of the Claude session that runs it, which is what
   lets that session message the agent with `SendMessage`. To pin a stricter mode regardless, set
   `PERMISSION_MODE=acceptEdits` in `config.env`.
3. **Set up alerts.** Nothing else tells you when the pool is down, so give the watchdog a way to reach you. In `config.env`:
   ```shell
   ALERT_EMAIL=you@example.com     # Slurm's own job mail, subject only; ASU addresses are <asurite>@asu.edu
   NTFY_TOPIC=<long-random-name>   # or phone push through ntfy.sh (free app, no account); anyone who knows the name can read it
   ```
   then `solx agent alert test` sends one message through every channel and says which took it. "Took it" is not "arrived":
   the e-mail comes when its 1-minute job ends, so check your inbox (and spam). `solx agent up` warns while no channel is set.
   Why Slurm job mail: Sol's compute nodes cannot send mail themselves (their `sendmail` relays to `127.0.0.1`, where nothing
   listens, so `mail` reports success and the message dies in a local queue). The controller can, so an alert is a throwaway
   1-second job on `general`/`private` that e-mails when it ends. The e-mail is Slurm's "Job N Ended" message and the alert
   is its `Job Name :` line (`sol-agent DOWN: no active session for 12 min`); nothing else. Because it is a job it waits in
   the queue like any job: seconds when Sol is quiet, several minutes at peak. ntfy and `NOTIFY_CMD` are sent straight from the
   running process, so they are immediate and also carry the pool summary. Use ntfy if you want the alert the moment it happens.
   What you get (the watchdog checks every 5 minutes): **DOWN** when no session has been active for `ALERT_DOWN_MIN`
   (10), **STILL DOWN** every `ALERT_REPEAT_H` (6) hours, **recovered** when it is back, and **no standby** after
   `ALERT_DEGRADED_MIN` (30). A normal failover takes seconds and sends nothing (set `NOTIFY_LEVEL=all` to hear about
   those too). Pausing with `down` and maintenance waits are expected and never alert.
   For the case where the watchdog itself is gone (its scrontab entry lost, Sol unreachable), add a `PING_URL` from a
   dead-man's-switch service such as healthchecks.io: it is pinged on every watchdog run while the pool is fine and at
   `<url>/fail` while a DOWN alert stands, so silence from either side reaches you.
4. **Start:**
   ```shell
   solx agent up
   solx agent status       # jobs RUNNING with a heartbeat; a `sol-agent` entry in Remote Control
   ```
   `up` installs the watchdog in your scrontab (it only adds its own marked block, and refuses to touch the
   table if it cannot read it) and queues the first job. The warm standby is queued by the watchdog on its next
   run (within about 5 minutes), away from the node the first job is on. If you are unsure whether Research
   Computing is fine with two idle 1-CPU allocations, ask them first.

Where you run `up` from does not matter. The scripts use absolute paths and start Claude in `WORKDIR`.

## Give it work

No prompt is needed to start it. Two prompts are built in: the standing instructions in
`workspace/CLAUDE.md` (loaded automatically) and a start/resume prompt passed at launch. With only those
the agent starts, arms its mailbox watcher and idles. Work reaches it through:

- the Remote Control chat (claude.ai/code or the phone app, session name `sol-agent`);
- a file message: `solx agent msg send sol-agent -s "subject" "text"`;
- `workspace/HANDOFF.md`, which it reads on every start;
- standing duties you write into `workspace/CLAUDE.md`.

Edit `workspace/CLAUDE.md` to change its standing behavior. It is yours: updates never overwrite it.

## Commands

| Command | What it does |
|---|---|
| `solx agent install [--dir D] [-n] [-f]` | Install or update the scripts. |
| `solx agent up [-n] [--force]` | Start (and install the watchdog). Refuses (exit 2) while the workspace is untrusted. |
| `solx agent down [-y\|-n]` · `resume` | Pause and close cleanly / continue. |
| `solx agent status` · `logs [N]` | Jobs, lease, heartbeats, pool health, alert channels, model, watchdog, maintenance plan, trust, permission mode, shipped defaults waiting beside your edited files / event log. |
| `solx agent alert test` | Send one test message through every alert channel (exit 1 if one fails or none is set). |
| `solx agent tunnel start\|stop [-y\|-n]\|status\|attach` | VS Code tunnel `sol_$USER`; prints a device code when a new machine must log in. |
| `solx agent msg send\|read\|list\|watch` | Plain-text file mailbox for other Claude sessions. |
| `solx agent gc [-y\|-n] [--tests] [--sessions]` | Collect what sol-agent opened; `-n` previews, `-y` does it. |
| `solx agent wd-install [-n]` · `wd-remove [-y\|-n]` | Add / remove only sol-agent's scrontab block. |
| `solx agent help [command]` | Usage for the forwarded commands. |

Everything after `solx agent` except `install` is forwarded to `~/.solx/agent/bin/sol-agent`, so
`~/.solx/agent/bin/sol-agent status` is the same thing. Use `SOL_AGENT_ROOT` for a non-default location: an
absolute path without spaces, exported whenever you run the scripts; `workspace/CLAUDE.md` names `~/.solx/agent`
paths, so edit them for another location.

### Output, confirmation and exit codes

The commands follow the same rules as the rest of `solx`, so a script or an agent can drive them without special cases:

- **JSON when piped.** `status`, `up`, `down`, `resume`, `gc`, `wd-*`, `tunnel start|stop|status` and
  `msg send|read|list` print one JSON document on stdout when stdout is not a terminal. `--json` forces it (before or after
  the command), `--text` forces the text view. In JSON mode the human-readable lines go to stderr.
  `solx agent status | jq '.jobs[].state'` works the way `solx job list | jq` does.
- **Streams stay text.** `logs`, `msg watch` and `msg hook` print text unless you pass `--json`, then one JSON object per line.
- **Preview, then confirm.** `down`, `gc`, `wd-remove` and `tunnel stop` take `-n` (show the plan, change nothing) and
  `-y` (do it). On a terminal, with neither, they ask `[y/N]`. Without a terminal they refuse with exit 2 instead of hanging.
  `-y` and `-n` together is exit 2. Cleanup with nothing to clean is exit 0.
- **Exit codes.** `0` done · `1` failed · `2` usage error or unconfirmed · `3` waiting for you (`tunnel start` while a GitHub
  device code is unapproved; the JSON says `"state": "awaiting_approval"` and carries the code).
- `up` refuses (exit 2) while the workspace is untrusted, because the session could not start; `--force` queues the jobs anyway.

```shell
solx agent status | jq '{paused, jobs: [.jobs[] | {id, state, node}], trusted: .workspace.trusted}'
solx agent tunnel start --wait 20 | jq -r 'select(.state == "awaiting_approval") | .device_code.code'
solx agent gc -n               # the plan, nothing changed
solx agent gc -y               # do it
```

`state/`, `logs/` and the mailbox are created mode 700: they hold message text, device codes and the session's
terminal log, and home directories on Sol are group-readable.

## Update

```shell
# 1. get the new solx binary (see the README), then
solx agent install            # replaces the scripts, keeps your files, prints what changed
```

What an update does and does not touch:

| | Behavior |
|---|---|
| `bin/`, `lib/`, `README.md`, `DESIGN.md` | Replaced. Each file is written to a temp name and renamed over the old one, so a script that is running right now keeps its old copy. |
| `config.env`, `workspace/CLAUDE.md` | Yours. A file you never edited (it still matches what the last install shipped) is updated in place. A file that differs from the shipped one is kept. When the shipped text itself changed since your last install, the new text is written beside yours as `config.env.new` / `workspace/CLAUDE.md.new` (see below). `-f` replaces your file with the shipped one and saves yours as `.bak`. |
| `state/`, `logs/`, `results/` | Never touched. |
| `.version`, `.shipped/` | Record which solx wrote the scripts and what text it shipped for `config.env` and `workspace/CLAUDE.md`. `solx agent <cmd>` prints a one-line note when the version differs from the binary. |

Your edited `config.env` or `workspace/CLAUDE.md` is never overwritten, so a new default would not reach it on its own.
When a release changes the shipped text of one of them, the install leaves the new text next to yours as `config.env.new`
(or `workspace/CLAUDE.md.new`). It compares against the text the previous install shipped (`.shipped/`), not against your
file, so editing the file does not trigger it and an install that ships the same text as before stays quiet. The install
output marks the file `staged` (copy just written or refreshed) or `kept` (nothing new to offer, or the copy is already
there), and the JSON entry carries `"new_default": "config.env.new"` while a copy is waiting:

```shell
diff ~/.solx/agent/config.env ~/.solx/agent/config.env.new     # what the shipped copy has that yours does not
# copy over the lines you want, then
rm ~/.solx/agent/config.env.new                                # settles it until the shipped text changes again
```

While a `.new` file exists, `solx agent status` says so (`seed_updates` in the JSON). Once your file matches the shipped
text (you copied it over, or ran with `-f`) the next install removes the `.new` file and lists it under `removed`; only
a regular file holding exactly the shipped text is removed. A `.new` file is always replaced as a whole file, never
written through a link. A tree installed by a solx that kept no `.shipped/` record gets the `.new` copy once. `-n` shows
all of this without writing anything.

A running agent keeps the old code until it restarts, and the watchdog and any newly submitted job read
the new files immediately, so a live update is a short mixed-version window. For a clean switch:

```shell
solx agent down && solx agent install && solx agent up
```

## Stop and clean up

```shell
solx agent tunnel stop -y  # if you started the VS Code tunnel (-n previews)
solx agent down -y         # pause, cancel jobs, close the session with /exit; waits up to 60 s for them to leave
solx agent gc -n           # preview what it would collect (the agent's jobs and tunnel job), then:
solx agent gc -y
solx agent wd-remove -y    # drop only the watchdog block from scrontab (your other entries stay)
rm -r ~/.solx/agent          # remove it entirely
```

## VS Code on demand

`solx agent tunnel start` runs Sol's own `code tunnel` (name `sol_$USER`, in its own small job, CLI in a
tmux session). The GitHub login belongs to the machine that created it, so `start` prefers the machine
that last logged in and usually needs no code. If it prints a device code, open
<https://github.com/login/device> and approve within about 15 minutes; do not start it again afterwards.
Do not use `gh auth token` as a login - the tunnel service answers 401. Never run it on a login node.

## Configuration

`config.env`; every value has a default in `lib/common.sh`.

| Key | Default | Meaning |
|---|---|---|
| `PARTITION` / `QOS` | `general` / `private` | Where the pool runs. `private` is nearly free in fairshare but preemptible. |
| `STANDBY` | `always` | `always`: warm standby on another node. `handoff`: queue the successor `LEAD_MIN` before the end. `off`. |
| `MAX_TIME` | `7-00:00:00` | Longest chunk. |
| `PREEMPT_STORM` / `PREEMPT_WINDOW_H` | `3` / `12` | After this many preemptions in the window, new jobs use `FALLBACK_PARTITION`/`FALLBACK_QOS` (`public`). |
| `PERMISSION_MODE` | `inherit` | Mode of the session that ran `up`, or pin one (`acceptEdits`, ...). |
| `MAINT_MARGIN_MIN` | `30` | Leave / avoid this long before a maintenance window. |
| `SOL_AGENT_MODEL` | `sonnet` | Model for the always-on session: an alias (`sonnet`, `opus`, `haiku`) or a model id. Empty uses Claude Code's own default, often Opus. The agent mostly waits and runs shell commands, so Sonnet is enough. Applies when a pool job starts, so `down` then `up` to change a running agent. |
| `ALERT_EMAIL`, `NTFY_TOPIC`, `NOTIFY_CMD` | off | Alert channels: e-mail through Slurm's job mail (subject only), ntfy.sh push, or your own script (`NOTIFY_CMD "<subject>" "<body>"`). |
| `ALERT_DOWN_MIN`, `ALERT_DEGRADED_MIN`, `ALERT_REPEAT_H` | `10`, `30`, `6` | How long a state must last before the first alert, and the repeat interval for DOWN. |
| `NOTIFY_LEVEL` | `alerts` | `all` also sends failovers, starts and tunnel-up notices. VS Code device codes go to `NOTIFY_CMD` only: ntfy topics and job names (which `squeue` shows to every user) are not private. |
| `PING_URL` | off | Dead-man's switch (healthchecks.io style): pinged while the pool is fine, `<url>/fail` while DOWN. |
| `WORKDIR` | `$AR/workspace` | The trusted workspace (do not change after the first start). |

## Troubleshooting

| Symptom | Check |
|---|---|
| `status` shows `NOT TRUSTED` | Do the trust step in `~/.solx/agent/workspace`. |
| Jobs stay PENDING | `squeue --me -t PD -O "JobID,Reason:50,StartTime"`; do not resubmit - the replacement inherits the same priority. |
| No `sol-agent` in Remote Control | `solx agent logs 30`; `tmux` session `bridge` on the active node (`solx agent attach`). |
| Watchdog entry `MISSING` | `solx agent up` or `~/.solx/agent/bin/sol-agent wd-install`. |
| `status` shows the watchdog `via` `--from-sol-agent` and a growing age | The scrontab job is stuck in the queue. The active session runs the watchdog itself every 15 minutes meanwhile. Entries written before 2026-10-19 use `htc`/`public`, which starved for over an hour at peak: run `solx agent wd-install` to move it to `general`/`private`. |
| Device code expired | `solx agent tunnel start` again for a fresh one. |
| A peer session's `SendMessage` is held | Modes differ; use `solx agent msg send` (the agent's instructions already say so). |

## Verified, and not

Tested on Sol with real Slurm jobs, a stub Remote Control bridge, Sol's real `code` CLI with a throwaway
data dir, and headless Claude runs for the skill (see [DEVELOPMENT.md](../DEVELOPMENT.md#sol-agent)).
**Not yet verified in the field:** multi-day runs (OAuth refresh, context growth), scrontab across a real
maintenance window, a real `private` preemption, a tunnel held for a full day, and desktop-editor
connections to the tunnel. Like the rest of this repo, it is a personal toolkit and not affiliated with
ASU Research Computing.
