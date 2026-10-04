# AI Context - RAG

> Generato: 2026-10-04T13:17:44Z
> Branch: dual-gpu-final

---

## File: rag/Dockerfile (691 byte)

```
FROM python:3.10-slim

WORKDIR /app

# Installa dipendenze di sistema minime
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copia requirements e installa dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice
COPY rag_service.py .
COPY rag_indexer_lib.py .

# Utente non-root per sicurezza
RUN useradd -m -u 1000 raguser && chown -R raguser:raguser /app
USER raguser

EXPOSE 6335

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:6335/health')" || exit 1

CMD ["python", "rag_service.py"]
```

## File: rag/docker-compose.prod.yml (996 byte)

```
version: '3.8'

services:
  rag-service:
    build: .
    container_name: ai-rag-service
    restart: unless-stopped
    ports:
      - "127.0.0.1:6335:6335"  # Esposto solo localhost per sicurezza
    environment:
      - QDRANT_URL=http://ai-qdrant-session:6333
      - RAG_DOCS_DIR=/home/raguser/ai-sessioni/document-ai
      - DEPLOY_TOKEN=${DEPLOY_TOKEN:-change-me-in-production}
      - RAG_SERVICE_HOST=0.0.0.0
      - RAG_SERVICE_PORT=6335
    volumes:
      # Mount dei documenti da indicizzare
      - ~/ai-sessioni/document-ai:/home/raguser/ai-sessioni/document-ai:ro
      # Mount per i file di deploy (solo se necessario)
      - ~/ai-sessioni/ollama/pipelines:/home/raguser/ai-sessioni/ollama/pipelines:ro
    networks:
      - ai-network
    mem_limit: 8g
    cpus: 2.0
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

networks:
  ai-network:
    external: true
    name: bridge  # I container usano il network bridge di default
```

## File: rag/patch_async_index.py (8204 byte)

```
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

def _run_index_job(job_id: str, incremental: bool = True) -> None:
    """Esegue l'indicizzazione in un thread separato."""
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

        hash_cache = _load_hash_cache()
        new_cache = hash_cache.copy()

        _log(f"🔍 Scansione documenti (incrementale={incremental})...")
        chunk_iter = scan_directory(DOCS_ROOT)
        qdrant = get_qdrant()
        processed = 0
        skipped = 0
        errors = 0
        files_seen: set[str] = set()
        domains_set: set[str] = set()
        t_start = time.time()
        batch: list[dict] = []
        batch_texts: list[str] = []
        skipped_files: set[str] = set()

        def _flush_batch() -> tuple[int, int]:
            nonlocal batch, batch_texts
            if not batch:
                return 0, 0
            try:
                embeddings = list(model.embed(batch_texts))
            except Exception as e:
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
            except Exception as e:
                n = len(batch)
                batch, batch_texts = [], []
                return 0, n

        for chunk in chunk_iter:
            file_path_str = chunk["path"]
            files_seen.add(file_path_str)
            domains_set.add(chunk["domain"])

            if incremental and file_path_str not in skipped_files:
                current_hash = _file_hash(Path(file_path_str))
                cached_hash = hash_cache.get(file_path_str, "")
                if current_hash and current_hash == cached_hash:
                    skipped_files.add(file_path_str)
                    skipped += 1
                    continue
                else:
                    new_cache[file_path_str] = current_hash
            elif file_path_str not in skipped_files:
                current_hash = _file_hash(Path(file_path_str))
                new_cache[file_path_str] = current_hash

            batch.append(chunk)
            batch_texts.append(chunk["text"])

            if len(batch) >= BATCH_SIZE:
                ok_n, err_n = _flush_batch()
                processed += ok_n
                errors += err_n
                if err_n:
                    _log(f"  ⚠️ {err_n} chunk saltati per errore")
                _log(f"  ✓ {processed} chunk processati")

        ok_n, err_n = _flush_batch()
        processed += ok_n
        errors += err_n

        _save_hash_cache(new_cache)

        elapsed = time.time() - t_start
        n_files = len(files_seen)
        domains = sorted(domains_set)

        _log(f"\n📄 {n_files} file scansionati")
        if skipped_files:
            _log(f"⏭️  {len(skipped_files)} file invariati saltati")
        _log(f"🏷️  Domini: {', '.join(domains) if domains else 'nessuno'}")
        if errors:
            _log(f"⚠️  {errors} chunk saltati per errori")
        _log(f"✅ Completato in {elapsed:.1f}s — {processed} chunk indicizzati")

        with _INDEX_JOBS_LOCK:
            _INDEX_JOBS[job_id]["status"] = "done"

    except Exception as e:
        _log(f"❌ Errore: {e}")
        with _INDEX_JOBS_LOCK:
            if job_id in _INDEX_JOBS:
                _INDEX_JOBS[job_id]["status"] = "error"


_WATCHER_INTERVAL = int(os.environ.get("RAG_WATCH_INTERVAL", 60))
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
                hash_cache = _load_hash_cache()
                changed = False
                for f in DOCS_ROOT.rglob("*"):
                    if f.is_file() and "__pycache__" not in str(f):
                        current_hash = _file_hash(f)
                        if current_hash != hash_cache.get(str(f), ""):
                            changed = True
                            break
                if changed:
                    print("[RAG] Watcher: file modificati, avvio indicizzazione incrementale", flush=True)
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

```

## File: rag/patch_endpoints.py (2505 byte)

```
# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: /index/async — indicizzazione asincrona con job ID
# Aggiunto in v1.4.0 — non modifica /index esistente
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/index/async", methods=["POST"])
def index_async():
    """
    Lancia l'indicizzazione in background e risponde subito con job_id.
    Parametro opzionale: {"incremental": true/false} (default: true)
    """
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
```

