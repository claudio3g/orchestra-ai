#!/bin/bash
# Controlla la sintassi dei soli file TRACCIATI (bash -n per .sh, ast.parse per .py).
# Ignora i file locali non tracciati (copie del codice in document-ai/system, .v01, ecc.), che possono essere
# vecchie o incomplete e darebbero falsi allarmi, e non scrive nulla nell albero (niente __pycache__).
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
if git rev-parse --is-inside-work-tree >/dev/null 2>&1 && [ -n "$(git ls-files | head -1)" ]; then
    files() { git ls-files -- "$@"; }
else
    files() { for p in "$@"; do find . -name "$p" -not -path './.git/*' -not -path './ComfyUI/*'; done; }
fi
bad=0; ns=0; np=0
while IFS= read -r f; do [ -f "$f" ] || continue; ns=$((ns+1)); bash -n "$f" 2>/dev/null || { echo "bash -n FALLITO: $f"; bad=1; }; done < <(files '*.sh')
while IFS= read -r f; do [ -f "$f" ] || continue; np=$((np+1))
    python3 -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" "$f" 2>/dev/null || { echo "sintassi python FALLITA: $f"; bad=1; }; done < <(files '*.py')
echo "controllati ${ns} script bash e ${np} file python tracciati"
exit $bad
