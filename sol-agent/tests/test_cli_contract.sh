#!/usr/bin/env bash
# The command-line contract of sol-agent (the same rules `solx` documents for itself, implemented in lib/out.sh):
#   JSON on stdout when stdout is not a terminal (--json / --text force it), human text on a terminal;
#   data on stdout and diagnostics on stderr; -n previews and changes nothing; -y/-n/prompt for destructive commands, and a
#   refusal with exit 2 when nobody can answer; streams stay text unless --json is explicit; stable exit codes.
# Offline: squeue, scancel, scrontab and the VS Code CLI are stubs on PATH, nothing is submitted.
set -u
export SOL_AGENT_ROOT=${SOL_AGENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)} SOL_AGENT_CONFIG=/dev/null
unset SOL_AGENT_FORMAT
T=$(mktemp -d); export SOL_AGENT_STATE=$T/state SOL_AGENT_RESV_FILE=$T/resv SOL_AGENT_DRY_SBATCH=1 NAME=sol-agent
export CODE_BIN=$T/bin/code STUB_CALLS=$T/calls STUB_TABLE=$T/table
mkdir -p "$T/bin" "$SOL_AGENT_STATE"; : > "$SOL_AGENT_RESV_FILE"; : > "$STUB_CALLS"
pass=0; fail=0; ok() { if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "PASS $1"; else fail=$((fail+1)); echo "FAIL $1: expected [$2] got [$3]"; fi; }
SA=$SOL_AGENT_ROOT/bin/sol-agent; M=$SOL_AGENT_ROOT/bin/sol-agent-msg; TUN=$SOL_AGENT_ROOT/bin/sol-agent-tunnel.sh
calls() { grep -c "$1" "$STUB_CALLS"; }
rc() { "$@" >/dev/null 2>&1; echo $?; }

cat > "$T/bin/squeue" <<'EOF'
#!/usr/bin/env bash
# Pool: 101 RUNNING + 102 PENDING; tunnel job 103; test leftovers 201. After any scancel, id queries return nothing.
args="$*"; echo "squeue $args" >> "$STUB_CALLS"
gone() { grep -q '^scancel' "$STUB_CALLS"; }
case $args in
  *"%i|%j|%T"*) printf '%s\n' '101|sol-agent|RUNNING' '102|sol-agent|PENDING' '103|sol-agent-vscode|RUNNING' '201|sol-agent-e2e|PENDING';;
  *"%i|%T|%L|%V|%r"*) gone || printf '%s\n' '101|RUNNING|6-22:00:00|2026-10-18T00:00:00|None' '102|PENDING|7-00:00:00|2026-10-18T00:00:01|Priority';;
  *"-n sol-agent-vscode"*"%i"*) gone || echo 103;;
  *"%i %j %N"*) printf '%s\n' '103 sol-agent-vscode sc003';;
  *"-n sol-agent -o %i"*) gone || printf '%s\n' 101 102;;
  *"-j"*"%N"*) echo sc001;;
  *"-j"*"%T"*) echo RUNNING;;
  *"-j"*"%L"*) echo 6-22:00:00;;
  *"-o %i"*|*"-o '%i'"*) gone || printf '%s\n' 101 102 103;;
esac
EOF
cat > "$T/bin/scancel" <<'EOF'
#!/usr/bin/env bash
echo "scancel $*" >> "$STUB_CALLS"
EOF
cat > "$T/bin/scrontab" <<'EOF'
#!/usr/bin/env bash
echo "scrontab $*" >> "$STUB_CALLS"
case ${1:-} in -l) if [ -f "$STUB_TABLE" ]; then cat "$STUB_TABLE"; else echo "no crontab for $USER"; exit 1; fi;; -r) rm -f "$STUB_TABLE";; *) cp "$1" "$STUB_TABLE";; esac
EOF
cat > "$T/bin/code" <<'EOF'
#!/bin/sh
case "$*" in *--version*) echo "1.0-stub";; *"user show"*) echo "not logged in";; esac
EOF
chmod +x "$T/bin/"*
export PATH=$T/bin:$PATH

