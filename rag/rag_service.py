"""
RAG Service v1.5.1 — Orchestra
====================================
Flask server per indicizzazione documenti e deploy sicuro dei file di codice.

CHANGELOG v1.5.1 rispetto a v1.5.0 (supporto eGPU RTX 3090):
  EGPU-01 /vram multi-GPU: 3090 (main) + 4060 (aux) lavorano insieme. Ruoli via
          ORCHESTRA_GPU_MAIN / ORCHESTRA_GPU_AUX (UUID/indice) o auto per VRAM
          totale. Campi storici riferiti a MAIN; nuovo array `gpus` con tutte le
          GPU e il loro ruolo. Nessun cambio con una sola GPU.

CHANGELOG v1.5.0 rispetto a v1.4.0 (ottimizzazioni velocità/memoria):
  OPT-01  _run_index_job usa scan_file() invece di scan_directory() + filtro:
          O(n²) → O(n). Con 50 file: ~50x meno I/O durante indicizzazione.

  OPT-02  BATCH_SIZE adattivo: 32 base (era 8), scala su/giù in base a RAM.
          RAM >8 GB → 64 | RAM 4-8 GB → 32 | RAM 2-4 GB → 16 | RAM <2 GB → 8
          fastembed ONNX è ottimizzato per batch ≥16; batch=8 causava overhead.

  OPT-03  hash_cache scritto ogni HASH_CACHE_FLUSH_EVERY file (default 5)
          invece che dopo ogni singolo file. Riduce I/O disco durante index.

  OPT-04  mtime pre-filtro nel watcher: controlla os.stat().st_mtime prima
          di calcolare SHA256. Risparmia hash su file non modificati.

  OPT-05  _WATCHER_INTERVAL definito esplicitamente con env var (default 60s).
          Risolve NameError latente se il blocco di configurazione è incompleto.

  OPT-06  Soglia RAM watcher alzata da 2048 a 3000 MB: evita che il watcher
          scatti quando il processo ha già il modello ONNX caricato (11 GB RSS
          ma RAM libera borderline).

CHANGELOG v1.3.1 rispetto a v1.3.0:
  BUG-VRAM Aggiunto endpoint GET /vram che legge la VRAM libera tramite
           nvidia-smi sull'host. Necessario perché il container Pipelines
           non ha accesso diretto alla CLI NVIDIA: il daemon in
           embedding_utils.py chiama questo endpoint invece di subprocess.
           Risposta: {"vram_free_mb": int, "source": "nvidia-smi"|"fallback"}

CHANGELOG v1.3.0 rispetto a v1.2.0:
  BUG-01/02/03/04/05/06/07/08: vedi changelog precedente.
"""


import os
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, Response, jsonify, request, stream_with_context

try:
    from fastembed import TextEmbedding
    _FASTEMBED_OK = True
except ImportError:
    _FASTEMBED_OK = False

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct
    _QDRANT_OK = True
except ImportError:
    _QDRANT_OK = False

from rag_indexer_lib import scan_directory, check_dependencies

# ─────────────────────────────────────────────────────────────────────────────
# Configurazione
# ─────────────────────────────────────────────────────────────────────────────
QDRANT_URL      = os.environ.get("QDRANT_URL",  "http://localhost:6333")
DOCS_ROOT       = Path(os.environ.get(
    "RAG_DOCS_DIR", str(Path.home() / "ai-sessioni" / "document-ai")
))
COLLECTION_NAME = "orchestra"
VECTOR_DIM      = 768
EMBED_MODEL     = "nomic-ai/nomic-embed-text-v1.5"
SERVICE_PORT    = int(os.environ.get("RAG_SERVICE_PORT", 6335))
SERVICE_HOST    = os.environ.get("RAG_SERVICE_HOST", "127.0.0.1")
DEPLOY_TOKEN    = os.environ.get("DEPLOY_TOKEN", "change-me-in-production")
DOMAIN_DIRS     = ["system", "comfy", "image", "3d", "audio"]

# OPT-05: intervallo watcher esplicito — evita NameError se la sezione
# di configurazione viene riorganizzata in futuro.
_WATCHER_INTERVAL = int(os.environ.get("RAG_WATCH_INTERVAL", 60))

# OPT-02: BATCH_SIZE base. Scalato a runtime da _adaptive_batch_size().
_BATCH_SIZE_BASE = 32

# OPT-03: scrive la hash_cache su disco ogni N file (bilancia durabilità/I/O).
HASH_CACHE_FLUSH_EVERY = int(os.environ.get("RAG_HASH_FLUSH_EVERY", 5))

# Numero massimo di backup da mantenere per file (BUG-05)
MAX_BACKUPS = 3

# Path consentiti per il deploy (whitelist di sicurezza)
ALLOWED_DEPLOY_PATHS = {
    Path(p).resolve() for p in [
        Path.home() / "ai-sessioni/ollama/pipelines/orchestra_manifold.py",
        Path.home() / "ai-sessioni/ollama/pipelines/image_loop.py",
        Path.home() / "ai-sessioni/ollama/pipelines/rag_filter.py",
        Path.home() / "ai-sessioni/ollama/pipelines/embedding_utils.py",
        Path.home() / "ai-sessioni/rag/rag_indexer_lib.py",
    ]
}

app = Flask(__name__)
_embed_model  = None
_qdrant_client = None


# ─────────────────────────────────────────────────────────────────────────────
# Utilità
# ─────────────────────────────────────────────────────────────────────────────

def get_embed_model():
    global _embed_model
    if _embed_model is None and _FASTEMBED_OK:
        try:
            _embed_model = TextEmbedding(EMBED_MODEL)
        except Exception as e:
            print(f"[RAG_SERVICE] Errore embedding model: {e}", flush=True)
    return _embed_model


