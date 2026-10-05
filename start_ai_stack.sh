#!/bin/bash
# Se lanciato con `sh start_ai_stack.sh` (su Ubuntu e' dash) ricade in bash: lo script usa array e
# altre estensioni di bash e con dash darebbe un errore di sintassi che sembra un file corrotto.
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
# ORCHESTRA — AI LOCAL STACK LAUNCHER v3.9
# -------------------------------------------------
# Changelog v3.9 (dual-GPU completo):
#   - EGPU-02b Secondo Ollama (ai-ollama-aux-session, porta 11436) fissato alla GPU aux
#     (4060) con volume proprio `ollama-aux-session` e solo i modelli leggeri
#     (coordinator + vision). Disattivabile con ORCHESTRA_AUX_OLLAMA=0.
#   - EGPU-04 Ollama: flash attention + cache KV q8_0 (ORCHESTRA_FLASH_ATTENTION,
#     ORCHESTRA_KV_CACHE_TYPE), 2 modelli caricabili sulla main se ha >= 20 GB, container
#     ricreati se queste variabili cambiano. Il 32b si scarica solo con >= 20 GB sulla main.
#   - FIX: il controllo "modello gia' presente" confrontava solo il nome prima dei due punti:
#     con qwen2.5-coder:14b installato il 32b non veniva mai scaricato. Ora nome:tag esatto.
#   - ARCH-01 ORCHESTRA_MAIN_PARALLEL / ORCHESTRA_AUX_PARALLEL (OLLAMA_NUM_PARALLEL, default 1) e
#     ORCHESTRA_HEAVY_MODEL (modello pesante a scelta, download non fatale).
#   - EGPU-06 ORCHESTRA_POWER_PROFILE=eco|balanced|performance (opzionale) applica i power
#     limit via document-ai/scripts/orchestra_power.sh; senza variabile nulla cambia.
#   - Logica GPU spostata in document-ai/scripts/orchestra_gpu_env.sh (condivisa con
#     start_comfyui.sh e orchestra_smoke_test.sh). Se manca, si torna al comportamento
#     storico (--gpus all).
#   - Il container Pipelines viene ricreato se cambiano ORCHESTRA_GPU_MAIN/AUX,
#     OLLAMA_AUX_URL o ORCHESTRA_COMFY_ROLE (le variabili d'ambiente sono fissate
#     alla creazione). Le dipendenze pip vengono reinstallate all'avvio.
#   - File opzionale orchestra.env nella root del repo (ignorato da git) per i
#     valori locali: vedi document-ai/config/orchestra.env.example.
#
# Changelog v3.8 (dual-GPU: RTX 3090 eGPU + RTX 4060):
#   - EGPU-02 Rileva i ruoli GPU (main = piu' VRAM = 3090, aux = 4060) e li esporta
#     in ORCHESTRA_GPU_MAIN / ORCHESTRA_GPU_AUX (UUID). Valori gia' impostati
#     nell'ambiente vengono rispettati se la GPU e' presente.
#   - Ollama fissato alla sola GPU main (--gpus device=<UUID>) e ricreato se il
#     container esistente era stato creato con --gpus all (volume modelli intatto).
#   - Smoke test integrato: verifica che Ollama veda UNA sola GPU.
#   - rag_service eredita le variabili (/vram multi-GPU); passate anche al
#     container Pipelines (fallback L2) alla prossima (ri)creazione.
#
# Changelog v3.7:
#   - Garantisce che nessun processo (container Docker o servizio RAG)
#     rimanga in esecuzione dopo l'uscita dello script.
#   - Ferma tutti i container all'avvio (pulizia da esecuzioni precedenti interrotte).
#   - Uccide i processi residui di rag_service.py all'avvio e all'uscita.
#   - I container usano `--restart no` (nessuna persistenza al boot).

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}ℹ️  $*${NC}"; }
success() { echo -e "${GREEN}✅ $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠️  $*${NC}"; }
error()   { echo -e "${RED}❌ $*${NC}"; }
header()  { echo -e "\n${BOLD}$*${NC}"; }

# ------------------------------------------------------------
# Variabili di configurazione
# ------------------------------------------------------------
OLLAMA_CONTAINER="ai-ollama-session"
OLLAMA_AUX_CONTAINER="ai-ollama-aux-session"
WEBUI_CONTAINER="ai-webui-session"
PIPELINES_CONTAINER="ai-pipelines-session"
QDRANT_CONTAINER="ai-qdrant-session"
NETWORK="ollama_default"