echo "## format selection: JSON on a pipe, text on a terminal, flags and env override"
ok "status on a pipe is one JSON document" yes "$("$SA" status 2>/dev/null | jq -e . >/dev/null && echo yes || echo no)"
ok "status has the documented keys (missing: 0)" 0 "$("$SA" status 2>/dev/null | jq -r '(["jobs","lease","maintenance","name","paused","permission_mode","pool","watchdog","workspace"] - keys)|length')"
ok "status lists the two pool jobs" "101 102" "$("$SA" status 2>/dev/null | jq -r '[.jobs[].id]|join(" ")')"
ok "status --text on a pipe is text" yes "$("$SA" status --text 2>/dev/null | grep -q '^--- jobs' && echo yes || echo no)"
ok "--json before the command works" sol-agent "$("$SA" --json status 2>/dev/null | jq -r .name)"
ok "--json after the command works" sol-agent "$("$SA" status --json 2>/dev/null | jq -r .name)"
ok "SOL_AGENT_FORMAT=text overrides the pipe" yes "$(SOL_AGENT_FORMAT=text "$SA" status 2>/dev/null | grep -q '^--- jobs' && echo yes || echo no)"
if command -v script >/dev/null 2>&1; then
  ok "on a terminal the default is text" yes "$(script -qec "$SA status" /dev/null 2>/dev/null | grep -q -- '--- jobs' && echo yes || echo no)"
  ok "on a terminal --json still gives JSON" yes "$(script -qec "$SA status --json" /dev/null 2>/dev/null | tr -d '\r' | jq -e .name >/dev/null 2>&1 && echo yes || echo no)"
fi

echo "## stdout is data, stderr is diagnostics"
out=$("$M" send alice -s hi "hello" 2>"$T/err")
ok "msg send: stdout is one JSON object" yes "$(jq -e '.sent[0].to=="alice"' <<<"$out" >/dev/null && echo yes || echo no)"
ok "msg send: the human line went to stderr" yes "$(grep -q '^sent ' "$T/err" && echo yes || echo no)"
out=$("$M" send alice --text "t" 2>"$T/err"); ok "msg send --text: the human line is on stdout, nothing on stderr" "yes 0" "$(grep -q '^sent ' <<<"$out" && echo yes) $(wc -c < "$T/err" | tr -d ' ')"
ok "msg list is a JSON array" "alice 2" "$("$M" list | jq -r '.[0]|"\(.name) \(.new)"')"
ok "msg read returns the messages and the data-not-instruction notice" "2 yes" \
   "$("$M" read alice | jq -r '"\(.messages|length) \(if (.notice|test("not a user instruction")) then "yes" else "no" end)"')"
ok "msg read acks (second read is empty JSON)" 0 "$("$M" read alice 2>/dev/null | jq '.messages|length')"
"$M" send bob "w1" >/dev/null 2>&1
ok "msg watch is text by default" yes "$("$M" watch bob --once | grep -q '^--- mailbox message for bob' && echo yes || echo no)"
"$M" send bob "w2" >/dev/null 2>&1
ok "msg watch --json is one object per line" "w2" "$("$M" watch bob --once --json | jq -r .body)"
ok "msg send with bad recipient is a usage error" 2 "$(rc "$M" send 'bad name' x)"

echo "## logs: text unless --json is explicit"
printf '%s\n' '2026-10-18T00:00:01-07:00 ctl[1] first event' '2026-10-18T00:00:02-07:00 pool[2] second event with spaces' > "$SOL_AGENT_STATE/events.log"
ok "logs on a pipe is plain text" "2026-10-18T00:00:02-07:00 pool[2] second event with spaces" "$("$SA" logs 1)"
ok "logs --json is NDJSON with ts/source/message" "pool[2]|second event with spaces" "$("$SA" logs 1 --json | jq -r '"\(.source)|\(.message)"')"
ok "logs rejects a non-number" 2 "$(rc "$SA" logs abc)"

