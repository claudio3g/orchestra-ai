# AI Context - Scripts

> Generato: 2026-10-07T08:30:27Z
> Branch: dual-gpu-final

---

## File: document-ai/scripts/download_lcm_lora.sh (10612 byte)

```
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

## File: document-ai/scripts/generate_ai_context.sh (3990 byte)

```
#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
MAX_FILE_SIZE=100000

rm -f AI_CONTEXT.md

# Elenco file del repository: tracciati + NUOVI non ancora in staging (rispettando .gitignore).
# Il workflow applica la patch con `git apply` (senza staging), esegue questo script e solo dopo
# fa `git add -A`: con `git ls-files` i file aggiunti dalla patch comparivano nel manifest con
# un commit di ritardo. Qui si usa lo stesso criterio di `git add -A`. I file cancellati dalla
# patch (ancora nell'indice) vengono scartati dal controllo di esistenza.
list_files() {
  git ls-files --cached --others --exclude-standard -- "$@" | sort -u | while IFS= read -r f; do
    if [ -f "$f" ]; then printf '%s\n' "$f"; fi
  done
}

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
  list_files | while IFS= read -r f; do
    size=$(stat -c%s "$f" 2>/dev/null || echo "?")
    sha=$(git hash-object "$f" | cut -c1-8)
    printf '| `%s` | %s | `%s` |\n' "$f" "$size" "$sha"
  done
  echo ""
  echo "**Totale: $(list_files | wc -l) file tracciati.**"
} > AI_MANIFEST.md
echo "OK: AI_MANIFEST.md"

# Sezione "2. File tracciati (GROUND TRUTH)" di AI_BOOTSTRAP.md: rigenerata dall'elenco reale
# (era scritta a mano e restava indietro: citava AI_CONTEXT.md, ormai inesistente). Le altre
# sezioni non vengono toccate.
if [ -f AI_BOOTSTRAP.md ] && grep -q '^## 2\. File tracciati' AI_BOOTSTRAP.md; then
  list_tmp="$(mktemp)"
  {
    echo '| Path | Byte |'
    echo '|------|------|'
    list_files | while IFS= read -r f; do
      printf '| `%s` | %s |\n' "$f" "$(stat -c%s "$f" 2>/dev/null || echo '?')"
    done
  } > "$list_tmp"
  awk -v list="$list_tmp" '
    /^## 2\. File tracciati/ {
      print; print ""
      print "**Nessun file esiste al di fuori di questa lista. Se un file non è qui, NON ESISTE.**"
      print ""
      while ((getline line < list) > 0) print line
      print ""; skip=1; next }
    /^## 3\./ { skip=0 }
    !skip { print }
  ' AI_BOOTSTRAP.md > AI_BOOTSTRAP.md.new && mv AI_BOOTSTRAP.md.new AI_BOOTSTRAP.md
  rm -f "$list_tmp"
  echo "OK: AI_BOOTSTRAP.md (sezione file tracciati)"
fi

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
    done < <(list_files "$@")
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
  list_files "document-ai/knowledge/" | while IFS= read -r f; do
    size=$(stat -c%s "$f" 2>/dev/null || echo "?")
    printf '| `%s` | %s |\n' "$f" "$size"
  done
} > AI_CTX_knowledge_index.md
echo "OK: AI_CTX_knowledge_index.md"

echo "Done."
```

## File: document-ai/scripts/orchestra_bench_models.sh (6418 byte)

```
#!/bin/bash
# =====================================================================
# orchestra_bench_models.sh — velocita' e concorrenza dei modelli su un backend Ollama
#
# Serve a decidere CON DATI MISURATI tra "un solo agente forte" e "piu' agenti in parallelo":
# per ogni modello misura la velocita' di un singolo flusso e il throughput AGGREGATO con N
# richieste simultanee sullo stesso modello (i pesi sono letti una volta sola, ogni slot ha la
# sua cache KV). Mostra anche se il modello sta tutto in GPU e la VRAM usata.
#
# Uso:   bash orchestra_bench_models.sh [opzioni] MODELLO [MODELLO...]
#   --role main|aux     backend da misurare (default main = 3090, porta 11435; aux = 4060, 11436)
#   --url URL           URL Ollama alternativo
#   --tokens N          token generati per richiesta (default 200)
#   --parallel "1 2 4"  livelli di concorrenza (default "1 2")
#   --ctx N             contesto (num_ctx) di ogni richiesta (default 8192 = quello del manifold)
# Esempio: bash orchestra_bench_models.sh --parallel "1 2 3" qwen3.6:27b qwen2.5-coder:14b-instruct-q4_K_M
#
# Nota: per vedere uno scaling reale l'istanza Ollama deve avere OLLAMA_NUM_PARALLEL >= N
# (launcher: ORCHESTRA_MAIN_PARALLEL / ORCHESTRA_AUX_PARALLEL). Ollama fissa a 1 slot alcune
# architetture (multimodali, ad attenzione ricorrente): se lo speedup resta ~1.0x e' un
# indizio che il modello non scala in parallelo su Ollama. Lo script scarica il modello a fine misura.
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/orchestra_gpu_env.sh"
ROLE=main; URL=""; TOKENS=200; PARS="1 2"; MODELS=(); CTX="${ORCHESTRA_CONTEXT_LENGTH:-8192}"
while [ $# -gt 0 ]; do case "$1" in
    --role) ROLE="$2"; shift 2;; --url) URL="$2"; shift 2;; --tokens) TOKENS="$2"; shift 2;;
    --parallel) PARS="$2"; shift 2;; --ctx) CTX="$2"; shift 2;; -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) MODELS+=("$1"); shift;; esac; done
