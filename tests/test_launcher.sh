#!/bin/bash
# Test del launcher start_ai_stack.sh in un ambiente FINTO: docker con stato, nvidia-smi a due
# GPU (4060 + 3090), curl/python/sleep simulati. NON tocca Docker, GPU o rete reali.
# Verifica i comandi che il launcher genererebbe nei vari scenari.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
STUBS="$HERE/helpers/stubs"; ok=1; n=0
pass() { n=$((n+1)); echo "PASS $*"; }
fail() { n=$((n+1)); echo "FAIL $*"; ok=0; }
chk()  { if eval "$2"; then pass "$1"; else fail "$1   [$2]"; fi; }

# prepara una HOME finta con una copia del repo (SCRIPT_DIR e $HOME/ai-sessioni coincidono)
setup() { # $1=nome scenario
  T="$(mktemp -d)"; export HOME="$T/home"; export STUB_STATE="$T/state"; export STUB_BIN="$STUBS"
  mkdir -p "$HOME/ai-sessioni/ComfyUI/venv/bin" "$STUB_STATE"
  ( cd "$REPO" && tar --exclude=.git --exclude=tests -cf - . ) | tar -xf - -C "$HOME/ai-sessioni"
  : > "$HOME/ai-sessioni/ComfyUI/venv/bin/activate"; : > "$HOME/ai-sessioni/ComfyUI/main.py"
  export PATH="$STUBS:$ORIG_PATH"
  unset ORCHESTRA_GPU_MAIN ORCHESTRA_GPU_AUX ORCHESTRA_AUX_OLLAMA ORCHESTRA_COMFY_ROLE COMFY_EXTRA_ARGS \
        FAKE_ONLY_4060 STUB_CURL_FAIL OLLAMA_AUX_URL PIPELINES_API_KEY
}
run() { ( cd "$HOME/ai-sessioni" && timeout 60 bash ./start_ai_stack.sh ) > "$STUB_STATE/out.log" 2>&1; echo "exit=$?" >> "$STUB_STATE/out.log"; }
args()  { cat "$STUB_STATE/containers/$1.args" 2>/dev/null | tr '\n' ' '; }
has()   { [ -f "$STUB_STATE/containers/$1.args" ]; }
ORIG_PATH="$PATH"

