#!/bin/bash
# Test di orchestra_bench_models.sh con Ollama simulato (aritmetica di throughput, avvisi, ruoli).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
B="$REPO/document-ai/scripts/orchestra_bench_models.sh"; ok=1; n=0
pass() { n=$((n+1)); echo "PASS $*"; }; fail() { n=$((n+1)); echo "FAIL $*"; ok=0; }
chk()  { if eval "$2"; then pass "$1"; else fail "$1   [$2]"; fi; }
export STUB_BIN="$HERE/helpers/stubs"; export PATH="$STUB_BIN:$PATH"
unset ORCHESTRA_GPU_MAIN ORCHESTRA_GPU_AUX
setup() { export STUB_STATE="$(mktemp -d)"; mkdir -p "$STUB_STATE/containers" "$STUB_STATE/models"
  printf '%s\n' --gpus device=GPU-3090-UUID -e OLLAMA_NUM_PARALLEL="${1:-2}" > "$STUB_STATE/containers/ai-ollama-session.args"
  printf '%s\n' --gpus device=GPU-4060-UUID -e OLLAMA_NUM_PARALLEL=1 > "$STUB_STATE/containers/ai-ollama-aux-session.args"
  printf '%s\n' qwen3.6:27b llama3.1:8b qwen3.8:27b > "$STUB_STATE/models/ai-ollama-session"
  printf '%s\n' llama3.2:3b > "$STUB_STATE/models/ai-ollama-aux-session"
  unset FAKE_PROC STUB_CURL_FAIL FAKE_API_ERROR; }
row() { echo "$out" | awk -v m="$1" -v n="$2" '$1==m && $2==n'; }

echo "== misura sul main, concorrenza 1 e 2"; setup 2
out=$(bash "$B" --parallel "1 2" qwen3.6:27b 2>&1); rc=$?
chk "esce con 0"                                '[ $rc = 0 ]'
chk "intestazione con backend e NUM_PARALLEL"   'echo "$out" | grep -q "Backend: main (http://127.0.0.1:11435).*OLLAMA_NUM_PARALLEL=2"'
r1=$(row qwen3.6:27b 1); r2=$(row qwen3.6:27b 2)
chk "N=1: 65.0 tok/s per flusso (65 tok/s a 350 W)" 'echo "$r1" | awk "{exit !(\$3==\"65.0\")}"'
chk "N=1: throughput totale 57.7 (include il prompt)" 'echo "$r1" | awk "{exit !(\$4==\"57.7\")}"'
chk "N=2: throughput totale 115.4 (2 flussi)"   'echo "$r2" | awk "{exit !(\$4==\"115.4\")}"'
chk "N=2: speedup 2.00x rispetto a N=1"         'echo "$r2" | awk "{exit !(\$5==\"2.00x\")}"'
chk "mostra 100% GPU"                           'echo "$r1" | grep -q "100% GPU"'
chk "mostra la VRAM usata (728 base + 9000 del modello = 9728 MiB)"  'echo "$r1" | grep -q "9728"'
chk "nessun avviso di offload"                  '! echo "$out" | grep -q "NON e. al 100% in GPU"'
chk "il modello viene scaricato a fine misura"  'grep -q "\"keep_alive\":0" "$STUB_STATE/calls.log" || grep -q "keep_alive.:0" "$STUB_STATE/calls.log"'