## File: rag/patch_job_index.py (7869 byte)

```
import re

target_file = "/home/claudio/ai-sessioni/rag/rag_service.py"

with open(target_file) as f:
    content = f.read()

# Nuovo _run_index_job — file per file con progressione
new_run_index_job = '''def _run_index_job(job_id: str, incremental: bool = True) -> None:
    """
    Indicizzazione file-per-file con progressione.
    - Un file alla volta per mantenere RAM stabile
    - gc.collect() dopo ogni file
    - File ordinati per dimensione (piccoli prima)
    - Log aggiornato in real-time con [N/TOT] filename
    """
    import gc
    import psutil

    if _INDEXING_IN_PROGRESS.is_set():
        print(f"[RAG] Job {job_id} saltato — indicizzazione già in corso", flush=True)
        with _INDEX_JOBS_LOCK:
            if job_id in _INDEX_JOBS:
                _INDEX_JOBS[job_id]["status"] = "skipped"
        return
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

        hash_cache = _load_hash_cache()
        new_cache = hash_cache.copy()

        # Raccoglie tutti i file da processare
        all_files = sorted(
            [f for f in DOCS_ROOT.rglob("*")
             if f.is_file() and "__pycache__" not in str(f)],
            key=lambda f: f.stat().st_size  # piccoli prima
        )
        total_files = len(all_files)
        _log(f"🔍 {total_files} file trovati (ordinati per dimensione)")

        qdrant = get_qdrant()
        processed_total = 0
        skipped_files = 0
        error_files = 0
        domains_set: set[str] = set()
        t_start = time.time()

        for file_idx, file_path in enumerate(all_files, 1):
            file_path_str = str(file_path)
            rel_name = file_path.name
            size_kb = file_path.stat().st_size // 1024

            # Controllo RAM disponibile prima di ogni file
            ram_free_mb = psutil.virtual_memory().available / 1024 / 1024
            if ram_free_mb < 1500:
                _log(f"  ⚠️ [{file_idx}/{total_files}] RAM insufficiente ({ram_free_mb:.0f} MB), skip: {rel_name}")
                skipped_files += 1
                continue

            # Controllo incrementale
            if incremental:
                current_hash = _file_hash(file_path)
                cached_hash = hash_cache.get(file_path_str, "")
                if current_hash and current_hash == cached_hash:
                    skipped_files += 1
                    continue
                new_cache[file_path_str] = current_hash

            _log(f"  📄 [{file_idx}/{total_files}] {rel_name} ({size_kb} KB)")

            try:
                # Chunking del singolo file
                from rag_indexer_lib import scan_directory as _scan
                file_chunks = [
                    c for c in _scan(DOCS_ROOT)
                    if c["path"] == file_path_str
                ]

                if not file_chunks:
                    _log(f"    ⚠️ Nessun chunk estratto")
                    continue

                domains_set.add(file_chunks[0].get("domain", ""))
                file_processed = 0
                file_errors = 0
                batch: list[dict] = []
                batch_texts: list[str] = []

                def _flush_batch() -> tuple[int, int]:
                    nonlocal batch, batch_texts
                    if not batch:
                        return 0, 0
                    try:
                        embeddings = list(model.embed(batch_texts))
                    except Exception as e:
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
                    except Exception as e:
                        n = len(batch)
                        batch, batch_texts = [], []
                        return 0, n

                for chunk in file_chunks:
                    batch.append(chunk)
                    batch_texts.append(chunk["text"])
                    if len(batch) >= BATCH_SIZE:
                        ok_n, err_n = _flush_batch()
                        file_processed += ok_n
                        file_errors += err_n

                ok_n, err_n = _flush_batch()
                file_processed += ok_n
                file_errors += err_n
                processed_total += file_processed

                _log(f"    ✅ {file_processed} chunk indicizzati" +
                     (f" ⚠️ {file_errors} errori" if file_errors else ""))

                # Salva hash cache dopo ogni file riuscito
                if incremental:
                    _save_hash_cache(new_cache)

            except Exception as e:
                _log(f"    ❌ Errore: {str(e)[:80]}")
                error_files += 1

            finally:
                # Libera memoria dopo ogni file
                gc.collect()

        elapsed = time.time() - t_start
        domains = sorted(domains_set - {""})

        _log(f"\\n📊 Riepilogo:")
        _log(f"  • File processati: {file_idx - skipped_files - error_files}/{total_files}")
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

'''

# Sostituisce la vecchia funzione _run_index_job
pattern = r'def _run_index_job\(job_id: str, incremental: bool = True\) -> None:.*?(?=\n# ────|\ndef _start_watcher)'
match = re.search(pattern, content, re.DOTALL)
if match:
    content = content[:match.start()] + new_run_index_job + content[match.end():]
    print(f"Funzione sostituita alla posizione {match.start()}")
else:
    print("ERRORE: pattern non trovato")

with open(target_file, "w") as f:
    f.write(content)

print("Patch applicata.")
```

## File: rag/pattern_logger.py (828 byte)

```
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
```

## File: rag/rag_indexer_lib.py (33768 byte)

