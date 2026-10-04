"""
Orchestra Manifold v3.9.0 — Orchestra dual-GPU
=============================================

CHANGELOG v3.9.0 rispetto a v3.8.2 (RTX 3090 eGPU "main" + RTX 4060 "aux"):
  EGPU-03 Backend per ruolo: valves ollama_url_aux / aux_models. I modelli elencati in
          aux_models (coordinator, vision) vanno sull'Ollama della 4060; tutto il resto
          sul main (3090). Se l'aux non risponde il manifold ricade sul main (che ha
          tutti i modelli) senza errori. Con ollama_url_aux vuoto nulla cambia.
  EGPU-04 Modello quality interamente in GPU quando la VRAM libera del main e'
          >= vram_quality_full_mb (11000): niente piu' num_gpu=20 con offload su CPU.
          Con GPU piccole (VRAM < soglia) il comportamento 8 GB resta identico.
  EGPU-04 keep_alive per ruolo: i modelli aux restano caricati (keep_alive_aux_s) e,
          con VRAM abbondante sul main, i modelli di testo restano caricati
          (keep_alive_main_s) evitando i ricaricamenti lenti sul link Thunderbolt.
  EGPU-04 Vision: la soglia usa la VRAM libera della GPU aux se la vision gira li'.
  EGPU-04 Fallback subprocess VRAM: legge solo la prima riga / GPU main (con 2 GPU
          nvidia-smi stampa piu' righe e int() falliva).
  Prompt agenti e banner aggiornati all'hardware dual-GPU.


CHANGELOG v3.8.2 rispetto a v3.8.1:
  FIX-1  num_predict esplicito in stream_ollama (default era 512 token = 380 parole).
         Aggiunto Valve num_predict_default=2048 e num_predict_dev=4096.
         orchestra_dev usa num_predict_dev per analisi codice più lunghe.

  FIX-2  Prompt coordinator: rimosso "conciso" hardcoded, sostituito con istruzione
         adattiva — sintetico per domande semplici, esaustivo per quelle complesse.

  FIX-3  Tutti i prompt specialisti arricchiti con istruzione COMPLETEZZA esplicita:
         ogni agente sa che non deve troncare risposte a metà e deve completare
         l'analisi/procedura/codice richiesto.

  FIX-5  _KEEP_ALIVE esteso: llama3.1:8b (300s) e qwen3.5:9b (300s) aggiunti.
         Riduce la latenza di ricarica del ~40% su sessioni con richieste ravvicinate.

CHANGELOG v3.8.1 rispetto a v3.8.0:
  BUG-C  pipes() aggiunto — manifold ora visibile in OpenWebUI.
  BUG-E  is_coordinator corretto in branch force_fast.
"""

import copy
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Union

import requests
from pydantic import BaseModel

sys.path.insert(0, "/app/pipelines")

try:
    from embedding_utils import embed_for_routing, get_routing_embedding_model, get_vram_free_mb as _daemon_vram_free_mb
    _VRAM_DAEMON_AVAILABLE = True
except ImportError:
    embed_for_routing           = None
    get_routing_embedding_model = None
    _VRAM_DAEMON_AVAILABLE      = False

try:
    # EGPU-03: VRAM per ruolo (main/aux). Assente con un embedding_utils vecchio.
    from embedding_utils import get_gpu_free_mb as _daemon_gpu_free_mb
except ImportError:
    _daemon_gpu_free_mb = None

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct
    _QDRANT_AVAILABLE = True
except ImportError:
    _QDRANT_AVAILABLE = False

try:
    from pattern_logger import log_event
except ImportError:
    def log_event(*args, **kwargs): pass

PATTERN_LOG_PATH = Path(os.environ.get(
    "PATTERN_LOG_PATH",
    str(Path.home() / "ai-sessioni/logs/patterns.jsonl")
))

# ── Stato globale ─────────────────────────────────────────────────────────────
_last_user_message: dict = {}
_LAST_MSG_TTL_S         = 3600   # TTL entry per utente: 1 ora
_last_cleanup_time      = 0.0    # FIX-03: timestamp ultima pulizia time-based
_CLEANUP_INTERVAL_S     = 600    # FIX-03: pulizia almeno ogni 10 minuti

_sdxl_lock         = threading.Lock()
_routing_init_lock = threading.Lock()

_KEEP_ALIVE: dict[str, int] = {
    "llama3.2:3b": 600,    # coordinator — sempre in standby, ~2 GB VRAM
    "llama3.1:8b": 300,    # fallback parziale — 5 minuti tra richieste
    "qwen3.5:9b":  300,    # fast — 5 minuti, evita reload su sessioni attive
}
_KEEP_ALIVE_DEFAULT = 0


def _log(tag: str, msg: str, user_id: str = "") -> None:
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uid = f" user={user_id}" if user_id else ""
    print(f"[{ts}] [{tag}]{uid} {msg}", flush=True)


def _debug_log(condition: bool, *args, **kwargs) -> None:
    """Stampa solo se la condizione è True (tipicamente: self.valves.debug_log)."""
    if condition:
        print(*args, **kwargs)


def _cleanup_last_messages() -> None:
    """Rimuove entry più vecchie di _LAST_MSG_TTL_S secondi."""
    global _last_cleanup_time
    now    = time.time()
    to_del = [uid for uid, v in _last_user_message.items()
              if (now - v.get("time", 0)) > _LAST_MSG_TTL_S]
    for uid in to_del:
        _last_user_message.pop(uid, None)
    _last_cleanup_time = now
    if to_del:
        _log("ORCHESTRA", f"Cleanup _last_user_message: rimossi {len(to_del)} entry")


_RAG_INSTRUCTIONS = (
    "\n\n📚 **Uso della knowledge base:**\n"
    "- Se vedi la sezione '📚 CONTESTO DALLA KNOWLEDGE BASE', utilizza "
    "ESCLUSIVAMENTE quelle informazioni per rispondere. Cita il file sorgente.\n"
    "- Se invece la sezione '📚 CONTESTO DALLA KNOWLEDGE BASE' NON è presente, "
    "NON fornire dati tecnici o numerici specifici. Rispondi in modo generico oppure "
    "dichiara esplicitamente: \"Non ho dati verificati su questo argomento.\"\n"
    "- Se ti viene chiesto di mostrare codice e il contesto lo contiene, copialo fedelmente. "
    "Se non lo contiene, dillo."
)