def get_qdrant() -> "QdrantClient | None":
    """
    Restituisce un client Qdrant funzionante oppure None.

    BUG-02 FIX: il client viene salvato SOLO dopo che get_collections() (probe)
    ha verificato la raggiungibilità del server.
    In caso di errore _qdrant_client rimane None e il prossimo request riprova.
    """
    global _qdrant_client
    if _qdrant_client is not None:
        return _qdrant_client
    if not _QDRANT_OK:
        return None
    try:
        client = QdrantClient(url=QDRANT_URL, timeout=10)
        client.get_collections()           # probe — solleva eccezione se non raggiungibile
        _qdrant_client = client            # salvato SOLO dopo probe OK
    except Exception as e:
        _qdrant_client = None              # reset esplicito — garantisce retry futuro
        print(f"[RAG_SERVICE] Qdrant non raggiungibile: {e}", flush=True)
    return _qdrant_client


# Chunk più grande tollerato prima dello split (caratteri).
# nomic-embed-text-v1.5 ha context 8192 token ≈ 6000 chars in media.
# 4000 chars è un limite conservativo che lascia margine e riduce i picchi
# di allocazione ONNX su chunk come il docstring di orchestra_manifold.py
# (10316 chars) che causava OOM e crash del processo Flask.
MAX_CHUNK_CHARS = int(os.environ.get("RAG_MAX_CHUNK_CHARS", 4000))


def _split_large_chunks(chunks: list[dict]) -> list[dict]:
    """
    Spezza i chunk che superano MAX_CHUNK_CHARS in sotto-chunk sovrapposti.

    Strategia: split per paragrafi (\\n\\n), poi per righe (\\n) se necessario.
    L'overlap di 200 chars preserva la continuità semantica tra sotto-chunk.
    Il doc_id viene suffissato con _p0, _p1, ... per garantire unicità su Qdrant.

    Chiamato in _run_index_job prima dell'embedding — non modifica rag_indexer_lib.
    """
    result: list[dict] = []
    OVERLAP = 200

    for chunk in chunks:
        text = chunk["text"]
        if len(text) <= MAX_CHUNK_CHARS:
            result.append(chunk)
            continue

        # Prova a splittare per paragrafi prima
        parts: list[str] = []
        current = ""
        for para in text.split("\n\n"):
            if len(current) + len(para) + 2 <= MAX_CHUNK_CHARS:
                current = current + ("\n\n" if current else "") + para
            else:
                if current:
                    parts.append(current)
                # Paragrafo singolo troppo grande? Spezza per righe
                if len(para) > MAX_CHUNK_CHARS:
                    lines = para.split("\n")
                    sub = ""
                    for line in lines:
                        if len(sub) + len(line) + 1 <= MAX_CHUNK_CHARS:
                            sub = sub + ("\n" if sub else "") + line
                        else:
                            if sub:
                                parts.append(sub)
                            sub = line
                    if sub:
                        parts.append(sub)
                else:
                    current = para
        if current:
            parts.append(current)

        if not parts:
            result.append(chunk)
            continue

        # Aggiunge overlap e crea sotto-chunk con doc_id suffissato
        for idx, part in enumerate(parts):
            # Aggiungi la coda del blocco precedente come contesto
            if idx > 0:
                prev_tail = parts[idx - 1][-OVERLAP:]
                part = prev_tail + "\n" + part
            sub_chunk = chunk.copy()
            sub_chunk["text"] = part
            sub_chunk["doc_id"] = f"{chunk['doc_id']}_p{idx}"
            result.append(sub_chunk)

    return result


    """
    OPT-02: calcola il batch size ottimale in base alla RAM disponibile.

    Soglia massima abbassata a 32 (era 64): con file grandi come
    orchestra_manifold.py (49 KB, ~35 chunk), batch=64 causa picchi
    di allocazione ONNX che portano a OOM e crash del processo Flask.
    32 è il punto di equilibrio ottimale per questo hardware.

    Soglie (RAM libera):
      > 8 GB  → 32  (massimo sicuro per file grandi)
      4-8 GB  → 16
      2-4 GB  → 8   (conservativo)
      < 2 GB  → 4   (minimo assoluto)
    """
    try:
        import psutil
        ram_free_gb = psutil.virtual_memory().available / (1024 ** 3)
        if ram_free_gb > 8:
            return 32
        elif ram_free_gb > 4:
            return 16
        elif ram_free_gb > 2:
            return 8
        else:
            return 4
    except ImportError:
        return _BATCH_SIZE_BASE


def ensure_collection() -> tuple[bool, str]:
    qdrant = get_qdrant()
    if not qdrant:
        return False, "Qdrant non raggiungibile"
    try:
        existing = [c.name for c in qdrant.get_collections().collections]
        if COLLECTION_NAME not in existing:
            qdrant.create_collection(
                COLLECTION_NAME,
                vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
            )
            return True, f"Collection '{COLLECTION_NAME}' creata"
        return True, f"Collection '{COLLECTION_NAME}' già esistente"
    except Exception as e:
        return False, f"Errore: {e}"


def _make_payload(chunk: dict) -> dict:
    """
    Costruisce il payload Qdrant da un chunk.

    Include metadati opzionali per tipo:
      - File Python:  function_name, start_line, end_line, type
      - File PDF:     pdf_page, pdf_total_pages, pdf_title, pdf_author, pdf_subject, type
    I campi opzionali vengono inclusi solo se presenti nel chunk.
    """
    payload = {
        "text":      chunk["text"],
        "domain":    chunk["domain"],
        "source":    chunk["source"],
        "path":      chunk["path"],
        "chunk_idx": chunk["chunk_idx"],
    }
    # Metadati Python AST
    for key in ("function_name", "start_line", "end_line"):
        if key in chunk:
            payload[key] = chunk[key]
    # Metadati PDF
    for key in ("pdf_page", "pdf_total_pages", "pdf_title", "pdf_author", "pdf_subject"):
        if key in chunk:
            payload[key] = chunk[key]
    # Tipo chunk (python: function/method/class, pdf: pdf_page, altri: assente)
    if "type" in chunk:
        payload["type"] = chunk["type"]
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: /health
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Async indexing — job registry + file hash cache
# Aggiunto in v1.4.0 — non modifica endpoint esistenti
# ─────────────────────────────────────────────────────────────────────────────
import threading
import uuid
import hashlib
import json

_INDEX_JOBS: dict = {}
_INDEX_JOBS_LOCK = threading.Lock()
_INDEXING_IN_PROGRESS = threading.Event()

