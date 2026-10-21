#!/usr/bin/env bash
# Unit tests for the "don't touch what isn't yours" fixes found in review. Offline: Slurm, scrontab and squeue are stubs on
# PATH, nothing is submitted. Covers: scrontab is never rewritten or removed when it cannot be read; `gc` and `gc --tests` select
# disjoint jobs (a test cleanup can never match the live agent); permission-mode capture cannot be faked by prompt text;
# the collector and the tunnel reject odd arguments; pool_submit honours PAUSE; private state directories.
set -u
export SOL_AGENT_ROOT=${SOL_AGENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)} SOL_AGENT_CONFIG=/dev/null
T=$(mktemp -d); export SOL_AGENT_STATE=$T/state SOL_AGENT_RESV_FILE=$T/resv SOL_AGENT_DRY_SBATCH=1 NAME=sol-agent
mkdir -p "$T/bin" "$SOL_AGENT_STATE"; : > "$SOL_AGENT_RESV_FILE"
pass=0; fail=0; ok() { if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "PASS $1"; else fail=$((fail+1)); echo "FAIL $1: expected [$2] got [$3]"; fi; }
SA=$SOL_AGENT_ROOT/bin/sol-agent

# ---- stubs --------------------------------------------------------------------------------------------------------
cat > "$T/bin/scrontab" <<'EOF'
#!/usr/bin/env bash
# table in $STUB_TABLE; STUB_FAIL=1 makes every read fail the way an unreachable controller does
echo "scrontab $*" >> "$STUB_CALLS"
case ${1:-} in
  -l) if [ "${STUB_FAIL:-0}" = 1 ]; then echo "scrontab: error: Unable to contact slurm controller (connect failure)" >&2; exit 1; fi
      if [ -f "$STUB_TABLE" ]; then cat "$STUB_TABLE"; else echo "no crontab for $USER"; exit 1; fi;;
  -r) rm -f "$STUB_TABLE";;
  *)  cp "$1" "$STUB_TABLE";;
esac
EOF
cat > "$T/bin/squeue" <<'EOF'
#!/usr/bin/env bash
# only the listing gc uses: one row per job, id|name|state
printf '%s\n' '101|sol-agent|RUNNING' '102|sol-agent|PENDING' '103|sol-agent-vscode|RUNNING' '201|sol-agent-e2e|PENDING' '202|sol-agent-tt-vscode|RUNNING' \
  '301|zz_mine|RUNNING' '302|analysis|RUNNING' '303|sol-agent-collect|RUNNING'
EOF
chmod +x "$T/bin/scrontab" "$T/bin/squeue"
export STUB_TABLE=$T/table STUB_CALLS=$T/calls; : > "$STUB_CALLS"
OLDPATH=$PATH

echo "## scrontab: never rewrite a table that could not be read"
export PATH=$T/bin:$OLDPATH
rm -f "$STUB_TABLE"; "$SA" wd-install >/dev/null 2>&1
ok "no table yet: block installed" yes "$(grep -q '>>> sol-agent >>>' "$STUB_TABLE" && echo yes || echo no)"
ok "no table yet: the 'no crontab' message is not written into the table" no "$(grep -q 'no crontab' "$STUB_TABLE" && echo yes || echo no)"
printf '%s\n' '#SCRON -J mine' '0 3 * * * ~/my-job.sh' > "$T/foreign"; cat "$STUB_TABLE" >> "$T/foreign"; cp "$T/foreign" "$STUB_TABLE"
"$SA" wd-install >/dev/null 2>&1; "$SA" wd-install >/dev/null 2>&1
ok "reinstall keeps one block" 1 "$(grep -c '>>> sol-agent >>>' "$STUB_TABLE")"
ok "reinstall keeps the user's own entries" yes "$(grep -q 'my-job.sh' "$STUB_TABLE" && echo yes || echo no)"
cp "$STUB_TABLE" "$T/before"; : > "$STUB_CALLS"
STUB_FAIL=1 "$SA" wd-install >/dev/null 2>&1; rc1=$?
STUB_FAIL=1 "$SA" wd-remove -y >/dev/null 2>&1; rc2=$?
ok "unreadable table: install refuses" 1 "$rc1"
ok "unreadable table: remove refuses"  1 "$rc2"
ok "unreadable table: table unchanged" same "$(cmp -s "$T/before" "$STUB_TABLE" && echo same || echo changed)"
ok "unreadable table: nothing written or removed (only -l calls)" 0 "$(grep -vc '^scrontab -l$' "$STUB_CALLS")"
"$SA" wd-remove -y >/dev/null 2>&1
ok "remove drops our block" 0 "$(grep -c '>>> sol-agent >>>' "$STUB_TABLE")"
ok "remove keeps the user's own entries" yes "$(grep -q 'my-job.sh' "$STUB_TABLE" && echo yes || echo no)"
rm -f "$STUB_TABLE"; "$SA" wd-install >/dev/null 2>&1; : > "$STUB_CALLS"; "$SA" wd-remove -y >/dev/null 2>&1
ok "only our block present: remove deletes the table" no "$([ -f "$STUB_TABLE" ] && echo yes || echo no)"

