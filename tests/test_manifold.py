"""Test del manifold (EGPU-03/04) con Ollama simulato. Richiede: pip install pydantic requests."""
import json, os, sys, types
from pathlib import Path
from unittest import mock
HERE = Path(__file__).resolve().parent; REPO = HERE.parent
sys.path.insert(0, str(HERE / "helpers")); sys.path.insert(0, str(REPO / "ollama" / "pipelines"))
from tiny import check, summary
os.environ.pop("OLLAMA_AUX_URL", None)
import orchestra_manifold as M

MAIN, AUX = "http://main:11434", "http://aux:11434"
class Resp:
    def __init__(s, lines=None, status=200): s._l = lines or []; s.status_code = status
    def __enter__(s): return s
    def __exit__(s, *a): return False
    def raise_for_status(s): pass
    def iter_lines(s): return iter(s._l)
def chunk(t): return json.dumps({"message": {"content": t}}).encode()
def new(aux=False, **v):
    p = M.Pipeline(); p.valves.ollama_url = MAIN
    if aux: p.valves.ollama_url_aux = AUX
    for k, x in v.items(): setattr(p.valves, k, x)
    p._aux_checked_until = 0.0; return p
def calls(post): return [(c.args[0], c.kwargs["json"]) for c in post.call_args_list]

print("== backend per ruolo")
p = new(aux=False)
check("aux spento: coordinator su main", p._backend_for("llama3.2:3b") == "main")
check("aux spento: url = main", p._url_for("llama3.2:3b") == MAIN)
check("aux spento: vram_aux = None", p.vram_aux_free_mb() is None)
p = new(aux=True)
with mock.patch.object(M.requests, "get", return_value=Resp(status=200)) as g:
    check("aux ok: coordinator su aux", p._backend_for("llama3.2:3b") == "aux")
    check("aux ok: moondream su aux", p._backend_for("moondream:v2") == "aux")
    check("aux ok: llava su aux", p._backend_for("llava:7b") == "aux")
    check("aux ok: qwen3.5:9b su main", p._backend_for("qwen3.5:9b") == "main")
    check("aux ok: quality su main", p._backend_for(p.valves.model_quality) == "main")
    check("health check in cache (1 sola GET per 5 chiamate)", g.call_count == 1, f"GET={g.call_count}")
p = new(aux=True)
with mock.patch.object(M.requests, "get", side_effect=M.requests.ConnectionError("down")):
    check("aux giu': coordinator ricade su main", p._backend_for("llama3.2:3b") == "main")
    check("aux giu': url = main", p._url_for("llama3.2:3b") == MAIN)

print("== selezione modello testo (soglia 11000 MB)")
st = lambda v: {"ram_mb": 20000, "vram_mb": v, "vram_aux_mb": None}
p = new()
check("3090 libera (23848) → quality, tutta in GPU", p.select_text_model("quality", st(23848)) == (p.valves.model_quality, {}))
check("12000 → quality in GPU", p.select_text_model("quality", st(12000)) == (p.valves.model_quality, {}))
check("10999 → quality con offload parziale (legacy)", p.select_text_model("quality", st(10999)) == (p.valves.model_quality, {"num_gpu": 20}))
check("4060 sola (7046) → comportamento 8 GB invariato", p.select_text_model("quality", st(7046)) == (p.valves.model_quality, {"num_gpu": 20}))
check("fast con 3090 → qwen3.5:9b", p.select_text_model("fast", st(23848))[0] == p.valves.model_fast)
check("VRAM 3000 → coordinator", p.select_text_model("fast", st(3000))[0] == p.valves.model_coordinator)

print("== vision: VRAM dell'aux quando la vision gira li'")
p = new(aux=True)
with mock.patch.object(M.requests, "get", return_value=Resp(status=200)):
    s = {"ram_mb": 20000, "vram_mb": 23848, "vram_aux_mb": 7000}
    check("aux 7000 → llava full GPU", p.select_vision_model(s) == (p.valves.model_vision, {}))
    s["vram_aux_mb"] = 4000
    m, o = p.select_vision_model(s); check("aux 4000 → llava parziale (num_gpu)", m == p.valves.model_vision and "num_gpu" in o, str((m, o)))
    s["vram_aux_mb"] = 1500
    check("aux 1500 → moondream (anche con main libero)", p.select_vision_model(s)[0] == p.valves.model_vision_fallback)
    s["vram_aux_mb"] = None
    check("aux sconosciuta → usa VRAM main (llava full)", p.select_vision_model(s) == (p.valves.model_vision, {}))
