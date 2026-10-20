#!/usr/bin/env bash
# sol-agent common library: config, logging, heartbeat, lease, Slurm helpers.
# Sourced by sol-agent.sbatch, sol-agent-wd.sh, sol-agent, sol-agent-tunnel.sh and the tests.
# Everything shared between nodes lives under $SOL_AGENT_STATE (on NFS $HOME), because
# compute nodes are ephemeral and cannot ssh back to the login node.

AR=${SOL_AGENT_ROOT:-$HOME/.solx/agent}
# shellcheck disable=SC1091
[ -f "${SOL_AGENT_CONFIG:-$AR/config.env}" ] && . "${SOL_AGENT_CONFIG:-$AR/config.env}"
# shellcheck disable=SC1091
. "$AR/lib/out.sh"                     # output contract: JSON/text, -y/-n, exit codes

: "${NAME:=sol-agent}"             # Slurm job name + Remote Control environment name
: "${WORKDIR:=$AR/workspace}"      # must be a *trusted* Claude workspace (see DESIGN.md)
: "${PARTITION:=public}" "${QOS:=public}" "${CPUS:=1}" "${MEM:=2G}"
: "${MAX_TIME:=7-00:00:00}"         # longest chunk the partition allows
: "${STANDBY:=always}"              # always | handoff | off
: "${LEAD_MIN:=1440}"               # (STANDBY=handoff) queue the successor when this much time is left
: "${HANDOFF_S:=600}"               # --signal=B:USR1@ seconds before the limit
: "${TICK_S:=20}"                   # election/heartbeat period inside the job
: "${STALE_S:=180}"                 # heartbeat older than this => holder considered dead
: "${KILL_STALE_S:=600}"            # watchdog scancels a RUNNING sol-agent whose heartbeat is this old
: "${MAINT_MARGIN_MIN:=30}"         # end chunks this long before a MAINT reservation starts
: "${MIN_USEFUL_MIN:=30}"           # do not submit chunks shorter than this
: "${SPAWN:=same-dir}" "${CAPACITY:=4}"
: "${BRIDGE_CMD:=}"                 # test hook: replaces `claude remote-control ...`
: "${PING_URL:=}"                   # optional dead-man's-switch URL (healthchecks-style), off by default
: "${PERMISSION_MODE:=inherit}"      # inherit = copy the mode of the Claude session that ran `sol-agent up`; or an explicit mode
: "${NOTIFY_CMD:=}"                 # optional: command receiving "<subject>" "<body>" (any script that can reach you)
: "${ALERT_EMAIL:=}"               # optional: e-mail address for alerts (delivered by Slurm's own job mail; see _notify_send)
: "${NTFY_TOPIC:=}"                # optional: ntfy.sh topic (or a full URL of your own ntfy server) for phone push
: "${NOTIFY_LEVEL:=alerts}"         # alerts = trouble only | all = also failovers, starts and tunnel-up notices
: "${ALERT_DOWN_MIN:=10}"           # no active session for this long -> DOWN alert (shorter gaps are normal failovers)
: "${ALERT_DEGRADED_MIN:=30}"       # no warm standby for this long -> DEGRADED alert
: "${ALERT_REPEAT_H:=6}"            # a DOWN that persists is repeated at this interval
: "${SOL_AGENT_MODEL:=}"            # model for the always-on session (alias like sonnet, or a model id); empty = Claude Code's default

STATE=${SOL_AGENT_STATE:-$AR/state}
HB=$STATE/hb; LOGS=$AR/logs
mkdir -p "$STATE" "$HB" "$LOGS"
chmod 700 "$STATE" "$HB" "$LOGS" 2>/dev/null || true     # mailbox text, device codes and bridge logs: private to the user (home is group-readable)

# Claude Code env vars leak into sbatch jobs when submitted from inside a Claude session;
# a child `claude remote-control` must not inherit another session's identity.
scrub_claude_env() {
  local v
  for v in $(env | grep -oE '^(CLAUDECODE|CLAUDE_CODE_[A-Z_]+|CLAUDE_PID|CLAUDE_EFFORT)'); do unset "$v"; done
}

now() { echo "${SOL_AGENT_FAKE_NOW:-$(date +%s)}"; }       # SOL_AGENT_FAKE_NOW: test hook
log() { printf '%s %s[%s] %s\n' "$(date -Is)" "${COMP:-sol-agent}" "${SLURM_JOB_ID:-$$}" "$*" >> "$STATE/events.log"; }

