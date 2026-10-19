#!/usr/bin/env bash
# LIVE check of the 401 fallback with Sol's real CLI: a stored login that the tunnel service rejects (a `gh auth token` login,
# measured to give HTTP 401) must be dropped and replaced by the device-code flow. Throwaway data dir and tunnel name, the
# code is never approved, so nothing is registered. The login is stored on the node the tunnel job is pinned to.
set -u
export SOL_AGENT_FORMAT=text   # these checks read the text rendering; test_cli_contract.sh covers JSON
export SOL_AGENT_ROOT=${SOL_AGENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)} SOL_AGENT_CONFIG=/dev/null NAME=sol-agent-l4
export SOL_AGENT_STATE=$SOL_AGENT_ROOT/state-l4 SOL_AGENT_MAILBOX=$SOL_AGENT_ROOT/state-l4/mailbox CODE_DATA=$SOL_AGENT_ROOT/state-l4/codedata
export TUNNEL_NAME=sol_t401 TUNNEL_PARTITION=lightwork TUNNEL_QOS=public TUNNEL_TIME=0:10:00
unset CODE_BIN; rm -rf "$SOL_AGENT_STATE"; mkdir -p "$CODE_DATA"
X=$(cat "$SOL_AGENT_ROOT/state/tunnel/auth_node" 2>/dev/null); : "${X:=sc002}"; export TUNNEL_SBATCH_EXTRA="-w $X"
C=/packages/apps/vscode/latest/bin/code
T=$SOL_AGENT_ROOT/bin/sol-agent-tunnel.sh; . "$SOL_AGENT_ROOT/lib/common.sh"
pass=0; fail=0; ok() { if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "PASS $1"; else fail=$((fail+1)); echo "FAIL $1: expected [$2] got [$3]"; fi; }
# store a (service-rejected) login on node X, token only via env
sbatch -W -p lightwork -q public -w "$X" -c 1 --mem=2G -t 3 -J l4-login -o /dev/null --wrap \
  "VSCODE_CLI_ACCESS_TOKEN=\$(gh auth token) $C tunnel user login --provider github --cli-data-dir $CODE_DATA </dev/null >/dev/null 2>&1" >/dev/null
for i in $(seq 1 40); do [ -s "$CODE_DATA/token.json" ] && break; sleep 2; done
ok "a login is stored in the throwaway dir"                      yes "$([ -s "$CODE_DATA/token.json" ] && echo yes || echo no)"
$T start --wait 100 >"$SOL_AGENT_STATE/out" 2>&1; rc=$?
ok "start ends with a device code (rc 3) instead of a dead job" 3 "$rc"
ok "the rejected login was detected and dropped"                 1 "$(grep -c 'stored login rejected' "$STATE/events.log")"
ok "a device code was issued after the 401"                      1 "$(grep -c 'DEVICE CODE: ' "$SOL_AGENT_STATE/out")"
$T stop -y >/dev/null
for i in $(seq 1 40); do [ -z "$(squeue --me -h -n sol-agent-l4-vscode -t R,PD,CG -o %i)" ] && break; sleep 1; done
ok "stop leaves no job"                                          0 "$(squeue --me -h -n sol-agent-l4-vscode -t R,PD,CG -o %i | wc -l | tr -d ' ')"
ok "nothing registered: no stored login left in the data dir"    0 "$(ls "$CODE_DATA" 2>/dev/null | grep -c token)"
grep -h 'error' "$STATE/tunnel/tunnel.log" 2>/dev/null | sed -E 's/\x1b\[[0-9;?]*[a-zA-Z]//g; s/request ID [0-9a-f-]+/request ID <id>/' | cut -c1-200 | head -2 > "$SOL_AGENT_ROOT/results/live_tunnel_401_error.txt"
if [ $fail -gt 0 ]; then echo "--- debug: start output"; cut -c1-200 "$SOL_AGENT_STATE/out"; echo "--- events"; tail -12 "$STATE/events.log" | cut -c1-180
  echo "--- tunnel.log"; sed -E 's/\x1b\[[0-9;?]*[a-zA-Z]//g' "$STATE/tunnel/tunnel.log" | grep -v '^\*' | grep . | cut -c1-180 | tail -8
  echo "--- job logs"; tail -5 "$LOGS"/vscode-*.out 2>/dev/null | cut -c1-180; fi
echo; echo "$pass passed, $fail failed"; rm -f "$LOGS"/vscode-*.out; rm -rf "$SOL_AGENT_STATE"; [ $fail -eq 0 ]
