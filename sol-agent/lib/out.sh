#!/usr/bin/env bash
# sol-agent output contract, shared by `sol-agent`, `sol-agent-tunnel.sh` and `sol-agent-msg`. The rules are the ones `solx`
# documents for itself, so `solx job list | jq` and `solx agent status | jq` behave the same way:
#   * Commands that return data print JSON on stdout when stdout is not a terminal. --json forces it, --text forces text.
#     SOL_AGENT_FORMAT=json|text is the same switch for callers that cannot pass a flag (`solx --json agent ...` sets it).
#   * Human-readable text goes to stdout in text mode and to stderr in JSON mode. Errors and warnings always go to stderr.
#   * Streams (`logs`, `msg watch`, `msg hook`) stay plain text unless --json is given explicitly; then it is one JSON
#     object per line.
#   * Destructive commands take -n/--dry-run (show the plan, change nothing) and -y/--yes (do it). With neither they ask on a
#     terminal and refuse with exit 2 when nobody can answer, instead of hanging on a prompt.
#   * Exit codes: 0 done (including "nothing to do" for idempotent cleanup) · 1 failed · 2 usage error or unconfirmed
#     destructive call · 3 waiting for you (`tunnel start` while a GitHub device code is unapproved).

FORMAT_EXPLICIT=0
case ${SOL_AGENT_FORMAT:-} in
  json|text) FORMAT=$SOL_AGENT_FORMAT; FORMAT_EXPLICIT=1;;
  *) if [ -t 1 ]; then FORMAT=text; else FORMAT=json; fi;;
esac
DRY=0; YES=0
OUT_ARGS=()
REST=()

# out_flags "$@": pull --json / --text out of the arguments (anywhere), leave the rest in OUT_ARGS. Use as:
#   out_flags "$@"; set -- "${OUT_ARGS[@]+"${OUT_ARGS[@]}"}"
out_flags() {
  OUT_ARGS=(); local a
  for a in "$@"; do
    case $a in --json) FORMAT=json; FORMAT_EXPLICIT=1;; --text) FORMAT=text; FORMAT_EXPLICIT=1;; *) OUT_ARGS+=("$a");; esac
  done
}
# parse_yn "$@": set DRY / YES from -n/--dry-run and -y/--yes, leave everything else in REST
parse_yn() { REST=(); local a; for a in "$@"; do case $a in -n|--dry-run) DRY=1;; -y|--yes) YES=1;; *) REST+=("$a");; esac; done; yn_check; }
yn_check() { if [ "$DRY" = 1 ] && [ "$YES" = 1 ]; then die2 "-y and -n cannot be combined"; fi; }
jq() { command jq -M "$@"; }      # never colour: a forced --json on a terminal must stay a clean document, like solx's own
# one id per line on stdin -> ["id",...] (JSON)
ids_json() { jq -R -s -c 'split("\n")|map(select(length>0))'; }
is_json()     { [ "$FORMAT" = json ]; }
stream_json() { [ "$FORMAT" = json ] && [ "$FORMAT_EXPLICIT" = 1 ]; }
say()  { if is_json; then printf '%s\n' "$*" >&2; else printf '%s\n' "$*"; fi; }   # human text
warn() { printf '%s\n' "$*" >&2; }
die2() { warn "error: $*"; exit 2; }                                                # usage error

# gate "<plan>": the destructive-command rule. Returns 0 to go ahead, 1 for a dry run (plan shown, nothing to change);
# exits 2 when it cannot ask. Reads the answer from the terminal only.
gate() {
  local plan=$1 ans
  yn_check
  if [ "$DRY" = 1 ]; then return 1; fi
  if [ "$YES" = 1 ]; then return 0; fi
  if [ -t 0 ] && [ -t 2 ]; then
    printf '%s\nProceed? [y/N] ' "$plan" >&2; read -r ans
    case $ans in y|Y|yes|YES) return 0;; *) warn "aborted"; exit 1;; esac
  fi
  warn "error: $plan"
  warn "refusing without -y (do it) or -n (preview): no terminal to ask on"
  exit 2
}
