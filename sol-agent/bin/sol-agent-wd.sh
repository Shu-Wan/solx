#!/usr/bin/env bash
# Heartbeat + watchdog. Runs as a Slurm job fired by scrontab every 5 minutes (slurmctld-side cron: it keeps
# firing across node loss and maintenance, which is the one thing on Sol that is truly always on).
#SBATCH directives live in the scrontab block written by `sol-agent wd-install`.
set -u
. "${SOL_AGENT_ROOT:-$HOME/.solx/agent}/lib/common.sh"
. "$AR/lib/pool.sh"
COMP=${COMP:-wd}

printf '{"ts":%s,"host":"%s","job":"%s","via":"%s"}\n' "$(now)" "$(hostname -s)" "${SLURM_JOB_ID:-manual}" "${1:-scron}" > "$STATE/wd.json.tmp" \
  && mv -f "$STATE/wd.json.tmp" "$STATE/wd.json"
hb_gc

if [ ! -f "$STATE/PAUSE" ] && slurm_ok; then
  # 1. a RUNNING sol-agent whose heartbeat stopped (hung node, wedged loop): kill it so a standby / new job takes over
  while IFS='|' read -r id st _; do
    [ "$st" = RUNNING ] || continue
    run=$(dur_to_s "$(squeue -h -j "$id" -o %M 2>/dev/null)")
    age=$(hb_age "$id")
    if [ "$run" -gt "$KILL_STALE_S" ] && [ "$age" -gt "$KILL_STALE_S" ]; then
      scancel "$id" && log "killed job $id: heartbeat ${age}s old after ${run}s runtime"; notify "sol-agent: killed hung job $id"
    fi
  done < <(sol_agent_jobs)
  # 2. keep the pool at policy (resubmits after preemption/timeout/NODE_FAIL/maintenance), fit to maintenance
  pool_ensure
fi

# 3. keep the log small
for f in "$LOGS/wd.out"; do [ -f "$f" ] && [ "$(stat -c %s "$f")" -gt 1048576 ] && tail -n 200 "$f" > "$f.tmp" && mv -f "$f.tmp" "$f"; done
[ -f "$STATE/events.log" ] && [ "$(stat -c %s "$STATE/events.log")" -gt 2097152 ] && tail -n 5000 "$STATE/events.log" > "$STATE/events.log.tmp" && mv -f "$STATE/events.log.tmp" "$STATE/events.log"

# 4. optional external dead-man's switch: if these pings stop, *you* get told (healthchecks.io-style)
[ -n "$PING_URL" ] && curl -fsS -m 10 --retry 2 "$PING_URL" >/dev/null 2>&1
exit 0
