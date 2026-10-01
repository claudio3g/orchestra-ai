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
