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