# Handler globale eccezioni nei thread — previene crash del processo principale
def _thread_excepthook(args):
    print(f"[RAG] Eccezione thread {args.thread.name}: {args.exc_value}", flush=True)
    import traceback
    traceback.print_exception(args.exc_type, args.exc_value, args.exc_tb)
threading.excepthook = _thread_excepthook  # previene indicizzazioni parallele

_FILE_HASH_CACHE_PATH = Path(os.environ.get(
    "RAG_HASH_CACHE",
    str(Path.home() / "ai-sessioni" / "rag" / ".file_hash_cache.json")
))

def _load_hash_cache() -> dict:
    """Carica la cache degli hash dal disco."""
    try:
        if _FILE_HASH_CACHE_PATH.exists():
            return json.loads(_FILE_HASH_CACHE_PATH.read_text())
    except Exception:
        pass
    return {}

def _save_hash_cache(cache: dict) -> None:
    """Salva la cache degli hash su disco."""
    try:
        _FILE_HASH_CACHE_PATH.write_text(json.dumps(cache, indent=2))
    except Exception as e:
        print(f"[RAG] Errore salvataggio hash cache: {e}", flush=True)

def _file_hash(file_path: Path) -> str:
    """Calcola SHA256 del file."""
    h = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except Exception:
        return ""
    return h.hexdigest()

def _adaptive_batch_size() -> int:
    """
    OPT-02: calcola il batch size ottimale in base alla RAM disponibile.

    Soglia massima 32: con file grandi (orchestra_manifold.py ~35 chunk),
    batch=64 causava picchi di allocazione ONNX → OOM → crash Flask.

    Soglie (RAM libera):
      > 8 GB  → 32  (massimo sicuro)
      4-8 GB  → 16
      2-4 GB  → 8
      < 2 GB  → 4   (minimo assoluto)
    """
    try:
        import psutil
        ram_free_gb = psutil.virtual_memory().available / (1024 ** 3)
        if ram_free_gb > 8:
            return 32
        elif ram_free_gb > 4:
            return 16
        elif ram_free_gb > 2:
            return 8
        else:
            return 4
    except ImportError:
        return _BATCH_SIZE_BASE