echo "== S1 avvio pulito, 2 GPU"; setup; run
chk "launcher termina senza errori"            'grep -q "exit=0" "$STUB_STATE/out.log"'
chk "Ollama main fissato alla 3090"            'args ai-ollama-session | grep -q -- "--gpus device=GPU-3090-UUID"'
chk "Ollama aux creato"                        'has ai-ollama-aux-session'
chk "Ollama aux fissato alla 4060"             'args ai-ollama-aux-session | grep -q -- "--gpus device=GPU-4060-UUID"'
chk "Ollama aux: porta 11436 solo locale"      'args ai-ollama-aux-session | grep -q -- "-p 127.0.0.1:11436:11434"'
chk "Ollama aux: volume dedicato"              'args ai-ollama-aux-session | grep -q -- "-v ollama-aux-session:/root/.ollama"'
chk "Ollama aux: 2 modelli caricabili"         'args ai-ollama-aux-session | grep -q "OLLAMA_MAX_LOADED_MODELS=2"'
chk "modelli aux scaricati (3)"                '[ "$(sort -u "$STUB_STATE/models/ai-ollama-aux-session" | wc -l)" = 3 ]'
chk "modelli main scaricati (7, incluso il 32b)" '[ "$(sort -u "$STUB_STATE/models/ai-ollama-session" | wc -l)" = 7 ]'
chk "il 32b e' stato scaricato (bug del grep sul solo nome)" 'grep -qx "qwen2.5-coder:32b" "$STUB_STATE/models/ai-ollama-session"'
chk "Ollama main: 2 modelli caricabili (3090 da 24 GB)" 'args ai-ollama-session | grep -q "OLLAMA_MAX_LOADED_MODELS=2"'
chk "Ollama main: flash attention + KV q8_0"   'args ai-ollama-session | grep -q "OLLAMA_FLASH_ATTENTION=1" && args ai-ollama-session | grep -q "OLLAMA_KV_CACHE_TYPE=q8_0"'
chk "Ollama aux: flash attention + KV q8_0"    'args ai-ollama-aux-session | grep -q "OLLAMA_FLASH_ATTENTION=1" && args ai-ollama-aux-session | grep -q "OLLAMA_KV_CACHE_TYPE=q8_0"'
chk "smoke: main vede 1 GPU"                   'grep -q "Ollama vede 1 sola GPU (main)" "$STUB_STATE/out.log"'
chk "smoke: aux vede 1 GPU"                    'grep -q "Ollama aux vede 1 sola GPU (aux)" "$STUB_STATE/out.log"'
chk "Pipelines riceve OLLAMA_AUX_URL"          'args ai-pipelines-session | grep -q "OLLAMA_AUX_URL=http://ai-ollama-aux-session:11434"'
chk "Pipelines riceve i ruoli GPU"             'args ai-pipelines-session | grep -q "ORCHESTRA_GPU_MAIN=GPU-3090-UUID" && args ai-pipelines-session | grep -q "ORCHESTRA_GPU_AUX=GPU-4060-UUID"'
chk "Pipelines riceve ORCHESTRA_COMFY_ROLE=main" 'args ai-pipelines-session | grep -q "ORCHESTRA_COMFY_ROLE=main"'
chk "rag_service eredita i ruoli"              'grep "rag_service.py" "$STUB_STATE/python.log" | grep -q "ORCHESTRA_GPU_MAIN=GPU-3090-UUID ORCHESTRA_GPU_AUX=GPU-4060-UUID"'
chk "ComfyUI sulla 3090 (UUID)"                'grep "main.py" "$STUB_STATE/python.log" | grep -q "CUDA_VISIBLE_DEVICES=GPU-3090-UUID CUDA_DEVICE_ORDER=PCI_BUS_ID"'
chk "ComfyUI: VAE su GPU (no --cpu-vae)"       '! grep "main.py" "$STUB_STATE/python.log" | grep -q -- "--cpu-vae"'
chk "ComfyUI: --normalvram"                    'grep "main.py" "$STUB_STATE/python.log" | grep -q -- "--normalvram"'
chk "nessun comando su volumi"                 '! grep -q "docker volume" "$STUB_STATE/calls.log"'

echo "== S2 container preesistenti creati col vecchio launcher (--gpus all, senza env)"; setup
mkdir -p "$STUB_STATE/containers"
printf '%s\n' --network ollama_default --gpus all -v ollama-session:/root/.ollama > "$STUB_STATE/containers/ai-ollama-session.args"
printf '%s\n' --network ollama_default --gpus all -e PIPELINES_API_KEY=x > "$STUB_STATE/containers/ai-pipelines-session.args"
run
chk "Ollama ricreato e fissato alla 3090"      'grep -q "docker rm -f ai-ollama-session" "$STUB_STATE/calls.log" && args ai-ollama-session | grep -q -- "--gpus device=GPU-3090-UUID"'
chk "Pipelines ricreato (env cambiate)"        'grep -q "docker rm -f ai-pipelines-session" "$STUB_STATE/calls.log" && args ai-pipelines-session | grep -q "OLLAMA_AUX_URL="'
chk "volumi mai rimossi"                       '! grep -q "docker volume" "$STUB_STATE/calls.log"'
chk "Qdrant/WebUI non rimossi"                 '! grep -q "docker rm -f ai-qdrant-session\|docker rm -f ai-webui-session" "$STUB_STATE/calls.log"'