PROMPTS: dict[str, str] = {
    "coordinator": (
        "Sei Orchestra, un assistente AI intelligente e polivalente. "
        "Calibra la lunghezza della risposta alla complessità della domanda: "
        "rispondi in modo sintetico per domande semplici, esaustivo e dettagliato "
        "per domande tecniche, analitiche o che richiedono spiegazioni approfondite. "
        "Usa markdown (titoli, elenchi, blocchi codice) quando migliora la leggibilità. "
        "Rispondi SEMPRE nella stessa lingua dell'utente. "
        "Non troncare mai una risposta a metà: se stai elencando passi o analizzando "
        "un problema, completa sempre l'analisi prima di fermarti."
        + _RAG_INSTRUCTIONS
    ),
    "linux_admin": (
        "Sei LINUX_ADMIN, esperto senior di sistemi Linux, Ubuntu 24.04, "
        "Docker, driver NVIDIA/CUDA, hardening Linux e amministrazione di sistema.\n\n"
        "PRIORITÀ OPERATIVE:\n"
        "1) Ottimizzazione e performance\n"
        "2) Semplicità, leggibilità e manutenzione\n"
        "3) Cybersicurezza e hardening\n\n"
        "Linee guida:\n"
        "- Fornisci sempre soluzioni ottimizzate per stabilità, consumo risorse, "
        "performance e compatibilità.\n"
        "- Preferisci approcci semplici, modulari e facilmente manutenibili.\n"
        "- Inserisci commenti chiari e sintetici nei file di configurazione, script "
        "e comandi complessi.\n"
        "- Spiega brevemente il motivo tecnico delle scelte effettuate.\n"
        "- Rispondi sempre nella lingua dell'utente.\n"
        "- Usa blocchi ```bash per tutti i comandi shell.\n"
        "- Evidenzia chiaramente eventuali comandi distruttivi, rischiosi o irreversibili.\n"
        "- Prima di modifiche critiche, suggerisci backup, snapshot o rollback.\n"
        "- Applica principi di cybersicurezza by default:\n"
        "  * minimo privilegio, riduzione superficie di attacco\n"
        "  * firewall e isolamento servizi, permessi minimi\n"
        "  * validazione input, repository e immagini affidabili\n"
        "  * aggiornamenti di sicurezza, protezione credenziali\n"
        "  * logging e auditing essenziali\n"
        "- Per Docker: immagini leggere e ufficiali, no container privilegiati,\n"
        "  porte e privilegi minimi, volumi e network sicuri, limiti CPU/RAM.\n"
        "- Per NVIDIA/CUDA: verifica compatibilità driver, evita installazioni\n"
        "  ridondanti, ottimizza utilizzo GPU e memoria video.\n\n"
        "COMPLETEZZA: fornisci sempre la risposta completa. Se la soluzione richiede "
        "più passi, elencali tutti. Non fermarti al primo comando — includi verifica, "
        "troubleshooting e casi limite rilevanti. Non troncare mai a metà una procedura.\n"
        + _RAG_INSTRUCTIONS
    ),
    "ml_engineer": (
        "Sei ML_ENGINEER, esperto di LLM, quantizzazione, VRAM, Ollama.\n\n"
        "Hardware: RTX 3090 24GB (eGPU, main) + RTX 4060 Laptop 8GB (aux) | i9-13900HX 32T | 32GB RAM | Ubuntu 24.04\n\n"
        "Linee guida:\n"
        "- Analisi sempre con numeri precisi (GB VRAM, token/s, parametri).\n"
        "- Compara sempre le opzioni disponibili con pro/contro espliciti.\n"
        "- Considera i vincoli hardware reali prima di suggerire un modello o configurazione.\n"
        "- Rispondi nella lingua dell'utente.\n\n"
        "COMPLETEZZA: quando confronti modelli o configurazioni, includi la tabella "
        "comparativa completa. Quando spieghi una tecnica (quantizzazione, RAG, fine-tuning), "
        "fornisci il quadro teorico E l'applicazione pratica. Non fermarti alla definizione.\n"
        + _RAG_INSTRUCTIONS
    ),
    "comfy_integrator": (
        "Sei COMFY_INTEGRATOR, esperto di ComfyUI, API REST, Python e integrazione AI.\n\n"
        "Sistema: ComfyUI host:8188 | Ollama Docker:11434(int)/11435(host) | "
        "OpenWebUI Docker:3001 | Pipelines Docker:9099\n\n"
        "Linee guida:\n"
        "- Codice Python in blocchi ```python, JSON workflow in ```json.\n"
        "- Anticipa gli errori comuni e includi la gestione delle eccezioni.\n"
        "- Spiega il perché delle scelte architetturali, non solo il come.\n"
        "- Rispondi nella lingua dell'utente.\n\n"
        "COMPLETEZZA: quando fornisci codice, includilo sempre completo e funzionante — "
        "niente snippet incompleti con '# resto del codice'. Se il workflow è complesso, "
        "spiega ogni nodo rilevante. Includi sempre le istruzioni di test/verifica.\n"
        + _RAG_INSTRUCTIONS
    ),
    "design_critic": (
        "Sei DESIGN_CRITIC, esperto di analisi visiva per immagini AI (SDXL).\n\n"
        "Per ogni immagine analizza in modo completo e strutturato:\n"
        "1. Composizione e bilanciamento visivo\n"
        "2. Qualità tecnica (nitidezza, rumore, artefatti)\n"
        "3. Fedeltà al prompt originale\n"
        "4. Punti di forza specifici\n"
        "5. Debolezze specifiche con spiegazione\n"
        "6. Punteggio 1-10 con motivazione\n"
        "7. Prompt migliorato concreto (non generico)\n\n"
        "COMPLETEZZA: non saltare nessuno dei 7 punti. Il prompt migliorato deve essere "
        "un testo SDXL completo e utilizzabile immediatamente, non una lista di suggerimenti.\n"
        "Rispondi nella lingua dell'utente.\n"
        + _RAG_INSTRUCTIONS
    ),
    "orchestra_dev": (
        "Sei ORCHESTRA_DEV, esperto dello stack Orchestra e del suo codice sorgente.\n\n"
        "Stack: OpenWebUI → Pipelines (manifold/filter/pipe) → Ollama → Qdrant | "
        "ComfyUI host:8188 | rag_service host:6335 | Ubuntu 24.04 | RTX 3090 24GB (main) + RTX 4060 8GB (aux)\n\n"
        "Linee guida:\n"
        "- Prima di proporre modifiche: identifica tutti i call site del simbolo coinvolto.\n"
        "- Changelog-first: descrivi cosa cambia PRIMA di scrivere codice.\n"
        "- Ogni patch deve essere validabile con ast.parse().\n"
        "- Considera sempre i vincoli VRAM (24GB main, 8GB aux) e la stabilità del sistema in produzione.\n"
        "- Snippet di codice sempre completi, mai troncati con '...' o '# resto'.\n"
        "- Rispondi nella lingua dell'utente.\n\n"
        "COMPLETEZZA: quando analizzi un bug, fornisci: causa root → tutti i call site "
        "impattati → fix completo → validazione. Quando mostri codice sorgente richiesto, "
        "mostralo INTEGRALMENTE senza omissioni. Non interrompere un'analisi a metà.\n"
        + _RAG_INSTRUCTIONS
    ),
    "github_integrator": (
        "Sei GITHUB_INTEGRATOR, esperto di GitHub e automazione.\n"
        "Puoi interagire con repository GitHub tramite API.\n"
        "Usa i comandi /github per eseguire operazioni.\n"
        "Rispondi in modo chiaro, mostrando i comandi e i risultati.\n"
        "Non condividere il token di accesso."
        + _RAG_INSTRUCTIONS
    ),
    "reasoner": (
        "Sei REASONER, un agente specializzato nel ragionamento logico formale, "
        "nell'analisi algoritmica e nel debugging profondo di codice Python.\n\n"
        "Il tuo metodo di lavoro è SEMPRE strutturato in fasi esplicite:\n\n"
        "## FASE 1 — COMPRENSIONE\n"
        "- Riformula il problema con parole tue per verificare di averlo capito.\n"
        "- Identifica: input, output atteso, vincoli, casi limite.\n"
        "- Se il problema è ambiguo, elenca le interpretazioni e scegli quella più probabile.\n\n"
        "## FASE 2 — ANALISI\n"
        "- Scomponi il problema nelle sue componenti elementari.\n"
        "- Per codice: traccia il flusso di esecuzione passo per passo con valori concreti.\n"
        "- Per algoritmi: calcola la complessità temporale e spaziale esplicitamente.\n"
        "- Per bug logici: costruisci un caso di test che riproduce il problema.\n"
        "- Per problemi di concorrenza: disegna la sequenza di eventi che porta al problema.\n\n"
        "## FASE 3 — SOLUZIONE\n"
        "- Proponi la soluzione ottimale con motivazione esplicita.\n"
        "- Se esistono alternative, confrontale con pro/contro concreti.\n"
        "- Per codice Python: usa typing, docstring, gestione eccezioni.\n"
        "- Includi la dimostrazione di correttezza o il ragionamento che la supporta.\n\n"
        "## FASE 4 — VERIFICA\n"
        "- Testa la soluzione mentalmente su almeno 3 casi: normale, limite, estremo.\n"
        "- Verifica che tutti i casi limite identificati nella FASE 1 siano gestiti.\n"
        "- Se trovi un problema, torna alla FASE 2 e documentalo.\n\n"
        "REGOLE FERME:\n"
        "- Non dare mai una risposta prima di aver completato almeno le FASI 1 e 2.\n"
        "- Non semplificare artificialmente: se il problema è complesso, la risposta lo è.\n"
        "- Usa esempi numerici concreti, mai solo definizioni astratte.\n"
        "- Se non sei certo di qualcosa, dillo esplicitamente invece di inventare.\n"
        "- Codice sempre completo, compilabile, con i casi limite gestiti.\n"
        "- Rispondi nella lingua dell'utente.\n"
        + _RAG_INSTRUCTIONS
    ),
}

