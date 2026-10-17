#!/bin/bash
# Test di orchestra_smoke_test.sh: scenario sano e guasti che DEVONO essere rilevati.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
S="$REPO/document-ai/scripts/orchestra_smoke_test.sh"; ok=1; n=0
pass() { n=$((n+1)); echo "PASS $*"; }; fail() { n=$((n+1)); echo "FAIL $*"; ok=0; }
chk()  { if eval "$2"; then pass "$1"; else fail "$1   [$2]"; fi; }
export STUB_BIN="$HERE/helpers/stubs"; export PATH="$STUB_BIN:$PATH"
unset ORCHESTRA_GPU_MAIN ORCHESTRA_GPU_AUX ORCHESTRA_COMFY_ROLE

setup() { # $1 = valore --gpus del container aux
  export STUB_STATE="$(mktemp -d)"; mkdir -p "$STUB_STATE/containers" "$STUB_STATE/models"
  printf '%s\n' --network n --gpus device=GPU-3090-UUID > "$STUB_STATE/containers/ai-ollama-session.args"
  printf '%s\n' --network n --gpus "${1:-device=GPU-4060-UUID}" > "$STUB_STATE/containers/ai-ollama-aux-session.args"
  printf '%s\n' llama3.2:3b qwen2.5-coder:14b-instruct-q4_K_M qwen2.5-coder:32b > "$STUB_STATE/models/ai-ollama-session"
  printf '%s\n' llama3.2:3b moondream:v2 llava:7b > "$STUB_STATE/models/ai-ollama-aux-session"
  unset FAKE_LEAK FAKE_PROC FAKE_COMFY_NAME FAKE_ONLY_4060
}

echo "== sano, controlli passivi"; setup; out=$(bash "$S" 2>&1); rc=$?
chk "esce con 0"                                '[ $rc = 0 ]'
chk "messaggio SMOKE TEST OK"                   'echo "$out" | grep -q "SMOKE TEST OK"'
chk "nessun controllo fallito"                  '! echo "$out" | grep -q "✘"'
chk "controlla il 32b sulla main da 24 GB"      'echo "$out" | grep -q "ha il 32b"'
chk "controlla la GPU di ComfyUI"               'echo "$out" | grep -q "ComfyUI usa la GPU del ruolo .main. (NVIDIA GeForce RTX 3090)"'

echo "== sano, con --load"; setup; out=$(bash "$S" --load 2>&1); rc=$?
chk "esce con 0"                                '[ $rc = 0 ]'
chk "coordinator carica sulla 4060 (3000 MiB)"  'echo "$out" | grep -q "coordinator su aux: la memoria cresce sulla GPU attesa"'
chk "quality carica sulla 3090"                 'echo "$out" | grep -q "quality su main: la memoria cresce sulla GPU attesa"'
chk "isolamento verificato su entrambe"         '[ "$(echo "$out" | grep -c "NON cresce")" = 2 ] && ! echo "$out" | grep -q "✘"'
chk "100% GPU verificato"                       '[ "$(echo "$out" | grep -c "100% GPU")" = 2 ]'

chk "il carico usa il contesto del manifold (num_ctx 8192)" 'grep "api/generate" "$STUB_STATE/calls.log" | grep -v "keep_alive.:0" | grep -q "num_ctx.:8192"'
chk "mostra la versione di Ollama"              'echo "$out" | grep -q "versione Ollama main: ollama version is"'

echo "== --load con ComfyUI che tiene 7 GB di VRAM"; setup; echo 7000 > "$STUB_STATE/bump_GPU-3090-UUID"
out=$(bash "$S" --load 2>&1)
chk "segnala la memoria gia occupata e suggerisce --free-comfy" 'echo "$out" | grep -q "memoria gia occupata sulla main prima del carico: 7728 MiB" && echo "$out" | grep -q "Usa --free-comfy"'
setup; echo 7000 > "$STUB_STATE/bump_GPU-3090-UUID"; out=$(bash "$S" --load --free-comfy 2>&1)
chk "--free-comfy: ComfyUI svuotato e memoria di partenza bassa" 'echo "$out" | grep -q "ComfyUI svuotato" && echo "$out" | grep -q "prima del carico: 728 MiB" && ! echo "$out" | grep -q "oltre 2 GB"'

echo "== guasto: Ollama aux creato con --gpus all (vede 2 GPU)"; setup "all"; out=$(bash "$S" 2>&1); rc=$?
chk "rilevato (rc != 0)"                        '[ $rc != 0 ]'
chk "indica che l'aux vede piu' di una GPU"     'echo "$out" | grep -q "✘ Ollama aux vede UNA sola GPU"'
chk "SMOKE TEST FALLITO"                        'echo "$out" | grep -q "SMOKE TEST FALLITO"'

echo "== guasto: il carico sulla main finisce anche sull'altra GPU"; setup; export FAKE_LEAK=1; out=$(bash "$S" --load 2>&1); rc=$?
chk "rilevato (rc != 0)"                        '[ $rc != 0 ]'
chk "indica che l'altra GPU cresce"             'echo "$out" | grep -q "✘ .*: l.altra GPU NON cresce"'

echo "== guasto: modello con offload su CPU"; setup; export FAKE_PROC="48%/52% CPU/GPU"; out=$(bash "$S" --load 2>&1); rc=$?
chk "rilevato (rc != 0)"                        '[ $rc != 0 ]'
chk "indica l'assenza di 100% GPU"              'echo "$out" | grep -q "✘ .*100% GPU"'

echo "== guasto: ComfyUI sulla GPU sbagliata"; setup; export FAKE_COMFY_NAME="NVIDIA GeForce RTX 4060 Laptop GPU"; out=$(bash "$S" 2>&1); rc=$?
chk "rilevato (rc != 0)"                        '[ $rc != 0 ]'
chk "indica ComfyUI"                            'echo "$out" | grep -q "✘ ComfyUI usa la GPU"'

echo "== eGPU scollegata (solo 4060)"; setup; export FAKE_ONLY_4060=1; out=$(bash "$S" 2>&1); rc=$?
chk "rilevato: servono due GPU"                 '[ $rc != 0 ] && echo "$out" | grep -q "✘ due GPU visibili al driver"'

echo; echo "$n controlli"; [ $ok = 1 ] && echo "SMOKE ALL OK" || echo "SMOKE FAILED"; [ $ok = 1 ]
