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
