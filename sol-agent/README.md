# sol-agent: an always-on Claude session on Sol

Sol kills jobs three ways: preemption (`private` QOS), job time limits, and maintenance windows. `sol-agent` keeps one
Claude Remote Control session alive through all three, so a workflow can keep running and stay reachable from
claude.ai/code or the phone. It can also start Sol's own VS Code tunnel on demand and hand you the GitHub device code.

- **Two 1-CPU/2 GB jobs** (ACTIVE + warm STANDBY, the standby placed away from nodes that already run a pool job), `general`/`private` by default, falling back to `public`
  after repeated preemptions. A preemption costs seconds (the standby takes the lease), not a restart.
- **The conversation is durable, the process is not:** a pinned session id, `claude --resume` after every restart.
- **Heartbeats + lease** on NFS (20 s) and a **scrontab watchdog** (5 min, held by slurmctld, survives node loss and maintenance).
- **Maintenance-aware:** reads the `maint` reservation (and `state/maint.txt`), sizes job chunks to fit, and a running job
  leaves early (a running Slurm job cannot be shortened by its owner).
- **Clean close:** sessions end with `/exit` (a kill leaves an `offline` entry in the Remote Control list); `sol-agent gc` collects leftovers.
- **Agent-to-agent mail:** `sol-agent-msg`, a plain-text file mailbox, for sessions whose permission modes differ (Claude's `SendMessage`
  then holds the message for approval).
- **VS Code:** `sol-agent tunnel start` runs Sol's `code tunnel` (same CLI, data dir and name `sol_$USER` as Sol's `code-tunnel`/`vscode`).
  The CLI's GitHub login is bound to one machine, so `start` prefers the machine that last logged in (usually no code needed);
  a new machine prints a device code and forwards it to you.
- The login node is not used. Everything runs as ordinary Slurm jobs.

## Install

```shell
solx agent install                      # writes (or updates) ~/.solx/agent from the scripts bundled in solx
cd ~/.solx/agent/workspace && claude      # once: accept the workspace trust prompt, then /exit
solx agent up                           # run it from the Claude session whose permission mode the sol-agent should inherit
solx agent status
```

Without `solx`: `git clone --depth 1 https://github.com/Shu-Wan/solx.git /tmp/solx-src && mkdir -p ~/.solx && cp -r /tmp/solx-src/sol-agent ~/.solx/agent`,
then use `~/.solx/agent/bin/sol-agent` in place of `solx agent`. To update, run `solx agent install` again: scripts are replaced,
`config.env` and `workspace/CLAUDE.md` are kept, and when a release changes the shipped text of one you edited, the new text is written beside it as `<name>.new`. Step-by-step manual: [docs/sol-agent.md](../docs/sol-agent.md).

`sol-agent up` copies the permission mode of the session that runs it (a `bypassPermissions` session makes a bypass sol-agent). Pin a
stricter one with `PERMISSION_MODE=acceptEdits` in `config.env`. The agent skill is
[`skills/sol-agent`](../skills/sol-agent/SKILL.md): `gh skill install Shu-Wan/solx sol-agent`.

## Commands

| | |
|---|---|
| `sol-agent status` / `logs [n]` | jobs, lease, heartbeats, pool health, alert channels, model, watchdog, maintenance plan, bridge URL, permission mode |
| `sol-agent alert test` | send one test message through `ALERT_EMAIL` / `NTFY_TOPIC` / `NOTIFY_CMD`; the watchdog sends the real alerts (DOWN, STILL DOWN, recovered, no standby) |
| `sol-agent up [-n] [--force]` · `down [-y\|-n]` · `resume` | start (and install the watchdog; refuses while the workspace is untrusted) / pause + close / continue |
| `sol-agent tunnel start [--job J] [--wait S] [--restart]` · `stop [-y\|-n]` · `status` · `attach` | VS Code tunnel `sol_$USER` |
| `sol-agent msg send <to> "text"` · `read` · `list` · `watch` | file mailbox (`bin/sol-agent-msg`) |
| `sol-agent gc [-y\|-n] [--tests] [--sessions]` | close what sol-agent opened; `-n` previews, `-y` does it |

Output is JSON when stdout is not a terminal (`--json` / `--text` force it); destructive commands need `-y` or `-n`; exit codes
0 done, 1 failed, 2 usage or unconfirmed, 3 waiting for you. `sol-agent help [command]` has the details; the rules are in
[lib/out.sh](lib/out.sh) and the [manual](../docs/sol-agent.md#output-confirmation-and-exit-codes).

Configuration is `config.env` (every value has a default in `lib/common.sh`). Design, measurements, failure matrix and the list of
what is *not* verified: [DESIGN.md](DESIGN.md).

## Tests

`tests/run_all.sh` runs the suite (about 40 minutes; short Slurm jobs and a few tiny real Claude sessions): `test_maint`, `test_pool`,
`test_perm_msg`, `test_tunnel` (stub CLI), `e2e` (real Slurm jobs, stub bridge) and `live_tunnel` (Sol's real `code`, a throwaway data dir,
the code is never approved). `tests/live_tunnel_401.sh` logs in to GitHub's tunnel service with your `gh` token, so it is run by hand.
`tests/skill_eval/run.sh` evaluates the skill with headless `claude -p` runs in dry-run mode.
Runtime state (`state*/`, `logs/`, `results/`) is created inside the install directory and ignored by git.

## Status

Built and tested on Sol in October 2026. Not yet verified in the field: multi-day runs (Claude OAuth refresh, context growth),
scrontab across a real maintenance window, a real `private` preemption, and a tunnel held for a full day. Two idle 1-CPU jobs sit
in a gray zone of the Research Computing acceptable-use policy (long idle waits); consider asking RC before leaving it on.
Like the rest of this repo, it is a personal toolkit and not affiliated with ASU Research Computing.
