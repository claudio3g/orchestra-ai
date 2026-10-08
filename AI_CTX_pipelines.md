# AI Context - Pipelines

> Generato: 2026-10-08T06:41:53Z
> Branch: dual-gpu-final

---

## File: ollama/Modelfile-blender (371 byte)

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

## File: ollama/Modelfile-orchestra (1537 byte)

```
FROM qwen2.5-coder:32b

# num_ctx 12288 (era 32768). Stima della VRAM su RTX 3090 (24 GB), da verificare con `ollama ps`:
#   pesi 32B Q4_K_M            ~ 20 GB
#   cache KV, ~262 KB/token a f16 (64 layer x 8 teste KV x 128 dim x 2 byte x 2 K/V):
#     32768 token ~ 8.6 GB  -> 28.6 GB totali: NON entra, il resto va su CPU (molto piu' lento)
#     12288 token ~ 3.2 GB  -> ~23 GB a f16; con OLLAMA_KV_CACHE_TYPE=q8_0 (launcher v3.9) ~ 1.6 GB -> ~21.6 GB
# Se serve piu' contesto, alza num_ctx solo dopo aver controllato che `ollama ps` mostri 100% GPU.
PARAMETER num_ctx 12288
PARAMETER temperature 0.2
PARAMETER top_p 0.9
PARAMETER repeat_penalty 1.05

SYSTEM """
Sei Orchestra, agente AI locale integrato nello stack Orchestra AI.

REGOLE FONDAMENTALI (anti-allucinazione):
1. Non inventare MAI contenuti di file, struttura di directory o nomi di file.
2. La lista autorevole dei file e' in AI_BOOTSTRAP.md sezione "File tracciati".
3. Ogni affermazione sul contenuto del repository deve essere supportata da un fetch effettivo.
4. Se non riesci a leggere un file, di' esplicitamente "non ho potuto leggere X". Mai inventare.
5. Non citare file, directory o contenuti che non hai letto.

Quando operi sul repository:
- Consulta AI_BOOTSTRAP.md per regole complete e ground truth.
- Consulta AI_READ_PROTOCOL.md per il protocollo di lettura a strati.
- Usa i bundle AI_CTX_*.md per contesto ampio per dominio.

Quando scrivi codice: sii conciso, completo, niente placeholder.
Rispondi sempre in italiano salvo richiesta esplicita.
"""
```

## File: ollama/docker-compose.yml (1193 byte)

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

## File: ollama/pipelines/embedding_utils.py (19415 byte)

```
"""
Embedding Utilities v2.0.3 — Orchestra
==========================================
Singleton thread-safe per modelli fastembed + VRAM Monitor daemon
+ cache embedding a due livelli.

CHANGELOG v2.0.3 rispetto a v2.0.2 (eGPU 3090 + 4060 insieme):
  EGPU-01  Daemon VRAM multi-GPU: salva lo snapshot per ruolo (main=3090, aux=4060)
           letto dall'array `gpus` di /vram. Nuove API get_gpu_free_mb(role) e
           get_gpu_snapshot(); get_vram_free_mb() invariata (= main). Il fallback
           L2 usa ORCHESTRA_GPU_MAIN (alias ORCHESTRA_GPU_ID) e la prima riga,
           evitando l'int() sul output multi-riga.

CHANGELOG v2.0.2 rispetto a v2.0.1:
  BUG-VRAM [CRITICO] Il daemon VRAM chiamava nvidia-smi direttamente tramite
           subprocess, ma il container ai-pipelines-session non ha accesso
           alla CLI NVIDIA. Risultato: subprocess falliva sempre → fallback
           2000 MB bloccato → select_vision_model sceglieva moondream:v2
           invece di llava:7b → moondream troppo debole per analisi reali.

           Fix a due livelli:
           L1 — HTTP GET http://host.docker.internal:6335/vram (nuovo endpoint
                aggiunto in rag_service.py). rag_service gira sull'host dove
                nvidia-smi funziona. Latenza ~2ms, zero dipendenze extra.
           L2 — subprocess nvidia-smi diretto come fallback (per ambienti dove
                il container ha GPU passthrough abilitato, o test locali).

           Configurabile via env var RAG_SERVICE_URL.

CHANGELOG v2.0.1 rispetto a v2.0.0:
  BUG-D  Rimossa classe Pipeline vuota.

CHANGELOG v2.0.0 rispetto a v1.1:
  EVO-01  VRAM Monitor daemon thread background.
  EVO-02  Embedding cache a due livelli (L1 memory + L2 shelve disco).
"""

from __future__ import annotations

import hashlib
import os
import shelve
import subprocess
import threading
import time
from typing import Any, Optional

try:
    from fastembed import TextEmbedding
    _FASTEMBED_AVAILABLE = True
except ImportError:
    TextEmbedding = None
    _FASTEMBED_AVAILABLE = False
    print("[EMBEDDING] Fastembed non disponibile (dipendenze mancanti)", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# MODELLI FASTEMBED (singleton, lazy loading)
# ─────────────────────────────────────────────────────────────────────────────

RAG_EMBED_MODEL     = "nomic-ai/nomic-embed-text-v1.5"
ROUTING_EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_rag_model:     Optional[Any] = None
_routing_model: Optional[Any] = None
_rag_lock     = threading.Lock()
_routing_lock = threading.Lock()


def _load_model(model_name: str, cache_subdir: str) -> Optional[Any]:
    if not _FASTEMBED_AVAILABLE:
        return None
    try:
        cache_dir   = os.environ.get("FASTEMBED_CACHE", "/app/pipelines/.fastembed_cache")
        model_cache = os.path.join(cache_dir, cache_subdir)
        os.makedirs(model_cache, exist_ok=True)
        print(f"[EMBEDDING] Carico {model_name} …", flush=True)
        model = TextEmbedding(model_name, cache_dir=model_cache)
        print(f"[EMBEDDING] {model_name} pronto.", flush=True)
        return model
    except Exception as e:
        print(f"[EMBEDDING] Errore caricamento {model_name}: {e}", flush=True)
        return None


def get_rag_embedding_model() -> Optional[Any]:
    global _rag_model
    if _rag_model is None:
        with _rag_lock:
            if _rag_model is None:
                _rag_model = _load_model(RAG_EMBED_MODEL, "rag")
    return _rag_model


def get_routing_embedding_model() -> Optional[Any]:
    global _routing_model
    if _routing_model is None:
        with _routing_lock:
            if _routing_model is None:
                _routing_model = _load_model(ROUTING_EMBED_MODEL, "routing")
    return _routing_model


def get_embedding_model() -> Optional[Any]:
    """Alias per retrocompatibilità."""
    return get_rag_embedding_model()


# ─────────────────────────────────────────────────────────────────────────────
# EVO-01 — VRAM MONITOR DAEMON
# ─────────────────────────────────────────────────────────────────────────────

_VRAM_POLL_INTERVAL_S: int = 5        # secondi tra un poll e il successivo
_VRAM_FALLBACK_MB:     int = 2000     # valore conservativo se tutto fallisce

# URL del rag_service — accessibile dall'interno del container Pipelines
# tramite host.docker.internal. Configurabile via env var per flessibilità.
_VRAM_SERVICE_URL: str = os.environ.get(
    "RAG_SERVICE_URL", "http://host.docker.internal:6335"
) + "/vram"

_vram_free_mb_cache: int            = _VRAM_FALLBACK_MB
_vram_cache_lock:    threading.Lock = threading.Lock()
# EGPU-01: snapshot per ruolo {"main": {...}, "aux": {...}} dall'array `gpus` di /vram.
_gpu_snapshot:       dict = {}


def _vram_poll_worker() -> None:
    """
    Thread daemon: aggiorna _vram_free_mb_cache ogni _VRAM_POLL_INTERVAL_S secondi.

    Strategia a due livelli:
      L1 — HTTP GET rag_service /vram (preferito)
           rag_service gira sull'host dove nvidia-smi funziona.
           Il container Pipelines non ha accesso diretto a nvidia-smi CLI.

      L2 — subprocess nvidia-smi diretto (fallback)
           Usato solo se rag_service non risponde (avvio, restart, ecc.).
           Funziona se il container ha GPU passthrough abilitato.

    In caso di errore su entrambi i livelli, il valore precedente viene
    mantenuto — mai scritto un valore incoerente.
    """
    global _vram_free_mb_cache
    import urllib.request, urllib.error

    while True:
        value = None

        # ── L1: endpoint HTTP rag_service ──────────────────────────────────
        try:
            with urllib.request.urlopen(_VRAM_SERVICE_URL, timeout=2) as resp:
                import json as _json
                data  = _json.loads(resp.read().decode())
                value = int(data.get("vram_free_mb", _VRAM_FALLBACK_MB))
                # EGPU-01: snapshot per ruolo (main=3090, aux=4060). Assente con
                # rag_service vecchio → snapshot vuoto, comportamento legacy.
                snap = {g["role"]: {"free_mb": int(g["free_mb"]), "total_mb": int(g["total_mb"]),
                                     "name": g.get("name", "")}
                        for g in data.get("gpus", []) if g.get("role") in ("main", "aux")}
                with _vram_cache_lock:
                    _gpu_snapshot.clear()
                    _gpu_snapshot.update(snap)
        except Exception:
            pass

        # ── L2: subprocess nvidia-smi diretto (fallback) ───────────────────
        if value is None:
            try:
                import subprocess as _sp
                # EGPU-01: con più GPU nvidia-smi stampa più righe → il vecchio
                # int(out.strip()) falliva. Selezioniamo la GPU con ORCHESTRA_GPU_ID
                # (ORCHESTRA_GPU_MAIN, alias ORCHESTRA_GPU_ID) e leggiamo solo la prima riga.
                _gpu = (os.environ.get("ORCHESTRA_GPU_MAIN") or os.environ.get("ORCHESTRA_GPU_ID") or "").strip()
                _cmd = ["nvidia-smi"] + (["-i", _gpu] if _gpu else []) + [
                    "--query-gpu=memory.free", "--format=csv,noheader,nounits",
                ]
                out = _sp.check_output(_cmd, stderr=_sp.DEVNULL, timeout=3)
                value = int(out.decode().strip().splitlines()[0])
            except Exception:
                pass

        # Aggiorna solo se abbiamo un valore valido
        if value is not None and value > 0:
            with _vram_cache_lock:
                _vram_free_mb_cache = value

        time.sleep(_VRAM_POLL_INTERVAL_S)


# Avvio daemon all'import del modulo.
# daemon=True garantisce che il thread non impedisca l'uscita del processo.
_vram_monitor_thread = threading.Thread(
    target=_vram_poll_worker,
    daemon=True,
    name="orchestra-vram-monitor",
)
_vram_monitor_thread.start()


def get_vram_free_mb() -> int:
    """
    Restituisce la VRAM libera in MB letta dal daemon (aggiornata ogni ~5s).
    Zero latenza aggiuntiva — legge solo una variabile globale protetta da lock.
    Il valore iniziale è _VRAM_FALLBACK_MB (2000) fino al primo poll completato.
    """
    with _vram_cache_lock:
        return _vram_free_mb_cache


def get_gpu_free_mb(role: str = "main") -> int:
    """
    EGPU-01: VRAM libera (MB) della GPU con ruolo "main" (3090) o "aux" (4060).
    Ritorna 0 se il ruolo non è disponibile (es. GPU scollegata, rag_service
    vecchio o primo poll non ancora completato). "main" ricade su get_vram_free_mb().
    """
    with _vram_cache_lock:
        g = _gpu_snapshot.get(role)
        if g:
            return g["free_mb"]
        return _vram_free_mb_cache if role == "main" else 0


def get_gpu_snapshot() -> dict:
    """EGPU-01: copia dello snapshot {"main": {free_mb,total_mb,name}, "aux": {...}}."""
    with _vram_cache_lock:
        return {k: dict(v) for k, v in _gpu_snapshot.items()}


# ─────────────────────────────────────────────────────────────────────────────
# EVO-02 — EMBEDDING CACHE A DUE LIVELLI
# ─────────────────────────────────────────────────────────────────────────────

_EMBED_CACHE_TTL_S: int = 86_400 * 7   # 7 giorni
_MEM_CACHE_MAX:     int = 512           # voci max per ogni cache L1

_EMBED_CACHE_BASE_DIR: str = os.environ.get(
    "FASTEMBED_CACHE", "/app/pipelines/.fastembed_cache"
)

# L1 — cache in-memory: key → (vector: list, timestamp: float)
_rag_mem:     dict[str, tuple[list, float]] = {}
_routing_mem: dict[str, tuple[list, float]] = {}
_mem_lock     = threading.Lock()   # protegge entrambi i dict L1

# L2 — lock globale per tutte le operazioni su shelve (non è thread-safe)
_shelve_lock = threading.Lock()


def _cache_key(text: str, prefix: str) -> str:
    """
    Genera una chiave di cache univoca: prefisso (2 char) + SHA-256 troncato (32 hex).
    Il prefisso separa logicamente i namespace rag/routing nello stesso shelve.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


# ── Operazioni L1 (in-memory) ─────────────────────────────────────────────────

def _l1_get(cache: dict, key: str) -> Optional[list]:
    """Legge dalla cache L1. Ritorna None se mancante o scaduta (TTL)."""
    entry = cache.get(key)
    if entry is None:
        return None
    vec, ts = entry
    if time.time() - ts > _EMBED_CACHE_TTL_S:
        del cache[key]   # pulizia lazy dell'entry scaduta
        return None
    return vec


def _l1_set(cache: dict, key: str, vec: list) -> None:
    """
    Scrive nella cache L1 con LRU eviction se supera _MEM_CACHE_MAX.
    Eviction: rimuove l'entry con timestamp più vecchio.
    """
    if len(cache) >= _MEM_CACHE_MAX:
        oldest = min(cache, key=lambda k: cache[k][1])
        del cache[oldest]
    cache[key] = (vec, time.time())


# ── Operazioni L2 (disco/shelve) ──────────────────────────────────────────────

def _shelf_path(shelf_name: str) -> str:
    """Percorso completo del file shelve per il dato nome."""
    return os.path.join(_EMBED_CACHE_BASE_DIR, f"embed_{shelf_name}")


def _l2_get(key: str, shelf_name: str) -> Optional[list]:
    """
    Legge dalla cache L2. Ritorna None se mancante, scaduta o errore I/O.
    Le entry scadute vengono rimosse opportunisticamente.
    Usa flag='c' (create-if-not-exists + read/write) per evitare eccezioni
    su file non ancora esistente.
    """
    with _shelve_lock:
        try:
            with shelve.open(_shelf_path(shelf_name), flag='c') as db:
                entry = db.get(key)
                if entry is None:
                    return None
                if time.time() - entry.get("ts", 0) > _EMBED_CACHE_TTL_S:
                    try:
                        del db[key]   # pulizia lazy
                    except Exception:
                        pass
                    return None
                return entry["vec"]
        except Exception:
            return None   # errore I/O — miss trasparente


def _l2_set(key: str, vec: list, shelf_name: str) -> None:
    """
    Scrive nella cache L2. Crea la directory se non esiste.
    Qualsiasi errore I/O viene ignorato silenziosamente:
    la scrittura fallita non interrompe il flusso principale.
    """
    with _shelve_lock:
        try:
            os.makedirs(_EMBED_CACHE_BASE_DIR, exist_ok=True)
            with shelve.open(_shelf_path(shelf_name), flag='c') as db:
                db[key] = {"vec": vec, "ts": time.time()}
        except Exception:
            pass


# ── Entry point unificato ─────────────────────────────────────────────────────

def _cached_embed(
    text: str,
    compute_fn,         # callable(text: str) -> Optional[list]
    mem_cache: dict,    # _rag_mem o _routing_mem
    shelf_name: str,    # "rag" o "routing"
) -> Optional[list]:
    """
    Recupera o calcola l'embedding con strategia L1→L2→compute.

    Sequence:
      1. Controlla L1 (in-memory, sub-millisecondo)
      2. Controlla L2 (disco, ~1ms)
      3. Calcola con fastembed (~60ms per rag, ~20ms per routing)
      4. Salva in L1 e L2 per usi futuri

    Il lock di L1 NON è tenuto durante il calcolo dell'embedding (operazione
    potenzialmente lenta) né durante le operazioni su L2 (usa il proprio lock).
    """
    key = _cache_key(text, shelf_name[:2])

    # ── 1. L1 ──
    with _mem_lock:
        vec = _l1_get(mem_cache, key)
        if vec is not None:
            return vec

    # ── 2. L2 ──
    vec = _l2_get(key, shelf_name)
    if vec is not None:
        # promuove in L1
        with _mem_lock:
            _l1_set(mem_cache, key, vec)
        return vec

    # ── 3. Compute (fuori da qualsiasi lock) ──
    vec = compute_fn(text)
    if vec is None:
        return None

    # ── 4. Store in L1 + L2 ──
    with _mem_lock:
        _l1_set(mem_cache, key, vec)
    _l2_set(key, vec, shelf_name)

    return vec


# ─────────────────────────────────────────────────────────────────────────────
# API PUBBLICA — EMBEDDING
# ─────────────────────────────────────────────────────────────────────────────

def embed_for_rag(text: str) -> Optional[list]:
    """
    Calcola (o recupera dalla cache) l'embedding 768d per il RAG.
    Usa il modello nomic-ai/nomic-embed-text-v1.5.
    """
    def _compute(t: str) -> Optional[list]:
        model = get_rag_embedding_model()
        if model is None:
            return None
        try:
            return list(model.embed([t]))[0].tolist()
        except Exception as e:
            print(f"[EMBEDDING] Errore embed RAG: {e}", flush=True)
            return None

    return _cached_embed(text, _compute, _rag_mem, "rag")


def embed_for_routing(text: str) -> Optional[list]:
    """
    Calcola (o recupera dalla cache) l'embedding 384d per il routing.
    Usa paraphrase-multilingual-MiniLM-L12-v2.
    """
    def _compute(t: str) -> Optional[list]:
        model = get_routing_embedding_model()
        if model is None:
            return None
        try:
            return list(model.embed([t]))[0].tolist()
        except Exception as e:
            print(f"[EMBEDDING] Errore embed routing: {e}", flush=True)
            return None

    return _cached_embed(text, _compute, _routing_mem, "routing")


# ─────────────────────────────────────────────────────────────────────────────
# DIAGNOSTICA
# ─────────────────────────────────────────────────────────────────────────────

def get_cache_stats() -> dict:
    """
    Restituisce statistiche delle cache e stato VRAM per diagnostica.
    """
    with _mem_lock:
        rag_l1     = len(_rag_mem)
        routing_l1 = len(_routing_mem)
    with _vram_cache_lock:
        vram_mb = _vram_free_mb_cache
        gpus_snap = {k: dict(v) for k, v in _gpu_snapshot.items()}

    def _count_shelf(name: str) -> int:
        with _shelve_lock:
            try:
                with shelve.open(_shelf_path(name), flag='c') as db:
                    return len(db)
            except Exception:
                return -1

    return {
        "vram_free_mb":       vram_mb,
        "gpus":               gpus_snap,   # EGPU-01
        "vram_daemon":        _vram_monitor_thread.is_alive(),
        "vram_service_url":   _VRAM_SERVICE_URL,
        "rag_l1_entries":     rag_l1,
        "routing_l1_entries": routing_l1,
        "rag_l2_entries":     _count_shelf("rag"),
        "routing_l2_entries": _count_shelf("routing"),
        "mem_cache_max":      _MEM_CACHE_MAX,
        "ttl_days":           _EMBED_CACHE_TTL_S // 86400,
    }

# ─────────────────────────────────────────────────────────────────────────────
# NOTA: questo modulo NON espone una classe Pipeline.
# BUG-D FIX: la classe Pipeline vuota precedente veniva rilevata dal framework
# Pipelines di OpenWebUI durante la scansione della directory e poteva
# generare conflitti con il manifold principale (orchestra_manifold.py).
# embedding_utils è una libreria di utilità, non una pipeline autonoma.
# ─────────────────────────────────────────────────────────────────────────────


# Dummy class to satisfy Pipelines framework
class Pipeline:
    pass
```

