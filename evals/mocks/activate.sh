# shellcheck shell=bash
#
# Source this file to put the sol-skill mock environment on PATH.
#
#   source evals/mocks/activate.sh
#
# After sourcing:
#   - hostname, module, srun, sbatch, scancel, squeue, ssh resolve to mocks
#   - $MOCK_LOG points at a per-session invocation log (every shim appends)
#   - $HOME points at evals/mocks/home (solx config [keep] + Sol warning CSVs)
#   - $MOCK_HOSTNAME controls what `hostname` returns (default: Sol login)
#
# Toggle the side under test (export first; inline assignment with
# `source` isn't reliable across shells):
#   export MOCK_HOSTNAME=macbook.local
#   source evals/mocks/activate.sh
#
# Toggle the solx-present branch by symlinking your real solx into bin/:
#   ln -s "$(command -v solx)" evals/mocks/bin/solx
# (and remove the symlink for the no-solx branch)

if [ -n "${BASH_SOURCE[0]:-}" ]; then
    _solskill_mock_src="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION:-}" ]; then
    _solskill_mock_src="${(%):-%x}"
else
    echo "evals/mocks/activate.sh: source from bash or zsh" >&2
    return 1 2>/dev/null || exit 1
fi

_SOLSKILL_MOCK_ROOT="$(cd "$(dirname "$_solskill_mock_src")" && pwd)"
unset _solskill_mock_src

export MOCK_ROOT="$_SOLSKILL_MOCK_ROOT"
export MOCK_HOSTNAME="${MOCK_HOSTNAME:-sc001.sol.rc.asu.edu}"
export MOCK_LOG="${MOCK_LOG:-/tmp/sol-skill-mock-$$.log}"
: > "$MOCK_LOG"

# Save originals so deactivate can restore them.
export _SOLSKILL_OLD_PATH="$PATH"
export _SOLSKILL_OLD_HOME="$HOME"

export PATH="$MOCK_ROOT/bin:$PATH"
export HOME="$MOCK_ROOT/home"

# On Sol login nodes, Lmod exports `module` as a bash function and it
# wins over PATH lookup. Same risk for any of our other names if the
# user has aliases/functions defined. Clear them so the shims are
# actually reached.
for _shim in hostname module srun sbatch scancel squeue ssh; do
    unset -f "$_shim" 2>/dev/null || true
    unalias "$_shim" 2>/dev/null || true
done
unset _shim
hash -r 2>/dev/null || true

# Keep uv's cache out of the fake $HOME so a real `solx keep` run
# doesn't repopulate the mock filesystem with a Python interpreter.
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/sol-skill-uv-cache}"

solskill_mock_deactivate() {
    if [ -n "${_SOLSKILL_OLD_PATH:-}" ]; then
        export PATH="$_SOLSKILL_OLD_PATH"
        unset _SOLSKILL_OLD_PATH
    fi
    if [ -n "${_SOLSKILL_OLD_HOME:-}" ]; then
        export HOME="$_SOLSKILL_OLD_HOME"
        unset _SOLSKILL_OLD_HOME
    fi
    unset MOCK_ROOT MOCK_HOSTNAME MOCK_LOG
    unset -f solskill_mock_deactivate 2>/dev/null
}

echo "sol-skill mock active:"
echo "  PATH:          $MOCK_ROOT/bin (prepended)"
echo "  HOME:          $HOME"
echo "  MOCK_HOSTNAME: $MOCK_HOSTNAME"
echo "  MOCK_LOG:      $MOCK_LOG"
echo "Run \`solskill_mock_deactivate\` to restore the original env."
