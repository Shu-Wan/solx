#!/usr/bin/env bash
# End-to-end failure-injection test of the sol-agent pool on real Slurm jobs (htc/public, minutes-long chunks).
# Uses a stub bridge so no Remote Control environments are created. Results -> results/e2e.txt
set -u
export SOL_AGENT_ROOT=${SOL_AGENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)} SOL_AGENT_CONFIG=/dev/null
export SOL_AGENT_STATE=$SOL_AGENT_ROOT/state-e2e NAME=sol-agent-e2e
export PARTITION=htc QOS=public MAX_TIME=0:05:00 HANDOFF_S=60 TICK_S=5 STALE_S=30 KILL_STALE_S=60
export MIN_USEFUL_MIN=1 MAINT_MARGIN_MIN=1 STANDBY=always SOL_AGENT_NO_BACKUP_WD=1 SOL_AGENT_NO_SCRON=1
export SOL_AGENT_RESV_FILE=$SOL_AGENT_ROOT/state-e2e.resv
export BRIDGE_CMD='while :; do echo "stub-bridge job=$SLURM_JOB_ID host=$(hostname -s) $(date +%T)"; sleep 5; done'
OUT=$SOL_AGENT_ROOT/results/e2e.txt; : > "$OUT"; : > "$SOL_AGENT_RESV_FILE"
rm -rf "$SOL_AGENT_STATE"; COMP=test
. "$SOL_AGENT_ROOT/lib/common.sh"; . "$SOL_AGENT_ROOT/lib/pool.sh"
scancel -n "$NAME" 2>/dev/null

pass=0; fail=0
say()  { echo "$*" | tee -a "$OUT"; }
ok()   { pass=$((pass+1)); say "PASS  $1${2:+  ($2)}"; }
bad()  { fail=$((fail+1)); say "FAIL  $1${2:+  ($2)}"; }
wait_for() { local t=$1; shift; local end=$((SECONDS+t)); until eval "$*" 2>/dev/null; do [ $SECONDS -ge $end ] && return 1; sleep 1; done; }
role()      { jq -r .role "$HB/$1.json" 2>/dev/null; }
bridge()    { jq -r .bridge "$HB/$1.json" 2>/dev/null; }
running()   { sol_agent_jobs | awk -F'|' '$2=="RUNNING"{print $1}' | sort -n; }
nrun()      { running | wc -l; }
nalive()    { sol_agent_jobs | awk -F'|' '$2!~/^(COMPLETING)$/' | wc -l; }
holder_is() { [ "$(lease_holder)" = "$1" ] && [ "$(role "$1")" = active ] && [ "$(bridge "$1")" = up ]; }
limit_of()  { squeue -h -j "$1" -o %l; }
t0=$SECONDS

say "== S1 cold start: watchdog policy submits an active + a standby"
export STAGGER_PCT=100   # unstaggered pair for S1/S1b only: a staggered standby may win the cold-start race and hand over legitimately mid-window
pool_ensure; pool_ensure
wait_for 90 'test "$(nrun)" -eq 2' && ok "two sol-agent jobs RUNNING" "$((SECONDS-t0))s" || bad "two sol-agent jobs RUNNING" "$(sol_agent_jobs | tr '\n' ' ')"
A=$(running | head -1); B=$(running | tail -1)
wait_for 60 holder_is "$A" || wait_for 30 holder_is "$B"; ACT=$(lease_holder)
[ -n "$ACT" ] && holder_is "$ACT" && ok "exactly one ACTIVE with bridge up" "active=$ACT" || bad "one ACTIVE with bridge up" "lease=$(lease_holder)"
STB=$(running | grep -vx "$ACT" | head -1)
wait_for 30 'test "$(role "$STB")" = standby' && ok "other job is STANDBY, bridge not started" "standby=$STB bridge=$(bridge "$STB")" || bad "standby role" "$(role "$STB")"
[ "$(bridge "$STB")" = down ] || bad "standby must not run a bridge" "$(bridge "$STB")"

say "== S1b regression: a healthy ACTIVE keeps its lease while a standby polls (a standby once stole it 31 s after the active's heartbeat file was created)"
ACT0=$(lease_holder); chg=0; for i in $(seq 1 25); do [ "$(lease_holder)" = "$ACT0" ] || chg=$((chg+1)); sleep 2; done
[ "$chg" -eq 0 ] && [ -n "$ACT0" ] && ok "no spurious lease change in 50 s with a polling standby" "holder=$ACT0" || bad "spurious steal from a healthy holder" "changes=$chg now=$(lease_holder)"
ACT=$ACT0; STB=$(running | grep -vx "$ACT" | head -1); export STAGGER_PCT=60