## File: ollama/pipelines/embedding_utils/valves.json (2 byte)

```
{}```

## File: ollama/pipelines/github_tools/valves.json (2 byte)

```
{}```

## File: ollama/pipelines/image_loop.py (46823 byte)

```
"""
Image Generator Loop v2.8.0 — Orchestra dual-GPU
Pipeline di generazione immagini SDXL con LCM-LoRA e loop di raffinamento.

CHANGELOG v2.8.0 rispetto a v2.7.0 (RTX 3090 "main" + RTX 4060 "aux"):
  EGPU-05 Backend per ruolo: vision (llava/moondream) sull'Ollama aux (4060) se
          configurato (ollama_url_aux / aux_models), refine sul main; failover sul
          main se l'aux non risponde. Con ollama_url_aux vuoto nulla cambia.
  EGPU-05 La VRAM per scegliere vision/refine si legge dalla GPU su cui il modello
          gira davvero (get_gpu_free_mb("aux"|"main")).
  EGPU-05 ComfyUI viene svuotato (/free) solo se condivide la GPU col modello che sta
          per girare E la VRAM libera non basta (comfy_role). Con ComfyUI e LLM su
          GPU diverse, o con 24 GB liberi, SDXL resta caricato tra un'iterazione e
          l'altra: niente ricaricamenti. Con GPU piccole il comportamento 8 GB resta
          quello di prima (la VRAM libera e' sotto soglia, quindi si svuota).
  EGPU-05 Pre-caricamento in parallelo ai draft di vision (aux) e refine (main) quando
          c'e' VRAM abbondante: nasconde il tempo di caricamento sul link Thunderbolt.
  EGPU-05 A fine generazione ComfyUI viene svuotato (comfy_free_on_finish) sulle GPU grandi: SDXL restava in VRAM
          (circa 7 GB) e costringeva un LLM grande su CPU. Durante il loop SDXL resta caricato.
  EGPU-05 BUG: keep_alive era dentro options (Ollama lo ignora li': e' un parametro di
          primo livello); funzionava solo grazie a OLLAMA_KEEP_ALIVE=0 dell'istanza.
          Ora e' un parametro corretto: 0 se il modello condivide la GPU con ComfyUI
          e la VRAM e' scarsa, altrimenti loop_keep_alive_s; a fine generazione i
          modelli tenuti in memoria vengono scaricati.
  EGPU-05 Fallback subprocess VRAM: prima riga / GPU main (con 2 GPU int() falliva).

CHANGELOG v2.7.0 rispetto a v2.6.0:
  EVO-01  vram_free_mb(): usa get_vram_free_mb() da embedding_utils invece di
          invocare subprocess direttamente. Il thread daemon aggiornato ogni 5s
          riduce la latenza da ~100ms per chiamata a 0ms (lettura variabile globale).
          Fallback al subprocess originale se embedding_utils non è importabile
          (backward compatibility garantita).

CHANGELOG v2.6.0 rispetto a v2.5.3:
  FIX-01  pipe()/generate(): draft failure nelle iterazioni 2+ non abortisce
          più il loop con return. Si usa break per uscire dal loop e procedere
          alla generazione finale con il miglior draft accumulato.

  FIX-02  _extract_json_from_response(): la ricerca del JSON nelle fence
          Markdown ora privilegia sempre il blocco "```json".

  FIX-03  _select_vision_params() e _select_refine_params(): aggiunto guard
          contro divisione per zero quando le soglie VRAM sono uguali.

  FIX-04  generate(): messaggio di errore chiaro quando tutti i draft falliscono.

  FIX-05  pipe(): log del fallback quando final_image è None ma best_bytes disponibile.