[ ${#MODELS[@]} -gt 0 ] || { echo "Uso: $0 [--role main|aux] [--parallel \"1 2 4\"] MODELLO [MODELLO...]  (-h per l'aiuto)"; exit 2; }
case "$ROLE" in
    aux)  URL="${URL:-http://127.0.0.1:11436}"; CONT="${OLLAMA_AUX_CONTAINER:-ai-ollama-aux-session}";;
    main) URL="${URL:-http://127.0.0.1:11435}"; CONT="${OLLAMA_CONTAINER:-ai-ollama-session}";;
    *) echo "Ruolo non valido: $ROLE (main|aux)"; exit 2;;
esac
command -v nvidia-smi >/dev/null 2>&1 && detect_gpu_roles >/dev/null 2>&1
UUID="$(gpu_for_role "$ROLE")"
mem_used() { [ -n "$UUID" ] && nvidia-smi -i "$UUID" --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' '; }
gen() { # <modello> <json options extra> <keep_alive> <prompt>
    curl -s -m 900 "$URL/api/generate" -d "{\"model\":\"$1\",\"prompt\":\"$4\",\"stream\":false,\"keep_alive\":$3,\"options\":{$2}}"; }

NP="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$CONT" 2>/dev/null | sed -n 's/^OLLAMA_NUM_PARALLEL=//p')"
echo "Backend: ${ROLE} (${URL}) · contenitore ${CONT} · OLLAMA_NUM_PARALLEL=${NP:-?} · contesto ${CTX} · ${TOKENS} token/richiesta · GPU ${UUID:-?}"
echo "Ollama: $(docker exec "$CONT" ollama --version 2>/dev/null | tail -1)"
printf '%-36s %3s %10s %10s %8s %9s  %s\n' modello N "tok/s flusso" "tok/s totale" speedup "VRAM MiB" processore
rc=0
for M in "${MODELS[@]}"; do
    # Il modello deve esistere sul backend: altrimenti un errore chiaro con il comando per scaricarlo.
    if command -v docker >/dev/null 2>&1 && docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${CONT}$"; then
        if ! ollama_has_model "$CONT" "$M"; then
            echo "  ✘ ${M}: non installato su ${ROLE}. Scaricalo con: docker exec ${CONT} ollama pull ${M}" >&2
            echo "    (se risponde 412 'requires a newer version of Ollama': aggiorna l immagine con ORCHESTRA_PULL_IMAGES=1 bash start_ai_stack.sh)" >&2
            rc=1; continue
        fi
    fi
    gen "$M" "\"num_ctx\":${CTX},\"num_predict\":1" '"10m"' "ok" >/dev/null 2>&1
    PROC_LINE="$(docker exec "$CONT" ollama ps 2>/dev/null | awk -v m="$M" '$1==m')"
    PROC="$(docker exec "$CONT" ollama ps 2>/dev/null | awk -v m="$M" '$1==m' | grep -Eo '[0-9]+% (GPU|CPU)|[0-9]+%/[0-9]+% CPU/GPU' | head -1)"
    VRAM="$(mem_used)"
    base=""
    for N in $PARS; do
        [ -n "$NP" ] && [ "$N" -gt "$NP" ] && echo "  ATTENZIONE: N=${N} > OLLAMA_NUM_PARALLEL=${NP}: le richieste in eccesso vengono messe in coda (alza ORCHESTRA_*_PARALLEL)" >&2
        T="$(mktemp -d)"
        for i in $(seq 1 "$N"); do
            ( gen "$M" "\"num_ctx\":${CTX},\"num_predict\":${TOKENS},\"temperature\":0" '"10m"' "Scrivi una funzione Python che ordina una lista (variante ${i}) e spiegala passo per passo." > "$T/$i.json" ) &
        done
        wait
        ROW="$(python3 - "$T" <<'PY'
import glob, json, sys
n = 0; tot = 0; per = []; durs = []; err = ""
for f in glob.glob(sys.argv[1] + "/*.json"):
    try:
        d = json.load(open(f))
        if "error" in d:
            err = str(d["error"])[:90]; continue
        c = d["eval_count"]; e = d["eval_duration"] / 1e9; t = d["total_duration"] / 1e9
    except Exception:
        continue
    n += 1; tot += c; per.append(c / e); durs.append(t)
if n == 0: print("ERR " + err)
else: print(f"{sum(per)/n:.1f} {tot/max(durs):.1f} {n}")
PY
)"
        rm -rf "$T"
        if [ "${ROW%% *}" = "ERR" ]; then
            [ -n "${ROW#ERR}" ] && echo "  Ollama risponde:${ROW#ERR}" >&2
            printf '%-36s %3s %10s %10s %8s %9s  %s\n' "$M" "$N" "errore" "errore" "-" "${VRAM:--}" "${PROC:--}"; rc=1; continue
        fi
        per=$(echo "$ROW" | awk '{print $1}'); agg=$(echo "$ROW" | awk '{print $2}')
        [ -z "$base" ] && base="$agg"
        printf '%-36s %3s %10s %10s %7sx %9s  %s\n' "$M" "$N" "$per" "$agg" "$(awk -v a="$agg" -v b="$base" 'BEGIN{printf "%.2f", a/b}')" "${VRAM:--}" "${PROC:--}"
    done
    [ -n "$PROC_LINE" ] && echo "  ollama ps: ${PROC_LINE}"
    case "$PROC" in *CPU*) echo "  ATTENZIONE: ${M} NON e' al 100% in GPU (${PROC}) con contesto ${CTX} e ${NP:-?} richieste parallele: parte dei layer e' su CPU, le prestazioni calano molto. Riduci --ctx o OLLAMA_NUM_PARALLEL (la cache KV cresce di contesto x parallelo)";; esac
    gen "$M" '' 0 "" >/dev/null 2>&1      # scarica il modello: la misura successiva parte pulita
