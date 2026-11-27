"""Test di image_loop (EGPU-05, scritto per v2.8.0, valido per v2.8.2) con ComfyUI e Ollama simulati."""
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
print("== anti-contaminazione: l'analisi visiva non deve far perdere il soggetto dell'utente")
# BUG REALE (15 ottobre 2026): /generate "un cubo rosso su fondo bianco" ha prodotto una ROSA.
# Il prompt di analisi conteneva un esempio concreto (found "red rose, green leaves", next_prompt "a close-up of
# a red rose with dew drops ..."). llava:7b lo ha copiato parola per parola e il ciclo ha usato quel next_prompt
# come prompt finale, perdendo il soggetto. Questi controlli devono FALLIRE col codice precedente.
with UP():
    p = new(); post = mock.Mock(return_value=Resp({"response": '{"score":6,"found":"a","missing":"b","issues":"c","next_prompt":"x y z"}'}))
    with mock.patch.object(IL.requests, "post", post): p.analyze_image_vision(b"img", "un cubo rosso su fondo bianco", model="llava:7b")
    (url,), kw = post.call_args; sent = kw["json"]["prompt"]
check("prompt di analisi: nessun contenuto dell'esempio vecchio", not any(w in sent for w in ["red rose", "dew drops", "green leaves", "overexposed petals"]), sent[:160])
check("prompt di analisi: contiene il prompt corrente", "un cubo rosso su fondo bianco" in sent)
check("prompt di analisi: chiede di mantenere lo stesso soggetto", "SAME main subject" in sent)
p = new(); orig = "un cubo rosso su fondo bianco"
parse = lambda raw: p._parse_vision_analysis(raw, orig)
J = lambda nxt: '{"score":6,"found":"a","missing":"b","issues":"c","next_prompt":"' + nxt + '"}'
check("next_prompt vuoto: resta il prompt corrente", parse(J(""))["next_prompt"] == orig)
check("next_prompt segnaposto <...>: resta il prompt corrente", parse(J("<improved SDXL prompt>"))["next_prompt"] == orig)
check("next_prompt troppo corto: resta il prompt corrente", parse(J("red"))["next_prompt"] == orig)
good = "a red cube on a plain white background, studio lighting, sharp focus"
check("next_prompt valido: conservato", parse(J(good))["next_prompt"] == good)
check("JSON rotto con segnaposto: resta il prompt corrente", parse('{"score":6, "next_prompt":"<improved SDXL prompt>" oops')["next_prompt"] == orig)
check("JSON rotto con prompt valido: conservato", parse('{"score":6, "next_prompt":"' + good + '" oops')["next_prompt"] == good)

print("== B27 vision: schema JSON, risposte non oggetto, moondream solo descrizione")
with UP():
    p = new(); post = mock.Mock(return_value=Resp({"response": '{"score":7,"found":"a","missing":"b","issues":"","next_prompt":"x y z"}'}))
    with mock.patch.object(IL.requests, "post", post): p.analyze_image_vision(b"img", "a cat", model="llava:7b")
    (url,), kw = post.call_args
check("llava: schema JSON passato a Ollama (format)", kw["json"].get("format") == IL.ANALYSIS_SCHEMA and "score" in IL.ANALYSIS_SCHEMA["required"])
p = new()
check("JSON lista (moondream): nessun errore, ripiego", parse('[{"score": 9}, {"score": 8}]')["issues"] == "Risposta non in formato JSON")
check("JSON stringa: nessun errore", parse('"testo"')["score"] == 5)
check("voto come testo \"8/10\" letto", parse('{"score":"8/10","found":"","missing":"","issues":"","next_prompt":""}')["score"] == 8)
with UP():
    p = new()
    answers = [Resp({"response": "A round flying saucer over a desert, a big planet behind."}),
               Resp({"response": '{"score":3,"found":"planet","missing":"galleon, rings","issues":"","next_prompt":"a wooden sailing galleon spaceship flying over the rings of saturn, 8k"}'})]
    post = mock.Mock(side_effect=answers)
    with mock.patch.object(IL.requests, "post", post): out = p.analyze_image_vision(b"img", "a starship like an ancient galleon over saturn rings", model="moondream:v2")
    pls = [c.kwargs["json"] for c in post.call_args_list]
