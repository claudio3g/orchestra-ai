#!/bin/bash
# =====================================================================
# orchestra_smoke_test.sh — verifica post-avvio dell'assetto dual-GPU
#
# Uso:   bash orchestra_smoke_test.sh [--load]
#   (senza opzioni)  controlli passivi: ruoli, isolamento GPU nei container, servizi, modelli
#   --load           carica davvero un modello su ogni backend e verifica che la memoria
#                    cresca sulla GPU giusta e NON sull'altra (isolamento reale)
# Esce con 0 solo se tutti i controlli passano. Non modifica configurazioni.
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/orchestra_gpu_env.sh"
RAG_URL="${ORCHESTRA_RAG_URL:-http://127.0.0.1:6335}"
MAIN_URL="${ORCHESTRA_OLLAMA_URL:-http://127.0.0.1:11435}"
AUX_URL="${ORCHESTRA_OLLAMA_AUX_URL:-http://127.0.0.1:11436}"
COMFY_URL="${ORCHESTRA_COMFY_URL:-http://127.0.0.1:8188}"
MAIN_C="${OLLAMA_CONTAINER:-ai-ollama-session}"; AUX_C="${OLLAMA_AUX_CONTAINER:-ai-ollama-aux-session}"
LOAD=0; [ "${1:-}" = "--load" ] && LOAD=1
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✔ $*"; }
bad() { FAIL=$((FAIL+1)); echo "  ✘ $*"; }
chk() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
jget() { python3 -c "import sys,json
d=json.load(sys.stdin)
try: print(eval(sys.argv[1]))
except Exception: print('')" "$1" 2>/dev/null; }
mem_used() { nvidia-smi -i "$1" --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' '; }

command -v nvidia-smi >/dev/null || { echo "nvidia-smi non trovato"; exit 1; }
detect_gpu_roles >/dev/null 2>&1
MAIN_U="${ORCHESTRA_GPU_MAIN:-}"; AUX_U="${ORCHESTRA_GPU_AUX:-}"

echo "1. Ruoli GPU e /vram"
chk "due GPU visibili al driver" '[ "$(nvidia-smi -L | grep -c "^GPU")" -ge 2 ]'
chk "ruoli main e aux assegnati" '[ -n "$MAIN_U" ] && [ -n "$AUX_U" ]'
VRAM="$(curl -sf "$RAG_URL/vram" 2>/dev/null)"
chk "/vram risponde con source=nvidia-smi" '[ "$(echo "$VRAM" | jget "d[\"source\"]")" = "nvidia-smi" ]'
ROLES="$(echo "$VRAM" | jget "' '.join(sorted(g['role'] for g in d['gpus']))")"
chk "/vram: l'array gpus contiene i ruoli aux e main (${ROLES})" 'echo "$ROLES" | grep -q aux && echo "$ROLES" | grep -q main'
MAIN_TOT="$(gpu_total_mb "$MAIN_U")"; AUX_TOT="$(gpu_total_mb "$AUX_U")"
chk "main ha piu' VRAM dell'aux (${MAIN_TOT:-?} > ${AUX_TOT:-?} MiB)" '[ "${MAIN_TOT:-0}" -gt "${AUX_TOT:-0}" ]'
chk "/vram: campi legacy riferiti alla main" '[ "$(echo "$VRAM" | jget "d[\"vram_total_mb\"]")" = "$MAIN_TOT" ]'

echo "2. Isolamento GPU nei container"
chk "Ollama main vede UNA sola GPU" '[ "$(container_gpu_count "$MAIN_C")" = "1" ]'
chk "Ollama main vede la GPU main" 'docker exec "$MAIN_C" nvidia-smi -L 2>/dev/null | grep -q "$MAIN_U"'
if docker ps -a --format "{{.Names}}" | grep -q "^${AUX_C}$"; then
    chk "Ollama aux vede UNA sola GPU" '[ "$(container_gpu_count "$AUX_C")" = "1" ]'
    chk "Ollama aux vede la GPU aux" 'docker exec "$AUX_C" nvidia-smi -L 2>/dev/null | grep -q "$AUX_U"'
else
    echo "  - Ollama aux non presente (ORCHESTRA_AUX_OLLAMA=0 o una sola GPU): salto"
fi

echo "3. Servizi e modelli"
chk "Ollama main risponde" 'curl -sf "$MAIN_URL/" >/dev/null'
chk "RAG service in salute" 'curl -sf "$RAG_URL/health" >/dev/null'
if docker ps -a --format "{{.Names}}" | grep -q "^${AUX_C}$"; then
    chk "Ollama aux risponde" 'curl -sf "$AUX_URL/" >/dev/null'
    chk "aux ha il coordinator (llama3.2:3b)" 'ollama_has_model "$AUX_C" llama3.2:3b'
    chk "main ha il coordinator come riserva (failover)" 'ollama_has_model "$MAIN_C" llama3.2:3b'
fi
chk "main ha il modello quality" 'ollama_has_model "$MAIN_C" qwen2.5-coder:14b-instruct-q4_K_M'
if [ "${MAIN_TOT:-0}" -ge 20000 ]; then chk "main (>=20 GB) ha il 32b" 'ollama_has_model "$MAIN_C" qwen2.5-coder:32b'; fi
COMFY="$(curl -sf "$COMFY_URL/system_stats" 2>/dev/null)"
if [ -n "$COMFY" ]; then
    WANT="$(nvidia-smi -i "$(gpu_for_role "${ORCHESTRA_COMFY_ROLE:-main}")" --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 | sed 's/^ *//;s/ *$//')"
    chk "ComfyUI usa la GPU del ruolo '${ORCHESTRA_COMFY_ROLE:-main}' (${WANT})" 'echo "$COMFY" | grep -q "$WANT"'
else
    echo "  - ComfyUI non in esecuzione: salto"
fi

if [ "$LOAD" = 1 ]; then
    echo "4. Carico reale e isolamento della memoria (--load)"
    load_check() { # <url> <modello> <uuid atteso> <uuid altra> <etichetta> <container>
        local url="$1" model="$2" want="$3" other="$4" label="$5" cont="$6" a0 b0 a1 b1
        a0=$(mem_used "$want"); b0=$(mem_used "$other")
        curl -sf "$url/api/generate" -d "{\"model\":\"$model\",\"prompt\":\"ok\",\"stream\":false,\"keep_alive\":60,\"options\":{\"num_predict\":4}}" >/dev/null
        a1=$(mem_used "$want"); b1=$(mem_used "$other")
        chk "$label: la memoria cresce sulla GPU attesa (${a0:-?} → ${a1:-?} MiB)" '[ "${a1:-0}" -gt "${a0:-0}" ]'
        chk "$label: l'altra GPU NON cresce (${b0:-?} → ${b1:-?} MiB)" '[ "${b1:-0}" -le $(( ${b0:-0} + 300 )) ]'
        chk "$label: 100% GPU, nessun offload su CPU" 'docker exec "$cont" ollama ps 2>/dev/null | grep "$model" | grep -q "100% GPU"'
        curl -sf "$url/api/generate" -d "{\"model\":\"$model\",\"keep_alive\":0}" >/dev/null
    }
    docker ps -a --format "{{.Names}}" | grep -q "^${AUX_C}$" && load_check "$AUX_URL" llama3.2:3b "$AUX_U" "$MAIN_U" "coordinator su aux" "$AUX_C"
    load_check "$MAIN_URL" qwen2.5-coder:14b-instruct-q4_K_M "$MAIN_U" "$AUX_U" "quality su main" "$MAIN_C"
fi

echo; echo "Risultato: ${PASS} ok, ${FAIL} falliti"; [ "$FAIL" = 0 ] && echo "SMOKE TEST OK" || echo "SMOKE TEST FALLITO"; [ "$FAIL" = 0 ]