done
exit $rc
```

## File: document-ai/scripts/orchestra_gpu_env.sh (10199 byte)

```
#!/bin/bash
# =====================================================================
# orchestra_gpu_env.sh — libreria condivisa (da `source`, non da eseguire)
# Usata da: start_ai_stack.sh, start_comfyui.sh, orchestra_smoke_test.sh
#
# EGPU-02/02b: ruoli GPU main (3090 eGPU, 24 GB) / aux (4060 interna, 8 GB).
# Si usano gli UUID (stabili): gli indici cambiano se la eGPU viene ricollegata.
# Nessun effetto collaterale al caricamento: definisce solo funzioni.
# =====================================================================

# Fallback minimi se il chiamante non ha definito le funzioni di log colorate.
type info    >/dev/null 2>&1 || info()    { echo "[info] $*"; }
type warn    >/dev/null 2>&1 || warn()    { echo "[warn] $*" >&2; }
type success >/dev/null 2>&1 || success() { echo "[ok] $*"; }

# detect_gpu_roles
#   Imposta ed esporta ORCHESTRA_GPU_MAIN / ORCHESTRA_GPU_AUX (UUID).
#   - valori gia' presenti nell'ambiente vengono rispettati se l'UUID esiste ora;
#   - altrimenti main = GPU con piu' VRAM, aux = la successiva;
#   - con una sola GPU (eGPU scollegata) main = quella GPU e aux resta vuota.
detect_gpu_roles() {
    command -v nvidia-smi >/dev/null 2>&1 || { warn "nvidia-smi non trovato: GPU non assegnate"; return 0; }
    local list
    # righe "uuid, nome, MiB totali", dalla piu' grande alla piu' piccola
    list=$(nvidia-smi --query-gpu=uuid,name,memory.total --format=csv,noheader,nounits 2>/dev/null \
           | sort -t, -k3 -n -r) || true
    [ -n "$list" ] || { warn "nvidia-smi non elenca GPU"; return 0; }

    # Valida i valori forniti dall'utente: devono essere UUID presenti ora.
    if [ -n "${ORCHESTRA_GPU_MAIN:-}" ] && ! echo "$list" | grep -q "^${ORCHESTRA_GPU_MAIN},"; then
        warn "ORCHESTRA_GPU_MAIN='${ORCHESTRA_GPU_MAIN}' non presente (eGPU scollegata? serve l'UUID): rilevo in automatico"
        unset ORCHESTRA_GPU_MAIN
    fi
    if [ -n "${ORCHESTRA_GPU_AUX:-}" ] && ! echo "$list" | grep -q "^${ORCHESTRA_GPU_AUX},"; then
        warn "ORCHESTRA_GPU_AUX='${ORCHESTRA_GPU_AUX}' non presente: rilevo in automatico"
        unset ORCHESTRA_GPU_AUX
    fi
    [ -n "${ORCHESTRA_GPU_MAIN:-}" ] || ORCHESTRA_GPU_MAIN=$(echo "$list" | sed -n 1p | cut -d, -f1)
    if [ -z "${ORCHESTRA_GPU_AUX:-}" ] || [ "$ORCHESTRA_GPU_AUX" = "$ORCHESTRA_GPU_MAIN" ]; then
        ORCHESTRA_GPU_AUX=$(echo "$list" | cut -d, -f1 | grep -v "^${ORCHESTRA_GPU_MAIN}$" | head -1) || true
    fi
    export ORCHESTRA_GPU_MAIN ORCHESTRA_GPU_AUX
    info "GPU main: $(echo "$list" | grep "^${ORCHESTRA_GPU_MAIN}," | cut -d, -f2 | sed 's/^ //') (${ORCHESTRA_GPU_MAIN})"
    [ -n "${ORCHESTRA_GPU_AUX:-}" ] \
        && info "GPU aux:  $(echo "$list" | grep "^${ORCHESTRA_GPU_AUX}," | cut -d, -f2 | sed 's/^ //') (${ORCHESTRA_GPU_AUX})" \
        || warn "Nessuna GPU aux (una sola GPU visibile)"
}

# gpu_for_role <main|aux>  → stampa l'UUID del ruolo (vuoto se non assegnato)
gpu_for_role() {
    case "${1:-main}" in
        aux)  echo "${ORCHESTRA_GPU_AUX:-}" ;;
        *)    echo "${ORCHESTRA_GPU_MAIN:-}" ;;
    esac
}

# gpu_total_mb <uuid>  → VRAM totale in MiB (vuoto se non trovata)
gpu_total_mb() {
    nvidia-smi --query-gpu=uuid,memory.total --format=csv,noheader,nounits 2>/dev/null \
        | awk -F', *' -v u="$1" '$1==u {print $2}'
}

# aux_ollama_enabled → exit 0 se il secondo Ollama (aux) va avviato.
#   Richiede una GPU aux e ORCHESTRA_AUX_OLLAMA != 0 (impostalo a 0 per la
#   variante "4060 solo per ComfyUI": nessun Ollama su aux).
aux_ollama_enabled() {
    [ -n "${ORCHESTRA_GPU_AUX:-}" ] && [ "${ORCHESTRA_AUX_OLLAMA:-1}" != "0" ]
}

# pick_image <container> <repository> <immagine-di-default>
#   Sceglie l immagine con cui (ri)creare un container SENZA indovinare la versione:
#   1) quella del container esistente; 2) la prima immagine locale del repository; 3) il default.
#   Va chiamata PRIMA di recreate_*, che rimuove il container.
pick_image() {
    local c="$1" repo="$2" def="$3" img
    img="$(docker inspect -f '{{.Config.Image}}' "$c" 2>/dev/null)"
    [ -n "$img" ] && { echo "$img"; return 0; }
    img="$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | awk -v r="${repo}:" 'index($0,r)==1 && $0 !~ /<none>/ {print; exit}')"
    [ -n "$img" ] && { echo "$img"; return 0; }
    echo "$def"
}

# image_id_of <immagine> → ID locale dell immagine (stessa sorgente usata per l etichetta del container:
#   confrontare l ID del container con quello dell immagine darebbe falsi positivi con certi archivi di immagini).
image_id_of() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null; }

