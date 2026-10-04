#!/bin/bash
# LCM-LoRA Download Script — Orchestra
# ==========================================
# Scarica lcm-lora-sdxl.safetensors da HuggingFace e verifica che
# il sampler 'lcm' sia disponibile in ComfyUI prima di procedere.
#
# FILE: ~/ai-sessioni/download_lcm_lora.sh
#
# Prerequisiti:
#   - ComfyUI in esecuzione su 127.0.0.1:8188
#   - start_ai_stack.sh già eseguito
#   - Connessione internet disponibile

set -eEo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}ℹ️  $*${NC}"; }
success() { echo -e "${GREEN}✅ $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠️  $*${NC}"; }
error()   { echo -e "${RED}❌ $*${NC}"; }
header()  { echo -e "\n${BOLD}═══ $* ═══${NC}"; }

# ─────────────────────────────────────────────────────────────────────────────
# Configurazione
# ─────────────────────────────────────────────────────────────────────────────
COMFYUI_URL="http://127.0.0.1:8188"
COMFYUI_DIR="$HOME/ai-sessioni/ComfyUI"
LORAS_DIR="${COMFYUI_DIR}/models/loras"

HF_URL="https://huggingface.co/latent-consistency/lcm-lora-sdxl/resolve/main/pytorch_lora_weights.safetensors"
LORA_FILENAME="lcm-lora-sdxl.safetensors"
LORA_PATH="${LORAS_DIR}/${LORA_FILENAME}"

# Dimensione attesa ~197MB — usiamo range per tollerare future versioni minori
LORA_MIN_MB=180
LORA_MAX_MB=220

# ─────────────────────────────────────────────────────────────────────────────
header "1/4  Verifica ComfyUI raggiungibile"
# ─────────────────────────────────────────────────────────────────────────────
if ! curl -sf "${COMFYUI_URL}/system_stats" >/dev/null 2>&1; then
    error "ComfyUI non risponde su ${COMFYUI_URL}"
    error "Avvia lo stack prima: ~/ai-sessioni/start_ai_stack.sh"
    exit 1
fi
success "ComfyUI raggiungibile"

# Leggi versione ComfyUI per log
COMFYUI_VERSION=$(curl -sf "${COMFYUI_URL}/system_stats" 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('system',{}).get('comfyui_version','unknown'))" \
    2>/dev/null || echo "unknown")
info "ComfyUI versione: ${COMFYUI_VERSION}"

# ─────────────────────────────────────────────────────────────────────────────
header "2/4  Verifica sampler 'lcm' disponibile"
# ─────────────────────────────────────────────────────────────────────────────
info "Interrogo /object_info/KSampler..."

KSAMPLER_INFO=$(curl -sf "${COMFYUI_URL}/object_info/KSampler" 2>/dev/null || echo "{}")

if echo "$KSAMPLER_INFO" | python3 -c "
import sys, json
data = json.load(sys.stdin)
samplers = data.get('KSampler', {}).get('input', {}).get('required', {}).get('sampler_name', [[]])[0]
if 'lcm' in samplers:
    print('FOUND')
    print('Samplers disponibili: ' + ', '.join(samplers[:8]) + '...')
else:
    print('NOT_FOUND')
    print('Samplers: ' + ', '.join(samplers[:8]))