p = new(aux=False)
check("aux spento: vision usa VRAM main", p.select_vision_model({"ram_mb": 1, "vram_mb": 6000, "vram_aux_mb": 100}) == (p.valves.model_vision, {}))

print("== stream_ollama: instradamento, keep_alive, failover")
def run(p, model, post):
    with mock.patch.object(M.requests, "post", post):
        return "".join(p.stream_ollama(model, [{"role": "user", "content": "x"}]))
p = new(aux=True); post = mock.Mock(return_value=Resp([chunk("ciao")]))
with mock.patch.object(M.requests, "get", return_value=Resp(status=200)):
    out = run(p, "llama3.2:3b", post); (u, pl), = calls(post)
    check("coordinator → URL aux", u == AUX + "/api/chat", u)
    check("coordinator: keep_alive >= 1800", pl["keep_alive"] >= 1800, str(pl["keep_alive"]))
    check("risposta restituita", out == "ciao", out)
    post.reset_mock()
    with mock.patch.object(p, "vram_free_mb", return_value=23848):
        run(p, p.valves.model_quality, post); (u, pl), = calls(post)
    check("quality → URL main", u == MAIN + "/api/chat", u)
    check("quality con VRAM abbondante: keep_alive 900", pl["keep_alive"] == 900, str(pl["keep_alive"]))
    post.reset_mock()
    with mock.patch.object(p, "vram_free_mb", return_value=7046):
        run(p, p.valves.model_quality, post); (u, pl), = calls(post)
    check("quality con VRAM scarsa: keep_alive storico (0)", pl["keep_alive"] == 0, str(pl["keep_alive"]))
p = new(aux=False); post = mock.Mock(return_value=Resp([chunk("ok")]))
run(p, "llama3.2:3b", post); (u, pl), = calls(post)
check("aux spento: coordinator su main", u == MAIN + "/api/chat")
check("aux spento: keep_alive storico 600", pl["keep_alive"] == 600, str(pl["keep_alive"]))

p = new(aux=True)
def flaky(url, **kw):
    if url.startswith(AUX): raise M.requests.ConnectionError("aux down")
    return Resp([chunk("dal main")])
post = mock.Mock(side_effect=flaky)
with mock.patch.object(M.requests, "get", return_value=Resp(status=200)):
    out = run(p, "llama3.2:3b", post)
us = [c[0] for c in calls(post)]
check("failover: prima aux poi main", us == [AUX + "/api/chat", MAIN + "/api/chat"], str(us))
check("failover: risposta dal main, nessun errore", out == "dal main", out)
check("failover: aux marcato giu'", p._aux_healthy is False and p._aux_checked_until > 0)
post2 = mock.Mock(return_value=Resp([chunk("x")]))
with mock.patch.object(M.requests, "post", post2): "".join(p.stream_ollama("llama3.2:3b", []))
check("dopo il failover le richieste vanno subito al main (TTL)", calls(post2)[0][0] == MAIN + "/api/chat")
p = new(aux=False); post = mock.Mock(side_effect=M.requests.ConnectionError("x"))
check("main giu': errore chiaro (nessun loop)", "non raggiungibile" in run(p, "llama3.2:3b", post) and post.call_count == 1)

print("== statistiche e banner")
p = new(aux=False); s = p.get_system_stats()
check("stats: vram_aux_mb presente (None se aux spento)", "vram_aux_mb" in s and s["vram_aux_mb"] is None)
check("banner senza aux", "aux" not in p.session_status("quality", {"ram_mb": 8192, "vram_mb": 24576, "vram_aux_mb": None}))
check("banner con aux", "+aux 6.8GB" in p.session_status("quality", {"ram_mb": 8192, "vram_mb": 24576, "vram_aux_mb": 7000}))
check("banner non cita piu' 8GB", "8GB" not in p.session_status("quality", {"ram_mb": 8192, "vram_mb": 1024, "vram_aux_mb": None}))

print("== fallback subprocess VRAM con 2 GPU (stub nvidia-smi)")
os.environ["PATH"] = str(HERE / "helpers" / "stubs") + os.pathsep + os.environ["PATH"]
M._VRAM_DAEMON_AVAILABLE = False
p = new()
os.environ["ORCHESTRA_GPU_MAIN"] = "GPU-3090-UUID"; check("main=3090 → 23848", p.vram_free_mb() == 23848, str(p.vram_free_mb()))
os.environ.pop("ORCHESTRA_GPU_MAIN"); v = p.vram_free_mb()
check("senza ruoli: nessun crash (prima GPU, non il vecchio 2000)", v == 7046, str(v))