AGENT_EXAMPLES: dict[str, list[str]] = {
    "linux_admin": [
        "come installo i driver nvidia?",
        "che comando per vedere la RAM libera?",
        "docker compose up non funziona",
        "come faccio a montare un disco?",
        "mostrami i permessi di un file",
        "apt update failed",
        "nvidia-smi command not found",
        "how to check ubuntu version",
        "crontab schedule every 5 minutes",
        "configure ufw firewall rules",
    ],
    "ml_engineer": [
        "quale modello LLM per 8 GB di VRAM?",
        "differenza tra quantizzazione q4 e q8",
        "quanto occupa qwen3.5:9b in VRAM?",
        "come fine-tunare llama3 su una singola GPU?",
        "cos'è il context length?",
        "out of memory durante l'inferenza",
        "stable diffusion quante immagini posso generare?",
        "gguf vs safetensors performance",
        "checkpoint sdxl base quanto pesa",
        "temperature and top_p explanation",
    ],
    "comfy_integrator": [
        "come creo un workflow in ComfyUI?",
        "errore timeout nella API di ComfyUI",
        "come usare LCM-LoRA con ComfyUI?",
        "script python per inviare un prompt a ComfyUI",
        "KSampler scheduler quale usare?",
        "checkpoint loader not found",
        "come collegare i nodi in ComfyUI?",
        "workflow json example",
        "comfyui api /history endpoint",
        "lora loader strength parameter",
    ],
    "design_critic": [
        "analizza questa immagine generata",
        "cosa ne pensi di questa foto?",
        "valuta la qualità di questa immagine",
        "come posso migliorare il prompt per ottenere un paesaggio migliore?",
        "immagine sfocata, cosa posso fare?",
        "describe this image",
        "image analysis feedback",
        "valutami questa composizione fotografica",
        "questa immagine ha un buon bilanciamento?",
        "suggerisci un prompt migliore per un ritratto",
    ],
    "orchestra_dev": [
        # analisi e refactoring
        "migliora questo codice",
        "analizza il file orchestra_manifold.py",
        "proponi una modifica per image_loop.py",
        "trova bug in rag_filter.py",
        "refactoring di questa funzione",
        "verifica la sicurezza dell'endpoint /deploy",
        "suggerisci miglioramenti al routing",
        "come aggiungere un nuovo agente?",
        # ispezione codice — FIX-1: queste query mancavano e cadevano su coordinator
        "mostrami la funzione route_text",
        "mostrami il codice di pipes()",
        "fai vedere stream_ollama",
        "come è implementata embed_for_routing?",
        "mostrami __init__ di Pipeline",
        "visualizza la funzione vram_free_mb",
        "mostra la funzione handle_generate",
        "come funziona _cached_embed?",
        "mostra la funzione chunk_python_file",
        "mostrami il codice di inlet()",
        # domande architetturali
        "come posso ottimizzare la gestione della VRAM?",
        "spiegami il flusso di /generate",
        "come funziona il daemon VRAM?",
        "spiega la logica di routing del manifold",
        "come funziona il chunking AST?",
    ],
    "coordinator": [
        "ciao", "come stai?", "grazie", "che ore sono?",
        "raccontami una barzelletta", "cosa sai fare?", "chi sei?",
        "spiegami la relatività", "tradurre una parola in inglese",
        "riassumi questo testo",
    ],
    "reasoner": [
        # ragionamento algoritmico
        "dimostrami perché questo algoritmo è O(n log n)",
        "qual è la complessità di questa funzione ricorsiva?",
        "ottimizza questo codice per ridurre la complessità temporale",
        "spiega passo per passo questo problema di programmazione dinamica",
        "qual è il modo più efficiente per risolvere questo problema?",
        # debugging logico profondo
        "trova il bug logico in questa funzione",
        "analizza i casi limite di questa funzione",
        "questo codice è thread-safe? dimostralo",
        "perché questo deadlock si verifica?",
        "trova tutti i possibili percorsi di errore in questo codice",
        "questo algoritmo è corretto? dimostra perché",
        "perché questa funzione ricorsiva non termina?",
        # analisi formale
        "dimostra la correttezza di questa soluzione",
        "questo codice ha race condition?",
        "analizza questo problema step by step",
        "ragiona su questo problema logico",
        "verifica formalmente questa implementazione",
        # python avanzato
        "spiega il comportamento di questo generatore Python",
        "perché questo decorator non funziona come previsto?",
        "analizza il memory leak in questo codice",
        "questo codice Python è corretto? analizza ogni caso",
        "perché questo metaclasse si comporta così?",
        # problemi matematici/logici
        "risolvi questo problema di logica passo per passo",
        "dimostra questa proprietà matematica",
        "analizza questo problema combinatorio",
    ],
    "github_integrator": [
        "come creo un issue su GitHub?",
        "leggi il file README del repository",
        "fai un commit su GitHub",
        "crea una pull request",
        "elenca i miei repository",
        "aggiungi un commento alla issue #12",
        "aggiorna il file config.json su GitHub",
        "cosa c'è nel file .github/workflows",
        "crea un nuovo repository su GitHub",
        "elimina un branch su GitHub",
        "github api limit",
        "come gestisco le issue su github",
    ],
}

ROUTING_COLLECTION           = "orchestra_routing"
ROUTING_VECTOR_DIM           = 384
ROUTING_SIMILARITY_THRESHOLD = 0.45

_NO_RAG_WARNING = (
    "\n\n⚠️ **Nessuna fonte verificata disponibile.**\n"
    "Non fornire dati tecnici specifici. Se necessario, "
    "ammetti che non hai informazioni verificate sull'argomento."
)

RAG_MARKER = "📚 CONTESTO DALLA KNOWLEDGE BASE"