def _run_index_job(job_id: str, incremental: bool = True) -> None:
    """
    Indicizzazione file-per-file con progressione.

    Ottimizzazioni v1.5.0:
      OPT-01: usa scan_file() invece di scan_directory() + filtro per ogni file.
              Prima: O(n²) I/O — ogni file triggera una scansione di tutta la dir.
              Ora:   O(n)  I/O — ogni file viene processato direttamente.

      OPT-02: batch size adattivo basato su RAM disponibile (8–64).
              fastembed ONNX ha overhead fisso per chiamata: batch più grandi
              sono significativamente più efficienti (meno context switch ONNX).

      OPT-03: hash_cache scritto su disco ogni HASH_CACHE_FLUSH_EVERY file
              invece che dopo ogni singolo file. Riduce I/O disco.

      OPT-04: mtime pre-filtro prima del calcolo SHA256 nel check incrementale.
              Se il mtime è uguale a quello salvato, saltiamo SHA256 completamente.
    """
    import gc
    import psutil

    # Il watcher setta _INDEXING_IN_PROGRESS prima di chiamare questo metodo
    # per evitare la race condition. Se il flag è già settato da noi (watcher),
    # procediamo. Se è settato da un job concorrente diverso, saltiamo.
    # Distinzione: il watcher passa always_proceed=True (implicito via flag già set).
    # Per i job API (/index/async), il flag non è ancora settato → lo settiamo noi.
    flag_was_set_by_caller = _INDEXING_IN_PROGRESS.is_set()
    if flag_was_set_by_caller:
        # Verifica che il job registrato sia il nostro e non di un altro
        with _INDEX_JOBS_LOCK:
            our_job = _INDEX_JOBS.get(job_id, {})
        if not our_job:
            # Flag settato ma il nostro job non è registrato → job concorrente esterno
            print(f"[RAG] Job {job_id} saltato — indicizzazione già in corso (job esterno)", flush=True)
            return
        # Flag settato dal watcher per il nostro job — procediamo senza re-settare
    else:
        _INDEXING_IN_PROGRESS.set()

    log = []

    def _log(msg: str) -> None:
        log.append(msg)
        with _INDEX_JOBS_LOCK:
            if job_id in _INDEX_JOBS:
                _INDEX_JOBS[job_id]["log"] = log[:]

    try:
        _log(f"📁 Docs root: {DOCS_ROOT}")
        if not _FASTEMBED_OK:
            _log("❌ fastembed non installato.")
            raise RuntimeError("fastembed mancante")
        if not _QDRANT_OK:
            _log("❌ qdrant-client non installato.")
            raise RuntimeError("qdrant-client mancante")
        if not DOCS_ROOT.exists():
            _log(f"❌ Directory non trovata: {DOCS_ROOT}")
            raise RuntimeError("docs_root mancante")

        ok, msg = ensure_collection()
        if not ok:
            _log(f"❌ {msg}")
            raise RuntimeError(msg)
        _log(f"✅ {msg}")

        _log(f"🔄 Carico embedding model: {EMBED_MODEL}...")
        model = get_embed_model()
        if model is None:
            _log("❌ Impossibile caricare embedding model")
            raise RuntimeError("embed model mancante")
        _log("✅ Embedding model pronto.")

        # OPT-02: calcola batch size ottimale in base a RAM disponibile ora
        batch_size = _adaptive_batch_size()
        _log(f"⚙️  Batch size adattivo: {batch_size} (RAM libera: "
             f"{psutil.virtual_memory().available // (1024**2)} MB)")

        hash_cache = _load_hash_cache()
        new_cache = hash_cache.copy()

        # OPT-04: cache mtime per pre-filtro rapido (evita SHA256 su file invariati)
        # Formato: {path_str: mtime_float}
        _MTIME_CACHE_KEY = "__mtime__"
        mtime_cache: dict[str, float] = hash_cache.get(_MTIME_CACHE_KEY, {})  # type: ignore[assignment]

        # Raccoglie tutti i file supportati, ordinati per dimensione (piccoli prima)
        from rag_indexer_lib import SUPPORTED_EXT
        all_files = sorted(
            [
                f for f in DOCS_ROOT.rglob("*")
                if f.is_file()
                and f.suffix.lower() in SUPPORTED_EXT
                and not f.name.startswith(".")
                and "__pycache__" not in str(f)
            ],
            key=lambda f: f.stat().st_size
        )
        total_files = len(all_files)
        _log(f"🔍 {total_files} file supportati trovati (ordinati per dimensione)")

        qdrant = get_qdrant()
        processed_total = 0
        skipped_files = 0
        error_files = 0
        domains_set: set[str] = set()
        t_start = time.time()
        files_since_flush = 0  # OPT-03: contatore per flush periodico hash_cache

        for file_idx, file_path in enumerate(all_files, 1):
            file_path_str = str(file_path)
            rel_name = file_path.name
            size_kb = file_path.stat().st_size // 1024

            # Controllo RAM disponibile prima di ogni file (soglia 1500 MB)
            ram_free_mb = psutil.virtual_memory().available / 1024 / 1024
            if ram_free_mb < 1500:
                _log(f"  ⚠️ [{file_idx}/{total_files}] RAM bassa ({ram_free_mb:.0f} MB), skip: {rel_name}")
                skipped_files += 1
                continue

            # OPT-02: ricalcola batch_size se la RAM è cambiata durante il job
            # (lo facciamo ogni 10 file per non chiamare psutil troppo spesso)
            if file_idx % 10 == 0:
                batch_size = _adaptive_batch_size()

            # OPT-04 + Controllo incrementale
            if incremental:
                try:
                    current_mtime = file_path.stat().st_mtime
                except OSError:
                    current_mtime = 0.0

                cached_mtime = mtime_cache.get(file_path_str, 0.0)

                if current_mtime and current_mtime == cached_mtime:
                    # mtime identico → file non modificato → salta senza SHA256
                    skipped_files += 1
                    continue

                # mtime diverso → calcola SHA256 per conferma
                current_hash = _file_hash(file_path)
                cached_hash = hash_cache.get(file_path_str, "")
                if current_hash and current_hash == cached_hash:
                    # Hash uguale (rinominazione/touch senza modifica): aggiorna mtime
                    mtime_cache[file_path_str] = current_mtime
                    skipped_files += 1
                    continue

                # File effettivamente modificato: salva hash e mtime
                # NOTA: new_cache viene aggiornato QUI solo per mtime_cache.
                # L'hash viene aggiornato dopo il successo del processing
                # (vedi sotto) per evitare di marcare come indicizzato un file
                # i cui chunk non sono stati upsertati correttamente.
                mtime_cache[file_path_str] = current_mtime

            _log(f"  📄 [{file_idx}/{total_files}] {rel_name} ({size_kb} KB)")

            try:
                # OPT-01: scan_file() elabora solo questo file, non tutta la dir
                from rag_indexer_lib import scan_file as _scan_file
                file_chunks = _scan_file(file_path, DOCS_ROOT)

                if not file_chunks:
                    _log(f"    ⚠️ Nessun chunk estratto")
                    continue

                # Spezza chunk troppo grandi prima dell'embedding.
                # orchestra_manifold.py produce un chunk da 10316 chars (docstring)
                # che causava picchi OOM in fastembed ONNX → crash Flask.
                original_count = len(file_chunks)
                file_chunks = _split_large_chunks(file_chunks)
                if len(file_chunks) != original_count:
                    _log(f"    ✂️  {original_count} → {len(file_chunks)} chunk (split chunk grandi)")

                domains_set.add(file_chunks[0].get("domain", ""))
                file_processed = 0
                file_errors = 0

                # Safety cap per file grandi: se il file produce molti chunk,
                # li processa in sotto-gruppi da MAX_CHUNKS_PER_PASS con gc()
                # intermedio. Previene picchi di allocazione ONNX su file come
                # orchestra_manifold.py (49 KB → ~35 chunk tutti in memoria).
                MAX_CHUNKS_PER_PASS = 20
                chunk_groups = [
                    file_chunks[i:i + MAX_CHUNKS_PER_PASS]
                    for i in range(0, len(file_chunks), MAX_CHUNKS_PER_PASS)
                ]

                for group in chunk_groups:
                    batch: list[dict] = []
                    batch_texts: list[str] = []

                    def _flush_batch() -> tuple[int, int]:
                        nonlocal batch, batch_texts
                        if not batch:
                            return 0, 0
                        try:
                            embeddings = list(model.embed(batch_texts))
                        except Exception:
                            n = len(batch)
                            batch, batch_texts = [], []
                            return 0, n
                        points = [
                            PointStruct(
                                id=chunk["doc_id"],
                                vector=emb.tolist(),
                                payload=_make_payload(chunk),
                            )
                            for chunk, emb in zip(batch, embeddings)
                        ]
                        try:
                            qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
                            n = len(batch)
                            batch, batch_texts = [], []
                            return n, 0
                        except Exception:
                            n = len(batch)
                            batch, batch_texts = [], []
                            return 0, n

                    for chunk in group:
                        batch.append(chunk)
                        batch_texts.append(chunk["text"])
                        if len(batch) >= batch_size:
                            ok_n, err_n = _flush_batch()
                            file_processed += ok_n
                            file_errors += err_n

                    ok_n, err_n = _flush_batch()
                    file_processed += ok_n
                    file_errors += err_n
                    gc.collect()  # libera allocazioni ONNX tra sotto-gruppi

                processed_total += file_processed
                _log(f"    ✅ {file_processed} chunk indicizzati" +
                     (f" ⚠️ {file_errors} errori" if file_errors else ""))

                # Aggiorna hash solo dopo successo del processing.
                # Se l'embedding o l'upsert fallisce, il file NON viene marcato
                # come indicizzato — verrà ritentato al prossimo job incrementale.
                if incremental and file_errors == 0 and file_processed > 0:
                    new_cache[file_path_str] = current_hash

                # OPT-03: flush hash_cache ogni HASH_CACHE_FLUSH_EVERY file
                if incremental:
                    files_since_flush += 1
                    if files_since_flush >= HASH_CACHE_FLUSH_EVERY:
                        new_cache[_MTIME_CACHE_KEY] = mtime_cache
                        _save_hash_cache(new_cache)
                        files_since_flush = 0

            except Exception as e:
                _log(f"    ❌ Errore: {str(e)[:80]}")
                error_files += 1

            finally:
                gc.collect()

        # Flush finale della cache anche se non abbiamo raggiunto la soglia
        if incremental and files_since_flush > 0:
            new_cache[_MTIME_CACHE_KEY] = mtime_cache  # type: ignore[assignment]
            _save_hash_cache(new_cache)

        elapsed = time.time() - t_start
        domains = sorted(domains_set - {""})

        _log(f"\n📊 Riepilogo:")
        _log(f"  • File processati: {total_files - skipped_files - error_files}/{total_files}")
        if skipped_files:
            _log(f"  • File saltati (invariati/RAM): {skipped_files}")
        if error_files:
            _log(f"  • File con errori: {error_files}")
        _log(f"  • Chunk totali: {processed_total}")
        _log(f"  • Domini: {', '.join(domains) if domains else 'nessuno'}")
        _log(f"  • Tempo: {elapsed:.1f}s")
        _log(f"✅ Indicizzazione completata")

        with _INDEX_JOBS_LOCK:
            _INDEX_JOBS[job_id]["status"] = "done"

    except Exception as e:
        _log(f"❌ Errore: {e}")
        with _INDEX_JOBS_LOCK:
            if job_id in _INDEX_JOBS:
                _INDEX_JOBS[job_id]["status"] = "error"
    finally:
        _INDEXING_IN_PROGRESS.clear()


