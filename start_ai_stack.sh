#!/bin/bash
# ORCHESTRA 8GB — AI LOCAL STACK LAUNCHER v3.7
# -------------------------------------------------
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
WEBUI_CONTAINER="ai-webui-session"
PIPELINES_CONTAINER="ai-pipelines-session"
QDRANT_CONTAINER="ai-qdrant-session"
NETWORK="ollama_default"

OLLAMA_PORT=11435
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
    "llava:7b"
    "moondream:v2"
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
# Funzioni di pulizia
# ------------------------------------------------------------
stop_containers() {
    info "Arresto dei container Docker..."
    for container in "$OLLAMA_CONTAINER" "$QDRANT_CONTAINER" "$PIPELINES_CONTAINER" "$WEBUI_CONTAINER"; do
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
header "▶  ORCHESTRA 8GB v3.7 — Avvio stack (nessuna persistenza dopo l'uscita)"

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
ensure_container "$OLLAMA_CONTAINER" \
    --network "$NETWORK" --gpus all \
    -v ollama-session:/root/.ollama \
    -p "127.0.0.1:${OLLAMA_PORT}:11434" \
    -e OLLAMA_HOST=0.0.0.0:11434 \
    -e OLLAMA_KEEP_ALIVE=0 \
    -e OLLAMA_MAX_LOADED_MODELS=1 \
    -e OLLAMA_NUM_PARALLEL=1 \
    -e OLLAMA_MAX_QUEUE=10

OLLAMA_READY=false
for i in $(seq 1 30); do
    curl -sf "http://127.0.0.1:${OLLAMA_PORT}/" >/dev/null 2>&1 && OLLAMA_READY=true && break
    sleep 2
done
if [ "$OLLAMA_READY" = true ]; then
    success "Ollama pronto"
    for model in "${REQUIRED_MODELS[@]}"; do
        model_name="${model%%:*}"
        docker exec "$OLLAMA_CONTAINER" ollama list 2>/dev/null | \
            grep -q "$model_name" || docker exec "$OLLAMA_CONTAINER" ollama pull "$model"
    done
else
    warn "Ollama non risponde — continuo"
fi

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
ensure_container "$PIPELINES_CONTAINER" \
    --gpus all \
    -v "${PIPELINES_DIR}:/app/pipelines" \
    -v "${LOGS_DIR}:/app/logs" \
    -v "${DOCS_ROOT}:/app/document-ai" \
    -v /usr/bin/nvidia-smi:/usr/bin/nvidia-smi:ro \
    -p "127.0.0.1:${PIPELINES_PORT}:9099" \
    -e PIPELINES_API_KEY="$PIPELINES_API_KEY" \
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


python main.py \
    --listen 0.0.0.0 --port "$COMFYUI_PORT" \
    --force-fp16 --dont-upcast-attention \
    --normalvram \
    --preview-method auto --cpu-vae

# Quando ComfyUI termina, lo script arriva qui e poi esegue il trap cleanup.
# I container e il RAG service vengono fermati dalla funzione cleanup.