OLLAMA_PORT=11435
OLLAMA_AUX_PORT=11436
WEBUI_PORT=3001
PIPELINES_PORT=9099
COMFYUI_PORT=8188
QDRANT_PORT=6333
RAG_SERVICE_PORT=6335

COMFYUI_DIR="$HOME/ai-sessioni/ComfyUI"
PIPELINES_DIR="$HOME/ai-sessioni/ollama/pipelines"
RAG_DIR="$HOME/ai-sessioni/rag"
DOCS_ROOT="$HOME/ai-sessioni/document-ai"
LOGS_DIR="$HOME/ai-sessioni/logs"
EXTERNAL_DISK_MOUNT="/media/claudio/01ED820B0D193A56"

REQUIRED_MODELS=(
    "llama3.2:3b"
    "qwen3.5:9b"
    "llama3.1:8b"
    "qwen2.5-coder:14b-instruct-q4_K_M"
    "qwen2.5-coder:32b"
    "llava:7b"
    "moondream:v2"
)

# Modelli serviti dall'Ollama aux (4060): coordinator + vision, leggeri.
# Devono coincidere con il valve `aux_models` del manifold e di image_loop.
# Modelli pesanti: scaricati solo se la GPU main ha almeno HEAVY_MIN_VRAM_MB di VRAM
# (il 32B Q4 pesa ~20 GB su disco e non sta in una GPU da 8 GB).
HEAVY_MODELS=("qwen2.5-coder:32b")
HEAVY_MIN_VRAM_MB=20000
# ARCH-01: modello pesante ALTERNATIVO/AGGIUNTIVO scelto dall utente (es. qwen3.6:27b, ~17 GB,
# oggi indicato come riferimento per 24 GB). Se impostato viene scaricato con le stesse regole dei
# modelli pesanti (solo con VRAM sufficiente) e il download e NON fatale: un tag errato non
# blocca il resto dello stack. Senza la variabile il comportamento e quello di prima.
if [ -n "${ORCHESTRA_HEAVY_MODEL:-}" ]; then
    HEAVY_MODELS+=("${ORCHESTRA_HEAVY_MODEL}")
    REQUIRED_MODELS+=("${ORCHESTRA_HEAVY_MODEL}")
fi

AUX_MODELS=(
    "llama3.2:3b"
    "moondream:v2"
    "llava:7b"
)

DOMAIN_DIRS=(system comfy image 3d audio)
RAG_SERVICE_PID=""

# ------------------------------------------------------------
# Gestione token persistente (generato una volta)
# ------------------------------------------------------------
TOKEN_FILE="$HOME/ai-sessioni/.orchestra_token"

generate_token() {
    openssl rand -hex 32 | tr -d '\n'
}

if [ -f "$TOKEN_FILE" ]; then
    PIPELINES_API_KEY=$(cat "$TOKEN_FILE")
    info "Token caricato da $TOKEN_FILE"
else
    if [ -n "$PIPELINES_API_KEY" ]; then
        if [ "$PIPELINES_API_KEY" = "ai-local-secure-key" ]; then
            warn "Token di default rilevato. Lo rendo persistente in $TOKEN_FILE."
            echo "$PIPELINES_API_KEY" > "$TOKEN_FILE"
            chmod 600 "$TOKEN_FILE"
        else
            echo "$PIPELINES_API_KEY" > "$TOKEN_FILE"
            chmod 600 "$TOKEN_FILE"
            info "Token da ambiente persistito in $TOKEN_FILE"
        fi
    else
        PIPELINES_API_KEY=$(generate_token)
        echo "$PIPELINES_API_KEY" > "$TOKEN_FILE"
        chmod 600 "$TOKEN_FILE"
        success "Nuovo token generato e salvato in $TOKEN_FILE"
        warn "⚠️  IMPORTANTE: configura manualmente questa chiave in OpenWebUI:"
        info "   1. Vai su Admin Panel → Settings → Connections"
        info "   2. Modifica la connessione esistente con:"
        info "      - URL: http://ai-pipelines-session:9099"
        info "      - Autenticazione: Bearer"
        info "      - API Key: $PIPELINES_API_KEY"
        info "      - Provider Type: OpenAI"
        info "      - API Type: Chat Completions"
        info "   3. Salva e ricarica le pipeline."
    fi