chk "mostra la versione di Ollama"             'echo "$out" | grep -q "Ollama: ollama version is 0.99.0"'
chk "intestazione: contesto 8192 (come il manifold)" 'echo "$out" | grep -q "contesto 8192"'
chk "tutte le richieste inviano num_ctx 8192"   'grep "api/generate" "$STUB_STATE/calls.log" | grep -v "keep_alive.:0" | grep -c "num_ctx.:8192" | grep -qv "^0$" && ! grep "api/generate" "$STUB_STATE/calls.log" | grep -v "keep_alive.:0" | grep -v "num_ctx.:8192" | grep -q .'
chk "mostra la riga di ollama ps"               'echo "$out" | grep -q "ollama ps: qwen3.6:27b"'
setup 2; out=$(bash "$B" --ctx 4096 --parallel 1 qwen3.6:27b 2>&1)
chk "--ctx 4096 e rispettato"                   'echo "$out" | grep -q "contesto 4096" && grep "api/generate" "$STUB_STATE/calls.log" | grep -v "keep_alive.:0" | grep -q "num_ctx.:4096"'
echo "== memoria gia occupata (ComfyUI con SDXL in VRAM) e --free-comfy"; setup 2; echo 7000 > "$STUB_STATE/bump_GPU-3090-UUID"
out=$(bash "$B" --parallel 1 qwen3.6:27b 2>&1)
chk "avvisa: GPU gia occupata (7728 MiB) e suggerisce --free-comfy" 'echo "$out" | grep -q "ATTENZIONE: la GPU ha gia 7728 MiB occupati" && echo "$out" | grep -q -- "--free-comfy"'
setup 2; echo 7000 > "$STUB_STATE/bump_GPU-3090-UUID"; out=$(bash "$B" --free-comfy --parallel 1 qwen3.6:27b 2>&1)
chk "--free-comfy: chiama /free di ComfyUI"     'grep -q "curl.*8188/free" "$STUB_STATE/calls.log" && echo "$out" | grep -q "ComfyUI svuotato"'
chk "--free-comfy: nessun avviso di memoria occupata" '! echo "$out" | grep -q "ATTENZIONE: la GPU ha gia"'
setup 2; out=$(bash "$B" --parallel 1 qwen3.6:27b 2>&1)
chk "GPU libera: nessun avviso"                 '! echo "$out" | grep -q "ATTENZIONE: la GPU ha gia"'

echo "== mediana, ripetizioni e telemetria (distinguere link, throttling e modello)"
chk "--median dispari"                          '[ "$(bash "$B" --median 10 30 20)" = "20.0" ]'
chk "--median pari (media dei due centrali)"    '[ "$(bash "$B" --median 1 2 3 4)" = "2.5" ]'
chk "--median di un solo valore"                '[ "$(bash "$B" --median 7)" = "7.0" ]'
setup 2; out=$(bash "$B" --repeat 3 --parallel "1 2" qwen3.6:27b 2>&1)
chk "intestazione: ripetizioni 3"               'echo "$out" | grep -q "ripetizioni 3"'
chk "riga delle ripetizioni (3, min-max)"       'echo "$out" | grep -q "ripetizioni: 3 · tok/s totali min-max"'
chk "con 3 ripetizioni i valori restano corretti (N=2: 115.4 totali, 2.00x)" 'row qwen3.6:27b 2 | awk "{exit !(\$4==\"115.4\" && \$5==\"2.00x\")}"'
setup 2; out=$(bash "$B" --telemetry --parallel 1 qwen3.6:27b 2>&1)
chk "telemetria: utilizzo, watt (60% di 350) e clock"  'echo "$out" | grep -q "telemetria: GPU 35% · 210 W · SM 1800 MHz"'
chk "telemetria: link PCIe osservato (Gen3 x4) e traffico reale in MB/s" 'echo "$out" | grep -q "PCIe Gen3 x4 · rx 120 (picco 120) / tx 40 (picco 40) MB/s"'
chk "telemetria: nessun throttling"             'echo "$out" | grep -q "throttle: nessuno"'
chk "telemetria: token per joule (57.7 / 210 = 0.27)" 'echo "$out" | grep -q "0.27 tok/J"'
setup 2; export FAKE_THROTTLE=0x0000000000000024; out=$(bash "$B" --telemetry --parallel 1 qwen3.6:27b 2>&1)
chk "telemetria: riconosce power-cap e thermal-sw" 'echo "$out" | grep -q "throttle: power-cap, thermal-sw"'
unset FAKE_THROTTLE
setup 2; export FAKE_NEW_DRIVER=1 FAKE_PCIE_RX=900 FAKE_PCIE_TX=300; out=$(bash "$B" --telemetry --parallel 1 qwen3.6:27b 2>&1)
chk "driver recente (clocks_event_reasons) e traffico PCIe diverso rilevati" 'echo "$out" | grep -q "rx 900 (picco 900) / tx 300 (picco 300) MB/s"'
unset FAKE_NEW_DRIVER FAKE_PCIE_RX FAKE_PCIE_TX
setup 2; export FAKE_EVAL_TOKENS=64; out=$(bash "$B" --parallel 1 qwen3.6:27b 2>&1)
chk "richieste brevi: avviso di misura rumorosa" 'echo "$out" | grep -q "AVVISO: richieste molto brevi" && echo "$out" | grep -q -- "--repeat 3"'
unset FAKE_EVAL_TOKENS
setup 2; out=$(bash "$B" --parallel 1 qwen3.6:27b 2>&1)
chk "richieste lunghe (3.9 s): nessun avviso"   '! echo "$out" | grep -q "AVVISO: richieste molto brevi"'