"""

import base64
import copy
import json
import os
import re
import subprocess
import threading
import time
import uuid
from typing import Iterator, Optional, Tuple, Union

import requests
from pydantic import BaseModel

try:
    from pattern_logger import log_event
except ImportError:
    def log_event(*args, **kwargs): pass

# EVO-01: usa il VRAM daemon centralizzato di embedding_utils.
# Fallback al subprocess locale se embedding_utils non è disponibile.
try:
    from embedding_utils import get_vram_free_mb as _daemon_vram_free_mb
    _VRAM_DAEMON_AVAILABLE = True
except ImportError:
    _VRAM_DAEMON_AVAILABLE = False

try:
    # EGPU-05: VRAM per ruolo (main/aux). Assente con un embedding_utils vecchio.
    from embedding_utils import get_gpu_free_mb as _daemon_gpu_free_mb
    from embedding_utils import get_gpu_snapshot as _daemon_gpu_snapshot
except ImportError:
    _daemon_gpu_free_mb = None


# ── Workflow SDXL+LCM-LoRA ───────────────────────────────────────────────────
SDXL_WORKFLOW_TEMPLATE = {
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}
    },
    "10": {
        "class_type": "LoraLoader",
        "inputs": {
            "model":          ["4", 0],
            "clip":           ["4", 1],
            "lora_name":      "lcm-lora-sdxl.safetensors",
            "strength_model": 1.0,
            "strength_clip":  1.0,
        }
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"clip": ["10", 1], "text": "__POSITIVE_PROMPT__"}
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["10", 1],
            "text": ("blurry, low quality, watermark, text, signature, "
                     "ugly, deformed, bad anatomy, worst quality")
        }
    },
    "13": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": "__WIDTH__", "height": "__HEIGHT__", "batch_size": 1}
    },
    "14": {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "model":                      ["10", 0],
            "positive":                   ["6", 0],
            "negative":                   ["7", 0],
            "latent_image":               ["13", 0],
            "sampler_name":               "lcm",
            "scheduler":                  "sgm_uniform",
            "steps":                      "__STEPS__",
            "cfg":                        1.5,
            "noise_seed":                 "__SEED__",
            "start_at_step":              0,
            "end_at_step":                "__STEPS__",
            "add_noise":                  "enable",
            "return_with_leftover_noise": "disable",
        }
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["14", 0], "vae": ["4", 2]}
    },
    "16": {
        "class_type": "SaveImage",
        "inputs": {"images": ["8", 0], "filename_prefix": "__FILENAME_PREFIX__"}
    },
}


def _build_workflow(
    prompt: str, width: int = 512, height: int = 512,
    steps: int = 4, seed: Optional[int] = None,
    filename_prefix: str = "orchestra_draft"
) -> Tuple[dict, int]:
    """Compila il workflow SDXL e restituisce (workflow, seed_usato)."""
    import random
    wf = copy.deepcopy(SDXL_WORKFLOW_TEMPLATE)
    if seed is None:
        seed = random.randint(0, 2**32 - 1)
    wf["6"]["inputs"]["text"]             = prompt
    wf["13"]["inputs"]["width"]           = width
    wf["13"]["inputs"]["height"]          = height
    wf["14"]["inputs"]["steps"]           = steps
    wf["14"]["inputs"]["end_at_step"]     = steps
    wf["14"]["inputs"]["noise_seed"]      = seed
    wf["16"]["inputs"]["filename_prefix"] = filename_prefix
    return wf, seed


class Pipeline:

    class Valves(BaseModel):
        model_config = {"protected_namespaces": ()}

        comfyui_url:              str  = "http://172.19.0.1:8188"
        ollama_url:               str  = "http://ai-ollama-session:11434"
        model_vision:             str  = "llava:7b"
        model_vision_fallback:    str  = "moondream:v2"
        model_refine:             str  = "qwen3.5:9b"
        model_refine_fallback:    str  = "qwen2.5-coder:14b-instruct-q4_K_M"
        refine_fallback_num_gpu:  int  = 12

        draft_width:              int  = 512
        draft_height:             int  = 512
        draft_steps:              int  = 4
        draft_max_iter:           int  = 3
        final_width:              int  = 1024
        final_height:             int  = 1024
        final_steps:              int  = 6
        final_high_quality_steps: int  = 12
        early_stop_score:         int  = 9

        vram_vision_full_mb:      int  = 5000
        vram_vision_partial_mb:   int  = 3000
        vram_refine_full_mb:      int  = 6000

        # EGPU-05: ruoli GPU. ollama_url_aux vuoto = Ollama aux disattivato.
        ollama_url_aux:           str  = os.environ.get("OLLAMA_AUX_URL", "")
        aux_models:               str  = "llama3.2:3b,moondream:v2,llava:7b"   # CSV, come nel manifold
        aux_health_ttl_s:         int  = 20
        # GPU su cui gira ComfyUI: "main" (3090) o "aux" (4060). Impostata dal launcher.
        comfy_role:               str  = os.environ.get("ORCHESTRA_COMFY_ROLE", "main")
        loop_keep_alive_s:        int  = 300     # modelli LLM tenuti caricati durante il loop
        warmup_enabled:           bool = True
        warmup_main_min_free_mb:  int  = 16000   # pre-carica il refine sul main solo con tanta VRAM
        final_min_free_mb:        int  = 6000    # VRAM minima della GPU di ComfyUI per il render finale
        # Se un LLM grande (es. qwen2.5-coder:32b) occupa la GPU di ComfyUI, scaricalo per fare
        # spazio a SDXL invece di attendere e fallire.
        evict_llms_for_comfy:     bool = True
        # A fine generazione svuota ComfyUI (/free) se la GPU e grande. Misurato sull hardware: dopo l uso SDXL
        # lascia circa 7 GB occupati sulla 3090 a riposo, e un LLM da 18 GB (27B) finisce al 20% su CPU
        # (7025 MiB gia presi prima del caricamento). Con GPU piccole resta il comportamento storico.
        comfy_free_on_finish:     bool = True
        comfy_free_min_total_mb:  int  = 16000

        preflight_enabled:        bool = False
        comfyui_timeout_s:        int  = 120
        vision_timeout_s:         int  = 120
        refine_timeout_s:         int  = 120

    def __init__(self):
        self.type      = "pipe"
        self.name      = "Image Loop v2.8.0"
        self.id        = "image_loop"
        self.valves    = self.Valves()
        # self.pipelines è richiesto dal framework Pipelines per i pipe autonomi.
        # "*" significa che questo pipe è disponibile per tutte le pipeline.
        self.pipelines = ["*"]
        self._aux_healthy       = False   # EGPU-05: cache del controllo di salute dell'aux
        self._aux_checked_until = 0.0
        self._kept: dict        = {}      # modello -> URL Ollama dove l'abbiamo tenuto caricato

    # =========================================================================
    # UTILITÀ SISTEMA
    # =========================================================================

    def vram_free_mb(self, role: str = "main") -> int:
        """
        Restituisce la VRAM libera in MB della GPU `role` ("main" = 3090, "aux" = 4060).
        EVO-01: legge dal VRAM daemon di embedding_utils (0ms di latenza).
        EGPU-05: per "aux" usa get_gpu_free_mb("aux"); se non disponibile ricade sul main.
        Fallback: subprocess nvidia-smi (GPU main, prima riga); fallback finale 2000 MB.
        """
        if role == "aux" and _daemon_gpu_free_mb is not None:
            v = _daemon_gpu_free_mb("aux")
            if v > 0:
                print(f"[IMAGE_LOOP] VRAM libera aux (daemon): {v} MB", flush=True)
                return v
        if _VRAM_DAEMON_AVAILABLE:
            free = _daemon_vram_free_mb()
            print(f"[IMAGE_LOOP] VRAM libera (daemon): {free} MB", flush=True)
            return free
        # fallback subprocess — solo se embedding_utils non e' importabile
        try:
            gpu = (os.environ.get("ORCHESTRA_GPU_MAIN")
                   or os.environ.get("ORCHESTRA_GPU_ID") or "").strip()
            cmd = ["nvidia-smi"] + (["-i", gpu] if gpu else []) + [
                "--query-gpu=memory.free", "--format=csv,noheader,nounits"]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=3)
            free = int(out.decode().strip().splitlines()[0])   # EGPU-05: prima riga
            print(f"[IMAGE_LOOP] VRAM libera (subprocess): {free} MB", flush=True)
            return free
        except Exception as e:
            print(
                f"[IMAGE_LOOP] nvidia-smi fallito ({e}), uso fallback conservativo 2000MB",
                flush=True
            )
            return 2000

    # =========================================================================
    # EGPU-05 — BACKEND PER RUOLO, KEEP-ALIVE E PRE-CARICAMENTO
    # =========================================================================

    def _aux_enabled(self) -> bool:
        return bool(self.valves.ollama_url_aux.strip())

    def _aux_model_set(self) -> set:
        return {m.strip() for m in self.valves.aux_models.split(",") if m.strip()}

    def _aux_ok(self) -> bool:
        """True se l'Ollama aux e' configurato e risponde (esito in cache)."""
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
        return ok

    def _mark_aux_down(self) -> None:
        self._aux_healthy       = False
        self._aux_checked_until = time.monotonic() + self.valves.aux_health_ttl_s

    def _role_of(self, model: str) -> str:
        """GPU su cui girera' il modello: 'aux' se servito dall'aux raggiungibile, altrimenti 'main'."""
        return "aux" if (model in self._aux_model_set() and self._aux_ok()) else "main"

    def _url_for(self, model: str) -> str:
        if self._role_of(model) == "aux":
            return self.valves.ollama_url_aux.rstrip("/")
        return self.valves.ollama_url

    def _post_generate(self, model: str, payload: dict, timeout: int):
        """
        POST /api/generate sul backend del modello, con failover aux→main se l'aux e' caduto
        prima di rispondere. Restituisce (risposta, url_usato).
        """
        url = self._url_for(model)
        try:
            return requests.post(f"{url}/api/generate", json=payload, timeout=timeout), url
        except requests.ConnectionError:
            if url != self.valves.ollama_url:
                self._mark_aux_down()
                print(f"[IMAGE_LOOP] Ollama aux non raggiungibile per {model}: failover sul main", flush=True)
                main = self.valves.ollama_url
                return requests.post(f"{main}/api/generate", json=payload, timeout=timeout), main
            raise

    def _keep_alive_for(self, model: str) -> int:
        """
        keep_alive (secondi) per le chiamate del loop. 0 = scarica subito (storico).
        Se il modello gira su una GPU diversa da ComfyUI, o la sua GPU ha VRAM abbondante,
        lo teniamo caricato per le iterazioni successive; il rilascio avviene a fine loop.
        """
        role = self._role_of(model)
        if role != self.valves.comfy_role:
            return self.valves.loop_keep_alive_s
        if self.vram_free_mb(role) >= self.valves.warmup_main_min_free_mb:
            return self.valves.loop_keep_alive_s
        return 0

    def _free_comfy_if_needed(self, role: str, need_mb: int) -> bool:
        """
        Svuota ComfyUI (/free) solo se condivide la GPU `role` col modello che sta per girare
        e la VRAM libera non basta. Con GPU diverse o con VRAM abbondante lascia SDXL caricato
        (il draft successivo parte subito). Restituisce True se ha liberato.
        """
        if self.valves.comfy_role != role:
            print(f"[IMAGE_LOOP] ComfyUI su '{self.valves.comfy_role}', modello su '{role}': nessun /free", flush=True)
            return False
        free = self.vram_free_mb(role)
        if free >= need_mb:
            print(f"[IMAGE_LOOP] VRAM {role} libera {free} MB >= {need_mb} MB: nessun /free", flush=True)
            return False
        self.free_comfyui_vram()
        return True

    def _release_kept(self, only_role: Optional[str] = None) -> None:
        """Scarica (keep_alive=0) i modelli tenuti in memoria dal loop, per liberare la VRAM."""
        for model, url in list(self._kept.items()):
            if only_role is not None and (url == self.valves.ollama_url) != (only_role == "main"):
                continue
            try:
                requests.post(f"{url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=10)
                print(f"[IMAGE_LOOP] scaricato {model} da {url}", flush=True)
            except Exception as e:
                print(f"[IMAGE_LOOP] scarico {model} fallito: {e}", flush=True)
            self._kept.pop(model, None)

    def _gpu_total_mb(self, role: str) -> int:
        """VRAM totale (MB) della GPU del ruolo secondo il daemon; 0 se non disponibile."""
        if _daemon_gpu_snapshot is None:
            return 0
        try:
            return int(_daemon_gpu_snapshot().get(role, {}).get("total_mb", 0))
        except Exception:
            return 0

    def _free_comfy_on_finish(self) -> bool:
        """
        Rilascia la VRAM di ComfyUI a fine generazione (SDXL resta caricato solo DURANTE il loop). Solo su GPU
        grandi: con 8 GB il comportamento resta quello storico. Restituisce True se ha liberato.
        """
        if not self.valves.comfy_free_on_finish:
            return False
        if self._gpu_total_mb(self.valves.comfy_role) < self.valves.comfy_free_min_total_mb:
            return False
        self.free_comfyui_vram()
        return True

    def _evict_llms(self, role: str) -> int:
        """
        Scarica TUTTI i modelli LLM caricati sull'Ollama del ruolo (elenco da /api/ps) per fare
        spazio a SDXL. Restituisce quanti ne ha scaricati.
        """
        url = (self.valves.ollama_url_aux.rstrip("/")
               if (role == "aux" and self._aux_enabled()) else self.valves.ollama_url)
        try:
            models = [m.get("name") or m.get("model")
                      for m in requests.get(f"{url}/api/ps", timeout=5).json().get("models", [])]
        except Exception as e:
            print(f"[IMAGE_LOOP] /api/ps su {url} fallito: {e}", flush=True)
            return 0
        n = 0
        for m in filter(None, models):
            try:
                requests.post(f"{url}/api/generate", json={"model": m, "keep_alive": 0}, timeout=10)
                self._kept.pop(m, None)
                n += 1
                print(f"[IMAGE_LOOP] scaricato {m} da {url} (spazio per SDXL)", flush=True)
            except Exception as e:
                print(f"[IMAGE_LOOP] scarico {m} fallito: {e}", flush=True)
        if n:
            time.sleep(1.5)
        return n

    def _plan_warmup(self) -> list:
        """
        Elenca (modello, url) da pre-caricare mentre ComfyUI genera il draft.
        Sicurezza: se il modello condivide la GPU con ComfyUI serve molta VRAM libera
        (warmup_main_min_free_mb), altrimenti il pre-caricamento ruberebbe memoria a SDXL
        (caso GPU singola da 8 GB: nessun pre-caricamento). Se la GPU e' diversa basta che
        il modello ci stia per intero (soglie full di vision/refine).
        """
        plan = []
        for model, full_mb in ((self.valves.model_vision, self.valves.vram_vision_full_mb),
                               (self.valves.model_refine, self.valves.vram_refine_full_mb)):
            role = self._role_of(model)
            need = self.valves.warmup_main_min_free_mb if role == self.valves.comfy_role else full_mb
            if self.vram_free_mb(role) >= need:
                plan.append((model, self._url_for(model)))
        return plan

    def _warm_up(self, plan: list) -> None:
        """Carica i modelli (prompt vuoto) mentre ComfyUI genera il draft. Errori ignorati."""
        for model, url in plan:
            try:
                requests.post(
                    f"{url}/api/generate",
                    json={"model": model, "prompt": "", "keep_alive": self.valves.loop_keep_alive_s},
                    timeout=180,
                )
                self._kept[model] = url
                print(f"[IMAGE_LOOP] pre-caricato {model} su {url}", flush=True)
            except Exception as e:
                print(f"[IMAGE_LOOP] pre-caricamento {model} fallito (ignorato): {e}", flush=True)

    # =========================================================================
    # SELEZIONE ADATTIVA MODELLI
    # =========================================================================

    def _select_vision_params(self, vram_mb: int) -> Tuple[str, Optional[int]]:
        """
        Seleziona modello vision e num_gpu in base alla VRAM disponibile.
        FIX-03: guard contro divisione per zero se le soglie sono uguali.
        """
        full_th    = self.valves.vram_vision_full_mb
        partial_th = self.valves.vram_vision_partial_mb

        if vram_mb >= full_th:
            print(f"[IMAGE_LOOP] VRAM={vram_mb}MB → {self.valves.model_vision} full GPU", flush=True)
            return self.valves.model_vision, None

        elif vram_mb >= partial_th and full_th > partial_th:
            # FIX-03: calcola ratio solo se il range è > 0
            total_layers = 32
            ratio   = (vram_mb - partial_th) / (full_th - partial_th)
            num_gpu = max(4, int(ratio * total_layers))
            num_gpu = min(num_gpu, total_layers - 1)
            print(
                f"[IMAGE_LOOP] VRAM={vram_mb}MB → {self.valves.model_vision} num_gpu={num_gpu}",
                flush=True
            )
            return self.valves.model_vision, num_gpu

        else:
            print(
                f"[IMAGE_LOOP] VRAM={vram_mb}MB → fallback {self.valves.model_vision_fallback}",
                flush=True
            )
            return self.valves.model_vision_fallback, None

    def _select_refine_params(self, vram_mb: int) -> Tuple[str, Optional[int]]:
        if vram_mb >= self.valves.vram_refine_full_mb:
            print(f"[IMAGE_LOOP] VRAM={vram_mb}MB → {self.valves.model_refine} full GPU", flush=True)
            return self.valves.model_refine, None
        else:
            print(
                f"[IMAGE_LOOP] VRAM={vram_mb}MB → "
                f"{self.valves.model_refine_fallback} num_gpu={self.valves.refine_fallback_num_gpu}",
                flush=True
            )
            return self.valves.model_refine_fallback, self.valves.refine_fallback_num_gpu

    # =========================================================================
    # COMFYUI
    # =========================================================================

    def submit_workflow(self, workflow: dict, client_id: str) -> Optional[str]:
        try:
            resp = requests.post(
                f"{self.valves.comfyui_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("prompt_id")
        except Exception as e:
            print(f"[IMAGE_LOOP] Errore submit workflow: {e}", flush=True)
            log_event("comfyui_submit_error", {"error": str(e)})
            return None

    @staticmethod
    def _check_comfyui_error(history_entry: dict) -> Optional[str]:
        status     = history_entry.get("status", {})
        status_str = status.get("status_str", "")
        if status_str == "error":
            for msg in status.get("messages", []):
                if isinstance(msg, (list, tuple)) and len(msg) >= 2:
                    if msg[0] == "execution_error":
                        return str(msg[1])
            return "ComfyUI execution error (no detail)"
        return None

    def wait_for_result(
        self, prompt_id: str, client_id: str
    ) -> Tuple[Optional[bytes], Optional[dict]]:
        deadline = time.time() + self.valves.comfyui_timeout_s
        while time.time() < deadline:
            try:
                resp = requests.get(
                    f"{self.valves.comfyui_url}/history/{prompt_id}", timeout=10
                )
                resp.raise_for_status()
                history = resp.json()
                if prompt_id in history:
                    err = self._check_comfyui_error(history[prompt_id])
                    if err is not None:
                        print(f"[IMAGE_LOOP] ComfyUI error per {prompt_id}: {err}", flush=True)
                        log_event("comfyui_error", {"prompt_id": prompt_id, "error": err})
                        return None, None
                    outputs = history[prompt_id].get("outputs", {})
                    for node_out in outputs.values():
                        images = node_out.get("images", [])
                        if images:
                            img_info = images[0]
                            img_resp = requests.get(
                                f"{self.valves.comfyui_url}/view",
                                params={
                                    "filename": img_info["filename"],
                                    "subfolder": img_info.get("subfolder", ""),
                                    "type":      img_info.get("type", "output"),
                                },
                                timeout=30,
                            )
                            img_resp.raise_for_status()
                            return img_resp.content, {
                                "filename": img_info["filename"],
                                "subfolder": img_info.get("subfolder", ""),
                                "type":      img_info.get("type", "output"),
                            }
            except Exception as e:
                print(f"[IMAGE_LOOP] Polling error: {e}", flush=True)
            time.sleep(2)
        print(f"[IMAGE_LOOP] Timeout polling ({self.valves.comfyui_timeout_s}s)", flush=True)
        log_event("comfyui_timeout", {"prompt_id": prompt_id})
        return None, None

    def free_comfyui_vram(self) -> None:
        try:
            requests.post(
                f"{self.valves.comfyui_url}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=10,
            )
            time.sleep(1.5)
            print("[IMAGE_LOOP] VRAM ComfyUI liberata.", flush=True)
        except Exception as e:
            print(f"[IMAGE_LOOP] Errore free VRAM: {e}", flush=True)

    def generate_image(
        self, prompt: str, width: int = 512, height: int = 512,
        steps: int = 4, seed: Optional[int] = None,
        filename_prefix: str = "orchestra_draft"
    ) -> Tuple[Optional[bytes], int, Optional[dict]]:
        """Genera un'immagine. Restituisce (bytes, seed_usato, file_info)."""
        client_id = str(uuid.uuid4())
        workflow, used_seed = _build_workflow(
            prompt=prompt, width=width, height=height,
            steps=steps, seed=seed, filename_prefix=filename_prefix,
        )
        prompt_id = self.submit_workflow(workflow, client_id)
        if not prompt_id:
            return None, used_seed, None
        img_bytes, file_info = self.wait_for_result(prompt_id, client_id)
        return img_bytes, used_seed, file_info

    # =========================================================================
    # VISION
    # =========================================================================

    @staticmethod
    def _extract_json_from_response(raw: str) -> Optional[dict]:
        """
        Estrae il primo JSON valido dalla risposta del modello.
        FIX-02: priorità al blocco ```json rispetto a qualsiasi parte con "{".
        Questo evita che testo pre-fence con "{" venga erroneamente selezionato.
        """
        if "```" in raw:
            parts = raw.split("```")
            # Prima priorità: blocco esplicitamente marcato come json
            for part in parts:
                if part.startswith("json"):
                    raw = part[4:].strip()
                    break
            else:
                # Seconda priorità: primo blocco che contiene JSON
                for part in parts:
                    if "{" in part and not part.startswith("json"):
                        raw = part.strip()
                        break

        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start >= 0 and end > start:
            raw = raw[start:end]
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _parse_vision_analysis(self, raw: str, fallback_prompt: str) -> dict:
        """
        Analisi robusta della risposta vision.
        1. Tenta estrazione JSON strutturato.
        2. Se fallisce, estrae score e next_prompt con regex.
        3. Se ancora fallisce, restituisce fallback (score=5, next_prompt=originale).
        """
        data = self._extract_json_from_response(raw)
        if data:
            return {
                "score":       max(1, min(10, int(data.get("score", 5)))),
                "found":       str(data.get("found", "")),
                "missing":     str(data.get("missing", "")),
                "issues":      str(data.get("issues", "")),
                "next_prompt": str(data.get("next_prompt", fallback_prompt)),
            }
        # Fallback regex per estrarre almeno score e next_prompt
        score_match = re.search(r'"score"\s*:\s*(\d+)', raw)
        next_match  = re.search(r'"next_prompt"\s*:\s*"([^"]+)"', raw)
        return {
            "score":       int(score_match.group(1)) if score_match else 5,
            "found":       "",
            "missing":     "",
            "issues":      "Risposta non in formato JSON",
            "next_prompt": next_match.group(1) if next_match else fallback_prompt,
        }

    def analyze_image_vision(
        self, image_bytes: bytes, prompt: str,
        model: str = "llava:7b", num_gpu: Optional[int] = None
    ) -> dict:
        """Analizza l'immagine con un modello vision (richiede JSON strutturato)."""
        b64 = base64.b64encode(image_bytes).decode()
        options: dict = {
            "num_ctx":    4096,
            "temperature": 0.2,   # output più deterministico
        }
        if num_gpu is not None:
            options["num_gpu"] = num_gpu

        analysis_prompt = (
            f"Analyze this SDXL-generated image for the prompt: '{prompt}'.\n"
            "You MUST respond with a single JSON object (no markdown, no extra text) "
            "exactly like this example:\n"
            '{"score":7,"found":"red rose, green leaves","missing":"dew drops, '
            'darker background","issues":"overexposed petals, soft focus",'
            '"next_prompt":"a close-up of a red rose with dew drops, sharp focus, '
            'dark background, 8k, highly detailed"}\n\n'
            "Now output YOUR analysis for the given image and prompt. "
            "The 'next_prompt' must be a concrete, improved SDXL prompt "
            "(not a question or request) of maximum 100 words."
        )

        try:
            keep = self._keep_alive_for(model)   # EGPU-05: parametro di primo livello
            resp, used_url = self._post_generate(model, {
                "model":      model,
                "prompt":     analysis_prompt,
                "images":     [b64],
                "stream":     False,
                "keep_alive": keep,
                "options":    options,
            }, self.valves.vision_timeout_s)
            if keep > 0:
                self._kept[model] = used_url
            resp.raise_for_status()
            raw = resp.json().get("response", "{}").strip()
            return self._parse_vision_analysis(raw, prompt)
        except Exception as e:
            print(f"[IMAGE_LOOP] Errore vision ({model}): {e}", flush=True)
            log_event("vision_fallback", {"model": model, "error": str(e)})
            return {
                "score": 5, "found": "", "missing": "",
                "issues": f"errore: {str(e)[:100]}", "next_prompt": prompt,
            }

    # =========================================================================
    # REFINEMENT
    # =========================================================================

    def refine_prompt(
        self, original_prompt: str, analysis: dict,
        model: str = "qwen3.5:9b", num_gpu: Optional[int] = None
    ) -> str:
        """Raffina il prompt usando i risultati dell'analisi vision."""
        options: dict = {"num_ctx": 4096, "num_predict": 200}
        if num_gpu is not None:
            options["num_gpu"] = num_gpu

        refine_prompt_text = (
            "You are an expert SDXL prompt engineer. Improve this prompt based on the analysis.\n\n"
            f"Current prompt: {original_prompt}\n"
            f"Score: {analysis['score']}/10\n"
            f"Found: {analysis['found']}\n"
            f"Missing: {analysis['missing']}\n"
            f"Issues: {analysis['issues']}\n\n"
            "Output ONLY the improved prompt (no quotes). Keep under 100 words.\n"
            "Include quality tags: 8k, highly detailed.\n"
            "Improved SDXL prompt:"
        )

        try:
            keep = self._keep_alive_for(model)   # EGPU-05: parametro di primo livello
            resp, used_url = self._post_generate(model, {
                "model":      model,
                "prompt":     refine_prompt_text,
                "stream":     False,
                "keep_alive": keep,
                "options":    options,
            }, self.valves.refine_timeout_s)
            if keep > 0:
                self._kept[model] = used_url
            resp.raise_for_status()
            refined = resp.json().get("response", "").strip()
            refined = refined.strip('"\'`')
            if refined.lower().startswith("improved sdxl prompt:"):
                refined = refined[len("improved sdxl prompt:"):].strip()
            if refined and len(refined) > 15:
                return refined
            return analysis.get("next_prompt", original_prompt)
        except Exception as e:
            print(f"[IMAGE_LOOP] Errore refinement ({model}): {e}", flush=True)
            return analysis.get("next_prompt", original_prompt)

    # =========================================================================
    # PRE-FLIGHT
    # =========================================================================

    def preflight_optimize(self, prompt: str) -> str:
        if not self.valves.preflight_enabled:
            return prompt
        opt_prompt = (
            "You are an SDXL expert. Rate this prompt 1-5 for completeness, "
            f"then output ONE improved version.\n\nPrompt: {prompt}\n\n"
            "Output format:\nScore: X/5\nImproved: <improved prompt only>"
        )
        try:
            resp, _ = self._post_generate("llama3.2:3b", {   # EGPU-05: coordinator → aux se attivo
                "model":      "llama3.2:3b",
                "prompt":     opt_prompt,
                "stream":     False,
                "keep_alive": 600,
                "options":    {"num_ctx": 2048, "num_predict": 150},
            }, 30)
            raw = resp.json().get("response", "")
            if "Improved:" in raw:
                improved = raw.split("Improved:", 1)[1].strip()
                if improved:
                    return improved
        except Exception as e:
            print(f"[IMAGE_LOOP] Preflight error: {e}", flush=True)
        return prompt

    # =========================================================================
    # ENTRY POINT
    # =========================================================================

    def pipe(
        self, user_message: str, model_id: str, messages: list, body: dict
    ) -> Union[str, Iterator[str]]:

        def _run() -> Iterator[str]:
            original_prompt = user_message.strip()
            yield "🎨 **Image Loop v2.8.0** — avvio generazione SDXL\n\n"
            yield f"📝 *Prompt originale:* `{original_prompt}`\n\n"

            if self.valves.preflight_enabled:
                yield "🔎 Pre-flight: ottimizzazione prompt...\n"
                current_prompt = self.preflight_optimize(original_prompt)
                if current_prompt != original_prompt:
                    yield f"✨ *Prompt ottimizzato:* `{current_prompt}`\n\n"
                else:
                    yield "✅ Prompt già ottimale.\n\n"
            else:
                current_prompt = original_prompt

            # EGPU-05: se la GPU di ComfyUI non ha spazio per SDXL (es. un LLM da 20 GB caricato),
            # scarica gli LLM prima di iniziare, cosi' il primo draft non va in errore.
            if (self.valves.evict_llms_for_comfy
                    and self.vram_free_mb(self.valves.comfy_role) < self.valves.final_min_free_mb):
                n_evicted = self._evict_llms(self.valves.comfy_role)
                if n_evicted:
                    yield f"🧹 Liberati {n_evicted} modelli LLM dalla GPU {self.valves.comfy_role} per fare spazio a SDXL\n\n"

            # EGPU-05: pre-carica vision (aux) e refine (main) in parallelo ai draft.
            if self.valves.warmup_enabled:
                plan = self._plan_warmup()
                if plan:
                    names = ", ".join(f"`{m}`" for m, _ in plan)
                    yield f"⚡ Pre-caricamento in parallelo: {names}\n\n"
                    threading.Thread(target=self._warm_up, args=(plan,), daemon=True).start()

            best_prompt = current_prompt
            best_score  = 0
            best_seed   = None
            best_bytes  = None

            # ── Loop draft ──────────────────────────────────────────────────
            for iteration in range(self.valves.draft_max_iter):
                yield f"---\n### 🔄 Iterazione {iteration + 1}/{self.valves.draft_max_iter}\n\n"
                yield (
                    f"⚙️ Generazione draft {self.valves.draft_width}×{self.valves.draft_height}"
                    f" ({self.valves.draft_steps} step LCM)...\n"
                )

                draft_bytes, used_seed, _ = self.generate_image(
                    prompt=current_prompt,
                    width=self.valves.draft_width,
                    height=self.valves.draft_height,
                    steps=self.valves.draft_steps,
                    filename_prefix=f"orchestra_draft_iter{iteration + 1}",
                )

                # FIX-01: usa break invece di return per non perdere i draft
                # già accumulati nelle iterazioni precedenti.
                if draft_bytes is None:
                    yield "❌ Generazione draft fallita. Esco dal loop, uso il miglior draft ottenuto.\n"
                    break

                yield f"✅ Draft generato (seed={used_seed}, {len(draft_bytes) // 1024} KB)\n\n"
                # EGPU-05: /free solo se ComfyUI condivide la GPU col vision e la VRAM non basta.
                vision_role = self._role_of(self.valves.model_vision)
                self._free_comfy_if_needed(vision_role, self.valves.vram_vision_full_mb)

                vram_after_comfy = self.vram_free_mb(vision_role)
                vision_model, vision_num_gpu = self._select_vision_params(vram_after_comfy)
                yield f"📊 VRAM libera: **{vram_after_comfy} MB** → `{vision_model}`"
                if vision_num_gpu:
                    yield f" num_gpu={vision_num_gpu}"
                yield "\n\n🔍 Analisi vision...\n"

                analysis = self.analyze_image_vision(
                    image_bytes=draft_bytes,
                    prompt=current_prompt,
                    model=vision_model,
                    num_gpu=vision_num_gpu,
                )

                score = analysis["score"]
                yield f"**Score: {score}/10**\n"
                if analysis["found"]:   yield f"✅ Trovato: {analysis['found']}\n"
                if analysis["missing"]: yield f"❌ Mancante: {analysis['missing']}\n"
                if analysis["issues"]:  yield f"⚠️ Problemi: {analysis['issues']}\n"
                yield "\n"

                # Aggiornamento del miglior draft
                if score > best_score:
                    best_score  = score
                    best_prompt = current_prompt
                    best_seed   = used_seed
                    best_bytes  = draft_bytes
                    yield f"✨ Nuovo miglior draft (score {best_score})\n\n"
                else:
                    yield f"📉 Score non migliorato (max {best_score}). Ripristino miglior prompt.\n\n"
                    current_prompt = best_prompt
                    continue   # salta raffinamento, prossima iterazione usa best_prompt

                if score >= self.valves.early_stop_score:
                    yield f"🎯 Score {score}/10 ≥ {self.valves.early_stop_score} — early stop!\n\n"
                    break

                # Raffinamento solo se lo score è migliorato
                refine_role = self._role_of(self.valves.model_refine)
                self._free_comfy_if_needed(refine_role, self.valves.vram_refine_full_mb)
                vram_after_vision = self.vram_free_mb(refine_role)
                refine_model, refine_num_gpu = self._select_refine_params(vram_after_vision)
                yield f"📊 VRAM dopo vision: **{vram_after_vision} MB** → `{refine_model}`"
                if refine_num_gpu:
                    yield f" num_gpu={refine_num_gpu}"
                yield "\n✏️ Raffinamento prompt...\n"

                current_prompt = self.refine_prompt(
                    original_prompt=current_prompt,
                    analysis=analysis,
                    model=refine_model,
                    num_gpu=refine_num_gpu,
                )
                yield (
                    f"📝 *Nuovo prompt:* `"
                    f"{current_prompt[:100]}{'...' if len(current_prompt) > 100 else ''}`\n\n"
                )

            # FIX-04: se nessun draft è stato ottenuto, esce con errore chiaro
            if best_bytes is None and best_score == 0:
                yield "❌ **Nessun draft generato con successo. Generazione annullata.**\n"
                return

            # ── Generazione finale ────────────────────────────────────────
            yield "---\n### 🖼️ Generazione finale ad alta risoluzione\n\n"

            if best_score >= self.valves.early_stop_score and best_seed is not None:
                final_steps = self.valves.final_high_quality_steps
                final_seed  = best_seed
                yield f"✨ **Modalità massimo dettaglio** — seed riutilizzato `{final_seed}`, step={final_steps}\n"
            else:
                final_steps = self.valves.final_steps
                final_seed  = None
                yield f"ℹ️ Modalità standard — nuovo seed casuale, step={final_steps}\n"

            yield (
                f"⚙️ Generazione {self.valves.final_width}×{self.valves.final_height} "
                f"({final_steps} step LCM) con prompt migliore (score {best_score}/10)...\n"
            )
            yield (
                f"📝 *Prompt finale:* `"
                f"{best_prompt[:100]}{'...' if len(best_prompt) > 100 else ''}`\n\n"
            )

            # EGPU-05: il render finale conta la VRAM della GPU di ComfyUI. Se basta (SDXL gia'
            # caricato o 3090 libera) non serve svuotare; altrimenti si scaricano i modelli LLM
            # tenuti dal loop su quella GPU e si svuota ComfyUI, come prima.
            comfy_role = self.valves.comfy_role
            min_free   = self.valves.final_min_free_mb
            if self.vram_free_mb(comfy_role) < min_free:
                self._release_kept(only_role=comfy_role)
                if self.valves.evict_llms_for_comfy:
                    self._evict_llms(comfy_role)
                self.free_comfyui_vram()
                time.sleep(2)
                free_before_final = self.vram_free_mb(comfy_role)
                if free_before_final < min_free:
                    yield f"⚠️ VRAM bassa ({free_before_final} MB), attendo liberazione...\n"
                    for _ in range(15):
                        time.sleep(2)
                        if self.vram_free_mb(comfy_role) >= min_free:
                            break

            final_image, used_final_seed, final_file_info = self.generate_image(
                prompt=best_prompt,
                width=self.valves.final_width,
                height=self.valves.final_height,
                steps=final_steps,
                seed=final_seed,
                filename_prefix="orchestra_final",
            )

            # FIX-05: fallback esplicito con log
            if final_image is None:
                yield "⚠️ Generazione finale fallita — uso miglior draft come risultato.\n"
                log_event("final_generation_fallback", {
                    "best_score": best_score, "best_seed": best_seed
                })
                final_image     = best_bytes
                final_file_info = None

            if final_image:
                if final_file_info:
                    image_url = (
                        "https://orchestra.tregambe.com/comfyui/view"
                        f"?filename={final_file_info['filename']}"
                        f"&subfolder={final_file_info['subfolder']}"
                        f"&type={final_file_info['type']}"
                    )
                    yield f"✅ Generazione completata! ({len(final_image) // 1024} KB)\n\n"
                    yield f"![Immagine generata]({image_url})\n\n"
                else:
                    b64_final = base64.b64encode(final_image).decode()
                    yield f"✅ Generazione completata (draft fallback, {len(final_image) // 1024} KB)\n\n"
                    yield f"![Immagine generata](data:image/png;base64,{b64_final})\n\n"
                yield f"**Prompt usato:** `{best_prompt}`\n"
                yield f"**Score miglior draft:** {best_score}/10\n"
                if best_score >= self.valves.early_stop_score and best_seed is not None:
                    yield f"**Seed finale (riutilizzato):** `{used_final_seed}`\n"
                else:
                    yield f"**Seed finale:** `{used_final_seed}`\n"
            else:
                yield "❌ Nessuna immagine disponibile.\n"

        def generate() -> Iterator[str]:
            # EGPU-05: a fine loop (anche in caso di errore o interruzione) scarica i modelli
            # che il loop ha tenuto caricati, cosi' la VRAM torna libera.
            try:
                yield from _run()
            finally:
                self._release_kept()
                self._free_comfy_on_finish()

        return generate()
