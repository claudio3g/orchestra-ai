# AI Context - Scripts

> Generato: 2026-10-02T19:34:27Z
> Branch: main

---

## File: document-ai/scripts/download_lcm_lora.sh (10616 byte)

```
#!/bin/bash
# LCM-LoRA Download Script — Orchestra 8GB
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
```

## File: document-ai/scripts/egpu_check.sh (1934 byte)

```
#!/bin/bash
# =====================================================================
# egpu_check.sh — STEP 0 migrazione dual-GPU (3090 eGPU + 4060 interna)
# SOLA LETTURA: non modifica nulla. Mostra le due GPU, propone i valori
# ORCHESTRA_GPU_MAIN / ORCHESTRA_GPU_AUX e verifica link e Docker.
# Uso:  bash egpu_check.sh
# =====================================================================
echo "== 1. Thunderbolt: eGPU autorizzata? =="
if command -v boltctl >/dev/null; then boltctl list | grep -i -A8 'aoostar\|ag02\|egpu' || boltctl list | head -20
else echo "boltctl assente (sudo apt install bolt)"; fi

echo; echo "== 2. GPU viste dal driver =="
if ! command -v nvidia-smi >/dev/null; then echo "nvidia-smi non trovato: driver NVIDIA non installato"; exit 1; fi
nvidia-smi -L
N=$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l)
[ "$N" -lt 2 ] && echo "ATTENZIONE: vedo $N GPU (eGPU non collegata/riconosciuta?)"

echo; echo "== 3. Ruoli proposti (la GPU con più VRAM = main) =="
nvidia-smi --query-gpu=uuid,name,memory.total --format=csv,noheader,nounits \
 | sort -t, -k3 -n -r | awk -F', ' 'NR==1{print "export ORCHESTRA_GPU_MAIN=" $1 "   # " $2} NR==2{print "export ORCHESTRA_GPU_AUX=" $1 "    # " $2}'

echo; echo "== 4. Link PCIe, VRAM, potenza, temperatura =="
# eGPU su TB4: link atteso ~Gen3 x4. La 4060 interna: link proprio (x8/x16).
nvidia-smi --query-gpu=index,name,pcie.link.gen.current,pcie.link.width.current,memory.total,memory.free,power.draw,temperature.gpu --format=csv

echo; echo "== 5. Variabili correnti =="
echo "CUDA_DEVICE_ORDER=${CUDA_DEVICE_ORDER:-<non impostato>}  (consigliato: PCI_BUS_ID)"
echo "ORCHESTRA_GPU_MAIN=${ORCHESTRA_GPU_MAIN:-<non impostato>}"
echo "ORCHESTRA_GPU_AUX=${ORCHESTRA_GPU_AUX:-<non impostato>}"

echo; echo "== 6. Runtime Docker NVIDIA =="
docker info 2>/dev/null | grep -i -E 'runtimes|nvidia' || echo "docker non raggiungibile o runtime nvidia assente"
```

## File: document-ai/scripts/generate_ai_context.sh (2422 byte)

```
#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
MAX_FILE_SIZE=100000

rm -f AI_CONTEXT.md

is_includable() {
  local f="$1"
  [ -f "$f" ] || return 1
  local size
  size=$(stat -c%s "$f" 2>/dev/null || echo 0)
  [ "$size" -gt "$MAX_FILE_SIZE" ] && return 1
  case "$f" in
    *.pdf|*.docx|*.xlsx|*.png|*.jpg|*.jpeg|*.gif|*.zip|*.tar|*.gz|*.gguf|*.safetensors|*.onnx|*.bin|*.pyc|*.jsonl) return 1 ;;
  esac
  return 0
}

{
  echo "# AI Manifest - Orchestra AI"
  echo ""
  echo "> Generato: $NOW"
  echo "> Branch: $BRANCH"
  echo ""
  echo "| Path | Byte | SHA breve |"
  echo "|------|------|-----------|"
  git ls-files | while IFS= read -r f; do
    [ -f "$f" ] || continue
    size=$(stat -c%s "$f" 2>/dev/null || echo "?")
    sha=$(git hash-object "$f" | cut -c1-8)
    printf '| `%s` | %s | `%s` |\n' "$f" "$size" "$sha"
  done
  echo ""
  echo "**Totale: $(git ls-files | wc -l) file tracciati.**"
} > AI_MANIFEST.md
echo "OK: AI_MANIFEST.md"

emit_bundle() {
  local out="$1"
  local title="$2"
  shift 2
  {
    echo "# $title"
    echo ""
    echo "> Generato: $NOW"
    echo "> Branch: $BRANCH"
    echo ""
    echo "---"
    echo ""
    while IFS= read -r f; do
      is_includable "$f" || continue
      local size
      size=$(stat -c%s "$f")
      echo "## File: $f ($size byte)"
      echo ""
      echo '```'
      cat "$f"
      echo '```'
      echo ""
    done < <(git ls-files -- "$@" | sort -u)
  } > "$out"
  echo "OK: $out"
}

