#!/usr/bin/env bash
# Build the paper. TeX Live basic here lacks some scalable fonts, so microtype's
# font expansion is switched off in main.tex rather than here.
set -euo pipefail
cd "$(dirname "$0")"
export TEXINPUTS=".:./figures:"
pdflatex -interaction=nonstopmode -halt-on-error main.tex > /dev/null 2>&1 || {
  echo "PASS 1 FAILED"; grep -nE "^(! |l\.[0-9]+)" main.log | head -30; exit 1; }
bibtex main > /dev/null 2>&1 || true
pdflatex -interaction=nonstopmode main.tex > /dev/null 2>&1 || true
pdflatex -interaction=nonstopmode main.tex > /dev/null 2>&1 || true
echo "pages: $(pdfinfo main.pdf 2>/dev/null | awk '/^Pages/{print $2}')"
echo "--- undefined refs/citations ---"
grep -oE "Citation \`[^']+' on page [0-9]+ undefined" main.log | sort -u | head -20
grep -oE "Reference \`[^']+' on page [0-9]+ undefined" main.log | sort -u | head -20
echo "--- overfull > 15pt ---"
grep -E "Overfull \\\\hbox \([0-9]{2,}" main.log | head -10
