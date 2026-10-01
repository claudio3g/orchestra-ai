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

