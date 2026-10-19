#!/usr/bin/env bash
# Maintenance awareness. Sol announces maintenance as cluster-wide reservations named "maint"
# (history: 2024-07-08, 2024-10-15, 2025-03-25, 2025-10-13, 2026-05-18, 2026-10-12; 1-4 days each,
# always starting 08:00 local). Slurm refuses to START a job whose time limit crosses a reservation,
# so an unaware 7-day submit just pends until after the window. We instead shrink the chunk to fit.
#
# Sources, merged: (1) `scontrol show reservation -o`  (or $SOL_AGENT_RESV_FILE for tests)
#                  (2) $STATE/maint.txt  lines: "<ISO start> <ISO end> [note]"  (manual, for windows
#                      announced on the dashboard/Slack before the reservation exists)
# shellcheck disable=SC1091
[ -n "${AR:-}" ] || . "${SOL_AGENT_ROOT:-$HOME/.solx/agent}/lib/common.sh"

_resv_text() {
  if [ -n "${SOL_AGENT_RESV_FILE:-}" ]; then cat "$SOL_AGENT_RESV_FILE"; else scontrol show reservation -o 2>/dev/null; fi
}

# prints "start_epoch end_epoch name" per relevant reservation (MAINT flag or name~maint)
maint_windows() {
  _resv_text | awk '
    { n="";s="";e="";f="";
      for(i=1;i<=NF;i++){ split($i,kv,"="); if(kv[1]=="ReservationName")n=kv[2];
        else if(kv[1]=="StartTime")s=kv[2]; else if(kv[1]=="EndTime")e=kv[2]; else if(kv[1]=="Flags")f=kv[2] }
      if (s!="" && (f ~ /(^|,)MAINT(,|$)/ || tolower(n) ~ /maint/)) print s, e, n }' |
  while read -r s e n; do
    printf '%s %s %s\n' "$(date -d "$s" +%s 2>/dev/null)" "$(date -d "$e" +%s 2>/dev/null)" "$n"
  done
  [ -f "$STATE/maint.txt" ] && grep -vE '^\s*(#|$)' "$STATE/maint.txt" | while read -r s e n; do
    printf '%s %s %s\n' "$(date -d "$s" +%s 2>/dev/null)" "$(date -d "$e" +%s 2>/dev/null)" "manual:${n:-note}"
  done
  true
}

# earliest window that has not finished yet: "start end name"
maint_next() { local n=${1:-$(now)}; maint_windows | awk -v n="$n" '$1~/^[0-9]+$/ && $2>n' | sort -n | head -1; }

# maint_plan [now]  ->  "NONE" | "FIT <seconds> <window_start>" | "HOLD <resume_epoch>"
#   NONE : nothing within reach, submit MAX_TIME
#   FIT  : submit a chunk of <seconds> so it ends MAINT_MARGIN_MIN before the window
#   HOLD : too close to / inside a window; do not submit, resume at <resume_epoch> (window end)
maint_plan() {
  local n=${1:-$(now)} s e nm fit
  read -r s e nm < <(maint_next "$n")
  [ -n "$s" ] || { echo NONE; return; }
  if [ "$n" -ge "$s" ]; then echo "HOLD $e"; return; fi
  fit=$(( s - n - MAINT_MARGIN_MIN*60 ))
  if [ "$fit" -lt $(( MIN_USEFUL_MIN*60 )) ]; then echo "HOLD $e"; return; fi
  if [ "$fit" -ge "$(dur_to_s "$MAX_TIME")" ]; then echo NONE; else echo "FIT $fit $s"; fi
}

# chunk_seconds [now] -> prints seconds to request, or "HOLD <epoch>"
chunk_seconds() {
  local p; p=$(maint_plan "${1:-}"); local max; max=$(dur_to_s "$MAX_TIME")
  case $p in NONE) echo "$max";; FIT*) set -- $p; echo "$2";; HOLD*) echo "$p";; esac
}

# running_job_cap <elapsed_s> <total_limit_s> [now] -> new total TimeLimit in seconds if the job would run into
# a window; else empty. Only usable on PENDING jobs on Sol (users cannot modify RUNNING jobs); running sol-agents
# compute the same bound themselves (see bin/sol-agent.sbatch).
running_job_cap() {
  local el=$1 tot=$2 n=${3:-$(now)} s e nm end new
  read -r s e nm < <(maint_next "$n"); [ -n "$s" ] && [ "$s" -gt "$n" ] || return 0
  end=$(( n + tot - el ))
  [ "$end" -gt $(( s - MAINT_MARGIN_MIN*60 )) ] || return 0
  new=$(( el + s - n - MAINT_MARGIN_MIN*60 ))
  [ "$new" -gt $(( el + 120 )) ] && echo "$new"
}
