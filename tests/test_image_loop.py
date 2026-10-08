"""Test di image_loop v2.8.0 (EGPU-05) con ComfyUI e Ollama simulati."""
import json, os, sys
from pathlib import Path
from unittest import mock
HERE = Path(__file__).resolve().parent; REPO = HERE.parent
sys.path.insert(0, str(HERE / "helpers")); sys.path.insert(0, str(REPO / "ollama" / "pipelines"))
from tiny import check, summary
os.environ.pop("OLLAMA_AUX_URL", None); os.environ.pop("ORCHESTRA_COMFY_ROLE", None)
import image_loop as IL

MAIN, AUX = "http://main:11434", "http://aux:11434"
class Resp:
    def __init__(s, j=None, status=200): s._j = j or {}; s.status_code = status
    def raise_for_status(s): pass
    def json(s): return s._j
def new(aux=True, comfy="main", free=None, total=None):
    p = IL.Pipeline(); p.valves.ollama_url = MAIN
    tot = total or {"main": 24576, "aux": 8188}
    p._gpu_total_mb = lambda role: tot.get(role, 0)
    if aux: p.valves.ollama_url_aux = AUX
    p.valves.comfy_role = comfy; p._aux_checked_until = 0.0
    free = free or {"main": 23848, "aux": 7000}
    p.vram_free_mb = lambda role="main": free[role]
    return p
UP = lambda: mock.patch.object(IL.requests, "get", return_value=Resp(status=200))

print("== ruoli e URL")
with UP():
    p = new()
    check("vision (llava) → aux", p._role_of("llava:7b") == "aux" and p._url_for("llava:7b") == AUX)
    check("preflight (llama3.2:3b) → aux", p._role_of("llama3.2:3b") == "aux")
    check("refine (qwen3.5:9b) → main", p._role_of("qwen3.5:9b") == "main" and p._url_for("qwen3.5:9b") == MAIN)
p = new(aux=False); check("aux spento: tutto su main", p._role_of("llava:7b") == "main")
with mock.patch.object(IL.requests, "get", side_effect=IL.requests.ConnectionError("x")):
    check("aux giu': llava ricade su main", new()._role_of("llava:7b") == "main")

print("== keep_alive (parametro di primo livello)")
with UP():
    check("vision su aux, ComfyUI su main → tenuto (300)", new()._keep_alive_for("llava:7b") == 300)
    check("refine su main = GPU ComfyUI, 24 GB liberi → tenuto (300)", new()._keep_alive_for("qwen3.5:9b") == 300)
    check("refine su main = GPU ComfyUI, VRAM scarsa → 0 (storico)", new(free={"main": 7000, "aux": 7000})._keep_alive_for("qwen3.5:9b") == 0)
check("GPU singola 8 GB (aux spento) → 0 (storico)", new(aux=False, free={"main": 7046, "aux": 0})._keep_alive_for("llava:7b") == 0)
with UP(): check("ComfyUI su aux, refine su main → tenuto", new(comfy="aux")._keep_alive_for("qwen3.5:9b") == 300)

print("== /free di ComfyUI solo se serve")
with UP():
    p = new(); p.free_comfyui_vram = mock.Mock()
    check("vision su aux, ComfyUI su main: nessun /free", p._free_comfy_if_needed("aux", 5000) is False and p.free_comfyui_vram.call_count == 0)
    check("refine su main (=ComfyUI) con 23 GB liberi: nessun /free", p._free_comfy_if_needed("main", 6000) is False and p.free_comfyui_vram.call_count == 0)
    p = new(free={"main": 1000, "aux": 7000}); p.free_comfyui_vram = mock.Mock()
    check("main=ComfyUI con 1 GB libero: /free eseguito", p._free_comfy_if_needed("main", 6000) is True and p.free_comfyui_vram.call_count == 1)
p = new(aux=False, free={"main": 800, "aux": 0}); p.free_comfyui_vram = mock.Mock()
check("GPU singola 8 GB con SDXL caricato: /free eseguito (storico)", p._free_comfy_if_needed("main", 5000) is True and p.free_comfyui_vram.call_count == 1)