_watcher_thread = None

def _start_watcher() -> None:
    """Avvia il watcher in background se non già attivo."""
    global _watcher_thread
    if _watcher_thread and _watcher_thread.is_alive():
        return

    def _watch():
        print(f"[RAG] Watcher avviato (interval={_WATCHER_INTERVAL}s)", flush=True)
        while True:
            time.sleep(_WATCHER_INTERVAL)
            try:
                # Salta se indicizzazione già in corso
                if _INDEXING_IN_PROGRESS.is_set():
                    print("[RAG] Watcher: indicizzazione in corso, skip", flush=True)
                    continue

                # OPT-06: soglia alzata da 2048 a 3000 MB.
                # Con il modello ONNX caricato (~11 GB RSS), la RAM "libera"
                # misurata da psutil può essere borderline. 3000 MB garantisce
                # un margine sicuro prima di avviare un nuovo job.
                try:
                    import psutil
                    ram_free_mb = psutil.virtual_memory().available / 1024 / 1024
                    if ram_free_mb < 3000:
                        print(f"[RAG] Watcher: RAM insufficiente ({ram_free_mb:.0f} MB liberi), skip", flush=True)
                        continue
                except ImportError:
                    pass  # psutil non disponibile, procedi comunque

                # OPT-04: mtime pre-filtro prima di SHA256.
                # Carica la mtime_cache (salvata dentro hash_cache con chiave speciale).
                hash_cache = _load_hash_cache()
                mtime_cache: dict = hash_cache.get("__mtime__", {})
                changed = False
                for f in DOCS_ROOT.rglob("*"):
                    if f.is_file() and "__pycache__" not in str(f):
                        f_str = str(f)
                        try:
                            current_mtime = f.stat().st_mtime
                        except OSError:
                            continue
                        # Primo check rapido: mtime diverso?
                        if current_mtime == mtime_cache.get(f_str, 0.0):
                            continue  # mtime identico → invariato → prossimo file
                        # mtime diverso: confirma con SHA256
                        current_hash = _file_hash(f)
                        if current_hash != hash_cache.get(f_str, ""):
                            changed = True
                            break
                if changed:
                    # FIX race condition: setta il flag QUI, prima di lanciare
                    # il thread, non dentro _run_index_job. Senza questo il
                    # watcher supera il check is_set() più volte nello stesso
                    # ciclo e lancia job paralleli → picchi RAM → crash OOM.
                    # Event.set() è idempotente: _run_index_job non fa danni
                    # se lo trova già settato (salta e ritorna subito).
                    # Secondo check dopo rglob: un job API potrebbe essere
                    # partito mentre scansionávamo i file.
                    if _INDEXING_IN_PROGRESS.is_set():
                        print("[RAG] Watcher: flag occupato dopo scansione, skip", flush=True)
                        continue
                    _INDEXING_IN_PROGRESS.set()
                    print(f"[RAG] Watcher: file modificati ({ram_free_mb:.0f} MB liberi), avvio indicizzazione", flush=True)
                    job_id = str(uuid.uuid4())[:8]
                    with _INDEX_JOBS_LOCK:
                        _INDEX_JOBS[job_id] = {
                            "status": "running",
                            "log": [],
                            "started": time.time(),
                            "source": "watcher"
                        }
                    t = threading.Thread(
                        target=_run_index_job,
                        args=(job_id, True),
                        daemon=True
                    )
                    t.start()
            except Exception as e:
                print(f"[RAG] Watcher errore: {e}", flush=True)

    _watcher_thread = threading.Thread(target=_watch, daemon=True)
    _watcher_thread.start()


@app.route("/health")
def health():
    deps         = check_dependencies()
    qdrant_alive = False
    try:
        q = get_qdrant()
        if q:
            q.get_collections()
            qdrant_alive = True
    except Exception:
        pass
    return jsonify({
        "status":      "ok" if (_FASTEMBED_OK and qdrant_alive) else "degraded",
        "fastembed":   _FASTEMBED_OK,
        "qdrant":      qdrant_alive,
        "qdrant_url":  QDRANT_URL,
        "docs_root":   str(DOCS_ROOT),
        "embed_model": EMBED_MODEL,
        "collection":  COLLECTION_NAME,
        "host":        SERVICE_HOST,
        "deps":        deps,
    })