fi

export PIPELINES_API_KEY

# ------------------------------------------------------------
# EGPU-02/02b — Ruoli GPU main (3090) / aux (4060): libreria condivisa
# ------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Valori locali opzionali (UUID GPU, ORCHESTRA_AUX_OLLAMA, ORCHESTRA_COMFY_ROLE...).
# shellcheck disable=SC1091
[ -f "$SCRIPT_DIR/orchestra.env" ] && . "$SCRIPT_DIR/orchestra.env"
GPU_LIB="$SCRIPT_DIR/document-ai/scripts/orchestra_gpu_env.sh"
if [ -f "$GPU_LIB" ]; then
    # shellcheck disable=SC1090
    . "$GPU_LIB"
else
    warn "Libreria GPU non trovata (${GPU_LIB}): comportamento storico (--gpus all, nessun Ollama aux)"
    detect_gpu_roles()        { :; }
    recreate_if_not_pinned()  { :; }
    recreate_if_env_stale()   { :; }
    aux_ollama_enabled()      { return 1; }
    container_gpu_count()     { echo 0; }
    gpu_for_role()            { :; }
    gpu_total_mb()            { :; }
    comfyui_gpu_setup()       { COMFY_MEM_ARGS=(--normalvram --cpu-vae); }
    ollama_has_model()        { docker exec "$1" ollama list 2>/dev/null | grep -q "${2%%:*}"; }
fi
detect_gpu_roles

# EGPU-06: profilo di potenza OPZIONALE (eco | balanced | performance). Senza la variabile non
# cambia nulla. Imposta i power limit delle GPU (richiede sudo senza password; i limiti tornano
# al predefinito al riavvio o con `orchestra_power.sh restore`). Misura prima con
# `orchestra_power.sh bench eco balanced performance`.
if [ -n "${ORCHESTRA_POWER_PROFILE:-}" ]; then
    POWER_SCRIPT="$SCRIPT_DIR/document-ai/scripts/orchestra_power.sh"
    if [ -f "$POWER_SCRIPT" ]; then
        info "Profilo di potenza: ${ORCHESTRA_POWER_PROFILE}"
        bash "$POWER_SCRIPT" profile "$ORCHESTRA_POWER_PROFILE" \
            || warn "Profilo di potenza non applicato (servono privilegi: sudo visudo -> NOPASSWD per nvidia-smi)"
    else
        warn "orchestra_power.sh non trovato: profilo di potenza ignorato"
    fi
fi

# ------------------------------------------------------------
# Funzioni di pulizia
# ------------------------------------------------------------
stop_containers() {
    info "Arresto dei container Docker..."
    for container in "$OLLAMA_CONTAINER" "$OLLAMA_AUX_CONTAINER" "$QDRANT_CONTAINER" "$PIPELINES_CONTAINER" "$WEBUI_CONTAINER"; do
        docker stop "$container" 2>/dev/null || true
    done
}

kill_rag_services() {
    info "Terminazione eventuali processi RAG service residui..."
    pkill -f "rag_service.py" 2>/dev/null || true
}

cleanup() {
    echo ""
    warn "Interruzione — fermando servizi..."
    kill_rag_services
    stop_containers
    exit 0
}
trap cleanup SIGINT SIGTERM

# Pulizia iniziale: assicuriamoci che non ci siano residui di esecuzioni precedenti
stop_containers
kill_rag_services

# ------------------------------------------------------------
# Funzioni di utilità
# ------------------------------------------------------------
ensure_container() {
    local name="$1"; shift
    if docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
        docker start "$name" >/dev/null 2>&1
        info "Container ${name} avviato (esistente)"
    else
        docker run -d --name "$name" --restart no "$@" >/dev/null
        success "Container ${name} creato e avviato"
    fi
    docker network inspect "$NETWORK" \
        --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null \
        | grep -q "$name" || docker network connect "$NETWORK" "$name" 2>/dev/null || true
}