emit_bundle "AI_CTX_core.md" "AI Context - Core" "README.md" "README.it.md" ".gitignore" ".github/workflows/" "start_ai_stack.sh" "start_comfyui.sh"
emit_bundle "AI_CTX_rag.md" "AI Context - RAG" "rag/"
emit_bundle "AI_CTX_pipelines.md" "AI Context - Pipelines" "ollama/"
emit_bundle "AI_CTX_scripts.md" "AI Context - Scripts" "document-ai/scripts/"
emit_bundle "AI_CTX_config.md" "AI Context - Config" "document-ai/config/"

{
  echo "# AI Context - Knowledge Index"
  echo ""
  echo "> Generato: $NOW"
  echo ""
  echo "| File | Byte |"
  echo "|------|------|"
  git ls-files "document-ai/knowledge/" | while IFS= read -r f; do
    [ -f "$f" ] || continue
    size=$(stat -c%s "$f" 2>/dev/null || echo "?")
    printf '| `%s` | %s |\n' "$f" "$size"
  done
} > AI_CTX_knowledge_index.md
echo "OK: AI_CTX_knowledge_index.md"

echo "Done."
```

## File: document-ai/scripts/orchestra_install_guide.sh (9699 byte)

```
#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║  ORCHESTRA 8GB — GUIDA INSTALLAZIONE E TEST                    ║
# ║  orchestra_install_guide.sh                                     ║
# ║                                                                  ║
# ║  NON eseguire questo file direttamente.                         ║
# ║  È una guida con comandi da copiare uno alla volta.             ║
# ╚══════════════════════════════════════════════════════════════════╝

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — PULIZIA MODELLI
# Libera ~44GB dal disco eliminando modelli inutilizzabili su 8GB VRAM
# ══════════════════════════════════════════════════════════════════════════════

echo "=== STEP 1: Pulizia modelli ==="

docker exec ai-ollama-session ollama rm deepseek-coder-v2:16b
docker exec ai-ollama-session ollama rm qwen2.5-coder:14b-instruct-q4_K_M
docker exec ai-ollama-session ollama rm qwen3-coder:30b-a3b-q4_K_M
docker exec ai-ollama-session ollama rm llama3.2-vision:11b

# Verifica risultato
docker exec ai-ollama-session ollama list
df -h ~/ai-sessioni/

# Output atteso dopo pulizia:
# moondream:v2          1.7 GB
# llava:7b              4.7 GB
# llama3.1:8b           4.9 GB
# llama3.2:3b           2.0 GB
# qwen3.5:9b            6.6 GB


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — COPIA FILE MANIFOLD
# ══════════════════════════════════════════════════════════════════════════════

echo "=== STEP 2: Copia orchestra_manifold.py ==="

# Copia il file nella directory pipelines
cp orchestra_manifold.py ~/ai-sessioni/ollama/pipelines/orchestra_manifold.py

# Correggi permessi (i file creati da Docker potrebbero essere di root)
sudo chown -R $USER:$USER ~/ai-sessioni/ollama/pipelines/

# Verifica
ls -la ~/ai-sessioni/ollama/pipelines/


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — RICARICA PIPELINES
# Il container Pipelines rileva automaticamente nuovi file nella directory
# montata. Un riavvio è comunque il modo più sicuro per assicurarsi.
# ══════════════════════════════════════════════════════════════════════════════

echo "=== STEP 3: Riavvio container Pipelines ==="

docker restart ai-pipelines-session

# Attendi 10 secondi
sleep 10

# Verifica che il manifold sia caricato
curl -s http://localhost:9099/pipelines \
  -H "Authorization: Bearer ai-local-secure-key" | python3 -m json.tool