echo "## destructive commands: -n changes nothing, no -y and no terminal refuses (exit 2)"
: > "$STUB_CALLS"
ok "down without -y/-n refuses" 2 "$(rc "$SA" down)"
ok "gc without -y/-n refuses" 2 "$(rc "$SA" gc)"
ok "gc --tests without -y/-n refuses" 2 "$(rc "$SA" gc --tests)"
ok "wd-remove without -y/-n refuses" 2 "$(rc "$SA" wd-remove)"
ok "tunnel stop without -y/-n refuses" 2 "$(rc "$TUN" stop)"
ok "-y with -n is a usage error" "2 2 2" "$(rc "$SA" down -y -n) $(rc "$SA" gc -y -n) $(rc "$TUN" stop -y -n)"
ok "refusals cancelled nothing" 0 "$(calls '^scancel')"
ok "refusal explains itself on stderr" yes "$("$SA" down 2>&1 >/dev/null | grep -q 'refusing without -y' && echo yes || echo no)"
ok "down -n: JSON plan lists what would be cancelled" "101,102 true" "$("$SA" down -n 2>/dev/null | jq -r '"\(.would_cancel|join(",")) \(.dry_run)"')"
ok "down -n changed nothing" "0 no" "$(calls '^scancel') $([ -f "$SOL_AGENT_STATE/PAUSE" ] && echo yes || echo no)"
ok "gc -n: plan has jobs 101 102 103, dry_run true" "101 102 103 true" "$("$SA" gc -n 2>/dev/null | jq -r '"\([.jobs[].id]|join(" ")) \(.dry_run)"')"
ok "gc --tests -n: only the test job" "201" "$("$SA" gc --tests -n 2>/dev/null | jq -r '[.jobs[].id]|join(" ")')"
ok "gc -n changed nothing" 0 "$(calls '^scancel')"
ok "tunnel stop -n: JSON plan" "true 103" "$("$TUN" stop -n 2>/dev/null | jq -r '"\(.dry_run) \(.would_cancel|join(" "))"')"
ok "tunnel stop -n changed nothing" 0 "$(calls '^scancel')"

echo "## on a terminal they ask first"
if command -v script >/dev/null 2>&1; then
  : > "$STUB_CALLS"; printf 'n\n' | script -qec "$SA gc" /dev/null >/dev/null 2>&1; r_n=$?
  ok "answering n aborts (exit 1) and cancels nothing" "1 0" "$r_n $(calls '^scancel')"
  : > "$STUB_CALLS"; printf 'y\n' | script -qec "$SA gc" /dev/null >/dev/null 2>&1; r_y=$?
  ok "answering y does it" "0 1" "$r_y $(calls '^scancel')"
fi

echo "## with -y the commands act and report"
: > "$STUB_CALLS"
out=$("$SA" gc -y 2>/dev/null); ok "gc -y cancels exactly the listed jobs" "scancel 101 102 103" "$(grep '^scancel' "$STUB_CALLS" | head -1)"
ok "gc -y reports the result" "false 0" "$(jq -r '"\(.dry_run) \(.result.jobs_still_listed)"' <<<"$out")"
: > "$STUB_CALLS"; rm -f "$SOL_AGENT_STATE/PAUSE"
out=$("$SA" down -y 2>/dev/null); ok "down -y cancels by name and pauses" "scancel -n sol-agent yes" "$(grep '^scancel' "$STUB_CALLS" | head -1) $([ -f "$SOL_AGENT_STATE/PAUSE" ] && echo yes || echo no)"
ok "down -y reports paused and cancelled ids" "true 101,102" "$(jq -r '"\(.paused) \(.cancelled|join(","))"' <<<"$out")"
rm -f "$SOL_AGENT_STATE/PAUSE"; : > "$STUB_CALLS"
out=$("$TUN" stop -y 2>/dev/null); ok "tunnel stop -y cancels the tunnel job" "scancel 103 true" "$(grep '^scancel' "$STUB_CALLS" | head -1 | sed 's/ $//') $(jq -r .stopped <<<"$out")"
: > "$STUB_CALLS"

echo "## watchdog entry"
rm -f "$STUB_TABLE"
ok "wd-install -n writes nothing" "0 true" "$(calls '^scrontab [^-]') $("$SA" wd-install -n 2>/dev/null | jq -r .dry_run)"
ok "wd-install installs and says so" installed "$("$SA" wd-install 2>/dev/null | jq -r .watchdog)"
cp "$STUB_TABLE" "$T/before"; ok "wd-remove -n leaves the table alone" "true same" "$("$SA" wd-remove -n 2>/dev/null | jq -r .dry_run) $(cmp -s "$T/before" "$STUB_TABLE" && echo same || echo changed)"
ok "wd-remove -y removes it" "removed no" "$("$SA" wd-remove -y 2>/dev/null | jq -r .watchdog) $([ -f "$STUB_TABLE" ] && echo yes || echo no)"
ok "wd-remove -y again is a no-op, not an error" "absent 0" "$("$SA" wd-remove -y 2>/dev/null | jq -r .watchdog) $(rc "$SA" wd-remove -y)"