```

## File: ollama/pipelines/image_loop/valves.json (2 byte)

```
{}```

## File: ollama/pipelines/orchestra_bootstrap.py (6486 byte)

```
"""
Orchestra Bootstrap Filter v1.1.0
=================================
Inietta le regole del progetto (AI_BOOTSTRAP.md + AI_READ_PROTOCOL.md) come system
message all'inizio di ogni conversazione.

Richiede mount /app/ai -> $HOME/ai-sessioni:ro nel container pipelines.

CHANGELOG v1.1.0 rispetto a v1.0.0:
  FIX-1  E' un FILTRO a tutti gli effetti: self.type = "filter" + valves `pipelines`
         e `priority`. Il framework Pipelines applica inlet()/outlet() solo ai moduli
         con type == "filter" e legge valves.pipelines / valves.priority; senza
         type la v1.0.0 veniva registrata come "pipe" semplice (inlet ignorato) e
         pipes() non fa parte dell'API di Pipelines (e' di Open WebUI Functions).
  OPT-1  Contesto COMPATTO: v1.0.0 iniettava ~11 KB (≈3-4 mila token) in OGNI messaggio,
         contro un context_length di 8192 del manifold. Ora di default solo le sezioni
         essenziali (regole, percorsi, divieti, hardware) + protocollo di lettura, tetto
         3500 caratteri. Elenco file e manifest si abilitano con include_file_list /
         include_manifest quando servono davvero.
  OPT-2  Se esiste gia' un system message viene ARRICCHITO (invece di saltare tutto):
         altrimenti con un prompt di sistema del modello le regole non arrivavano mai.
         Iniezione idempotente (marker) e cache invalidata anche se cambiano i valve.
"""
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel

MARKER = "Contesto automatico Orchestra AI"


class Pipeline:
    class Valves(BaseModel):
        # Richiesti dal framework Pipelines per i filtri.
        pipelines: List[str] = ["*"]
        priority: int = 0

        enabled: bool = True
        bootstrap_path: str = "/app/ai/AI_BOOTSTRAP.md"
        manifest_path: str = "/app/ai/AI_MANIFEST.md"
        read_protocol_path: str = "/app/ai/AI_READ_PROTOCOL.md"
        # Sezioni di AI_BOOTSTRAP.md da iniettare (titolo che CONTIENE la voce, CSV).
        sections: str = "REGOLE FONDAMENTALI,Percorsi reali,Cose da NON fare,Contesto hardware"
        include_file_list: bool = False     # sezione "File tracciati" (~3 KB)
        include_manifest: bool = False      # AI_MANIFEST.md (~4 KB)
        max_bytes: int = 3500               # tetto del contesto iniettato (caratteri)

    def __init__(self):
        self.type = "filter"
        self.id = "orchestra_bootstrap"
        self.name = "Orchestra Bootstrap"
        self.valves = self.Valves()
        self._cached: Optional[str] = None
        self._cache_key: Optional[tuple] = None

    # ── lettura ──────────────────────────────────────────────────────────────
    @staticmethod
    def _read_file(path: str) -> str:
        p = Path(path)
        try:
            return p.read_text(encoding="utf-8") if p.exists() else ""
        except Exception:
            return ""

    @staticmethod
    def _mtime(path: str) -> float:
        try:
            return Path(path).stat().st_mtime
        except Exception:
            return 0.0

    def _wanted_sections(self) -> List[str]:
        wanted = [s.strip() for s in self.valves.sections.split(",") if s.strip()]
        if self.valves.include_file_list:
            wanted.append("File tracciati")
        return wanted

    def _select_sections(self, text: str) -> str:
        """Tiene l'intestazione del documento e solo le sezioni '## ' richieste."""
        wanted = self._wanted_sections()
        chunks, current = [], []
        for line in text.splitlines():
            if line.startswith("## ") and current:
                chunks.append("\n".join(current)); current = []
            current.append(line)
        if current:
            chunks.append("\n".join(current))
        kept = []
        for i, c in enumerate(chunks):
            head = c.splitlines()[0] if c else ""
            if i == 0 and not head.startswith("## "):
                kept.append(head)                    # titolo "# AI Bootstrap"
            elif any(w.lower() in head.lower() for w in wanted):
                kept.append(c.strip())
        return "\n\n".join(kept)

    def _load_context(self) -> str:
        v = self.valves
        paths = [v.bootstrap_path, v.read_protocol_path] + ([v.manifest_path] if v.include_manifest else [])
        key = (tuple(self._mtime(p) for p in paths), v.sections, v.include_file_list,
               v.include_manifest, v.max_bytes)
        if self._cached is not None and key == self._cache_key:
            return self._cached

        parts = []
        boot = self._read_file(v.bootstrap_path)
        if boot:
            parts.append("=== AI_BOOTSTRAP.md (estratto) ===\n" + self._select_sections(boot))
        proto = self._read_file(v.read_protocol_path)
        if proto:
            parts.append("=== AI_READ_PROTOCOL.md ===\n" + proto.strip())
        if v.include_manifest:
            manifest = self._read_file(v.manifest_path)
            if manifest:
                parts.append("=== AI_MANIFEST.md ===\n" + manifest.strip())

        combined = "\n\n".join(p for p in parts if p.strip())
        if len(combined) > v.max_bytes:
            combined = combined[: v.max_bytes].rstrip() + "\n\n[... troncato ...]"
        self._cached, self._cache_key = combined, key
        return combined

    # ── filtro ───────────────────────────────────────────────────────────────
    async def inlet(self, body: dict, user: Optional[dict] = None) -> dict:
        if not self.valves.enabled:
            return body
        context = self._load_context()
        if not context:
            return body

        messages = body.get("messages", [])
        header = f"{MARKER} (regole del progetto).\n\n"
        if messages and messages[0].get("role") == "system":
            existing = messages[0].get("content") or ""
            if isinstance(existing, str) and MARKER not in existing:
                messages[0] = {**messages[0], "content": existing.rstrip() + "\n\n" + header + context}
        else:
            messages.insert(0, {"role": "system", "content": header + context})
        body["messages"] = messages
        return body

    async def outlet(self, body: dict, user: Optional[dict] = None) -> dict:
        return body