echo "== modello non installato / errore dell API"; setup 2
out=$(bash "$B" --parallel 1 modello-inesistente:1b 2>&1); rc=$?
chk "modello mancante: messaggio con il comando di pull" '[ $rc = 1 ] && echo "$out" | grep -q "non installato su main" && echo "$out" | grep -q "ollama pull modello-inesistente:1b"'
chk "suggerisce l aggiornamento di Ollama (errore 412)" 'echo "$out" | grep -q "ORCHESTRA_PULL_IMAGES=1"'
setup 2; export FAKE_API_ERROR="this model requires a newer version of Ollama"; out=$(bash "$B" --parallel 1 qwen3.8:27b 2>&1); rc=$?
chk "errore dell API mostrato (non solo errore generico)" '[ $rc = 1 ] && echo "$out" | grep -q "Ollama risponde: this model requires a newer version of Ollama"'
unset FAKE_API_ERROR

echo "== concorrenza oltre NUM_PARALLEL"; setup 1
out=$(bash "$B" --parallel "1 4" qwen3.6:27b 2>&1)
chk "avviso: N=4 > OLLAMA_NUM_PARALLEL=1"       'echo "$out" | grep -q "ATTENZIONE: N=4 > OLLAMA_NUM_PARALLEL=1"'

echo "== offload su CPU rilevato"; setup 2; export FAKE_PROC="48%/52% CPU/GPU"
out=$(bash "$B" qwen3.6:27b 2>&1)
chk "avviso: non al 100% in GPU"                'echo "$out" | grep -q "NON e. al 100% in GPU"'
unset FAKE_PROC

echo "== piu' modelli e ruolo aux"; setup 2
out=$(bash "$B" --parallel 1 qwen3.6:27b llama3.1:8b 2>&1)
chk "una riga per modello"                      '[ -n "$(row qwen3.6:27b 1)" ] && [ -n "$(row llama3.1:8b 1)" ]'
setup 2; out=$(bash "$B" --role aux --parallel 1 llama3.2:3b 2>&1)
chk "ruolo aux: backend e porta 11436"          'echo "$out" | grep -q "Backend: aux (http://127.0.0.1:11436)" && grep -q ":11436/api/generate" "$STUB_STATE/calls.log"'
chk "ruolo aux: la VRAM misurata e quella della 4060" 'echo "$out" | grep -q "GPU GPU-4060-UUID"'

echo "== errori"; setup 2
bash "$B" >/dev/null 2>&1; chk "senza modelli: uso e rc=2" '[ $? = 2 ]'
bash "$B" --role xyz m >/dev/null 2>&1; chk "ruolo non valido: rc=2" '[ $? = 2 ]'
setup 2; export STUB_CURL_FAIL='api/generate'; out=$(bash "$B" --parallel 1 qwen3.6:27b 2>&1); rc=$?
chk "Ollama irraggiungibile: riga errore e rc=1, nessun crash" 'echo "$out" | grep -q "errore" && [ $rc = 1 ]'

echo; echo "$n controlli"; [ $ok = 1 ] && echo "BENCH ALL OK" || echo "BENCH FAILED"; [ $ok = 1 ]