print("== pre-caricamento: sicuro con GPU singola")
with UP():
    names = lambda pl: [m for m, _ in pl]
    check("dual-GPU: pre-carica llava (aux) e qwen3.5:9b (main)", names(new()._plan_warmup()) == ["llava:7b", "qwen3.5:9b"])
    check("dual-GPU: URL giusti", [u for _, u in new()._plan_warmup()] == [AUX, MAIN])
    check("aux con poca VRAM (3000): niente llava", names(new(free={"main": 23848, "aux": 3000})._plan_warmup()) == ["qwen3.5:9b"])
    check("main con poca VRAM (9000): niente refine", names(new(free={"main": 9000, "aux": 7000})._plan_warmup()) == ["llava:7b"])
check("GPU singola 8 GB: NESSUN pre-caricamento (non ruba VRAM a SDXL)", new(aux=False, free={"main": 7046, "aux": 0})._plan_warmup() == [])
check("GPU singola 24 GB: pre-carica (c'e' spazio)", names(new(aux=False, free={"main": 23848, "aux": 0})._plan_warmup()) == ["llava:7b", "qwen3.5:9b"])

print("== chiamate Ollama: payload e failover")
with UP():
    p = new(); post = mock.Mock(return_value=Resp({"response": '{"score":6,"found":"a","missing":"b","issues":"c","next_prompt":"x y z"}'}))
    with mock.patch.object(IL.requests, "post", post): out = p.analyze_image_vision(b"img", "a cat", model="llava:7b")
    (url,), kw = post.call_args; pl = kw["json"]
    check("vision → URL aux", url == AUX + "/api/generate", url)
    check("keep_alive a primo livello (non in options)", pl["keep_alive"] == 300 and "keep_alive" not in pl["options"], str(pl))
    check("risposta vision interpretata", out["score"] == 6, str(out))
    check("modello registrato come 'tenuto'", p._kept.get("llava:7b") == AUX)
    post = mock.Mock(return_value=Resp({"response": "a much improved sdxl prompt with detail"}))
    with mock.patch.object(IL.requests, "post", post): r = p.refine_prompt("a cat", {"score": 5, "found": "", "missing": "", "issues": "", "next_prompt": "n"}, model="qwen3.5:9b")
    (url,), kw = post.call_args
    check("refine → URL main, keep_alive primo livello", url == MAIN + "/api/generate" and kw["json"]["keep_alive"] == 300 and "keep_alive" not in kw["json"]["options"])
    check("refine restituisce il prompt raffinato", r.startswith("a much improved"))
    # failover
    p = new()
    def flaky(url, **kw):
        if url.startswith(AUX): raise IL.requests.ConnectionError("down")
        return Resp({"response": '{"score":4,"found":"","missing":"","issues":"","next_prompt":"p p p"}'})
    post = mock.Mock(side_effect=flaky)
    with mock.patch.object(IL.requests, "post", post): out = p.analyze_image_vision(b"i", "x", model="llava:7b")
    check("failover: aux poi main", [c.args[0] for c in post.call_args_list] == [AUX + "/api/generate", MAIN + "/api/generate"])
    check("failover: analisi ottenuta, nessun errore", out["score"] == 4 and "errore" not in out["issues"])
    check("failover: modello 'tenuto' sul main", p._kept.get("llava:7b") == MAIN)

print("== rilascio VRAM a fine loop")
p = new(); p._kept = {"llava:7b": AUX, "qwen3.5:9b": MAIN}; post = mock.Mock()
with mock.patch.object(IL.requests, "post", post): p._release_kept()
sent = sorted((c.args[0], c.kwargs["json"]["model"], c.kwargs["json"]["keep_alive"]) for c in post.call_args_list)
check("scarica entrambi con keep_alive=0", sent == [(AUX + "/api/generate", "llava:7b", 0), (MAIN + "/api/generate", "qwen3.5:9b", 0)], str(sent))
check("registro svuotato", p._kept == {})
p._kept = {"llava:7b": AUX, "qwen3.5:9b": MAIN}; post = mock.Mock()
with mock.patch.object(IL.requests, "post", post): p._release_kept(only_role="main")
check("only_role=main scarica solo il main", [c.kwargs["json"]["model"] for c in post.call_args_list] == ["qwen3.5:9b"] and "llava:7b" in p._kept)

