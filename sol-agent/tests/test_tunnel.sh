#!/usr/bin/env bash
# Plumbing test for sol-agent-tunnel.sh with a STUB VS Code CLI. The stub models the two real behaviours that matter:
#  * the login token is MACHINE-BOUND (measured with the real binary: tests/probes/probe_token_machine_bound.sh),
#  * a second tunnel is refused while a lock file exists, and only a clean (TERM) exit removes it.
# The real device-code output format is captured with the real binary in tests/live_tunnel.sh; what happens after a human
# approves on GitHub (real tunnel registration) is NOT covered here.
set -u
export SOL_AGENT_FORMAT=text   # these checks read the text rendering; test_cli_contract.sh covers JSON
export SOL_AGENT_ROOT=${SOL_AGENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)} SOL_AGENT_CONFIG=/dev/null NAME=sol-agent-tt
export SOL_AGENT_STATE=$SOL_AGENT_ROOT/state-tt
export SOL_AGENT_MAILBOX=$SOL_AGENT_STATE/mailbox STUB_DIR=$SOL_AGENT_STATE/stub CODE_DATA=$SOL_AGENT_STATE/codedata
export TUNNEL_PARTITION=htc TUNNEL_QOS=public TUNNEL_TIME=0:08:00
rm -rf "$SOL_AGENT_STATE"; mkdir -p "$STUB_DIR" "$CODE_DATA"
cat > "$SOL_AGENT_STATE/code" <<'EOF'
#!/usr/bin/env bash
D=${STUB_DIR:?}; L=${CODE_DATA:?}/tunnel-stable.lock; H=$(hostname -s); args="$*"
authorized() { [ -f "$D/authorized.$H" ]; }
login() { echo "To grant access to the server, please log into https://github.com/login/device and use code ABCD-1234"
  for i in $(seq 1 120); do [ -f "$D/approve" ] && { touch "$D/authorized.$H"; echo "Successfully logged in"; return 0; }; sleep 1; done; return 1; }
case "$args" in
  *"--version"*) echo "code 1.140.0 (stub)";;
  *"tunnel user show"*) authorized && echo "logged in with provider GitHub Account" || echo "not logged in";;
  *"tunnel user login"*) login || exit 1;;
  *"tunnel status"*) echo '{"tunnel":null,"service_installed":false}';;
  *"tunnel "*)
    [ -e "$L" ] && { echo "Error: another tunnel is running (lock $L)"; exit 1; }
    authorized || login || exit 1
    echo $$ > "$L"; trap 'rm -f "$L"; exit 0' TERM INT
    echo "Open this link in your browser https://vscode.dev/tunnel/stub/home/x"; echo "RUNNING host=$H job=${SLURM_JOB_ID:-none} step=${SLURM_STEP_ID:-none}"
    while :; do sleep 1; done;;
esac
EOF
chmod +x "$SOL_AGENT_STATE/code"; export CODE_BIN=$SOL_AGENT_STATE/code
T=$SOL_AGENT_ROOT/bin/sol-agent-tunnel.sh; M=$SOL_AGENT_ROOT/bin/sol-agent-msg; . "$SOL_AGENT_ROOT/lib/common.sh"
LOCKF=$CODE_DATA/tunnel-stable.lock; TL=$STATE/tunnel/tunnel.log
pass=0; fail=0; ok() { if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "PASS $1"; else fail=$((fail+1)); echo "FAIL $1: expected [$2] got [$3]"; fi; }
njobs() { squeue --me -h -t R,PD -n "$1" -o %i | wc -l; }
waitfor() { local i; for i in $(seq 1 "${3:-90}"); do eval "$1" && return 0; sleep 1; done; return 1; }   # waitfor 'cmd' label [secs]