# recreate_if_image_outdated <container> <immagine>
#   Ricrea il container se l immagine locale e' stata AGGIORNATA dopo la sua creazione (etichetta
#   orchestra.image_id diversa dall ID attuale). Un container senza etichetta (creato prima di questa
#   funzione) NON viene toccato. ORCHESTRA_RECREATE_OLLAMA=1 forza la ricreazione una volta.
#   I volumi dei modelli non vengono toccati; il container vecchio resta come .bak finche il nuovo non parte.
recreate_if_image_outdated() {
    local c="$1" img="$2" cur want
    docker ps -a --format '{{.Names}}' | grep -q "^${c}$" || return 0
    if [ "${ORCHESTRA_RECREATE_OLLAMA:-0}" = "1" ]; then
        warn "${c}: ricreazione forzata (ORCHESTRA_RECREATE_OLLAMA=1)"; retire_container "$c"; return 0
    fi
    cur="$(docker inspect -f '{{index .Config.Labels "orchestra.image_id"}}' "$c" 2>/dev/null)"
    want="$(image_id_of "$img")"
    { [ -n "$cur" ] && [ -n "$want" ] && [ "$cur" != "$want" ]; } || return 0
    warn "${c}: l immagine ${img} e stata aggiornata: ricreo il container (volumi intatti)"
    retire_container "$c"
}

# retire_container <nome>: mette da parte il container (rename in <nome>.bak) invece di cancellarlo.
#   Se la ricreazione fallisce, restore_container lo rimette com era; se riesce, discard_backup lo elimina.
retire_container() {
    local c="$1"
    docker rm -f "${c}.bak" >/dev/null 2>&1 || true
    if docker rename "$c" "${c}.bak" >/dev/null 2>&1; then
        info "${c}: container precedente conservato come ${c}.bak (ripristino automatico se la ricreazione fallisce)"
    else
        docker rm -f "$c" >/dev/null
    fi
}
restore_container() {
    local c="$1"
    docker ps -a --format '{{.Names}}' | grep -q "^${c}\.bak$" || return 1
    docker rm -f "$c" >/dev/null 2>&1 || true
    docker rename "${c}.bak" "$c" && docker start "$c" >/dev/null 2>&1
}
discard_backup() { docker rm -f "${1}.bak" >/dev/null 2>&1 || true; }

# recreate_if_not_pinned <container> <uuid>
#   ensure_container RIUSA i container esistenti (docker start): un container creato
#   con `--gpus all` resterebbe visibile su entrambe le GPU anche cambiando gli
#   argomenti. Lo rimuoviamo se non e' fissato all'UUID richiesto. I modelli stanno
#   in volumi nominati, che `docker rm` NON tocca.
recreate_if_not_pinned() {
    local c="$1" uuid="$2"
    [ -n "$uuid" ] || return 0
    docker ps -a --format '{{.Names}}' | grep -q "^${c}$" || return 0
    if ! docker inspect -f '{{json .HostConfig.DeviceRequests}}' "$c" 2>/dev/null | grep -q "$uuid"; then
        warn "${c} non e' fissato alla GPU richiesta: ricreo il container (volumi intatti)"
        retire_container "$c"
    fi
}

# recreate_if_env_stale <container> ARG...
#   Le variabili d'ambiente di un container sono fissate alla creazione. Ogni ARG e' o il
#   NOME di una variabile della shell (confronta con il suo valore attuale) oppure una
#   coppia letterale NOME=VALORE. Se il container ha un valore diverso (o manca), lo
#   ricreiamo: i volumi nominati restano intatti.
recreate_if_env_stale() {
    local c="$1"; shift
    docker ps -a --format '{{.Names}}' | grep -q "^${c}$" || return 0
    local envs v want
    envs=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" 2>/dev/null) || return 0
    for v in "$@"; do
        if [[ "$v" == *=* ]]; then want="$v"; else want="${v}=${!v:-}"; fi
        if ! printf '%s\n' "$envs" | grep -qxF "$want"; then
            warn "${c}: ${want%%=*} cambiato o assente → ricreo il container"
            retire_container "$c"
            return 0
        fi
    done
}

# ollama_has_model <container> <nome:tag>
#   Confronto ESATTO sul nome completo. Il vecchio controllo usava solo il nome prima dei
#   due punti: con qwen2.5-coder:14b gia' installato il 32b risultava "presente" e non
#   veniva mai scaricato.
ollama_has_model() {
    local c="$1" m="$2"
    docker exec "$c" ollama list 2>/dev/null | awk 'NR>1 {print $1}' \
        | grep -qxF -e "$m" -e "${m}:latest"
}

# container_gpu_count <container> → numero di GPU viste dentro il container
container_gpu_count() {
    docker exec "$1" nvidia-smi -L 2>/dev/null | grep -c '^GPU' || true
}

