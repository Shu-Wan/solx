#!/usr/bin/env bash
# End-to-end failure-injection test of the sol-agent pool on real Slurm jobs (htc/public, minutes-long chunks).
# Uses a stub bridge so no Remote Control environments are created. Results -> results/e2e.txt
set -u
export SOL_AGENT_ROOT=${SOL_AGENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)} SOL_AGENT_CONFIG=/dev/null
export SOL_AGENT_STATE=$SOL_AGENT_ROOT/state-e2e-s3 NAME=sol-agent-e2e
export PARTITION=htc QOS=public MAX_TIME=0:05:00 HANDOFF_S=60 TICK_S=5 STALE_S=30 KILL_STALE_S=60
export MIN_USEFUL_MIN=1 MAINT_MARGIN_MIN=1 STANDBY=always SOL_AGENT_NO_BACKUP_WD=1 SOL_AGENT_NO_SCRON=1
export SOL_AGENT_RESV_FILE=$SOL_AGENT_ROOT/state-e2e-s3.resv
export BRIDGE_CMD='while :; do echo "stub-bridge job=$SLURM_JOB_ID host=$(hostname -s) $(date +%T)"; sleep 5; done'
OUT=$SOL_AGENT_ROOT/results/e2e_s3.txt; : > "$OUT"; : > "$SOL_AGENT_RESV_FILE"
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

say "== S3 natural time-limit handoff to a younger standby (older job J1, younger J2; successor must outlive J1 by > HANDOFF_S)"
scancel -n "$NAME"; wait_for 40 'test "$(nalive)" -eq 0'; STANDBY_SAVE=$STANDBY; export STANDBY=off
J1=$(pool_submit s3-old 100 | tail -1); wait_for 60 'test "$(job_state "$J1")" = RUNNING'; t1=$SECONDS; wait_for 40 holder_is "$J1"
wait_for 90 'test $((SECONDS-t1)) -ge 75'; J2=$(pool_submit s3-young 100 | tail -1); wait_for 60 'test "$(job_state "$J2")" = RUNNING'
left=$(timeleft_s "$J1"); say "   (J1=$J1 active, ${left}s left; J2=$J2 standby)"
wait_for $((left+20)) 'test "$(role "$J1")" = draining -o -z "$(job_state "$J1")"' && ok "J1 handed over at USR1" "role=$(role "$J1")" || bad "J1 handed over" "role=$(role "$J1")"
for i in $(seq 1 12); do say "   diag t+${i}s lease=[$(lease_holder)] J2.role=[$(role "$J2")] J2.bridge=[$(bridge "$J2")] J2.hb=$(cat "$HB/$J2.json" 2>&1 | cut -c1-120)"; holder_is "$J2" && break; sleep 1; done
wait_for 30 holder_is "$J2" && ok "younger standby holds lease + bridge up" "lease=$(lease_holder)" || bad "handoff to younger standby" "lease=$(lease_holder)"
wait_for 60 'test -z "$(squeue -h -j "$J1" -t RUNNING 2>/dev/null)"' && ok "J1 left EARLY (did not wait for its time limit)" "J1 left=$(timeleft_s "$J1")s state=$(sacct -n -P -j "$J1" -X -o State | head -1)" || bad "J1 early exit" "$(job_state "$J1")"
scancel -n "$NAME"; wait_for 40 'test "$(nalive)" -eq 0'


scancel -n "$NAME"; sleep 2
say ""; say "e2e_s3: $pass passed, $fail failed"; echo done >> "$OUT"
