#!/usr/bin/env bash
# Unit tests for lib/maint.sh against synthetic `scontrol show reservation -o` text.
set -u
T=$(mktemp -d); export SOL_AGENT_STATE=$T/state SOL_AGENT_RESV_FILE=$T/resv.txt; mkdir -p "$SOL_AGENT_STATE"
export SOL_AGENT_ROOT=${SOL_AGENT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
. "$SOL_AGENT_ROOT/lib/common.sh"; . "$SOL_AGENT_ROOT/lib/maint.sh"
pass=0; fail=0
ok() { # name expected actual
  if [ "$2" = "$3" ]; then pass=$((pass+1)); printf 'PASS %s\n' "$1"; else fail=$((fail+1)); printf 'FAIL %s: expected [%s] got [%s]\n' "$1" "$2" "$3"; fi; }
iso() { date -d "@$1" +%Y-%m-%dT%H:%M:%S; }
resv() { # name start_epoch end_epoch flags
  printf 'ReservationName=%s StartTime=%s EndTime=%s Duration=x Nodes=sc[001-100] NodeCnt=100 CoreCnt=9999 Features=(null) PartitionName=(null) Flags=%s TRES=cpu=9999 Users=(null) State=INACTIVE\n' "$1" "$(iso "$2")" "$(iso "$3")" "$4"; }
N=$(now); D=86400; MAXS=$(dur_to_s "$MAX_TIME"); MARGIN=$((MAINT_MARGIN_MIN*60))

: > "$SOL_AGENT_RESV_FILE";                                          ok "no reservations -> NONE"           NONE "$(maint_plan $N)"
resv maint $((N+3*D)) $((N+6*D)) MAINT,IGNORE_JOBS,ALL_NODES > "$SOL_AGENT_RESV_FILE"
ok "maint in 3d -> FIT 3d-margin"      "FIT $((3*D-MARGIN)) $((N+3*D))" "$(maint_plan $N)"
ok "chunk_seconds mirrors FIT"         "$((3*D-MARGIN))"                 "$(chunk_seconds $N)"
resv maint $((N+10*D)) $((N+12*D)) MAINT > "$SOL_AGENT_RESV_FILE"; ok "maint in 10d (> MAX_TIME) -> NONE" NONE "$(maint_plan $N)"
resv maint $((N+20*60)) $((N+2*D)) MAINT > "$SOL_AGENT_RESV_FILE"
ok "maint in 20 min -> HOLD until end" "HOLD $((N+2*D))" "$(maint_plan $N)"
resv maint $((N-3600)) $((N+D)) MAINT > "$SOL_AGENT_RESV_FILE"
ok "maint in progress -> HOLD until end" "HOLD $((N+D))" "$(maint_plan $N)"
resv maint $((N+D)) $((N+2*D)) MAINT > "$SOL_AGENT_RESV_FILE"
ok "margin: 31 min of room after margin<MIN_USEFUL -> HOLD" "HOLD $((N+2*D))" "$(maint_plan $((N+D-MARGIN-20*60)))"
resv GPU_DAY $((N+D)) $((N+2*D)) "FLEX" > "$SOL_AGENT_RESV_FILE"; ok "non-maint reservation ignored" NONE "$(maint_plan $N)"
resv weirdname $((N+2*D)) $((N+3*D)) MAINT,SPEC_NODES > "$SOL_AGENT_RESV_FILE"; ok "MAINT flag with other name counts" "FIT $((2*D-MARGIN)) $((N+2*D))" "$(maint_plan $N)"
{ resv hackathon $((N+D/2)) $((N+D)) FLEX; resv maint $((N+5*D)) $((N+6*D)) MAINT; resv maint $((N+2*D)) $((N+3*D)) MAINT; } > "$SOL_AGENT_RESV_FILE"
ok "several windows: earliest MAINT wins" "FIT $((2*D-MARGIN)) $((N+2*D))" "$(maint_plan $N)"
: > "$SOL_AGENT_RESV_FILE"; printf '# comment\n%s %s dashboard-announced\n' "$(iso $((N+4*D)))" "$(iso $((N+5*D)))" > "$SOL_AGENT_STATE/maint.txt"
ok "manual maint.txt window used" "FIT $((4*D-MARGIN)) $((N+4*D))" "$(maint_plan $N)"; rm -f "$SOL_AGENT_STATE/maint.txt"
# running job that would cross a window: 1h elapsed of a 7d limit, window in 2d
resv maint $((N+2*D)) $((N+3*D)) MAINT > "$SOL_AGENT_RESV_FILE"
ok "running job crossing window gets capped" "$((3600 + 2*D - MARGIN))" "$(running_job_cap 3600 $((7*D)) $N)"
ok "running job already safe -> no cap"      ""                          "$(running_job_cap 3600 $((D)) $N)"
# dur parsing
ok "dur 6-23:07:40" 601660 "$(dur_to_s 6-23:07:40)"; ok "dur 59:03" 3543 "$(dur_to_s 59:03)"; ok "dur UNLIMITED" 999999999 "$(dur_to_s UNLIMITED)"
ok "s_to_dur" "7-00:00:00" "$(s_to_dur $((7*D)))"
printf '\n%d passed, %d failed\n' $pass $fail; rm -rf "$T"; [ $fail -eq 0 ]
