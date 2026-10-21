#!/usr/bin/env bash
# LIVE check of the device-code path with Sol's REAL VS Code CLI. Safe: a throwaway data dir, and the code is never approved,
# so no GitHub login happens and no tunnel is ever registered. Verifies: Sol's binary is used, the dedicated job starts, the
# real output format yields a code, the code is relayed (state file, mailbox, stdout), status shows it, stop leaves nothing.
set -u
export SOL_AGENT_FORMAT=text   # these checks read the text rendering; test_cli_contract.sh covers JSON
export SOL_AGENT_ROOT=${SOL_AGENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)} SOL_AGENT_CONFIG=/dev/null NAME=sol-agent-lt
export SOL_AGENT_STATE=$SOL_AGENT_ROOT/state-lt SOL_AGENT_MAILBOX=$SOL_AGENT_ROOT/state-lt/mailbox CODE_DATA=$SOL_AGENT_ROOT/state-lt/codedata
export TUNNEL_PARTITION=htc TUNNEL_QOS=public TUNNEL_TIME=0:10:00
unset CODE_BIN
rm -rf "$SOL_AGENT_STATE"; mkdir -p "$CODE_DATA"
T=$SOL_AGENT_ROOT/bin/sol-agent-tunnel.sh; M=$SOL_AGENT_ROOT/bin/sol-agent-msg; . "$SOL_AGENT_ROOT/lib/common.sh"
pass=0; fail=0; ok() { if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "PASS $1"; else fail=$((fail+1)); echo "FAIL $1: expected [$2] got [$3]"; fi; }
$T start --wait 100 >"$SOL_AGENT_STATE/out" 2>&1; rc=$?
ok "start returns 3 (code issued, waiting for a human)"        3 "$rc"
CODE=$(grep -oE 'DEVICE CODE: [A-Z0-9]{4}-[A-Z0-9]{4}' "$SOL_AGENT_STATE/out" | awk '{print $3}')
ok "real CLI output yields a XXXX-XXXX code on stdout"          yes "$([ -n "$CODE" ] && echo yes || echo no)"
ok "state file holds the same code"                             "$CODE" "$(jq -r .code "$STATE/tunnel/device_code" 2>/dev/null)"
ok "mailbox 'user' got it"                                      yes "$(waitfor() { for i in $(seq 1 40); do $M read user 2>&1 | grep -q "$CODE" && return 0; sleep 2; done; return 1; }; waitfor && echo yes || echo no)"
ok "status reports Sol's own CLI and its version"               1 "$($T status | head -1 | grep -c 'cli=/packages/apps/vscode/latest/bin/code (1\.[0-9]*\.[0-9]*)')"
echo "--- status:"; $T status | sed 's/^/    /'
ok "status shows the pending code"                              1 "$($T status | grep -c "pending device code: $CODE")"
$T stop -y >/dev/null
for i in $(seq 1 40); do [ -z "$(squeue --me -h -n sol-agent-lt-vscode -o %i)" ] && break; sleep 1; done
ok "stop leaves no job"                                         0 "$(squeue --me -h -n sol-agent-lt-vscode -o %i | wc -l | tr -d ' ')"
ok "nothing logged in, nothing registered (data dir has no token)" 0 "$(ls "$CODE_DATA" 2>/dev/null | grep -c token)"
echo; echo "$pass passed, $fail failed"
cp "$SOL_AGENT_STATE/out" "$SOL_AGENT_ROOT/results/live_tunnel_start.out" 2>/dev/null; sed -i -E 's/[A-Z0-9]{4}-[A-Z0-9]{4}/XXXX-XXXX/g' "$SOL_AGENT_ROOT/results/live_tunnel_start.out"
rm -f "$LOGS"/vscode-*.out; rm -rf "$SOL_AGENT_STATE"; [ $fail -eq 0 ]
