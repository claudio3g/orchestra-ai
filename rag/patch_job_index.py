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
