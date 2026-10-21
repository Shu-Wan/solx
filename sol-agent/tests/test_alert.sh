#!/usr/bin/env bash
# Alerts and pool health, offline: the real watchdog (bin/sol-agent-wd.sh) runs against stub squeue / sbatch / curl with a fake clock
# (SOL_AGENT_FAKE_NOW), so a "10 minute outage" takes no time and nothing is sent anywhere. Covers:
#   health levels · one alert per lasting state, none for short gaps · repeat · recovery · paused/hold stay silent ·
#   ping vs ping/fail · channels (email, ntfy, command), private messages never reach ntfy, a dead channel never breaks the
#   watchdog · `alert test` · the status fields · the `up` warning · the model setting reaching the launch command.
set -u
export SOL_AGENT_ROOT=${SOL_AGENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)} SOL_AGENT_CONFIG=/dev/null
unset SOL_AGENT_FORMAT ALERT_EMAIL NTFY_TOPIC NOTIFY_CMD PING_URL SOL_AGENT_MODEL NOTIFY_LEVEL
T=$(mktemp -d); export SOL_AGENT_STATE=$T/state SOL_AGENT_RESV_FILE=$T/resv SOL_AGENT_DRY_SBATCH=1 NAME=sol-agent
export STUB_DIR=$T STUB_CURL_RC=0 STUB_SBATCH_RC=0
mkdir -p "$T/bin" "$SOL_AGENT_STATE/hb"; : > "$SOL_AGENT_RESV_FILE"; : > "$T/calls"; : > "$T/jobs"
pass=0; fail=0; ok() { if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "PASS $1"; else fail=$((fail+1)); echo "FAIL $1: expected [$2] got [$3]"; fi; }
SA=$SOL_AGENT_ROOT/bin/sol-agent; WD=$SOL_AGENT_ROOT/bin/sol-agent-wd.sh

cat > "$T/bin/squeue" <<'EOF'
#!/usr/bin/env bash
args="$*"
case $args in
  *"-j "*"-o %T"*) id=$(sed -E 's/.*-j ([0-9]+).*/\1/' <<<"$args"); awk -F'|' -v i="$id" '$1==i{print $2}' "$STUB_DIR/jobs";;
  *"-j "*"-o %M"*) echo 01:00:00;;
  *"-j "*"%L"*) echo 6-00:00:00;;
  *"-t R"*"%N"*) awk -F'|' '$2=="RUNNING"{print "n"$1}' "$STUB_DIR/jobs";;
  *"%i|%T|%L|%V|%r"*) cat "$STUB_DIR/jobs";;
  *"-o %i"*) awk -F'|' '{print $1}' "$STUB_DIR/jobs";;
esac
EOF
cat > "$T/bin/curl" <<'EOF'
#!/usr/bin/env bash
body=""; case "$*" in *--data-binary*) body=$(cat);; esac
printf 'curl|%s|%s\n' "$*" "$body" >> "$STUB_DIR/calls"; exit "${STUB_CURL_RC:-0}"
EOF
printf '#!/usr/bin/env bash\necho "cmd|$*" >> "$STUB_DIR/calls"\n' > "$T/notifier"; chmod +x "$T/notifier" "$T/bin/"*
for c in scancel scrontab; do printf '#!/usr/bin/env bash\necho "%s $*" >> "$STUB_DIR/calls"\n' "$c" > "$T/bin/$c"; chmod +x "$T/bin/$c"; done
printf '#!/usr/bin/%s\necho "sbatch $*" >> "$STUB_DIR/calls"\nexit "${STUB_SBATCH_RC:-0}"\n' env\ bash > "$T/bin/sbatch"; chmod +x "$T/bin/sbatch"
export PATH=$T/bin:$PATH