```
"""
RAG Indexer Library v2.3.0 — Orchestra
==========================================
Estrae testo da file di vari formati e li suddivide in chunk.
Per i file Python (.py) usa chunking AST che produce un chunk per ogni
funzione/metodo, con metadati (function_name, start_line, end_line, type).
Per i file PDF usa chunking per pagina con metadati (pdf_page, pdf_title,
pdf_author, pdf_total_pages) e supporto opzionale a pdfplumber per tabelle.
Formati supportati: .md .txt .py .sh .json .pdf .docx .xlsx
Dipendenze obbligatorie: nessuna
Dipendenze opzionali: pypdf  pdfplumber  python-docx  openpyxl  pytesseract

CHANGELOG v2.3.0 rispetto a v2.2.0:
  OPT-01  scan_file(file_path, docs_root): processa un singolo file senza
          riscansire tutta la directory. Usato da _run_index_job per passare
          da O(n²) a O(n): prima ogni file triggerava scan_directory() intera.

CHANGELOG v2.2.0 rispetto a v2.1.0:
  PDF-1  Chunking per pagina: ogni pagina PDF diventa 1+ chunk con metadati
         (pdf_page, pdf_total_pages, pdf_title, pdf_author).
         Prima: testo estratto flat → chunking a 500 char → contesto pagina perso.

  PDF-2  Livello 2 — pdfplumber: se installato, sostituisce pypdf per l'estrazione
         di testo e tabelle con layout preservato. Attivazione automatica.

  PDF-3  Livello 3 — OCR stub: se pypdf e pdfplumber restituiscono testo vuoto
         (PDF scansionato), il chunk contiene un placeholder leggibile invece di
         sparire silenziosamente. OCR reale via pytesseract: flag USE_OCR=True
         nella sezione configurazione (richiede tesseract-ocr nel sistema).

  PDF-4  Gestione errori differenziata: PDF protetti da password → messaggio
         specifico; PDF corrotti/troncati → log con tipo eccezione distinto.

CHANGELOG v2.1.0 rispetto a v2.0.0:
  BUG-01/02/03/04/05: vedi changelog precedente.
"""

import ast
import json
import re
import uuid
from pathlib import Path
from typing import Iterator

# ─────────────────────────────────────────────────────────────────────────────
# Import opzionali — il servizio parte anche senza, con avvisi
# ─────────────────────────────────────────────────────────────────────────────
try:
    import pypdf
    _PYPDF_OK = True
except ImportError:
    _PYPDF_OK = False

try:
    import pdfplumber as _pdfplumber
    _PDFPLUMBER_OK = True
except ImportError:
    _PDFPLUMBER_OK = False

# Flag OCR: metti True per abilitare pytesseract su PDF scansionati.
# Richiede: pip install pytesseract pdf2image  +  apt install tesseract-ocr
USE_OCR = False
try:
    import pytesseract as _pytesseract
    from pdf2image import convert_from_path as _pdf2image
    _OCR_OK = True
except ImportError:
    _OCR_OK = False

try:
    import docx as _docx
    _DOCX_OK = True
except ImportError:
    _DOCX_OK = False

try:
    import openpyxl as _openpyxl
    _XLSX_OK = True
except ImportError:
    _XLSX_OK = False

# ─────────────────────────────────────────────────────────────────────────────
# Parametri chunking
# ─────────────────────────────────────────────────────────────────────────────
_CHARS_PER_TOKEN = 4
_CHUNK_TOKENS    = 512
_OVERLAP_TOKENS  = 64
CHUNK_CHARS      = _CHUNK_TOKENS  * _CHARS_PER_TOKEN   # 2048 caratteri
OVERLAP_CHARS    = _OVERLAP_TOKENS * _CHARS_PER_TOKEN  # 256 caratteri

_SUB_CHUNK_LINES = 80   # soglia righe per sub-chunking AST
_SUB_OVERLAP     = 10   # overlap righe tra sotto-chunk

SUPPORTED_EXT = frozenset({
    ".md", ".txt", ".py", ".sh", ".json",
    ".pdf", ".docx", ".xlsx"
})


# =============================================================================
# ESTRAZIONE TESTO
# =============================================================================

def extract_text(file_path: Path) -> str:
    """
    Estrae testo grezzo da un file in base all'estensione.
    Ritorna stringa vuota se il formato non è supportato o se c'è errore.
    """
    ext = file_path.suffix.lower()
    try:
        if ext in (".md", ".txt", ".py", ".sh"):
            return file_path.read_text(encoding="utf-8", errors="replace")

        if ext == ".json":
            raw  = file_path.read_text(encoding="utf-8", errors="replace")
            data = json.loads(raw)
            return json.dumps(data, ensure_ascii=False, indent=2)

        if ext == ".pdf":
            # extract_text() per PDF ritorna solo il testo grezzo concatenato.
            # Per chunking di qualità usa chunk_pdf_file() direttamente.
            pages_text = _extract_pdf_pages_text(file_path)
            return "\n\n".join(t for _, t in pages_text if t.strip())

        if ext == ".docx":
            if not _DOCX_OK:
                return f"[DOCX: python-docx non installato — {file_path.name}]"
            doc  = _docx.Document(str(file_path))
            pars = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(pars)

        if ext == ".xlsx":
            if not _XLSX_OK:
                return f"[XLSX: openpyxl non installato — {file_path.name}]"
            wb     = _openpyxl.load_workbook(
                str(file_path), read_only=True, data_only=True
            )
            sheets = []
            for name in wb.sheetnames:
                ws   = wb[name]
                rows = []
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None and str(c).strip()]
                    if cells:
                        rows.append(" | ".join(cells))
                if rows:
                    sheets.append(f"[Foglio: {name}]\n" + "\n".join(rows))
            wb.close()
            return "\n\n".join(sheets)

    except Exception as e:
        return f"[Errore estrazione {file_path.name}: {type(e).__name__}: {e}]"

    return ""


# =============================================================================
# CHUNKING STANDARD (per file non-Python)
# =============================================================================

def chunk_text(
    text:   str,
    source: str,
    domain: str,
    path:   str,
) -> list[dict]:
    """
    Divide il testo in chunk con overlap.
    Ogni chunk è un dict pronto per l'upsert in Qdrant.

    BUG-03 FIX: aggiunto `if end >= n: break` dopo aver accodato ogni chunk.
    Impedisce la produzione di un chunk finale spurio contenente solo
    i caratteri di overlap già presenti nel chunk precedente.
    """
    if not text.strip():
        return []

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{3,}", " ", text)

    chunks: list[dict] = []
    start = 0
    idx   = 0
    n     = len(text)

    while start < n:
        end = min(start + CHUNK_CHARS, n)

        # Cerca punto di taglio naturale (paragrafo → riga → spazio)
        if end < n:
            half = start + CHUNK_CHARS // 2
            bp = text.rfind("\n\n", half, end)
            if bp > half:
                end = bp + 2
            else:
                bp = text.rfind("\n", half, end)
                if bp > half:
                    end = bp + 1
                else:
                    bp = text.rfind(" ", half, end)
                    if bp > half:
                        end = bp + 1

        chunk_content = text[start:end].strip()
        if chunk_content:
            doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}::{idx}"))
            chunks.append({
                "text":      chunk_content,
                "domain":    domain,
                "source":    source,
                "path":      path,
                "chunk_idx": idx,
                "doc_id":    doc_id,
            })
            idx += 1

        # BUG-03 FIX: interrompi se abbiamo raggiunto la fine del testo.
        # Senza questo check, il loop proseguiva producendo un chunk spurio
        # con i soli caratteri di overlap già presenti nel chunk precedente.
        if end >= n:
            break

        new_start = end - OVERLAP_CHARS
        if new_start <= start:
            break
        start = new_start

    return chunks


# =============================================================================
# AST CHUNKING PER FILE PYTHON
# =============================================================================

def _sub_chunk(
    all_lines:   list[str],
    start_line0: int,         # 0-based, riga di inizio del nodo nel file
    end_line0:   int,         # 0-based esclusivo
    name:        str,
    node_type:   str,
    path:        str,
    domain:      str,
    source:      str,
    chunks:      list[dict],
    idx_ref:     list[int],   # [idx] mutabile per aggiornamento nonlocal
) -> None:
    """
    Divide un blocco di righe (funzione o metodo) in sotto-chunk da
    _SUB_CHUNK_LINES righe con overlap di _SUB_OVERLAP righe.

    BUG-02 FIX: la condizione di uscita è `if sub_end >= len(node_lines): break`
    (testato DOPO aver accodato il sotto-chunk). Prima: `pos = sub_end - 10`
    con guard `pos >= len-5` non avanzava mai per funzioni 81–N righe,
    causando infinite loop.
    """
    node_lines = all_lines[start_line0:end_line0]
    total      = len(node_lines)
    pos        = 0
    sub_idx    = 0

    while pos < total:
        sub_end = min(pos + _SUB_CHUNK_LINES, total)

        # Cerca un punto di taglio naturale (riga vuota) nell'ultima parte
        if sub_end < total:
            for lookback in range(1, min(20, sub_end - pos)):
                if node_lines[sub_end - lookback].strip() == "":
                    sub_end = sub_end - lookback + 1
                    break

        sub_code = "\n".join(node_lines[pos:sub_end]).strip()
        if sub_code:
            doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}::{idx_ref[0]}"))
            chunks.append({
                "text":          sub_code,
                "domain":        domain,
                "source":        source,
                "path":          path,
                "chunk_idx":     idx_ref[0],
                "doc_id":        doc_id,
                "function_name": f"{name}__part{sub_idx}" if total > _SUB_CHUNK_LINES else name,
                "start_line":    start_line0 + pos + 1,
                "end_line":      start_line0 + sub_end,
                "type":          node_type,
            })
            idx_ref[0] += 1
            sub_idx    += 1

        # BUG-02 FIX: se abbiamo raggiunto la fine del nodo, usciamo subito.
        # Prima: `pos = sub_end - 10` con guard `pos >= len-5` non terminava
        # mai quando sub_end == total, causando loop infinito.
        if sub_end >= total:
            break

        pos = sub_end - _SUB_OVERLAP
        if pos <= 0:
            pos = sub_end   # edge case: overlap più grande del sotto-chunk


def _extract_node(
    node:       ast.AST,
    all_lines:  list[str],
    path:       str,
    domain:     str,
    source:     str,
    chunks:     list[dict],
    idx_ref:    list[int],
    class_name: str = "",
) -> None:
    """
    Estrae un singolo FunctionDef / AsyncFunctionDef come chunk (o sotto-chunk
    se >_SUB_CHUNK_LINES righe).
    """
    start_line0 = node.lineno - 1       # 0-based
    end_line0   = node.end_lineno       # 0-based esclusivo
    n_lines     = end_line0 - start_line0

    full_name = f"{class_name}.{node.name}" if class_name else node.name
    node_type = (
        "async_method"   if class_name and isinstance(node, ast.AsyncFunctionDef) else
        "method"         if class_name else
        "async_function" if isinstance(node, ast.AsyncFunctionDef) else
        "function"
    )

    if n_lines > _SUB_CHUNK_LINES:
        _sub_chunk(
            all_lines, start_line0, end_line0,
            full_name, node_type,
            path, domain, source, chunks, idx_ref,
        )
    else:
        code = "\n".join(all_lines[start_line0:end_line0]).strip()
        if code:
            doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}::{idx_ref[0]}"))
            chunks.append({
                "text":          code,
                "domain":        domain,
                "source":        source,
                "path":          path,
                "chunk_idx":     idx_ref[0],
                "doc_id":        doc_id,
                "function_name": full_name,
                "start_line":    node.lineno,
                "end_line":      node.end_lineno,
                "type":          node_type,
            })
            idx_ref[0] += 1


def chunk_python_file(
    text:   str,
    source: str,
    domain: str,
    path:   str,
) -> list[dict]:
    """
    Suddivide un file Python in chunk per funzione/metodo usando AST.

    Strategia:
      L1 — funzioni top-level  → 1 chunk per funzione (sub-chunking se >80 righe)
      L2 — ClassDef            → 1 chunk per metodo della classe (BUG-01 FIX)
                                  la classe stessa NON diventa un chunk monolitico
      L3 — codice modulo-level → 1 chunk per blocco contiguo (import, costanti, ecc.)

    BUG-01 FIX: prima l'intera ClassDef diventava 1 chunk (es. class Pipeline
    di orchestra_manifold.py: 839 righe, 32 metodi ignorati → 4 chunk totali).
    Ora ogni metodo è un chunk separato → 35+ chunk su manifold.py.
    """
    if not text.strip():
        return []

    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Fallback al chunking standard se il file ha errori di sintassi
        return chunk_text(text, source, domain, path)

    all_lines   = text.splitlines()
    total_lines = len(all_lines)
    chunks:     list[dict] = []
    idx_ref:    list[int]  = [0]

    # Righe occupate da nodi top-level (0-based, end esclusivo)
    # Usato per estrarre il codice a livello modulo in seguito.
    occupied: list[tuple[int, int]] = []

    for node in ast.iter_child_nodes(tree):

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # ── Funzione top-level ────────────────────────────────────────
            _extract_node(
                node, all_lines, path, domain, source, chunks, idx_ref,
                class_name="",
            )
            occupied.append((node.lineno - 1, node.end_lineno))

        elif isinstance(node, ast.ClassDef):
            # ── BUG-01 FIX: ricorri nei metodi della classe ───────────────
            # Prima: chunk monolitico dell'intera classe.
            # Ora: 1 chunk per metodo, con sub-chunking se necessario.
            # La firma della classe (decoratori + nome + eredità) viene
            # inclusa nel primo metodo per dare contesto al RAG.
            class_header_line = node.lineno - 1  # 0-based

            # Separa metodi da attributi di classe (Assign, AnnAssign)
            methods = [
                child for child in ast.iter_child_nodes(node)
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]

            if not methods:
                # Classe senza metodi (solo attributi): chunk monolitico
                start_l = node.lineno - 1
                end_l   = node.end_lineno
                code    = "\n".join(all_lines[start_l:end_l]).strip()
                if code:
                    doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}::{idx_ref[0]}"))
                    chunks.append({
                        "text":          code,
                        "domain":        domain,
                        "source":        source,
                        "path":          path,
                        "chunk_idx":     idx_ref[0],
                        "doc_id":        doc_id,
                        "function_name": node.name,
                        "start_line":    node.lineno,
                        "end_line":      node.end_lineno,
                        "type":          "class",
                    })
                    idx_ref[0] += 1
            else:
                # Chunk degli attributi di classe + intestazione (righe prima
                # del primo metodo)
                first_method_line = methods[0].lineno - 1
                header_block = "\n".join(
                    all_lines[class_header_line:first_method_line]
                ).strip()
                if header_block:
                    doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}::{idx_ref[0]}"))
                    chunks.append({
                        "text":          header_block,
                        "domain":        domain,
                        "source":        source,
                        "path":          path,
                        "chunk_idx":     idx_ref[0],
                        "doc_id":        doc_id,
                        "function_name": f"{node.name}.__class_header__",
                        "start_line":    node.lineno,
                        "end_line":      methods[0].lineno - 1,
                        "type":          "class_header",
                    })
                    idx_ref[0] += 1

                # 1 chunk per metodo
                for method in methods:
                    _extract_node(
                        method, all_lines, path, domain, source, chunks, idx_ref,
                        class_name=node.name,
                    )

            occupied.append((node.lineno - 1, node.end_lineno))

    # ── Codice modulo-level (righe non occupate da funzioni/classi) ───────────
    # Raccoglie blocchi contigui di righe non coperte da nodi già estratti.
    # La ricerca è O(n) con set di righe occupate invece di O(n×m) con lista.
    occupied_lines: set[int] = set()
    for start_occ, end_occ in occupied:
        occupied_lines.update(range(start_occ, end_occ))

    module_parts: list[str] = []
    block_start: int | None = None

    for i in range(total_lines):
        in_occ = i in occupied_lines
        if not in_occ and block_start is None:
            block_start = i
        elif in_occ and block_start is not None:
            block = "\n".join(all_lines[block_start:i]).strip()
            if block:
                module_parts.append(block)
            block_start = None

    # Ultimo blocco (se il file termina con codice modulo-level)
    if block_start is not None:
        block = "\n".join(all_lines[block_start:total_lines]).strip()
        if block:
            module_parts.append(block)

    if module_parts:
        module_text = "\n\n".join(module_parts).strip()
        if module_text:
            doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}::{idx_ref[0]}"))
            chunks.append({
                "text":          module_text,
                "domain":        domain,
                "source":        source,
                "path":          path,
                "chunk_idx":     idx_ref[0],
                "doc_id":        doc_id,
                "function_name": "(module_level)",
                "start_line":    1,
                "end_line":      total_lines,
                "type":          "module_level",
            })
            idx_ref[0] += 1

    return chunks


# =============================================================================
# PDF: ESTRAZIONE PER PAGINA + METADATA + CHUNKING (PDF-1/2/3/4)
# =============================================================================

def _extract_pdf_metadata(file_path: Path) -> dict:
    """
    Estrae i metadati del PDF (title, author, subject, num_pages).
    Ritorna dict con valori stringa/int; stringa vuota se non disponibile.
    PDF-4: gestione separata per PDF protetti vs corrotti.
    """
    meta = {"pdf_title": "", "pdf_author": "", "pdf_subject": "", "pdf_total_pages": 0}
    if not _PYPDF_OK:
        return meta
    try:
        reader = pypdf.PdfReader(str(file_path), strict=False)
        meta["pdf_total_pages"] = len(reader.pages)
        if reader.metadata:
            meta["pdf_title"]   = str(reader.metadata.get("/Title",   "") or "").strip()
            meta["pdf_author"]  = str(reader.metadata.get("/Author",  "") or "").strip()
            meta["pdf_subject"] = str(reader.metadata.get("/Subject", "") or "").strip()
    except pypdf.errors.FileNotDecryptedError:
        meta["pdf_title"] = "[PDF protetto da password]"
    except Exception:
        pass
    return meta


def _extract_pdf_pages_text(file_path: Path) -> list[tuple[int, str]]:
    """
    Estrae il testo di ogni pagina PDF come lista di (numero_pagina_1based, testo).

    Strategia a tre livelli (PDF-1/2/3):
      L1 pypdf       — sempre disponibile, testo digitale
      L2 pdfplumber  — se installato, migliore per tabelle e layout
      L3 OCR         — se USE_OCR=True e testo vuoto (PDF scansionato)

    PDF-4: gestione differenziata per PDF protetti da password vs corrotti.
    """
    if not _PYPDF_OK and not _PDFPLUMBER_OK:
        return [(1, "[PDF: pypdf e pdfplumber non installati]")]

    pages: list[tuple[int, str]] = []

    # ── Livello 2: pdfplumber (preferito se disponibile) ─────────────────────
    if _PDFPLUMBER_OK:
        try:
            with _pdfplumber.open(str(file_path)) as pdf:
                for i, page in enumerate(pdf.pages, 1):
                    parts: list[str] = []
                    text = page.extract_text() or ""
                    if text.strip():
                        parts.append(text.strip())
                    # Tabelle: converti in testo tabulare leggibile
                    try:
                        tables = page.extract_tables() or []
                        for table in tables:
                            rows = [
                                " | ".join(str(cell or "").strip() for cell in row)
                                for row in table if any(cell for cell in row)
                            ]
                            if rows:
                                parts.append("\n".join(rows))
                    except Exception:
                        pass
                    page_text = "\n\n".join(parts)
                    pages.append((i, page_text))
            if any(t.strip() for _, t in pages):
                return pages   # pdfplumber ha estratto testo → usa questo
        except Exception as e:
            err_type = type(e).__name__
            print(f"[RAG_INDEXER] pdfplumber fallito su {file_path.name}: {err_type}: {e}", flush=True)
        pages = []  # reset — prova con pypdf

    # ── Livello 1: pypdf ──────────────────────────────────────────────────────
    if _PYPDF_OK:
        try:
            reader = pypdf.PdfReader(str(file_path), strict=False)
            # PDF-4: PDF protetto da password
            if reader.is_encrypted:
                return [(1, f"[PDF protetto da password — impossibile estrarre testo: {file_path.name}]")]
            for i, page in enumerate(reader.pages, 1):
                try:
                    text = page.extract_text() or ""
                    pages.append((i, text.strip()))
                except Exception as pe:
                    pages.append((i, f"[Errore pagina {i}: {type(pe).__name__}]"))
        except pypdf.errors.PdfStreamError as e:
            return [(1, f"[PDF corrotto o troncato: {file_path.name} — {e}]")]
        except pypdf.errors.PdfReadError as e:
            return [(1, f"[PDF non leggibile: {file_path.name} — {e}]")]
        except Exception as e:
            return [(1, f"[Errore lettura PDF {file_path.name}: {type(e).__name__}: {e}]")]

    # ── Livello 3: OCR per PDF scansionati (testo vuoto dopo L1/L2) ──────────
    if USE_OCR and _OCR_OK and not any(t.strip() for _, t in pages):
        try:
            print(f"[RAG_INDEXER] OCR su {file_path.name}...", flush=True)
            images = _pdf2image(str(file_path))
            pages = []
            for i, img in enumerate(images, 1):
                ocr_text = _pytesseract.image_to_string(img, lang="ita+eng").strip()
                pages.append((i, ocr_text))
            print(f"[RAG_INDEXER] OCR completato: {len(pages)} pagine", flush=True)
        except Exception as e:
            print(f"[RAG_INDEXER] OCR fallito su {file_path.name}: {e}", flush=True)

    # PDF-3: stub per PDF scansionati senza OCR
    if not any(t.strip() for _, t in pages):
        total = len(pages) if pages else 1
        return [
            (i, f"[Pagina {i}/{total} — PDF scansionato: testo non estraibile. "
                 f"Abilita USE_OCR=True in rag_indexer_lib.py per l'OCR automatico.]")
            for i in range(1, total + 1)
        ]

    return pages


def chunk_pdf_file(
    file_path: Path,
    source:    str,
    domain:    str,
    path:      str,
) -> list[dict]:
    """
    Chunking per pagina di un file PDF con metadati completi.

    Ogni pagina PDF → 1 chunk (se ≤ CHUNK_CHARS) oppure N chunk con overlap
    (se la pagina è molto lunga, es. PDF con testo denso).

    Metadati nel payload Qdrant:
      pdf_page, pdf_total_pages, pdf_title, pdf_author, pdf_subject

    Questo permette query RAG tipo:
      "cosa dice pagina 5 del manuale?"
      "documenti scritti da [autore]"
      "trova il capitolo 3 di [titolo]"
    """
    meta   = _extract_pdf_metadata(file_path)
    pages  = _extract_pdf_pages_text(file_path)
    chunks: list[dict] = []
    idx    = 0

    for page_num, page_text in pages:
        if not page_text.strip():
            continue

        # Normalizza il testo della pagina
        page_text = re.sub(r"\n{3,}", "\n\n", page_text)
        page_text = re.sub(r"[ \t]{3,}", " ", page_text)

        # Prefisso di contesto: sempre visibile al modello
        prefix = f"[Pagina {page_num}/{meta['pdf_total_pages']} — {source}]\n"
        if meta["pdf_title"]:
            prefix = f"[{meta['pdf_title']} — Pagina {page_num}/{meta['pdf_total_pages']}]\n"

        full_text = prefix + page_text

        if len(full_text) <= CHUNK_CHARS:
            # Pagina intera in un chunk unico
            doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}::pdf::{page_num}"))
            chunks.append({
                "text":            full_text,
                "domain":          domain,
                "source":          source,
                "path":            path,
                "chunk_idx":       idx,
                "doc_id":          doc_id,
                "pdf_page":        page_num,
                "pdf_total_pages": meta["pdf_total_pages"],
                "pdf_title":       meta["pdf_title"],
                "pdf_author":      meta["pdf_author"],
                "pdf_subject":     meta["pdf_subject"],
                "type":            "pdf_page",
            })
            idx += 1
        else:
            # Pagina lunga: sub-chunking con overlap, preservando il prefisso
            start = 0
            sub_idx = 0
            n = len(page_text)
            while start < n:
                end = min(start + CHUNK_CHARS - len(prefix), n)
                if end < n:
                    half = start + (CHUNK_CHARS - len(prefix)) // 2
                    for sep in ("\n\n", "\n", " "):
                        bp = page_text.rfind(sep, half, end)
                        if bp > half:
                            end = bp + len(sep)
                            break
                chunk_content = (prefix + page_text[start:end]).strip()
                if chunk_content:
                    sub_label = f"::pdf::{page_num}::{sub_idx}"
                    doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}{sub_label}"))
                    chunks.append({
                        "text":            chunk_content,
                        "domain":          domain,
                        "source":          source,
                        "path":            path,
                        "chunk_idx":       idx,
                        "doc_id":          doc_id,
                        "pdf_page":        page_num,
                        "pdf_total_pages": meta["pdf_total_pages"],
                        "pdf_title":       meta["pdf_title"],
                        "pdf_author":      meta["pdf_author"],
                        "pdf_subject":     meta["pdf_subject"],
                        "type":            "pdf_page",
                    })
                    idx += 1
                    sub_idx += 1
                if end >= n:
                    break
                new_start = end - OVERLAP_CHARS
                if new_start <= start:
                    break
                start = new_start

    return chunks


# =============================================================================
# SCANSIONE DIRECTORY
# =============================================================================

def scan_directory(docs_root: Path) -> Iterator[dict]:
    """
    Scansiona ricorsivamente docs_root e restituisce chunk via generator.
    Per i file Python usa chunking AST; per tutti gli altri usa chunking
    testuale standard con overlap.

    BUG-04 FIX: ogni file è wrappato in try/except. Un file problematico
    logga l'errore e continua la scansione invece di bloccarla.
    """
    for file_path in sorted(docs_root.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in SUPPORTED_EXT:
            continue
        if file_path.name.startswith("."):
            continue

        try:
            rel    = file_path.relative_to(docs_root)
            domain = rel.parts[0] if len(rel.parts) > 1 else "general"
        except ValueError:
            domain = "general"

        try:
            text = extract_text(file_path)
            if not text.strip():
                continue

            if file_path.suffix.lower() == ".py":
                yield from chunk_python_file(
                    text   = text,
                    source = file_path.name,
                    domain = domain,
                    path   = str(file_path),
                )
            elif file_path.suffix.lower() == ".pdf":
                # PDF-1/2/3/4: chunking per pagina con metadati
                yield from chunk_pdf_file(
                    file_path = file_path,
                    source    = file_path.name,
                    domain    = domain,
                    path      = str(file_path),
                )
            else:
                yield from chunk_text(
                    text   = text,
                    source = file_path.name,
                    domain = domain,
                    path   = str(file_path),
                )

        except Exception as e:
            # BUG-04 FIX: log per file problematico, scansione continua.
            print(
                f"[RAG_INDEXER] Errore su {file_path.name}: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )
            continue


def scan_file(file_path: Path, docs_root: Path) -> list[dict]:
    """
    OPT-01: processa un singolo file e ritorna la sua lista di chunk.

    Equivalente a [c for c in scan_directory(docs_root) if c["path"] == str(file_path)]
    ma senza riscansire tutta la directory. Riduce il costo da O(n_files²) a O(1)
    per file durante l'indicizzazione file-per-file in _run_index_job.

    Ritorna lista vuota se il file non è supportato, è nascosto, o dà errore.
    """
    if not file_path.is_file():
        return []
    if file_path.suffix.lower() not in SUPPORTED_EXT:
        return []
    if file_path.name.startswith("."):
        return []

    try:
        rel    = file_path.relative_to(docs_root)
        domain = rel.parts[0] if len(rel.parts) > 1 else "general"
    except ValueError:
        domain = "general"

    try:
        text = extract_text(file_path)
        if not text.strip():
            return []

        ext = file_path.suffix.lower()
        if ext == ".py":
            return chunk_python_file(
                text=text, source=file_path.name,
                domain=domain, path=str(file_path),
            )
        elif ext == ".pdf":
            return chunk_pdf_file(
                file_path=file_path, source=file_path.name,
                domain=domain, path=str(file_path),
            )
        else:
            return chunk_text(
                text=text, source=file_path.name,
                domain=domain, path=str(file_path),
            )
    except Exception as e:
        print(
            f"[RAG_INDEXER] Errore su {file_path.name}: "
            f"{type(e).__name__}: {e}",
            flush=True,
        )
        return []


# =============================================================================
# UTILITÀ
# =============================================================================

def check_dependencies() -> dict[str, bool]:
    """Verifica disponibilità dipendenze opzionali."""
    return {
        "pypdf":       _PYPDF_OK,
        "pdfplumber":  _PDFPLUMBER_OK,
        "ocr":         USE_OCR and _OCR_OK,
        "python-docx": _DOCX_OK,
        "openpyxl":    _XLSX_OK,
    }
```

