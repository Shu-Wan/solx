# sol-agent: an always-on Claude session on Sol

Goal: a Claude session that keeps going through Sol's preemption, job time limits and maintenance windows; is reachable from a
phone (Remote Control); can start VS Code (and hand the user the device code) on demand; plans around maintenance; and cleans up after itself.
Installed to `~/.solx/agent/` (`SOL_AGENT_ROOT`). Entry point: `bin/sol-agent`. Tests: `tests/`. The probe scripts and raw result files behind the measurements below are not shipped.

## 1. Proposal

Treat the Claude **process** as disposable and the Claude **conversation** as durable. The conversation is a pinned session id whose transcript lives on NFS
`$HOME`, so any node can `claude --resume` it. A 1-CPU/2 GB Slurm job runs that session in tmux with `--remote-control`. Two such jobs exist at all times
(ACTIVE + warm STANDBY, placed away from nodes that already host a pool job); they coordinate by lease + heartbeat files. A **scrontab** entry (Slurm's own cron, held by slurmctld, so it survives
node loss and maintenance) fires a 3-minute watchdog every 5 minutes that resubmits after any death and sizes chunks to fit before maintenance.
Default queue is `general`/`private` (fairshare factor 0.001, preemptible but handled); an automatic fallback to `public` kicks in after a preemption storm.
The login node is not used.

## 2. What was measured on Sol (2026-10-15)

| Fact | Evidence | Consequence |
|---|---|---|
| `private` QOS `UsageFactor` **0.001** vs `public` 1.0 | `sacctmgr show qos` | Sol-agent on `private` is ~free in fairshare. On `public` it would cost ~1.5 billing units/h, ~250/week (~9 A100-hours): small either way |
| Real start of a 1-CPU/2 G job on one quiet afternoon (2026-10-15, one account): htc 0 s, lightwork 1 s, public 23-32 s, **general/private 32 s** (1 h, 7 d and 14 d limits alike) | queue-latency probes | In that measurement `private` had no queue penalty; peak-hour latency is not measured (see section 12). `sbatch --test-only` predicted 4 h / 17 h / 3.5 d: useless |
| Preemption: `PreemptMode=CANCEL`, `GraceTime=0`, `KillWait=30 s`; only `private` is preemptible (35 `grp_*` QOS preempt it); never requeued | `scontrol show config`, `sacctmgr` | Needs the pool (below). How often it happens depends on the owners' demand and varies by account and season: count your own with `sacct -X -s PREEMPTED --name sol-agent` |
| Time limits: `public` 7 d, `general` 14 d, `htc` 4 h, `lightwork` 1 d | `scontrol show partition` | Chunks + handoff |
| **Users cannot modify a RUNNING job** (`Only pending jobs can be modified`) | `scontrol update` | Running sol-agents enforce the maintenance bound themselves; only pending jobs are re-fitted |
| Maintenance = cluster-wide reservation `maint`: 2024-07-08, 2024-10-15, 2025-03-25, 2025-10-13, 2026-05-18, 2026-10-12 (to 10-15 15:00, 3.3 d); 08:00 starts, 1-4 days | `sacctmgr show reservation start=2024-01-01` | ~2/yr; Slurm will not start a job that crosses one |
| `ScronParameters=enable`; scrontab fires on the minute, jobs start in 0-2 s on varying nodes, `sbatch` works inside | live round-trip | The heartbeat/watchdog mechanism |
| Dependency-held and `--begin`-held jobs show `AccrueTime=Unknown` | `scontrol show job` | A dependent successor would not accrue age priority: successors are submitted eligible and coordinate by lease instead |
| **NFS `$HOME` cross-node visibility: new file 7.3-8.1 s, deleted file 13.4-26.1 s, file replaced by rename 0.3-0.9 s. BeeGFS `/scratch`: 0.1-1.0 s for all** | writer/observer probes on two nodes | The lease is only ever *replaced* (tombstone on release), never created/deleted. This also explained a test-client "30 s" artefact |
| Compute nodes reach claude.ai, GitHub and the VS Code tunnel service (outbound HTTPS); compute -> login node `ssh` times out | curl, ssh | Remote Control and `code tunnel` run on compute nodes; the login node cannot be the control point |
| VS Code CLI login token is encrypted per machine: written on `scc054`, "not logged in" on `sc005`; one tunnel per data dir (`~/.vscode/tunnel-stable.lock`, left behind by unclean exits) | fake-token probe with Sol's real `code`, Sol troubleshooting page | A device code is needed on every tunnel start on a new node; stale lock cleared automatically |
| AUP: login-node processes that load it can be killed; unattended listening services banned; jobs with long waits / sustained sleep loops "may be terminated" | RC AUP | Login-node residency is a gray area (not recommended). An idle 1-CPU job is also gray: ask RC (section 12) |
| Idle footprint: bridge 153 MB + session 240 MB ~ 390 MB RSS, 1-4 % CPU | `ps` in a job | `--mem=2G` (the site minimum) is ample |
| `srun --jobid J --overlap` from inside another job needs `--cpu-bind=none` | error otherwise | Used by the VS Code and attach helpers |
| `scancel --signal=STOP` makes Slurm report the job as `STOPPED`, not RUNNING | `squeue` after the signal | A first "frozen holder" test only exercised the job-state path (instant takeover). The real heartbeat-staleness test freezes the process from inside the job |
| NFS `stat` mtime of a heartbeat replaced every 5 s reads fresh from another node (median 4 s, max 6 s), same as the in-file timestamp | heartbeat observer probe | A stale-`stat` theory for a suspected false steal was **wrong**; that "steal" was the test's own SIGSTOP (job state `STOPPED`). A grace was added anyway so a *missing* heartbeat file (new files can take 7-30 s to appear over NFS) never counts against a RUNNING job younger than 2 min |