# comfyui_gpu_setup [flag_memoria_gpu_piccola]
#   EGPU-05: fissa ComfyUI alla GPU del ruolo ORCHESTRA_COMFY_ROLE (default: main).
#   - esporta CUDA_DEVICE_ORDER=PCI_BUS_ID e CUDA_VISIBLE_DEVICES=<UUID>: CUDA accetta
#     gli UUID, quindi niente dipendenza dall'ordine degli indici (nvidia-smi elenca la
#     4060 come 0 e la 3090 come 1, ma l'ordine predefinito di CUDA e' "piu' veloce prima");
#   - riempie l'array globale COMFY_MEM_ARGS:
#       GPU >= 16 GB (3090): --normalvram e VAE su GPU;
#       GPU piccola o sconosciuta: <flag> (default --normalvram) + --cpu-vae, come prima.
comfyui_gpu_setup() {
    local small_flag="${1:---normalvram}" role uuid total
    role="${ORCHESTRA_COMFY_ROLE:-main}"
    uuid="$(gpu_for_role "$role")"
    if [ -z "$uuid" ] && [ "$role" != "main" ]; then
        warn "Ruolo GPU '${role}' di ComfyUI non assegnato: uso main"
        role="main"; uuid="$(gpu_for_role main)"
    fi
    if [ -z "$uuid" ]; then
        warn "Nessuna GPU assegnata a ComfyUI: GPU predefinita, flag storici"
        COMFY_MEM_ARGS=("$small_flag" --cpu-vae)
        return 0
    fi
    export CUDA_DEVICE_ORDER=PCI_BUS_ID
    export CUDA_VISIBLE_DEVICES="$uuid"
    total="$(gpu_total_mb "$uuid")"
    info "ComfyUI sulla GPU ${role} (${uuid}, ${total:-?} MiB)"
    if [ -n "$total" ] && [ "$total" -ge 16000 ]; then
        COMFY_MEM_ARGS=(--normalvram)
    else
        COMFY_MEM_ARGS=("$small_flag" --cpu-vae)
    fi
}
```

## File: document-ai/scripts/orchestra_install_guide.sh (9699 byte)

```
#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║  ORCHESTRA     — GUIDA INSTALLAZIONE E TEST                    ║
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

## File: document-ai/scripts/orchestra_power.sh (8658 byte)