echo "## gc: --tests never matches the live agent; plain gc never matches test jobs"
ids() { jq -r '.jobs[].id' | sort -n | paste -sd' ' -; }
ok "gc selects the agent and its tunnel job only" "101 102 103" "$("$SA" gc -n --json 2>/dev/null | ids)"
ok "gc --tests selects only test jobs" "201 202 303" "$("$SA" gc --tests -n --json 2>/dev/null | ids)"
"$SA" gc -n >/dev/null 2>&1; ok "gc -n exits 0" 0 "$?"
"$SA" gc --tests -n >/dev/null 2>&1; ok "gc --tests -n exits 0" 0 "$?"
export PATH=$OLDPATH

echo "## permission-mode capture"
. "$SOL_AGENT_ROOT/lib/common.sh"
python3 -I -c 'import time; time.sleep(60)' $'--note\n--dangerously-skip-permissions' & P1=$!
python3 -I -c 'import time; time.sleep(60)' --dangerously-skip-permissions & P2=$!
python3 -I -c 'import time; time.sleep(60)' --permission-mode acceptEdits & P3=$!
sleep 0.5
m1=$(HOME=$T detect_permission_mode "$P1"); m2=$(detect_permission_mode "$P2"); m3=$(detect_permission_mode "$P3")
kill "$P1" "$P2" "$P3" 2>/dev/null; wait 2>/dev/null
ok "prompt text containing the flag on its own line does not make a bypass session" default "$m1"
ok "a real --dangerously-skip-permissions is detected" bypassPermissions "$m2"
ok "--permission-mode value is read" acceptEdits "$m3"

echo "## argument validation"
bash "$SOL_AGENT_ROOT/bin/sol-agent-collect.sbatch" 'abc;touch /tmp/x' name >/dev/null 2>&1; ok "collector rejects a session id that is not a uuid" 2 "$?"
bash "$SOL_AGENT_ROOT/bin/sol-agent-collect.sbatch" 0123abcd-0123-0123-0123-0123456789ab 'n;x' >/dev/null 2>&1; ok "collector rejects an odd session name" 2 "$?"
env -u SLURM_JOB_ID "$SOL_AGENT_ROOT/bin/sol-agent-tunnel.sh" run >/dev/null 2>&1; ok "tunnel run refuses outside a Slurm allocation (login node)" 2 "$?"
"$SOL_AGENT_ROOT/bin/sol-agent-tunnel.sh" start --job '123; touch /tmp/x' >/dev/null 2>&1; ok "tunnel start rejects a non-numeric --job" 2 "$?"

echo "## PAUSE and private state"
. "$SOL_AGENT_ROOT/lib/pool.sh"
out=$(PARTITION=general QOS=private SOL_AGENT_FAKE_NODES="" SOL_AGENT_PREEMPT_COUNT=0 pool_submit t)
ok "without PAUSE pool_submit builds an sbatch command" yes "$(grep -q '^DRY: sbatch' <<<"$out" && echo yes || echo no)"
touch "$STATE/PAUSE"; out=$(PARTITION=general QOS=private SOL_AGENT_FAKE_NODES="" SOL_AGENT_PREEMPT_COUNT=0 pool_submit t); rm -f "$STATE/PAUSE"
ok "with PAUSE pool_submit submits nothing" "" "$out"
ok "state, heartbeat and log directories are mode 700" "700 700 700" "$(stat -c %a "$STATE" "$HB" "$LOGS" | paste -sd' ' -)"

echo "safety: $pass passed, $fail failed"
rm -rf "$T"
[ "$fail" -eq 0 ]
