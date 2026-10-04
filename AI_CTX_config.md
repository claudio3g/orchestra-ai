# AI Context - Config

> Generato: 2026-10-04T00:36:16Z
> Branch: dual-gpu-final

---

## File: document-ai/config/Modelfile-blender (371 byte)

```
FROM qwen2.5-coder:14b-instruct

SYSTEM """
Sei un assistente tecnico specializzato in Blender 5 e Python.
Genera codice bpy completo, eseguibile, privo di errori.
Usa solo API ufficiali.
Nessuna fantasia.
Se il requisito è ambiguo, fai domande tecniche.
Output prioritario: codice pulito.
"""

PARAMETER temperature 0.1
PARAMETER top_p 0.9
PARAMETER repeat_penalty 1.1
```

## File: document-ai/config/docker-compose.yml (1193 byte)

```
# NOTA (dual-GPU): questo compose NON e' usato da start_ai_stack.sh, che crea i container con
# `docker run`. Se lo usi a mano, fissa Ollama a una GPU invece di `count: 1`:
#   deploy.resources.reservations.devices: [{driver: nvidia, device_ids: ["<UUID main>"], capabilities: [gpu]}]
# e non usare `--gpus all`. Il launcher e' la fonte di verita' (vedi document-ai/knowledge/ORCHESTRA_HANDOFF_v9.md).
services:
  ollama:
    image: ollama/ollama:latest
    container_name: ai-ollama-session
    volumes:
      - ollama-session:/root/.ollama
    ports:
      - "11435:11434"  # Porta diversa da ComfyUI
    restart: "no"  # Non riparte da solo
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1  # Solo 1 GPU, ComfyUI usa resto
              capabilities: [gpu]

  webui:
    image: ghcr.io/open-webui/open-webui:main
    container_name: ai-webui-session
    volumes:
      - webui-session:/app/backend/data
    ports:
      - "3001:8080"  # Porta diversa
    environment:
      - OLLAMA_BASE_URL=http://ai-ollama-session:11434
    depends_on:
      - ollama
    restart: "no"

volumes:
  ollama-session:
  webui-session:
```

## File: document-ai/config/docker_daemon.json (128 byte)

```
{
    "runtimes": {
        "nvidia": {
            "args": [],
            "path": "nvidia-container-runtime"
        }
    }
}```

## File: document-ai/config/orchestra.env.example (1522 byte)

```
# =====================================================================
# orchestra.env.example — valori locali per start_ai_stack.sh e start_comfyui.sh
# Copia in ~/ai-sessioni/orchestra.env (il file reale e' ignorato da git) e togli i commenti.
# Tutte le variabili sono OPZIONALI: senza di esse il launcher rileva le GPU da solo.
# =====================================================================

# --- Ruoli GPU (UUID da `nvidia-smi -L`; mai gli indici) ---
# Se omessi: main = GPU con piu' VRAM (3090), aux = la successiva (4060).
#ORCHESTRA_GPU_MAIN=GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
#ORCHESTRA_GPU_AUX=GPU-yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy

# --- Ripartizione dei carichi ---
# 0 = nessun Ollama sulla 4060 (variante "4060 solo per ComfyUI"); default 1 se esiste una GPU aux.
#ORCHESTRA_AUX_OLLAMA=1
# GPU su cui gira ComfyUI/SDXL: main (default) oppure aux.
#ORCHESTRA_COMFY_ROLE=main
# Argomenti aggiuntivi per ComfyUI (senza toccare gli script).
#COMFY_EXTRA_ARGS=

# --- Ollama ---
# Flash attention + cache KV compressa: dimezza la memoria del contesto. 0 per disattivare.
#ORCHESTRA_FLASH_ATTENTION=1
#ORCHESTRA_KV_CACHE_TYPE=q8_0

# --- Consumi (opzionale; richiede sudo senza password per nvidia-smi) ---
# Misura prima: bash document-ai/scripts/orchestra_power.sh bench eco balanced performance
#ORCHESTRA_POWER_PROFILE=balanced
#ORCHESTRA_POWER_MAIN_W=260      # override in watt per la 3090
#ORCHESTRA_POWER_AUX_W=          # la 4060 laptop di solito non consente di cambiare il limite
```

