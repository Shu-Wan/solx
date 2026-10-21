#!/usr/bin/env bash
# Sol-agent pool management: how many sol-agent jobs should exist, how long each may run.
# Used by the scrontab watchdog, by a dying sol-agent (to queue its successor), and by `sol-agent up`.
# shellcheck disable=SC1091
[ -n "${AR:-}" ] || . "${SOL_AGENT_ROOT:-$HOME/.solx/agent}/lib/common.sh"
. "$AR/lib/maint.sh"

# number of this pool's jobs that ended PREEMPTED in the last PREEMPT_WINDOW_H hours (test hook: SOL_AGENT_PREEMPT_COUNT)
preempted_recently() {
  [ -n "${SOL_AGENT_PREEMPT_COUNT:-}" ] && { echo "$SOL_AGENT_PREEMPT_COUNT"; return; }
  # -E now is required: with a state filter and no end time, sacct reports only jobs that are in that state *at this moment*, so a
  # job that was preempted an hour ago is invisible and the count is always 0 (found 2026-10-21; the storm fallback never fired)
  sacct -u "$USER" -n -P -X -S "now-${PREEMPT_WINDOW_H:-12}hours" -E now -s PREEMPTED --name "$NAME" -o JobID 2>/dev/null | wc -l
}

# pool_submit <reason> [percent]: queue one sol-agent chunk, sized to end before the next maintenance window.
# percent < 100 shortens the chunk: a warm standby submitted together with the active one would otherwise expire at the
# same moment (tested: both jobs then hit the time limit together and nobody serves), so it is staggered to 60%.
# Placement: never on a node that already hosts an sol-agent (tested: active+standby co-located, one node failure or one
# owner preemption would take both); after PREEMPT_STORM preemptions in PREEMPT_WINDOW_H hours a private-QOS pool falls
# back to FALLBACK_PARTITION/QOS (public) so restart churn cannot spiral.
pool_submit() {
  local why=$1 pct=${2:-100} plan secs full sig id max ex part=$PARTITION qos=$QOS
  plan=$(chunk_seconds)
  case $plan in
    HOLD*) echo "$plan" > "$STATE/hold"; log "hold: inside/near maintenance, resume at $(date -d "@${plan#HOLD }" -Is)"; return 0;;
  esac
  rm -f "$STATE/hold"
  max=$(dur_to_s "$MAX_TIME"); secs=$(( plan > max ? max : plan )); full=$secs; secs=$(( secs * pct / 100 ))
  [ "$secs" -lt $(( MIN_USEFUL_MIN*60 )) ] && secs=$(( MIN_USEFUL_MIN*60 ))
  [ "$secs" -gt "$full" ] && secs=$full                       # never longer than the plan/partition allows
  sig=$HANDOFF_S; [ "$sig" -gt $(( secs / 2 )) ] && sig=$(( secs / 2 ))
  ex=${SOL_AGENT_FAKE_NODES-$(squeue --me -h -n "$NAME" -t R -o %N 2>/dev/null | sort -u | paste -sd, -)}
  if [ "$QOS" = private ] && [ "$(preempted_recently)" -ge "${PREEMPT_STORM:-3}" ]; then
    part=${FALLBACK_PARTITION:-public}; qos=${FALLBACK_QOS:-public}
    log "preemption storm ($(preempted_recently) in ${PREEMPT_WINDOW_H:-12}h): this submission goes to $part/$qos instead of $PARTITION/$QOS"
  fi
  [ -f "$STATE/PAUSE" ] && { log "paused: not submitting ($why)"; return 0; }      # re-check: `down` may have run since pool_ensure looked
  local cmd=(sbatch --parsable -J "$NAME" -p "$part" -q "$qos" -c "$CPUS" --mem="$MEM" ${ex:+--exclude="$ex"}
             -t "$(s_to_dur "$secs")" --signal="B:USR1@$sig" --open-mode=append -o "$LOGS/job-%j.out" ${SBATCH_EXTRA:-} "$AR/bin/sol-agent.sbatch")
  if [ -n "${SOL_AGENT_DRY_SBATCH:-}" ]; then echo "DRY: ${cmd[*]}"; return 0; fi
  id=$("${cmd[@]}" 2>&1) || { log "sbatch FAILED: $id"; return 1; }
  id=${id%%;*}
  if [ -f "$STATE/PAUSE" ]; then scancel "$id" 2>/dev/null; log "paused while submitting: cancelled $id"; return 0; fi        # `down` ran between the check and sbatch
  log "submitted $id reason=$why limit=$(s_to_dur "$secs") part=$part/$qos${ex:+ exclude=$ex}"; echo "$id"
}