say "== S2 preemption-equivalent: plain scancel = SIGTERM, then SIGKILL after KillWait (30s) -- same signal path as PreemptMode=CANCEL"
tk=$SECONDS; scancel "$ACT"; tc=$((SECONDS-tk))
wait_for 90 holder_is "$STB" && ok "standby promoted to ACTIVE after TERM" "scancel took ${tc}s, promoted $((SECONDS-tk))s after issuing" || bad "standby promoted after TERM" "lease=$(lease_holder)"
grep -q "sol-agent\\[$ACT\\] TERM/INT received" "$STATE/events.log" && ok "TERM trap ran and released the lease" "job=$ACT" || bad "TERM trap ran for job $ACT"
pool_ensure; wait_for 60 'test "$(nrun)" -eq 2' && ok "watchdog refilled the pool" || bad "pool refilled" "$(sol_agent_jobs | tr '\n' ' ')"
ACT=$STB; STB=$(running | grep -vx "$ACT" | head -1)

say "== S2b untrapped death: SIGKILL the ACTIVE's batch shell (no trap can run; models OOM-kill / node crash of the shell)"
tk=$SECONDS; scancel -b --signal=KILL "$ACT"
wait_for 90 holder_is "$STB" && ok "standby promoted after untrapped kill" "takeover=$((SECONDS-tk))s" || bad "standby promoted after untrapped kill" "lease=$(lease_holder)"
wait_for 60 'test -z "$(squeue -h -j "$ACT" -t RUNNING 2>/dev/null)"' && ok "Slurm ended the job (its bridge/tmux are reaped with the job)" "state=$(sacct -n -P -j "$ACT" -X -o State | head -1)" || bad "job ended after shell kill" "$(job_state "$ACT")"
pool_ensure; wait_for 60 'test "$(nrun)" -eq 2' && ok "pool refilled" || bad "pool refilled after untrapped kill" "$(sol_agent_jobs | tr '\n' ' ')"
ACT=$(lease_holder); STB=$(running | grep -vx "$ACT" | head -1)

say "== S3 natural time-limit handoff to a younger standby (older job J1, younger J2; successor must outlive J1 by > HANDOFF_S)"
scancel -n "$NAME"; wait_for 40 'test "$(nalive)" -eq 0'; STANDBY_SAVE=$STANDBY; export STANDBY=off
J1=$(pool_submit s3-old 100 | tail -1); wait_for 60 'test "$(job_state "$J1")" = RUNNING'; t1=$SECONDS; wait_for 40 holder_is "$J1"
wait_for 90 'test $((SECONDS-t1)) -ge 75'; J2=$(pool_submit s3-young 100 | tail -1); wait_for 60 'test "$(job_state "$J2")" = RUNNING'
left=$(timeleft_s "$J1"); say "   (J1=$J1 active, ${left}s left; J2=$J2 standby)"
wait_for $((left+20)) 'test "$(role "$J1")" = draining -o -z "$(job_state "$J1")"' && ok "J1 handed over at USR1" "role=$(role "$J1")" || bad "J1 handed over" "role=$(role "$J1")"
wait_for 30 holder_is "$J2" && ok "younger standby holds lease + bridge up" "lease=$(lease_holder)" || bad "handoff to younger standby" "lease=$(lease_holder)"
wait_for 60 'test -z "$(squeue -h -j "$J1" -t RUNNING 2>/dev/null)"' && ok "J1 left EARLY (did not wait for its time limit)" "J1 left=$(timeleft_s "$J1")s state=$(sacct -n -P -j "$J1" -X -o State | head -1)" || bad "J1 early exit" "$(job_state "$J1")"
scancel -n "$NAME"; wait_for 40 'test "$(nalive)" -eq 0'

