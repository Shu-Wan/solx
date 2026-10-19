#!/usr/bin/env bash
# sol-agent job. SBATCH options are supplied by pool_submit (so the chunk length can follow the
# maintenance plan): -J sol-agent -p public -q public -c 1 --mem=2G -t <chunk> --signal=B:USR1@<N>
#
# Roles: any number of sol-agent jobs may run; exactly one is ACTIVE (holds the lease, runs the Remote
# Control bridge); the rest are STANDBY and take over within one tick when the holder is gone
# (job ended, node died, heartbeat stale) or releases the lease (USR1 before the time limit).
set -u
. "${SOL_AGENT_ROOT:-$HOME/.solx/agent}/lib/common.sh"
. "$AR/lib/pool.sh"
COMP=sol-agent; scrub_claude_env
export PATH="$HOME/.local/bin:$PATH"

JOB=${SLURM_JOB_ID:?run under sbatch}
# `down` pauses first, then cancels; a watchdog that passed its PAUSE check just before can still have queued a job. Do not serve.
[ -f "$STATE/PAUSE" ] && { log "paused: job $JOB exits without serving"; exit 0; }
TM="tmux -L $NAME-$JOB"
ROLE=standby; HANDOFF=0; STOP=0; RESTARTS=0; LAST_START=0; BLOG=$LOGS/bridge-$JOB.log
WD_BACKUP_AT=0

bridge_up()   { $TM has-session -t bridge 2>/dev/null; }
# A takeover from a holder that Slurm still lists as running (frozen supervisor, NFS stall) would leave two Claude processes
# on one conversation. Cancel the old holder and wait for it to go before starting ours. Best effort: a frozen node can still
# lag behind Slurm's kill; the wait is bounded and the outcome is logged.
fence_previous() {
  local prev=$1 i st
  [ -n "$prev" ] && [ "$prev" != "$JOB" ] || return 0
  st=$(job_state "$prev"); case $st in RUNNING|COMPLETING) ;; *) return 0;; esac
  log "fencing previous holder $prev ($st): scancel"; scancel "$prev" 2>/dev/null
  for i in $(seq 1 30); do st=$(job_state "$prev"); case $st in RUNNING|COMPLETING) sleep 2;; *) return 0;; esac; done
  log "previous holder $prev still $st after 60s; starting anyway"
}
# Clean close: a graceful /exit REMOVES the session from the Remote Control list, kill-server leaves an "offline" husk
# there (tested). After a clean exit `--resume` re-registers under the same identity, so this is safe for restarts too.
# The exit asks for confirmation when background work (the mailbox watcher) runs, hence the second Enter.
bridge_stop() {
  if [ -z "$BRIDGE_CMD" ] && bridge_up; then
    if [ "${SOL_AGENT_KIND:-session}" = session ]; then
      $TM send-keys -t bridge Escape 2>/dev/null; sleep 0.5
      $TM send-keys -t bridge '/exit' Enter 2>/dev/null; sleep 2; $TM send-keys -t bridge Enter 2>/dev/null
    else $TM send-keys -t bridge C-c 2>/dev/null; fi
    local i; for i in 1 2 3 4 5 6 7 8; do bridge_up || break; sleep 1; done
  fi
  $TM kill-server 2>/dev/null
}

