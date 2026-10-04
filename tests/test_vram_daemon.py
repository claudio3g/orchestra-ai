"""Test del daemon VRAM di embedding_utils contro un rag_service nuovo e uno vecchio (compat)."""
import importlib, os, subprocess, sys, threading, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; REPO = HERE.parent
sys.path.insert(0, str(HERE / "helpers")); sys.path.insert(0, str(REPO / "ollama" / "pipelines"))
from tiny import check, summary
os.environ["PATH"] = str(HERE / "helpers" / "stubs") + os.pathsep + os.environ["PATH"]
from flask import Flask, request, jsonify
from werkzeug.serving import make_server

src = (REPO / "rag" / "rag_service.py").read_text(encoding="utf-8")
a = src.index("_roles_warned = False"); b = src.index("# ─────", src.index('@app.route("/vram")'))
new = Flask("new"); exec(compile(src[a:b], "v", "exec"), dict(app=new, request=request, jsonify=jsonify, subprocess=subprocess, os=os))
old = Flask("old")
@old.route("/vram")
def _old(): return jsonify({"vram_free_mb": 6000, "source": "nvidia-smi"})   # formato v1.5.0 senza gpus[]

def run(label, flask_app, port, expect):
    os.environ["RAG_SERVICE_URL"] = f"http://127.0.0.1:{port}"
    sys.modules.pop("embedding_utils", None)
    srv = make_server("127.0.0.1", port, flask_app); threading.Thread(target=srv.serve_forever, daemon=True).start()
    eu = importlib.import_module("embedding_utils")
    for _ in range(40):
        if eu.get_vram_free_mb() != 2000: break
        time.sleep(0.1)
    got = (eu.get_vram_free_mb(), eu.get_gpu_free_mb("main"), eu.get_gpu_free_mb("aux"))
    check(f"{label}: (main legacy, main, aux) = {expect}", got == expect, str(got)); srv.shutdown()
run("rag_service nuovo", new, 18651, (23848, 23848, 7046))
run("rag_service VECCHIO (compat)", old, 18652, (6000, 6000, 0))
summary("VRAM-DAEMON")