```
#!/bin/bash
# =====================================================================
# orchestra_power.sh — consumi energetici delle GPU (3090 main + 4060 aux)
#
#   status                  potenza, limiti, P-state, carico e temperatura per GPU
#   profile <p>             imposta il power limit: eco | balanced | performance
#   restore                 ripristina i limiti predefiniti del costruttore
#   log [--interval S] [--count N]   campiona i watt in logs/power.csv (Ctrl-C per fermare)
#   report [file]           potenza media e Wh per GPU dal log
#   bench [p1 p2 ...]       confronta i profili: token/s, watt medi, token per joule
#
# Profili = percentuale del limite PREDEFINITO della GPU, limitata a [min, max] del driver:
#   eco 70% · balanced 85% · performance 100%   (override: ORCHESTRA_POWER_MAIN_W / _AUX_W)
# Nota: la generazione di testo e' limitata dalla banda di memoria, quindi ridurre il limite
# di potenza tende a costare poche prestazioni: va pero' MISURATO con `bench` sulla tua
# macchina prima di adottare un profilo. I limiti non sono persistenti: tornano al
# predefinito al riavvio. Richiede privilegi per -pl (usa `sudo -n`, mai interattivo).
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/orchestra_gpu_env.sh"
LOG_FILE="${ORCHESTRA_POWER_LOG:-$REPO_ROOT/logs/power.csv}"
OLLAMA_BENCH_URL="${ORCHESTRA_BENCH_URL:-http://127.0.0.1:11435}"
BENCH_MODEL="${ORCHESTRA_BENCH_MODEL:-qwen2.5-coder:14b-instruct-q4_K_M}"
BENCH_RUNS="${ORCHESTRA_BENCH_RUNS:-3}"
BENCH_PROMPT="Scrivi in Python una funzione che calcola i numeri primi fino a N con il crivello di Eratostene, spiegando ogni passaggio."

command -v nvidia-smi >/dev/null 2>&1 || { echo "nvidia-smi non trovato"; exit 1; }
detect_gpu_roles >/dev/null 2>&1

SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo -n"
# q: valore di un campo; toglie solo gli spazi ai bordi (i nomi GPU contengono spazi).
q()   { nvidia-smi -i "$1" --query-gpu="$2" --format=csv,noheader,nounits 2>/dev/null | head -1 | sed 's/^ *//;s/ *$//'; }
uuid_of() { gpu_for_role "$1"; }
# GPU su cui operare: ORCHESTRA_POWER_ROLES (default "main aux"), solo se assegnate.
roles() { local r; for r in main aux; do [ -n "$(uuid_of "$r")" ] || continue
            case " ${ORCHESTRA_POWER_ROLES:-main aux} " in *" $r "*) echo "$r";; esac; done; }

pct_of() { case "$1" in eco) echo 70;; balanced) echo 85;; performance|default) echo 100;; *) echo ""; esac; }

# calc_limit <uuid> <percentuale>  → watt interi, limitati a [min,max]
calc_limit() {
    local def min max
    def=$(q "$1" power.default_limit); min=$(q "$1" power.min_limit); max=$(q "$1" power.max_limit)
    awk -v d="$def" -v p="$2" -v mn="$min" -v mx="$max" 'BEGIN{w=int(d*p/100+0.5); if(w<mn)w=mn; if(w>mx)w=mx; print w}'
}

# set_limit <ruolo> <uuid> <watt>  → 0 se ok; non fatale se la GPU non lo supporta (laptop)
set_limit() {
    local role="$1" uuid="$2" watt="$3" cur
    cur=$(q "$uuid" power.limit)
    if $SUDO nvidia-smi -i "$uuid" -pl "$watt" >/dev/null 2>&1; then
        echo "  ${role}: power limit ${cur} W → ${watt} W"; return 0
    fi
    echo "  ${role}: impossibile impostare ${watt} W (GPU laptop con limite bloccato, o privilegi mancanti: riprova con sudo)" >&2
    return 1
}

cmd_status() {
    printf '%-5s %-30s %8s %8s %8s %-6s %5s %6s %9s\n' ruolo gpu "W ora" "limite" "default" pstate "util%" "temp" "mem MiB"
    for r in $(roles); do u=$(uuid_of "$r")
        printf '%-5s %-30s %8s %8s %8s %-6s %5s %6s %9s\n' "$r" "$(q "$u" name | cut -c1-30)" \
            "$(q "$u" power.draw)" "$(q "$u" power.limit)" "$(q "$u" power.default_limit)" \
            "$(q "$u" pstate)" "$(q "$u" utilization.gpu)" "$(q "$u" temperature.gpu)" "$(q "$u" memory.used)"
    done
}

cmd_profile() {
    local p="${1:-}" pct; pct=$(pct_of "$p")
    [ -n "$pct" ] || { echo "Profilo sconosciuto '${p}': usa eco | balanced | performance"; return 2; }
    echo "Profilo ${p} (${pct}% del limite predefinito):"
    local r u w ov rc=0
    for r in $(roles); do u=$(uuid_of "$r")
        ov="ORCHESTRA_POWER_$(echo "$r" | tr a-z A-Z)_W"; w="${!ov:-}"
        [ -n "$w" ] || w=$(calc_limit "$u" "$pct")
        set_limit "$r" "$u" "$w" || rc=0   # la GPU non modificabile non e' un errore globale
    done
    return $rc
}

cmd_restore() { echo "Ripristino i limiti predefiniti:"; for r in $(roles); do u=$(uuid_of "$r"); set_limit "$r" "$u" "$(q "$u" power.default_limit)" || true; done; }

cmd_log() {
    local interval=5 count=0 n=0 r u ts
    while [ $# -gt 0 ]; do case "$1" in --interval) interval="$2"; shift 2;; --count) count="$2"; shift 2;; *) shift;; esac; done
    mkdir -p "$(dirname "$LOG_FILE")"
    [ -s "$LOG_FILE" ] || echo "timestamp,role,uuid,power_w,limit_w,util_pct,mem_used_mib,pstate,temp_c,interval_s" > "$LOG_FILE"
    echo "Campionamento ogni ${interval}s su ${LOG_FILE} (Ctrl-C per fermare)"
    while true; do
        ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        for r in $(roles); do u=$(uuid_of "$r")
            echo "${ts},${r},${u},$(q "$u" power.draw),$(q "$u" power.limit),$(q "$u" utilization.gpu),$(q "$u" memory.used),$(q "$u" pstate),$(q "$u" temperature.gpu),${interval}" >> "$LOG_FILE"
        done
        n=$((n+1)); [ "$count" -gt 0 ] && [ "$n" -ge "$count" ] && break
        sleep "$interval"
    done
}

cmd_report() {
    local f="${1:-$LOG_FILE}"
    [ -s "$f" ] || { echo "Nessun log in ${f}: esegui prima 'log'"; return 1; }
    awk -F, 'NR>1 { n[$2]++; s[$2]+=$4; wh[$2]+=$4*$10/3600; if($4>mx[$2])mx[$2]=$4; if(first==""||$1<first)first=$1; if($1>last)last=$1 }
      END { printf "Periodo: %s → %s\n%-6s %8s %10s %10s %12s\n","" first, last, "ruolo","campioni","media W","picco W","energia Wh";
            tot=0; for(r in n){ printf "%-6s %8d %10.1f %10.1f %12.3f\n", r, n[r], s[r]/n[r], mx[r], wh[r]; tot+=wh[r] }
            printf "TOTALE energia: %.3f Wh\n", tot }' "$f"
}

# media dei watt di un file di campioni (uno per riga)
mean_w() { awk '{s+=$1;n++} END{ if(n) printf "%.1f", s/n; else print 0 }' "$1"; }

cmd_bench() {
    local profiles=("$@"); [ ${#profiles[@]} -gt 0 ] || profiles=(current)
    local u; u=$(uuid_of main); [ -n "$u" ] || { echo "GPU main non assegnata"; return 1; }
    local orig; orig=$(q "$u" power.limit)
    echo "Benchmark: modello ${BENCH_MODEL} su ${OLLAMA_BENCH_URL}, ${BENCH_RUNS} esecuzioni per profilo (GPU main)"
    printf '%-12s %8s %9s %9s %10s\n' profilo "limite W" "tok/s" "media W" "tok/joule"
    # Ripristino garantito anche con Ctrl-C o errori. Le variabili sono espanse ORA (trap tra
    # doppi apici): $u e $orig sono locali e a fine funzione non esisterebbero piu'.
    trap "$SUDO nvidia-smi -i '$u' -pl '$orig' >/dev/null 2>&1" EXIT
    local p lim run tps_all samples out tps avg
    for p in "${profiles[@]}"; do
        # il benchmark misura la GPU main: non toccare l'aux
        [ "$p" = current ] || ORCHESTRA_POWER_ROLES=main cmd_profile "$p" >/dev/null 2>&1 || true
        lim=$(q "$u" power.limit); samples=$(mktemp); tps_all=$(mktemp)
        # riscaldamento: carica il modello (non misurato)
        curl -s "${OLLAMA_BENCH_URL}/api/generate" -d "{\"model\":\"${BENCH_MODEL}\",\"prompt\":\"ok\",\"stream\":false,\"options\":{\"num_predict\":1}}" >/dev/null
        for run in $(seq 1 "$BENCH_RUNS"); do
            q "$u" power.draw >> "$samples"
            out=$(curl -s "${OLLAMA_BENCH_URL}/api/generate" -d "{\"model\":\"${BENCH_MODEL}\",\"prompt\":\"${BENCH_PROMPT}\",\"stream\":false,\"options\":{\"num_predict\":256,\"temperature\":0}}")
            q "$u" power.draw >> "$samples"
            echo "$out" | python3 -c 'import sys,json
d=json.load(sys.stdin); print(d["eval_count"]/(d["eval_duration"]/1e9))' >> "$tps_all" 2>/dev/null
        done
        tps=$(sort -n "$tps_all" | awk '{a[NR]=$1} END{ if(NR) printf "%.1f", a[int((NR+1)/2)]; else print 0 }')
        avg=$(mean_w "$samples")
        printf '%-12s %8s %9s %9s %10s\n' "$p" "$lim" "$tps" "$avg" "$(awk -v t="$tps" -v w="$avg" 'BEGIN{ if(w>0) printf "%.3f", t/w; else print "n/d" }')"
        rm -f "$samples" "$tps_all"
    done
    $SUDO nvidia-smi -i "$u" -pl "$orig" >/dev/null 2>&1 && trap - EXIT
    echo "Limite originale (${orig} W) ripristinato."
}

case "${1:-status}" in
    status)  cmd_status ;;
    profile) shift; cmd_profile "$@" ;;
    restore) cmd_restore ;;
    log)     shift; cmd_log "$@" ;;
    report)  shift; cmd_report "$@" ;;
    bench)   shift; cmd_bench "$@" ;;
    *) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
```