n() { grep -c -E "$1" "$T/calls" || true; }           # number of recorded calls matching a pattern
nmail() { n '^sbatch .*--mail-user=me@example.test'; }   # an e-mail alert is a throwaway Slurm job that mails when it ends
T0=1800000000
# pool(active-only|both|none) at fake time $1: jobs, lease and fresh heartbeats as Slurm and the jobs would leave them
pool() { # time kind
  export SOL_AGENT_FAKE_NOW=$1; rm -f "$SOL_AGENT_STATE/hb/"*.json
  case $2 in
    none) : > "$T/jobs"; printf -- '- %s\n' "$1" > "$SOL_AGENT_STATE/lease";;
    active) printf '101|RUNNING|6-00:00:00|x|None\n' > "$T/jobs"; printf '101 %s\n' "$1" > "$SOL_AGENT_STATE/lease"; hb 101 up;;
    both) printf '101|RUNNING|6-00:00:00|x|None\n102|RUNNING|4-00:00:00|x|None\n' > "$T/jobs"; printf '101 %s\n' "$1" > "$SOL_AGENT_STATE/lease"; hb 101 up; hb 102 down;;
  esac
}
hb() { printf '{"ts":%s,"job":"%s","role":"x","bridge":"%s"}\n' "$SOL_AGENT_FAKE_NOW" "$1" "$2" > "$SOL_AGENT_STATE/hb/$1.json"; touch -d "@$SOL_AGENT_FAKE_NOW" "$SOL_AGENT_STATE/hb/$1.json"; }
tick() { SOL_AGENT_FAKE_NOW=$1 bash "$WD" </dev/null >/dev/null 2>&1; echo $?; }
health() { cut -d' ' -f1 "$SOL_AGENT_STATE/health" 2>/dev/null; }

export ALERT_EMAIL=me@example.test NTFY_TOPIC=test-topic-123 PING_URL=http://ping.test/abc NOTIFY_CMD=$T/notifier

echo "## down: nothing until it has lasted ALERT_DOWN_MIN"
pool $T0 none
ok "watchdog exits 0" 0 "$(tick $T0)"
ok "first sight: level is down" down "$(health)"
ok "no alert at once" 0 "$(nmail)"
ok "within the grace the base URL is pinged" 1 "$(n 'curl\|.*http://ping.test/abc\|$')"
pool $((T0+300)) none; tick $((T0+300)) >/dev/null
ok "5 min: still quiet" 0 "$(nmail)"
pool $((T0+660)) none; tick $((T0+660)) >/dev/null
ok "11 min: one e-mail" 1 "$(nmail)"
ok "the e-mail job is named with the DOWN subject" 1 "$(n '^sbatch --parsable -J sol-agent DOWN: no active session for 11 min -p general -q private -t 1 .*--mail-type=END,FAIL --mail-user=me@example.test')"
ok "one ntfy push, high priority, to the topic" 1 "$(n 'curl\|.*Priority: high.*https://ntfy.sh/test-topic-123')"
ok "the push carries the message as the body" 1 "$(n 'curl\|.*ntfy.sh/test-topic-123\|jobs: none')"
ok "NOTIFY_CMD got it too" 1 "$(n '^cmd\|sol-agent DOWN')"
ok "ping now goes to /fail" 1 "$(n 'http://ping.test/abc/fail')"
pool $((T0+960)) none; tick $((T0+960)) >/dev/null
ok "16 min: no second alert" 1 "$(nmail)"
ok "the state remembers when it began" "down $T0" "$(cut -d' ' -f1,2 "$SOL_AGENT_STATE/health")"
pool $((T0+660+21600+60)) none; tick $((T0+660+21600+60)) >/dev/null
ok "6 h later: STILL DOWN once" 2 "$(nmail)"
ok "the repeat is labelled" 1 "$(n '^sbatch --parsable -J STILL DOWN: no active session for ')"

echo "## recovery"
pool $((T0+30000)) both; tick $((T0+30000)) >/dev/null
ok "level ok" ok "$(health)"
ok "one recovery e-mail" 1 "$(n '^sbatch --parsable -J sol-agent recovered after ')"
ok "ping is back to the base URL" "http://ping.test/abc" "$(grep 'ping.test' "$T/calls" | tail -1 | awk '{print $NF}' | sed 's/|$//')"
before=$(nmail); pool $((T0+30300)) both; tick $((T0+30300)) >/dev/null
ok "ok stays quiet" "$before" "$(nmail)"

