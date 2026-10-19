#!/usr/bin/env bash
# VS Code tunnel on demand, built on Sol's own VS Code (the same CLI, data dir and tunnel name as Sol's `code-tunnel`
# alias and `vscode` command), with the GitHub device code captured and handed back (stdout, state file, NOTIFY_CMD, mailbox).
#   sol-agent-tunnel.sh start [--job J | --here] [--wait S] [--restart]
#        default : own small job (like Sol's `vscode`: lightwork/public, 1 CPU, 1 day) -> survives sol-agent restarts
#        --job J : inside job J's allocation (srun --overlap), e.g. a GPU worker, so the editor sees that node
#        --here  : on this node (only inside an allocation; never on a login node)
#   sol-agent-tunnel.sh stop [-y|-n] | status
#   sol-agent-tunnel.sh attach         watch/interact with the tunnel's tmux session on its node (detach: Ctrl-b d)
#   Output: JSON on stdout when stdout is not a terminal (--json / --text force it), see lib/out.sh. `start` exits 0 when the
#   tunnel is up or already running, 3 while a device code waits for your approval, 1 if nothing happened, 2 on bad usage.
#   sol-agent-tunnel.sh run            (internal: executes on the node that hosts the tunnel; the CLI lives in a tmux session there)
# MEASURED: the CLI's login token is encrypted with a per-machine key. A token written on one node reads "not logged in" on
# another, so every tunnel start on a new (ephemeral) node needs a fresh GitHub device-code approval. Hence the relay.
# The approval is only needed once per start: the waiting `run` carries on and brings the tunnel up by itself.
set -u
. "${SOL_AGENT_ROOT:-$HOME/.solx/agent}/lib/common.sh"
COMP=tunnel
out_flags "$@"; set -- "${OUT_ARGS[@]+"${OUT_ARGS[@]}"}"
CODE=${CODE_BIN:-/packages/apps/vscode/latest/bin/code}
DATA=${CODE_DATA:-$HOME/.vscode}
TNAME=${TUNNEL_NAME:-sol_$(whoami)}; TNAME=${TNAME:0:20}        # VS Code limits tunnel names to 20 chars
: "${TUNNEL_PARTITION:=lightwork}" "${TUNNEL_QOS:=public}" "${TUNNEL_TIME:=1-0}" "${TUNNEL_MEM:=2G}" "${TUNNEL_CPUS:=1}"
JOBNAME=${NAME}-vscode
LOCK=$DATA/tunnel-stable.lock
TS=$STATE/tunnel; mkdir -p "$TS"
TM="tmux -L ${NAME}-tunnel"                              # on the submitting host: supervises --job / --here runs
tmr() { tmux -L "${NAME}-tunnel-run-${1}" "${@:2}"; }     # on the tunnel host, one socket per job id (nodes are shared between jobs)
strip() { sed -E 's/\x1b\[[0-9;?]*[a-zA-Z]//g'; }
logged_in() { ! "$CODE" tunnel user show --cli-data-dir "$DATA" </dev/null 2>&1 | grep -qi 'not logged in'; }
code_in() { [ -f "$1" ] || return 0; strip < "$1" 2>/dev/null | grep -oE 'use code [A-Z0-9]{4}-[A-Z0-9]{4}' | head -1 | awk '{print $3}'; }
url_in()  { [ -f "$1" ] || return 0; strip < "$1" 2>/dev/null | grep -oE 'https://vscode\.dev/tunnel/[^ ]+' | head -1; }
# jobs that hold (or are about to hold) the single tunnel slot: ours, plus Sol's own `vscode` wrapper (srun names the job "code")
active_jobs() { squeue --me -h -t "$1" -o '%i %j %N' | awk -v n="$JOBNAME" '$2==n || $2=="code" || $2=="vscode"'; }