say "== S3b regression: two jobs started together expire together (previously BOTH drained and nobody served)"
J3=$(pool_submit s3b-a 100 | tail -1); J4=$(pool_submit s3b-b 100 | tail -1); wait_for 60 'test "$(nrun)" -eq 2'
wait_for 40 'test -n "$(lease_holder)"'; left=$(timeleft_s "$J3"); say "   (J3=$J3 J4=$J4, ${left}s left)"
wait_for $((left-30)) 'test "$(jq -r .handoff "$HB/$J3.json" 2>/dev/null)" = 1'; sleep 15
H=$(lease_holder); [ -n "$H" ] && [ "$(bridge "$H")" = up ] && ok "someone still serving 15s after both entered handoff" "holder=$H role=$(role "$H")" || bad "no deadlock: someone serves" "lease=[$H]"
gap=0; for i in $(seq 1 $((left-45))); do [ -z "$(lease_holder)" ] && gap=$((gap+1)); sleep 1; [ -z "$(squeue -h -n "$NAME" -t RUNNING)" ] && break; done
say "   (seconds with an empty lease before both jobs ended: $gap)"
[ "$gap" -le 12 ] && ok "no serving gap while the pair ran out" "gap=${gap}s" || bad "serving gap while pair ran out" "gap=${gap}s"
scancel -n "$NAME"; wait_for 40 'test "$(nalive)" -eq 0'; export STANDBY=$STANDBY_SAVE

say "== S4 hung holder: freeze the ACTIVE's batch shell from INSIDE the job (Slurm still says RUNNING; only the stale heartbeat can reveal it)"
pool_ensure; pool_ensure; wait_for 60 'test "$(nrun)" -eq 2'; wait_for 40 'test -n "$(lease_holder)"'
ACT=$(lease_holder); STB=$(running | grep -vx "$ACT" | head -1); wait_for 60 'test "$(role "$STB")" = standby'
freeze() { srun --jobid="$1" --overlap --cpu-bind=none -N1 -n1 --mem=0 bash -c 'for p in $(scontrol listpids '"$1"' 2>/dev/null | awk "NR>1 && \$3==\"batch\"{print \$1}"); do tr "\0" " " < /proc/$p/cmdline | grep -q slurm_script && { kill -STOP $p; echo froze $p; exit 0; }; done; echo "no batch shell found"; exit 1'; }
fr=$(freeze "$ACT" 2>&1 | tail -1); say "   ($fr; Slurm state now: $(job_state "$ACT"))"
tk=$SECONDS
[ "$(job_state "$ACT")" = RUNNING ] && ok "frozen job still shows RUNNING in Slurm (so detection cannot come from job state)" || bad "frozen job should still be RUNNING" "$(job_state "$ACT")"
wait_for 120 holder_is "$STB"; tt=$((SECONDS-tk))
[ "$tt" -ge $((STALE_S-5)) ] && [ "$tt" -le 90 ] && holder_is "$STB" && ok "standby took the lease only after the heartbeat went stale" "takeover=${tt}s, STALE_S=$STALE_S" || bad "stale-heartbeat takeover" "takeover=${tt}s lease=$(lease_holder)"
wait_for 80 'test "$(hb_age "$ACT")" -gt "$KILL_STALE_S"'; COMP=wd "$SOL_AGENT_ROOT/bin/sol-agent-wd.sh"
wait_for 30 'test -z "$(squeue -h -j "$ACT" -t RUNNING 2>/dev/null)"' && ok "watchdog scancelled the hung job" || bad "watchdog killed hung job" "$(job_state "$ACT")"

say "== S5 PAUSE honoured"
touch "$STATE/PAUSE"; scancel -n "$NAME"; wait_for 40 'test "$(nalive)" -eq 0'; pool_ensure
[ "$(nalive)" -eq 0 ] && ok "paused: nothing submitted" || bad "paused" "$(sol_agent_jobs | tr '\n' ' ')"
rm -f "$STATE/PAUSE"