# Dovresti vedere nell'output qualcosa come:
# {
#     "data": [
#         { "id": "orchestra", "name": "🎼 Orchestra — AI Coordinator" },
#         { "id": "image-loop", "name": "🔄 Image Generator Loop" },
#         ...
#     ]
# }


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — VERIFICA IN OPENWEBUI
# Passaggi manuali nell'interfaccia web (http://localhost:3001)
# ══════════════════════════════════════════════════════════════════════════════

# 4.1 → Admin Panel → Settings → Pipelines
#       Verifica che l'URL sia: http://ai-pipelines-session:9099
#       Clicca "Refresh" o "Reload Pipelines"
#       Dovresti vedere "🎼 Orchestra — AI Coordinator" nella lista

# 4.2 → Nuova chat
#       Nel selettore modelli cerca "Orchestra"
#       Seleziona "🎼 Orchestra — AI Coordinator"

# 4.3 → TEST RAPIDI (copia e incolla uno alla volta):

# TEST A — Risposta generica (deve usare COORDINATORE / llama3.2:3b)
# "Ciao, come stai?"

# TEST B — Linux/Docker (deve usare LINUX_ADMIN / qwen3.5:9b)
# "Come verifico lo stato di un container Docker?"

# TEST C — ML/VRAM (deve usare ML_ENGINEER / qwen3.5:9b)
# "Qual è la differenza tra quantizzazione Q4_K_M e Q8_0?"

# TEST D — Python/API (deve usare COMFY_INTEGRATOR / qwen3.5:9b)
# "Scrivi uno script Python per chiamare l'API REST di ComfyUI"

# TEST E — Immagine (deve usare DESIGN_CRITIC / qwen3.5:9b)
# Allega un'immagine e scrivi "Analizza questa immagine"

# TEST F — Generazione (deve avviare IMAGE_LOOP)
# "/generate a futuristic city at night, neon lights, cyberpunk style"


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — MONITORING IN TEMPO REALE
# In un terminale separato, guarda i log per verificare il routing
# ══════════════════════════════════════════════════════════════════════════════

echo "=== STEP 5: Monitoring (eseguire in terminale separato) ==="

# Log del routing (mostra quale agente viene scelto)
docker logs ai-pipelines-session -f | grep -E "\[ORCHESTRA\]"

# Log completi Pipelines (verbose)
docker logs ai-pipelines-session -f

# Stato VRAM in tempo reale
watch -n 2 "nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader"


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — DOPO CONFERMA TEST OK: PULIZIA FINALE
# Solo quando il manifold funziona perfettamente
# ══════════════════════════════════════════════════════════════════════════════

echo "=== STEP 6: Pulizia finale (solo dopo test OK) ==="

# 6.1 — In OpenWebUI: elimina i Custom Models
#       Workspace → Models → elimina:
#         - coordinatore
#         - linux-admin
#         - ml-engineer
#         - comfy-integrator
#         - design-critic

# 6.2 — Disattiva ai_router.py (lo teniamo come backup, ma lo disattiviamo)
#       In OpenWebUI: Admin → Settings → Pipelines
#       Rimuovi "AI Router" dai modelli attivi (campo pipelines: "*")
#       OPPURE rinomina il file per disabilitarlo:
mv ~/ai-sessioni/ollama/pipelines/ai_router.py \
   ~/ai-sessioni/ollama/pipelines/ai_router.py.disabled

# 6.3 — Aggiorna start_ai_stack.sh
#       Sostituisci il blocco REQUIRED_MODELS con quello in patch_required_models.sh


# ══════════════════════════════════════════════════════════════════════════════
# TROUBLESHOOTING
# ══════════════════════════════════════════════════════════════════════════════

# PROBLEMA: Il manifold non appare in OpenWebUI
# SOLUZIONE:
docker logs ai-pipelines-session --tail 50 | grep -i error
# Verifica errori di sintassi Python nel file orchestra_manifold.py

# PROBLEMA: "❌ Impossibile connettersi a Ollama"
# SOLUZIONE: Verifica che Ollama sia raggiungibile dalla rete Docker
docker exec ai-pipelines-session curl -s http://ai-ollama-session:11434/api/tags | head -5

# PROBLEMA: Risposta molto lenta (>60s)
# SOLUZIONE: Verifica VRAM e che il modello stia andando in GPU
nvidia-smi
# Se VRAM è piena, qualcosa non si è scaricato correttamente
curl -s http://localhost:11435/api/ps | python3 -m json.tool

