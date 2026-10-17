"""
Pattern Logger — Orchestra
================================
Scrive eventi significativi in un file JSONL per analisi successive.
Utilizzato dal manifold per tracciare messaggi, comandi e fallback.
"""

import json
import os
from datetime import datetime
from pathlib import Path

# Percorso del file di log, configurabile via variabile d'ambiente
LOG_PATH = Path(os.environ.get("PATTERN_LOG_PATH", "/app/logs/patterns.jsonl"))


def log_event(event_type: str, data: dict) -> None:
    """
    Aggiunge un evento al file di log.
    
    Args:
        event_type: tipo di evento (es. "user_message", "command_repeat", 
                    "vision_fallback", "generate_command")
        data: dizionario con i dettagli dell'evento
    """
    try:
        # Crea la directory se non esiste
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

# In fondo a pattern_logger.py
class Pipeline:
    pass