echo "## a failover gap shorter than the grace sends nothing"
before=$(nmail); logged=$(grep -c 'health ok -> down\|health down -> ok' "$SOL_AGENT_STATE/events.log")
pool $((T0+40000)) none; tick $((T0+40000)) >/dev/null
pool $((T0+40060)) both; tick $((T0+40060)) >/dev/null
ok "down for one tick then ok: no mail" "$before" "$(nmail)"
ok "...but both changes are logged" $((logged+2)) "$(grep -c 'health ok -> down\|health down -> ok' "$SOL_AGENT_STATE/events.log")"

echo "## degraded (no standby)"
fails_before=$(n 'abc/fail')
pool $((T0+50000)) active; tick $((T0+50000)) >/dev/null
ok "level degraded" degraded "$(health)"
before=$(nmail); pool $((T0+50000+1000)) active; tick $((T0+50000+1000)) >/dev/null
ok "17 min without a standby: quiet" "$before" "$(nmail)"
pool $((T0+50000+1900)) active; tick $((T0+50000+1900)) >/dev/null
ok "32 min: one no-standby e-mail" 1 "$(n '^sbatch --parsable -J sol-agent has no standby for ')"
pool $((T0+50000+2200)) both; tick $((T0+50000+2200)) >/dev/null
ok "standby back: one e-mail" 1 "$(n '^sbatch --parsable -J sol-agent has a standby again after ')"
ok "ping stays on the base URL while degraded" "$fails_before" "$(n 'abc/fail')"

echo "## paused and hold are expected, never alerted"
pool $((T0+60000)) none; tick $((T0+60000)) >/dev/null; pool $((T0+60000+700)) none; tick $((T0+60000+700)) >/dev/null
before=$(nmail)
touch "$SOL_AGENT_STATE/PAUSE"; pool $((T0+61000)) none; tick $((T0+61000)) >/dev/null
ok "PAUSE: level paused" paused "$(health)"
ok "leaving a standing DOWN for pause is silent" "$before" "$(nmail)"
pool $((T0+70000)) none; tick $((T0+70000)) >/dev/null
ok "paused for hours: still no alert" "$before" "$(nmail)"
rm -f "$SOL_AGENT_STATE/PAUSE"
echo "1900000000 1900001000 maint" > "$SOL_AGENT_STATE/hold"
ok "hold: level hold when nothing is running" hold "$(SOL_AGENT_ROOT=$SOL_AGENT_ROOT bash -c '. "$SOL_AGENT_ROOT/lib/health.sh"; pool_health')"
rm -f "$SOL_AGENT_STATE/hold"
ok "unknown when Slurm does not answer" unknown "$(SOL_AGENT_ROOT=$SOL_AGENT_ROOT bash -c '. "$SOL_AGENT_ROOT/lib/health.sh"; slurm_ok() { return 1; }; pool_health')"

echo "## channels"
: > "$T/calls"; export SOL_AGENT_FAKE_NOW=$T0
ok "ntfy full URL is used as given" "curl|-fsS -m 10 -H Title: s -H Priority: default -H Tags: robot --data-binary @- https://ntfy.example.test/t|b" \
  "$(NTFY_TOPIC=https://ntfy.example.test/t ALERT_EMAIL= NOTIFY_CMD= bash -c '. "$SOL_AGENT_ROOT/lib/common.sh"; notify s b' >/dev/null; cat "$T/calls")"