mount_external_disk() {
    header "0️⃣  SSD Esterno"
    mountpoint -q "$EXTERNAL_DISK_MOUNT" && success "SSD esterno già montato" && return 0
    mount "$EXTERNAL_DISK_MOUNT" 2>/dev/null && success "SSD esterno montato" || \
        warn "SSD esterno non montato"
}

setup_zram() {
    header "0️⃣  zram swap"
    zramctl 2>/dev/null | grep -q "/dev/zram" && success "zram già attivo" && return 0
    systemctl list-unit-files 2>/dev/null | grep -q zramswap && \
        { sudo systemctl start zramswap && success "zramswap avviato"; } || \
        warn "zramswap non configurato"
}

# ------------------------------------------------------------
# Avvio dello stack
# ------------------------------------------------------------
header "▶  ORCHESTRA v3.9 — Avvio stack (nessuna persistenza dopo l'uscita)"

mount_external_disk
setup_zram

header "0️⃣  Directory"
mkdir -p "$LOGS_DIR" "$PIPELINES_DIR" "$RAG_DIR"
for d in "${DOMAIN_DIRS[@]}"; do mkdir -p "$DOCS_ROOT/$d"; done
success "Struttura directory pronta"

header "0️⃣  Cleanup ComfyUI"
COMFYUI_PID=$(pgrep -f "python main.py" 2>/dev/null || true)
[ -n "$COMFYUI_PID" ] && {
    kill "$COMFYUI_PID" 2>/dev/null || true; sleep 2
    kill -9 "$COMFYUI_PID" 2>/dev/null || true
}
ss -tlnp | grep -q ":${COMFYUI_PORT}" && { error "Porta ${COMFYUI_PORT} occupata"; exit 1; }
rm -f "${COMFYUI_DIR}/user/comfyui.db-wal" "${COMFYUI_DIR}/user/comfyui.db-shm" 2>/dev/null || true

header "1️⃣  Rete Docker"
docker network ls --format '{{.Name}}' | grep -q "^${NETWORK}$" || \
    docker network create "$NETWORK"
success "Rete ${NETWORK} pronta"

header "2️⃣  Ollama (127.0.0.1:${OLLAMA_PORT})"
# EGPU-02: ensure_container RIUSA i container esistenti: ricreiamo Ollama se non e' fissato
# alla GPU main (volume modelli `ollama-session` intatto). Vedi orchestra_gpu_env.sh.
recreate_if_not_pinned "$OLLAMA_CONTAINER" "${ORCHESTRA_GPU_MAIN:-}"

# EGPU-04: ottimizzazione VRAM/cooperazione.
#  - Flash attention + cache KV in q8_0: dimezza la memoria del contesto (con qwen2.5-coder
#    32b e num_ctx 12288 la KV passa da ~3 a ~1.5 GB, stime da verificare con `ollama ps`).
#    Disattivabili con ORCHESTRA_FLASH_ATTENTION=0 (la KV torna f16).
#  - Con >= 20 GB sulla main possono restare caricati 2 modelli (es. quality + fast): niente
#    scambi continui sul link Thunderbolt. Con GPU piccole resta 1, come prima.
OLLAMA_FA="${ORCHESTRA_FLASH_ATTENTION:-1}"
if [ "$OLLAMA_FA" = "1" ]; then OLLAMA_KV="${ORCHESTRA_KV_CACHE_TYPE:-q8_0}"; else OLLAMA_KV="f16"; fi
MAIN_TOTAL_MB=""
[ -n "${ORCHESTRA_GPU_MAIN:-}" ] && MAIN_TOTAL_MB="$(gpu_total_mb "$ORCHESTRA_GPU_MAIN")"
# ARCH-01: richieste in parallelo per modello caricato (stessi pesi, una cache KV per slot).
# Default 1 (come prima). La memoria della KV cresce di num_ctx x parallel: misurare con
# document-ai/scripts/orchestra_bench_models.sh prima di alzarlo.
OLLAMA_MAIN_PAR="${ORCHESTRA_MAIN_PARALLEL:-1}"
OLLAMA_AUX_PAR="${ORCHESTRA_AUX_PARALLEL:-1}"
OLLAMA_MAIN_LOADED=1
[ -n "$MAIN_TOTAL_MB" ] && [ "$MAIN_TOTAL_MB" -ge "$HEAVY_MIN_VRAM_MB" ] && OLLAMA_MAIN_LOADED=2
info "Ollama main: modelli caricabili=${OLLAMA_MAIN_LOADED}, richieste parallele=${OLLAMA_MAIN_PAR}, flash-attention=${OLLAMA_FA}, KV=${OLLAMA_KV}"
# Le variabili d'ambiente sono fissate alla creazione: ricrea se sono cambiate.
recreate_if_env_stale "$OLLAMA_CONTAINER" \
    "OLLAMA_MAX_LOADED_MODELS=${OLLAMA_MAIN_LOADED}" "OLLAMA_NUM_PARALLEL=${OLLAMA_MAIN_PAR}" \
    "OLLAMA_FLASH_ATTENTION=${OLLAMA_FA}" "OLLAMA_KV_CACHE_TYPE=${OLLAMA_KV}"