```

## File: ollama/pipelines/orchestra_bootstrap/valves.json (375 byte)

```
{
  "pipelines": ["*"],
  "priority": 0,
  "enabled": true,
  "bootstrap_path": "/app/ai/AI_BOOTSTRAP.md",
  "manifest_path": "/app/ai/AI_MANIFEST.md",
  "read_protocol_path": "/app/ai/AI_READ_PROTOCOL.md",
  "sections": "REGOLE FONDAMENTALI,Percorsi reali,Cose da NON fare,Contesto hardware",
  "include_file_list": false,
  "include_manifest": false,
  "max_bytes": 3500
}
```

## File: ollama/pipelines/orchestra_evolver.py (42452 byte)

```
"""
orchestra_evolver.py — Auto-evoluzione conservativa — Orchestra
====================================================================
ATTENZIONE: Questo file NON definisce una classe Pipeline.
È un modulo utility importato dinamicamente da orchestra_manifold.py
tramite importlib, esattamente come image_loop.py.

Viene caricato da orchestra_manifold.py quando riceve il comando /evolve.
Non viene interpretato come pipeline autonoma dal framework Pipelines.

PRINCIPI FONDAMENTALI (non violare mai):
  1. MAI modificare codice senza backup automatico
  2. MAI applicare modifiche senza validazione sintattica (ast.parse)
  3. MAI modificare più di un file per ciclo evolutivo
  4. MAI applicare modifiche che superano il 30% del file
  5. Rollback automatico se smoke test fallisce
  6. In caso di dubbio → propone su JSONL, non applica

LIVELLI DI AUTONOMIA:
  0 (default) — propone, non applica mai
  1 (admin)   — può aggiornare Valves runtime
  2 (admin)   — può aggiornare esempi routing in Qdrant
  3 (admin)   — può modificare codice Python (con backup + smoke test)

COMANDI (gestiti dal manifold via /evolve):
  /evolve status           — stato sistema, proposte pendenti, backups
  /evolve analyze          — analisi patterns.jsonl + nuove proposte LLM
  /evolve apply-code N     — applica proposta N (admin, livello 3)
  /evolve rollback         — ripristina ultimo backup (admin)
  /evolve update-routing   — aggiorna esempi Qdrant (admin, livello 2)
  /evolve routing-history  — elenca gli snapshot di routing disponibili
  /evolve routing-restore <file> — ripristina uno snapshot (admin)
  /evolve apply-config     — guida alla modifica Valves via OpenWebUI
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

import requests

# ── NUOVI IMPORT per update-routing ────────────────────────────────────────
try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, PointStruct
    _QDRANT_AVAILABLE = True
except ImportError:
    _QDRANT_AVAILABLE = False
    QdrantClient = None
    PointStruct = None

# ── Percorsi ─────────────────────────────────────────────────────────────────
PIPELINES_DIR = Path(os.environ.get("PIPELINES_DIR", "/app/pipelines"))
LOGS_DIR      = Path(os.environ.get("PATTERN_LOG_PATH",
                                    "/app/logs/patterns.jsonl")).parent
PATTERN_LOG   = LOGS_DIR / "patterns.jsonl"
PROPOSALS_LOG = LOGS_DIR / "evolution_proposals.jsonl"
STATE_FILE    = LOGS_DIR / "orchestra_state.json"
BACKUP_DIR    = LOGS_DIR / "backups"

# Whitelist conservativa dei file modificabili
ALLOWED_FILES = {
    "orchestra_manifold.py",
    "rag_filter.py",
    "image_loop.py",
    "embedding_utils.py",
    "pattern_logger.py",
}

# ── NUOVE COSTANTI per update-routing ─────────────────────────────────────
VALID_AGENTS = {
    "linux_admin", "ml_engineer", "comfy_integrator",
    "design_critic", "orchestra_dev", "coordinator"
}
MAX_QUERIES_TO_PROCESS = 50
CLASSIFIER_TIMEOUT_S = 15
MIN_FREQUENCY = 2

QDRANT_URL = "http://ai-qdrant-session:6333"
ROUTING_COLLECTION = "orchestra_routing"
ROUTING_SNAPSHOTS_DIR = Path(os.environ.get("DOCS_ROOT", "/app/document-ai")) / "routing_snapshots"

# NOTA: embed_for_routing viene importato DINAMICAMENTE dentro _cmd_update_routing
#       per evitare problemi di path e dipendenze circolari.

# ── STATO ────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "version": "1.0",
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "evolution_cycles": 0,
            "applied_changes": [],
        }
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"version": "1.0", "created_at": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
                "evolution_cycles": 0, "applied_changes": []}

def _save_state(state: dict) -> None:
    state["last_updated"] = datetime.now().isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ── BACKUP ───────────────────────────────────────────────────────────────────

def _backup(filepath: Path) -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"{filepath.name}.{ts}.bak"
    shutil.copy2(filepath, dest)
    return str(dest)

def _restore(backup_path: str, target: Path) -> bool:
    src = Path(backup_path)
    if not src.exists():
        return False
    shutil.copy2(src, target)
    return True

def _list_backups() -> list:
    if not BACKUP_DIR.exists():
        return []
    files = sorted(BACKUP_DIR.glob("*.bak"), key=os.path.getmtime, reverse=True)
    return [{"file": f.name, "created": datetime.fromtimestamp(os.path.getmtime(f)).isoformat()} for f in files]

# ── VALIDAZIONE ─────────────────────────────────────────────────────────────

def _validate(original: str, modified: str, filename: str) -> tuple:
    try:
        ast.parse(modified)
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"
    for pattern in ["os.system(", "eval(", "exec(", "__import__("]:
        if pattern in modified and pattern not in original:
            return False, f"Pattern pericoloso introdotto: {pattern}"
    if len(modified) < 0.7 * len(original) or len(modified) > 1.3 * len(original):
        return False, "Modifica troppo invasiva (>30%)"
    return True, "OK"

# ── SMOKE TEST ──────────────────────────────────────────────────────────────

def _smoke_test(filepath: Path) -> tuple:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("test_module", filepath)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, "Pipeline"):
            pip = module.Pipeline()
            return True, f"type={getattr(pip, 'type', '?')} name={getattr(pip, 'name', '?')}"
        return True, "modulo valido (nessuna Pipeline)"
    except Exception as e:
        return False, str(e)

# ── PROPOSALS ───────────────────────────────────────────────────────────────

def _load_proposals(status: Optional[str] = None) -> list:
    if not PROPOSALS_LOG.exists():
        return []
    out = []
    for line in PROPOSALS_LOG.read_text().strip().splitlines():
        try:
            p = json.loads(line)
            if status and p.get("status") != status:
                continue
            out.append(p)
        except Exception:
            pass
    return out

def _write_proposal(proposal: dict) -> int:
    proposals = _load_proposals()
    new_id = max([p.get("id", 0) for p in proposals], default=-1) + 1
    proposal["id"] = new_id
    proposal["created_at"] = datetime.now().isoformat()
    proposal["status"] = "pending"
    PROPOSALS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(PROPOSALS_LOG, "a") as f:
        f.write(json.dumps(proposal) + "\n")
    return new_id

def _mark_proposal(pid: int, status: str, note: str = "") -> None:
    lines = []
    if PROPOSALS_LOG.exists():
        lines = PROPOSALS_LOG.read_text().strip().splitlines()
    new_lines = []
    for line in lines:
        try:
            p = json.loads(line)
            if p.get("id") == pid:
                p["status"] = status
                if note:
                    p["note"] = note
                p["updated_at"] = datetime.now().isoformat()
            new_lines.append(json.dumps(p))
        except Exception:
            new_lines.append(line)
    PROPOSALS_LOG.write_text("\n".join(new_lines) + "\n")

# ── ANALYZE PATTERNS ────────────────────────────────────────────────────────

def _analyze_patterns() -> dict:
    stats = {
        "period_start": None, "period_end": None,
        "total_messages": 0, "ungrounded": 0,
        "command_repeats": 0, "generate_commands": 0,
        "vision_fallbacks": 0, "ungrounded_queries": [],
        "repeated_commands": [],
    }
    if not PATTERN_LOG.exists():
        return stats
    for line in PATTERN_LOG.read_text().strip().splitlines():
        try:
            event = json.loads(line)
            ts    = event.get("timestamp", "")
            etype = event.get("type", "")
            data  = event.get("data", {})
            if not stats["period_start"] or ts < stats["period_start"]:
                stats["period_start"] = ts
            if not stats["period_end"] or ts > stats["period_end"]:
                stats["period_end"] = ts
            if etype == "user_message":
                stats["total_messages"] += 1
            elif etype == "ungrounded_response":
                stats["ungrounded"] += 1
                q = data.get("query", "")
                if q and len(stats["ungrounded_queries"]) < 20:
                    stats["ungrounded_queries"].append(q[:120])
            elif etype == "command_repeat":
                stats["command_repeats"] += 1
                t = data.get("text", "")
                if t and len(stats["repeated_commands"]) < 10:
                    stats["repeated_commands"].append(t[:80])
            elif etype == "generate_command":
                stats["generate_commands"] += 1
            elif etype == "vision_fallback":
                stats["vision_fallbacks"] += 1
        except Exception:
            pass
    if stats["total_messages"] > 0:
        stats["ungrounded_rate"] = round(stats["ungrounded"] / stats["total_messages"], 3)
    else:
        stats["ungrounded_rate"] = 0.0
    return stats

# ── FUNZIONI HELPER per update-routing ────────────────────────────────────

def _load_ungrounded_queries(min_frequency: int = MIN_FREQUENCY,
                             max_queries: int = MAX_QUERIES_TO_PROCESS) -> list:
    """
    Legge patterns.jsonl e restituisce le query non coperte più frequenti.
    Ritorna una lista di dict: [{"query": str, "count": int}, ...]
    Ordinata per conteggio decrescente, limitata a max_queries.
    """
    if not PATTERN_LOG.exists():
        return []
    
    query_counts: dict = {}
    for line in PATTERN_LOG.read_text().strip().splitlines():
        try:
            event = json.loads(line)
            if event.get("type") != "ungrounded_response":
                continue
            q = event.get("data", {}).get("query", "").strip()
            if q:
                query_counts[q] = query_counts.get(q, 0) + 1
        except Exception:
            pass
    
    filtered = [(q, c) for q, c in query_counts.items() if c >= min_frequency]
    filtered.sort(key=lambda x: x[1], reverse=True)
    filtered = filtered[:max_queries]
    
    return [{"query": q, "count": c} for q, c in filtered]


def _classify_query(query: str, ollama_url: str, timeout: int = CLASSIFIER_TIMEOUT_S) -> Optional[str]:
    """
    Classifica una query in uno degli agenti validi usando LLM on-demand.
    Tenta prima con qwen3.5:9b, poi con llama3.2:3b in caso di timeout/errore.
    Restituisce il nome dell'agente validato o None se la classificazione fallisce.
    """
    models_to_try = [
        ("qwen3.5:9b", {"num_ctx": 512, "num_predict": 20, "keep_alive": 0}),
        ("llama3.2:3b", {"num_ctx": 512, "num_predict": 20, "keep_alive": 600}),
    ]
    
    prompt = (
        "Classify this query into exactly one of these agents: linux_admin, "
        "ml_engineer, comfy_integrator, design_critic, orchestra_dev, coordinator.\n"
        "Reply ONLY with the agent name, no other text.\n"
        f"Query: '{query}'"
    )
    
    for model, opts in models_to_try:
        try:
            resp = requests.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": opts,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            result = resp.json().get("response", "").strip().lower()
            result = result.strip('"\'. \t\n')
            if result in VALID_AGENTS:
                return result
        except Exception as e:
            print(f"[EVOLVER] Classificazione fallita con {model}: {e}", flush=True)
            continue
    
    return None


def _snapshot_routing(qdrant_url: str = QDRANT_URL,
                      collection: str = ROUTING_COLLECTION,
                      snapshots_dir: Path = ROUTING_SNAPSHOTS_DIR) -> Optional[Path]:
    """
    Salva uno snapshot completo della collection di routing su disco.
    Restituisce il Path del file creato, o None in caso di errore.
    """
    if not _QDRANT_AVAILABLE:
        print("[EVOLVER] Qdrant client non disponibile per snapshot", flush=True)
        return None
    
    try:
        client = QdrantClient(url=qdrant_url, timeout=10)
        points, next_offset = client.scroll(
            collection_name=collection,
            with_vectors=True,
            with_payload=True,
            limit=1000,
        )
        all_points = list(points)
        while next_offset:
            points, next_offset = client.scroll(
                collection_name=collection,
                with_vectors=True,
                with_payload=True,
                limit=1000,
                offset=next_offset,
            )
            all_points.extend(points)
        
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_path = snapshots_dir / f"routing_{timestamp}.json"
        
        data = {
            "timestamp": timestamp,
            "collection": collection,
            "count": len(all_points),
            "points": [
                {
                    "id": p.id,
                    "payload": p.payload,
                    "vector": p.vector,
                }
                for p in all_points
            ],
        }
        snapshot_path.write_text(json.dumps(data, indent=2))
        print(f"[EVOLVER] Snapshot salvato: {snapshot_path}", flush=True)
        return snapshot_path
    except Exception as e:
        print(f"[EVOLVER] Errore snapshot: {e}", flush=True)
        return None


def _restore_routing_from_backup(backup_path: Path,
                                 qdrant_url: str = QDRANT_URL,
                                 collection: str = ROUTING_COLLECTION) -> bool:
    """
    Ripristina la collection di routing da un file di snapshot JSON.
    Cancella tutti i punti esistenti e reinserisce quelli del backup.
    Restituisce True se il ripristino è riuscito.
    """
    if not _QDRANT_AVAILABLE:
        print("[EVOLVER] Qdrant client non disponibile per ripristino", flush=True)
        return False
    if not backup_path.exists():
        print(f"[EVOLVER] File backup non trovato: {backup_path}", flush=True)
        return False
    
    try:
        data = json.loads(backup_path.read_text())
        points_raw = data.get("points", [])
        if not points_raw:
            print("[EVOLVER] Backup vuoto, annullato", flush=True)
            return False
        
        client = QdrantClient(url=qdrant_url, timeout=10)
        # Svuota la collection
        client.delete(collection_name=collection, points_selector=Filter())
        
        original_points = [
            PointStruct(
                id=p["id"],
                vector=p["vector"],
                payload=p["payload"],
            )
            for p in points_raw
        ]
        client.upsert(collection_name=collection, points=original_points)
        print(f"[EVOLVER] Ripristino completato: {len(original_points)} punti da {backup_path.name}", flush=True)
        return True
    except Exception as e:
        print(f"[EVOLVER] Errore ripristino: {e}", flush=True)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CLASSE PRINCIPALE (no Pipeline, importata come modulo)
# ─────────────────────────────────────────────────────────────────────────────

class OrchestraEvolver:
    """
    Classe di auto-evoluzione conservativa.
    Istanziata da orchestra_manifold.py via importlib.
    """

    def __init__(self):
        self.ollama_url    = os.environ.get("OLLAMA_URL", "http://ai-ollama-session:11434")
        self.model_analyst = "qwen2.5-coder:14b-instruct-q4_K_M"
        self.autonomy      = 0   # default: solo proposta, mai applicazione
        self._state        = _load_state()

    # ── Entry point dal manifold ──────────────────────────────────────────

    def handle_evolve(self, text: str, role: str = "user") -> Iterator[str]:
        parts   = text.strip().split(None, 2)
        command = parts[1].lower() if len(parts) >= 2 else "status"
        arg     = parts[2].strip() if len(parts) >= 3 else ""

        ADMIN_ONLY = {"apply-code", "rollback", "update-routing", "apply-config", "routing-restore"}
        if command in ADMIN_ONLY and role != "admin":
            yield f"🔒 **`/evolve {command}` riservato agli amministratori.**\n"
            return

        dispatch = {
            "status":           self._cmd_status,
            "analyze":          self._cmd_analyze,
            "apply-code":       lambda: self._cmd_apply_code(arg),
            "rollback":         self._cmd_rollback,
            "update-routing":   self._cmd_update_routing,
            "routing-history":  self._cmd_routing_history,
            "routing-restore":  lambda: self._cmd_routing_restore(arg),
            "apply-config":     self._cmd_apply_config,
        }
        fn = dispatch.get(command)
        if fn:
            yield from fn()
        else:
            yield self._help()

    # ── STATUS ────────────────────────────────────────────────────────────

    def _cmd_status(self) -> Iterator[str]:
        yield "🔄 **Orchestra Evolver — Stato sistema**\n\n"
        s = self._state
        yield f"- Cicli evolutivi: **{s['evolution_cycles']}**\n"
        yield f"- Livello autonomia: **{self.autonomy}** (0=solo proposte)\n"
        yield f"- Ultimo aggiornamento: {s['last_updated'][:19]}\n\n"

        pending = _load_proposals(status="pending")
        yield f"**Proposte pendenti:** {len(pending)}\n\n"
        if pending:
            yield "| ID | File | Rischio | Descrizione |\n"
            yield "|----|------|---------|-------------|\n"
            for p in pending[-8:]:
                risk_e = {"basso": "🟢", "medio": "🟡", "alto": "🔴"}.get(p.get("risk", ""), "⚪")
                yield (
                    f"| {p['id']} | `{p.get('file', '?')}` | "
                    f"{risk_e} {p.get('risk', '?')} | "
                    f"{p.get('description', '')[:45]} |\n"
                )
            yield "\n"

        backups = _list_backups()
        if backups:
            yield f"**Backup disponibili:** {len(backups)}\n"
            yield f"Più recente: `{backups[0]['file']}` — {backups[0]['created']}\n\n"
        else:
            yield "**Backup:** nessuno ancora.\n\n"

        yield self._help()

    # ── ANALYZE ───────────────────────────────────────────────────────────

    def _cmd_analyze(self) -> Iterator[str]:
        yield "🔬 **Analisi sistema e generazione proposte**\n\n"
        stats = _analyze_patterns()

        if stats["period_start"]:
            yield f"**Periodo:** {stats['period_start'][:10]} → {stats['period_end'][:10]}\n"
        else:
            yield "⚠️ Nessun evento nel log. Inizia ad usare Orchestra per raccogliere dati.\n\n"
            return

        yield f"- Messaggi: {stats['total_messages']}\n"
        yield f"- Risposte senza RAG: {stats['ungrounded']} ({stats.get('ungrounded_rate', 0):.1%})\n"
        yield f"- /generate: {stats['generate_commands']}\n"
        yield f"- Vision fallback: {stats['vision_fallbacks']}\n"
        yield f"- Comandi ripetuti: {stats['command_repeats']}\n\n"

        if stats["ungrounded_queries"]:
            yield "**Esempi query senza RAG:**\n"
            for q in stats["ungrounded_queries"][-3:]:
                yield f"  - `{q}`\n"
            yield "\n"

        yield "🤖 *Generazione proposte con ORCHESTRA_DEV...*\n\n"
        yield from self._generate_proposals_llm(stats)

    def _generate_proposals_llm(self, stats: dict) -> Iterator[str]:
        prompt = f"""Sei ORCHESTRA_DEV. Analizza le metriche di Orchestra e genera proposte CONSERVATIVE.

Metriche:
- Risposte senza RAG: {stats['ungrounded']} ({stats.get('ungrounded_rate', 0):.1%})
- Vision fallback: {stats['vision_fallbacks']}
- Comandi ripetuti: {stats['command_repeats']}
- Query tecniche senza RAG (esempi): {stats['ungrounded_queries'][-3:]}

REGOLE ASSOLUTE per le proposte:
1. Ogni proposta modifica UNA SOLA funzione in UN SOLO file
2. La modifica deve essere < 15 righe di codice
3. Nessuna modifica alle interfacce pubbliche (pipe, inlet, outlet)
4. Nessuna aggiunta di dipendenze esterne
5. Rischio ALTO = non proporre (troppo rischioso per applicazione automatica)

File modificabili: rag_filter.py, orchestra_manifold.py, image_loop.py, embedding_utils.py, pattern_logger.py