relay_code() {
  local c=$1
  printf '{"code":"%s","url":"https://github.com/login/device","ts":%s}\n' "$c" "$(now)" > "$TS/device_code.tmp" && mv -f "$TS/device_code.tmp" "$TS/device_code"
  log "device code issued host=$(hostname -s) (the code itself goes to the state file, the user mailbox and stdout, not this log)"; notify_private "VS Code device code: $c" "https://github.com/login/device"
  "$AR/bin/sol-agent-msg" send user -f tunnel -s "VS Code device code $c" "Open https://github.com/login/device and enter $c to authorize the Sol tunnel $TNAME. The tunnel comes up by itself afterwards." >/dev/null 2>&1
}

# VS Code allows ONE tunnel per data dir (lock file). An unclean exit (time limit, scancel, kill) leaves the lock on NFS and the
# next tunnel on another node refuses to start. Sol's docs: remove only the stale lock; login, registration and server cache stay.
clear_stale_lock() {
  [ -e "$LOCK" ] || return 0
  local busy; busy=$(active_jobs R | head -1)
  [ -z "$busy" ] && $TM has-session -t tunnel 2>/dev/null && busy="tmux-session"
  # a tunnel started with --job lives in a step of a worker job and its tmux supervisor is on another node after a failover:
  # trust the job id the running tunnel recorded, and keep the lock while that job still runs (use `stop` to clear it)
  if [ -z "$busy" ]; then
    local cur; cur=$(awk 'NR==1{print $1}' "$TS/current" 2>/dev/null)
    case $cur in ""|none|*[!0-9]*) ;; *) [ "$(job_state "$cur")" = RUNNING ] && busy="job $cur";; esac
  fi
  if [ -n "$busy" ]; then R_NOTE="a tunnel is already running ($busy); lock kept. Use 'stop' first, or connect to $TNAME."; say "$R_NOTE"; return 1; fi
  rm -f "$LOCK"; log "removed stale tunnel lock"; say "removed stale tunnel lock (no tunnel job of yours is running; login and server cache are kept)"
}