## 3. Architecture

```
 phone / claude.ai/code --(Remote Control, outbound)-------------------------+
                                                                             v
 scrontab (slurmctld) --5 min--> sol-agent-wd.sh (htc, 3-min job)       claude --remote-control NAME
     writes wd.json, keeps pool at policy,                           --resume <pinned uuid> "<resume prompt>"
     fits chunks to maintenance, kills hung jobs                                 ^ (tmux, inside the job)
                                                                                 |
   +--------------------+   lease + hb/<job>.json + events.log on NFS  +--------------------+
   | sol-agent job A    | <------ ~/.solx/agent/state (NFS) ---------> | sol-agent job B    |
   | ACTIVE (node X)    |                                              | STANDBY (node Y)   |
   +--------------------+                                              +--------------------+
        | sbatch / srun --overlap                       
        v
   worker jobs (GPU, private QOS fine): same resume machinery, own pool config (copy config.env)
```

* **Lease.** `state/lease` = `<jobid> <epoch>`, guarded by an `mkdir` lock (flock is unreliable on NFS); release writes a tombstone `- <epoch>` by rename (never delete).
  A holder is alive only if Slurm says RUNNING **and** its heartbeat is < 180 s old; if Slurm is unreachable nobody steals.
* **Pool.** `STANDBY=always` (default): a warm standby, staggered to 60 % of the chunk so a pair never expires together, placed on a different node (`--exclude`; in testing
  active and standby co-located on one node). `handoff`: successor queued when < `LEAD_MIN` remain. `off`: watchdog resubmits only.
* **Handoff.** A job near its effective end (USR1 600 s before the limit, or the maintenance bound) releases the lease only to a successor that is RUNNING, fresh, *not itself in handoff*
  and outlives it by more than the handoff window. It then closes its session cleanly and exits as soon as the successor is serving.
* **Session continuity.** First start `--session-id <uuid>` + a start prompt; afterwards `--resume <uuid>` + a resume prompt. The uuid is pinned in `state/session_id`; all prompts use
  absolute paths (`sol-agent` is not on `PATH` in a job; a bare name confused the agent in testing). Repeated resumes append to the same transcript (verified: one file, one session id).
* **Preemption-storm fallback.** `PREEMPT_STORM` (3) preemptions of this pool in `PREEMPT_WINDOW_H` (12) hours: new submissions go to `FALLBACK_PARTITION/QOS` (public/public).
* `SOL_AGENT_KIND=bridge` runs the headless spawn-on-demand `claude remote-control` instead of an interactive session.

## 4. Heartbeats

