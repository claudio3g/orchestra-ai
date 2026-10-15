#!/bin/bash
# Test di comfyui_gpu_setup (document-ai/scripts/orchestra_gpu_env.sh): flag di memoria passati a ComfyUI.
#
# PERCHE ESISTE (bug reale, trovato sul PC dell utente):
#   --normalvram e' stato RIMOSSO da ComfyUI (era il comportamento predefinito).
#   Verifica dell utente: `grep -n normalvram ComfyUI/comfy/cli_args.py` -> nessun risultato.
#   Se il launcher lo passa, argparse rifiuta l argomento e ComfyUI non parte.
#   Questi controlli devono FALLIRE con il codice precedente e passare dopo la correzione.
#
# COSA PROTEGGE IN PIU:
#   start_comfyui.sh chiama `comfyui_gpu_setup --lowvram`: l argomento opzionale deve restare
#   supportato per le GPU piccole (una prima correzione locale lo ignorava e perdeva --lowvram).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
LIB="$REPO/document-ai/scripts/orchestra_gpu_env.sh"; ok=1; n=0
pass() { n=$((n+1)); echo "PASS $*"; }; fail() { n=$((n+1)); echo "FAIL $*"; ok=0; }
chk()  { if eval "$2"; then pass "$1"; else fail "$1   [$2]"; fi; }

# Esegue comfyui_gpu_setup in una subshell con GPU finte e stampa COMFY_MEM_ARGS su una riga.
#   $1 = UUID della GPU del ruolo ("" = nessuna GPU assegnata)   $2 = VRAM totale in MiB
#   $3 = argomento opzionale (flag per GPU piccola), come fa start_comfyui.sh
args_for() {
  ( FAKE_UUID="$1"; FAKE_TOTAL="$2"; FAKE_FLAG="$3"
    . "$LIB"
    warn() { :; }; info() { :; }                      # definite dal launcher, non dalla libreria
    gpu_for_role() { printf '%s' "$FAKE_UUID"; }      # sostituisce la lettura reale di nvidia-smi
    gpu_total_mb() { printf '%s' "$FAKE_TOTAL"; }
    unset ORCHESTRA_COMFY_ROLE
    if [ -n "$FAKE_FLAG" ]; then comfyui_gpu_setup "$FAKE_FLAG"; else comfyui_gpu_setup; fi
    echo "${COMFY_MEM_ARGS[*]}" )
}

echo "== GPU grande (3090, 24576 MiB): nessun flag di memoria, VAE su GPU"
out="$(args_for GPU-3090-UUID 24576 "")"
chk "3090: nessun flag di memoria"                '[ -z "$out" ]'
out="$(args_for GPU-3090-UUID 24576 --lowvram)"
chk "3090: ignora il flag per GPU piccole"        '[ -z "$out" ]'

echo "== GPU piccola (4060, 8188 MiB): VAE su CPU, flag opzionale mantenuto"
out="$(args_for GPU-4060-UUID 8188 "")"
chk "4060 senza argomento: solo --cpu-vae"        '[ "$out" = "--cpu-vae" ]'
out="$(args_for GPU-4060-UUID 8188 --lowvram)"
chk "4060 con --lowvram (start_comfyui.sh): --lowvram --cpu-vae"  '[ "$out" = "--lowvram --cpu-vae" ]'

echo "== nessuna GPU assegnata: flag storici prudenti"
out="$(args_for "" "" "")"
chk "nessuna GPU senza argomento: solo --cpu-vae"  '[ "$out" = "--cpu-vae" ]'
out="$(args_for "" "" --lowvram)"
chk "nessuna GPU con --lowvram: --lowvram --cpu-vae" '[ "$out" = "--lowvram --cpu-vae" ]'

echo "== --normalvram non deve mai essere passato"
all="$(args_for GPU-3090-UUID 24576 ""; args_for GPU-4060-UUID 8188 ""; args_for "" "" "")"
chk "nessun caso produce --normalvram"            '! echo "$all" | grep -q -- "--normalvram"'
# Guardia statica: nessuna riga di CODICE (commenti esclusi) deve contenere --normalvram.
chk "nessuna riga di codice con --normalvram"     '! grep -nE "^[^#]*--normalvram" "$REPO/start_ai_stack.sh" "$REPO/start_comfyui.sh" "$LIB"'

echo; echo "$n controlli"; [ $ok = 1 ] && echo "COMFY FLAGS ALL OK" || echo "COMFY FLAGS FAILED"; [ $ok = 1 ]
