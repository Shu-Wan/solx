#!/bin/sh
# Install solx as a single-file zipapp at ~/.local/bin/solx.
#
# Usage:
#   install.sh                  # download the latest release artifact
#   install.sh path/to/solx.pyz # install a local build (testing)
#
# Environment:
#   SOLX_INSTALL_DIR  install location (default: $XDG_BIN_HOME, falling
#                     back to ~/.local/bin)
#   SOLX_PYTHON       interpreter version stamped on the shebang
#                     (default: 3.11; must match what build-pyz.sh
#                     compiled with — the embedded bytecode is
#                     interpreter-specific)
#
# Sol's system python3 is older than solx supports, so the script
# resolves a uv-managed interpreter and binds the .pyz to it via an
# absolute shebang. uv is only needed at install time, not at runtime.
set -eu

PYVER="${SOLX_PYTHON:-3.11}"
BIN="${SOLX_INSTALL_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}"
SRC="${1:-https://github.com/Shu-Wan/solx/releases/latest/download/solx.pyz}"

command -v uv >/dev/null 2>&1 || {
    echo "solx install: uv is required to provision Python $PYVER." >&2
    echo "Install it first: https://docs.astral.sh/uv/" >&2
    exit 1
}
uv python find "$PYVER" >/dev/null 2>&1 || uv python install "$PYVER"
PY="$(uv python find "$PYVER")"

TMP="$(mktemp)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$TMP" "$STAGE"' EXIT
case "$SRC" in
    http://* | https://*) curl -fsSL "$SRC" -o "$TMP" ;;
    *) cp "$SRC" "$TMP" ;;
esac

# A zipapp records its central-directory offsets as absolute file positions
# that include the shebang line, so the interpreter cannot be rebound by
# swapping the shebang bytes — a different-length path shifts every offset and
# zipimport (which runs the archive) refuses it with "bad central directory".
# Extract the payload and rebuild the archive around this machine's
# interpreter instead, which regenerates the offsets. zipfile reads the
# build machine's shebang prefix fine; only zipimport is strict.
"$PY" -m zipfile -e "$TMP" "$STAGE"

mkdir -p "$BIN"
# Remove first: a previous `uv tool install` leaves a symlink here, and
# writing through it would clobber the tool venv's entry point instead.
rm -f "$BIN/solx"

# Rebuild the zipapp around $1 and confirm it actually runs. A correctly
# built archive runs under any matching interpreter; the smoke test is the
# guard that we never install a solx that can't start.
build_solx() {
    "$1" -m zipapp "$STAGE" -o "$BIN/solx" -p "$1" || return 1
    chmod +x "$BIN/solx"
    "$BIN/solx" --version >/dev/null 2>&1
}

if ! build_solx "$PY"; then
    # The resolved interpreter can't run the archive (a system python may be
    # built without working zipapp support). Provision a uv-managed one and
    # retry — that is what `uv python install` guarantees.
    echo "solx install: $PY can't run a zipapp; provisioning a uv-managed Python $PYVER." >&2
    UV_PYTHON_PREFERENCE=only-managed uv python install "$PYVER" >/dev/null 2>&1 || true
    PY="$(UV_PYTHON_PREFERENCE=only-managed uv python find "$PYVER")"
    build_solx "$PY" || {
        echo "solx install: could not produce a working solx with $PY." >&2
        exit 1
    }
fi

echo "installed $BIN/solx (solx $("$BIN/solx" --version))"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "note: $BIN is not on your PATH" >&2 ;;
esac