cmd_run() {
  local cpid="" wpid="" c i attempt rc t0 rejected=0
  local jid=${SLURM_JOB_ID:-$$}
  [ -n "${SLURM_JOB_ID:-}" ] || { echo "error: 'tunnel run' only works inside a Slurm allocation (never on a login node)" >&2; exit 2; }
  tmux_stop() { tmr "$jid" send-keys -t code-tunnel C-c 2>/dev/null     # Ctrl-C: the CLI shuts down cleanly
    for i in $(seq 1 20); do tmr "$jid" has-session -t code-tunnel 2>/dev/null || return 0; sleep 1; done; tmr "$jid" kill-server 2>/dev/null; }
  trap '[ -n "$wpid" ] && kill "$wpid" 2>/dev/null; [ -n "$cpid" ] && kill "$cpid" 2>/dev/null && wait "$cpid" 2>/dev/null; tmux_stop; rm -f "$TS/run-$jid.sh" "$TS/run-$jid.rc"; log "tunnel run ended (signal)"; : > "$TS/current"; exit 143' TERM INT HUP
  # No other tunnel job of yours may be running here (the caller checked). Used to decide whether the lock file is ours to delete.
  alone() { [ -z "$(active_jobs R | awk -v me="${SLURM_JOB_ID:-0}" '$1!=me')" ]; }
  for attempt in 1 2; do
    if ! logged_in; then
      : > "$TS/login.log"
      "$CODE" tunnel user login --provider github --cli-data-dir "$DATA" </dev/null > "$TS/login.log" 2>&1 & cpid=$!
      c=""; for i in $(seq 1 60); do c=$(code_in "$TS/login.log"); [ -n "$c" ] && break; kill -0 "$cpid" 2>/dev/null || break; sleep 1; done
      [ -n "$c" ] || { log "no device code appeared (see $TS/login.log)"; exit 1; }
      relay_code "$c"
      wait "$cpid" || { log "device login failed or expired"; : > "$TS/device_code"; exit 1; }
      cpid=""; : > "$TS/device_code"; log "device login approved on $(hostname -s)"
    fi
    # Delete a stale lock from THIS node: a delete done on the submitting node can take 13-26 s to show up here (NFS), long
    # enough to make the new tunnel refuse to start.
    alone && rm -f "$LOCK"
    # a token that is stored but no longer valid can make `code tunnel` itself ask for a code: relay that one too
    ( for i in $(seq 1 180); do
        c=$(code_in "$TS/tunnel.log"); [ -n "$c" ] && { relay_code "$c"; break; }
        if [ -n "$(url_in "$TS/tunnel.log")" ]; then       # up: this machine holds a working login; remember it (next start prefers it, no code needed)
          hostname -s > "$TS/auth_node.tmp" && mv -f "$TS/auth_node.tmp" "$TS/auth_node"; break; fi
        sleep 1
      done ) & wpid=$!
    echo "${SLURM_JOB_ID:-none} $(hostname -s) $(date -Is)" > "$TS/current.tmp" && mv -f "$TS/current.tmp" "$TS/current"
    t0=$SECONDS
    # The long-lived CLI runs in a tmux session on this node (attachable with `sol-agent tunnel attach`); output is tee'd to tunnel.log.
    local sh=$TS/run-$jid.sh rcf=$TS/run-$jid.rc      # inside the private $STATE dir, never a predictable /tmp name
    rm -f "$rcf"; tmr "$jid" kill-server 2>/dev/null
    { echo '#!/bin/bash'; printf '%q ' "$CODE" tunnel --accept-server-license-terms --disable-telemetry --cli-data-dir "$DATA" --name "$TNAME"
      printf '</dev/null 2>&1 | tee -a %q\necho ${PIPESTATUS[0]} > %q\n' "$TS/tunnel.log" "$rcf"; } > "$sh"; chmod +x "$sh"
    tmr "$jid" new-session -d -s code-tunnel -x 200 -y 50 "$sh"
    # Measured: when the service rejects the stored login (HTTP 401) the CLI does NOT exit; it stays up with only the error in its
    # log. So watch for that while it runs (first attempt, before the tunnel is up) and stop it ourselves.
    rejected=0
    # "login rejected" = the tunnel never came up and the log says 401. On a fresh data dir the CLI also EXITS (rc 0) a few seconds
    # after the 401, so the exit code says nothing; the log does.
    login_rejected() { [ -z "$(url_in "$TS/tunnel.log")" ] && strip < "$TS/tunnel.log" 2>/dev/null | grep -qiE 'HTTP status 401|unauthori[sz]ed'; }
    while tmr "$jid" has-session -t code-tunnel 2>/dev/null; do
      if [ "$attempt" = 1 ] && [ $((SECONDS-t0)) -lt 300 ] && login_rejected; then rejected=1; tmux_stop; break; fi
      sleep 2
    done
    rc=$(cat "$rcf" 2>/dev/null); rc=${rc:-1}; rm -f "$sh" "$rcf"
    alone && rm -f "$LOCK"                       # a CLI that exited (any way but a clean stop) leaves its lock behind
    # Drop the rejected login (file keychain, no network call) and go through the device-code flow once.
    if [ "$attempt" = 1 ] && { [ "$rejected" = 1 ] || { [ $((SECONDS-t0)) -lt 300 ] && login_rejected; }; }; then
      log "stored login rejected by the tunnel service (401); dropping it and asking for a device code"; rm -f "$DATA/token.json"; : > "$TS/auth_node"; continue
    fi
    break
  done
  : > "$TS/current"; log "tunnel exited rc=$rc"; exit $rc
}