## File: rag/rag_patch_2.py (1389 byte)

```
target_file = "/home/claudio/ai-sessioni/rag/rag_service.py"

with open(target_file) as f:
    content = f.read()

# Rimuove l'excepthook inserito nel posto sbagliato
old = """app = Flask(__name__)

# Handler globale eccezioni nei thread — previene crash del processo principale
import sys as _sys
def _thread_excepthook(args):
    print(f"[RAG] Eccezione non gestita nel thread {args.thread.name}: {args.exc_value}", flush=True)
    import traceback
    traceback.print_exception(args.exc_type, args.exc_value, args.exc_tb)
threading.excepthook = _thread_excepthook"""

new = "app = Flask(__name__)"
content = content.replace(old, new, 1)

# Inserisce l'excepthook DOPO il blocco degli import threading (dopo _INDEX_JOBS_LOCK)
old = "_INDEX_JOBS_LOCK = threading.Lock()\n_INDEXING_IN_PROGRESS = threading.Event()"
new = """_INDEX_JOBS_LOCK = threading.Lock()
_INDEXING_IN_PROGRESS = threading.Event()

# Handler globale eccezioni nei thread — previene crash del processo principale
def _thread_excepthook(args):
    print(f"[RAG] Eccezione thread {args.thread.name}: {args.exc_value}", flush=True)
    import traceback
    traceback.print_exception(args.exc_type, args.exc_value, args.exc_tb)
threading.excepthook = _thread_excepthook"""

content = content.replace(old, new, 1)

with open(target_file, "w") as f:
    f.write(content)

print("Excepthook spostato nel posto corretto.")
```