class Pipeline:

    class Valves(BaseModel):
        model_config = {"protected_namespaces": ()}

        ollama_url:             str   = "http://ai-ollama-session:11434"
        rag_service_url:        str   = "http://172.19.0.1:6335"
        qdrant_url:             str   = "http://ai-qdrant-session:6333"
        model_quality:          str   = "qwen2.5-coder:14b-instruct-q4_K_M"
        quality_num_gpu:        int   = 20
        model_fast:             str   = "qwen3.5:9b"
        model_fallback:         str   = "llama3.1:8b"
        model_coordinator:      str   = "llama3.2:3b"
        model_vision:           str   = "llava:7b"
        model_vision_fallback:  str   = "moondream:v2"
        model_emergency:        str   = "llama3.2:3b"

        ram_quality_min_mb:        int   = 8000
        ram_fast_min_mb:           int   = 4000
        vram_fast_min_mb:          int   = 7000
        vram_fallback_min_mb:      int   = 5500
        # Soglia per offload parziale di llama3.1:8b con num_gpu ridotto.
        # Con ~3.5 GB VRAM, 22/32 layer in GPU + resto in RAM (~4 GB RAM).
        # Meglio di llama3.2:3b per query di codice con RAG context.
        vram_partial_fallback_mb:  int   = 3200
        fallback_partial_num_gpu:  int   = 22
        vram_vision_full_mb:    int   = 5000
        vram_vision_partial_mb: int   = 3000

        # EGPU-03/04: ruoli GPU. ollama_url_aux vuoto = Ollama aux disattivato
        # (comportamento storico). Il launcher lo passa via env OLLAMA_AUX_URL.
        ollama_url_aux:         str   = os.environ.get("OLLAMA_AUX_URL", "")
        # Modelli serviti dall'aux (4060): coordinator + vision. CSV.
        aux_models:             str   = "llama3.2:3b,moondream:v2,llava:7b"
        aux_health_ttl_s:       int   = 20      # cache del controllo di salute dell'aux
        # VRAM libera del main oltre la quale il modello quality sta tutto in GPU.
        # 14B Q4 ~9 GB + contesto: 11000 lascia margine. Sotto soglia: num_gpu parziale.
        vram_quality_full_mb:   int   = 11000
        keep_alive_aux_s:       int   = 1800    # modelli aux (coordinator) sempre pronti
        keep_alive_main_s:      int   = 900     # modelli di testo sul main con VRAM abbondante

        show_agent_header:      bool  = True
        show_session_status:    bool  = True
        strip_thinking_tags:    bool  = True
        # Se True, mostra i titoli dei passi di ragionamento estratti dai <think>
        # tag di qwen3.5:9b. Utile per debug e per apprezzare il processo mentale.
        show_thinking:          bool  = False
        debug_log:              bool  = False

        context_length:         int   = 8192
        # Token di output massimi per categoria di task.
        # Default Ollama senza parametro: ~512 token (≈380 parole) — insufficiente.
        num_predict_default:    int   = 2048   # chat/coordinator ≈1500 parole
        num_predict_dev:        int   = 4096   # orchestra_dev/analisi codice ≈3000 parole
        # Per REASONER: il modello genera prima i <think> (300-1500 token),
        # poi la risposta. Budget alto garantisce output completo dopo il thinking.
        num_predict_reasoning:  int   = 6144   # REASONER/logica ≈4500 parole nette

        # Temperatura per agente: più bassa = più determinismo logico.
        # Ollama default: 0.8 — troppo alta per ragionamento e codice.
        temperature_coordinator:  float = 0.70  # chat: leggera varianza
        temperature_technical:    float = 0.30  # linux/ml/comfy: comandi precisi
        temperature_code:         float = 0.20  # orchestra_dev: codice deterministico
        temperature_reasoning:    float = 0.15  # REASONER: logica formale
        temperature_creative:     float = 0.60  # design_critic: varianza estetica

        ollama_timeout_s:       int   = 180
        dev_timeout_s:          int   = 300
        reasoning_timeout_s:    int   = 420    # REASONER: thinking può essere lungo
        vision_timeout_s:       int   = 240
        rag_timeout_s:          int   = 300

        routing_similarity_threshold: float = 0.45
        github_token:          str   = ""   # Token GitHub (se vuoto, usa env GITHUB_TOKEN)

    _TECHNICAL_KEYWORDS = (
        "hardware", "scheda video", "gpu", "cpu", "processore", "ram", "vram",
        "memoria", "driver", "versione", "modello", "specifiche", "configurazione",
        "nvidia", "amd", "intel", "ryzen", "core i", "geforce", "rtx", "gtx",
        "ubuntu", "kernel", "docker", "comfyui", "ollama", "qdrant", "pipeline",
        "manifold", "filter", "embedding", "routing", "chunk", "collection",
    )

    def __init__(self):
        self.type      = "manifold"
        self.name      = "Orchestra"
        self.valves    = self.Valves()
        self.pipelines = [{"id": "orchestra", "name": "🎼 Orchestra — AI Coordinator"}]
        self._image_loop_instance = None
        self._evolver_instance    = None
        self._qdrant: Optional[QdrantClient] = None
        self._routing_ready       = False
        self._aux_healthy         = False   # EGPU-03: stato cache dell'Ollama aux
        self._aux_checked_until   = 0.0

    def pipes(self) -> list[dict]:
        """
        BUG-C FIX: metodo richiesto dal framework OpenWebUI/Pipelines per
        enumerare i modelli esposti da questo manifold.
        Senza pipes() il manifold viene caricato ma nessun modello appare
        nella lista di OpenWebUI.
        Ritorna la stessa lista di self.pipelines per coerenza.
        """
        return self.pipelines

    # =========================================================================
    # LAZY LOADER
    # =========================================================================

    def _load_module_instance(self, filename: str, cache_attr: str):
        if getattr(self, cache_attr) is not None:
            return getattr(self, cache_attr)
        try:
            this_dir   = os.path.dirname(os.path.abspath(__file__))
            candidates = [os.path.join(this_dir, filename), f"/app/pipelines/{filename}"]
            path = next((p for p in candidates if os.path.isfile(p)), None)
            if not path:
                _log("ORCHESTRA", f"{filename} non trovato")
                return None
            spec = importlib.util.spec_from_file_location(filename[:-3], path)
            if spec is None or spec.loader is None:
                _log("ORCHESTRA", f"{filename}: spec o loader None, skip")
                return None
            module   = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            instance = module.Pipeline() if hasattr(module, "Pipeline") else module
            setattr(self, cache_attr, instance)
            _log("ORCHESTRA", f"{filename} caricato da: {path}")
            return instance
        except Exception as e:
            _log("ORCHESTRA", f"Errore caricamento {filename}: {e}")
            return None

    def _load_image_loop(self):
        return self._load_module_instance("image_loop.py", "_image_loop_instance")

    def _load_evolver(self):
        return self._load_module_instance("orchestra_evolver.py", "_evolver_instance")

    # =========================================================================
    # ROUTING — lazy init Qdrant con lock
    # =========================================================================

    def _ensure_routing(self):
        if self._routing_ready:
            return
        with _routing_init_lock:
            if self._routing_ready:
                return
            if embed_for_routing is None or not _QDRANT_AVAILABLE:
                return
            model = get_routing_embedding_model()
            if model is None:
                return
            try:
                self._qdrant = QdrantClient(url=self.valves.qdrant_url, timeout=10)
                self._qdrant.get_collections()
            except Exception as e:
                _log("ORCHESTRA", f"Qdrant non raggiungibile: {e}")
                return
            try:
                existing = [c.name for c in self._qdrant.get_collections().collections]
                if ROUTING_COLLECTION in existing:
                    info = self._qdrant.get_collection(ROUTING_COLLECTION)
                    if info.config.params.vectors.size == ROUTING_VECTOR_DIM:
                        self._routing_ready = True
                        _log("ORCHESTRA", f"Collection '{ROUTING_COLLECTION}' già pronta")
                        return
                    self._qdrant.delete_collection(ROUTING_COLLECTION)
                self._qdrant.create_collection(
                    ROUTING_COLLECTION,
                    vectors_config=VectorParams(size=ROUTING_VECTOR_DIM, distance=Distance.COSINE),
                )
                points, pid = [], 0
                for agent, phrases in AGENT_EXAMPLES.items():
                    for phrase in phrases:
                        vec = embed_for_routing(phrase)
                        if vec:
                            points.append(PointStruct(id=pid, vector=vec, payload={"agent": agent}))
                            pid += 1
                if points:
                    self._qdrant.upsert(collection_name=ROUTING_COLLECTION, points=points)
                    _log("ORCHESTRA", f"Popolati {len(points)} esempi routing")
                self._routing_ready = True
            except Exception as e:
                _log("ORCHESTRA", f"Errore init routing: {e}")

    # =========================================================================
    # SISTEMA
    # =========================================================================

    def ram_available_mb(self) -> int:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) // 1024
        except Exception:
            pass
        return 32000

    def vram_free_mb(self) -> int:
        """
        Restituisce la VRAM libera in MB.
        EVO-01: legge dal VRAM daemon di embedding_utils (0ms di latenza).
        Fallback: subprocess nvidia-smi se il daemon non è disponibile.
        Fallback finale: valore conservativo 2000 MB.
        """
        if _VRAM_DAEMON_AVAILABLE:
            return _daemon_vram_free_mb()
        # fallback subprocess — solo se embedding_utils non è importabile
        try:
            # EGPU-04: con 2 GPU nvidia-smi stampa una riga per GPU: seleziona la main
            # (ORCHESTRA_GPU_MAIN, alias ORCHESTRA_GPU_ID) e leggi solo la prima riga.
            gpu = (os.environ.get("ORCHESTRA_GPU_MAIN")
                   or os.environ.get("ORCHESTRA_GPU_ID") or "").strip()
            cmd = ["nvidia-smi"] + (["-i", gpu] if gpu else []) + [
                "--query-gpu=memory.free", "--format=csv,noheader,nounits"]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=3)
            return int(out.decode().strip().splitlines()[0])
        except Exception as e:
            _log("ORCHESTRA", f"nvidia-smi fallito ({e}), uso fallback conservativo 2000MB")
            return 2000

    # ── EGPU-03: backend per ruolo ───────────────────────────────────────────
    def _aux_enabled(self) -> bool:
        return bool(self.valves.ollama_url_aux.strip())

    def _aux_model_set(self) -> set:
        return {m.strip() for m in self.valves.aux_models.split(",") if m.strip()}

    def _aux_ok(self) -> bool:
        """True se l'Ollama aux e' configurato e risponde (esito in cache per aux_health_ttl_s)."""
        if not self._aux_enabled():
            return False
        now = time.monotonic()
        if now < self._aux_checked_until:
            return self._aux_healthy
        try:
            ok = requests.get(self.valves.ollama_url_aux.rstrip("/") + "/", timeout=1.5).status_code == 200
        except Exception:
            ok = False
        self._aux_healthy       = ok
        self._aux_checked_until = now + self.valves.aux_health_ttl_s
        if not ok:
            _log("ORCHESTRA", "Ollama aux non raggiungibile: i modelli aux girano sul main")
        return ok

    def _mark_aux_down(self) -> None:
        self._aux_healthy       = False
        self._aux_checked_until = time.monotonic() + self.valves.aux_health_ttl_s

    def _backend_for(self, model: str) -> str:
        """'aux' se il modello e' servito dall'aux ed e' raggiungibile, altrimenti 'main'."""
        return "aux" if (model in self._aux_model_set() and self._aux_ok()) else "main"

    def _url_for(self, model: str) -> str:
        if self._backend_for(model) == "aux":
            return self.valves.ollama_url_aux.rstrip("/")
        return self.valves.ollama_url

    def vram_aux_free_mb(self) -> Optional[int]:
        """VRAM libera della GPU aux, None se non disponibile (aux spento o daemon vecchio)."""
        if not self._aux_enabled() or _daemon_gpu_free_mb is None:
            return None
        v = _daemon_gpu_free_mb("aux")
        return v if v > 0 else None

    def get_system_stats(self) -> dict:
        return {"ram_mb": self.ram_available_mb(), "vram_mb": self.vram_free_mb(),
                "vram_aux_mb": self.vram_aux_free_mb()}

    def select_mode(self, stats: dict) -> str:
        ram = stats["ram_mb"]
        if ram >= self.valves.ram_quality_min_mb:   return "quality"
        elif ram >= self.valves.ram_fast_min_mb:    return "fast"
        else:                                        return "emergency"

    def select_text_model(self, mode: str, stats: dict) -> tuple[str, dict]:
        """
        Seleziona modello e opzioni in base a mode e risorse disponibili.

        Fasce VRAM:
          ≥ 7000 MB → model_fast  (qwen3.5:9b, full GPU)
          ≥ 5500 MB → model_fallback (llama3.1:8b, full GPU)
          ≥ 3200 MB → model_fallback (llama3.1:8b, num_gpu=22, ~3.5 GB VRAM)
                      Significativamente meglio di llama3.2:3b per query di codice con RAG.
          < 3200 MB → model_coordinator (llama3.2:3b, ~2 GB VRAM)
        EGPU-04: in mode quality con VRAM >= vram_quality_full_mb il modello quality
        gira interamente in GPU (nessun num_gpu).
        """
        vram = stats["vram_mb"]
        ram  = stats["ram_mb"]
        if mode == "quality":
            if ram >= self.valves.ram_quality_min_mb and vram >= 4000:
                # EGPU-04: con VRAM abbondante (3090) il quality model sta tutto in GPU:
                # niente offload di layer su CPU (num_gpu lasciato ad Ollama = tutti i layer).
                if vram >= self.valves.vram_quality_full_mb:
                    return self.valves.model_quality, {}
                return self.valves.model_quality, {"num_gpu": self.valves.quality_num_gpu}
            elif vram >= self.valves.vram_fast_min_mb:
                return self.valves.model_fast, {}
            elif vram >= self.valves.vram_fallback_min_mb:
                return self.valves.model_fallback, {}
            elif vram >= self.valves.vram_partial_fallback_mb:
                return self.valves.model_fallback, {"num_gpu": self.valves.fallback_partial_num_gpu}
            else:
                return self.valves.model_coordinator, {}
        elif mode == "fast":
            if vram >= self.valves.vram_fast_min_mb:
                return self.valves.model_fast, {}
            elif vram >= self.valves.vram_fallback_min_mb:
                return self.valves.model_fallback, {}
            elif vram >= self.valves.vram_partial_fallback_mb:
                return self.valves.model_fallback, {"num_gpu": self.valves.fallback_partial_num_gpu}
            else:
                return self.valves.model_coordinator, {}
        else:
            return self.valves.model_emergency, {}

    def select_vision_model(self, stats: dict) -> tuple[str, dict]:
        """
        Seleziona modello vision e num_gpu in base alla VRAM libera.
        Logica identica a image_loop._select_vision_params() per coerenza.
        Guard FIX-03: no divisione per zero se le soglie sono uguali.
        """
        vram    = stats["vram_mb"]
        # EGPU-04: se la vision gira sull'aux (4060) conta la VRAM libera dell'aux.
        if (self._aux_ok() and self.valves.model_vision in self._aux_model_set()
                and stats.get("vram_aux_mb") is not None):
            vram = stats["vram_aux_mb"]
        full_th = self.valves.vram_vision_full_mb
        part_th = self.valves.vram_vision_partial_mb

        if vram >= full_th:
            _log("ORCHESTRA", f"Vision: VRAM={vram}MB → {self.valves.model_vision} full GPU")
            return self.valves.model_vision, {}

        elif vram >= part_th and full_th > part_th:
            total_layers = 32
            ratio   = (vram - part_th) / (full_th - part_th)
            num_gpu = max(4, int(ratio * total_layers))
            num_gpu = min(num_gpu, total_layers - 1)
            _log("ORCHESTRA", f"Vision: VRAM={vram}MB → {self.valves.model_vision} num_gpu={num_gpu}")
            return self.valves.model_vision, {"num_gpu": num_gpu}

        else:
            _log("ORCHESTRA", f"Vision: VRAM={vram}MB → fallback {self.valves.model_vision_fallback}")
            return self.valves.model_vision_fallback, {}

    def mode_info(self, mode: str) -> tuple[str, str]:
        return {
            "quality":   ("🔋", "Qualità"),
            "fast":      ("⚡", "Veloce"),
            "emergency": ("⚠️", "Emergenza RAM"),
        }.get(mode, ("❓", mode))

    # =========================================================================
    # MESSAGGI
    # =========================================================================

    _OWUI_USER_PREFIXES     = ("query:", "search:")
    _OWUI_INTERNAL_PREFIXES = (
        "### task:", "### instruction:",
        "create a concise", "generate a title",
        "generate 3 follow", "generate 1-3 broad",
    )

    def has_image(self, messages: list) -> bool:
        if not messages: return False
        content = messages[-1].get("content", "")
        if isinstance(content, list):
            return any(item.get("type") in ("image", "image_url") for item in content)
        return False

    def extract_text(self, messages: list) -> str:
        if not messages: return ""
        content = messages[-1].get("content", "")
        if isinstance(content, str):
            raw = content
        elif isinstance(content, list):
            raw = " ".join(i.get("text", "") for i in content if i.get("type") == "text")
        else:
            raw = ""
        stripped = raw.strip()
        lower    = stripped.lower()
        for p in self._OWUI_USER_PREFIXES:
            if lower.startswith(p):
                stripped = stripped[len(p):].strip()
                break
        return stripped

    def is_internal_request(self, text: str) -> bool:
        return any(text.lower().startswith(p) for p in self._OWUI_INTERNAL_PREFIXES)

    def get_user_info(self, body: dict) -> tuple[str, str]:
        user = body.get("user", {})
        return user.get("id", "unknown"), user.get("role", "user")

    @staticmethod
    def _has_rag_context(messages: list) -> bool:
        for msg in messages:
            if msg.get("role") == "system":
                if RAG_MARKER in msg.get("content", ""):
                    return True
        return False

    @classmethod
    def _looks_like_technical_query(cls, text: str) -> bool:
        return any(kw in text.lower() for kw in cls._TECHNICAL_KEYWORDS)

    @staticmethod
    def _inject_warning(messages: list, warning: str) -> list:
        # FIX-02: copy è ora importato a livello modulo
        new_messages = copy.deepcopy(messages)
        sys_idx = next((i for i, m in enumerate(new_messages) if m.get("role") == "system"), None)
        if sys_idx is not None:
            new_messages[sys_idx]["content"] = warning + "\n" + new_messages[sys_idx]["content"]
        else:
            new_messages.insert(0, {"role": "system", "content": warning})
        return new_messages

    def build_ollama_messages(self, messages: list, system_prompt: str) -> list:
        """Costruisce la lista messaggi per Ollama, estraendo il contesto RAG."""
        extra_context_parts = []
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "").strip()
                if not content:
                    continue
                if RAG_MARKER in content:
                    idx = content.find("---\n" + RAG_MARKER)
                    if idx == -1:
                        idx = content.find(RAG_MARKER)
                    if idx >= 0:
                        extra_context_parts.append(content[idx:].strip())
                else:
                    # FIX-04: soglia abbassata da 30 a 5 char per non scartare
                    # messaggi di sistema brevi ma validi.
                    if content != "Sei un assistente AI." and len(content) > 5:
                        extra_context_parts.append(content)

        if extra_context_parts:
            system_prompt += "\n\n" + "\n\n".join(extra_context_parts)

        result = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            role = msg.get("role", "user")
            if role == "system":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                result.append({"role": role, "content": content})
            elif isinstance(content, list):
                texts, images = [], []
                for item in content:
                    t = item.get("type", "")
                    if t == "text":
                        texts.append(item.get("text", ""))
                    elif t == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        if url.startswith("data:") and "," in url:
                            b64 = url.split(",", 1)[1]
                            if b64: images.append(b64)
                    elif t == "image":
                        b64 = item.get("data", "")
                        if b64: images.append(b64)
                entry: dict = {"role": role, "content": " ".join(texts)}
                if images: entry["images"] = images
                result.append(entry)

        if self.valves.debug_log:
            has_rag_marker = RAG_MARKER in system_prompt
            _debug_log(
                True,
                f"[MANIFOLD] system_prompt len={len(system_prompt)}, "
                f"RAG marker presente: {has_rag_marker}",
                flush=True
            )

        return result

    # =========================================================================
    # ROUTING
    # =========================================================================

    def route_text(self, text: str) -> tuple[str, str, str, float]:
        """
        Determina l'agente più adatto tramite embedding semantico su Qdrant.

        Restituisce: (agent_key, emoji, label, confidence)
          - agent_key: chiave in PROMPTS (es. "linux_admin")
          - emoji:     icona dell'agente (es. "🖥️")
          - label:     nome visualizzato (es. "LINUX_ADMIN")
          - confidence: similarità coseno Qdrant [0.45, 1.0] se trovato,
                        0.0 se fallback a coordinator.

        EVO-03: il confidence score era già disponibile dalla query Qdrant
        ma non veniva restituito. Ora viene passato a pipe()/stream()
        per il routing confidence display.
        """
        self._ensure_routing()
        if not self._routing_ready:
            return "coordinator", "🧠", "COORDINATORE", 0.0
        vec = embed_for_routing(text)
        if vec is None:
            return "coordinator", "🧠", "COORDINATORE", 0.0
        try:
            results = self._qdrant.query_points(
                collection_name=ROUTING_COLLECTION,
                query=vec,
                limit=1,
                score_threshold=self.valves.routing_similarity_threshold,
                with_payload=True,
            )
            hits = results.points if hasattr(results, "points") else []
            if hits and hits[0].payload:
                agent      = hits[0].payload.get("agent", "coordinator")
                confidence = float(hits[0].score)
                _log("ROUTING", f"embedding → {agent} (score={confidence:.3f})")
                icon_map = {
                    "linux_admin":      "🖥️",
                    "ml_engineer":      "🤖",
                    "comfy_integrator": "🔌",
                    "design_critic":    "🎨",
                    "orchestra_dev":    "🛠️",
                    "reasoner":         "🔬",
                    "coordinator":      "🧠",
                }
                label_map = {
                    "linux_admin":      "LINUX_ADMIN",
                    "ml_engineer":      "ML_ENGINEER",
                    "comfy_integrator": "COMFY_INTEGRATOR",
                    "design_critic":    "DESIGN_CRITIC",
                    "orchestra_dev":    "ORCHESTRA_DEV",
                    "reasoner":         "REASONER",
                    "coordinator":      "COORDINATORE",
                }
                return (
                    agent,
                    icon_map.get(agent, "🧠"),
                    label_map.get(agent, "COORDINATORE"),
                    confidence,
                )
        except Exception as e:
            _log("ROUTING", f"Errore query Qdrant: {e}")
        return "coordinator", "🧠", "COORDINATORE", 0.0

    # =========================================================================
    # STREAM OLLAMA
    # =========================================================================

    def _get_timeout(self, model: str, reasoning_mode: bool = False) -> int:
        if reasoning_mode:
            return self.valves.reasoning_timeout_s
        if model in (self.valves.model_vision, self.valves.model_vision_fallback):
            return self.valves.vision_timeout_s
        if model == self.valves.model_quality:
            return self.valves.dev_timeout_s
        return self.valves.ollama_timeout_s

    def stream_ollama(
        self, model: str, ollama_messages: list,
        extra_options:   dict | None = None,
        num_predict:     int | None  = None,
        temperature:     float | None = None,
        reasoning_mode:  bool = False,
    ) -> Iterator[str]:
        """
        Streama la risposta di Ollama con supporto completo per:
        - num_predict esplicito (evita troncatura silenziosa)
        - temperatura per agente (determinismo adattivo)
        - reasoning_mode: timeout esteso + show_thinking opzionale
        """
        keep_alive = _KEEP_ALIVE.get(model, _KEEP_ALIVE_DEFAULT)
        # EGPU-03/04: backend e keep_alive per ruolo.
        backend = self._backend_for(model)
        url     = self._url_for(model)
        if backend == "aux":
            keep_alive = max(keep_alive, self.valves.keep_alive_aux_s)
        elif (model in (self.valves.model_quality, self.valves.model_fast, self.valves.model_fallback)
              and self.vram_free_mb() >= self.valves.vram_quality_full_mb):
            # VRAM abbondante sul main: tieni il modello caricato (ricaricare sul link TB4 e' lento).
            keep_alive = max(keep_alive, self.valves.keep_alive_main_s)
        options: dict = {"num_ctx": self.valves.context_length}
        options["num_predict"] = (
            num_predict if num_predict is not None
            else self.valves.num_predict_default
        )
        if temperature is not None:
            options["temperature"] = temperature
        if extra_options:
            options.update(extra_options)

        payload = {
            "model":      model,
            "messages":   ollama_messages,
            "stream":     True,
            "keep_alive": keep_alive,
            "options":    options,
        }

        in_think      = False
        think_buffer  = []          # accumula righe <think> per show_thinking
        timeout       = self._get_timeout(model, reasoning_mode)

        started = False
        try:
            with requests.post(
                f"{url}/api/chat",
                json=payload, stream=True, timeout=timeout,
            ) as resp:
                resp.raise_for_status()
                started = True      # EGPU-03: connessione stabilita, niente failover da qui in poi
                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    try:
                        chunk = json.loads(raw_line).get("message", {}).get("content", "")
                        if not chunk:
                            continue
                        if self.valves.strip_thinking_tags:
                            output, remaining = "", chunk
                            while remaining:
                                if in_think:
                                    if "</think>" in remaining:
                                        # Fine del blocco thinking
                                        think_content, remaining = remaining.split("</think>", 1)
                                        think_buffer.append(think_content)
                                        in_think = False
                                        # show_thinking: emetti un sommario del reasoning
                                        if self.valves.show_thinking and think_buffer:
                                            full_think = "".join(think_buffer).strip()
                                            if full_think:
                                                # Mostra solo le prime 3 righe non vuote
                                                # del processo di ragionamento
                                                lines = [
                                                    l.strip() for l in full_think.splitlines()
                                                    if l.strip()
                                                ][:3]
                                                if lines:
                                                    summary = "\n> ".join(lines)
                                                    yield f"\n> 💭 *{summary}*\n\n"
                                        think_buffer = []
                                    else:
                                        think_buffer.append(remaining)
                                        remaining = ""
                                else:
                                    if "<think>" in remaining:
                                        before, remaining = remaining.split("<think>", 1)
                                        output  += before
                                        in_think = True
                                    else:
                                        output  += remaining
                                        remaining = ""
                            if output:
                                yield output
                        else:
                            yield chunk
                    except json.JSONDecodeError:
                        continue
        except requests.Timeout:
            yield f"\n\n⚠️ *Timeout ({timeout}s) per `{model}`.*"
        except requests.ConnectionError:
            if backend == "aux" and not started:
                # EGPU-03: l'aux e' caduto tra due controlli: segnalo e riprovo sul main
                # (che conserva tutti i modelli). _backend_for ora restituira' "main".
                self._mark_aux_down()
                _log("ORCHESTRA", f"Ollama aux non raggiungibile per {model}: failover sul main")
                yield from self.stream_ollama(model, ollama_messages, extra_options,
                                              num_predict, temperature, reasoning_mode)
                return
            yield "\n\n❌ *Ollama non raggiungibile.*"
        except Exception as e:
            yield f"\n\n❌ *Errore: {type(e).__name__}: {e}*"

    def session_status(self, mode: str, stats: dict, active_model: str = "") -> str:
        mode_e, mode_desc = self.mode_info(mode)
        display = active_model if active_model else {
            "quality":   self.valves.model_quality,
            "fast":      self.valves.model_fast,
            "emergency": self.valves.model_emergency,
        }.get(mode, self.valves.model_fast)
        aux = stats.get("vram_aux_mb")
        aux_txt = f" (+aux {aux / 1024:.1f}GB)" if aux else ""
        return (
            f"> 🎼 **Orchestra** — "
            f"RAM {stats['ram_mb'] / 1024:.1f}GB | VRAM {stats['vram_mb'] / 1024:.1f}GB{aux_txt} | "
            f"{mode_e} {mode_desc} | `{display}`\n\n"
        )

    # =========================================================================
    # GESTIONE COMANDI
    # =========================================================================

    def handle_generate(self, text: str, user_id: str = "") -> Iterator[str]:
        prompt = text[len("/generate"):].strip()
        if not prompt:
            yield (
                "⚠️ **Prompt mancante.**\n\n"
                "Formato: `/generate <descrizione>`\n\n"
                "Esempio: `/generate a futuristic city at night, cyberpunk, 8k`"
            )
            return
        if not _sdxl_lock.acquire(blocking=False):
            yield (
                "⏳ **Generazione già in corso.**\n\n"
                "Un'altra generazione SDXL è attiva. Attendi il completamento."
            )
            return
        try:
            _log("ORCHESTRA", f"→ IMAGE_LOOP | prompt: '{prompt[:60]}'", user_id)
            yield "🖼️ **[IMAGE_LOOP]** — Avvio generazione SDXL...\n\n"
            yield f"📝 *Prompt:* `{prompt}`\n\n---\n\n"
            loop = self._load_image_loop()
            if not loop:
                yield "❌ **image_loop.py non trovato.**"
                return
            result = loop.pipe(
                user_message=prompt, model_id="image-loop",
                messages=[{"role": "user", "content": prompt}], body={},
            )
            if hasattr(result, "__iter__") and not isinstance(result, str):
                yield from result
            else:
                yield str(result)
        finally:
            _sdxl_lock.release()

    def handle_rag(self, text: str, user_id: str = "", role: str = "user") -> Iterator[str]:
        parts   = text.split(None, 2)
        command = parts[1].lower() if len(parts) >= 2 else ""
        if command in {"index"} and role != "admin":
            yield f"🔒 **Comando `/rag {command}` riservato agli amministratori.**"
            return
        if command == "index":
            _log("ORCHESTRA", "→ RAG INDEX", user_id)
            yield "📚 **[RAG]** Avvio indicizzazione `document-ai/`...\n\n"
            try:
                # Usa /index/async + polling invece dello streaming /index.
                # Lo streaming causava ChunkedEncodingError: requests chiudeva
                # la connessione durante i batch lunghi di embedding (silenzio
                # > timeout tra due bytes). Con async il job gira in background
                # e Orchestra fa polling ogni 3s senza rischi di timeout.
                resp = requests.post(
                    f"{self.valves.rag_service_url}/index/async",
                    json={"incremental": True},
                    timeout=15,
                )
                # 409 = indicizzazione già in corso (watcher o altro job API)
                if resp.status_code == 409:
                    yield "⚠️ **Indicizzazione già in corso** — riprova tra qualche minuto\n"
                    return
                resp.raise_for_status()
                job = resp.json()
                job_id = job.get("job_id", "")
                if not job_id:
                    yield "❌ **rag_service non ha restituito job_id**\n"
                    return

                yield f"⚙️ Job avviato: `{job_id}` — polling ogni 3s...\n\n"

                # Polling — mostra le righe di log nuove ad ogni ciclo
                shown_lines = 0
                deadline = time.time() + self.valves.rag_timeout_s
                while time.time() < deadline:
                    time.sleep(3)
                    try:
                        sr = requests.get(
                            f"{self.valves.rag_service_url}/index/status/{job_id}",
                            timeout=10,
                        )
                        sr.raise_for_status()
                        status_data = sr.json()
                    except Exception as poll_err:
                        yield f"⚠️ Polling fallito: {poll_err}\n"
                        break

                    log_lines = status_data.get("log", [])
                    # Yield solo le righe nuove dall'ultimo ciclo
                    for line in log_lines[shown_lines:]:
                        yield line + "\n"
                    shown_lines = len(log_lines)

                    job_status = status_data.get("status", "running")
                    if job_status in ("done", "error", "skipped"):
                        elapsed = status_data.get("elapsed", 0)
                        if job_status == "done":
                            yield f"\n✅ **Indicizzazione completata** in {elapsed:.0f}s\n"
                        elif job_status == "skipped":
                            yield "⚠️ **Job saltato** — indicizzazione già in corso\n"
                        else:
                            yield f"❌ **Job terminato con errore** (elapsed {elapsed:.0f}s)\n"
                        break
                else:
                    yield f"⏱️ **Timeout** ({self.valves.rag_timeout_s}s) — job `{job_id}` ancora in corso\n"

            except requests.ConnectionError:
                yield "❌ **rag_service non raggiungibile**\n"
            except Exception as e:
                yield f"❌ *Errore: {type(e).__name__}: {e}*\n"
        elif command == "status":
            _log("ORCHESTRA", "→ RAG STATUS", user_id)
            try:
                resp = requests.get(f"{self.valves.rag_service_url}/status", timeout=10)
                resp.raise_for_status()
                data   = resp.json()
                total  = data.get("total_chunks", 0)
                domains = data.get("by_domain", {})
                yield "📊 **[RAG]** Stato knowledge base\n\n"
                yield f"**Collection:** `{data.get('collection', 'orchestra')}`  \n"
                yield f"**Docs root:** `{data.get('docs_root', '')}`  \n"
                yield f"**Chunk totali:** {total}\n\n"
                if domains:
                    yield "| Domain | Chunk |\n|--------|-------|\n"
                    for domain, count in sorted(domains.items()):
                        yield f"| `{domain}` | {count} |\n"
                else:
                    yield "⚠️ Nessun documento indicizzato — esegui `/rag index` (admin)\n"
            except requests.ConnectionError:
                yield "❌ **rag_service non raggiungibile**\n"
            except Exception as e:
                yield f"❌ *Errore: {type(e).__name__}: {e}*\n"
        else:
            yield (
                "### 📚 Comandi RAG\n\n"
                "| Comando | Descrizione | Ruolo |\n"
                "|---------|-------------|-------|\n"
                "| `/rag index` | Indicizza `document-ai/` | Admin |\n"
                "| `/rag status` | Statistiche knowledge base | Tutti |\n"
            )

    def handle_review(self, user_id: str) -> Iterator[str]:
        _log("ORCHESTRA", "→ REVIEW", user_id)
        yield "🔍 **Orchestra — Revisione sistema**\n\n"
        yield "*Analisi in corso...*\n\n"
        stats = self._analyze_pattern_logs()
        yield f"**📊 Periodo analizzato:** {stats['start_date']} → {stats['end_date']}\n"
        yield f"- Messaggi totali: {stats['total_messages']}\n"
        yield f"- /generate: {stats['generate_commands']}\n"
        yield f"- Vision fallback: {stats['vision_fallback_count']}\n"
        yield f"- Comandi ripetuti: {stats['repeat_commands']}\n\n"
        rag_status = self._get_rag_status()
        yield (
            f"**📚 RAG:** {rag_status['total_chunks']} chunk "
            f"in {len(rag_status['by_domain'])} domain\n\n"
        )
        yield "💡 **Proposte di miglioramento:**\n\n"
        yield from self._generate_review_proposals(stats, rag_status)

    def handle_evolve(self, text: str, user_id: str = "", role: str = "user") -> Iterator[str]:
        evolver = self._load_evolver()
        if evolver is None:
            yield "❌ **orchestra_evolver.py non trovato.**\n"
            yield "Verifica che il file sia presente in `~/ai-sessioni/ollama/pipelines/`.\n"
            return
        try:
            yield from evolver.handle_evolve(text, role=role)
        except Exception as e:
            yield f"❌ **Errore Evolver:** {type(e).__name__}: {e}\n"

    def _analyze_pattern_logs(self) -> dict:
        stats = {
            "total_messages": 0, "generate_commands": 0,
            "vision_fallback_count": 0, "repeat_commands": 0,
            "start_date": "N/D", "end_date": "N/D",
        }
        try:
            if not PATTERN_LOG_PATH.exists():
                return stats
            lines    = PATTERN_LOG_PATH.read_text().strip().splitlines()
            start_ts = end_ts = None
            for line in lines:
                try:
                    event = json.loads(line)
                    ts    = datetime.fromisoformat(event["timestamp"])
                    if start_ts is None or ts < start_ts: start_ts = ts
                    if end_ts   is None or ts > end_ts:   end_ts   = ts
                    etype = event["type"]
                    if etype == "user_message":        stats["total_messages"] += 1
                    elif etype == "command_repeat":    stats["repeat_commands"] += 1
                    elif etype == "vision_fallback":   stats["vision_fallback_count"] += 1
                    elif etype == "generate_command":  stats["generate_commands"] += 1
                except Exception:
                    pass
            if start_ts:
                stats["start_date"] = start_ts.strftime("%Y-%m-%d")
                stats["end_date"]   = end_ts.strftime("%Y-%m-%d")
        except Exception as e:
            _log("ORCHESTRA", f"Errore lettura pattern log: {e}")
        return stats

    def _get_rag_status(self) -> dict:
        try:
            resp = requests.get(f"{self.valves.rag_service_url}/status", timeout=5)
            return resp.json()
        except Exception:
            return {"total_chunks": 0, "by_domain": {}}

    def _generate_review_proposals(self, stats: dict, rag_status: dict) -> Iterator[str]:
        prompt = (
            f"Sei ORCHESTRA_DEV. Analizza questi dati e proponi 2-3 miglioramenti.\n\n"
            f"Statistiche: messaggi={stats['total_messages']}, /generate={stats['generate_commands']}, "
            f"vision_fallback={stats['vision_fallback_count']}, comandi_ripetuti={stats['repeat_commands']}.\n"
            f"RAG: {rag_status.get('total_chunks', 0)} chunk, "
            f"domain: {', '.join(rag_status.get('by_domain', {}).keys())}.\n\n"
            f"Proponi titolo, problema, soluzione (eventuale snippet), impatto. Markdown, italiano."
        )
        try:
            response = requests.post(
                f"{self.valves.ollama_url}/api/generate",
                json={
                    "model":  self.valves.model_quality,
                    "prompt": prompt,
                    "stream": True,
                    "keep_alive": 0,
                    "options": {"num_ctx": 4096, "num_predict": 2048},
                },
                timeout=self.valves.dev_timeout_s, stream=True,
            )
            for line in response.iter_lines():
                if line:
                    try:
                        chunk = json.loads(line).get("response", "")
                        yield chunk
                    except Exception:
                        pass
        except Exception as e:
            yield f"\n❌ Errore: {e}\n"

    # =========================================================================
    # ENTRY POINT
    # =========================================================================

    def pipe(
        self, user_message: str, model_id: str, messages: list, body: dict
    ) -> Union[str, Iterator]:
        text       = self.extract_text(messages)
        text_clean = text.strip()
        has_img    = self.has_image(messages)
        stats      = self.get_system_stats()
        mode       = self.select_mode(stats)
        user_id, role = self.get_user_info(body)

        _log(
            "ORCHESTRA",
            f"in='{text_clean[:60]}' mode={mode} "
            f"ram={stats['ram_mb']}MB vram={stats['vram_mb']}MB role={role}",
            user_id
        )
        log_event("user_message", {"user": user_id, "text": text_clean[:200]})

        # FIX-03: pulizia time-based indipendente dalla dimensione del dict
        now = time.time()
        if now - _last_cleanup_time > _CLEANUP_INTERVAL_S or len(_last_user_message) > 50:
            _cleanup_last_messages()
        last = _last_user_message.get(user_id)
        if last and last["text"] == text_clean and (now - last["time"]) < 300:
            log_event("command_repeat", {"user": user_id, "text": text_clean[:100]})
        _last_user_message[user_id] = {"text": text_clean, "time": now}

        if self.is_internal_request(text_clean) or not text_clean:
            return ""

        lower = text_clean.lower()
        if lower.startswith("/generate"):
            log_event("generate_command", {"user": user_id, "prompt": text_clean[9:].strip()[:100]})
            return self.handle_generate(text_clean, user_id)
        if lower.startswith("/rag"):
            return self.handle_rag(text_clean, user_id, role)
        if lower.startswith("/review"):
            return self.handle_review(user_id)
        if lower.startswith("/evolve"):
            return self.handle_evolve(text_clean, user_id, role)

        has_rag    = self._has_rag_context(messages)
        force_fast = False

        if self.valves.debug_log:
            _debug_log(True, f"[MANIFOLD] RAG={has_rag}", flush=True)
            for i, m in enumerate(messages):
                if isinstance(m.get("content"), str) and "CONTESTO" in m["content"]:
                    _debug_log(True, f"[MANIFOLD]   msg {i} role={m['role']} has RAG", flush=True)

        if not has_rag:
            messages = self._inject_warning(messages, _NO_RAG_WARNING)
            log_event("ungrounded_response", {
                "user": user_id, "query": text_clean[:200], "rag_available": False
            })
            if self._looks_like_technical_query(text_clean):
                force_fast = True
                _log("ORCHESTRA", "forzo qwen3.5:9b — domanda tecnica senza RAG", user_id)

        def stream() -> Iterator[str]:
            # Mappa agente → temperatura ottimale.
            # Più bassa = più determinismo. Vedi Valves temperature_* per i valori.
            TEMP_MAP: dict[str, float] = {
                "coordinator":      self.valves.temperature_coordinator,
                "linux_admin":      self.valves.temperature_technical,
                "ml_engineer":      self.valves.temperature_technical,
                "comfy_integrator": self.valves.temperature_technical,
                "design_critic":    self.valves.temperature_creative,
                "orchestra_dev":    self.valves.temperature_code,
                "reasoner":         self.valves.temperature_reasoning,
            }

            confidence     = 0.0
            is_coordinator = False
            is_reasoner    = False

            if has_img:
                model, extra_opts = self.select_vision_model(stats)
                prompt_key        = "design_critic"
                emoji, label      = "🎨", "DESIGN_CRITIC"

            elif has_rag:
                prompt_key, emoji, label, confidence = self.route_text(text_clean)
                is_coordinator = (prompt_key == "coordinator")
                is_reasoner    = (prompt_key == "reasoner")
                if is_coordinator:
                    model, extra_opts = self.valves.model_coordinator, {}
                elif is_reasoner:
                    model, extra_opts = self.valves.model_fast, {}
                else:
                    model, extra_opts = self.valves.model_fast, {}

            elif force_fast:
                model, extra_opts                    = self.valves.model_fast, {}
                prompt_key, emoji, label, confidence = self.route_text(text_clean)
                is_coordinator = (prompt_key == "coordinator")
                is_reasoner    = (prompt_key == "reasoner")
                if is_coordinator:
                    model, extra_opts = self.valves.model_coordinator, {}
                elif is_reasoner:
                    model, extra_opts = self.valves.model_fast, {}

            else:
                prompt_key, emoji, label, confidence = self.route_text(text_clean)
                is_coordinator = (prompt_key == "coordinator")
                is_reasoner    = (prompt_key == "reasoner")
                if is_coordinator:
                    model, extra_opts = self.valves.model_coordinator, {}
                elif is_reasoner:
                    model, extra_opts = self.valves.model_fast, {}
                else:
                    model, extra_opts = self.select_text_model(mode, stats)

            # Parametri di generazione per agente
            temperature = TEMP_MAP.get(prompt_key, self.valves.temperature_coordinator)
            if is_reasoner:
                num_pred = self.valves.num_predict_reasoning
            elif prompt_key == "orchestra_dev":
                num_pred = self.valves.num_predict_dev
            else:
                num_pred = self.valves.num_predict_default

            if self.valves.show_session_status:
                yield self.session_status(mode, stats, active_model=model)

            if self.valves.show_agent_header:
                if is_reasoner:
                    latency = "⏳ ~15–40s"
                elif is_coordinator:
                    latency = "⚡ ~2–5s"
                elif model == self.valves.model_quality:
                    latency = "⏳ ~60–90s"
                elif extra_opts.get("num_gpu"):
                    latency = "⏳ ~20–35s"
                else:
                    latency = "⚡ ~5–15s"

                if confidence > 0.0:
                    pct    = int(confidence * 100)
                    filled = min(5, round(confidence * 5))
                    bar    = "◼" * filled + "◻" * (5 - filled)
                    confidence_str = f" {bar} {pct}%"
                else:
                    confidence_str = ""

                temp_str = f" t={temperature}" if self.valves.debug_log else ""
                yield f"{emoji} **[{label}]** — `{model}` {latency}{confidence_str}{temp_str}\n\n"

            _log(
                "ORCHESTRA",
                f"→ {label} | model={model} opts={extra_opts} "
                f"temp={temperature} num_predict={num_pred}"
                + (f" | routing={confidence:.2f}" if confidence > 0.0 else ""),
                user_id,
            )
            yield from self.stream_ollama(
                model,
                self.build_ollama_messages(messages, PROMPTS[prompt_key]),
                extra_opts,
                num_predict    = num_pred,
                temperature    = temperature,
                reasoning_mode = is_reasoner,
            )

        return stream()