| Heartbeat | Writer / period | Consumer | If it goes silent |
|---|---|---|---|
| `state/hb/<job>.json` (role, bridge up, left_s, eff_s, handoff) | every sol-agent job / 20 s | standby (promotes), watchdog, `sol-agent status` | > 180 s: lease can be taken; RUNNING and > 600 s: watchdog `scancel`s the job |
| `state/lease` | holder, on change | all sol-agents | holder not RUNNING or hb stale: standby acquires |
| `state/wd.json` | scrontab watchdog / 5 min | **active sol-agent** (backup watchdog), `sol-agent status` | > 15 min: the active sol-agent runs the watchdog itself and reinstalls the scrontab entry if it vanished |
| `state/health` (level, since, last alert) | watchdog / 5 min (`lib/health.sh`) | `ALERT_EMAIL` / `NTFY_TOPIC` / `NOTIFY_CMD`, `sol-agent status` | DOWN after `ALERT_DOWN_MIN` without an alive active session, no-standby after `ALERT_DEGRADED_MIN`; one recovery message; DOWN repeats every `ALERT_REPEAT_H` |
| optional `PING_URL` (healthchecks-style dead-man's switch) | watchdog / 5 min | an external service you choose | base URL while the pool is fine or paused on purpose, `/fail` while a DOWN alert stands; silence means the watchdog is gone. Off by default |

Mutual supervision: watchdog supervises sol-agents, sol-agents supervise the watchdog. The remaining hole (both gone *and* the scrontab entry gone, or Sol unreachable) is what the optional external ping is for: it reports the pool as well as the watchdog, so the one service covers both. The internal alerts cover everything else and need no account (Sol's `mail`); ntfy is the phone-push alternative. A failover takes seconds, so a level must last before it alerts: the watchdog samples every 5 minutes and a single sample inside a failover would otherwise page for nothing. Device codes (secrets) are never sent to ntfy, whose topics are readable by anyone who knows the name.

## 5. Failure matrix (times from the e2e event logs unless noted)

| Event | Detection | Recovery |
|---|---|---|
| Preempt-kill (SIGTERM, +30 s SIGKILL) | trap closes the session, releases lease | standby ACTIVE in **1-8 s** (1, 4, 8 in three runs). No standby: watchdog resubmits in <= 5 min + ~30 s queue |
| Untrapped death (shell SIGKILL, OOM, node crash) | Slurm state != RUNNING | standby ACTIVE in **6-11 s** |
| Time limit | USR1 600 s before | handoff in **3 s** to the younger standby; old job exits early |
| Pair expiring together | both in handoff, neither eligible | one keeps serving: **0 s gap** (regression test for a bug the e2e found) |
| Hung holder (job RUNNING, heartbeat stops) | hb age > `STALE_S` (180 s) | standby takes the lease after the threshold: **29 s** in the test with `STALE_S=30` (so ~3 min in production, the slowest path); watchdog kills the hung job after 600 s |
| Maintenance window announced | `scontrol show reservation` + manual `state/maint.txt` | running sol-agent leaves 30 min before it (**55-59 s before the window** in the test, 1-min margin); watchdog HOLDs until it ends; pending jobs are shortened |
| Watchdog silent | `wd.json` age | active sol-agent runs it (and repairs the scrontab entry) |
| Claude process dies inside a healthy job | tmux session gone | restart with crash-loop guard (> 3 in 10 min: back off 5 min) |

Cross-node: the lease is replaced by rename (visible in < 1 s on NFS), so graceful handoff between nodes is not slowed by NFS caching.

## 6. Maintenance awareness

`lib/maint.sh` merges `scontrol show reservation -o` (reservations with the `MAINT` flag or a `maint` name) and `state/maint.txt` (`<ISO start> <ISO end> [note]`, for windows announced on the dashboard/Slack
before Slurm has the reservation). `maint_plan` returns `NONE`, `FIT <seconds>` or `HOLD <resume time>`. New chunks end `MAINT_MARGIN_MIN` (30) before the window; pending jobs are re-fitted with
`scontrol update`; a *running* sol-agent computes `effective end = min(Slurm limit, window start - margin)`, hands off early and leaves (it cannot be shortened from outside). **Caveat:** the parser is tested on
synthetic text in Slurm's documented format; no live reservation exists today to validate against.

## 7. Permission modes and agent-to-agent messages

`SendMessage` is delivered automatically only between sessions in the same permission-mode class. Tested:

* bypass -> bypass: delivered and answered in ~2 s (also across nodes to a Remote Control session on sc003).
* bypass -> auto: the receiver stops at a human "Deliver / Deny" dialog; an unattended session would hold it forever. Two such messages were held, then expired with `[Cross-session delivery notice] ... not approved before expiry`.

So `sol-agent up` records the permission mode of the Claude session that runs it (`/proc/$CLAUDE_PID/cmdline`, else `settings.json` `defaultMode`) and the sol-agent is launched with the same mode.
`PERMISSION_MODE=` pins one explicitly. **This means a sol-agent created from a bypass session is a bypass session** (decision for the operator, section 12). Only the *initial* mode is detectable.

**Fallback when modes differ (or the peer is a script/job, offline, or on another node): `bin/sol-agent-msg`**, a plain-text Maildir-style mailbox (`state/mailbox/<name>/{tmp,new,cur}`): atomic rename on write, claim-by-rename on read
(exactly-once with competing readers), names `[A-Za-z0-9._-]`, 64 KB cap. Delivery into a Claude session: `Monitor(command: "sol-agent-msg watch <name>")`, or a hook running `sol-agent-msg hook <name>`.
Messages print under a banner saying they are agent-to-agent data, not a user instruction. **Tested with a real agent in a mismatched mode:** an `auto`-mode sol-agent received a file message through its own
Monitor and replied `PONG-23669` through the mailbox. Latency on NFS: a new message file appears after ~7-8 s plus the 15 s poll.

## 8. VS Code on demand + device code (Sol's own VS Code, verified with the real tunnel)

Decision: use Sol's VS Code, not a private download. `sol-agent tunnel` runs `/packages/apps/vscode/latest/bin/code tunnel` (RC-maintained, 1.140.0 today) with the same arguments as Sol's `code-tunnel` alias and `vscode` command:
`--cli-data-dir ~/.vscode --name sol_$USER --accept-server-license-terms --disable-telemetry`. Same tunnel name a desktop VS Code already lists for that user (the existing registration is reused, not recreated), the downloaded server cache in
`~/.vscode/servers` is reused, nothing private to keep current, and the sol-agent and a manual `vscode` share one lock, so they cannot collide (the sol-agent sees a running `vscode` job and does nothing).

* `tunnel start` (default): its own small job like Sol's wrapper (`lightwork`/`public`, 1 CPU, 1 day; `TUNNEL_PARTITION/QOS/TIME`, e.g. `public` + `7-0`), editor opens at `$HOME` (`TUNNEL_CHDIR`). Not inside the 2 GB sol-agent allocation. `--job J` runs it inside
  job J's allocation (`srun --overlap`, for a GPU worker), `--here` on the current node (refused on a login node). Inside the job the CLI lives in a **tmux session** (`code-tunnel`, one socket per job id), output tee'd to `state/tunnel/tunnel.log`;
  `sol-agent tunnel attach` opens it. `stop` sends Ctrl-C into tmux, so the CLI exits cleanly (~1 s).
* **The login is machine-bound** (measured with the real binary and a fake token in a throwaway data dir). Written on `scc054` it reads "logged in", on `sc005` "not logged in". Consequences, all seen with the real tunnel:
  a restart on a *different* node needs a GitHub device code; a restart on the *same* machine needs none (sc001 restart, sc002 `--job`, dedicated job back on sc002 in 3 s). `start` therefore remembers the machine whose login last worked (`state/tunnel/auth_node`),
  asks Slurm for it first and falls back to any node after 15 s (`TUNNEL_PREFER_AUTH_NODE`, `TUNNEL_NODE_WAIT`). Most starts need no human; a new machine needs one approval.
* **Device code path:** parsed from the CLI (`use code XXXX-XXXX`), printed by `start` (5-7 s after the command in the live runs, 2-3 s in the real ones), written to `state/tunnel/device_code`, sent to `NOTIFY_CMD` and the `user` mailbox (mailbox: +7-8 s NFS +15 s poll).
  After the human approves, the waiting job brings the tunnel up by itself (real run: approved 62 s and 118 s after the code, tunnel `Connected` within 2 s). Codes live ~15 min.
* **Does not work: `gh auth token`.** `VSCODE_CLI_ACCESS_TOKEN=$(gh auth token) code tunnel user login` is accepted locally, but the tunnel service answers `HTTP 401 Unauthorized` and the tunnel never starts (the gh token belongs to a different OAuth app/scopes).
  `run` detects a stored login that the service rejects (no tunnel URL plus `HTTP status 401` in the log; on a fresh data dir the CLI even exits with code 0, so the exit code is no signal), drops it and falls back to the device flow once. Verified with the real CLI (`tests/live_tunnel_401.sh`, 6/6); the first two versions of this check were wrong (waited for a non-zero exit, then for a dead process) and the live test is what caught it.
* **Lock file:** one tunnel per data dir (`~/.vscode/tunnel-stable.lock`). The real CLI leaves the file in place even after a clean stop, and an unclean exit or a failed start (the 401 run) leaves it blocking a start on another node (Sol's troubleshooting page).
  So presence of the file means nothing; `start` deletes it only when none of the user's jobs named like a tunnel (`<NAME>-vscode`, `code`, `vscode`) is RUNNING, and the tunnel host deletes it itself before starting (a delete made on another node shows up here only after 13-26 s on NFS).
  Login and server cache are never touched.
* Verified with the real tunnel (2026-10-15, two device codes approved by the account owner): registration (`tunnel status` inside the job: `Connected`, name `sol_$USER`), takeover of the same name/id from a second machine (sc001 then sc002), restart on the same machine without a code,
  `--job` inside another allocation (target job survives `stop`), tmux session present and stop clean. `tests/test_tunnel.sh` (stub that models the machine-bound token and the lock) and `tests/live_tunnel.sh` (real CLI, code never approved) cover the plumbing repeatably.

## 9. Closing and collecting what was opened

* A **kill** (`scancel` of an old-style job, `kill-server`) leaves the session in the Remote Control list as `offline` forever. A **clean `/exit`** removes it. `--resume` after a clean exit comes back with the **same peer ref**.
* So the sol-agent closes gracefully (Escape, `/exit`, Enter, Enter: the second Enter answers "Background work is running" because of the mailbox watcher) and only then kills the tmux server. `sol-agent down`: TERM to gone in **5 s** (tested on a fresh job).
* `sol-agent gc [-y|-n] [--tests] [--sessions] [--purge] [--uninstall]`: cancels jobs, kills tmux servers and removes stale sockets, **collects lingering sessions** (resume with no prompt, clean exit; costs no tokens), deletes test state dirs, removes the scrontab block.
  Every session the sol-agent creates is recorded in `state/sessions.log`. Tested: it cleared all four `sol-agent-live*` husks from the list; unrelated entries were never touched.
* Known wart: `gc --sessions` also re-collects a session that already exited cleanly (harmless, one tiny job).

## 10. Operating it

```
cd ~/.solx/agent/workspace && claude       # once: accept the workspace trust prompt, /exit  (sessions refuse untrusted dirs; sol-agent never edits Claude's trust settings)
~/.solx/agent/bin/sol-agent up             # run FROM the Claude session whose permission mode should be inherited
~/.solx/agent/bin/sol-agent status         # jobs, lease, heartbeats, maintenance plan, trust, bridge URL, mailbox
~/.solx/agent/bin/sol-agent tunnel start   # VS Code + device code            ~/.solx/agent/bin/sol-agent msg send <to> "text"
~/.solx/agent/bin/sol-agent down -y | resume  # pause+close / continue          ~/.solx/agent/bin/sol-agent attach   # tmux of the active sol-agent
~/.solx/agent/bin/sol-agent gc -n --tests --sessions      # preview a cleanup (JSON on a pipe; add -y to do it)
```
Config: `config.env`. Extra pools (GPU workers on `general`/`private`): copy it, set `NAME`, `PARTITION`, `QOS`, `SBATCH_EXTRA`, run with `SOL_AGENT_CONFIG=... SOL_AGENT_STATE=...`, add a second scrontab block.

## 11. Test results

| Suite (all in `tests/`) | What it covers | Result |
|---|---|---|
| `test_maint.sh` | maintenance parser, plan (NONE/FIT/HOLD), manual windows, pending-job cap, duration parsing | **17/17** |
| `test_pool.sh` | anti-affinity, private->public storm fallback, stagger, chunk clamp, tombstone lease, invisible-heartbeat grace | **25/25** |
| `test_cli_contract.sh` | command-line contract (`lib/out.sh`): JSON on a pipe / text on a terminal, `--json`/`--text`, stdout vs stderr, `-n` changes nothing, `-y`/`-n`/prompt and exit-2 refusal, exit codes, `up` needs a trusted workspace, structured `status`/`gc`/`msg`/`tunnel` (stubs) | **65/65** |
| `test_safety.sh` | review regressions: scrontab is never rewritten or removed when unreadable (and foreign entries survive), `gc` vs `gc --tests` select disjoint jobs and the live agent is never a test target, permission-mode capture cannot be faked by prompt text, collector/tunnel argument validation, PAUSE honoured by `pool_submit`, private state dirs | 25/25 |
| `test_perm_msg.sh` | permission-mode detection; mailbox (round trip, ack, traversal, whitespace, oversize, 40 writers x 3 readers exactly-once, hook, watch) | **23/23** |
| `test_tunnel.sh` | Sol-style tunnel job, device code relay + approval, tmux session on the node, auth-node preference (restart needs no code), idempotent start, clean stop, stale-lock removal, foreign `vscode` job protected, login-node refusal, `--job` via `srun --overlap`, `--restart` (stub CLI with machine-bound token and lock) | **23/23** |
| `e2e.sh` (real Slurm jobs, `htc`, 5-min chunks, stub bridge, 970 s) | S1 cold start; S1b healthy holder keeps lease 50 s; S2 preemption path; S2b untrapped kill; S3 handoff to younger standby; S3b pair expiring together; S4 hung holder (frozen inside job, takeover only after stale threshold); S5 PAUSE; S6 a-d maintenance HOLD/fit/pending cap/running sol-agent leaves early; S7 backup watchdog | **25/25** |
| Real Claude sessions (transcripts in `~/.claude/projects/`) | (1) interactive `--remote-control` session killed, resumed in a new job: recalled `PELICAN-7`. (2) **Shipped path** (`sol-agent.sbatch`): killed, revived by the pool policy on a *different node* with the baked-in resume prompt: recalled `PELICAN-8`. (3) auto-mode sol-agent vs bypass lead through the mailbox: `PONG-23669`. (4) SendMessage A/B: bypass->bypass answered ~2 s; bypass->auto held, 2 expired | all as stated |
| Close/collect | kill leaves an `offline` husk, clean `/exit` removes it, resume after clean exit keeps the peer ref; `down` TERM->gone 5 s; collector cleared 4 husks; final `gc` left zero jobs, sockets, processes, scrontab entries, state dirs | verified, see section 9 |
| `live_tunnel.sh` (Sol's real `code`, throwaway data dir, code never approved) | dedicated job starts, real output yields the `XXXX-XXXX` code in 5-7 s, relayed to stdout/state file/mailbox, `status` shows it, `stop` leaves no job and nothing registered | **8/8** |
| `live_tunnel_401.sh` (real CLI, throwaway dir) | a stored login the service rejects (HTTP 401) is dropped and replaced by a device code | **6/6** |
| `tests/skill_eval/run.sh` (headless `claude -p`, dry-run: permission mode `dontAsk` with a read-only allow list, every attempted command recorded) | 6 task prompts with and without the skill (set up, tunnel, device code, status, cross-mode message, stop) plus 3 negative prompts (queue/fairshare, plain sbatch, plain requeue) that must not trigger it | **with skill 28/28 checks, without 9-11/25 (varies run to run); no false triggers** (1 run per case, regex graders, bare environment without user CLAUDE.md/sol-skill: indicative, not statistical). The runs happen in a throwaway directory outside any repo: from inside a repo the project memory leaked into the no-skill arm (baseline 19/25). The installed user-scope skill was also confirmed to trigger with real user settings loaded (ran `status` + `logs`, answered "not started") |
| Probes (working scripts, not shipped) | scrontab round trip, real queue latency (2 probes), QOS usage factors, private preemption history, NFS vs BeeGFS visibility, heartbeat staleness, bridge identity across nodes, VS Code CLI device-code format | numbers in section 2 |

**Bugs the tests found (all fixed):** (1) two jobs started together expired together and each "handed over" to the other, leaving nobody serving (S3b); (2) it was assumed that a running job's time limit could be shortened, it cannot on Sol, so the sol-agent now enforces the maintenance bound itself;
(3) a graceful-stop-less kill left `offline` husks; (4) `MIN_USEFUL` clamp could inflate a chunk past `MAX_TIME`; (5) recipient names with spaces were silently split into several mailboxes; (6) default mailbox sender got a stray `_`; (7) NFS made lease create/delete take 7-26 s, so the lease is now replace-only;
(8) active and standby were placed on the same node (anti-affinity added); (9) a missing heartbeat file could read as a dead holder (hardened); (10) `tunnel start --job` run twice restarted a healthy tunnel because the "already running" test looked at a lock file that NFS had not shown yet (now decided by our own supervisor); (11) a lock deleted on one node can still be visible on another for 13-26 s, so the tunnel host now clears the stale lock itself; (12) a stored login that the service rejects (401) made the tunnel job end silently with nothing to report (found by the live test, fixed in three rounds), now it falls back to a device code; (13) the real CLI leaves its lock file after a clean stop, which the stub test had modelled the other way (harmless: the file is only deleted when no tunnel job runs). **Wrong turns during development, for honesty:** the VS Code login was first assumed to survive restarts on any node (it does not: machine-bound token); NFS `stat` staleness was first blamed for a "false steal" that was actually the test freezing the job; several e2e failures were test defects (wrong job inspected, a legitimate handoff inside a stability window), not product faults.


## 12. Not verified, risks, and decisions for the operator

**Not verified (be skeptical of these):**
1. scrontab behaviour across a *real* maintenance window (do entries persist and fire again afterwards?). No window is open to test; the optional external ping is the safety net.
2. The `maint` parser against a real live reservation (synthetic fixtures only).
3. VS Code tunnel: verified live (registration, takeover from a second machine, same-machine restart without a code, `--job`, tmux, stop). Still unverified: a tunnel held for the full day (1-day limit, then the next start needs a code only if it lands elsewhere), the desktop-editor side (vscode.dev was never opened in the tests), and what the real CLI does with a lock left by a SIGKILLed run on a different node (relies on Sol's documented behaviour and deletes the file). `gh auth token` does not work (401).
4. Days-long behaviour: only minute-scale runs. Unknowns: Claude OAuth refresh in a headless session over days, transcript/context growth (each restart adds a resume turn; a big context makes a cold resume expensive), log growth.
5. A real `private` preemption (only the identical signal path via `scancel` was exercised).
6. Queue latency under peak load (32 s measured on a quiet afternoon; the watchdog logs real waits to `state/latency.log`).
7. Login node: not tested (unreachable from compute). Recommendation rests on the AUP and the failed ssh, not on a measured login-node limit.
8. Placement is best effort: the standby avoids nodes that already host a *running* pool job, so two jobs that are both pending can still start on the same node. Fencing on takeover (below) bounds the damage of that and of a split brain, it does not prevent them.
9. The test client reads NFS from another node; delays there were a measurement artefact, now removed from the lease path, but mailbox delivery still pays ~7-8 s for new files.

**Known limitations (from an independent review on 2026-10-17; the reviewers read the code, they did not run it, and each item below was checked by hand before being listed):**
* *Takeover fencing is best effort.* When a standby takes the lease from a holder that Slurm still lists as RUNNING (frozen supervisor, NFS stall), it `scancel`s that job and waits up to 60 s before starting its own Claude process. A node that is really frozen can lag behind Slurm's kill, so two sessions can briefly share one conversation.
* *The lease lock is a `mkdir` lock that is reclaimed after 30 s.* A critical section stalled longer than that (slurmctld timeout) can be entered twice; the next tick's holder check repairs the lease, but it is not a true mutex.
* *Liveness uses two Slurm queries* (the job, then "is Slurm reachable"). A transient failure between them can declare a healthy holder dead. With fencing that now costs the old job instead of briefly running two.
* *Pool top-ups are not serialized.* The scrontab watchdog and the active job's backup watchdog can both submit; surplus *pending* jobs are trimmed, an extra running one is not.
* *`tunnel start --job` and failover.* The supervisor tmux session lives on the submitting node; after a failover the recorded job id keeps the lock, and `tunnel stop` is the way to clear it.
* *`gc --sessions` marks a session collected when the collector job is submitted*, not when it exits cleanly.

**Risks:** an idle 1-CPU job (x2) is in a gray zone of the AUP ("jobs with long waits ... may be terminated"): cost is tiny but worth one ticket to RC; the sol-agent holds the user's Claude credentials/identity and, if inherited from a
bypass session, runs without permission prompts; a wedged `--resume` is the single point that the watchdog can only notice through heartbeats, not fix.

**Decisions for the operator:** (1) inherit bypass mode into the always-on session, or pin `PERMISSION_MODE=acceptEdits` and rely on the mailbox for peers? (2) `private` + warm standby (default now) vs `public`. (3) enable `PING_URL` (needs a free account at a dead-man's-switch service; off).
(4) do the one-time trust step. (5) ask RC whether two idle 1-CPU allocations are fine.