# ─────────────────────────────────────────────────────────────────────────────
# EGPU-01 — Monitor VRAM MULTI-GPU con ruoli (main / aux)
#
# CONTESTO: il portatile ha DUE GPU che lavorano INSIEME in un sistema
# multi-agente: RTX 3090 esterna (eGPU) e RTX 4060 interna. `nvidia-smi
# --query-gpu=memory.free` stampa una riga per GPU: il vecchio `int(out.strip())`
# andava in ValueError e /vram rispondeva SEMPRE 2000 MB (fallback) → il manifold
# sceglieva sempre llama3.2:3b ignorando i 24 GB della 3090.
#
# RUOLI (variabili d'ambiente; valore = UUID "GPU-xxxx" oppure indice):
#   ORCHESTRA_GPU_MAIN   GPU principale (3090)  — alias legacy: ORCHESTRA_GPU_ID
#   ORCHESTRA_GPU_AUX    GPU ausiliaria (4060)
# Si consiglia l'UUID: l'indice può cambiare se la eGPU viene ricollegata.
# Se non impostate: main = GPU con più VRAM totale, aux = la successiva
# (degrada con grazia: se la eGPU è scollegata, main diventa la 4060 e il
# sistema si comporta come la vecchia versione 8 GB).
#
# COMPATIBILITÀ: `vram_free_mb` / `source` restano e si riferiscono a MAIN.
# Con UNA sola GPU nulla cambia.
# ─────────────────────────────────────────────────────────────────────────────
_roles_warned = False


def _list_gpus() -> list:
    """Elenco GPU [{index, uuid, name, free_mb, total_mb}]. Solleva se nvidia-smi fallisce."""
    out = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=index,uuid,name,memory.free,memory.total",
         "--format=csv,noheader,nounits"],
        stderr=subprocess.DEVNULL, timeout=3,
    ).decode()
    gpus = []
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        idx, uuid, rest = [x.strip() for x in ln.split(",", 2)]
        name, free, total = [x.strip() for x in rest.rsplit(",", 2)]
        gpus.append({"index": idx, "uuid": uuid, "name": name,
                     "free_mb": int(free), "total_mb": int(total)})
    if not gpus:
        raise RuntimeError("nvidia-smi: nessuna GPU")
    return gpus


def _match_gpu(gpus: list, ref: str):
    ref = (ref or "").strip()
    if not ref:
        return None
    return next((g for g in gpus if ref in (g["uuid"], g["index"])), None)


def _assign_roles(gpus: list):
    """Restituisce (main, aux_o_None, mode) con mode 'env' | 'auto'. Aggiunge g['role']."""
    global _roles_warned
    main_ref = os.environ.get("ORCHESTRA_GPU_MAIN") or os.environ.get("ORCHESTRA_GPU_ID") or ""
    aux_ref = os.environ.get("ORCHESTRA_GPU_AUX", "")
    main = _match_gpu(gpus, main_ref)
    mode = "env"
    if main is None:
        mode = "auto"
        main = max(gpus, key=lambda g: g["total_mb"])
        if main_ref and not _roles_warned:
            _roles_warned = True
            print(f"[RAG_SERVICE] ATTENZIONE: GPU MAIN '{main_ref}' non trovata "
                  f"(eGPU scollegata?) → uso {main['name']}.", flush=True)
    aux = _match_gpu(gpus, aux_ref)
    if aux is None or aux is main:
        others = [g for g in gpus if g is not main]
        aux = max(others, key=lambda g: g["total_mb"]) if others else None
    for g in gpus:
        g["role"] = "main" if g is main else ("aux" if g is aux else "other")
    return main, aux, mode