check("moondream: prima descrive (con immagine, senza schema)", len(pls) == 2 and pls[0]["model"] == "moondream:v2" and pls[0].get("images") and "format" not in pls[0], str([x.get("model") for x in pls]))
check("moondream: il voto lo da judge_model con lo schema", pls[1]["model"] == "llama3.2:3b" and pls[1].get("format") == IL.ANALYSIS_SCHEMA and "flying saucer" in pls[1]["prompt"])
check("moondream: voto e mancanze dal giudice", out["score"] == 3 and "galleon" in out["missing"] and out["issues"] != "Risposta non in formato JSON", str(out))

print("== B28 raffinamento: VRAM vera, aux, nuovo tentativo su CPU")
with UP():
    check("main pieno, aux libero: refine sull'aux", new(free={"main": 1000, "aux": 5000})._select_refine_params(1000) == ("llama3.2:3b", None))
    m, g = new(free={"main": 1000, "aux": 1000})._select_refine_params(1000)
    check("main e aux pieni: layer secondo la VRAM (1000 MB → 2, non 12)", (m, g) == ("qwen2.5-coder:14b-instruct-q4_K_M", 2), str((m, g)))
    check("VRAM sotto 500 MB: tutto su CPU", new(free={"main": 300, "aux": 300})._select_refine_params(300)[1] == 0)
    check("VRAM abbondante: modello principale", new()._select_refine_params(23848) == ("qwen3.5:9b", None))
    p = new(); post = mock.Mock(side_effect=[Resp(status=500), Resp({"response": "a wooden galleon spaceship over saturn rings, 8k"})])
    an = {"score": 3, "found": "", "missing": "galleon", "issues": "", "next_prompt": "n"}
    with mock.patch.object(IL.requests, "post", post): r = p.refine_prompt("a starship", an, model="qwen2.5-coder:14b-instruct-q4_K_M", num_gpu=12)
    opts = [c.kwargs["json"]["options"].get("num_gpu") for c in post.call_args_list]
check("errore 500 sulla GPU: secondo tentativo su CPU", opts == [12, 0] and r.startswith("a wooden galleon"), str(opts))
class Bad(Resp):
    def raise_for_status(s): raise IL.requests.HTTPError("500 Server Error")
with UP():
    p = new(); post = mock.Mock(return_value=Bad(status=500))
    with mock.patch.object(IL.requests, "post", post): r = p.refine_prompt("a starship", an, model="qwen3.5:9b", num_gpu=0)
check("errore anche su CPU: motivo salvato per la chat", r == "n" and "500" in p.last_refine_error, p.last_refine_error)

print("== B30 vision: modello gia' caricato usato anche con poca VRAM libera")
ps_llava = Resp({"models": [{"name": "llava:7b", "model": "llava:7b"}]})
with mock.patch.object(IL.requests, "get", return_value=ps_llava):
    p = new(); check("llava gia' in memoria, VRAM 1046: resta llava", p._select_vision_params(1046) == ("llava:7b", None))
with UP():
    p = new(); check("llava non in memoria, VRAM 1046: moondream", p._select_vision_params(1046)[0] == "moondream:v2")
    check("llava non in memoria, VRAM 6000: llava", p._select_vision_params(6000) == ("llava:7b", None))

print("== B29 raffinamento: schema JSON, rifiuti e preamboli scartati")
with UP():
    p = new(); post = mock.Mock(return_value=Resp({"response": '{"prompt": "a wooden sailing galleon spaceship flying over the rings of saturn, 8k, highly detailed"}'}))
    with mock.patch.object(IL.requests, "post", post): r = p.refine_prompt("a starship", an, model="llama3.2:3b")
    pl = post.call_args.kwargs["json"]
check("refine: schema {prompt} passato a Ollama", pl.get("format") == IL.REFINE_SCHEMA)
check("refine: prompt letto dal JSON", r.startswith("a wooden sailing galleon") and "{" not in r, r)
real = "I can't help you with that. I can, however, provide you with a suggested improved SDXL prompt:\n\nstarship galleon"
for txt in (real, '{"prompt": "I am sorry, but I cannot create that image of a starship"}', "Here is the improved prompt: a starship over saturn"):
    with UP():
        p = new(); post = mock.Mock(return_value=Resp({"response": txt}))
        with mock.patch.object(IL.requests, "post", post): r = p.refine_prompt("a starship", an, model="llama3.2:3b")
    check(f"rifiuto/preambolo scartato: {txt[:30]!r}", r == "n" and "rifiuto" in p.last_refine_error, r)