## File: document-ai/scripts/orchestra_smoke_test.sh (6043 byte)

```
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
# Stesso contesto che usa il manifold (valve context_length): senza, Ollama usa il suo default, che con
# piu richieste parallele puo gonfiare la cache KV e spostare layer su CPU (misura non rappresentativa).
CTX="${ORCHESTRA_CONTEXT_LENGTH:-8192}"
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
    echo "4. Carico reale e isolamento della memoria (--load, contesto ${CTX})"
    echo "  - versione Ollama main: $(docker exec "$MAIN_C" ollama --version 2>/dev/null | tail -1)"
    load_check() { # <url> <modello> <uuid atteso> <uuid altra> <etichetta> <container>
        local url="$1" model="$2" want="$3" other="$4" label="$5" cont="$6" a0 b0 a1 b1
        a0=$(mem_used "$want"); b0=$(mem_used "$other")
        curl -sf "$url/api/generate" -d "{\"model\":\"$model\",\"prompt\":\"ok\",\"stream\":false,\"keep_alive\":60,\"options\":{\"num_ctx\":${CTX},\"num_predict\":4}}" >/dev/null
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
```

## File: document-ai/scripts/orchestra_sync.sh (7793 byte)

```
#!/bin/bash
# =====================================================================
# orchestra_sync.sh — allinea in modo SICURO la cartella locale al remoto (branch o tag)
#
# Uso (dalla cartella del repository):   bash document-ai/scripts/orchestra_sync.sh [riferimento]
#   riferimento   branch (default: dual-gpu-final) oppure tag (es. dual-gpu-rc2, pre-dual-gpu)
#   --repo DIR    repository da allineare (default: quello in cui ti trovi)
#   --no-tests    salta la suite di test finale
#
# Cosa fa, in ordine, SENZA perdere nulla:
#   1. salva un backup in ~/orchestra-backup-locale/<data>/: stato, modifiche, tutti i file locali
#      modificati o non tracciati e un bundle git con tutti i riferimenti locali;
#   2. annulla un merge/rebase/cherry-pick rimasto a meta (e' la causa tipica di un file con
#      marcatori <<<<<<< che sembra "corrotto"; il backup contiene lo stato con i marcatori);
#   3. mette da parte (stash) le modifiche locali ai file tracciati e sposta in backup i file non
#      tracciati che collidono con quelli del remoto; gli altri file locali NON vengono toccati
#      (copie in document-ai/system, .v01, orchestra.env, segreti...);
#   4. scarica il remoto e passa al riferimento richiesto (rifiuta se il branch locale ha commit
#      che il remoto non ha);
#   5. verifica: commit uguale al remoto, nessun marcatore di conflitto, sintassi di tutti gli
#      script bash/python, start_ai_stack.sh identico al remoto, permessi eseguibili, test.
# Non fa mai push, reset --hard o cancellazioni.
# =====================================================================
REF="dual-gpu-final"; RUN_TESTS=1; REPO=""
while [ $# -gt 0 ]; do case "$1" in
    --repo) REPO="$2"; shift 2;; --no-tests) RUN_TESTS=0; shift;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) REF="$1"; shift;; esac; done
if [ -n "$REPO" ]; then cd "$REPO" || { echo "cartella non trovata: $REPO"; exit 1; }; fi
TOP="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -n "$TOP" ] || { echo "Non sei in un repository git: spostati in ~/ai-sessioni oppure usa --repo"; exit 1; }
cd "$TOP" || exit 1
GD="$(git rev-parse --git-dir)"; TS="$(date +%Y%m%d_%H%M%S)"
BK="${ORCHESTRA_BACKUP_DIR:-$HOME/orchestra-backup-locale}/$TS"; mkdir -p "$BK"
FAIL=0
okc()  { echo "  ✔ $*"; }
badc() { echo "  ✘ $*"; FAIL=1; }

echo "== 1. Stato locale =="
echo "  repository: $TOP"
echo "  branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD 2>/dev/null)"
git status --short > "$BK/status.txt"; echo "  modifiche locali: $(wc -l < "$BK/status.txt") voci"
git diff HEAD > "$BK/tracked-changes.patch" 2>/dev/null
git bundle create "$BK/refs.bundle" --all >/dev/null 2>&1
git ls-files -m -o -u --exclude-standard | sort -u > "$BK/files.list"
if [ -s "$BK/files.list" ]; then tar czf "$BK/files.tgz" -T "$BK/files.list" 2>/dev/null; fi
echo "  backup: $BK"

echo "== 2. Operazioni rimaste a meta =="
INPROG=0
if [ -f "$GD/MERGE_HEAD" ];       then echo "  merge in corso: lo annullo (il backup contiene lo stato attuale)"; git merge --abort 2>/dev/null || git reset -q --merge; INPROG=1; fi
if [ -d "$GD/rebase-merge" ] || [ -d "$GD/rebase-apply" ]; then echo "  rebase in corso: lo annullo"; git rebase --abort 2>/dev/null; INPROG=1; fi
if [ -f "$GD/CHERRY_PICK_HEAD" ]; then echo "  cherry-pick in corso: lo annullo"; git cherry-pick --abort 2>/dev/null; INPROG=1; fi
[ $INPROG = 0 ] && echo "  nessuna"
if [ -n "$(git ls-files -u)" ]; then echo "  file ancora in conflitto: ripristino l'indice"; git reset -q; fi

echo "== 3. Modifiche locali ai file tracciati =="
if ! git diff --quiet HEAD 2>/dev/null; then
    git stash push -q -m "orchestra-sync $TS" && echo "  messe da parte: $(git stash list | head -1)"
    echo "  per rivederle:  git stash show -p stash@{0}     (non vengono riapplicate in automatico)"
else echo "  nessuna"; fi

echo "== 4. Allineamento a '$REF' =="
git fetch --all --tags --prune -q 2>&1 | sed 's/^/  /'
if git rev-parse -q --verify "refs/tags/$REF" >/dev/null; then KIND=tag; TARGET="refs/tags/$REF"
elif git rev-parse -q --verify "refs/remotes/origin/$REF" >/dev/null; then KIND=branch; TARGET="origin/$REF"
else echo "  riferimento sconosciuto: $REF"; echo "  branch: $(git branch -r | sed 's|origin/||;s/ //g;/HEAD/d' | tr '\n' ' ')"; echo "  tag: $(git tag | tr '\n' ' ')"; exit 1; fi
# Collisioni: file locali NON tracciati (anche quelli IGNORATI da .gitignore: git switch li sovrascrive in
# silenzio) che hanno lo stesso percorso di un file del remoto. Si parte dai percorsi della destinazione.
COLL="$BK/collisioni"; ncoll=0
while IFS= read -r f; do
    [ -f "$f" ] || continue
    git ls-files --error-unmatch -- "$f" >/dev/null 2>&1 && continue      # tracciato: ci pensano stash e switch
    mkdir -p "$COLL/$(dirname "$f")"; mv "$f" "$COLL/$f"; ncoll=$((ncoll+1)); echo "  spostato in backup (collideva col remoto): $f"
done < <(git ls-tree -r --name-only "$TARGET")
[ $ncoll = 0 ] && echo "  nessuna collisione di file non tracciati"
if [ "$KIND" = tag ]; then
    git switch -q --detach "$TARGET" && echo "  su tag $REF (detached, sola lettura)"
else
    if git show-ref -q --verify "refs/heads/$REF"; then
        git switch -q "$REF" || { echo "  switch fallito"; exit 1; }
        if ! git merge --ff-only -q "$TARGET" 2>/dev/null; then
            echo "  ✘ il branch locale '$REF' ha commit che il remoto non ha (o e' divergente): mi fermo."
            echo "    Nulla e' stato perso: $BK/refs.bundle contiene tutti i commit locali."
            echo "    Per ripartire dal remoto in un nuovo branch:  git switch -c ${REF}-allineato --track $TARGET"
            exit 1
        fi
    else
        git switch -q -c "$REF" --track "$TARGET" || { echo "  switch fallito"; exit 1; }
    fi
    echo "  su branch $REF = $TARGET"
fi

echo "== 5. Verifiche =="
[ "$(git rev-parse HEAD)" = "$(git rev-parse "$TARGET^{commit}")" ] && okc "commit locale = remoto ($(git rev-parse --short HEAD))" || badc "il commit locale non coincide col remoto"
if git grep -q -I -E '^(<<<<<<< |>>>>>>> )' -- ':!*.md' ':!AI_CTX_*' 2>/dev/null; then badc "marcatori di conflitto presenti:"; git grep -n -I -E '^(<<<<<<< |>>>>>>> )' -- ':!*.md' ':!AI_CTX_*' | head -5; else okc "nessun marcatore di conflitto nei file tracciati"; fi
bad=0; while IFS= read -r f; do bash -n "$f" 2>/dev/null || { badc "sintassi bash non valida: $f"; bad=1; }; done < <(git ls-files '*.sh')
[ $bad = 0 ] && okc "sintassi bash valida in tutti gli script ($(git ls-files '*.sh' | wc -l))"
if command -v python3 >/dev/null; then bad=0
    while IFS= read -r f; do python3 -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" "$f" 2>/dev/null || { badc "sintassi python non valida: $f"; bad=1; }; done < <(git ls-files '*.py')
    [ $bad = 0 ] && okc "sintassi python valida in tutti i file ($(git ls-files '*.py' | wc -l))"; fi
for L in start_ai_stack.sh start_comfyui.sh; do
    [ -f "$L" ] || continue
    [ "$(git hash-object "$L")" = "$(git rev-parse "$TARGET:$L")" ] && okc "$L identico al remoto ($(wc -l < "$L") righe)" || badc "$L diverso dal remoto"
    [ -x "$L" ] || { chmod +x "$L" && okc "$L reso eseguibile"; }
done
if [ "$RUN_TESTS" = 1 ] && [ -f tests/run_all.sh ]; then
    if python3 -c "import flask, pydantic, requests" 2>/dev/null; then
        res="$(bash tests/run_all.sh 2>&1 | tail -3)"; echo "$res" | grep -q "TUTTI I TEST OK" && okc "suite di test: TUTTI I TEST OK" || { badc "suite di test fallita:"; echo "$res" | sed 's/^/      /'; }
    else echo "  - test saltati: servono flask, pydantic, requests (pip install flask pydantic requests)"; fi
fi
echo; if [ $FAIL = 0 ]; then echo "ALLINEAMENTO OK — backup in $BK"; else echo "ALLINEAMENTO CON PROBLEMI — vedi sopra; backup in $BK"; fi
[ $FAIL = 0 ]
```

## File: document-ai/scripts/patch_required_models.sh (2121 byte)

```
#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║  ORCHESTRA     — start_ai_stack.sh                             ║
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

## File: document-ai/scripts/setup_security.sh (11133 byte)

```
#!/bin/bash
# ORCHESTRA — SECURITY SETUP v1.0
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
echo -e "\n${BOLD}Piano di sicurezza Orchestra${NC}"
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