stop_do() {
  local j i; j=$(squeue --me -h -t R,PD -n "$JOBNAME" -o %i | tr '\n' ' ')
  [ -n "$j" ] && scancel $j        # SIGTERM first: `run` stops the CLI, which releases its lock
  if $TM has-session -t tunnel 2>/dev/null; then       # --job / --here: Ctrl-C first so the CLI exits cleanly and frees its lock
    $TM send-keys -t tunnel C-c 2>/dev/null
    for i in $(seq 1 15); do $TM has-session -t tunnel 2>/dev/null || break; sleep 1; done
    $TM kill-server 2>/dev/null
    for i in $(seq 1 10); do $TM has-session -t tunnel 2>/dev/null || break; sleep 1; done
  fi
  : > "$TS/device_code"; : > "$TS/current"; log "tunnel stopped (jobs: ${j:-none})"
  STOPPED_JOBS=$j; say "stopped${j:+ (cancelled job $j)}"
}
STOPPED_JOBS=""
cmd_stop() {
  parse_yn "$@"; [ "${#REST[@]}" -eq 0 ] || die2 "stop: unexpected argument ${REST[0]}"
  local j; j=$(squeue --me -h -t R,PD -n "$JOBNAME" -o %i | tr '\n' ' ')
  local sess=0; $TM has-session -t tunnel 2>/dev/null && sess=1
  if [ -z "$j" ] && [ "$sess" = 0 ]; then
    say "no tunnel of the sol-agent is running"
    is_json && jq -n -c '{action:"stop",stopped:false,cancelled:[]}'; return 0; fi
  gate "stop the tunnel: cancel job(s) ${j:-none}$( [ "$sess" = 1 ] && echo ' and end its tmux session' )"
  if [ $? -eq 1 ]; then say "(dry run: nothing changed)"; is_json && printf '%s' "$j" | tr ' ' '\n' | ids_json | jq -c --argjson s "$sess" '{action:"stop",dry_run:true,would_cancel:.,tmux_session:($s==1)}'; return 0; fi
  stop_do
  is_json && printf '%s' "$STOPPED_JOBS" | tr ' ' '\n' | ids_json | jq -c '{action:"stop",stopped:true,cancelled:.}'
  return 0
}