" 2>/dev/null | grep -q "FOUND"; then
    success "Sampler 'lcm' confermato disponibile in ComfyUI ${COMFYUI_VERSION}"

    # Mostra anche schedulers per conferma sgm_uniform
    SCHEDULERS=$(echo "$KSAMPLER_INFO" | python3 -c "
import sys, json
data = json.load(sys.stdin)
schedulers = data.get('KSampler', {}).get('input', {}).get('required', {}).get('scheduler', [[]])[0]
print(', '.join(schedulers))
" 2>/dev/null || echo "non disponibile")
    info "Schedulers disponibili: ${SCHEDULERS}"

    if echo "$SCHEDULERS" | grep -q "sgm_uniform"; then
        success "Scheduler 'sgm_uniform' confermato"
    else
        warn "Scheduler 'sgm_uniform' non trovato — verrà usato 'sgm_uniform' o fallback"
        warn "Lista completa: ${SCHEDULERS}"
    fi
else
    error "Sampler 'lcm' NON disponibile in questa versione di ComfyUI"
    error "ComfyUI ${COMFYUI_VERSION} potrebbe non supportare LCM"
    echo ""
    info "Samplers trovati:"
    echo "$KSAMPLER_INFO" | python3 -c "
import sys, json
data = json.load(sys.stdin)
samplers = data.get('KSampler', {}).get('input', {}).get('required', {}).get('sampler_name', [[]])[0]
for s in samplers: print(f'  - {s}')
" 2>/dev/null || echo "  (parsing fallito)"
    exit 1
fi

# Verifica che LoraLoader sia disponibile
info "Verifica nodo LoraLoader..."
LORA_INFO=$(curl -sf "${COMFYUI_URL}/object_info/LoraLoader" 2>/dev/null || echo "{}")
if echo "$LORA_INFO" | python3 -c "
import sys, json
data = json.load(sys.stdin)
sys.exit(0 if 'LoraLoader' in data else 1)
" 2>/dev/null; then
    success "Nodo LoraLoader disponibile"

    # Lista loras già installate
    INSTALLED_LORAS=$(echo "$LORA_INFO" | python3 -c "
import sys, json
data = json.load(sys.stdin)
loras = data.get('LoraLoader', {}).get('input', {}).get('required', {}).get('lora_name', [[]])[0]
print(', '.join(loras) if loras else 'nessuna')
" 2>/dev/null || echo "non disponibile")
    info "LoRA già installate: ${INSTALLED_LORAS}"
else
    warn "Nodo LoraLoader non trovato — potrebbe essere necessario installare un custom node"
fi

# ─────────────────────────────────────────────────────────────────────────────
header "3/4  Download LCM-LoRA"
# ─────────────────────────────────────────────────────────────────────────────

# Crea directory loras se non esiste
mkdir -p "$LORAS_DIR"

# Controlla se già scaricato
if [ -f "$LORA_PATH" ]; then
    SIZE_MB=$(du -m "$LORA_PATH" | cut -f1)
    if [ "$SIZE_MB" -ge "$LORA_MIN_MB" ] && [ "$SIZE_MB" -le "$LORA_MAX_MB" ]; then
        success "LCM-LoRA già presente e valida (${SIZE_MB}MB): ${LORA_PATH}"
        SKIP_DOWNLOAD=true
    else
        warn "File esistente ma dimensione anomala (${SIZE_MB}MB, atteso ${LORA_MIN_MB}–${LORA_MAX_MB}MB) — riscarico"
        rm -f "$LORA_PATH"
        SKIP_DOWNLOAD=false
    fi
else
    SKIP_DOWNLOAD=false
fi

if [ "$SKIP_DOWNLOAD" = false ]; then
    info "Download da HuggingFace..."
    info "URL: ${HF_URL}"
    info "Destinazione: ${LORA_PATH}"
    info "Dimensione attesa: ~197MB — attendi circa 30–60 secondi"
    echo ""

    # Download con progress bar, retry automatico, timeout generoso
    if curl -L \
        --progress-bar \
        --retry 3 \
        --retry-delay 5 \
        --connect-timeout 30 \
        --max-time 300 \
        --output "$LORA_PATH" \
        "$HF_URL"; then

        SIZE_MB=$(du -m "$LORA_PATH" | cut -f1)

        if [ "$SIZE_MB" -lt "$LORA_MIN_MB" ]; then
            error "File scaricato troppo piccolo: ${SIZE_MB}MB (minimo ${LORA_MIN_MB}MB)"
            error "Possibile errore di rete o URL non valido"
            rm -f "$LORA_PATH"
            exit 1
        elif [ "$SIZE_MB" -gt "$LORA_MAX_MB" ]; then
            warn "File più grande del previsto: ${SIZE_MB}MB — potrebbe essere una versione aggiornata"
            success "Download completato (${SIZE_MB}MB)"
        else
            success "Download completato (${SIZE_MB}MB)"
        fi
    else
        error "Download fallito"
        rm -f "$LORA_PATH"
        exit 1
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
header "4/4  Verifica finale"
# ─────────────────────────────────────────────────────────────────────────────

# Verifica che ComfyUI veda la lora appena scaricata
info "Verifico che ComfyUI riconosca la LoRA scaricata..."
info "(ComfyUI richiede un refresh del model cache)"

# Triggera refresh models su ComfyUI
REFRESH=$(curl -sf -X POST "${COMFYUI_URL}/api/models/loras" 2>/dev/null || \
          curl -sf "${COMFYUI_URL}/object_info/LoraLoader" 2>/dev/null || echo "{}")

LORA_VISIBLE=$(echo "$REFRESH" | python3 -c "
import sys, json
raw = sys.stdin.read()
try:
    data = json.loads(raw)
    # Cerca in object_info format
    loras = data.get('LoraLoader', {}).get('input', {}).get('required', {}).get('lora_name', [[]])[0]
    print('YES' if 'lcm-lora-sdxl.safetensors' in loras else 'NEEDS_RESTART')
except:
    print('NEEDS_RESTART')
" 2>/dev/null || echo "NEEDS_RESTART")

if [ "$LORA_VISIBLE" = "YES" ]; then
    success "ComfyUI riconosce 'lcm-lora-sdxl.safetensors' — pronto all'uso"
else
    warn "ComfyUI non vede ancora la LoRA nella cache"
    info "Normale: ComfyUI aggiorna la lista al prossimo avvio oppure dopo refresh"
    info "Esegui da interfaccia ComfyUI: Settings → Refresh Models"
    info "Oppure riavvia: ~/ai-sessioni/start_ai_stack.sh"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo ""
success "Setup LCM-LoRA completato!"
echo ""
info "File installato:"
echo "  ${LORA_PATH}"
echo "  $(du -h "$LORA_PATH" | cut -f1)"
echo ""
info "Prossimi passi:"
echo "  1. image_loop.py v2.1 è già configurato per usare LCM automaticamente"
echo "  2. Valve lcm_enabled=True attiva il workflow accelerato (default)"
echo "  3. Prima generazione: /generate <prompt>"
echo ""
info "Parametri LCM attivi con image_loop v2.1:"
echo "  Steps:   6  (vs 25 standard)"
echo "  CFG:     1.8  (vs 7.5 standard)"
echo "  Sampler: lcm + sgm_uniform"
echo "  Tempo:   ~20s per immagine  (vs ~3-4 min standard)"