# 1. dedicated job (like Sol's `vscode`), unauthorised on its node: the device code comes back, approval brings the tunnel up by itself
$T start --wait 150 >"$SOL_AGENT_STATE/s1.out" 2>&1 & SP=$!
waitfor '[ -s "$STATE/tunnel/device_code" ]' code 120
ok "device code relayed to the state file"            ABCD-1234 "$(jq -r .code "$STATE/tunnel/device_code" 2>/dev/null)"
ok "code relayed to the 'user' mailbox"               yes "$(waitfor '$M read user 2>&1 | grep -q ABCD-1234' mb 60 && echo yes || echo no)"
touch "$STUB_DIR/approve"
wait $SP; rc=$?
ok "start printed the code and, after approval, 'tunnel up'" "0 1 1" "$rc $(grep -c 'DEVICE CODE: ABCD-1234' "$SOL_AGENT_STATE/s1.out") $(grep -c 'tunnel up (in job' "$SOL_AGENT_STATE/s1.out")"
J1=$(squeue --me -h -t R -n sol-agent-tt-vscode -o %i)
ok "exactly one dedicated tunnel job is RUNNING"        1 "$(njobs sol-agent-tt-vscode)"
ok "tunnel ran inside that job (stub saw job id)"       1 "$(grep -c "job=$J1" "$TL")"
ok "current file names the job"                         "$J1" "$(awk '{print $1}' "$STATE/tunnel/current" 2>/dev/null)"
ok "device_code cleared once approved"                  0 "$(wc -c < "$STATE/tunnel/device_code" | tr -d ' ')"
ok "the CLI runs in a tmux session on its node (attachable)"   yes "$(srun --jobid="$J1" --overlap --cpu-bind=none -N1 -n1 --mem=0 tmux -L "sol-agent-tt-tunnel-run-$J1" has-session -t code-tunnel </dev/null >/dev/null 2>&1 && echo yes || echo no)"
ok "auth_node records the machine with the working login"   "$(squeue --me -h -t R -n sol-agent-tt-vscode -o %N)" "$(waitfor '[ -s "$STATE/tunnel/auth_node" ]' an 30; cat "$STATE/tunnel/auth_node" 2>/dev/null)"
ok "tunnel lock present while running"                  yes "$([ -e "$LOCKF" ] && echo yes || echo no)"

# 2. idempotent start
$T start --wait 5 >"$SOL_AGENT_STATE/s2.out" 2>&1
ok "second start does not submit another job"           "1 1" "$(njobs sol-agent-tt-vscode) $(grep -c 'already exists' "$SOL_AGENT_STATE/s2.out")"
out=$(SOL_AGENT_FORMAT=json $T start --wait 5 2>/dev/null); ok "second start as JSON: already_running, exit 0" "already_running 0" "$(jq -r '"\(.state) \(.exit_code)"' <<<"$out")"
ok "tunnel status as JSON names the running job and tunnel"  "$J1 yes" "$(SOL_AGENT_FORMAT=json $T status 2>/dev/null | jq -r '"\(.jobs[0].id) \(if .url != null then "yes" else "no" end)"')"

# 3. stop is clean: TERM reaches the CLI, which releases its lock
$T stop -y >/dev/null; waitfor '[ "$(njobs sol-agent-tt-vscode)" = 0 ] && [ -z "$(squeue --me -h -j '"$J1"' -o %T)" ]' gone 60
ok "stop cancels the job"                                0 "$(njobs sol-agent-tt-vscode)"
ok "clean stop removed the lock (TERM path)"             no "$(waitfor '[ ! -e "$LOCKF" ]' lock 40 && echo no || echo yes)"

# 3b. the next start prefers the machine whose login still works: no device code needed (stub token is machine-bound too)
AN=$(cat "$STATE/tunnel/auth_node"); rm -f "$STUB_DIR/approve"
$T start --wait 90 >"$SOL_AGENT_STATE/s3b.out" 2>&1; rc=$?
ok "restart lands on the authorised machine and needs no code" "0 0 1 $AN" "$rc $(grep -c 'DEVICE CODE' "$SOL_AGENT_STATE/s3b.out") $(grep -c 'tunnel up' "$SOL_AGENT_STATE/s3b.out") $(squeue --me -h -t R -n sol-agent-tt-vscode -o %N)"
$T stop -y >/dev/null; waitfor '[ "$(squeue --me -h -t R,PD,CG -n sol-agent-tt-vscode -o %i | wc -l)" = 0 ]' gone 60; touch "$STUB_DIR/approve"

# 3c. a machine without a working login, started as JSON (what an agent does): exit 3, awaiting_approval, the code in the document
rm -f "$STUB_DIR/approve"
out=$(TUNNEL_PREFER_AUTH_NODE=0 TUNNEL_SBATCH_EXTRA="-x $AN" SOL_AGENT_FORMAT=json $T start --wait 60 2>"$SOL_AGENT_STATE/s3c.err"); rc=$?
ok "JSON start on a new machine: exit 3, awaiting_approval, the device code" "3 awaiting_approval ABCD-1234" "$rc $(jq -r .state <<<"$out") $(jq -r .device_code.code <<<"$out")"
[ "$rc" = 3 ] || { echo "--- debug 3c: out=$out"; cat "$SOL_AGENT_STATE/s3c.err"; tail -6 "$STATE/events.log"; squeue --me -h -o '%i %j %T %N %r' | grep sol-agent-tt; }
ok "tunnel status as JSON shows the pending code"        ABCD-1234 "$(SOL_AGENT_FORMAT=json $T status 2>/dev/null | jq -r .pending_device_code.code)"
touch "$STUB_DIR/approve"
ok "after approval the tunnel comes up by itself (status has the url)" yes "$(waitfor 'SOL_AGENT_FORMAT=json $T status 2>/dev/null | jq -e ".url != null" >/dev/null' up 90 && echo yes || echo no)"
$T stop -y >/dev/null; waitfor '[ "$(squeue --me -h -t R,PD,CG -n sol-agent-tt-vscode -o %i | wc -l)" = 0 ]' gone 60