# shorten PENDING sol-agent jobs so they can start (and end) before the next maintenance window.
# Sol does not let users modify a RUNNING job ("Only pending jobs can be modified"), so running sol-agents
# enforce the window themselves: see the effective-end logic in bin/sol-agent.sbatch.
pool_fit_running() {
  local id st el tot cap
  while IFS='|' read -r id st _; do
    [ -n "$id" ] && [ "$st" = PENDING ] || continue
    IFS='|' read -r el tot < <(squeue -h -j "$id" -o '%M|%l' 2>/dev/null)
    [ -n "$tot" ] || continue
    el=0:00
    cap=$(running_job_cap "$(dur_to_s "$el")" "$(dur_to_s "$tot")")
    if [ -n "$cap" ]; then
      scontrol update JobId="$id" TimeLimit="$(s_to_dur "$cap")" 2>/dev/null \
        && log "capped job $id TimeLimit -> $(s_to_dur "$cap") (maintenance window ahead)"
    fi
  done < <(sol_agent_jobs)
}

# record queue latency once per started job: the number that tells whether STANDBY=handoff is enough
pool_record_latency() {
  local id st f
  while IFS='|' read -r id st _; do
    [ "$st" = RUNNING ] || continue
    [ -f "$STATE/latency/$id" ] && continue
    mkdir -p "$STATE/latency"
    f=$(scontrol show job "$id" 2>/dev/null | tr ' ' '\n' | grep -E '^(SubmitTime|StartTime)=' | cut -d= -f2 | tr '\n' ' ')
    set -- $f
    [ -n "${2:-}" ] && { echo "$(( $(date -d "$2" +%s) - $(date -d "$1" +%s) ))" > "$STATE/latency/$id"; \
      echo "$(date -Is) job=$id queue_wait_s=$(cat "$STATE/latency/$id") part=$PARTITION" >> "$STATE/latency.log"; }
  done < <(sol_agent_jobs)
}

# the one policy function
pool_ensure() {
  [ -f "$STATE/PAUSE" ] && { echo paused; return 0; }
  slurm_ok || { log "slurm unreachable; not acting"; return 0; }
  local R=0 PD=0 minleft=999999999 id st left l total why=""
  while IFS='|' read -r id st left _; do
    [ -n "$id" ] || continue
    case $st in
      RUNNING) R=$((R+1)); l=$(dur_to_s "$left"); [ "$l" -lt "$minleft" ] && minleft=$l;;
      COMPLETING|COMPLETED|CANCELLED|TIMEOUT|FAILED|NODE_FAIL|PREEMPTED|STOPPED) ;;   # on its way out: not alive
      *)       PD=$((PD+1));;
    esac
  done < <(sol_agent_jobs)
  total=$((R+PD))
  if   [ "$total" -eq 0 ]; then why=none-alive
  elif [ "$total" -lt 2 ]; then
    case $STANDBY in
      always)  why=standby;;
      handoff) [ "$R" -ge 1 ] && [ "$minleft" -lt $(( LEAD_MIN*60 )) ] && why=successor-lead;;
    esac
  fi
  if [ -n "$why" ]; then
    # pool_submit prints the new job id (or the DRY: line) on stdout for direct callers; here it is only a log line, and `up` / `resume`
    # print one JSON document on stdout after this returns
    if [ "$why" = standby ]; then pool_submit "$why" "${STAGGER_PCT:-60}" >&2; else pool_submit "$why" >&2; fi
  fi
  if [ "$total" -gt 2 ]; then   # over-provisioned (races): drop the newest pending one
    id=$(sol_agent_jobs | awk -F'|' '$2!="RUNNING"{print $4" "$1}' | sort | tail -1 | awk '{print $2}')
    [ -n "$id" ] && scancel "$id" && log "cancelled surplus pending $id"
  fi
  pool_fit_running
  pool_record_latency
}