: > "$T/calls"
bash -c '. "$SOL_AGENT_ROOT/lib/common.sh"; notify_private "VS Code device code: ABCD-1234" "x"' >/dev/null
ok "private messages reach the command only: not e-mail (job names are public), not ntfy" "0 1 0" "$(nmail) $(n '^cmd\|') $(n 'ntfy.sh')"
: > "$T/calls"
bash -c '. "$SOL_AGENT_ROOT/lib/common.sh"; notify_info "tunnel up" "u"' >/dev/null
ok "info is silent at NOTIFY_LEVEL=alerts" 0 "$(n '.')"
NOTIFY_LEVEL=all bash -c '. "$SOL_AGENT_ROOT/lib/common.sh"; notify_info "tunnel up" "u"' >/dev/null
ok "...and sent at NOTIFY_LEVEL=all" "1 1 1" "$(nmail) $(n '^cmd\|') $(n 'ntfy.sh')"
: > "$T/calls"
bash -c '. "$SOL_AGENT_ROOT/lib/common.sh"; notify "line one
line two" "b"' >/dev/null
ok "a newline in the subject cannot split the job name" 1 "$(n '^sbatch --parsable -J line one line two -p general')"
: > "$T/calls"
bash -c '. "$SOL_AGENT_ROOT/lib/common.sh"; notify "s" "@/etc/passwd"' >/dev/null
ok "a body starting with @ is sent as text, not read as a file" 1 "$(n 'ntfy.sh/test-topic-123\|@/etc/passwd')"
STUB_CURL_RC=22 STUB_SBATCH_RC=1 SOL_AGENT_FAKE_NOW=$((T0+80000)) bash -c '. "$SOL_AGENT_ROOT/lib/common.sh"; notify s b; echo "caller continues rc=$?"' | grep -c "caller continues rc=0" | { read -r v; ok "a dead channel never fails the caller" 1 "$v"; }
ok "failures are logged" 1 "$(grep -c 'notify ntfy rc=22' "$SOL_AGENT_STATE/events.log" | awk '{print ($1>=1)?1:0}')"

echo "## alert test"
export STUB_CURL_RC=0 STUB_SBATCH_RC=0; : > "$T/calls"
ok "all channels take it: exit 0" 0 "$(SOL_AGENT_FORMAT=json "$SA" alert test >/dev/null 2>&1; echo $?)"
ok "JSON lists the three channels" "email ntfy cmd" "$(SOL_AGENT_FORMAT=json "$SA" alert test 2>/dev/null | jq -r '[.channels[].name]|join(" ")')"
ok "JSON says sent" true "$(SOL_AGENT_FORMAT=json "$SA" alert test 2>/dev/null | jq -r .sent)"
ok "text mode names each channel" 3 "$(SOL_AGENT_FORMAT=text "$SA" alert test 2>/dev/null | grep -c ': accepted')"
ok "the e-mail line says it is Slurm job mail and that arrival is not confirmed" 1 "$(SOL_AGENT_FORMAT=text "$SA" alert test 2>/dev/null | grep -c 'Slurm sends it when the 1-minute mail job ends')"
ok "a failing e-mail submission: exit 1, that channel not ok" "1 false" "$(STUB_SBATCH_RC=1 SOL_AGENT_FORMAT=json "$SA" alert test 2>/dev/null >"$T/o"; echo -n "$? "; jq -r '[.channels[]|select(.name=="email")|.ok][0]' "$T/o")"
ok "a failing channel: exit 1, sent false, that channel not ok" "1 false false" "$(STUB_CURL_RC=7 SOL_AGENT_FORMAT=json "$SA" alert test 2>/dev/null >"$T/o"; echo -n "$? "; jq -r '"\(.sent) \([.channels[]|select(.name=="ntfy")|.ok][0])"' "$T/o")"
ok "no channel: exit 1 with an explanation" "1 false" "$(ALERT_EMAIL= NTFY_TOPIC= NOTIFY_CMD= SOL_AGENT_FORMAT=json "$SA" alert test 2>/dev/null >"$T/o"; echo -n "$? "; jq -r .sent "$T/o")"
ok "bad usage: exit 2" 2 "$("$SA" alert frob >/dev/null 2>&1; echo $?)"
ok "help alert prints usage" yes "$("$SA" help alert | grep -q 'alert test' && echo yes || echo no)"

