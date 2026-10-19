#!/usr/bin/env bash
#SBATCH -p htc
#SBATCH -q public
#SBATCH -t 0:04:00
#SBATCH -c 1
#SBATCH --mem=2G
#SBATCH -J sol-agent-collect
# (stdout/stderr path is passed by `sol-agent gc`: -o "$LOGS/collect-%j.out")
# Collect an "offline" Remote Control husk: resume the session (no prompt, no tokens spent), let it re-register, close it cleanly.
# usage: sbatch sol-agent-collect.sbatch <session-uuid> <name> <workdir>
UUID=$1; SNAME=$2; WD=${3:-$HOME}
case $UUID  in ""|*[!0-9a-fA-F-]*) echo "error: session id must be a uuid" >&2; exit 2;; esac
case $SNAME in ""|*[!A-Za-z0-9._-]*) echo "error: session name may only contain letters, digits, . _ -" >&2; exit 2;; esac
for v in $(env | grep -oE '^(CLAUDECODE|CLAUDE_CODE_[A-Z_]+|CLAUDE_PID|CLAUDE_EFFORT)'); do unset "$v"; done
export PATH=$HOME/.local/bin:$PATH
TM="tmux -L sol-agent-collect-$SLURM_JOB_ID"
cd "$WD" || exit 1
$TM new-session -d -s c "claude --remote-control $SNAME --name $SNAME --resume $UUID"
sleep 25                                   # registration with Remote Control
$TM send-keys -t c Escape; sleep 0.5; $TM send-keys -t c '/exit' Enter; sleep 2; $TM send-keys -t c Enter
for i in 1 2 3 4 5 6 7 8 9 10; do $TM has-session -t c 2>/dev/null || break; sleep 1; done
if $TM has-session -t c 2>/dev/null; then echo "collect $SNAME $UUID: exit did not complete, killed"; $TM kill-server; else echo "collect $SNAME $UUID: closed cleanly"; fi
