"""
Pattern Logger per Orchestra
Traccia eventi significativi per analisi proattiva.
Salva in ~/ai-sessioni/logs/patterns.jsonl
"""

import json
import os
from datetime import datetime
from pathlib import Path

LOG_PATH = Path(os.environ.get("PATTERN_LOG_PATH", str(Path.home() / "ai-sessioni/logs/patterns.jsonl")))

def log_event(event_type: str, data: dict):
    """Aggiunge una riga JSON al log."""
    try:
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