echo "== S3 secondo avvio, stato gia' corretto: nessuna ricreazione"; : > "$STUB_STATE/calls.log"; run
chk "nessun docker rm -f"                      '! grep -q "docker rm -f" "$STUB_STATE/calls.log"'
chk "launcher termina senza errori"            'grep -q "exit=0" "$STUB_STATE/out.log"'

echo "== S4 eGPU scollegata (solo 4060)"; setup; export FAKE_ONLY_4060=1; run
chk "Ollama main sulla 4060 (degrado)"         'args ai-ollama-session | grep -q -- "--gpus device=GPU-4060-UUID"'
chk "GPU piccola: 1 solo modello caricabile"    'args ai-ollama-session | grep -q "OLLAMA_MAX_LOADED_MODELS=1"'
chk "GPU piccola: il 32b viene saltato"         'grep -q "Salto qwen2.5-coder:32b" "$STUB_STATE/out.log" && ! grep -qx "qwen2.5-coder:32b" "$STUB_STATE/models/ai-ollama-session"'
chk "GPU piccola: scaricati 6 modelli"          '[ "$(sort -u "$STUB_STATE/models/ai-ollama-session" | wc -l)" = 6 ]'
chk "nessun Ollama aux"                        '! has ai-ollama-aux-session'
chk "OLLAMA_AUX_URL vuoto in Pipelines"        'args ai-pipelines-session | grep -q "OLLAMA_AUX_URL= \|OLLAMA_AUX_URL=$"'
chk "ComfyUI con flag storici (--cpu-vae)"     'grep "main.py" "$STUB_STATE/python.log" | grep -q -- "--cpu-vae"'
chk "launcher termina senza errori"            'grep -q "exit=0" "$STUB_STATE/out.log"'

echo "== S5 ORCHESTRA_AUX_OLLAMA=0 (4060 solo per ComfyUI)"; setup; export ORCHESTRA_AUX_OLLAMA=0 ORCHESTRA_COMFY_ROLE=aux; run
chk "nessun Ollama aux"                        '! has ai-ollama-aux-session'
chk "ComfyUI sulla 4060"                       'grep "main.py" "$STUB_STATE/python.log" | grep -q "CUDA_VISIBLE_DEVICES=GPU-4060-UUID"'
chk "ComfyUI sulla 4060: --normalvram --cpu-vae" 'grep "main.py" "$STUB_STATE/python.log" | grep -q -- "--normalvram" && grep "main.py" "$STUB_STATE/python.log" | grep -q -- "--cpu-vae"'
chk "Pipelines: ORCHESTRA_COMFY_ROLE=aux"      'args ai-pipelines-session | grep -q "ORCHESTRA_COMFY_ROLE=aux"'

echo "== S6 Ollama aux non risponde: il manifold userebbe solo il main"; setup; export STUB_CURL_FAIL=':11436'; run
chk "avviso emesso"                            'grep -q "Ollama aux non risponde" "$STUB_STATE/out.log"'
chk "OLLAMA_AUX_URL vuoto in Pipelines"        'args ai-pipelines-session | grep -q "OLLAMA_AUX_URL= \|OLLAMA_AUX_URL=$"'
chk "launcher termina senza errori"            'grep -q "exit=0" "$STUB_STATE/out.log"'

echo "== S7 UUID obsoleto in ambiente (eGPU ricollegata): ricalcolo"; setup; export ORCHESTRA_GPU_MAIN="GPU-VECCHIO"; run
chk "avviso UUID non presente"                 'grep -q "non presente" "$STUB_STATE/out.log"'
chk "Ollama main sulla 3090 comunque"          'args ai-ollama-session | grep -q -- "--gpus device=GPU-3090-UUID"'