# 4. stale lock after an unclean exit is cleared automatically
echo 99999 > "$LOCKF"
$T start --wait 3 >"$SOL_AGENT_STATE/s4.out" 2>&1; $T stop -y >/dev/null
ok "stale lock (no tunnel job) removed before starting"  1 "$(grep -c 'removed stale tunnel lock' "$SOL_AGENT_STATE/s4.out")"
waitfor '[ "$(squeue --me -h -t R,PD,CG -n sol-agent-tt-vscode -o %i | wc -l)" = 0 ]' gone 60; rm -f "$LOCKF"

# 5. a tunnel started elsewhere (Sol's `vscode` names its job "code") protects the lock and prevents a duplicate
FJ=$(sbatch --parsable -p htc -q public -t 0:06:00 -c 1 --mem=2G -J code -o /dev/null --wrap='sleep 300')
waitfor '[ "$(squeue --me -h -j '"$FJ"' -o %T)" = RUNNING ]' fj 60; echo 99999 > "$LOCKF"
$T start --wait 3 >"$SOL_AGENT_STATE/s5.out" 2>&1; rc=$?
ok "foreign tunnel job: start reports it, lock kept, no duplicate" "0 yes 0 1" "$rc $([ -e "$LOCKF" ] && echo yes || echo no) $(njobs sol-agent-tt-vscode) $(grep -c 'outside the sol-agent' "$SOL_AGENT_STATE/s5.out")"
scancel "$FJ"; rm -f "$LOCKF"

# 6. never on a login node
env -u SLURM_JOB_ID "$T" start --here >/dev/null 2>&1; ok "--here refuses outside an allocation (login node)" 2 $?

# 7. inside another job's allocation (srun --overlap), restart replaces the instance
J=$(sbatch --parsable -p htc -q public -t 0:08:00 -c 1 --mem=2G -J sol-agent-tt-target -o /dev/null --wrap='sleep 420')
waitfor '[ "$(squeue --me -h -j '"$J"' -o %T)" = RUNNING ]' tj 60; TN=$(squeue -h -j "$J" -o %N)
$T start --job "$J" --wait 120 >"$SOL_AGENT_STATE/s7.out" 2>&1
ok "start --job brings the tunnel up inside the target allocation" 1 "$(grep -c 'tunnel up (inside job' "$SOL_AGENT_STATE/s7.out")"
ok "tunnel process ran in job $J on its node ($TN)"       "1 1" "$(grep -c "job=$J" "$TL") $(grep -c "host=$TN" "$TL")"
ok "current file records the target job"                   "$J" "$(awk '{print $1}' "$STATE/tunnel/current")"
EX0=$(grep -c 'tunnel exited' "$STATE/events.log")
$T start --job "$J" --wait 3 >"$SOL_AGENT_STATE/s7b.out" 2>&1; rc=$?; sleep 4
ok "second start (no --restart) declines and leaves the tunnel alone" "0 1 $EX0" "$rc $(grep -c 'already running' "$SOL_AGENT_STATE/s7b.out") $(grep -c 'tunnel exited' "$STATE/events.log")"
sup() { ps -u "$USER" -o pid,args | awk '/sol-agent-tt-tunnel new-session -d -s tunnel/ && !/awk/ {print $1}'; }
OLDP=$(sup)
$T start --job "$J" --restart --wait 120 >"$SOL_AGENT_STATE/s7c.out" 2>&1; NEWP=$(sup)
ok "--restart replaces it: one supervisor, old one gone, tunnel up" "1 yes 1" "$(wc -l <<<"$NEWP" | tr -d ' ') $([ -n "$OLDP" ] && [ "$OLDP" != "$NEWP" ] && ! kill -0 "$OLDP" 2>/dev/null && echo yes || echo no) $(grep -c 'tunnel up (inside job' "$SOL_AGENT_STATE/s7c.out")"
if [ $fail -gt 0 ]; then echo "--- debug: restart output"; cat "$SOL_AGENT_STATE/s7c.out"; echo "--- tunnel.log"; tail -5 "$TL"; echo "--- events"; tail -12 "$STATE/events.log"; echo "--- ps"; ps -u "$USER" -o pid,ppid,args | grep -E 'tmux|sol-agent-tunnel|srun' | grep -v grep | cut -c1-160; fi
$T stop -y >/dev/null; sleep 2
ok "stop ends the srun step"                               0 "$(ps -u "$USER" -o args | awk -v j="--jobid=$J" '$1=="srun" && index($0,j)' | wc -l | tr -d ' ')"
scancel "$J"
echo; echo "$pass passed, $fail failed"; rm -f "$LOGS"/vscode-*.out; rm -rf "$SOL_AGENT_STATE"; [ $fail -eq 0 ]