print("== flusso completo pipe()")
def flow(p):
    p.events = []
    def _gen(*a, **k): p.events.append("gen"); return (b"png" * 100, 123, {"filename": "f.png", "subfolder": "", "type": "output"})
    def _free(*a, **k): p.events.append("free")
    p.generate_image = mock.Mock(side_effect=_gen)
    p.analyze_image_vision = mock.Mock(return_value={"score": 5, "found": "a", "missing": "b", "issues": "c", "next_prompt": "better prompt here"})
    p.refine_prompt = mock.Mock(return_value="a refined prompt for the next draft iteration")
    p.free_comfyui_vram = mock.Mock(side_effect=_free); p._release_kept = mock.Mock(wraps=p._release_kept)
    p._warm_up = mock.Mock()
    with mock.patch.object(IL.time, "sleep"), mock.patch.object(IL.requests, "post", mock.Mock(return_value=Resp())), UP():
        out = "".join(p.pipe("a red rose", "m", [], {}))
    return out
p = new(); out = flow(p)
check("dual-GPU: generazione completata", "Generazione completata" in out, out[-200:])
check("dual-GPU: SDXL resta caricato durante le iterazioni (nessun /free prima del render finale)", p.events.index("free") == len(p.events) - 1 if "free" in p.events else False, str(p.events))
check("dual-GPU: ComfyUI svuotato UNA volta, a fine generazione", p.free_comfyui_vram.call_count == 1 and p.events[-1] == "free", str(p.events))
check("dual-GPU: 3 draft + 1 render finale", p.generate_image.call_count == 4, str(p.generate_image.call_count))
check("dual-GPU: pre-caricamento avviato e annunciato", "Pre-caricamento in parallelo" in out)
check("dual-GPU: modelli rilasciati a fine loop", p._release_kept.call_count >= 1)
p = new(aux=False, free={"main": 800, "aux": 0}, total={"main": 8188, "aux": 0}); out = flow(p)
check("GPU singola 8 GB: completato", "Generazione completata" in out)
check("GPU singola 8 GB: nessun /free a fine generazione (storico)", p.events[-1] == "gen", str(p.events))
check("GPU singola 8 GB: /free eseguito (comportamento storico)", p.free_comfyui_vram.call_count >= 1)
check("GPU singola 8 GB: nessun pre-caricamento", "Pre-caricamento" not in out)
print("== rilascio di ComfyUI a fine generazione")
p = new(); p.free_comfyui_vram = mock.Mock()
check("GPU da 24 GB: libera", p._free_comfy_on_finish() is True and p.free_comfyui_vram.call_count == 1)
p = new(total={"main": 8188, "aux": 0}); p.free_comfyui_vram = mock.Mock()
check("GPU da 8 GB: non libera (storico)", p._free_comfy_on_finish() is False and p.free_comfyui_vram.call_count == 0)
p = new(); p.valves.comfy_free_on_finish = False; p.free_comfyui_vram = mock.Mock()
check("valve disattivato: non libera", p._free_comfy_on_finish() is False and p.free_comfyui_vram.call_count == 0)
p = new(total={"main": 0, "aux": 0}); p.free_comfyui_vram = mock.Mock()
check("VRAM totale sconosciuta: non libera (prudenza)", p._free_comfy_on_finish() is False)
p = new(comfy="aux"); p.free_comfyui_vram = mock.Mock()
check("ComfyUI su aux (8 GB): non libera", p._free_comfy_on_finish() is False)
p = new(); p.free_comfyui_vram = mock.Mock(); p._run_fail = True
def boom(): raise RuntimeError("x")
pp = new(); pp.free_comfyui_vram = mock.Mock(); pp.generate_image = mock.Mock(side_effect=RuntimeError("comfy giu"))
with mock.patch.object(IL.time, "sleep"), mock.patch.object(IL.requests, "post", mock.Mock(return_value=Resp())), UP():
    try: "".join(pp.pipe("a rose", "m", [], {}))
    except Exception: pass
