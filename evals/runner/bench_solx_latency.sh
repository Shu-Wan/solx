#!/usr/bin/env bash
# Benchmark solx's per-command latency against the equivalent raw SLURM
# command, on Sol. solx wraps squeue/salloc/srun in Python; on Sol's NFS home
# each invocation pays interpreter start + module imports (rich, the command
# body) that a raw SLURM binary does not. This quantifies that tax so the
# skill's "solx vs raw SLURM" guidance is grounded in real numbers rather than
# a guess.
#
# This is an L3 (real-Sol) measurement — the numbers only mean anything on a
# Sol login/compute node, where the NFS home and a live Slurm controller are
# what they are. It is strictly READ-ONLY: it times `squeue`, `solx job
# list/time`, and `solx --version`; it never allocates or cancels anything.
#
# Usage: evals/runner/bench_solx_latency.sh [N]    (N = timed runs/command, default 7)
set -u

# Force a C locale: `time`/`%R` and awk honor LC_NUMERIC, so under a
# comma-decimal locale (common on shared logins) `sort -n` and the median
# arithmetic would silently mis-handle the timings.
export LC_ALL=C

N="${1:-7}"
case "$N" in
    '' | *[!0-9]* | 0)
        echo "usage: bench_solx_latency.sh [N]  (N = positive integer runs/command)" >&2
        exit 2
        ;;
esac

command -v squeue >/dev/null 2>&1 || {
    echo "not on Sol (no squeue on PATH) — run this on a Sol login/compute node." >&2
    exit 2
}
command -v solx >/dev/null 2>&1 || {
    echo "solx not on PATH — install it first (curl … install.sh | sh)." >&2
    exit 2
}

_median() { sort -n | awk '{a[NR]=$1} END{print (NR%2)?a[(NR+1)/2]:(a[NR/2]+a[NR/2+1])/2}'; }

# bench "<label>" cmd args...  — prints "<label>  warm <median>s (n=N) · cold <t>s"
# Reports BOTH the warm median (steady state for repeated calls in a session)
# and the cold first invocation (the NFS/import-storm tax a fresh session
# pays once). A command that exits non-zero is reported as failed and never
# folded into the median, so a Slurm hiccup can't masquerade as a fast read.
bench() {
    label="$1"; shift
    # First invocation = cold (caches cold); it also warms the rest.
    cold="$( { TIMEFORMAT='%R'; time "$@" >/dev/null 2>&1; } 2>&1 )"
    if [ $? -ne 0 ]; then
        printf '  %-28s (command failed — skipped)\n' "$label"
        return
    fi
    times=""
    i=0
    while [ "$i" -lt "$N" ]; do
        i=$((i + 1))
        t="$( { TIMEFORMAT='%R'; time "$@" >/dev/null 2>&1; } 2>&1 )"
        [ $? -eq 0 ] || continue   # don't fold a failed run into the median
        times="$times$t
"
    done
    med="$(printf '%s' "$times" | grep . | _median)"
    printf '  %-28s warm %ss (n=%d) · cold %ss\n' "$label" "${med:-?}" "$N" "$cold"
}

echo "host: $(hostname)   SLURM_JOB_ID=${SLURM_JOB_ID:-<login node>}   solx $(solx --version 2>/dev/null)"
echo

echo "list my jobs:"
bench "squeue --me (raw)"   squeue --me
bench "solx job list"       solx job list

if [ -n "${SLURM_JOB_ID:-}" ]; then
    echo "time left on this allocation:"
    bench "squeue -o %L (raw)"  squeue -h -j "$SLURM_JOB_ID" -o %L
    bench "solx job time"       solx job time
fi

echo "startup floor:"
bench "solx --version"      solx --version

echo
echo "Takeaway: a raw SLURM read is ~0.05-0.07s; a solx 'job' command pays Python"
echo "startup + imports on the NFS home (~1-2s here), independent of install"
echo "channel. Use raw squeue/scancel for one-off status/cancel; reserve solx"
echo "for the multi-step lifecycle (job start/jump) and keep, where it removes"
echo "real friction. See skills/sol-skill/SKILL.md ('solx vs raw SLURM')."
