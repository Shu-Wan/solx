#!/usr/bin/env bash
# Build the Sol cheatsheet PDF from the skill's markdown source.
# Requires pandoc + a LaTeX engine (xelatex/pdflatex). On Sol, `tinytex`
# provides the engine (see SKILL.md "Getting the Software You Need").
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/skills/sol-skill/references/cheatsheet.md"
OUT="$ROOT/docs/cheatsheet.pdf"

command -v pandoc >/dev/null || { echo "error: pandoc not found"; exit 1; }
ENGINE=""
for e in xelatex pdflatex tectonic; do
  command -v "$e" >/dev/null 2>&1 && ENGINE="$e" && break
done
[ -n "$ENGINE" ] || { echo "error: no LaTeX engine (xelatex/pdflatex/tectonic)"; exit 1; }

# Strip the decorative emoji and map a few Unicode glyphs the default
# LaTeX fonts lack to ASCII, so the build is clean and CI-portable.
# (The markdown source keeps the nicer glyphs for terminal/GitHub.)
TMP="$(mktemp --suffix=.md)"
trap 'rm -f "$TMP"' EXIT
sed -e 's/🌵 *//g' \
    -e 's/≤/<=/g' -e 's/≥/>=/g' \
    -e 's/↔/<->/g' -e 's/→/->/g' \
    "$SRC" > "$TMP"

pandoc "$TMP" -o "$OUT" \
  --pdf-engine="$ENGINE" \
  -V geometry:margin=0.6in -V fontsize=10pt -V colorlinks=true \
  --metadata title="Sol Cheatsheet"

echo "wrote $OUT ($(du -h "$OUT" | cut -f1), engine: $ENGINE)"