echo "== S8 libreria GPU assente: comportamento storico"; setup; rm "$HOME/ai-sessioni/document-ai/scripts/orchestra_gpu_env.sh"; run
chk "avviso libreria mancante"                 'grep -q "Libreria GPU non trovata" "$STUB_STATE/out.log"'
chk "Ollama con --gpus all"                    'args ai-ollama-session | grep -q -- "--gpus all"'
chk "nessun Ollama aux"                        '! has ai-ollama-aux-session'
chk "ComfyUI con flag storici"                 'grep "main.py" "$STUB_STATE/python.log" | grep -q -- "--normalvram" && grep "main.py" "$STUB_STATE/python.log" | grep -q -- "--cpu-vae"'
chk "launcher termina senza errori"            'grep -q "exit=0" "$STUB_STATE/out.log"'

echo "== S9 ORCHESTRA_FLASH_ATTENTION=0: KV torna f16"; setup; export ORCHESTRA_FLASH_ATTENTION=0; run
chk "flash attention disattivata"              'args ai-ollama-session | grep -q "OLLAMA_FLASH_ATTENTION=0" && args ai-ollama-aux-session | grep -q "OLLAMA_FLASH_ATTENTION=0"'
chk "KV f16"                                   'args ai-ollama-session | grep -q "OLLAMA_KV_CACHE_TYPE=f16"'
unset ORCHESTRA_FLASH_ATTENTION

echo "== S10 Ollama gia' fissato alla 3090 ma con variabili vecchie: ricreato per le env"; setup
mkdir -p "$STUB_STATE/containers"
printf '%s\n' --network ollama_default --gpus device=GPU-3090-UUID -v ollama-session:/root/.ollama -e OLLAMA_MAX_LOADED_MODELS=1 > "$STUB_STATE/containers/ai-ollama-session.args"
run
chk "ricreato per env cambiate (non per la GPU)" 'grep -q "docker rm -f ai-ollama-session" "$STUB_STATE/calls.log" && grep -q "OLLAMA_FLASH_ATTENTION cambiato\|OLLAMA_MAX_LOADED_MODELS cambiato" "$STUB_STATE/out.log"'
chk "nuove variabili presenti"                  'args ai-ollama-session | grep -q "OLLAMA_KV_CACHE_TYPE=q8_0"'
chk "volume mai rimosso"                        '! grep -q "docker volume" "$STUB_STATE/calls.log"'

echo "== S11 profilo di potenza opzionale"; setup
run; chk "senza variabile: nessun power limit toccato" '[ ! -f "$STUB_STATE/pl.log" ]'
setup; export PATH="$HERE/helpers/stubs_power:$PATH" ORCHESTRA_POWER_PROFILE=eco; run
chk "con eco: limite 3090 impostato a 245 W"  'grep -qx 245 "$STUB_STATE/pl.log"'
chk "avviso/info profilo emesso"              'grep -q "Profilo di potenza: eco" "$STUB_STATE/out.log"'
chk "launcher termina senza errori"           'grep -q "exit=0" "$STUB_STATE/out.log"'
unset ORCHESTRA_POWER_PROFILE
setup; export ORCHESTRA_POWER_PROFILE=turbo; run
chk "profilo non valido: avviso, launcher prosegue" 'grep -q "Profilo di potenza non applicato" "$STUB_STATE/out.log" && grep -q "exit=0" "$STUB_STATE/out.log"'
unset ORCHESTRA_POWER_PROFILE

