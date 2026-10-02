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
