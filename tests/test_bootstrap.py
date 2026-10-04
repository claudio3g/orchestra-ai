"""Test del filtro orchestra_bootstrap v1.1.0 con i file reali del repository."""
import asyncio, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; REPO = HERE.parent
sys.path.insert(0, str(HERE / "helpers")); sys.path.insert(0, str(REPO / "ollama" / "pipelines"))
from tiny import check, summary
import orchestra_bootstrap as B

def mk(**kw):
    p = B.Pipeline()
    p.valves.bootstrap_path = str(REPO / "AI_BOOTSTRAP.md")
    p.valves.manifest_path = str(REPO / "AI_MANIFEST.md")
    p.valves.read_protocol_path = str(REPO / "AI_READ_PROTOCOL.md")
    for k, v in kw.items(): setattr(p.valves, k, v)
    return p
run = lambda p, body: asyncio.run(p.inlet(body, {}))
msgs = lambda: {"messages": [{"role": "user", "content": "ciao"}]}

print("== classificazione del framework Pipelines (main.py: type == 'filter', valves.pipelines/priority)")
p = mk()
check("type == 'filter'", p.type == "filter")
check("valves.pipelines = ['*'] e priority presente", p.valves.pipelines == ["*"] and hasattr(p.valves, "priority"))
check("id e name definiti", p.id == "orchestra_bootstrap" and p.name)
check("nessun pipes() (non e' API di Pipelines)", not hasattr(p, "pipes"))

print("== contesto compatto")
ctx = p._load_context()
check(f"dimensione <= 3500+margine ({len(ctx)} car.)", len(ctx) <= 3500 + 30)
check("contiene la policy anti-hallucination", "Anti-hallucination" in ctx or "anti-hallucination" in ctx.lower())
check("contiene il protocollo di lettura", "AI_READ_PROTOCOL" in ctx)
check("NON contiene la sezione elenco file (## 2. File tracciati)", "## 2. File tracciati" not in ctx)
check("NON contiene il manifest", "AI_MANIFEST.md ===" not in ctx)
full = mk(include_file_list=True, include_manifest=True, max_bytes=20000)._load_context()
check(f"con le opzioni: file list + manifest inclusi ({len(full)} car.)", "## 2. File tracciati" in full and "AI_MANIFEST.md ===" in full)
check("la versione compatta e' molto piu' piccola", len(ctx) < len(full) / 2, f"{len(ctx)} vs {len(full)}")
old_total = sum(len((REPO / f).read_text(encoding="utf-8")) for f in ("AI_BOOTSTRAP.md", "AI_MANIFEST.md", "AI_READ_PROTOCOL.md"))
print(f"   (v1.0.0 iniettava ~{old_total} caratteri; v1.1.0 compatta {len(ctx)}: -{100 - 100 * len(ctx) // old_total}%)")

print("== iniezione")
b = run(mk(), msgs())
check("system message inserito in testa", b["messages"][0]["role"] == "system" and B.MARKER in b["messages"][0]["content"])
check("messaggio utente intatto", b["messages"][1] == {"role": "user", "content": "ciao"})
b2 = run(mk(), {"messages": [{"role": "system", "content": "Sei un assistente."}, {"role": "user", "content": "x"}]})
check("system esistente arricchito (non saltato)", b2["messages"][0]["content"].startswith("Sei un assistente.") and B.MARKER in b2["messages"][0]["content"] and len(b2["messages"]) == 2)
b3 = run(mk(), b2)
check("idempotente: nessuna doppia iniezione", b3["messages"][0]["content"].count(B.MARKER) == 1)
check("disabilitato: body invariato", run(mk(enabled=False), msgs()) == msgs())
check("file mancanti: body invariato", run(mk(bootstrap_path="/nope", read_protocol_path="/nope"), msgs()) == msgs())

print("== cache")
p = mk(); a = p._load_context(); k = p._cache_key
check("seconda lettura da cache", p._load_context() is a)
p.valves.include_file_list = True
check("cache invalidata se cambiano i valve", p._load_context() != a and p._cache_key != k)
summary("BOOTSTRAP")