# PROBLEMA: /generate non funziona
# SOLUZIONE: Verifica che image-loop sia attivo
curl -s http://localhost:9099/pipelines \
  -H "Authorization: Bearer ai-local-secure-key" | python3 -m json.tool
# Verifica ComfyUI
curl -s http://localhost:8188/system_stats | python3 -c "
import sys,json
d=json.load(sys.stdin)['devices'][0]
print(f'VRAM: {d[\"vram_free\"]//1024//1024}MB liberi')
"

# PROBLEMA: I blocchi <think> di qwen3.5 appaiono nella risposta
# SOLUZIONE: Verifica in OpenWebUI che la valve "strip_thinking_tags" sia True
#            Admin → Settings → Pipelines → Orchestra → Valves
```

## File: document-ai/scripts/patch_required_models.sh (2121 byte)

```
#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║  ORCHESTRA 8GB — start_ai_stack.sh                             ║
# ║  SEZIONE DA SOSTITUIRE: REQUIRED_MODELS                         ║
# ║                                                                  ║
# ║  Trovare questa riga nell'originale:                            ║
# ║    REQUIRED_MODELS=(                                            ║
# ║        "llama3.2:3b"                                            ║
# ║        "qwen2.5:4b"                                             ║
# ║        "llama3.1:8b"                                            ║
# ║        "deepseek-coder-v2:16b"                                  ║
# ║    )                                                             ║
# ║                                                                  ║
# ║  E SOSTITUIRLA CON IL BLOCCO QUI SOTTO:                         ║
# ╚══════════════════════════════════════════════════════════════════╝

REQUIRED_MODELS=(
    "llama3.2:3b"        # COORDINATORE — risposte veloci/generiche (2GB)
    "qwen3.5:9b"         # SPECIALISTA UNICO — linux/ML/python/vision (6.6GB)
    "llama3.1:8b"        # FALLBACK specialista se VRAM bassa (4.9GB)
    "llava:7b"           # VISION FALLBACK (4.7GB)
    "moondream:v2"       # VISION EMERGENZA (1.7GB)
)

# ── NOTA: il modello viene verificato per nome parziale ──────────────────────
# La funzione ensure_container originale usa grep sul nome.
# "moondream:v2" potrebbe non matchare "moondream2" — verifica con:
#   docker exec ai-ollama-session ollama list
# Se il nome è "moondream:v2" nel tuo Ollama, il blocco qui sopra è corretto.
# Se fosse "moondream2" (senza :v2), cambia l'entry di conseguenza.
```

## File: document-ai/scripts/pattern_logger.py (828 byte)

```
"""
Pattern Logger per Orchestra
Traccia eventi significativi per analisi proattiva.
Salva in ~/ai-sessioni/logs/patterns.jsonl
"""

import json
import os
from datetime import datetime
from pathlib import Path

LOG_PATH = Path(os.environ.get("PATTERN_LOG_PATH", str(Path.home() / "ai-sessioni/logs/patterns.jsonl")))