echo "## up: needs a trusted workspace"
ok "up in an untrusted workspace refuses (exit 2) and queues nothing" "2 0" "$(rc "$SA" up) $(calls '^sbatch')"
ok "up -n also reports it (exit 2)" 2 "$(rc "$SA" up -n)"
mkdir -p "$T/home"; WD=$("$SA" status 2>/dev/null | jq -r .workspace.path)
jq -n --arg d "$WD" '{projects:{($d):{hasTrustDialogAccepted:true}}}' > "$T/home/.claude.json"
out=$(HOME=$T/home "$SA" up -n 2>/dev/null); ok "trusted: up -n is a JSON plan" "true true" "$(jq -r '"\(.dry_run) \(.workspace_trusted)"' <<<"$out")"
out=$(HOME=$T/home "$SA" up 2>/dev/null); ok "trusted: up returns a small result, not the status dump" "up installed 2" "$(jq -r '"\(.action) \(.watchdog) \(.jobs|length)"' <<<"$out")"
rm -f "$STUB_TABLE"
ok "--force queues even when untrusted" "0" "$(rc "$SA" up --force)"
rm -f "$STUB_TABLE" "$SOL_AGENT_STATE/PAUSE"

echo "## tunnel: structured status, usage errors, no hangs"
out=$("$TUN" status 2>/dev/null); ok "tunnel status is JSON with the job and tunnel name" "103 yes" "$(jq -r '"\(.jobs[0].id) \(if (.tunnel_name|length)>0 then "yes" else "no" end)"' <<<"$out")"
ok "tunnel status has no pending code by default" null "$(jq -c .pending_device_code <<<"$out")"
printf '{"code":"ABCD-1234","url":"https://github.com/login/device","ts":%s}\n' "$(date +%s)" > "$SOL_AGENT_STATE/tunnel/device_code"
ok "tunnel status shows a pending device code" "ABCD-1234" "$("$TUN" status 2>/dev/null | jq -r .pending_device_code.code)"
: > "$SOL_AGENT_STATE/tunnel/device_code"
ok "tunnel status --text is text" yes "$("$TUN" status --text 2>/dev/null | grep -q '^tunnel_jobs:' && echo yes || echo no)"
ok "tunnel start --job abc is a usage error" 2 "$(rc "$TUN" start --job abc)"
ok "tunnel attach without a terminal refuses" 2 "$(rc "$TUN" attach)"
ok "attach without a terminal refuses" 2 "$(rc "$SA" attach)"

echo "## help and unknown input"
ok "help prints usage on stdout, exit 0" "0 yes" "$(rc "$SA" help) $("$SA" help | grep -q '^  status' && echo yes || echo no)"
ok "help <command> prints that command" yes "$("$SA" help gc | grep -q -- '-y|-n' && echo yes || echo no)"
ok "no command: usage on stderr, exit 2" "2 0" "$(rc "$SA") $("$SA" 2>/dev/null | wc -c | tr -d ' ')"
ok "unknown command: exit 2" 2 "$(rc "$SA" frobnicate)"
ok "unknown option: exit 2" "2 2" "$(rc "$SA" gc --nope) $(rc "$SA" up --nope)"

echo "## shipped defaults waiting beside a changed config.env / CLAUDE.md (left by \`solx agent install\`)"
ok "status: no .new files, seed_updates is empty" "[]" "$("$SA" status 2>/dev/null | jq -c .seed_updates)"
R=$T/root; mkdir -p "$R/workspace"; cp -r "$SOL_AGENT_ROOT/bin" "$SOL_AGENT_ROOT/lib" "$R/"; : > "$R/config.env"
echo "# shipped" > "$R/config.env.new"; echo "# shipped" > "$R/workspace/CLAUDE.md.new"
ok "status lists each .new file" '["config.env.new","workspace/CLAUDE.md.new"]' "$(SOL_AGENT_ROOT=$R "$R/bin/sol-agent" status 2>/dev/null | jq -c .seed_updates)"
ok "status text names them" yes "$(SOL_AGENT_ROOT=$R "$R/bin/sol-agent" status --text 2>/dev/null | grep -q 'new defaults waiting.*config.env.new' && echo yes || echo no)"
ok "status text is quiet without them" no "$("$SA" status --text 2>/dev/null | grep -q 'new defaults waiting' && echo yes || echo no)"

echo "contract: $pass passed, $fail failed"
rm -rf "$T"
[ "$fail" -eq 0 ]