start_do() {
  local job="" here=0 wait_s=90 restart=0 j i
  while [ $# -gt 0 ]; do case $1 in --job) job=$2; shift 2;; --here) here=1; shift;; --wait) wait_s=$2; shift 2;; --restart) restart=1; shift;; *) shift;; esac; done
  case $job in ""|*[!0-9]*) [ -z "$job" ] || { R_STATE=error; R_NOTE="--job needs a numeric job id"; warn "error: $R_NOTE"; return 2; };; esac
  [ -x "$CODE" ] || { R_STATE=error; R_NOTE="VS Code CLI not found at $CODE (Sol provides /packages/apps/vscode/latest/bin/code)"; warn "$R_NOTE"; return 2; }
  if [ "$here" = 1 ] && [ -z "${SLURM_JOB_ID:-}" ]; then
    R_STATE=error; R_NOTE="refusing to run a tunnel on a login node (Sol policy); use 'start' (own job) or '--job J'"; warn "$R_NOTE"; return 2; fi
  if [ "$restart" = 1 ]; then stop_do >/dev/null 2>&1; for j in $(seq 1 40); do [ -z "$(active_jobs R,PD,CG)" ] && break; sleep 1; done; fi
  j=$(active_jobs R,PD | head -1)
  if [ -n "$j" ] && [ -z "$job" ] && [ "$here" = 0 ]; then
    R_STATE=already_running; R_WHERE="job ${j%% *}"
    case $j in *" $JOBNAME "*) R_NOTE="a tunnel job already exists: $j  (use --restart to replace it)";;
      *) R_NOTE="a tunnel job already exists: $j  (started outside the sol-agent, e.g. Sol's \`vscode\`; scancel it first if the sol-agent should manage the tunnel)";; esac
    say "$R_NOTE"; is_json || cmd_status; return 0; fi
  if $TM has-session -t tunnel 2>/dev/null; then       # decided by our own supervisor, not by the lock (NFS shows new files after 7-30 s)
    R_STATE=already_running; R_NOTE="a tunnel is already running ($(grep . "$TS/current" 2>/dev/null || echo starting)); use --restart to replace it"
    say "$R_NOTE"; is_json || cmd_status; return 0; fi
  clear_stale_lock || { R_STATE=blocked; return 1; }
  : > "$TS/tunnel.log"; : > "$TS/device_code"; : > "$TS/current"
  local run where; run="$(printf '%q' "$AR/bin/sol-agent-tunnel.sh") run"
  if [ -n "$job" ]; then
    $TM kill-session -t tunnel 2>/dev/null
    $TM new-session -d -s tunnel "srun --jobid=$job --overlap --cpu-bind=none -N1 -n1 --mem=0 $run"; where="inside job $job"
  elif [ "$here" = 1 ]; then
    $TM kill-session -t tunnel 2>/dev/null
    $TM new-session -d -s tunnel "$run"; where="on $(hostname -s) (job $SLURM_JOB_ID)"
  else
    # The CLI's login is bound to the machine it was created on (measured), and a restart there needs no code. So ask for the
    # machine that last had a working login; if it is not free within TUNNEL_NODE_WAIT seconds, take any node (and a new code).
    local pref="" st=""
    if [ "${TUNNEL_PREFER_AUTH_NODE:-1}" = 1 ] && [ -s "$TS/auth_node" ] && ! printf '%s' "${TUNNEL_SBATCH_EXTRA:-}" | grep -qE -- '(^| )(-w|-x|--nodelist|--exclude)'; then
      pref=$(cat "$TS/auth_node"); fi
    submit_tunnel() { sbatch --parsable -J "$JOBNAME" -p "$TUNNEL_PARTITION" -q "$TUNNEL_QOS" -N1 -n1 -c "$TUNNEL_CPUS" --mem="$TUNNEL_MEM" -t "$TUNNEL_TIME" \
        -D "${TUNNEL_CHDIR:-$HOME}" ${TUNNEL_SBATCH_EXTRA:-} ${1:+-w "$1"} -o "$LOGS/vscode-%j.out" --wrap "exec $run"; }
    j=$(submit_tunnel "$pref") || { R_STATE=error; R_NOTE="sbatch failed"; warn "sbatch failed"; return 1; }
    if [ -n "$pref" ]; then
      for i in $(seq 1 "${TUNNEL_NODE_WAIT:-15}"); do st=$(squeue -h -j "$j" -o %T 2>/dev/null); [ "$st" = RUNNING ] && break; sleep 1; done
      if [ "$st" != RUNNING ]; then scancel "$j"; log "preferred node $pref not free in ${TUNNEL_NODE_WAIT:-15}s; resubmitting anywhere"
        j=$(submit_tunnel "") || { R_STATE=error; R_NOTE="sbatch failed"; warn "sbatch failed"; return 1; }; pref=""; fi
    fi
    where="in job $j ($TUNNEL_PARTITION/$TUNNEL_QOS, $TUNNEL_TIME${pref:+, on $pref where the stored login still works})"
  fi
  log "tunnel start requested $where"; R_WHERE=$where
  local t0=$SECONDS end=$((SECONDS+wait_s)) url code shown=""
  while [ $SECONDS -lt $end ]; do
    url=$(url_in "$TS/tunnel.log"); [ -n "$url" ] && break
    code=$(jq -r '.code // empty' "$TS/device_code" 2>/dev/null)
    if [ -n "$code" ] && [ "$shown" != "$code" ]; then
      say "DEVICE CODE: $code   open https://github.com/login/device and enter it ($where; code ready after $((SECONDS-t0)) s). The tunnel then starts by itself."; log "device code shown to caller after $((SECONDS-t0)) s"; shown=$code; fi
    sleep 1
  done
  if [ -n "$url" ]; then R_STATE=up; R_URL=$url; say "tunnel up ($where): $url   (VS Code: Remote-Tunnels -> $TNAME)"; log "tunnel up $url"; notify_info "VS Code tunnel up" "$url"; return 0; fi
  if [ -n "$shown" ]; then R_STATE=awaiting_approval; R_CODE=$shown; say "waiting for approval of $shown; check later with: sol-agent tunnel status"; return 3; fi
  R_STATE=pending; R_NOTE="tunnel requested $where but nothing to report yet (queue or NFS delay); sol-agent tunnel status"; say "$R_NOTE"; return 1
}
R_STATE=none; R_WHERE=""; R_URL=""; R_CODE=""; R_NOTE=""
cmd_start() {
  start_do "$@"; local rc=$?
  is_json && jq -n -c --arg state "$R_STATE" --arg where "$R_WHERE" --arg url "$R_URL" --arg code "$R_CODE" --arg note "$R_NOTE" --arg name "$TNAME" --argjson rc "$rc" '
    def nul: if . == "" then null else . end;
    {action:"start",state:$state,exit_code:$rc,tunnel_name:$name,where:($where|nul),url:($url|nul),
     device_code:(if $code == "" then null else {code:$code,verification_url:"https://github.com/login/device",approve_within_s:900} end),note:($note|nul)}'
  return $rc
}

