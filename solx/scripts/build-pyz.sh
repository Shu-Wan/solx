#!/usr/bin/env bash
# Build dist/solx.pyz — solx and its dependencies as a single-file zipapp.
#
# Why a zipapp: on an NFS home (Sol), a venv install pays one network
# round-trip per module file at cold start; a .pyz is one file open, so a
# cold `solx` start stays fast no matter how many modules are inside.
#
# Bytecode is precompiled in legacy layout (compileall -b puts `mod.pyc`
# beside `mod.py`) because that is the layout zipimport loads — it never
# writes a bytecode cache of its own. The .pyc format is interpreter-
# specific, so the version here must match the shebang install.sh stamps:
# both default to PYVER below and read SOLX_PYTHON to override together.
set -euo pipefail

PYVER="${SOLX_PYTHON:-3.13}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$ROOT/build/pyz"

uv python find "$PYVER" >/dev/null 2>&1 || uv python install "$PYVER"
PY="$(uv python find "$PYVER")"

rm -rf "$STAGE"
mkdir -p "$STAGE" "$ROOT/dist"

uv pip install --python "$PY" --target "$STAGE" --quiet "$ROOT"
rm -rf "$STAGE/bin"  # entry-point scripts; the zipapp __main__ replaces them

"$PY" -m compileall -b -q "$STAGE"
"$PY" -m zipapp "$STAGE" -o "$ROOT/dist/solx.pyz" -m "solx.cli:app" -c

echo "built $ROOT/dist/solx.pyz ($(du -h "$ROOT/dist/solx.pyz" | cut -f1), python $PYVER)"
