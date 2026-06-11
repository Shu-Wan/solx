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
    # run_case NAME XDG_FIXTURE(sample|nokeep|empty) HOME_SOLKEEP(yes|no|lossy) [VAR=VAL ...] -- ARGS...
    local name="$1" xdg_fix="$2" solkeep="$3"; shift 3
    local envs=()
    while [ "$1" != "--" ]; do envs+=("$1"); shift; done
    shift  # drop --

    local home; home="$(mktemp -d /tmp/solx-parity-case-XXXXXX)"
    mkdir -p "$home/.config/solx"
    cp "$PARITY"/fixtures/home/*.csv "$home/" 2>/dev/null
    case "$solkeep" in
        yes)   cp "$PARITY/fixtures/home/.solkeep" "$home/.solkeep" ;;
        lossy) cp "$PARITY/fixtures/solkeep-lossy" "$home/.solkeep" ;;
        no)    ;;
    esac
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

# Special: import-solkeep-ok also snapshots the resulting config.
run_case_import_ok() {
    local name="$1"
    local home; home="$(mktemp -d /tmp/solx-parity-case-XXXXXX)"
    mkdir -p "$home/.config/solx"
    cp "$PARITY/fixtures/config-nokeep.toml" "$home/.config/solx/config.toml"
    cp "$PARITY/fixtures/home/.solkeep" "$home/.solkeep"
    env -i PATH="$PARITY/bin:/usr/bin:/bin" HOME="$home" XDG_CONFIG_HOME="$home/.config" \
        USER=sparky LOGNAME=sparky TERM=dumb LC_ALL=C \
        "$SOLX" config import-solkeep >"$OUTDIR/$name.out" 2>"$OUTDIR/$name.err"
    echo $? > "$OUTDIR/$name.code"
    cp "$home/.config/solx/config.toml" "$OUTDIR/$name.config-after"
    sed -i "s|$home|__HOME__|g" "$OUTDIR/$name.out" "$OUTDIR/$name.err" "$OUTDIR/$name.config-after"
    rm -rf "$home"
}

# ---- top level / meta ------------------------------------------------------
run_case version-flag        sample yes -- --version
run_case version-cmd         sample yes -- version
run_case help-flag           sample yes -- --help
run_case help-cmd            sample yes -- help
run_case no-args             sample yes --
run_case unknown-cmd         sample yes -- frobnicate
run_case job-noargs          sample yes -- job
run_case job-badsub          sample yes -- job frobnicate

# ---- job list --------------------------------------------------------------
run_case job-list-json       sample yes -- --json job list
run_case job-list-piped      sample yes -- job list
run_case jobs-alias          sample yes -- --json jobs list
run_case job-ls-alias        sample yes -- --json job ls
run_case job-list-empty      sample yes MOCK_SQUEUE_EMPTY=1 -- --json job list
run_case job-list-fail       sample yes MOCK_SQUEUE_FAIL=1 -- job list

# ---- job time --------------------------------------------------------------
run_case job-time-inside     sample yes SLURM_JOB_ID=54800001 -- --json job time
run_case job-time-arg        sample yes -- --json job time 12345
run_case job-time-mostrecent sample yes -- --json job time
run_case job-time-empty      sample yes MOCK_SQUEUE_EMPTY=1 -- --json job time

# ---- job stop ---------------------------------------------------------------
run_case job-stop-ambig      sample yes -- --json job stop
run_case job-stop-dryrun     sample yes -- --json job stop 12345 -n
run_case job-stop-yes        sample yes -- --json job stop 12345 -y
run_case job-stop-force      sample yes -- --json job stop 12345 --force
run_case job-stop-yn         sample yes -- job stop 12345 -y -n
run_case job-stop-noninter   sample yes -- job stop 12345
run_case job-stop-self       sample yes SLURM_JOB_ID=12345 -- --json job stop 12345 -n

# ---- job start --------------------------------------------------------------
run_case job-start-dry            sample yes -- --json job start -n
run_case job-start-dry-tmpl       sample yes -- --json job start gpu -n
run_case job-start-dry-dashdash   sample yes -- --json job start gpu -n -- --mem=128G
run_case job-start-dry-mixed      sample yes -- --json job start gpu -n --mem=128G -c 8
run_case job-start-dry-dd-notmpl  sample yes -- --json job start -n -- --mem=128G
run_case job-start-real           sample yes -- --json job start
run_case job-start-badtimeout     sample yes -- job start --timeout never
run_case job-start-unknown-tmpl   sample yes -- --json job start nosuch -n
run_case job-start-timeout-dry    sample yes -- --json job start --timeout 30s -n

# ---- jump ---------------------------------------------------------------
run_case jump-arg            sample yes -- --json jump 12345 -q
run_case jump-noarg          sample yes -- jump
run_case jump-inside         sample yes SLURM_JOB_ID=999 -- jump
run_case jump-mostrecent     sample yes MOCK_SQUEUE_TWORUNNING=1 -- jump
run_case job-jump-arg        sample yes -- --json job jump 12345 -q

# ---- keep ---------------------------------------------------------------
run_case keep-dry            sample yes -- --json keep -n
run_case keep-dry-stage      sample yes -- --json keep -n --stage pending
run_case keep-dry-over90     sample yes -- --json keep -n --stage over90
run_case keep-dry-verbose    sample yes -- keep -n -v
run_case keep-invalid-stage  sample yes -- keep --stage bogus
run_case keep-yes            sample yes -- --json keep -y -j 1
run_case keep-solkeep-flag   sample yes -- --json keep -n --solkeep __HOMEDIR__/.solkeep
run_case keep-fallback       empty  yes -- keep -n
run_case keep-nothing        empty  no  -- keep -n

# ---- config ---------------------------------------------------------------
run_case config-show         sample yes -- config show
run_case config-show-json    sample yes -- config show --json
run_case config-show-rootjson sample yes -- --json config show
run_case config-edit-ok      sample yes EDITOR=true -- config edit
run_case config-edit-flags   sample yes EDITOR="/bin/echo -n" -- config edit
run_case config-edit-noconfig empty yes EDITOR=true -- config edit
run_case_import_ok import-solkeep-ok
run_case import-solkeep-exists  sample yes -- config import-solkeep
run_case import-solkeep-noconfig empty yes -- config import-solkeep
run_case import-solkeep-lossy   nokeep lossy -- config import-solkeep
run_case import-solkeep-lossy-f nokeep lossy -- --json config import-solkeep -f

# ---- init ---------------------------------------------------------------
run_case init-fresh          empty  yes -- --json init
run_case_init_twice init-exists
run_case init-force          sample yes -- --json init -f

# ---- completions -----------------------------------------------------------
run_case completions-bash    sample yes -- completions bash
run_case completions-zsh     sample yes -- completions zsh
run_case completions-fish    sample yes -- completions fish
run_case completions-tcsh    sample yes -- completions tcsh

# ---- dispatch edge cases -----------------------------------------------------
# `--` shielding: tokens after `--` pass through to sbatch verbatim.
run_case js-dd-shield-n       sample yes -- --json job start gpu -- -n
run_case js-dd-shield-n4      sample yes -- --json job start gpu -- -n 4
run_case js-dd-shield-timeout sample yes -- --json job start -- --timeout 30s
run_case js-dd-dd             sample yes -- --json job start gpu -n -- --mem=1G -- -c 2
run_case js-bundled-shorts    sample yes -- --json job start -nn
run_case js-dryrun-eq         sample yes -- job start --dry-run=true
run_case version-junk-arg     sample yes -- version bogus
run_case version-junk-pre     sample yes -- --bogus --version
run_case version-junk-post    sample yes -- --version --bogus
run_case keep-j-zero          sample yes -- keep -n -j 0
run_case help-job-arg         sample yes -- help job
run_case dash-h-root          sample yes -- -h
run_case dash-h-stop          sample yes -- job stop 12345 -h

# ---- known divergence probes (documented, not strict) -----------------------
run_case leaf-json-position  sample yes -- job list --json

echo "matrix complete: $(ls "$OUTDIR" | grep -c '\.code$') cases -> $OUTDIR"