echo "## status fields"
pool $((T0+90000)) both; tick $((T0+90000)) >/dev/null
SOL_AGENT_FAKE_NOW=$((T0+90000)) "$SA" status --json 2>/dev/null > "$T/status.json"
ok "health.level" ok "$(jq -r .health.level "$T/status.json")"
ok "alerts.channels" "email ntfy cmd" "$(jq -r '.alerts.channels|join(" ")' "$T/status.json")"
ok "alerts.email shown, the ntfy topic is not" "me@example.test true" "$(jq -r '"\(.alerts.email) \(.alerts.ntfy)"' "$T/status.json")"
ok "the topic never appears in status" 0 "$(grep -c test-topic-123 "$T/status.json")"
ok "alerts.ping_url" true "$(jq -r .alerts.ping_url "$T/status.json")"
ok "model.configured is null when unset" null "$(jq -r .model.configured "$T/status.json")"
ok "model.configured follows the setting" sonnet "$(SOL_AGENT_MODEL=sonnet "$SA" status --json 2>/dev/null | jq -r .model.configured)"
ok "text status has health, alerts and model lines" 3 "$(SOL_AGENT_FORMAT=text "$SA" status 2>/dev/null | grep -c -E '^--- (health|alerts|model):')"
ok "no channel: the alerts line says NONE" yes "$(ALERT_EMAIL= NTFY_TOPIC= NOTIFY_CMD= PING_URL= SOL_AGENT_FORMAT=text "$SA" status 2>/dev/null | grep -q '^--- alerts: NONE' && echo yes || echo no)"

echo "## up warns when nobody would be told"
ok "up without a channel: warning on stderr" yes "$(ALERT_EMAIL= NTFY_TOPIC= NOTIFY_CMD= "$SA" up --force 2>&1 >/dev/null | grep -q 'no alert channel is set' && echo yes || echo no)"
ok "up with a channel: no warning" no "$("$SA" up --force 2>&1 >/dev/null | grep -q 'no alert channel is set' && echo yes || echo no)"
ok "up JSON lists the channels" "email ntfy cmd" "$("$SA" up --force 2>/dev/null | jq -r '.alert_channels|join(" ")')"

echo "## a cold start prints one JSON document (the submission line goes to stderr)"
pool $((T0+95000)) none
ok "up on an empty pool: stdout is one JSON document" yes "$("$SA" up --force 2>/dev/null | jq -e '.action=="up"' >/dev/null && echo yes || echo no)"
ok "...and the submission line went to stderr" yes "$("$SA" up --force 2>&1 >/dev/null | grep -q '^DRY: sbatch' && echo yes || echo no)"
ok "resume on an empty pool: the same" yes "$("$SA" resume 2>/dev/null | jq -e '.action' >/dev/null && echo yes || echo no)"

echo "## the model reaches the launch command"
launch() { SOL_AGENT_ROOT=$SOL_AGENT_ROOT bash -c '. "$SOL_AGENT_ROOT/lib/common.sh"; JOB=1; TM=true; eval "$(sed -n "/^session_cmd()/,/^}/p" "$SOL_AGENT_ROOT/bin/sol-agent.sbatch")"; session_cmd' 2>/dev/null; }
ok "SOL_AGENT_MODEL=sonnet adds --model sonnet" yes "$(SOL_AGENT_MODEL=sonnet launch | grep -q -- '--model sonnet' && echo yes || echo no)"
ok "empty model adds no --model" no "$(launch | grep -q -- '--model' && echo yes || echo no)"
ok "the shipped config.env sets sonnet" "sonnet" "$(bash -c 'AR=/x; . "$1"; echo "$SOL_AGENT_MODEL"' _ "$SOL_AGENT_ROOT/config.env")"

echo "alert: $pass passed, $fail failed"
rm -rf "$T"
[ "$fail" -eq 0 ]