## File: document-ai/config/ufw_rules_export.txt (1935 byte)

```
Stato: attivo
Registrazione: on (low)
Predefinito: deny (in entrata), allow (in uscita), deny (instradato)
Nuovi profili: skip

A                          Azione      Da
-                          ------      --
6335/tcp                   ALLOW IN    172.17.0.0/16              # RAG Service - Docker bridge
6335/tcp                   ALLOW IN    172.19.0.0/16              # RAG Service - ollama_default
80/tcp                     ALLOW IN    Anywhere                   # HTTP - ACME challenge Let-Encrypt
443/tcp                    ALLOW IN    Anywhere                   # HTTPS - Caddy reverse proxy
3001                       DENY IN     Anywhere                   # Orchestra internal - block external
6333                       DENY IN     Anywhere                   # Orchestra internal - block external
9099                       DENY IN     Anywhere                   # Orchestra internal - block external
8188                       ALLOW IN    172.19.0.0/16              # ComfyUI - ollama_default
8188                       DENY IN     Anywhere                   # Orchestra internal - block external
6335                       DENY IN     Anywhere                   # Orchestra internal - block external
80/tcp (v6)                ALLOW IN    Anywhere (v6)              # HTTP - ACME challenge Let-Encrypt
443/tcp (v6)               ALLOW IN    Anywhere (v6)              # HTTPS - Caddy reverse proxy
3001 (v6)                  DENY IN     Anywhere (v6)              # Orchestra internal - block external
11435 (v6)                 DENY IN     Anywhere (v6)              # Orchestra internal - block external
6333 (v6)                  DENY IN     Anywhere (v6)              # Orchestra internal - block external
6335 (v6)                  DENY IN     Anywhere (v6)              # Orchestra internal - block external
8188 (v6)                  DENY IN     Anywhere (v6)              # Orchestra internal - block external

```

## File: document-ai/config/valves_ai_router.json (20 byte)

```
{"pipelines": ["*"]}```

## File: document-ai/config/valves_image_loop.json (2 byte)

```
{}```

## File: document-ai/config/valves_orchestra_manifold.example.json (926 byte)

```
{
  "ollama_url": "http://ai-ollama-session:11434",
  "rag_service_url": "http://host.docker.internal:6335",
  "model_quality": "qwen2.5-coder:14b-instruct-q4_K_M",
  "quality_num_gpu": 20,
  "model_fast": "qwen3.5:9b",
  "model_fallback": "llama3.1:8b",
  "model_coordinator": "llama3.2:3b",
  "model_vision_fallback": "llava:7b",
  "model_emergency": "llama3.2:3b",
  "ram_quality_min_mb": 8000,
  "ram_fast_min_mb": 4000,
  "vram_fast_min_mb": 7000,
  "vram_fallback_min_mb": 5000,
  "show_agent_header": true,
  "show_session_status": true,
  "strip_thinking_tags": true,
  "context_length": 8192,
  "ollama_timeout_s": 180,
  "dev_timeout_s": 300,
  "rag_timeout_s": 300,
  "deploy_token": "",
  "ollama_url_aux": "http://ai-ollama-aux-session:11434",
  "aux_models": "llama3.2:3b,moondream:v2,llava:7b",
  "aux_health_ttl_s": 20,
  "vram_quality_full_mb": 11000,
  "keep_alive_aux_s": 1800,
  "keep_alive_main_s": 900
}
```

## File: document-ai/config/valves_rag_filter.json (296 byte)

```
{"qdrant_url": "http://ai-qdrant-session:6333", "collection_name": "orchestra", "embed_model": "nomic-ai/nomic-embed-text-v1.5", "fastembed_cache": "/app/pipelines/.fastembed_cache", "top_k": 6, "min_score": 0.45, "max_context_chars": 3000, "enabled": true, "debug_log": true, "pipelines": ["*"]}```