Rispondi SOLO con JSON array (niente markdown, niente spiegazioni):
[
  {{
    "file": "nome_file.py",
    "function": "nome_funzione",
    "description": "descrizione breve (max 60 char)",
    "problem": "problema specifico osservato",
    "solution": "soluzione concisa",
    "risk": "basso",
    "code_diff": "snippet Python della modifica (max 15 righe)"
  }}
]"""

        try:
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model":   self.model_analyst,
                    "prompt":  prompt,
                    "stream":  False,
                    "options": {"num_ctx": 4096, "num_predict": 2048, "keep_alive": 0},
                },
                timeout=300,
            )
            raw = resp.json().get("response", "[]").strip()

            # Pulizia JSON
            if "```" in raw:
                for part in raw.split("```"):
                    if part.startswith("json"):
                        raw = part[4:].strip()
                        break
                    elif "[" in part:
                        raw = part.strip()
                        break
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            if start >= 0 and end > start:
                raw = raw[start:end]

            proposals = json.loads(raw)
            saved_ids = []
            for p in proposals:
                if isinstance(p, dict):
                    if p.get("file") not in ALLOWED_FILES:
                        continue
                    if p.get("risk", "alto") == "alto":
                        continue   # rifiuta proposte ad alto rischio
                    pid = _write_proposal(p)
                    saved_ids.append(pid)

            yield f"✅ **{len(saved_ids)} proposte salvate** (ID: {saved_ids})\n\n"
            for p in proposals:
                if p.get("file") not in ALLOWED_FILES:
                    continue
                risk_e = {"basso": "🟢", "medio": "🟡"}.get(p.get("risk", ""), "⚪")
                yield f"{risk_e} **`{p.get('file')}`** — {p.get('description', '')}\n"
                yield f"   📍 Funzione: `{p.get('function', '?')}`\n"
                yield f"   ⚠️ Problema: {p.get('problem', '')}\n"
                yield f"   💡 Soluzione: {p.get('solution', '')}\n\n"

            if self.autonomy < 3:
                yield (
                    "> 💡 Per applicare una proposta: `/evolve apply-code <ID>` (admin)\n"
                    "> Il sistema eseguirà backup automatico e smoke test prima di applicare.\n"
                )

        except json.JSONDecodeError as e:
            yield f"⚠️ Parsing JSON fallito: {e}\n"
            yield "Il LLM non ha risposto in formato JSON. Riprova con `/evolve analyze`.\n"
        except Exception as e:
            yield f"❌ Errore generazione proposte: {e}\n"

    # ── APPLY-CODE ────────────────────────────────────────────────────────

    def _cmd_apply_code(self, arg: str) -> Iterator[str]:
        # Parsea l'ID
        try:
            pid = int(arg.strip())
        except (ValueError, AttributeError):
            yield "❌ Specifica l'ID: `/evolve apply-code <N>`\n"
            return

        yield f"🔧 **Applicazione proposta #{pid}**\n\n"

        # Carica proposta
        proposals = _load_proposals()
        proposal  = next((p for p in proposals if p.get("id") == pid), None)
        if not proposal:
            yield f"❌ Proposta #{pid} non trovata.\n"
            return
        if proposal.get("status") != "pending":
            yield f"⚠️ Proposta #{pid} in stato `{proposal['status']}` — non applicabile.\n"
            return

        filename = proposal.get("file", "")
        if filename not in ALLOWED_FILES:
            yield f"❌ `{filename}` non nella whitelist dei file modificabili.\n"
            _mark_proposal(pid, "rejected", "file non in whitelist")
            return

        filepath = PIPELINES_DIR / filename
        if not filepath.exists():
            yield f"❌ File non trovato: `{filepath}`\n"
            return

        original_code = filepath.read_text()

        # Genera codice modificato via LLM
        yield "📝 Generazione modifica via LLM...\n"
        modified_code = self._generate_modified_code(original_code, proposal)
        if not modified_code:
            yield "❌ Generazione modifica fallita.\n"
            _mark_proposal(pid, "failed", "LLM non ha prodotto codice valido")
            return

        # Validazione
        yield "🔍 Validazione...\n"
        ok, reason = _validate(original_code, modified_code, filename)
        if not ok:
            yield f"❌ Validazione fallita: {reason}\n"
            _mark_proposal(pid, "rejected", reason)
            return
        yield "✅ Validazione: OK\n"

        # Backup
        backup_path = _backup(filepath)
        yield f"💾 Backup: `{Path(backup_path).name}`\n"

        # Scrivi modifica
        filepath.write_text(modified_code)
        yield "📄 Modifica applicata.\n"

        # Smoke test
        yield "🧪 Smoke test...\n"
        ok, msg = _smoke_test(filepath)
        if not ok:
            yield f"❌ Smoke test fallito: {msg}\n"
            yield "🔄 Rollback automatico...\n"
            _restore(backup_path, filepath)
            yield "✅ Rollback completato. File ripristinato.\n"
            _mark_proposal(pid, "rolled_back", msg)
            return

        yield f"✅ Smoke test: {msg}\n\n"

        # Registra nel state
        self._state["applied_changes"].append({
            "timestamp":   datetime.now().isoformat(),
            "proposal_id": pid,
            "description": proposal.get("description", ""),
            "file":        filename,
            "backup":      backup_path,
            "cycle":       self._state["evolution_cycles"],
        })
        self._state["evolution_cycles"] += 1
        _save_state(self._state)
        _mark_proposal(pid, "applied", "smoke test OK")

        yield f"🎉 **Proposta #{pid} applicata con successo!**\n\n"
        yield "> **Importante:** riavviare il container per attivare le modifiche:\n"
        yield "> ```bash\n> docker restart ai-pipelines-session\n> ```\n"

    def _generate_modified_code(self, original: str, proposal: dict) -> Optional[str]:
        prompt = (
            f"Modifica il file Python applicando SOLO questa correzione specifica.\n\n"
            f"File: {proposal.get('file')}\n"
            f"Funzione da modificare: {proposal.get('function')}\n"
            f"Problema: {proposal.get('problem')}\n"
            f"Soluzione: {proposal.get('solution')}\n"
            f"Snippet suggerito:\n{proposal.get('code_diff', '')}\n\n"
            f"REGOLE ASSOLUTE:\n"
            f"1. Rispondi SOLO con il file Python completo\n"
            f"2. NON modificare nulla al di fuori della funzione specificata\n"
            f"3. NON aggiungere import non presenti nell'originale\n"
            f"4. Mantieni tutti i docstring e commenti\n"
            f"5. Niente spiegazioni, solo il codice\n\n"
            f"CODICE ORIGINALE:\n```python\n{original}\n```\n\n"
            f"CODICE MODIFICATO (solo il file completo):"
        )
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model":   self.model_analyst,
                    "prompt":  prompt,
                    "stream":  False,
                    "options": {"num_ctx": 16384, "num_predict": 8192, "keep_alive": 0},
                },
                timeout=300,
            )
            raw = resp.json().get("response", "").strip()
            # Estrai solo il Python
            if "```python" in raw:
                raw = raw.split("```python")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            return raw if len(raw) > 200 else None
        except Exception as e:
            print(f"[EVOLVER] Errore generazione codice: {e}", flush=True)
            return None

    # ── ROLLBACK ──────────────────────────────────────────────────────────

    def _cmd_rollback(self) -> Iterator[str]:
        yield "🔄 **Rollback — ultimo backup registrato**\n\n"
        changes = self._state.get("applied_changes", [])
        if not changes:
            yield "⚠️ Nessuna modifica registrata. Nulla da ripristinare.\n"
            return
        last = changes[-1]
        fp   = PIPELINES_DIR / last["file"]
        ok   = _restore(last["backup"], fp)
        if ok:
            yield f"✅ Ripristinato: `{last['file']}` da `{Path(last['backup']).name}`\n"
            yield f"   Modifica annullata: {last.get('description', '?')}\n\n"
            yield "> Riavviare: `docker restart ai-pipelines-session`\n"
        else:
            yield f"❌ Rollback fallito. Verifica manualmente il backup:\n"
            yield f"   `{last['backup']}`\n"

    # ── UPDATE-ROUTING (nuova versione completa) ──────────────────────────

    def _cmd_update_routing(self) -> Iterator[str]:
        """Aggiorna gli esempi di routing in Qdrant con backup e rollback automatico."""
        yield "🔀 **Aggiornamento esempi routing**\n\n"

        # Pre-checks
        if not _QDRANT_AVAILABLE:
            yield "❌ Libreria `qdrant_client` non installata nel container.\n"
            return

        # Import dinamico di embed_for_routing (risolve il problema di path)
        sys.path.insert(0, "/app/pipelines")
        try:
            from embedding_utils import embed_for_routing
        except ImportError:
            yield "❌ Funzione `embed_for_routing` non disponibile (modulo non importabile).\n"
            return

        # Snapshot pre-operazione
        yield "💾 Creazione snapshot di sicurezza...\n"
        snapshot_path = _snapshot_routing()
        if snapshot_path is None:
            yield "❌ Impossibile creare lo snapshot. Operazione annullata.\n"
            return
        yield f"✅ Snapshot salvato: `{snapshot_path.name}`\n\n"

        # Carica query non coperte
        yield "📊 Analisi query senza RAG...\n"
        queries = _load_ungrounded_queries()
        if not queries:
            yield "✅ Nessuna query senza RAG con frequenza sufficiente. Routing già ben calibrato.\n"
            return
        yield f"Trovate **{len(queries)}** query candidate.\n\n"

        # Classificazione
        yield "🤖 Classificazione query in corso...\n\n"
        classified = []  # lista di (query, agent)
        for i, entry in enumerate(queries, 1):
            q = entry["query"]
            yield f"  `{i}/{len(queries)}` classificazione: `{q[:60]}{'...' if len(q)>60 else ''}` → "
            agent = _classify_query(q, self.ollama_url)
            if agent:
                yield f"**{agent}**\n"
                classified.append((q, agent))
            else:
                yield f"⚠️ saltata (classificazione fallita)\n"

        if not classified:
            yield "\n❌ Nessuna query classificata con successo.\n"
            return

        yield f"\n✅ **{len(classified)}** query classificate.\n\n"

        # Preparazione nuovi punti
        yield "🧮 Calcolo embedding e preparazione punti...\n"
        client = QdrantClient(url=QDRANT_URL, timeout=10)

        # Trova il massimo ID esistente
        existing_ids = []
        try:
            existing, _ = client.scroll(
                collection_name=ROUTING_COLLECTION,
                with_vectors=False,
                with_payload=False,
                limit=1000,
            )
            existing_ids = [p.id for p in existing]
        except Exception as e:
            yield f"⚠️ Errore lettura ID esistenti: {e}. Uso ID da 1000.\n"

        next_id = max(existing_ids) + 1 if existing_ids else 1000
        new_points = []
        skipped = 0

        for q, agent in classified:
            vec = embed_for_routing(q)
            if vec is None:
                skipped += 1
                continue
            new_points.append(PointStruct(
                id=next_id,
                vector=vec,
                payload={"agent": agent, "source": "auto_routing"},
            ))
            next_id += 1

        if not new_points:
            yield "❌ Nessun embedding calcolato. Operazione annullata.\n"
            return

        yield f"✅ {len(new_points)} punti preparati (ID {new_points[0].id}–{new_points[-1].id}).\n"
        if skipped:
            yield f"⚠️ {skipped} query scartate (embedding fallito).\n"
        yield "\n"

        # Inserimento atomico
        yield "📥 Inserimento in Qdrant...\n"
        try:
            client.upsert(collection_name=ROUTING_COLLECTION, points=new_points)
            yield "✅ Upsert completato.\n"
        except Exception as e:
            yield f"❌ Errore upsert: {e}\n"
            yield "🔄 Ripristino snapshot...\n"
            if _restore_routing_from_backup(snapshot_path):
                yield "✅ Collection ripristinata allo stato precedente.\n"
            else:
                yield "❌ Anche il ripristino è fallito! Contatta un amministratore.\n"
            return

        # Verifica post-inserimento
        yield "🔍 Verifica consistenza...\n"
        try:
            info = client.get_collection(ROUTING_COLLECTION)
            new_count = info.points_count
            old_count = len(existing_ids)
            expected = old_count + len(new_points)
            if new_count != expected:
                yield f"⚠️ Conteggio errato: attesi {expected}, trovati {new_count}. Ripristino...\n"
                _restore_routing_from_backup(snapshot_path)
                yield "✅ Collection ripristinata.\n"
                return
            yield f"✅ Conteggio OK: {old_count} → {new_count} punti.\n"
        except Exception as e:
            yield f"⚠️ Verifica fallita: {e}. Ripristino...\n"
            _restore_routing_from_backup(snapshot_path)
            yield "✅ Collection ripristinata.\n"
            return

        # Report finale
        yield "\n📊 **Riepilogo aggiornamento**\n\n"
        yield "| Agente | Nuovi esempi |\n"
        yield "|--------|-------------|\n"
        agent_counts = {}
        for p in new_points:
            a = p.payload["agent"]
            agent_counts[a] = agent_counts.get(a, 0) + 1
        for agent in sorted(agent_counts):
            yield f"| {agent} | {agent_counts[agent]} |\n"

        yield f"\n💾 Snapshot pre-operazione: `{snapshot_path.name}`\n"
        yield f"📁 Directory snapshot: `{ROUTING_SNAPSHOTS_DIR}`\n\n"
        yield (
            "> **Importante:** per attivare i nuovi esempi, riavvia il container:\n"
            "> `docker restart ai-pipelines-session`\n"
        )

    # ── ROUTING-HISTORY ────────────────────────────────────────────────────

    def _cmd_routing_history(self) -> Iterator[str]:
        """Elenca gli snapshot di routing disponibili."""
        yield "📜 **Snapshot di routing disponibili**\n\n"
        if not ROUTING_SNAPSHOTS_DIR.exists():
            yield "Nessuno snapshot trovato.\n"
            return
        snapshots = sorted(ROUTING_SNAPSHOTS_DIR.glob("routing_*.json"), reverse=True)
        if not snapshots:
            yield "Nessuno snapshot trovato.\n"
            return
        yield f"Directory: `{ROUTING_SNAPSHOTS_DIR}`\n\n"
        for s in snapshots[:10]:  # ultimi 10
            try:
                data = json.loads(s.read_text())
                count = data.get("count", "?")
                ts = data.get("timestamp", s.stem)
                yield f"- `{s.name}` — {count} punti, {ts}\n"
            except Exception:
                yield f"- `{s.name}` — file non valido\n"
        yield "\nPer ripristinare: `/evolve routing-restore <nome_file>`\n"

    # ── ROUTING-RESTORE ────────────────────────────────────────────────────

    def _cmd_routing_restore(self, filename: str) -> Iterator[str]:
        """Ripristina uno snapshot di routing specifico."""
        if not filename:
            yield "❌ Specifica il nome del file snapshot.\n"
            yield "Esempio: `/evolve routing-restore routing_20260504_153000.json`\n"
            return

        backup_path = ROUTING_SNAPSHOTS_DIR / filename
        if not backup_path.exists():
            yield f"❌ File non trovato: `{backup_path}`\n"
            yield "Usa `/evolve routing-history` per vedere gli snapshot disponibili.\n"
            return

        yield f"🔄 **Ripristino da `{filename}`**\n\n"
        if _restore_routing_from_backup(backup_path):
            yield "✅ Collection ripristinata con successo.\n"
        else:
            yield "❌ Ripristino fallito.\n"

    # ── APPLY-CONFIG ──────────────────────────────────────────────────────

    def _cmd_apply_config(self) -> Iterator[str]:
        yield (
            "⚙️ **Modifica configurazione Valves**\n\n"
            "Per modificare i Valves di una pipeline, usa l'interfaccia OpenWebUI:\n"
            "1. Clicca sull'icona ⚙️ accanto al modello **🎼 Orchestra**\n"
            "2. Modifica i valori desiderati\n"
            "3. Salva\n\n"
            "Le modifiche ai Valves sono immediate e non richiedono riavvio.\n"
        )

    # ── HELP ──────────────────────────────────────────────────────────────

    def _help(self) -> str:
        return (
            "**Comandi disponibili:**\n\n"
            "| Comando | Descrizione | Ruolo |\n"
            "|---------|-------------|-------|\n"
            "| `/evolve status` | Stato, proposte, backup | Tutti |\n"
            "| `/evolve analyze` | Analisi pattern + proposte LLM | Tutti |\n"
            "| `/evolve apply-code N` | Applica proposta N (backup+test) | Admin |\n"
            "| `/evolve rollback` | Ripristina ultimo backup | Admin |\n"
            "| `/evolve update-routing` | Aggiorna esempi Qdrant | Admin |\n"
            "| `/evolve routing-history` | Elenca snapshot routing | Tutti |\n"
            "| `/evolve routing-restore <file>` | Ripristina snapshot routing | Admin |\n"
            "| `/evolve apply-config` | Modifica Valves OpenWebUI | Admin |\n"
        )


# ── Istanza globale (importata da manifold) ───────────────────────────────
class Pipeline(OrchestraEvolver):
    """Alias per compatibilità con il loader generico del manifold."""
    pass
```

## File: ollama/pipelines/orchestra_evolver/valves.json (2 byte)

```
{}```

## File: ollama/pipelines/orchestra_manifold.py (72912 byte)

```
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
```

## File: ollama/pipelines/orchestra_manifold/valves.json (2 byte)

```
{}```

## File: ollama/pipelines/pattern_logger.py (1276 byte)

```
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
```

## File: ollama/pipelines/pattern_logger/valves.json (2 byte)

```
{}```

## File: ollama/pipelines/rag_filter.py (17384 byte)

```
"""
RAG Filter v1.6.0 — Orchestra
==================================
Filtro Pipelines con lazy loading del modello di embedding RAG.

