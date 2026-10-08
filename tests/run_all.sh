#!/bin/bash
# Esegue l'intera suite di test (nessuna GPU, Docker o rete reali: tutto simulato con stub).
# Requisiti: bash, python3, `pip install flask pydantic requests`.   Uso: bash tests/run_all.sh
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO" || exit 1
rc=0; results=()
run() { # <etichetta> <comando...>
  local label="$1"; shift; local out; out=$("$@" 2>&1); local r=$?
  local n; n=$(echo "$out" | grep -Eo '^[0-9]+ controlli' | head -1)
  if [ $r = 0 ]; then results+=("PASS  ${label}  (${n:-ok})"); else results+=("FAIL  ${label}"); rc=1; echo "$out" | grep -E 'FAIL|Error|Traceback' | head -8; fi
}
echo "== sintassi"
if bash tests/check_syntax.sh; then results+=("PASS  sintassi python/bash (solo file tracciati)"); else results+=("FAIL  sintassi python/bash"); rc=1; fi
echo "== test"
run "endpoint /vram"         python3 tests/test_vram_endpoint.py
run "daemon VRAM"            python3 tests/test_vram_daemon.py
run "manifold"               python3 tests/test_manifold.py
run "image_loop"             python3 tests/test_image_loop.py
run "bootstrap filter"       python3 tests/test_bootstrap.py
run "launcher (scenari S1-S14)"  bash tests/test_launcher.sh
run "consumi (power)"        bash tests/test_power.sh
run "benchmark modelli"       bash tests/test_bench.sh
run "allineamento locale"     bash tests/test_sync.sh
run "smoke test"             bash tests/test_smoke.sh
echo; printf '%s\n' "${results[@]}"; echo; [ $rc = 0 ] && echo "TUTTI I TEST OK" || echo "ALCUNI TEST FALLITI"; exit $rc