# ---- notifications ------------------------------------------------------------------------------------------------
#   notify <subject> [body] [priority]   trouble: every configured channel (priority: default | high | urgent)
#   notify_info <subject> [body]         routine news (takeover, start, tunnel up): only with NOTIFY_LEVEL=all
#   notify_private <subject> [body]      secrets (a VS Code device code): NOTIFY_CMD only. Never ntfy, whose topics anyone who
#                                        knows the name can read, and never e-mail, which rides in a job name that every user
#                                        on Sol can list with squeue
# Delivery is best effort: a dead channel never fails the caller, every attempt is logged, and NOTIFY_RESULTS (a file),
# when set, collects "<channel><TAB><exit code>" for `sol-agent alert test`.
notify_channels() { # [private]
  [ -n "$ALERT_EMAIL" ] && [ "${1:-}" != private ] && echo email
  [ -n "$NTFY_TOPIC" ] && [ "${1:-}" != private ] && echo ntfy
  [ -n "$NOTIFY_CMD" ] && echo cmd
  return 0
}
_notify_send() { # channel subject body priority
  local url
  case $1 in
    # Sol's compute nodes cannot send mail themselves (their sendmail relays to 127.0.0.1, where nothing listens: `mail` reports
    # success and the message sits in a local queue that is lost with the node). The one mail path the cluster supports is
    # Slurm's job mail, sent by the controller. So an alert is a throwaway 1-second job that e-mails when it ends, and the
    # text is the job name: the e-mail is Slurm's "Job N Ended" with a "Job Name : <subject>" line (verified). No body. It leaves
    # when the job has run, so it waits in the queue like any job (general/private: the quickest queue measured at peak).
    email) timeout 30 sbatch --parsable -J "${2:0:150}" -p general -q private -t 1 -c 1 --mem=2G --mail-type=END,FAIL \
             --mail-user="$ALERT_EMAIL" -o /dev/null --wrap true;;
    ntfy)  case $NTFY_TOPIC in http://*|https://*) url=$NTFY_TOPIC;; *) url=https://ntfy.sh/$NTFY_TOPIC;; esac
           printf '%s' "$3" | curl -fsS -m 10 -H "Title: $2" -H "Priority: $4" -H "Tags: robot" --data-binary @- "$url";;
    cmd)   timeout 30 $NOTIFY_CMD "$2" "$3";;
  esac
}
notify_to() { # mode(all|private) subject [body] [priority]
  local mode=$1 subj=${2//$'\n'/ } body=${3:-} prio=${4:-default} ch rc
  body=$(printf '%s' "$body" | head -c 2000)
  for ch in $(notify_channels "$mode"); do
    _notify_send "$ch" "$subj" "$body" "$prio" >/dev/null 2>&1; rc=$?
    log "notify $ch rc=$rc: $subj"
    [ -n "${NOTIFY_RESULTS:-}" ] && printf '%s\t%s\n' "$ch" "$rc" >> "$NOTIFY_RESULTS"
  done
  return 0
}
notify()         { notify_to all "$@"; }
notify_private() { notify_to private "$@"; }
notify_info()    { [ "$NOTIFY_LEVEL" = all ] && notify_to all "$@"; return 0; }

# D-HH:MM:SS | HH:MM:SS | MM:SS | UNLIMITED  -> seconds
dur_to_s() {
  local s=$1 d=0 h=0 m=0 x=0
  [ "$s" = UNLIMITED ] || [ "$s" = INVALID ] && { echo 999999999; return; }
  case $s in *-*) d=${s%%-*}; s=${s#*-};; esac
  IFS=: read -r a b c <<<"$s"
  if [ -n "$c" ]; then h=$a m=$b x=$c; elif [ -n "$b" ]; then m=$a x=$b; else x=$a; fi
  echo $(( 10#$d*86400 + 10#$h*3600 + 10#$m*60 + 10#$x ))
}
s_to_dur() { local t=$1; printf '%d-%02d:%02d:%02d' $((t/86400)) $((t%86400/3600)) $((t%3600/60)) $((t%60)); }

# ---- Slurm views (all tolerate Slurm hiccups: empty output => "unknown", never "dead") ----
job_state() { squeue -h -j "$1" -o %T 2>/dev/null | head -1; }          # RUNNING|PENDING|... or empty if gone
slurm_ok()  { squeue --me -h -o %i >/dev/null 2>&1; }
sol_agent_jobs() { squeue --me -h -n "$NAME" -o '%i|%T|%L|%V|%r' 2>/dev/null; }  # id|state|left|submit|reason
timeleft_s() { dur_to_s "$(squeue -h -j "$1" -o %L 2>/dev/null | head -1)"; }

# ---- heartbeat: one small JSON per job, rewritten every tick; mtime is the clock ----
hb_write() { # role [extra-json-fields]
  local id=${SLURM_JOB_ID:-test$$} f
  f=$HB/$id.json
  printf '{"ts":%s,"job":"%s","node":"%s","role":"%s","left_s":%s%s}\n' \
    "$(now)" "$id" "$(hostname -s)" "$1" "${LEFT_S:--1}" "${2:+,$2}" > "$f.tmp" && mv -f "$f.tmp" "$f"
}
hb_age() { local f=$HB/$1.json m; [ -f "$f" ] || { echo 999999; return; }; m=$(stat -c %Y "$f"); echo $(( $(now) - m )); }
hb_gc() { find "$HB" -name '*.json' -mmin +2880 -delete 2>/dev/null; }

# alive = Slurm says RUNNING *and* the heartbeat is fresh. Slurm is the authority; NFS is not: a heartbeat file that
# was created a moment ago can stay invisible to another node for tens of seconds (observed: a standby stole the lease
# from a healthy active exactly 31 s after the active's heartbeat file was created). So a MISSING heartbeat only counts
# against a job once it has been running longer than HB_GRACE_S. If Slurm itself is unreachable, assume alive.
holder_alive() {
  local id=$1 st age run; [ -n "$id" ] || return 1
  st=$(job_state "$id")
  if [ -z "$st" ]; then slurm_ok && return 1 || return 0; fi
  [ "$st" = RUNNING ] || return 1
  age=$(hb_age "$id")
  if [ "$age" -ge 999999 ]; then
    run=$(dur_to_s "$(squeue -h -j "$id" -o %M 2>/dev/null | head -1)")
    [ "$run" -lt "${HB_GRACE_S:-120}" ]
    return
  fi
  [ "$age" -lt "$STALE_S" ]
}

# ---- lease: "<jobid> <epoch>" in $STATE/lease; mkdir lock because flock is unreliable on NFS ----
# NFS $HOME shows a NEW file after 7-8 s and a DELETED file after 13-26 s on another node, but a file replaced by
# rename within <1 s (measured). So the lease is only ever replaced: release writes a tombstone "- <epoch>", not rm.
lease_holder() { awk 'NR==1 && $1!="-"{print $1}' "$STATE/lease" 2>/dev/null; }
_lock() { local i=0 d=$STATE/lease.lock
  while ! mkdir "$d" 2>/dev/null; do
    [ -d "$d" ] && [ $(( $(now) - $(stat -c %Y "$d" 2>/dev/null || now) )) -gt 30 ] && rmdir "$d" 2>/dev/null
    i=$((i+1)); [ $i -gt 50 ] && return 1; sleep 0.2
  done; }
_unlock() { rmdir "$STATE/lease.lock" 2>/dev/null; }
# lease_acquire <jobid>: succeeds if I already hold it or the holder is dead
lease_acquire() {
  local me=$1 h; _lock || return 1
  h=$(lease_holder)
  if [ "$h" = "$me" ] || ! holder_alive "$h"; then
    printf '%s %s\n' "$me" "$(now)" > "$STATE/lease.tmp" && mv -f "$STATE/lease.tmp" "$STATE/lease"
    [ "$h" != "$me" ] && log "lease ${h:-none} -> $me"
    _unlock; return 0
  fi
  _unlock; return 1
}
lease_release() { local me=$1; _lock || return 1
  [ "$(lease_holder)" = "$me" ] && { printf -- '- %s\n' "$(now)" > "$STATE/lease.tmp" && mv -f "$STATE/lease.tmp" "$STATE/lease"; log "lease released by $me"; }
  _unlock; }

# ---- permission mode ------------------------------------------------------------------------------------------
# Claude cross-session messages are only delivered automatically between sessions of the same permission-mode class;
# otherwise the receiver holds them behind a human approval dialog (tested: bypass->bypass delivered in 2s,
# bypass->auto blocked). So sessions spawned by the sol-agent must carry the mode of the session that set them up.
# Modes: acceptEdits auto bypassPermissions default dontAsk plan
detect_permission_mode() { # [pid]  -> mode of that claude process (initial mode: shift+tab changes are not visible)
  local pid=${1:-${CLAUDE_PID:-}} args=() m="" i
  [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ] || return 1
  mapfile -d '' -t args < "/proc/$pid/cmdline"          # one array element per argv entry: a prompt containing a newline cannot fake a flag
  for ((i=0; i<${#args[@]}; i++)); do
    case ${args[i]} in
      --dangerously-skip-permissions) echo bypassPermissions; return 0;;
      --permission-mode) m=${args[i+1]:-};;
      --permission-mode=*) m=${args[i]#*=};;
    esac
  done
  [ -z "$m" ] && m=$(jq -r '.permissions.defaultMode // empty' "$HOME/.claude/settings.json" 2>/dev/null)
  echo "${m:-default}"
}
capture_permission_mode() { local m; m=$(detect_permission_mode "$@") && [ -n "$m" ] && { echo "$m" > "$STATE/permission_mode"; echo "$m"; }; }
effective_permission_mode() {
  case $PERMISSION_MODE in
    inherit) cat "$STATE/permission_mode" 2>/dev/null;;
    none|"") ;;
    *) echo "$PERMISSION_MODE";;
  esac
}