check("anche con un errore nel loop la VRAM viene rilasciata", pp.free_comfyui_vram.call_count >= 1)

print("== spazio per SDXL: scarico degli LLM (es. 32b da 20 GB sulla 3090)")
def ps_and_post(models):
    get = mock.Mock(return_value=Resp({"models": [{"name": m} for m in models]})); post = mock.Mock(return_value=Resp())
    return get, post
with UP():
    p = new(); get, post = ps_and_post(["qwen2.5-coder:32b"])
    with mock.patch.object(IL.requests, "get", get), mock.patch.object(IL.requests, "post", post), mock.patch.object(IL.time, "sleep"):
        n = p._evict_llms("main")
    check("evict: legge /api/ps del main", get.call_args_list[-1].args[0] == MAIN + "/api/ps", str(get.call_args_list))
    check("evict: scarica il 32b con keep_alive 0", n == 1 and post.call_args.kwargs["json"] == {"model": "qwen2.5-coder:32b", "keep_alive": 0})
    p = new(); get, post = ps_and_post(["llava:7b"])
    with mock.patch.object(IL.requests, "get", get), mock.patch.object(IL.requests, "post", post), mock.patch.object(IL.time, "sleep"):
        p._evict_llms("aux")
    check("evict aux: usa l'URL dell'aux", post.call_args.args[0] == AUX + "/api/generate")
p = new(); 
with mock.patch.object(IL.requests, "get", side_effect=IL.requests.ConnectionError("x")): check("evict: /api/ps irraggiungibile → 0, nessun crash", p._evict_llms("main") == 0)

def flow2(p, ps_models):
    p.generate_image = mock.Mock(return_value=(b"png" * 100, 1, {"filename": "f.png", "subfolder": "", "type": "output"}))
    p.analyze_image_vision = mock.Mock(return_value={"score": 9, "found": "a", "missing": "b", "issues": "c", "next_prompt": "p p p"})
    p.refine_prompt = mock.Mock(return_value="x" * 30); p.free_comfyui_vram = mock.Mock(); p._warm_up = mock.Mock()
    get = mock.Mock(return_value=Resp({"models": [{"name": m} for m in ps_models]})); post = mock.Mock(return_value=Resp())
    with mock.patch.object(IL.time, "sleep"), mock.patch.object(IL.requests, "post", post), mock.patch.object(IL.requests, "get", get):
        out = "".join(p.pipe("a rose", "m", [], {}))
    return out, post
p = new(free={"main": 4000, "aux": 7000}); out, post = flow2(p, ["qwen2.5-coder:32b"])
check("3090 occupata dal 32b: lo scarica prima del draft", "Liberati 1 modelli LLM" in out, out[:300])
check("3090 occupata: keep_alive 0 inviato al 32b", any(c.kwargs.get("json") == {"model": "qwen2.5-coder:32b", "keep_alive": 0} for c in post.call_args_list))
p = new(); out, post = flow2(p, ["qwen2.5-coder:32b"])
check("3090 libera: nessuno scarico inutile", "Liberati" not in out and not any(c.kwargs.get("json") == {"model": "qwen2.5-coder:32b", "keep_alive": 0} for c in post.call_args_list))
p = new(free={"main": 4000, "aux": 7000}); p.valves.evict_llms_for_comfy = False; out, post = flow2(p, ["qwen2.5-coder:32b"])
check("valve disattivato: nessuno scarico", "Liberati" not in out)
summary("IMAGE_LOOP")