say "== S6 maintenance awareness (synthetic reservations)"
iso() { date -d "@$1" +%Y-%m-%dT%H:%M:%S; }
resv() { printf 'ReservationName=maint StartTime=%s EndTime=%s Nodes=ALL Flags=MAINT,IGNORE_JOBS,ALL_NODES State=INACTIVE\n' "$(iso "$1")" "$(iso "$2")"; }
export STANDBY=off
N=$(now); resv $((N-600)) $((N+3600)) > "$SOL_AGENT_RESV_FILE"; pool_ensure
[ "$(nalive)" -eq 0 ] && [ -f "$STATE/hold" ] && ok "(a) inside a window: HOLD, no submit" "$(cat "$STATE/hold")" || bad "(a) HOLD inside window" "alive=$(nalive)"
N=$(now); resv $((N+300)) $((N+7200)) > "$SOL_AGENT_RESV_FILE"; pool_ensure
J=$(sol_agent_jobs | awk -F'|' '$2!="COMPLETING"{print $1}' | sort -n | tail -1); L=$(dur_to_s "$(limit_of "$J")")
[ -n "$J" ] && [ "$L" -ge 200 ] && [ "$L" -le 240 ] && ok "(b) window in 5 min: chunk shrunk to fit (< MAX_TIME 5:00)" "limit=${L}s" || bad "(b) chunk fits before window" "job=$J limit=${L:-?}s"
scancel -n "$NAME"; wait_for 40 'test "$(nalive)" -eq 0'; : > "$SOL_AGENT_RESV_FILE"
HJ=$(sbatch --parsable --hold -J "$NAME" -p htc -q public -c 1 --mem=2G -t 0:05:00 -o /dev/null --wrap=true); before=$(dur_to_s "$(limit_of "$HJ")")
N=$(now); resv $((N+240)) $((N+7200)) > "$SOL_AGENT_RESV_FILE"; pool_fit_running; after=$(dur_to_s "$(limit_of "$HJ")")
[ "$after" -lt "$before" ] && [ "$after" -ge 150 ] && [ "$after" -le 185 ] && ok "(c) PENDING job capped to end before the window" "${before}s -> ${after}s" || bad "(c) pending job capped" "${before}s -> ${after}s"
scancel "$HJ"; : > "$SOL_AGENT_RESV_FILE"; wait_for 30 'test "$(nalive)" -eq 0'
pool_ensure; wait_for 60 'test "$(nrun)" -eq 1'; X=$(running | head -1); wait_for 40 holder_is "$X"
N=$(now); WSTART=$((N+170)); resv $WSTART $((N+7200)) > "$SOL_AGENT_RESV_FILE"
wait_for 230 'test -z "$(squeue -h -j "$X" -t RUNNING 2>/dev/null)"'; ended=$(date -d "$(sacct -n -P -j "$X" -X -o End | head -1)" +%s 2>/dev/null || echo 0)
{ [ "$ended" -gt 0 ] && [ "$ended" -lt "$WSTART" ] && grep -q "leaving before maintenance window" "$STATE/events.log"; } \
  && ok "(d) RUNNING sol-agent left by itself before the window" "ended $((WSTART-ended))s before window start; state=$(sacct -n -P -j "$X" -X -o State | head -1)" || bad "(d) running sol-agent leaves before window" "ended=$ended wstart=$WSTART state=$(job_state "$X")"
pool_ensure; [ "$(nalive)" -eq 0 ] && ok "(d') watchdog does not resubmit into the window" "$(cat "$STATE/hold" 2>/dev/null)" || bad "(d') no resubmit into window" "$(sol_agent_jobs | tr '\n' ' ')"
: > "$SOL_AGENT_RESV_FILE"

say "== S7 backup watchdog: ACTIVE sol-agent runs the watchdog if wd.json is stale"
echo '{"ts":1}' > "$STATE/wd.json"; touch -d '2 hours ago' "$STATE/wd.json"
unset SOL_AGENT_NO_BACKUP_WD; export WD_STALE_S=60 STANDBY=handoff
pool_ensure; wait_for 60 'test "$(nrun)" -ge 1'
wait_for 90 'test "$(( $(now) - $(stat -c %Y "$STATE/wd.json") ))" -lt 60' && ok "stale watchdog revived by the active sol-agent" "backup runs logged: $(grep -c 'watchdog silent' "$STATE/events.log")" || bad "backup watchdog" "wd.json age=$(( $(now) - $(stat -c %Y "$STATE/wd.json") ))s"

touch "$STATE/PAUSE"   # PAUSE first, then cancel repeatedly: an active job's backup watchdog can be mid-submission of a successor while it is being cancelled
for _ in 1 2 3 4; do scancel -n "$NAME"; sleep 5; done; wait_for 60 'test "$(nalive)" -eq 0'
cp "$STATE/events.log" "$SOL_AGENT_ROOT/results/e2e_events.log"
say ""; say "e2e: $pass passed, $fail failed in $((SECONDS-t0))s"; echo done >> "$OUT"
[ $fail -eq 0 ]