OLLAMA_GPU_ARG=(--gpus all)   # fallback storico se nvidia-smi non e' disponibile
[ -n "${ORCHESTRA_GPU_MAIN:-}" ] && OLLAMA_GPU_ARG=(--gpus "device=${ORCHESTRA_GPU_MAIN}")
ensure_container "$OLLAMA_CONTAINER" \
    --network "$NETWORK" "${OLLAMA_GPU_ARG[@]}" \
    -v ollama-session:/root/.ollama \
    -p "127.0.0.1:${OLLAMA_PORT}:11434" \
    -e OLLAMA_HOST=0.0.0.0:11434 \
    -e OLLAMA_KEEP_ALIVE=0 \
    -e OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAIN_LOADED}" \
    -e OLLAMA_FLASH_ATTENTION="${OLLAMA_FA}" \
    -e OLLAMA_KV_CACHE_TYPE="${OLLAMA_KV}" \
    -e OLLAMA_NUM_PARALLEL="${OLLAMA_MAIN_PAR}" \
    -e OLLAMA_MAX_QUEUE=10

OLLAMA_READY=false
for i in $(seq 1 30); do
    curl -sf "http://127.0.0.1:${OLLAMA_PORT}/" >/dev/null 2>&1 && OLLAMA_READY=true && break
    sleep 2
done
if [ "$OLLAMA_READY" = true ]; then
    success "Ollama pronto"
    # EGPU-02 smoke test: Ollama deve vedere UNA sola GPU (la main).
    if [ -n "${ORCHESTRA_GPU_MAIN:-}" ]; then
        OLLAMA_GPUS=$(container_gpu_count "$OLLAMA_CONTAINER")
        if [ "$OLLAMA_GPUS" = "1" ]; then
            success "Ollama vede 1 sola GPU (main)"
        else
            warn "Ollama vede ${OLLAMA_GPUS} GPU (atteso 1): controlla --gpus / ORCHESTRA_GPU_MAIN"
        fi
    fi
    for model in "${REQUIRED_MODELS[@]}"; do
        # Modelli pesanti: solo se la GPU main li regge (VRAM nota e sufficiente, o non rilevabile).
        if [[ " ${HEAVY_MODELS[*]} " == *" ${model} "* ]] \
                && [ -n "$MAIN_TOTAL_MB" ] && [ "$MAIN_TOTAL_MB" -lt "$HEAVY_MIN_VRAM_MB" ]; then
            info "Salto ${model}: GPU main ${MAIN_TOTAL_MB} MiB < ${HEAVY_MIN_VRAM_MB} MiB"
            continue
        fi
        # EGPU-04: confronto esatto nome:tag (il vecchio grep sul solo nome saltava il 32b).
        if [[ " ${HEAVY_MODELS[*]} " == *" ${model} "* ]]; then
            # modelli pesanti: un tag errato o un download interrotto non deve fermare lo stack
            ollama_has_model "$OLLAMA_CONTAINER" "$model" \
                || docker exec "$OLLAMA_CONTAINER" ollama pull "$model" \
                || warn "Download di ${model} fallito (modello pesante): proseguo senza"
        else
            ollama_has_model "$OLLAMA_CONTAINER" "$model" \
                || docker exec "$OLLAMA_CONTAINER" ollama pull "$model"
        fi
    done
else
    warn "Ollama non risponde — continuo"
fi