def log_event(event_type: str, data: dict):
    """Aggiunge una riga JSON al log."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "data": data
        }
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[PATTERN_LOGGER] Errore scrittura: {e}", flush=True)
```

## File: document-ai/scripts/setup_security.sh (11141 byte)

```
#!/bin/bash
# ORCHESTRA 8GB — SECURITY SETUP v1.0
# Configura Caddy + ufw per esposizione sicura su internet.
# Eseguire UNA SOLA VOLTA dalla macchina locale (non via SSH).
# Rollback LIFO automatico in caso di errore.
#
# Prerequisiti:
#   - Stack Orchestra già funzionante in locale
#   - Porta 80 e 443 aperte sul router NAT verso questa macchina
#   - DDNS saponetta.mooo.com già puntato all'IP pubblico
#
# FILE: ~/ai-sessioni/setup_security.sh

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
CADDY_CONFIG_DIR="/etc/caddy"
CADDY_CADDYFILE="${CADDY_CONFIG_DIR}/Caddyfile"
CADDY_LOG_DIR="/var/log/caddy"
AI_SESSIONS_DIR="$HOME/ai-sessioni"
CADDYFILE_SRC="${AI_SESSIONS_DIR}/Caddyfile"
DOMAIN="saponetta.mooo.com"

ROLLBACK_ACTIONS=()

rollback() {
    local EXIT_CODE=$?
    error "ERRORE (exit ${EXIT_CODE}) — Rollback in corso..."
    local N=${#ROLLBACK_ACTIONS[@]}
    for (( i=N-1; i>=0; i-- )); do
        echo "  ↩ ${ROLLBACK_ACTIONS[$i]}"
        eval "${ROLLBACK_ACTIONS[$i]}" 2>/dev/null || true
    done
    warn "Rollback completato."
    exit "${EXIT_CODE}"
}
trap rollback ERR INT TERM

# ─────────────────────────────────────────────────────────────────────────────
# Stato pre-esistente
# ─────────────────────────────────────────────────────────────────────────────
CADDY_PRE_INSTALLED=false
UFW_PRE_ACTIVE=false
UFW_PRE_ENABLED=false

dpkg -l caddy &>/dev/null 2>&1 && CADDY_PRE_INSTALLED=true
systemctl is-active --quiet ufw 2>/dev/null  && UFW_PRE_ACTIVE=true
systemctl is-enabled --quiet ufw 2>/dev/null && UFW_PRE_ENABLED=true

# ─────────────────────────────────────────────────────────────────────────────
# Piano e conferma
# ─────────────────────────────────────────────────────────────────────────────
echo -e "\n${BOLD}Piano di sicurezza Orchestra 8GB${NC}"
echo "  1. Installa Caddy (reverse proxy + TLS automatico)"
echo "  2. Copia Caddyfile in /etc/caddy/"
echo "  3. Configura ufw (80, 443 aperti — tutto il resto chiuso)"
echo "  4. Abilita e avvia Caddy come servizio systemd"
echo ""
echo "  Dominio target: ${DOMAIN}"
echo "  Caddyfile sorgente: ${CADDYFILE_SRC}"
echo ""
warn "SSH è disabilitato — assicurati di avere accesso fisico alla macchina."
echo -n "Procedere? [s/N] "
read -r CONFIRM
[[ ! "$CONFIRM" =~ ^[sS]$ ]] && { info "Annullato."; trap - ERR INT TERM; exit 0; }

# ─────────────────────────────────────────────────────────────────────────────
header "1/4  Caddy — installazione"
# ─────────────────────────────────────────────────────────────────────────────
if [ "$CADDY_PRE_INSTALLED" = false ]; then
    info "Aggiunta repository Caddy..."
    sudo apt-get update -qq
    sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl

    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    ROLLBACK_ACTIONS+=("sudo rm -f /usr/share/keyrings/caddy-stable-archive-keyring.gpg")

    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | sudo tee /etc/apt/sources.list.d/caddy-stable.list > /dev/null
    ROLLBACK_ACTIONS+=("sudo rm -f /etc/apt/sources.list.d/caddy-stable.list")

    sudo apt-get update -qq
    sudo apt-get install -y caddy
    ROLLBACK_ACTIONS+=("sudo apt-get purge -y caddy 2>/dev/null || true")
    success "Caddy installato"
else
    success "Caddy già installato ($(caddy version))"
fi

# ─────────────────────────────────────────────────────────────────────────────
header "2/4  Caddy — configurazione"
# ─────────────────────────────────────────────────────────────────────────────

# Verifica che il Caddyfile sorgente esista
if [ ! -f "$CADDYFILE_SRC" ]; then
    error "Caddyfile non trovato in ${CADDYFILE_SRC}"
    error "Copia prima Caddyfile nella directory ai-sessioni/"
    exit 1
fi

# Backup Caddyfile esistente
if [ -f "$CADDY_CADDYFILE" ]; then
    BAK="${CADDY_CADDYFILE}.backup.$(date +%Y%m%d_%H%M%S)"
    sudo cp "$CADDY_CADDYFILE" "$BAK"
    ROLLBACK_ACTIONS+=("sudo cp '${BAK}' '${CADDY_CADDYFILE}'")
    info "Backup Caddyfile: ${BAK}"
fi

# Copia Caddyfile
sudo mkdir -p "$CADDY_CONFIG_DIR"
sudo cp "$CADDYFILE_SRC" "$CADDY_CADDYFILE"
ROLLBACK_ACTIONS+=("sudo rm -f '${CADDY_CADDYFILE}'")
success "Caddyfile copiato in ${CADDY_CADDYFILE}"

# Directory log
sudo mkdir -p "$CADDY_LOG_DIR"
sudo chown caddy:caddy "$CADDY_LOG_DIR" 2>/dev/null || \
    sudo chown www-data:www-data "$CADDY_LOG_DIR" 2>/dev/null || true

# Validazione sintattica
info "Validazione Caddyfile..."
sudo caddy validate --config "$CADDY_CADDYFILE" && success "Caddyfile valido" || {
    error "Caddyfile non valido — controlla la sintassi"
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
header "3/4  ufw — firewall"
# ─────────────────────────────────────────────────────────────────────────────
info "Configurazione regole ufw..."

# Installa ufw se mancante (non presente di default su tutti i sistemi Ubuntu)
if ! command -v ufw &>/dev/null; then
    info "Installazione ufw..."
    sudo apt-get install -y ufw
    ROLLBACK_ACTIONS+=("sudo apt-get purge -y ufw 2>/dev/null || true")
    success "ufw installato"
fi

# Politica di default
sudo ufw default deny incoming  2>/dev/null || true
sudo ufw default allow outgoing 2>/dev/null || true

# Regole permissive (internet)
sudo ufw allow 80/tcp   comment 'HTTP - ACME challenge Let-Encrypt'
sudo ufw allow 443/tcp  comment 'HTTPS - Caddy reverse proxy'

# ComfyUI — ALLOW dai container Docker prima del DENY globale
# ufw valuta le regole dall'alto verso il basso: gli ALLOW specifici
# devono precedere il DENY Anywhere, altrimenti vengono ignorati.
# docker0 (bridge default) e ollama_default (rete Orchestra) usano
# tipicamente 172.17.x.x e 172.19.x.x
sudo ufw allow from 172.17.0.0/16 to any port 8188 \
    comment 'ComfyUI - Docker bridge' 2>/dev/null || true
sudo ufw allow from 172.19.0.0/16 to any port 8188 \
    comment 'ComfyUI - ollama_default' 2>/dev/null || true

# Blocco porte interne Orchestra
# IMPORTANTE: 8188 inserita DOPO gli ALLOW Docker — ordine critico per ufw
for PORT in 3001 8188 11435 6333 6335 9099; do
    sudo ufw deny "${PORT}" comment "Orchestra internal - block external" 2>/dev/null || true
done

# Abilita ufw
if [ "$UFW_PRE_ENABLED" = false ]; then
    sudo ufw --force enable
    ROLLBACK_ACTIONS+=("sudo ufw --force disable 2>/dev/null || true")
fi
sudo ufw reload 2>/dev/null || true

success "ufw configurato"
sudo ufw status verbose

# ─────────────────────────────────────────────────────────────────────────────
header "4/4  Caddy — avvio servizio"
# ─────────────────────────────────────────────────────────────────────────────
sudo systemctl enable caddy
ROLLBACK_ACTIONS+=("sudo systemctl disable caddy 2>/dev/null || true")

# Disabilita ERR trap prima di avviare Caddy:
# un errore di start è recuperabile (porta in uso, NAT non ancora aperto)
# e NON deve causare rollback dell'installazione.
trap - ERR

sudo systemctl restart caddy 2>/dev/null || true
sleep 3

if systemctl is-active --quiet caddy; then
    success "Caddy attivo"
else
    warn "Caddy non si è avviato. Log diagnostici:"
    echo ""
    sudo journalctl -u caddy -n 25 --no-pager 2>/dev/null || true
    echo ""
    warn "Cause comuni:"
    warn "  - Porta 80 o 443 già occupata da altro processo"
    warn "    Controlla: ss -tlnp | grep -E \":80 |:443 \""
    warn "  - NAT router non ancora configurato (normale in questa fase)"
    info "Dopo aver aperto le porte 80 e 443 sul router:"
    info "  sudo systemctl start caddy"
    info "  sudo journalctl -u caddy -f"
    warn "Caddy è installato e abilitato — si avvierà correttamente."
fi


# ─────────────────────────────────────────────────────────────────────────────
trap - ERR INT TERM

success "Setup sicurezza completato!"
echo ""
info "Prossimi passi:"
echo "  1. Apri porta 443 e 80 sul router NAT → questa macchina"
echo "  2. Verifica: curl -v https://${DOMAIN}"
echo "  3. Rimuovi la regola NAT :3001 dal router"
echo "  4. Verifica finale: https://${DOMAIN} → OpenWebUI"
echo ""
info "Log Caddy: sudo journalctl -u caddy -f"
info "Stato:     sudo systemctl status caddy"
info "Cert TLS:  sudo caddy list-certificates 2>/dev/null || curl -s https://${DOMAIN} -I"
```

