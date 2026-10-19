#!/usr/bin/env bash
# Pool health and alerts. Sourced by the watchdog (bin/sol-agent-wd.sh, every 5 min) and by `sol-agent status`.
#
# Levels, from `pool_health`:
#   ok        an alive active session and, with STANDBY=always, a RUNNING standby
#   degraded  an alive active session but no RUNNING standby (a failover would cost minutes, not seconds)
#   down      nobody serves: no alive lease holder, or its bridge is down
#   paused    `sol-agent down` was run (PAUSE file): expected, never alerted
#   hold      waiting out a maintenance window with nothing running: expected, never alerted
#   unknown   Slurm did not answer: the stored state is kept, nothing is concluded
#
# `health_update` compares the level with the one stored in $STATE/health ("<level> <since> <alerted_at>", replaced by
# rename like the lease) and alerts on the *lasting* states only: a failover takes seconds and must not page anyone, so
# `down` alerts after ALERT_DOWN_MIN, `degraded` after ALERT_DEGRADED_MIN, a persisting `down` repeats every ALERT_REPEAT_H,
# and leaving an alerted state sends one "recovered" message. Every change of level goes to events.log either way.
# Sets HEALTH_LEVEL and HEALTH_ALERTING (1 while a DOWN alert is standing) for the watchdog's dead-man ping.
# shellcheck disable=SC1091
[ -n "${AR:-}" ] || . "${SOL_AGENT_ROOT:-$HOME/.solx/agent}/lib/common.sh"

HEALTH_LEVEL=unknown; HEALTH_ALERTING=0

pool_health() {
  [ -f "$STATE/PAUSE" ] && { echo paused; return; }
  slurm_ok || { echo unknown; return; }
  local h running
  h=$(lease_holder)
  if [ -n "$h" ] && holder_alive "$h" && [ "$(jq -r '.bridge // "up"' "$HB/$h.json" 2>/dev/null)" != down ]; then
    running=$(sol_agent_jobs | awk -F'|' '$2=="RUNNING"' | wc -l)
    if [ "$STANDBY" = always ] && [ "$running" -lt 2 ]; then echo degraded; else echo ok; fi
    return
  fi
  if [ -f "$STATE/hold" ]; then echo hold; else echo down; fi
}

# 754 -> "12 min", 7500 -> "2 h 5 min"
fmt_dur() {
  local t=$1
  if [ "$t" -lt 3600 ]; then echo "$(( (t + 30) / 60 )) min"; else echo "$(( t / 3600 )) h $(( t % 3600 / 60 )) min"; fi
}

# one line for the alert body: the pool as Slurm sees it, then the last events
pool_summary() {
  local jobs events
  jobs=$(sol_agent_jobs | awk -F'|' '{printf "%s%s %s(%s)", s, $1, $2, $5; s=", "}')
  events=$(tail -n 3 "$STATE/events.log" 2>/dev/null | cut -c1-110 | paste -sd'|' -)
  printf 'jobs: %s\nlast events: %s\n' "${jobs:-none}" "${events:-none}"
}

health_update() {
  local lvl t prev since alerted limit="" subj="" prio=default age
  lvl=$(pool_health); t=$(now)
  [ -f "$STATE/health" ] && read -r prev since alerted < "$STATE/health"
  if [ "$lvl" = unknown ]; then HEALTH_LEVEL=${prev:-unknown}; [ "${prev:-}" = down ] && [ "${alerted:-0}" -gt 0 ] && HEALTH_ALERTING=1; return 0; fi
  [ -n "${prev:-}" ] || { prev=ok; since=$t; alerted=0; }
  : "${since:=$t}" "${alerted:=0}"
  if [ "$lvl" != "$prev" ]; then
    log "health $prev -> $lvl"
    if [ "$alerted" -gt 0 ]; then
      case "$prev:$lvl" in
        down:ok|down:degraded) notify "sol-agent recovered after $(fmt_dur $(( t - since )))" "$(pool_summary)";;
        degraded:ok)           notify "sol-agent has a standby again after $(fmt_dur $(( t - since )))" "$(pool_summary)";;
      esac
    fi
    prev=$lvl; since=$t; alerted=0
  fi
  age=$(( t - since ))
  case $lvl in
    down)     limit=$(( ALERT_DOWN_MIN * 60 )); prio=high; subj="sol-agent DOWN: no active session for $(fmt_dur "$age")";;
    degraded) limit=$(( ALERT_DEGRADED_MIN * 60 )); subj="sol-agent has no standby for $(fmt_dur "$age")";;
  esac
  if [ -n "$limit" ]; then
    if [ "$alerted" -eq 0 ] && [ "$age" -ge "$limit" ]; then
      notify "$subj" "$(pool_summary)"$'\n'"The watchdog resubmits every 5 min. Check: solx agent status. If this persists: solx agent up" "$prio"; alerted=$t
    elif [ "$alerted" -gt 0 ] && [ "$lvl" = down ] && [ $(( t - alerted )) -ge $(( ALERT_REPEAT_H * 3600 )) ]; then
      notify "STILL DOWN: ${subj#sol-agent DOWN: }" "$(pool_summary)" "$prio"; alerted=$t
    fi
  fi
  HEALTH_LEVEL=$lvl
  [ "$lvl" = down ] && [ "$alerted" -gt 0 ] && HEALTH_ALERTING=1
  printf '%s %s %s\n' "$lvl" "$since" "$alerted" > "$STATE/health.tmp" && mv -f "$STATE/health.tmp" "$STATE/health"
  return 0
}