print("== B28 ciclo: con voto non migliorato si raffina il miglior prompt")
p = new(); scores = iter([5, 5, 5]); refined = iter(["prompt raffinato numero uno per il draft due", "prompt raffinato numero due per il draft tre"])
p.generate_image = mock.Mock(return_value=(b"png" * 100, 1, {"filename": "f.png", "subfolder": "", "type": "output"}))
p.analyze_image_vision = mock.Mock(side_effect=lambda **k: {"score": next(scores), "found": "", "missing": "galleon", "issues": "", "next_prompt": "x"})
p.refine_prompt = mock.Mock(side_effect=lambda **k: next(refined)); p.free_comfyui_vram = mock.Mock(); p._warm_up = mock.Mock()
with mock.patch.object(IL.time, "sleep"), mock.patch.object(IL.requests, "post", mock.Mock(return_value=Resp())), UP():
    out = "".join(p.pipe("a starship like an ancient galleon", "m", [], {}))
prompts = [c.kwargs["prompt"] for c in p.generate_image.call_args_list]
check("voto fermo: 2 raffinamenti (dopo il draft 1 e 2), nessuno dopo l'ultimo", p.refine_prompt.call_count == 2, str(p.refine_prompt.call_count))
check("voto fermo: il terzo draft usa un prompt nuovo (prima ripeteva il migliore)", prompts[2] == "prompt raffinato numero due per il draft tre", str(prompts))
check("voto fermo: il raffinamento parte dal miglior prompt", p.refine_prompt.call_args_list[1].kwargs["original_prompt"] == "a starship like an ancient galleon")
check("ultima iterazione: non annuncia un raffinamento che non fa", out.count("Raffino il miglior prompt") == 1, str(out.count("Raffino il miglior prompt")))
print("== B31 deriva: si giudica e si raffina sempre rispetto alla richiesta dell'utente")
check("vision giudica sempre la richiesta originale", all(c.kwargs["prompt"] == "a starship like an ancient galleon" for c in p.analyze_image_vision.call_args_list), str([c.kwargs["prompt"] for c in p.analyze_image_vision.call_args_list]))
check("raffinamento riceve la richiesta originale", all(c.kwargs.get("user_request") == "a starship like an ancient galleon" for c in p.refine_prompt.call_args_list))
with UP():
    q = new(); post = mock.Mock(return_value=Resp({"response": '{"prompt": "a digital art piece of an ancient galleon starship over saturn rings, 8k"}'}))
    with mock.patch.object(IL.requests, "post", post): q.refine_prompt("a digital art piece of a starship", an, model="qwen3.5:9b", user_request="a starship like an ancient galleon over saturn ring")
    txt = post.call_args.kwargs["json"]["prompt"]
check("testo del raffinamento: richiesta dell'utente obbligatoria", "User request (every element MUST stay in the prompt): a starship like an ancient galleon over saturn ring" in txt)
p = new(); p.generate_image = mock.Mock(return_value=(b"png" * 100, 1, {"filename": "f.png", "subfolder": "", "type": "output"}))
p.analyze_image_vision = mock.Mock(return_value={"score": 4, "found": "", "missing": "", "issues": "", "next_prompt": "x"})
p.refine_prompt = mock.Mock(return_value="a starship like an ancient galleon"); p.free_comfyui_vram = mock.Mock(); p._warm_up = mock.Mock()
with mock.patch.object(IL.time, "sleep"), mock.patch.object(IL.requests, "post", mock.Mock(return_value=Resp())), UP():
    out = "".join(p.pipe("a starship like an ancient galleon", "m", [], {}))
check("prompt invariato: detto in chiaro, niente finto \"Nuovo prompt\"", "Prompt invariato" in out and "Nuovo prompt" not in out)

print("== B32 schede e VRAM in chat, fabbisogno reale dei modelli, verifica GPU/CPU")
SNAP = {"main": {"name": "NVIDIA GeForce RTX 3090", "free_mb": 22630, "total_mb": 24576},
        "aux":  {"name": "NVIDIA GeForce RTX 4060 Laptop GPU", "free_mb": 7600, "total_mb": 8188}}
