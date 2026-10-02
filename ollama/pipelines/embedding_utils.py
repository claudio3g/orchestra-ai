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
