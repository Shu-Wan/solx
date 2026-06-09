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
trap 'rm -f "$TMP"' EXIT
case "$SRC" in
    http://* | https://*) curl -fsSL "$SRC" -o "$TMP" ;;
    *) cp "$SRC" "$TMP" ;;
esac

mkdir -p "$BIN"
# Remove first: a previous `uv tool install` leaves a symlink here, and
# writing through it would clobber the tool venv's entry point instead.
rm -f "$BIN/solx"
# The artifact carries the build machine's shebang so it runs in place;
# drop it and stamp one bound to this machine's interpreter.
SKIP=0
if [ "$(head -c 2 "$TMP")" = "#!" ]; then
    SKIP="$(head -n 1 "$TMP" | wc -c)"
fi
{
    printf '#!%s\n' "$PY"
    tail -c +$((SKIP + 1)) "$TMP"
} >"$BIN/solx"
chmod +x "$BIN/solx"

echo "installed $BIN/solx (solx $("$BIN/solx" --version))"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "note: $BIN is not on your PATH" >&2 ;;
esac