with mock.patch.object(IL, "_daemon_gpu_snapshot", lambda: SNAP), UP():
    p = new(); line = p.gpu_status_line()
    check("riga GPU: nomi delle schede", "RTX 3090" in line and "RTX 4060 Laptop GPU" in line and "NVIDIA" not in line, line)
    check("riga GPU: VRAM libera/totale in GB", "22.1/24.0 GB liberi" in line and "7.4/8.0 GB liberi" in line, line)
    check("riga GPU: dice dove gira ComfyUI", "ComfyUI su main" in line)
    check("nome per ruolo", p.gpu_name("aux") == "RTX 4060 Laptop GPU")
with mock.patch.object(IL, "_daemon_gpu_snapshot", lambda: {}), mock.patch.object(IL.Pipeline, "_nvidia_smi_info", staticmethod(lambda g: None)), UP():
    check("senza dati: lo dice, nessun errore", "non disponibili" in new().gpu_status_line())
TAGS = Resp({"models": [{"name": "llava:7b", "size": 4514 * 2**20}, {"name": "qwen3.5:9b", "size": 6288 * 2**20},
                        {"name": "moondream:v2", "size": 1657 * 2**20}, {"name": "llama3.2:3b", "size": 1925 * 2**20}]})
def get_tags(url, **k): return TAGS if url.endswith("/api/tags") else Resp({"models": []})
with mock.patch.object(IL.requests, "get", side_effect=get_tags):
    p = new()
    check("fabbisogno llava dalla dimensione reale (~5,0 GB)", p._model_need_mb("llava:7b", 5000) == int(4514 * 1.05) + 300)
    m, g = p._select_vision_params(5000)
    check("llava con 5000 MB liberi su ~5040 necessari: non 'pieno', solo parte in GPU", m == "llava:7b" and g is not None and g > 0, str((m, g)))
    check("il motivo della scelta e' registrato", "servono" in p.last_choice and "liberi" in p.last_choice, p.last_choice)
    check("qwen3.5:9b: 6000 MB non bastano (servono ~6,9 GB) → si cerca un'alternativa", p._select_refine_params(6000)[0] != "qwen3.5:9b")
    p = new(); check("qwen3.5:9b con 16 GB liberi: in GPU", p._select_refine_params(16000) == ("qwen3.5:9b", None))
PS = Resp({"models": [{"name": "llama3.2:3b", "size": 2276 * 2**20, "size_vram": 1598 * 2**20}, {"name": "llava:7b", "size": 4253 * 2**20, "size_vram": 4253 * 2**20}]})
with mock.patch.object(IL.requests, "get", return_value=PS):
    p = new()
    check("verifica: llava 100% in GPU", "100% GPU" in p._placement_line("llava:7b"))
    pl = p._placement_line("llama3.2:3b")
    check("verifica: llama3.2:3b in parte su CPU (misura reale 70%)", "70% GPU" in pl and "30% su CPU" in pl and "⚠️" in pl, pl)
    check("modello non in memoria: nessuna riga", p._placement_line("qwen3.5:9b") == "")
with mock.patch.object(IL, "_daemon_gpu_snapshot", lambda: SNAP):
    p = new(); out = flow(p)
check("flusso: schede all'avvio", "Schede all'avvio" in out and "RTX 3090" in out)
check("flusso: riga GPU a ogni iterazione e prima del render", out.count("🖥️ **GPU**") == 3 and "🖥️ **Render finale** — main **RTX 3090**" in out, str(out.count("🖥️ **GPU**")))
p = new()
raw_nl = '{"score": 7, "found": "ship", "missing": "", "issues": "riga uno\nriga due", "next_prompt": "an ancient galleon starship over saturn rings"}'
check("B33: JSON con a capo dentro una stringa letto (prima: non JSON)", p._parse_vision_analysis(raw_nl, "x")["score"] == 7 and p._parse_vision_analysis(raw_nl, "x")["issues"].startswith("riga uno"))
check("flusso: vision e raffinamento dicono su quale scheda", "Vision: `" in out and "su **RTX 4060 Laptop GPU** (aux)" in out and "Raffinamento: `" in out)

summary("IMAGE_LOOP")
