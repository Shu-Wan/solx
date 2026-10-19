#!/usr/bin/env bash
# Unit tests: pool placement policy (anti-affinity, storm fallback, stagger, clamp) and tombstone lease. No jobs submitted.
set -u
export SOL_AGENT_ROOT=${SOL_AGENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)} SOL_AGENT_CONFIG=/dev/null; T=$(mktemp -d); export SOL_AGENT_STATE=$T/state SOL_AGENT_RESV_FILE=$T/resv SOL_AGENT_DRY_SBATCH=1
mkdir -p "$SOL_AGENT_STATE"; : > "$SOL_AGENT_RESV_FILE"
. "$SOL_AGENT_ROOT/lib/common.sh"; . "$SOL_AGENT_ROOT/lib/pool.sh"
pass=0; fail=0; ok() { if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "PASS $1"; else fail=$((fail+1)); echo "FAIL $1: expected [$2] got [$3]"; fi; }
has() { grep -qE -e "$1" <<<"$2" && echo yes || echo no; }

echo "## placement"
PARTITION=general QOS=private MAX_TIME=7-00:00:00 SOL_AGENT_FAKE_NODES="" SOL_AGENT_PREEMPT_COUNT=0 ; out=$(pool_submit t)
ok "private pool stays private with no storm"          yes "$(has '-p general -q private' "$out")"
ok "no --exclude when no sol-agent runs"                  no  "$(has '--exclude' "$out")"
SOL_AGENT_FAKE_NODES="sc003,sc004"; out=$(pool_submit t)
ok "anti-affinity excludes nodes already hosting sol-agents" yes "$(has '--exclude=sc003,sc004' "$out")"
SOL_AGENT_PREEMPT_COUNT=2; out=$(pool_submit t);  ok "2 preemptions in window: still private" yes "$(has '-p general -q private' "$out")"
SOL_AGENT_PREEMPT_COUNT=3; out=$(pool_submit t);  ok "3 preemptions in window: falls back to public/public" yes "$(has '-p public -q public' "$out")"
ok "fallback logged"  1 "$(grep -c 'preemption storm' "$STATE/events.log")"
PARTITION=public QOS=public SOL_AGENT_PREEMPT_COUNT=9; out=$(pool_submit t); ok "a public pool never 'falls back'" yes "$(has '-p public -q public' "$out")"
SOL_AGENT_FAKE_NODES=""; PARTITION=public QOS=public MAX_TIME=1-00:00:00 SOL_AGENT_PREEMPT_COUNT=0
out=$(pool_submit t 100); ok "full chunk"  yes "$(has '-t 1-00:00:00' "$out")"
out=$(pool_submit standby 60); ok "standby staggered to 60% (14:24:00)" yes "$(has '-t 0-14:24:00' "$out")"
MAX_TIME=0:09:00 MIN_USEFUL_MIN=30; out=$(pool_submit t 100); ok "chunk never inflated past MAX_TIME by MIN_USEFUL clamp" yes "$(has '-t 0-00:09:00' "$out")"
MIN_USEFUL_MIN=30; MAX_TIME=2:00:00; out=$(pool_submit standby 10); ok "stagger clamps up to MIN_USEFUL (not below)" yes "$(has '-t 0-00:30:00' "$out")"
N=$(now); printf 'ReservationName=maint StartTime=%s EndTime=%s Flags=MAINT\n' "$(date -d @$((N-60)) +%FT%T)" "$(date -d @$((N+3600)) +%FT%T)" > "$SOL_AGENT_RESV_FILE"
ok "inside a window: no sbatch at all" "" "$(pool_submit t)"; : > "$SOL_AGENT_RESV_FILE"

echo "## tombstone lease"
SOL_AGENT_STATE=$T/s2; mkdir -p $T/s2; STATE=$SOL_AGENT_STATE; HB=$STATE/hb; mkdir -p "$HB"
job_state() { [ "$1" = 111 -o "$1" = 222 ] && echo RUNNING; }; slurm_ok() { return 0; }
for j in 111 222; do echo '{}' > "$HB/$j.json"; done
lease_acquire 111; ok "111 acquires"                       111 "$(lease_holder)"
lease_acquire 222;  ok "222 cannot take a live holder's lease" 111 "$(lease_holder)"
lease_release 222;  ok "non-holder release is a no-op"     111 "$(lease_holder)"
lease_release 111;  ok "release leaves a TOMBSTONE file (never deleted)" "yes -" "$([ -f $STATE/lease ] && echo yes) $(awk '{print $1}' $STATE/lease)"
ok "holder reads as empty after release" "" "$(lease_holder)"
lease_acquire 222;  ok "222 takes the released lease"      222 "$(lease_holder)"
job_state() { :; }; lease_acquire 111; ok "dead holder (job gone) is replaced" 111 "$(lease_holder)"

echo "## holder_alive vs invisible heartbeat (NFS)"
job_state() { echo RUNNING; }; slurm_ok() { return 0; }
squeue() { echo "${FAKE_RUN:-0:30}"; }
rm -f "$HB"/*.json
FAKE_RUN=0:30;  holder_alive 333 && r=alive || r=dead; ok "RUNNING 30 s, heartbeat file not visible yet -> still alive (no false steal)" alive "$r"
FAKE_RUN=1:59;  holder_alive 333 && r=alive || r=dead; ok "RUNNING 1:59, no heartbeat file -> still alive (inside grace)" alive "$r"
FAKE_RUN=3:00;  holder_alive 333 && r=alive || r=dead; ok "RUNNING 3:00, still no heartbeat file -> dead" dead "$r"
echo '{}' > "$HB/333.json"; holder_alive 333 && r=alive || r=dead; ok "fresh heartbeat -> alive" alive "$r"
touch -d '10 minutes ago' "$HB/333.json"; FAKE_RUN=30:00; holder_alive 333 && r=alive || r=dead; ok "stale heartbeat (10 min) -> dead" dead "$r"
job_state() { echo PENDING; }; holder_alive 333 && r=alive || r=dead; ok "non-RUNNING job -> dead" dead "$r"
echo; echo "$pass passed, $fail failed"; rm -rf "$T"; [ $fail -eq 0 ]