@app.route("/vram")
def vram_status():
    """
    VRAM libera delle GPU (host, via nvidia-smi). Punto di verità: il container
    Pipelines non ha la CLI NVIDIA e interroga questo endpoint.

    Risposta (retro-compatibile):
      {"vram_free_mb": int, "source": "nvidia-smi"|"fallback",      # riferiti a MAIN
        "vram_total_mb": int, "gpu_name": str, "gpu_id": str,
        "roles_mode": "env"|"auto",
        "gpus": [{index, uuid, name, free_mb, total_mb, role}, ...]}   # extra se nvidia-smi ok
    Debug: /vram?gpu=aux  oppure ?gpu=<indice|uuid> sposta i campi di primo livello su quella GPU.
    """
    try:
        gpus = _list_gpus()
        main, aux, mode = _assign_roles(gpus)
        want = (request.args.get("gpu", "main") or "main").strip()
        if want == "main":
            target = main
        elif want == "aux":
            target = aux
        else:
            target = _match_gpu(gpus, want)
        if target is None:
            return jsonify({"error": f"GPU '{want}' non trovata", "gpus": gpus}), 404
        return jsonify({
            "vram_free_mb":  target["free_mb"],
            "source":        "nvidia-smi",
            "vram_total_mb": target["total_mb"],
            "gpu_name":      target["name"],
            "gpu_id":        target["uuid"],
            "roles_mode":    mode,
            "gpus":          gpus,
        })
    except Exception as e:
        print(f"[RAG_SERVICE] /vram nvidia-smi fallito: {e}", flush=True)
        return jsonify({"vram_free_mb": 2000, "source": "fallback"}), 200


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: /status
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/status")
def status():
    qdrant = get_qdrant()
    if not qdrant:
        return jsonify({"error": "Qdrant non raggiungibile"}), 503
    try:
        info  = qdrant.get_collection(COLLECTION_NAME)
        total = info.points_count or 0
    except Exception as e:
        return jsonify({"error": f"Collection non trovata: {e}"}), 404

    domains: dict[str, int] = {}
    try:
        offset = None
        while True:
            result, offset = qdrant.scroll(
                COLLECTION_NAME, limit=1000, offset=offset,
                with_payload=["domain"], with_vectors=False,
            )
            for point in result:
                d = point.payload.get("domain", "unknown") if point.payload else "unknown"
                domains[d] = domains.get(d, 0) + 1
            if offset is None:
                break
    except Exception as e:
        domains = {"error": str(e)}

    return jsonify({
        "collection":   COLLECTION_NAME,
        "total_chunks": total,
        "by_domain":    dict(sorted(domains.items())),
        "docs_root":    str(DOCS_ROOT),
        "embed_model":  EMBED_MODEL,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: /search
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/search", methods=["POST"])
def search_documents():
    """Query semantica diretta. Body: {"query": "...", "top_k": 5, "min_score": 0.3}"""
    data = request.get_json()
    if not data or "query" not in data:
        return jsonify({"error": "Missing query"}), 400

    query     = data.get("query", "").strip()
    top_k     = int(data.get("top_k", 5))
    min_score = float(data.get("min_score", 0.3))

    if not query:
        return jsonify({"error": "Empty query"}), 400

    model = get_embed_model()
    if model is None:
        return jsonify({"error": "Embedding model non disponibile"}), 503

    qdrant = get_qdrant()
    if qdrant is None:
        return jsonify({"error": "Qdrant non raggiungibile"}), 503

    try:
        vec     = list(model.embed([query]))[0].tolist()
        results = qdrant.query_points(
            collection_name=COLLECTION_NAME,
            query=vec,
            limit=top_k,
            score_threshold=min_score,
            with_payload=True,
        ).points
        chunks = [
            {
                "text":          r.payload.get("text",          "") if r.payload else "",
                "domain":        r.payload.get("domain",        "") if r.payload else "",
                "source":        r.payload.get("source",        "") if r.payload else "",
                "function_name": r.payload.get("function_name", "") if r.payload else "",
                "start_line":    r.payload.get("start_line",    0)  if r.payload else 0,
                "type":          r.payload.get("type",          "") if r.payload else "",
                "score":         round(r.score, 3),
            }
            for r in results if r.payload
        ]
        return jsonify({"query": query, "chunks": chunks, "total": len(chunks)})
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: /index
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/index", methods=["POST"])
def index_documents():
    def generate():
        yield f"📁 Docs root: {DOCS_ROOT}\n"

        if not _FASTEMBED_OK:
            yield "❌ fastembed non installato.\n"
            return
        if not _QDRANT_OK:
            yield "❌ qdrant-client non installato.\n"
            return
        if not DOCS_ROOT.exists():
            yield f"❌ Directory non trovata: {DOCS_ROOT}\n"
            return

        ok, msg = ensure_collection()
        if not ok:
            yield f"❌ {msg}\n"
            return
        yield f"✅ {msg}\n"

        yield f"🔄 Carico embedding model: {EMBED_MODEL}...\n"
        model = get_embed_model()
        if model is None:
            yield "❌ Impossibile caricare embedding model\n"
            return
        yield "✅ Embedding model pronto.\n🔍 Scansione documenti...\n"

        chunk_iter  = scan_directory(DOCS_ROOT)
        qdrant      = get_qdrant()
        processed   = 0
        errors      = 0
        files_seen: set[str]  = set()
        domains_set: set[str] = set()
        t_start     = time.time()
        batch: list[dict]     = []
        batch_texts: list[str] = []
        # Usa batch adattivo — stessa logica dell'endpoint /index/async
        _batch_size = _adaptive_batch_size()

        def _flush_batch() -> tuple[int, int]:
            """Embeds e upsert del batch corrente. Ritorna (ok, err)."""
            nonlocal batch, batch_texts
            if not batch:
                return 0, 0
            try:
                embeddings = list(model.embed(batch_texts))
            except Exception as e:
                n = len(batch)
                batch, batch_texts = [], []
                return 0, n   # caller stampa l'avviso

            points = [
                PointStruct(
                    id=chunk["doc_id"],
                    vector=emb.tolist(),
                    payload=_make_payload(chunk),  # BUG-04 FIX
                )
                for chunk, emb in zip(batch, embeddings)
            ]
            try:
                qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
                n = len(batch)
                batch, batch_texts = [], []
                return n, 0
            except Exception as e:
                n = len(batch)
                batch, batch_texts = [], []
                return 0, n

        try:
            for chunk in chunk_iter:
                files_seen.add(chunk["path"])
                domains_set.add(chunk["domain"])
                batch.append(chunk)
                batch_texts.append(chunk["text"])

                if len(batch) >= _batch_size:
                    ok_n, err_n = _flush_batch()
                    processed += ok_n
                    errors    += err_n
                    if err_n:
                        yield f"  ⚠️  {err_n} chunk saltati per errore embedding/upsert\n"
                    # BUG-01 FIX: era `//n`, ora `\n`
                    yield f"  ✓ {processed} chunk processati\n"

            # Ultimo batch rimanente
            ok_n, err_n = _flush_batch()
            processed += ok_n
            errors    += err_n
            if err_n:
                yield f"  ⚠️  {err_n} chunk saltati (batch finale)\n"

        except Exception as e:
            yield f"❌ Errore durante la scansione: {e}\n"
            return

        n_files = len(files_seen)
        domains = sorted(domains_set)
        elapsed = time.time() - t_start

        yield f"\n📄 {n_files} file → {processed + errors} chunk totali\n"
        yield f"🏷️  Domain: {', '.join(domains) if domains else 'nessuno'}\n"
        if errors:
            yield f"⚠️  {errors} chunk saltati per errori\n"
        yield f"✅ Completato in {elapsed:.1f}s — {processed} chunk indicizzati\n"

    return Response(stream_with_context(generate()), mimetype="text/plain")


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: /deploy
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: /index/async — indicizzazione asincrona con job ID
# Aggiunto in v1.4.0 — non modifica /index esistente
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/index/async", methods=["POST"])
def index_async():
    """
    Lancia l'indicizzazione in background e risponde subito con job_id.
    Parametro opzionale: {"incremental": true/false} (default: true)
    Se un'indicizzazione è già in corso risponde con status "busy".
    """
    # Check preventivo: evita di registrare un job che verrebbe subito skippato
    if _INDEXING_IN_PROGRESS.is_set():
        return jsonify({"status": "busy", "message": "Indicizzazione già in corso"}), 409

    body = request.get_json(silent=True) or {}
    incremental = body.get("incremental", True)
    job_id = str(uuid.uuid4())[:8]
    with _INDEX_JOBS_LOCK:
        _INDEX_JOBS[job_id] = {
            "status": "running",
            "log": [],
            "started": time.time(),
            "source": "api",
            "incremental": incremental
        }
    t = threading.Thread(
        target=_run_index_job,
        args=(job_id, incremental),
        daemon=True
    )
    t.start()
    return jsonify({"job_id": job_id, "status": "running", "incremental": incremental})


@app.route("/index/status/<job_id>", methods=["GET"])
def index_status(job_id: str):
    """
    Restituisce lo stato e il log di un job di indicizzazione.
    Status: running | done | error
    """
    with _INDEX_JOBS_LOCK:
        job = _INDEX_JOBS.get(job_id)
    if job is None:
        return jsonify({"error": "job non trovato"}), 404
    elapsed = time.time() - job["started"]
    return jsonify({
        "job_id": job_id,
        "status": job["status"],
        "elapsed": round(elapsed, 1),
        "log": job["log"],
        "incremental": job.get("incremental", True),
        "source": job.get("source", "api")
    })


@app.route("/index/jobs", methods=["GET"])
def index_jobs():
    """Lista tutti i job di indicizzazione (ultimi 20)."""
    with _INDEX_JOBS_LOCK:
        jobs = [
            {
                "job_id": jid,
                "status": j["status"],
                "elapsed": round(time.time() - j["started"], 1),
                "source": j.get("source", "api")
            }
            for jid, j in list(_INDEX_JOBS.items())[-20:]
        ]
    return jsonify({"jobs": jobs})


def _backup_file(target_path: Path) -> Path | None:
    """
    Crea un backup con timestamp del file di destinazione.

    BUG-05 FIX: nome backup = file.py.bak_YYYYMMDD_HHMMSS invece di file.py.bak
    fisso. Impedisce la sovrascrittura silente del backup precedente.
    Pulisce automaticamente i backup più vecchi oltre MAX_BACKUPS.
    """
    if not target_path.exists():
        return None
    ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = target_path.with_suffix(f"{target_path.suffix}.bak_{ts}")
    target_path.rename(backup_path)

    # Pulizia: mantieni solo gli ultimi MAX_BACKUPS backup
    pattern  = f"{target_path.name}.bak_*"
    backups  = sorted(target_path.parent.glob(pattern))
    for old in backups[:-MAX_BACKUPS]:
        try:
            old.unlink()
        except Exception:
            pass   # non bloccante

    return backup_path


@app.route("/deploy", methods=["POST"])
def deploy_file():
    """Endpoint sicuro per aggiornare i file di codice di Orchestra."""

    # Autenticazione
    token = request.headers.get("X-Deploy-Token")
    if token != DEPLOY_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401

    # Validazione input
    data = request.get_json()
    if not data or "path" not in data or "content" not in data:
        return jsonify({"error": "Missing path or content"}), 400

    # BUG-07 FIX: content vuoto supera la validazione originale
    if not data["content"] or not data["content"].strip():
        return jsonify({"error": "Content is empty — deploy rifiutato per sicurezza"}), 400

    # Whitelist path
    target_path = Path(data["path"]).resolve()
    if target_path not in ALLOWED_DEPLOY_PATHS:
        return jsonify({"error": f"Path non consentito: {target_path}"}), 403

    # Validazione sintattica Python
    # BUG-06 FIX: cattura anche ValueError (null bytes nel source)
    try:
        compile(data["content"], str(target_path), "exec")
    except (SyntaxError, ValueError) as e:
        return jsonify({"error": f"Validazione fallita: {type(e).__name__}: {e}"}), 400

    # Guardia anti-troncatura: il contenuto non può ridursi di oltre il 40%
    if target_path.exists():
        original_size = target_path.stat().st_size
        new_size      = len(data["content"].encode("utf-8"))
        if original_size > 0 and new_size < original_size * 0.6:
            return jsonify({
                "error": (
                    f"Contenuto ridotto oltre il 40% "
                    f"({original_size} → {new_size} byte) — "
                    "possibile troncatura, deploy rifiutato"
                )
            }), 400

    # Backup con timestamp
    # BUG-05 FIX: backup timestampato, pulizia automatica oltre MAX_BACKUPS
    backup_path = _backup_file(target_path)

    # Scrittura
    # BUG-03 FIX: in caso di errore di scrittura, ripristina il backup prima
    # di restituire l'errore — impedisce che il file di produzione scompaia.
    try:
        target_path.write_text(data["content"], encoding="utf-8")
    except Exception as e:
        # Rollback: ripristina il backup se esiste
        if backup_path and backup_path.exists():
            try:
                backup_path.rename(target_path)
                rollback_msg = "Rollback eseguito — file originale ripristinato."
            except Exception as rb_err:
                rollback_msg = f"Rollback FALLITO ({rb_err}) — verificare manualmente {backup_path}"
        else:
            rollback_msg = "Nessun backup disponibile per il rollback."
        return jsonify({
            "error":   f"Errore scrittura: {e}",
            "rollback": rollback_msg,
        }), 500

    # Riavvio container Pipelines (opzionale)
    restart_error = None
    if data.get("restart", False):
        try:
            subprocess.run(
                ["docker", "restart", "ai-pipelines-session"],
                check=False, timeout=30,
            )
        except Exception as e:
            restart_error = str(e)

    response = {
        "status":  "deployed",
        "path":    str(target_path),
        "backup":  str(backup_path) if backup_path else None,
    }
    if restart_error:
        response["restart_error"] = restart_error
    return jsonify(response)


# ─────────────────────────────────────────────────────────────────────────────
# Avvio
# ─────────────────────────────────────────────────────────────────────────────

# Avvio watcher automatico
_start_watcher()

if __name__ == "__main__":
    # BUG-08 FIX: avviso se il token di deploy non è stato personalizzato
    if DEPLOY_TOKEN == "change-me-in-production":
        print(
            "[RAG_SERVICE] ⚠️  DEPLOY_TOKEN non impostato — "
            "usa il valore di default pubblicamente noto. "
            "Imposta la variabile d'ambiente DEPLOY_TOKEN prima dell'uso in produzione.",
            flush=True,
        )

    print(
        f"[RAG_SERVICE] host={SERVICE_HOST} porta={SERVICE_PORT} "
        f"docs={DOCS_ROOT} qdrant={QDRANT_URL}",
        flush=True,
    )

    # Crea le directory dei domain se non esistono
    for d in DOMAIN_DIRS:
        (DOCS_ROOT / d).mkdir(parents=True, exist_ok=True)

    app.run(host=SERVICE_HOST, port=SERVICE_PORT, debug=False, threaded=True)
