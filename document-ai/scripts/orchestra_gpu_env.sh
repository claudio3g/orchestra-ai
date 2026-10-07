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
