"""Test dell'endpoint /vram di rag_service (EGPU-01): ruoli main/aux, degrado, fallback. Richiede flask."""
import os, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; REPO = HERE.parent
sys.path.insert(0, str(HERE / "helpers"))
from tiny import check, summary
os.environ["PATH"] = str(HERE / "helpers" / "stubs") + os.pathsep + os.environ["PATH"]
from flask import Flask, request, jsonify

# Estrae SOLO le funzioni /vram da rag_service.py (il modulo intero richiede fastembed/qdrant).
src = (REPO / "rag" / "rag_service.py").read_text(encoding="utf-8")
a = src.index("_roles_warned = False"); b = src.index("# ─────", src.index('@app.route("/vram")'))
app = Flask("t"); ns = dict(app=app, request=request, jsonify=jsonify, subprocess=subprocess, os=os)
exec(compile(src[a:b], "rag_service_vram", "exec"), ns)
c = app.test_client()
def clear():
    for k in ("ORCHESTRA_GPU_MAIN", "ORCHESTRA_GPU_AUX", "ORCHESTRA_GPU_ID", "FAKE_ONLY_4060"): os.environ.pop(k, None)
    ns["_roles_warned"] = False

clear(); d = c.get("/vram").get_json()
roles = {("3090" if "3090" in g["name"] else "4060"): g["role"] for g in d["gpus"]}
check("auto: main=3090 (piu' VRAM), aux=4060", roles == {"3090": "main", "4060": "aux"}, str(roles))
check("auto: campi legacy = 3090", d["vram_free_mb"] == 23848 and d["source"] == "nvidia-smi" and d["vram_total_mb"] == 24576 and d["roles_mode"] == "auto")
clear(); os.environ.update(ORCHESTRA_GPU_MAIN="GPU-3090-UUID", ORCHESTRA_GPU_AUX="GPU-4060-UUID")
d = c.get("/vram").get_json()
check("env UUID: roles_mode=env, main=3090", d["roles_mode"] == "env" and d["gpu_name"].endswith("3090") and d["vram_free_mb"] == 23848)
d = c.get("/vram?gpu=aux").get_json()
check("?gpu=aux → 4060 al primo livello", d["gpu_name"].endswith("Laptop GPU") and d["vram_free_mb"] == 7046)
check("?gpu=inesistente → 404", c.get("/vram?gpu=GPU-XXXX").status_code == 404)
clear(); os.environ["ORCHESTRA_GPU_ID"] = "1"
d = c.get("/vram").get_json(); check("alias legacy ORCHESTRA_GPU_ID=1 → main 3090", d["gpu_name"].endswith("3090") and d["roles_mode"] == "env")
clear(); os.environ.update(ORCHESTRA_GPU_MAIN="GPU-3090-UUID", FAKE_ONLY_4060="1")
d = c.get("/vram").get_json()
check("eGPU scollegata: degrada a 4060 (auto), nessun aux", d["roles_mode"] == "auto" and d["gpu_name"].endswith("Laptop GPU") and [g["role"] for g in d["gpus"]] == ["main"])
clear(); os.environ["FAKE_ONLY_4060"] = "1"
d = c.get("/vram").get_json(); check("una sola GPU, nessuna env: come prima", d["vram_free_mb"] == 7046)
saved = os.environ["PATH"]; os.environ["PATH"] = "/usr/bin:/bin"; clear()
d = c.get("/vram").get_json(); check("nvidia-smi assente → fallback 2000", d == {"vram_free_mb": 2000, "source": "fallback"}, str(d))
os.environ["PATH"] = saved
summary("VRAM-ENDPOINT")