CHANGELOG v1.6.0 rispetto a v1.5.5:
  FIX-01  _get_qdrant(): il client Qdrant non viene più memorizzato se la
          connessione di prova (get_collections) fallisce. Prima: si salvava
          un client "zombie" che impediva i tentativi successivi. Ora: la
          variabile self._qdrant viene impostata solo dopo la verifica OK.

  FIX-02  inlet(): rimosso l'import ridondante di embed_for_rag (importato
          ma mai usato direttamente — la funzione _embed() fa già l'import
          internamente). Dead import eliminato.

  FIX-03  max_context_chars: default corretto da 7500 a 12000 per allinearlo
          alla documentazione e alla Sezione 8 dell'handoff.

  FIX-04  HARDWARE_KEYWORDS: lista ridotta e specializzata. Rimossi termini
          generici ("audio", "monitor", "rgb", "led", "webcam", "case",
          "microfono") che causavano iniezione hardware su query non correlate.
          Tenuti solo termini inequivocabilmente legati all'hardware fisico
          del sistema.

  FIX-05  __init__: il messaggio di log mostra correttamente lo stato del
          flag debug_log invece di hardcodare "debug ON".

  FIX-06  _get_qdrant: aggiunto reset self._qdrant = None nell'except per
          garantire il retry alla prossima richiesta.
"""

from __future__ import annotations

import re
import sys
import traceback
from typing import Optional
from pydantic import BaseModel

# Rende le pipeline importabili anche quando il modulo non è nel path
sys.path.insert(0, "/app/pipelines")

# ── Parole chiave hardware ─────────────────────────────────────────────────────
# FIX-04: lista ridotta a termini inequivocabilmente hardware-specifici.
# Rimossi: audio, monitor, webcam, microfono, rgb, led, case, rumore,
# alimentatore, usb, thunderbolt, bluetooth, wifi (troppo generici).
HARDWARE_KEYWORDS = [
    "ram", "cpu", "gpu", "vram", "nvidia", "geforce", "rtx", "gtx",
    "cuda", "processore", "scheda video", "scheda madre",
    "disco", "ssd", "nvme", "pcie", "sata",
    "memoria", "ddr", "dimm", "slot", "zram",
    "core i", "i9", "i7", "i5", "i3", "intel", "amd", "ryzen",
    "driver", "firmware", "bios", "uefi",
    "clock", "overclock", "boost", "tdp", "watt",
    "temperatura", "dissipatore", "termica", "throttling",
    "chipset", "soc", "npu", "tpu", "tensore",
    "benchmark", "vram usata", "memoria video",
    "quanta ram", "quanta vram", "quanto spazio",
    "nvidia-smi", "gpu-z", "hwinfo",
    "laptop specifiche", "portatile specifiche",
]

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qdrant_models
    _QDRANT_AVAILABLE = True
except ImportError:
    _QDRANT_AVAILABLE = False


class Pipeline:

    class Valves(BaseModel):
        qdrant_url:        str   = "http://ai-qdrant-session:6333"
        collection_name:   str   = "orchestra"
        top_k:             int   = 6
        min_score:         float = 0.45
        max_context_chars: int   = 12000   # FIX-03: allineato alla documentazione (era 7500)
        enabled:           bool  = True
        debug_log:         bool  = False
        pipelines:         list  = []

    _FILENAME_PATTERN = re.compile(
        r'\b(\w+\.(?:py|sh|md|json|yaml|yml))\b', re.IGNORECASE
    )

    def __init__(self) -> None:
        self.type      = "filter"
        self.name      = "RAG Filter"
        self.valves    = self.Valves()
        self.pipelines = ["*"]
        self._qdrant: Optional[QdrantClient] = None
        self._cache: dict[str, list[float]] = {}
        self._skip_prefixes = (
            "### task:", "### instruction:",
            "create a concise", "generate a title",
            "generate 3 follow", "generate 1-3 broad",
        )
        self._skip_commands = ("/generate", "/rag", "/evolve", "/review")
        # FIX-05: mostra lo stato reale del debug_log
        debug_state = "ON" if self.valves.debug_log else "OFF"
        print(f"[RAG_FILTER] Inizializzato v1.6.0 (hardware query support, debug {debug_state}).", flush=True)

    def _get_model(self):
        from embedding_utils import get_rag_embedding_model
        return get_rag_embedding_model()

    def _get_qdrant(self) -> Optional[QdrantClient]:
        """
        Restituisce un client Qdrant funzionante o None.

        FIX-01: il client viene salvato in self._qdrant SOLO dopo che la
        connessione di prova (get_collections) ha avuto successo. Se la prova
        fallisce, self._qdrant rimane None così il prossimo request ritenta.
        FIX-06: in caso di eccezione, self._qdrant viene esplicitamente
        reimpostato a None per garantire il retry.
        """
        if self._qdrant is not None:
            return self._qdrant
        if not _QDRANT_AVAILABLE:
            return None
        try:
            client = QdrantClient(url=self.valves.qdrant_url, timeout=5)
            client.get_collections()   # probe di connessione
            self._qdrant = client      # assegnato SOLO se il probe ha successo
        except Exception as e:
            print(f"[RAG_FILTER] Errore connessione Qdrant: {e}", flush=True)
            self._qdrant = None        # FIX-06: reset esplicito per retry futuro
        return self._qdrant

    def _embed(self, text: str) -> Optional[list[float]]:
        if text in self._cache:
            return self._cache[text]
        from embedding_utils import embed_for_rag
        vec = embed_for_rag(text)
        if vec is None:
            return None
        if len(self._cache) >= 256:
            del self._cache[next(iter(self._cache))]
        self._cache[text] = vec
        return vec

    def _search(self, query_vec: list[float]) -> list[dict]:
        qdrant = self._get_qdrant()
        if qdrant is None:
            return []
        try:
            results = qdrant.query_points(
                collection_name=self.valves.collection_name,
                query=query_vec,
                limit=self.valves.top_k,
                score_threshold=self.valves.min_score,
                with_payload=True,
            )
            hits = results.points if hasattr(results, 'points') else []
            return [
                {"text": r.payload.get("text", "") if r.payload else "",
                 "domain": r.payload.get("domain", "") if r.payload else "",
                 "source": r.payload.get("source", "") if r.payload else "",
                 "score": round(r.score, 3)}
                for r in hits if r.payload
            ]
        except Exception as e:
            # Reset del client su errore di search — al prossimo request viene
            # ricreato e testato con il probe
            self._qdrant = None
            print(f"[RAG_FILTER] Errore search Qdrant: {e}", flush=True)
            return []

    def _search_by_source(self, source_name: str, limit: int = 100) -> list[dict]:
        qdrant = self._get_qdrant()
        if qdrant is None:
            return []
        try:
            results, _ = qdrant.scroll(
                collection_name=self.valves.collection_name,
                scroll_filter=qdrant_models.Filter(
                    must=[qdrant_models.FieldCondition(
                        key="source", match=qdrant_models.MatchValue(value=source_name)
                    )]
                ),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            return [
                {"text": r.payload.get("text", "") if r.payload else "",
                 "domain": r.payload.get("domain", "") if r.payload else "",
                 "source": r.payload.get("source", "") if r.payload else "",
                 "score": 1.0}
                for r in results if r.payload
            ]
        except Exception as e:
            print(f"[RAG_FILTER] Errore search by source '{source_name}': {e}", flush=True)
            return []

    @classmethod
    def _filter_relevant_chunks(cls, query: str, chunks: list[dict], filename: str) -> list[dict]:
        """
        Filtra i chunk cercando definizioni di funzione menzionate nella query.
        Cerca identificatori multi-parola con underscore (es. embed_for_rag).
        """
        words = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*(?:_[a-zA-Z0-9_]+)+)\b', query)
        if not words:
            return []
        relevant = []
        for chunk in chunks:
            text = chunk.get("text", "")
            for word in words:
                if f'def {word}' in text or f'async def {word}' in text:
                    relevant.append(chunk)
                    break
        return relevant

    @classmethod
    def _detect_filenames(cls, text: str) -> list[str]:
        return [m.group(1) for m in cls._FILENAME_PATTERN.finditer(text)]

    def _should_skip(self, text: str) -> bool:
        if not self.valves.enabled:
            return True
        # Eccezione: le query hardware vengono sempre processate, anche se corte
        if self._is_hardware_query(text):
            return False
        if len(text.strip()) < 20:
            return True
        lower = text.lower().strip()
        if any(lower.startswith(cmd) for cmd in self._skip_commands):
            return True
        return any(lower.startswith(p) for p in self._skip_prefixes)

    @staticmethod
    def _is_hardware_query(query: str) -> bool:
        """Verifica se la query riguarda l'hardware fisico del sistema."""
        query_lower = query.lower()
        for kw in HARDWARE_KEYWORDS:
            if kw in query_lower:
                return True
        return False

    @staticmethod
    def _filter_hardware_chunks(query: str, chunks: list[dict]) -> list[dict]:
        """
        Restituisce solo i chunk hardware pertinenti alla domanda specifica.
        Mappa le keyword a categorie e seleziona i chunk che le contengono.
        """
        query_lower = query.lower()
        keyword_map = {
            "ram":         ["memoria", "ram", "ddr", "gigabyte", "zram", "dimm"],
            "cpu":         ["processore", "cpu", "core", "thread", "i9", "intel", "ryzen"],
            "gpu":         ["nvidia", "geforce", "rtx", "gpu", "cuda", "vram", "scheda video"],
            "disco":       ["ssd", "nvme", "storage", "disco", "micron", "pcie"],
            "temperatura": ["temperatura", "termico", "celsius", "°c", "throttling", "dissipatore"],
        }
        areas = []
        for area, keywords in keyword_map.items():
            for kw in keywords:
                if kw in query_lower:
                    areas.append(area)
                    break
        if not areas:
            return []   # nessuna area specifica, il chiamante userà il fallback
        relevant = []
        for chunk in chunks:
            text_lower = chunk.get("text", "").lower()
            for area in areas:
                for kw in keyword_map[area]:
                    if kw in text_lower:
                        relevant.append(chunk)
                        break
                else:
                    continue
                break
        return relevant

    def _format_context(self, chunks: list[dict]) -> str:
        exact    = [c for c in chunks if c["score"] >= 1.0]
        semantic = [c for c in chunks if c["score"] < 1.0]
        lines, total = [], 0
        for c in exact:
            entry = f"[{c['domain']}/{c['source']} | codice sorgente]\n{c['text']}"
            if total + len(entry) > self.valves.max_context_chars:
                room = self.valves.max_context_chars - total
                if room > 120:
                    lines.append(entry[:room] + "…")
                break
            lines.append(entry)
            total += len(entry) + 2
        for c in semantic:
            entry = f"[{c['domain']}/{c['source']} | similarità: {c['score']}]\n{c['text']}"
            if total + len(entry) > self.valves.max_context_chars:
                room = self.valves.max_context_chars - total
                if room > 120:
                    lines.append(entry[:room] + "…")
                break
            lines.append(entry)
            total += len(entry) + 2
        return "\n\n".join(lines)

    async def inlet(self, body: dict, user: dict | None = None) -> dict:
        try:
            messages = body.get("messages", [])
            if not messages:
                return body
            last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
            if not last_user:
                return body
            content = last_user.get("content", "")
            query = (
                content if isinstance(content, str)
                else " ".join(i.get("text", "") for i in content if i.get("type") == "text")
                if isinstance(content, list)
                else str(content)
            ).strip()

            if self.valves.debug_log:
                print(
                    f"[RAG_FILTER] Query: '{query[:80]}' | "
                    f"Hardware: {self._is_hardware_query(query)} | "
                    f"Skip: {self._should_skip(query)}",
                    flush=True
                )

            if self._should_skip(query):
                return body

            # FIX-02: rimosso import ridondante di embed_for_rag (non usato
            # direttamente qui — self._embed() lo importa internamente)
            vec = self._embed(query)
            if vec is None:
                if self.valves.debug_log:
                    print("[RAG_FILTER] Embedding fallito, esco.", flush=True)
                return body

            chunks = self._search(vec)
            if self.valves.debug_log:
                print(f"[RAG_FILTER] Chunk semantici: {len(chunks)}", flush=True)

            # Ricerca supplementare per nome file menzionato nella query
            filenames = self._detect_filenames(query)
            if filenames:
                existing_texts = {c["text"] for c in chunks}
                for fname in filenames:
                    if self.valves.debug_log:
                        print(f"[RAG_FILTER] Ricerca file: {fname}", flush=True)
                    all_file_chunks = self._search_by_source(fname, limit=100)
                    filtered  = self._filter_relevant_chunks(query, all_file_chunks, fname)
                    selected  = filtered[:10] if filtered else all_file_chunks[:5]
                    for sc in selected:
                        if sc["text"] not in existing_texts:
                            chunks.append(sc)
                            existing_texts.add(sc["text"])

            # Iniezione forzata del report hardware con filtro intelligente
            if self._is_hardware_query(query):
                hw_chunks_all = self._search_by_source("hardware-report.md", limit=20)
                if self.valves.debug_log:
                    print(f"[RAG_FILTER] Hardware chunk totali: {len(hw_chunks_all)}", flush=True)
                if hw_chunks_all:
                    relevant_hw = self._filter_hardware_chunks(query, hw_chunks_all)
                    if not relevant_hw:
                        # Fallback: domanda generica → mix essenziale
                        essential_kw = ["ram", "cpu", "gpu", "nvidia", "disco", "ssd",
                                        "memoria", "processore", "vram"]
                        for hc in hw_chunks_all:
                            text_lower = hc["text"].lower()
                            if any(kw in text_lower for kw in essential_kw):
                                relevant_hw.append(hc)
                        if not relevant_hw:
                            relevant_hw = hw_chunks_all[:6]

                    existing_texts = {c["text"] for c in chunks}
                    added = 0
                    for hc in relevant_hw:
                        if hc["text"] not in existing_texts:
                            chunks.append(hc)
                            existing_texts.add(hc["text"])
                            added += 1
                    if self.valves.debug_log:
                        print(
                            f"[RAG_FILTER] Aggiunti {added} chunk hardware (filtro intelligente)",
                            flush=True
                        )

            if not chunks:
                return body

            ctx   = self._format_context(chunks)
            block = f"\n\n---\n📚 CONTESTO DALLA KNOWLEDGE BASE\n{ctx}\n---"

            sys_idx = next((i for i, m in enumerate(messages) if m.get("role") == "system"), None)
            if sys_idx is not None:
                messages[sys_idx]["content"] += block
            else:
                messages.insert(0, {"role": "system", "content": f"Sei un assistente AI.{block}"})
            body["messages"] = messages

            if self.valves.debug_log:
                print("[RAG_FILTER] Contesto iniettato nel system message.", flush=True)
            return body

        except Exception as e:
            print(f"[RAG_FILTER] FATAL ERROR: {e}", flush=True)
            traceback.print_exc()
            return body

    async def outlet(self, body: dict, user: dict | None = None) -> dict:
        return body
```

## File: ollama/pipelines/rag_filter/valves.json (193 byte)

```
{"qdrant_url": "http://ai-qdrant-session:6333", "collection_name": "orchestra", "top_k": 6, "min_score": 0.45, "max_context_chars": 7500, "enabled": true, "debug_log": true, "pipelines": ["*"]}```

## File: ollama/pipelines/requirements.txt (38 byte)

```
fastembed>=0.3.0
qdrant-client>=1.9.0
```

