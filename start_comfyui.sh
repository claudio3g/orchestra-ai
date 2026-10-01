#!/bin/bash
cd ~/ai-sessioni/ComfyUI
source venv/bin/activate
export OLLAMA_HOST=http://host.docker.internal:11435
python main.py \
  --listen 0.0.0.0 \
  --port 8188 \
  --force-fp16 \
  --dont-upcast-attention \
  --lowvram \
  --preview-method auto \
  --cpu-vae