header "2️⃣b Ollama AUX (127.0.0.1:${OLLAMA_AUX_PORT}) — GPU aux"
# EGPU-02b: seconda istanza Ollama, fissata alla GPU aux. Ospita i modelli leggeri
# (coordinator + vision) cosi' gli agenti pesanti sulla main non li fanno aspettare.
# Volume proprio: nessuna condivisione di file con l'Ollama main. Il main conserva
# comunque TUTTI i modelli: se l'aux non risponde il manifold ricade sul main.
OLLAMA_AUX_URL=""
if aux_ollama_enabled; then
    recreate_if_not_pinned "$OLLAMA_AUX_CONTAINER" "$ORCHESTRA_GPU_AUX"
    recreate_if_env_stale "$OLLAMA_AUX_CONTAINER" \
        "OLLAMA_NUM_PARALLEL=${OLLAMA_AUX_PAR}" \
        "OLLAMA_FLASH_ATTENTION=${OLLAMA_FA}" "OLLAMA_KV_CACHE_TYPE=${OLLAMA_KV}"
    ensure_container "$OLLAMA_AUX_CONTAINER" \
        --network "$NETWORK" --gpus "device=${ORCHESTRA_GPU_AUX}" \
        -v ollama-aux-session:/root/.ollama \
        -p "127.0.0.1:${OLLAMA_AUX_PORT}:11434" \
        -e OLLAMA_HOST=0.0.0.0:11434 \
        -e OLLAMA_KEEP_ALIVE=10m \
        -e OLLAMA_MAX_LOADED_MODELS=2 \
        -e OLLAMA_FLASH_ATTENTION="${OLLAMA_FA}" \
        -e OLLAMA_KV_CACHE_TYPE="${OLLAMA_KV}" \
        -e OLLAMA_NUM_PARALLEL="${OLLAMA_AUX_PAR}" \
        -e OLLAMA_MAX_QUEUE=10

    OLLAMA_AUX_READY=false
    for i in $(seq 1 30); do
        curl -sf "http://127.0.0.1:${OLLAMA_AUX_PORT}/" >/dev/null 2>&1 && OLLAMA_AUX_READY=true && break
        sleep 2
    done
    if [ "$OLLAMA_AUX_READY" = true ]; then
        success "Ollama aux pronto"
        OLLAMA_AUX_GPUS=$(container_gpu_count "$OLLAMA_AUX_CONTAINER")
        if [ "$OLLAMA_AUX_GPUS" = "1" ]; then
            success "Ollama aux vede 1 sola GPU (aux)"
        else
            warn "Ollama aux vede ${OLLAMA_AUX_GPUS} GPU (atteso 1): controlla --gpus / ORCHESTRA_GPU_AUX"
        fi
        for model in "${AUX_MODELS[@]}"; do
            ollama_has_model "$OLLAMA_AUX_CONTAINER" "$model" \
                || docker exec "$OLLAMA_AUX_CONTAINER" ollama pull "$model" \
                || warn "Download di ${model} sull'aux fallito (il manifold usera' il main)"
        done
        OLLAMA_AUX_URL="http://${OLLAMA_AUX_CONTAINER}:11434"
    else
        warn "Ollama aux non risponde — il manifold usera' solo il main"
    fi
else
    info "Ollama aux non attivo (nessuna GPU aux oppure ORCHESTRA_AUX_OLLAMA=0)"
fi
# Variabili lette dal container Pipelines (manifold e image_loop).
export OLLAMA_AUX_URL
export ORCHESTRA_COMFY_ROLE="${ORCHESTRA_COMFY_ROLE:-main}"

header "3️⃣  Qdrant (127.0.0.1:${QDRANT_PORT})"
ensure_container "$QDRANT_CONTAINER" \
    --network "$NETWORK" \
    -v qdrant-data:/qdrant/storage \
    -p "127.0.0.1:${QDRANT_PORT}:6333"

QDRANT_READY=false
for i in $(seq 1 15); do
    curl -sf "http://127.0.0.1:${QDRANT_PORT}/healthz" >/dev/null 2>&1 && \
        QDRANT_READY=true && break
    sleep 2
done
[ "$QDRANT_READY" = true ] && success "Qdrant pronto" || warn "Qdrant non risponde"

header "4️⃣  Pipelines (127.0.0.1:${PIPELINES_PORT})"
# EGPU-02b: le variabili d'ambiente sono fissate alla creazione del container.
recreate_if_env_stale "$PIPELINES_CONTAINER" \
    ORCHESTRA_GPU_MAIN ORCHESTRA_GPU_AUX OLLAMA_AUX_URL ORCHESTRA_COMFY_ROLE
