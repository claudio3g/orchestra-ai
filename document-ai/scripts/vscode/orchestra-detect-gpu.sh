#!/bin/bash
# orchestra-detect-gpu.sh
# Rileva le GPU NVIDIA presenti e determina la modalità di Orchestra.
#
# Uscita:
#   exit 0 → MODALITÀ FULL (doppia GPU: 3090 + 4060)
#   exit 1 → MODALITÀ NORMAL (singola GPU: 4060)
#   exit 2 → ERRORE (nvidia-smi non disponibile)

set -u

if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "ERRORE: nvidia-smi non trovato nel PATH" >&2
    exit 2
fi

GPU_COUNT=$(nvidia-smi -L 2>/dev/null | wc -l)
GPU_LIST=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null)

echo "GPU rilevate: $GPU_COUNT"
echo "$GPU_LIST" | while read -r name; do
    echo "  - $name"
done

if [ "$GPU_COUNT" -ge 2 ]; then
    echo "MODALITÀ: FULL (doppia GPU)"
    exit 0
else
    echo "MODALITÀ: NORMAL (singola GPU)"
    exit 1
fi