print("== B25 memorie di Open WebUI 0.13 nel messaggio utente")
MEM = "[Relevant Context]\n- L'utente usa un Acer Predator con RTX 4060"
BLOCK = f"\n<memory_context>\n{MEM}\n</memory_context>"
PROMPT = "/generate a starship like an ancient galeon flying over saturn ring"
msgs = [{"role": "system", "content": "regole"}, {"role": "user", "content": "ciao"},
        {"role": "assistant", "content": "ciao!"}, {"role": "user", "content": PROMPT + BLOCK}]
clean, mem = M.Pipeline._separate_memory_context(msgs)
check("blocco tolto dal messaggio utente", clean[-1]["content"] == PROMPT, repr(clean[-1]["content"]))
check("memoria restituita senza marcatori", mem == MEM, repr(mem))
check("memoria come sistema dopo i sistemi iniziali", clean[1]["role"] == "system" and clean[1]["content"].startswith(M.MEMORY_MARKER) and MEM in clean[1]["content"])
check("lista originale non modificata", msgs[-1]["content"].endswith("</memory_context>") and len(msgs) == 4)
c2, m2 = M.Pipeline._separate_memory_context([{"role": "user", "content": "solo testo"}])
check("senza memorie: nulla cambia", c2 == [{"role": "user", "content": "solo testo"}] and m2 == "")
lst = [{"role": "user", "content": [{"type": "text", "text": "descrivi"}, {"type": "image_url", "image_url": {"url": "data:x,AA"}},
                                     {"type": "text", "text": BLOCK.strip()}]}]
c3, m3 = M.Pipeline._separate_memory_context(lst)
check("contenuto a lista: parte solo-memoria tolta, immagine tenuta",
      [i.get("type") for i in c3[-1]["content"]] == ["text", "image_url"] and c3[-1]["content"][0]["text"] == "descrivi" and m3 == MEM, str(c3))
c4, m4 = M.Pipeline._separate_memory_context([{"role": "user", "content": "a" + BLOCK}, {"role": "user", "content": "b" + BLOCK}])
check("stessa memoria in due messaggi: un solo blocco", m4 == MEM and c4[0]["content"].count(MEM) == 1 and c4[1]["content"] == "a")

p = new(show_session_status=False)
p.get_system_stats = lambda: {"ram_mb": 8000, "vram_mb": 20000, "vram_aux_mb": None}
seen = {}
class Loop:
    def pipe(self, user_message, model_id, messages, body):
        seen["prompt"] = user_message; seen["msgs"] = messages; return iter(["fatto"])
p._load_image_loop = lambda: Loop()
out = "".join(p.pipe(PROMPT + BLOCK, "orchestra", msgs, {"user": {"id": "u", "role": "admin"}}))
check("/generate: prompt pulito a image_loop", seen.get("prompt") == PROMPT[len("/generate"):].strip(), repr(seen.get("prompt")))
check("/generate: niente memorie nella risposta", "memory_context" not in out and "Acer" not in out, out[:200])

p = new(show_session_status=False)
p.get_system_stats = lambda: {"ram_mb": 8000, "vram_mb": 20000, "vram_aux_mb": None}
routed = {}
def fake_route(t): routed["text"] = t; return ("linux_admin", "🖥️", "LINUX_ADMIN", 0.61)
p.route_text = fake_route
sent = {}
def fake_stream(model, ollama_messages, *a, **k): sent["m"] = ollama_messages; return iter(["risposta"])
p.stream_ollama = fake_stream
q = [{"role": "system", "content": M.RAG_MARKER + "\ndoc"}, {"role": "user", "content": "come vedo lo spazio su disco?" + BLOCK}]
out = "".join(p.pipe("x", "orchestra", q, {"user": {"id": "u2", "role": "admin"}}))
check("routing sul testo pulito", routed.get("text") == "come vedo lo spazio su disco?", repr(routed.get("text")))
check("intestazione con confidenza (il perche della scelta)", "◼◼◼◻◻ 61%" in out, out[:200])
sysmsg = sent["m"][0]["content"]; usr = sent["m"][-1]["content"]
check("al modello: memoria nel sistema, utente pulito", MEM in sysmsg and usr == "come vedo lo spazio su disco?", usr)
check("al modello: RAG ancora nel sistema", M.RAG_MARKER in sysmsg)

p.route_text = lambda t: ("coordinator", "🧠", "COORDINATORE", 0.0)
out = "".join(p.pipe("x", "orchestra", q, {"user": {"id": "u3", "role": "admin"}}))
check("senza confidenza: intestazione dice il motivo", "nessuna corrispondenza di routing" in out, out[:200])
summary("MANIFOLD")