ensure_container "$PIPELINES_CONTAINER" \
    --gpus all \
    -v "${PIPELINES_DIR}:/app/pipelines" \
    -v "${HOME}/ai-sessioni:/app/ai:ro" \
    -v "${LOGS_DIR}:/app/logs" \
    -v "${DOCS_ROOT}:/app/document-ai" \
    -v /usr/bin/nvidia-smi:/usr/bin/nvidia-smi:ro \
    -p "127.0.0.1:${PIPELINES_PORT}:9099" \
    -e PIPELINES_API_KEY="$PIPELINES_API_KEY" \
    -e ORCHESTRA_GPU_MAIN="${ORCHESTRA_GPU_MAIN:-}" \
    -e ORCHESTRA_GPU_AUX="${ORCHESTRA_GPU_AUX:-}" \
    -e OLLAMA_AUX_URL="${OLLAMA_AUX_URL:-}" \
    -e ORCHESTRA_COMFY_ROLE="${ORCHESTRA_COMFY_ROLE:-main}" \
    -e PATTERN_LOG_PATH="/app/logs/patterns.jsonl" \
    -e DOCS_ROOT="/app/document-ai" \
    -e PIPELINES_REQUIREMENTS_PATH="/app/pipelines/requirements.txt"

success "Pipelines pronto"

header "5️⃣  OpenWebUI (192.168.1.51:${WEBUI_PORT})"
ensure_container "$WEBUI_CONTAINER" \
    -v webui-session:/app/backend/data \
    -p "192.168.1.51:${WEBUI_PORT}:8080" \
    -e ENABLE_PIPELINES=true \
    -e PIPELINES_API_KEY="$PIPELINES_API_KEY" \
    -e OLLAMA_BASE_URL="http://${OLLAMA_CONTAINER}:11434" \
    -e PIPELINES_URL="http://${PIPELINES_CONTAINER}:${PIPELINES_PORT}" \
    -e OPENAI_API_BASE_URLS="http://ai-pipelines-session:9099" \
    -e OPENAI_API_KEYS="$PIPELINES_API_KEY" \
    -e COMFYUI_BASE_URL="http://172.17.0.1:${COMFYUI_PORT}" \
    -e WEBUI_SECRET_KEY="$(cat ~/ai-sessioni/.webui_secret_key)" \
    ghcr.io/open-webui/open-webui:main

success "OpenWebUI pronto"

# Auto-allineamento token pipelines nel database OpenWebUI
info "Attendo avvio OpenWebUI..."
for i in $(seq 1 30); do
    curl -sf "http://192.168.1.51:3001/health" >/dev/null 2>&1 && break
    sleep 2
done
sleep 5
_TOKEN=$(cat "$TOKEN_FILE")
docker exec "$WEBUI_CONTAINER" python3 -c "
import sqlite3, json
conn = sqlite3.connect('/app/backend/data/webui.db')
cur = conn.cursor()
cur.execute('SELECT data FROM config WHERE id=1')
row = cur.fetchone()
if row:
    data = json.loads(row[0])
    openai_cfg = data.get('openai', {})
    keys = openai_cfg.get('api_keys', [])
    urls = openai_cfg.get('api_base_urls', [])
    token = '${_TOKEN}'
 
    # Costruisce il set di chiavi legittime:
    # - token corrente (posizione 0, per Pipelines)
    # - tutte le chiavi in posizione >= 1 che corrispondono a un URL (es. DeepSeek)
    # Le chiavi orfane (senza URL corrispondente) vengono rimosse.
    legitimate = []
 
    # Posizione 0: sempre il token Pipelines corrente
    legitimate.append(token)
 
    # Posizioni 1+: mantieni solo le chiavi che hanno un URL associato
    for i, url in enumerate(urls[1:], start=1):
        if i < len(keys) and keys[i] and keys[i] != token:
            legitimate.append(keys[i])
 
    if keys != legitimate:
        print(f'[ORCHESTRA] Token prima: {keys}')
        print(f'[ORCHESTRA] Token dopo:  {legitimate}')
        data['openai']['api_keys'] = legitimate
        cur.execute('UPDATE config SET data=? WHERE id=1', (json.dumps(data),))
        conn.commit()
        print('[ORCHESTRA] Token pipelines allineato nel DB OpenWebUI')
    else:
        print('[ORCHESTRA] Token già allineato — nessuna modifica')
