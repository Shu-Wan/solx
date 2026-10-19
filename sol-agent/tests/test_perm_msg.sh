#!/usr/bin/env bash
# Unit tests: permission-mode detection + plain-text mailbox (no Slurm needed).
set -u
export SOL_AGENT_FORMAT=text   # these checks read the text rendering; test_cli_contract.sh covers JSON
export SOL_AGENT_ROOT=${SOL_AGENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)} SOL_AGENT_CONFIG=/dev/null
T=$(mktemp -d); export SOL_AGENT_STATE=$T/state SOL_AGENT_MAILBOX=$T/mb; mkdir -p "$SOL_AGENT_STATE"
. "$SOL_AGENT_ROOT/lib/common.sh"; M="$SOL_AGENT_ROOT/bin/sol-agent-msg"
pass=0; fail=0
ok() { if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "PASS $1"; else fail=$((fail+1)); echo "FAIL $1: expected [$2] got [$3]"; fi; }
mode_of() { bash -c 'sleep 8; true' x "$@" & local p=$!; sleep 0.2; detect_permission_mode $p; kill $p 2>/dev/null; wait $p 2>/dev/null; }

echo "## permission mode"
ok "--dangerously-skip-permissions -> bypassPermissions" bypassPermissions "$(mode_of --dangerously-skip-permissions)"
ok "--permission-mode plan"                              plan              "$(mode_of --permission-mode plan)"
ok "--permission-mode=acceptEdits"                       acceptEdits       "$(mode_of --permission-mode=acceptEdits)"
ok "no flag -> settings defaultMode"  "$(jq -r '.permissions.defaultMode // "default"' ~/.claude/settings.json)" "$(mode_of --foo)"
[ -n "${CLAUDE_PID:-}" ] && ok "this very session" bypassPermissions "$(detect_permission_mode)"
PERMISSION_MODE=inherit; echo plan > "$STATE/permission_mode"; ok "inherit reads captured mode" plan "$(effective_permission_mode)"
PERMISSION_MODE=acceptEdits; ok "explicit mode wins" acceptEdits "$(effective_permission_mode)"
PERMISSION_MODE=none; ok "none -> empty" "" "$(effective_permission_mode)"

echo "## mailbox"
$M send alice -s "subj A" -f bob "hello
second line" >/dev/null
out=$($M read alice 2>&1)
ok "roundtrip keeps both lines + sender + subject" 3 "$(grep -cE 'hello|second line|from bob \|.*subj A' <<<"$out")"
ok "read acks (second read empty)" "(no new messages for alice)" "$($M read alice 2>&1)"
ok "stdin body"            "piped body" "$(echo 'piped body' | $M send carol -f x - >/dev/null; $M read carol 2>&1 | sed -n 2p)"
$M send ../../etc -f x hi >/dev/null 2>&1; ok "path traversal recipient rejected (exit 2)" 2 $?
$M send 'a b' -f x hi >/dev/null 2>&1;     ok "space in recipient rejected" 2 $?
ok "hostile sender name sanitised" 0 "$($M send dave -f '../../x;rm -rf' 'hi' >/dev/null; ls "$SOL_AGENT_MAILBOX"/dave/new | grep -c '/')"
ok "multi-recipient send" "1 1" "$($M send e1,e2 -f x hi >/dev/null; echo "$(ls $SOL_AGENT_MAILBOX/e1/new | wc -l) $(ls $SOL_AGENT_MAILBOX/e2/new | wc -l)")"
big=$(head -c 70000 /dev/zero | tr '\0' x); $M send big -f x "$big" >/dev/null 2>&1; ok "oversize body rejected" 2 $?
# concurrency: 40 writers across 4 parallel batches, 3 competing readers -> every message delivered exactly once
for i in $(seq 1 40); do ( $M send race -f w$i "msg-$i" >/dev/null ) & done; wait
for r in 1 2 3; do ( $M read race > "$T/r$r.out" 2>/dev/null ) & done; wait
all=$(cat "$T"/r?.out | grep -E '^msg-[0-9]+$' | sort); ok "40 writers / 3 readers: 40 delivered" 40 "$(wc -l <<<"$all")"
ok "no duplicates, none lost" 40 "$(sort -u <<<"$all" | wc -l)"
ok "mailbox drained" 0 "$(ls $SOL_AGENT_MAILBOX/race/new | wc -l)"
# watch --once + hook
$M send hk -f x "for the hook" >/dev/null; ok "hook prints unread" 1 "$($M hook hk | grep -c 'for the hook')"
ok "hook silent when empty" "" "$($M hook hk)"
$M send w -f x "w1" >/dev/null; ok "watch --once prints + acks" "1 0" "$($M watch w --once | grep -c '^w1$') $(ls $SOL_AGENT_MAILBOX/w/new | wc -l)"
# watch (streaming) sees a message that arrives later, within one interval
( $M watch live --interval 1 > "$T/live.out" & echo $! > "$T/live.pid"; sleep 4; kill "$(cat "$T/live.pid")" ) 2>/dev/null &
sleep 1.5; $M send live -f x "arrived later" >/dev/null; wait
ok "streaming watch picks up late message" 1 "$(grep -c 'arrived later' "$T/live.out")"
echo; echo "$pass passed, $fail failed"; rm -rf "$T"; [ $fail -eq 0 ]