echo "== S12 parallelismo e modello pesante configurabili (ARCH-01)"; setup
run; chk "default: NUM_PARALLEL=1 su main e aux (come prima)" 'args ai-ollama-session | grep -q "OLLAMA_NUM_PARALLEL=1" && args ai-ollama-aux-session | grep -q "OLLAMA_NUM_PARALLEL=1"'
setup; export ORCHESTRA_MAIN_PARALLEL=3 ORCHESTRA_AUX_PARALLEL=2; run
chk "main: NUM_PARALLEL=3"                      'args ai-ollama-session | grep -q "OLLAMA_NUM_PARALLEL=3"'
chk "aux: NUM_PARALLEL=2"                       'args ai-ollama-aux-session | grep -q "OLLAMA_NUM_PARALLEL=2"'
chk "info sul parallelismo nel log"             'grep -q "richieste parallele=3" "$STUB_STATE/out.log"'
export ORCHESTRA_MAIN_PARALLEL=2; run
chk "cambio parallelismo: Ollama main ricreato" 'grep -q "docker rm -f ai-ollama-session" "$STUB_STATE/calls.log" && args ai-ollama-session | grep -q "OLLAMA_NUM_PARALLEL=2"'
chk "volumi mai rimossi"                        '! grep -q "docker volume" "$STUB_STATE/calls.log"'
unset ORCHESTRA_MAIN_PARALLEL ORCHESTRA_AUX_PARALLEL
setup; export ORCHESTRA_HEAVY_MODEL=qwen3.6:27b; run
chk "modello pesante scelto scaricato"          'grep -qx "qwen3.6:27b" "$STUB_STATE/models/ai-ollama-session"'
chk "il 32b resta (nessuna rimozione)"          'grep -qx "qwen2.5-coder:32b" "$STUB_STATE/models/ai-ollama-session"'
setup; export ORCHESTRA_HEAVY_MODEL=qwen3.6:27b FAKE_ONLY_4060=1; run
chk "GPU piccola: modello pesante saltato"      'grep -q "Salto qwen3.6:27b" "$STUB_STATE/out.log" && ! grep -qx "qwen3.6:27b" "$STUB_STATE/models/ai-ollama-session"'
unset ORCHESTRA_HEAVY_MODEL FAKE_ONLY_4060
setup; export ORCHESTRA_HEAVY_MODEL=qwen3.6:99b-sbagliato FAKE_PULL_FAIL=qwen3.6:99b-sbagliato; run
chk "tag errato del modello pesante: avviso, stack prosegue" 'grep -q "Download di qwen3.6:99b-sbagliato fallito" "$STUB_STATE/out.log" && grep -q "exit=0" "$STUB_STATE/out.log"'
chk "gli altri modelli sono stati scaricati comunque"       '[ "$(sort -u "$STUB_STATE/models/ai-ollama-session" | wc -l)" = 7 ]'
unset ORCHESTRA_HEAVY_MODEL FAKE_PULL_FAIL
setup; export FAKE_PULL_FAIL=llama3.1:8b; run
chk "modello OBBLIGATORIO che fallisce: lo stack si ferma (come prima)" '! grep -q "exit=0" "$STUB_STATE/out.log"'
unset FAKE_PULL_FAIL

echo "== S14 avvio con sh (dash): ricade in bash invece di dare errore di sintassi"; setup
( cd "$HOME/ai-sessioni" && timeout 60 sh ./start_ai_stack.sh ) > "$STUB_STATE/out.log" 2>&1; echo "exit=$?" >> "$STUB_STATE/out.log"
chk "sh: nessun Syntax error"                  '! grep -q "Syntax error" "$STUB_STATE/out.log"'
chk "sh: il launcher parte e termina bene"     'grep -q "exit=0" "$STUB_STATE/out.log" && args ai-ollama-session | grep -q -- "--gpus device=GPU-3090-UUID"'
setup; ( cd / && timeout 60 bash "$HOME/ai-sessioni/start_ai_stack.sh" ) > "$STUB_STATE/out.log" 2>&1; echo "exit=$?" >> "$STUB_STATE/out.log"
chk "avvio da un altra cartella (percorso assoluto)" 'grep -q "exit=0" "$STUB_STATE/out.log" && args ai-ollama-aux-session | grep -q -- "--gpus device=GPU-4060-UUID"'

echo; echo "$n controlli"; [ $ok = 1 ] && echo "LAUNCHER ALL OK" || echo "LAUNCHER FAILED"; [ $ok = 1 ]