## File: rag/rag_patch_3.py (4072 byte)

```
target_file = "/home/claudio/ai-sessioni/rag/rag_service.py"

with open(target_file) as f:
    content = f.read()

# Sostituisce il blocco _watch con versione resource-aware
old = """    def _watch():
        print(f"[RAG] Watcher avviato (interval={_WATCHER_INTERVAL}s)", flush=True)
        while True:
            time.sleep(_WATCHER_INTERVAL)
            try:
                hash_cache = _load_hash_cache()
                changed = False
                for f in DOCS_ROOT.rglob("*"):
                    if f.is_file() and "__pycache__" not in str(f):
                        current_hash = _file_hash(f)
                        if current_hash != hash_cache.get(str(f), ""):
                            changed = True
                            break
                if changed:
                    print("[RAG] Watcher: file modificati, avvio indicizzazione incrementale", flush=True)
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
                print(f"[RAG] Watcher errore: {e}", flush=True)"""

new = """    def _watch():
        print(f"[RAG] Watcher avviato (interval={_WATCHER_INTERVAL}s)", flush=True)
        while True:
            time.sleep(_WATCHER_INTERVAL)
            try:
                # Salta se indicizzazione già in corso
                if _INDEXING_IN_PROGRESS.is_set():
                    print("[RAG] Watcher: indicizzazione in corso, skip", flush=True)
                    continue

                # Verifica RAM disponibile — richiede almeno 2 GB liberi
                try:
                    import psutil
                    ram_free_mb = psutil.virtual_memory().available / 1024 / 1024
                    if ram_free_mb < 2048:
                        print(f"[RAG] Watcher: RAM insufficiente ({ram_free_mb:.0f} MB liberi), skip", flush=True)
                        continue
                except ImportError:
                    pass  # psutil non disponibile, procedi comunque

                hash_cache = _load_hash_cache()
                changed = False
                for f in DOCS_ROOT.rglob("*"):
                    if f.is_file() and "__pycache__" not in str(f):
                        current_hash = _file_hash(f)
                        if current_hash != hash_cache.get(str(f), ""):
                            changed = True
                            break
                if changed:
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
                print(f"[RAG] Watcher errore: {e}", flush=True)"""

content = content.replace(old, new, 1)

# Riabilita il watcher
old = "# Avvio watcher automatico — disabilitato temporaneamente\n# _start_watcher()"
new = "# Avvio watcher automatico\n_start_watcher()"
content = content.replace(old, new, 1)

with open(target_file, "w") as f:
    f.write(content)

print("Watcher resource-aware aggiornato.")
```

## File: rag/rag_service.py (55047 byte)

```
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
```

## File: rag/requirements.txt (102 byte)

```
flask==2.3.3
fastembed==0.3.3
qdrant-client==1.10.0
pypdf==3.17.4
pdfplumber==0.10.4
requests==2.31.0
```