conn.close()
" && success "Token pipelines allineato" || warn "Auto-allineamento token fallito — verifica manualmente"

header "6️⃣  RAG Service (127.0.0.1:${RAG_SERVICE_PORT})"
if [ ! -f "$RAG_DIR/rag_service.py" ]; then
    warn "rag_service.py non trovato — RAG service non avviato"
else
    if ss -tlnp | grep -q "127.0.0.1:${RAG_SERVICE_PORT}"; then
        warn "Porta ${RAG_SERVICE_PORT} già in uso"
    else
        cd "$RAG_DIR"
        source "${COMFYUI_DIR}/venv/bin/activate"
        RAG_DOCS_DIR="$DOCS_ROOT" \
        QDRANT_URL="http://127.0.0.1:${QDRANT_PORT}" \
        RAG_SERVICE_PORT="$RAG_SERVICE_PORT" \
        RAG_SERVICE_HOST="0.0.0.0" \
        DEPLOY_TOKEN="$PIPELINES_API_KEY" \
        nohup python rag_service.py > "${LOGS_DIR}/rag_service.log" 2>&1 &
        RAG_SERVICE_PID=$!
        sleep 2
        if kill -0 "$RAG_SERVICE_PID" 2>/dev/null; then
            success "RAG Service avviato (PID: ${RAG_SERVICE_PID})"
        else
            warn "RAG Service non avviato — controlla: tail -20 ${LOGS_DIR}/rag_service.log"
            RAG_SERVICE_PID=""
        fi
        cd "$HOME"
    fi
fi

# Indicizzazione automatica se Qdrant è vuoto
if [ -n "$RAG_SERVICE_PID" ]; then
    for i in $(seq 1 10); do
        curl -sf "http://127.0.0.1:${RAG_SERVICE_PORT}/health" >/dev/null 2>&1 && break
        sleep 2
    done
    if curl -sf "http://127.0.0.1:${RAG_SERVICE_PORT}/status" | grep -q '"total_chunks":0'; then
        info "RAG: nessun documento indicizzato, avvio indicizzazione in background..."
        curl -X POST "http://127.0.0.1:${RAG_SERVICE_PORT}/index" > /dev/null 2>&1 &
    fi
fi

header "7️⃣  NPM — verifica"
if curl -sf http://192.168.1.101:81 >/dev/null 2>&1; then
    success "NPM raggiungibile su rasdom1-pi4"
else
    warn "NPM non raggiungibile — verifica rasdom1-pi4:81"
fi

header "8️⃣  ComfyUI (host, foreground)"
echo ""
success "Stack completo:"
info "  OpenWebUI  → https://orchestra.tregambe.com"
info "  ComfyUI    → http://127.0.0.1:${COMFYUI_PORT}"
info "  Qdrant     → http://127.0.0.1:${QDRANT_PORT}"
info "  RAG API    → http://127.0.0.1:${RAG_SERVICE_PORT}/health"
echo ""

cd "$COMFYUI_DIR"
source venv/bin/activate
export OLLAMA_HOST="http://127.0.0.1:${OLLAMA_PORT}"

# export OLLAMA_HOST="http://172.17.0.1:${OLLAMA_PORT}"


# EGPU-05: ComfyUI sulla GPU del ruolo ORCHESTRA_COMFY_ROLE (default main = 3090).
# Sulla 3090 il VAE resta su GPU; sulle GPU piccole restano i flag storici
# (--normalvram --cpu-vae). COMFY_EXTRA_ARGS permette flag aggiuntivi senza toccare lo script.
comfyui_gpu_setup --normalvram

# shellcheck disable=SC2086
python main.py \
    --listen 0.0.0.0 --port "$COMFYUI_PORT" \
    --force-fp16 --dont-upcast-attention \
    "${COMFY_MEM_ARGS[@]}" \
    --preview-method auto ${COMFY_EXTRA_ARGS:-}

# Quando ComfyUI termina, lo script arriva qui e poi esegue il trap cleanup.
# I container e il RAG service vengono fermati dalla funzione cleanup.
