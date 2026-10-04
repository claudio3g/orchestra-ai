#!/bin/bash
# start_comfyui.sh — avvio STANDALONE di ComfyUI (alternativa al passo 8 di start_ai_stack.sh)
#
# EGPU-05: ComfyUI viene fissato alla GPU del ruolo ORCHESTRA_COMFY_ROLE (main = 3090,
# aux = 4060; default main) tramite CUDA_VISIBLE_DEVICES=<UUID>. Sulla 3090 il VAE resta
# su GPU; sulle GPU piccole restano i flag storici (--lowvram --cpu-vae).
# Variabili opzionali: ORCHESTRA_COMFY_ROLE, ORCHESTRA_GPU_MAIN/AUX (UUID), COMFY_EXTRA_ARGS.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/orchestra.env" ] && . "$SCRIPT_DIR/orchestra.env"
if [ -f "$SCRIPT_DIR/document-ai/scripts/orchestra_gpu_env.sh" ]; then
    . "$SCRIPT_DIR/document-ai/scripts/orchestra_gpu_env.sh"
    detect_gpu_roles
    comfyui_gpu_setup --lowvram
else
    echo "[warn] libreria GPU non trovata: flag storici, GPU predefinita" >&2
    COMFY_MEM_ARGS=(--lowvram --cpu-vae)
fi

cd ~/ai-sessioni/ComfyUI
source venv/bin/activate
export OLLAMA_HOST=http://host.docker.internal:11435
# shellcheck disable=SC2086
python main.py \
  --listen 0.0.0.0 \
  --port 8188 \
  --force-fp16 \
  --dont-upcast-attention \
  "${COMFY_MEM_ARGS[@]}" \
  --preview-method auto ${COMFY_EXTRA_ARGS:-}
