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