# Two launch kinds (SOL_AGENT_KIND):
#   session (default) interactive `claude --remote-control`, pinned to one conversation id, `--resume`d after every restart
#   bridge            headless `claude remote-control` that only spawns sessions when claude.ai/code asks for one
session_cmd() {
  local pm sid resume=0 prompt args
  pm=$(effective_permission_mode); SID_FILE=$STATE/session_id
  [ -s "$SID_FILE" ] || cat /proc/sys/kernel/random/uuid > "$SID_FILE"
  sid=$(cat "$SID_FILE")
  ls "$HOME"/.claude/projects/*/"$sid".jsonl >/dev/null 2>&1 && resume=1
  args=(claude --remote-control "$NAME" --name "$NAME")
  case $pm in bypassPermissions) args+=(--dangerously-skip-permissions);; "") ;; *) args+=(--permission-mode "$pm");; esac
  [ -n "${SOL_AGENT_MODEL:-}" ] && args+=(--model "$SOL_AGENT_MODEL")
  # sol-agent/sol-agent-msg are NOT on PATH inside the job: every prompt uses full paths (a bare `sol-agent` confused the agent in testing)
  if [ "$resume" -eq 1 ]; then
    prompt=${RESUME_PROMPT:-"You were restarted on $(hostname -s) at $(date +%T) after the previous Sol job ended. Re-read $AR/workspace/CLAUDE.md, run $AR/bin/sol-agent status and $AR/bin/sol-agent logs 20, re-arm the mailbox watcher ($AR/bin/sol-agent-msg watch sol-agent --interval 15, via the Monitor tool), then continue your standing instructions. The control CLI is not on PATH: use the full paths."}
    args+=(--resume "$sid" "$prompt")
  else
    echo "$sid $NAME $(date -Is) $WORKDIR" >> "$STATE/sessions.log"      # registry used by `sol-agent gc --sessions`
    prompt=${START_PROMPT:-"You are the always-on sol-agent session (Slurm job $JOB on $(hostname -s)). Read $AR/workspace/CLAUDE.md for your standing instructions and do its start-up steps now. The control CLI is $AR/bin/sol-agent (not on PATH: use the full path)."}
    args+=(--session-id "$sid" "$prompt")
  fi
  printf '%q ' "${args[@]}"
}
bridge_start() {
  local pm; pm=$(effective_permission_mode)
  if [ -n "$BRIDGE_CMD" ]; then      # test hook (stub)
    $TM new-session -d -s bridge -c "$WORKDIR" "($BRIDGE_CMD) 2>&1 | tee -a $BLOG"
  elif [ "${SOL_AGENT_KIND:-session}" = session ]; then
    $TM new-session -d -s bridge -c "$WORKDIR" "exec $(session_cmd)" && $TM pipe-pane -t bridge -o "cat >> $BLOG"
  else
    # --spawn is explicit on purpose: without it the bridge blocks on an interactive "Choose [1/2]" prompt
    $TM new-session -d -s bridge -c "$WORKDIR" "(claude remote-control --name $NAME --spawn $SPAWN --capacity $CAPACITY ${pm:+--permission-mode $pm} ${SESSION_ID:+--session-id $SESSION_ID}) 2>&1 | tee -a $BLOG"
  fi && { LAST_START=$(now); log "bridge started kind=${BRIDGE_CMD:+stub}${BRIDGE_CMD:-${SOL_AGENT_KIND:-session}} (restarts=$RESTARTS, permission_mode=${pm:-unset})"; }
}
CLEANED=0
cleanup() { [ "$CLEANED" -eq 1 ] && return; CLEANED=1; bridge_stop; lease_release "$JOB"; rm -f "$HB/$JOB.json"; log "exit role=$ROLE reason=${1:-exit}"; }
trap 'HANDOFF=1; log "USR1: time limit approaching"' USR1
trap 'STOP=1; log "TERM/INT received"' TERM INT
trap 'cleanup exit' EXIT

log "start node=$(hostname -s) limit=$(squeue -h -j "$JOB" -o %l) bridge=${BRIDGE_CMD:+stub}${BRIDGE_CMD:-claude}"
pool_record_latency

DRAIN_AT=0; EFF_S=$(timeleft_s "$JOB")
# best_successor: id of another RUNNING sol-agent that is fresh, not in handoff, and has > HANDOFF_S more effective time than me
best_successor() {
  local id st f eff
  while IFS='|' read -r id st _; do
    [ "$st" = RUNNING ] && [ "$id" != "$JOB" ] || continue
    [ "$(hb_age "$id")" -lt "$STALE_S" ] || continue
    f=$HB/$id.json
    [ "$(jq -r '.handoff // 1' "$f" 2>/dev/null)" = 0 ] || continue
    eff=$(jq -r '.eff_s // 0' "$f" 2>/dev/null)
    [ "$eff" -gt $(( EFF_S + HANDOFF_S )) ] && { echo "$id"; return; }
  done < <(sol_agent_jobs)
}
MAINT_CHECK_AT=0; MAINT_S=""
while [ "$STOP" -eq 0 ]; do
  LEFT_S=$(timeleft_s "$JOB")

  # Effective end = min(Slurm time limit, next maintenance start - margin). Sol users cannot shorten a RUNNING
  # job, so the sol-agent enforces the maintenance window itself: hand off early, then leave before the admins kill it.
  if [ "$(now)" -ge "$MAINT_CHECK_AT" ]; then
    # a failed reservation query keeps the last known window instead of reading as "no maintenance"
    if [ -n "${SOL_AGENT_RESV_FILE:-}" ] || scontrol show reservation -o >/dev/null 2>&1; then MAINT_S=$(maint_next | awk '{print $1}'); fi
    MAINT_CHECK_AT=$(( $(now) + 60 ))
  fi
  EFF_S=$LEFT_S; EFF_WHY="time limit"
  if [ -n "$MAINT_S" ]; then
    mleft=$(( MAINT_S - $(now) - MAINT_MARGIN_MIN*60 ))
    [ "$mleft" -lt "$EFF_S" ] && { EFF_S=$mleft; EFF_WHY=maintenance; }
  fi
  if [ "$EFF_S" -le "$HANDOFF_S" ] && [ "$HANDOFF" -eq 0 ]; then HANDOFF=1; log "handoff: effective end in ${EFF_S}s ($EFF_WHY)"; fi
  if [ "$EFF_S" -le 0 ] && [ "$EFF_WHY" = maintenance ]; then log "leaving before maintenance window"; break; fi

  if [ "$HANDOFF" -eq 1 ] && [ "$ROLE" = active ]; then
    pool_ensure                                  # make sure a successor is at least queued
    # hand over only to a job that is RUNNING, fresh, not itself in handoff, and outlives me by > HANDOFF_S.
    # (Two jobs submitted together expire together; without this test both "hand over" to each other and nobody serves.)
    succ=$(best_successor)
    if [ -n "$succ" ]; then bridge_stop; lease_release "$JOB"; ROLE=draining; DRAIN_AT=$(now); log "handoff: released lease to $succ"; fi
  fi

  if [ "$ROLE" = draining ]; then
    # leave as soon as the successor is confirmed serving; if it never shows up, go back to being a candidate
    h=$(lease_holder)
    if [ -n "$h" ] && [ "$h" != "$JOB" ] && [ "$(jq -r .bridge "$HB/$h.json" 2>/dev/null)" = up ]; then log "successor $h is serving; leaving early"; break; fi
    [ $(( $(now) - DRAIN_AT )) -gt 120 ] && [ -z "$h" ] && { ROLE=standby; log "no successor took over in 120s; back to standby"; }
  fi

  if [ "$ROLE" != draining ]; then
    can_acquire=1; prev_holder=$(lease_holder)
    # a standby that is itself near its end must not grab the lease while a longer-lived candidate exists
    if [ "$HANDOFF" -eq 1 ] && [ "$ROLE" != active ] && [ -n "$(best_successor)" ]; then can_acquire=0; fi
    if [ "$can_acquire" -eq 1 ] && lease_acquire "$JOB"; then
      [ "$ROLE" != active ] && { ROLE=active; log "ACTIVE"; notify "sol-agent active on $(hostname -s)" "job $JOB"; fence_previous "$prev_holder"; }
      if ! bridge_up; then
        # crash-loop guard: >3 restarts in 10 min => back off 5 min and say so in the heartbeat
        [ $(( $(now) - LAST_START )) -lt 600 ] && RESTARTS=$((RESTARTS+1)) || RESTARTS=0
        if [ "$RESTARTS" -gt 3 ]; then log "bridge crash-looping; backing off 300s"; sleep 300 & wait $!; RESTARTS=0; fi
        bridge_start
      fi
    else
      [ "$ROLE" = active ] && log "lost lease to $(lease_holder)"
      [ "$ROLE" = active ] && bridge_stop
      ROLE=standby
    fi
  fi

  hb_write "$ROLE" "\"bridge\":\"$(bridge_up && echo up || echo down)\",\"restarts\":$RESTARTS,\"handoff\":$HANDOFF,\"eff_s\":$EFF_S"

  # backup watchdog: if the scrontab watchdog went silent, the active sol-agent does its job
  if [ "$ROLE" = active ] && [ -z "${SOL_AGENT_NO_BACKUP_WD:-}" ] && [ "$(now)" -ge "$WD_BACKUP_AT" ]; then
    wdage=$(( $(now) - $(stat -c %Y "$STATE/wd.json" 2>/dev/null || echo 0) ))
    if [ "$wdage" -gt "${WD_STALE_S:-900}" ]; then
      log "watchdog silent for ${wdage}s: running it from the sol-agent"; COMP=sol-agent-as-wd "$AR/bin/sol-agent-wd.sh" --from-sol-agent
      # the usual reason the watchdog goes quiet is that its scrontab entry vanished (e.g. after maintenance): put it back
      if [ -z "${SOL_AGENT_NO_SCRON:-}" ] && ! scrontab -l 2>/dev/null | grep -qF '>>> sol-agent >>>'; then
        "$AR/bin/sol-agent" wd-install >/dev/null 2>&1 && log "scrontab entry was missing: reinstalled"
      fi
      WD_BACKUP_AT=$(( $(now) + 900 ))
    fi
  fi

  sleep "$TICK_S" & wait $!        # `wait` so traps fire immediately instead of after the sleep
done
cleanup term