cmd_attach() {
  [ -t 0 ] && [ -t 1 ] || die2 "attach opens an interactive tmux and needs a terminal; use 'tunnel status' instead"
  local j; j=$(active_jobs R | awk -v n="$JOBNAME" '$2==n{print $1; exit}')
  [ -n "$j" ] || j=$(awk '{print $1}' "$TS/current" 2>/dev/null)
  [ -n "$j" ] && [ "$j" != none ] || { echo "no running tunnel to attach to" >&2; return 1; }
  exec srun --jobid="$j" --overlap --cpu-bind=none -N1 -n1 --mem=0 --pty tmux -L "${NAME}-tunnel-run-$j" attach -t code-tunnel
}

status_json() {
  local code_json='null' cur; cur=$(grep . "$TS/current" 2>/dev/null || true)
  [ -n "$(jq -r '.code // empty' "$TS/device_code" 2>/dev/null)" ] && code_json=$(jq -c --argjson now "$(now)" '{code:.code,verification_url:.url,age_s:($now - .ts)}' "$TS/device_code")
  jq -n -c --arg cli "$CODE" --arg ver "$("$CODE" --version 2>/dev/null | head -1)" --arg data "$DATA" --arg name "$TNAME" \
    --argjson jobs "$(active_jobs R,PD | jq -R -s -c 'split("\n")|map(select(length>0)|split(" ")|{id:.[0],name:.[1],node:(.[2] // null)})')" \
    --arg cur "$cur" --argjson lock "$([ -e "$LOCK" ] && echo true || echo false)" --argjson li "$(logged_in && echo true || echo false)" \
    --argjson code "$code_json" --arg url "$(url_in "$TS/tunnel.log")" '
    def nul: if . == "" then null else . end;
    {cli:$cli,version:($ver|nul),data_dir:$data,tunnel_name:$name,jobs:$jobs,
     current:(if $cur == "" or ($cur|startswith("none")) then null else ($cur|split(" ")|{job:.[0],host:.[1],since:.[2]}) end),
     lock_present:$lock,logged_in_on_this_node:$li,pending_device_code:$code,url:($url|nul)}'
}
cmd_status() {
  if is_json; then status_json; return 0; fi
  printf 'cli=%s (%s) data=%s tunnel_name=%s\n' "$CODE" "$("$CODE" --version 2>/dev/null | head -1)" "$DATA" "$TNAME"
  local a; a=$(active_jobs R,PD); printf 'tunnel_jobs: %s\n' "${a:-none}"
  printf 'current=%s lock=%s logged_in_on_this_node=%s\n' "$(cat "$TS/current" 2>/dev/null | grep . || echo none)" "$([ -e "$LOCK" ] && echo present || echo none)" "$(logged_in && echo yes || echo no)"
  local code; code=$(jq -r '.code // empty' "$TS/device_code" 2>/dev/null)
  [ -n "$code" ] && echo "pending device code: $code (issued $(( $(now) - $(jq -r .ts "$TS/device_code") ))s ago)"
  local u; u=$(url_in "$TS/tunnel.log"); [ -n "$u" ] && echo "url: $u"
}

case ${1:-} in
  start) shift; cmd_start "$@";; stop) shift; cmd_stop "$@";; status) cmd_status;; attach) cmd_attach;; run) cmd_run;;
  help|-h|--help) sed -n '2,/^# MEASURED/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//';;
  *) sed -n '2,/^# MEASURED/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//' >&2; exit 2;;
esac
