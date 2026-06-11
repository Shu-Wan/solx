#!/usr/bin/env bash
# Run the solx behavioral parity matrix against one solx binary.
#
#   run_matrix.sh /path/to/solx OUTDIR
#
# Each case runs in a fresh fake HOME (+XDG_CONFIG_HOME) with deterministic
# SLURM mocks on PATH, and captures stdout / stderr / exit code into
# OUTDIR/<case>.{out,err,code}. Paths that embed the per-case tempdir are
# normalized to __HOME__ so two runs (or two solx implementations) diff clean.
set -u
SOLX="$1"
OUTDIR="$2"
PARITY="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$OUTDIR"

run_case() {
    # run_case NAME XDG_FIXTURE(sample|nokeep|empty) [VAR=VAL ...] -- ARGS...
    local name="$1" xdg_fix="$2"; shift 2
    local envs=()
    while [ "$1" != "--" ]; do envs+=("$1"); shift; done
    shift  # drop --

    local home; home="$(mktemp -d /tmp/solx-parity-case-XXXXXX)"
    mkdir -p "$home/.config/solx"
    cp "$PARITY"/fixtures/home/*.csv "$home/" 2>/dev/null
    case "$xdg_fix" in
        sample) cp "$PARITY/fixtures/config-sample.toml" "$home/.config/solx/config.toml" ;;
        nokeep) cp "$PARITY/fixtures/config-nokeep.toml" "$home/.config/solx/config.toml" ;;
        empty)  ;;
    esac

    # Substitute the per-case home into args (for flags that take a path).
    local args=()
    local a
    for a in "$@"; do args+=("${a//__HOMEDIR__/$home}"); done

    env -i \
        PATH="$PARITY/bin:/usr/bin:/bin" \
        HOME="$home" \
        XDG_CONFIG_HOME="$home/.config" \
        USER=sparky \
        LOGNAME=sparky \
        TERM=dumb \
        LC_ALL=C \
        "${envs[@]+"${envs[@]}"}" \
        "$SOLX" "${args[@]+"${args[@]}"}" >"$OUTDIR/$name.out" 2>"$OUTDIR/$name.err"
    echo $? > "$OUTDIR/$name.code"

    # Normalize per-case tempdir paths so runs are comparable.
    sed -i "s|$home|__HOME__|g" "$OUTDIR/$name.out" "$OUTDIR/$name.err"
    rm -rf "$home"
}

# Special: init-exists needs init run twice in the SAME home.
run_case_init_twice() {
    local name="$1"
    local home; home="$(mktemp -d /tmp/solx-parity-case-XXXXXX)"
    mkdir -p "$home/.config"
    env -i PATH="$PARITY/bin:/usr/bin:/bin" HOME="$home" XDG_CONFIG_HOME="$home/.config" \
        USER=sparky LOGNAME=sparky TERM=dumb LC_ALL=C \
        "$SOLX" init >/dev/null 2>&1
    env -i PATH="$PARITY/bin:/usr/bin:/bin" HOME="$home" XDG_CONFIG_HOME="$home/.config" \
        USER=sparky LOGNAME=sparky TERM=dumb LC_ALL=C \
        "$SOLX" init >"$OUTDIR/$name.out" 2>"$OUTDIR/$name.err"
    echo $? > "$OUTDIR/$name.code"
    sed -i "s|$home|__HOME__|g" "$OUTDIR/$name.out" "$OUTDIR/$name.err"
    rm -rf "$home"
}

# ---- top level / meta ------------------------------------------------------
run_case version-flag        sample -- --version
run_case version-cmd         sample -- version
run_case help-flag           sample -- --help
run_case help-cmd            sample -- help
run_case no-args             sample --
run_case unknown-cmd         sample -- frobnicate
run_case job-noargs          sample -- job
run_case job-badsub          sample -- job frobnicate

# ---- job list --------------------------------------------------------------
run_case job-list-json       sample -- --json job list
run_case job-list-piped      sample -- job list
run_case jobs-alias          sample -- --json jobs list
run_case job-ls-alias        sample -- --json job ls
run_case job-list-empty      sample MOCK_SQUEUE_EMPTY=1 -- --json job list
run_case job-list-fail       sample MOCK_SQUEUE_FAIL=1 -- job list

# ---- job time --------------------------------------------------------------
run_case job-time-inside     sample SLURM_JOB_ID=54800001 -- --json job time
run_case job-time-arg        sample -- --json job time 12345
run_case job-time-mostrecent sample -- --json job time
run_case job-time-empty      sample MOCK_SQUEUE_EMPTY=1 -- --json job time

# ---- job stop ---------------------------------------------------------------
run_case job-stop-ambig      sample -- --json job stop
run_case job-stop-dryrun     sample -- --json job stop 12345 -n
run_case job-stop-yes        sample -- --json job stop 12345 -y
run_case job-stop-force      sample -- --json job stop 12345 --force
run_case job-stop-yn         sample -- job stop 12345 -y -n
run_case job-stop-noninter   sample -- job stop 12345
run_case job-stop-self       sample SLURM_JOB_ID=12345 -- --json job stop 12345 -n

# ---- job start --------------------------------------------------------------
run_case job-start-dry            sample -- --json job start -n
run_case job-start-dry-tmpl       sample -- --json job start gpu -n
run_case job-start-dry-dashdash   sample -- --json job start gpu -n -- --mem=128G
run_case job-start-dry-mixed      sample -- --json job start gpu -n --mem=128G -c 8
run_case job-start-dry-dd-notmpl  sample -- --json job start -n -- --mem=128G
run_case job-start-real           sample -- --json job start
run_case job-start-badtimeout     sample -- job start --timeout never
run_case job-start-unknown-tmpl   sample -- --json job start nosuch -n
run_case job-start-timeout-dry    sample -- --json job start --timeout 30s -n

# ---- jump ---------------------------------------------------------------
run_case jump-arg            sample -- --json jump 12345 -q
run_case jump-noarg          sample -- jump
run_case jump-inside         sample SLURM_JOB_ID=999 -- jump
run_case jump-mostrecent     sample MOCK_SQUEUE_TWORUNNING=1 -- jump
run_case job-jump-arg        sample -- --json job jump 12345 -q

# ---- keep ---------------------------------------------------------------
run_case keep-dry            sample -- --json keep -n
run_case keep-dry-stage      sample -- --json keep -n --stage pending
run_case keep-dry-over90     sample -- --json keep -n --stage over90
run_case keep-dry-verbose    sample -- keep -n -v
run_case keep-invalid-stage  sample -- keep --stage bogus
run_case keep-yes            sample -- --json keep -y -j 1
run_case keep-nothing        empty  -- keep -n

# ---- config ---------------------------------------------------------------
run_case config-show         sample -- config show
run_case config-show-json    sample -- config show --json
run_case config-show-rootjson sample -- --json config show
run_case config-edit-ok      sample EDITOR=true -- config edit
run_case config-edit-flags   sample EDITOR="/bin/echo -n" -- config edit
run_case config-edit-noconfig empty EDITOR=true -- config edit

# ---- init ---------------------------------------------------------------
run_case init-fresh          empty -- --json init
run_case_init_twice init-exists
run_case init-force          sample -- --json init -f

# ---- completions -----------------------------------------------------------
run_case completions-bash    sample -- completions bash
run_case completions-zsh     sample -- completions zsh
run_case completions-fish    sample -- completions fish
run_case completions-tcsh    sample -- completions tcsh

# ---- dispatch edge cases -----------------------------------------------------
# `--` shielding: tokens after `--` pass through to sbatch verbatim.
run_case js-dd-shield-n       sample -- --json job start gpu -- -n
run_case js-dd-shield-n4      sample -- --json job start gpu -- -n 4
run_case js-dd-shield-timeout sample -- --json job start -- --timeout 30s
run_case js-dd-dd             sample -- --json job start gpu -n -- --mem=1G -- -c 2
run_case js-bundled-shorts    sample -- --json job start -nn
run_case js-dryrun-eq         sample -- job start --dry-run=true
run_case version-junk-arg     sample -- version bogus
run_case version-junk-pre     sample -- --bogus --version
run_case version-junk-post    sample -- --version --bogus
run_case keep-j-zero          sample -- keep -n -j 0
run_case help-job-arg         sample -- help job
run_case dash-h-root          sample -- -h
run_case dash-h-stop          sample -- job stop 12345 -h

# ---- known divergence probes (documented, not strict) -----------------------
run_case leaf-json-position  sample -- job list --json

echo "matrix complete: $(ls "$OUTDIR" | grep -c '\.code$') cases -> $OUTDIR"
