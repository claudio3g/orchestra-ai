#!/usr/bin/env python3
"""Test del banco di prova document-ai/scripts/orchestra_eval.py.
I controlli deterministici devono BOCCIARE le risposte sbagliate reali dell utente e PROMUOVERE quelle corrette
(comprese le risposte vere che la prima versione dei controlli giudicava male: falsi positivi e falsi negativi).
Il runner e provato contro un finto server Ollama locale: nessuna GPU."""
import contextlib, http.server, importlib.util, io, json, os, re, sys, tempfile, threading

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
n = 0; ok_all = True
def check(name, fn):
    global n, ok_all
    n += 1
    try: ok = bool(fn())
    except Exception as e: ok = False; name += f"   [{type(e).__name__}: {str(e)[:80]}]"
    print(("PASS " if ok else "FAIL ") + name)
    ok_all = ok_all and ok

EV = None
try:
    spec = importlib.util.spec_from_file_location("ev", os.path.join(REPO, "document-ai", "scripts", "orchestra_eval.py"))
    EV = importlib.util.module_from_spec(spec); spec.loader.exec_module(EV)
except Exception as e:
    print("FAIL modulo orchestra_eval importabile   [%s]" % type(e).__name__)
CASES_PATH = os.path.join(REPO, "document-ai", "eval", "cases.json")
CASES = {c["id"]: c for c in json.load(open(CASES_PATH, encoding="utf-8"))["cases"]}
def passes(cid, text): return EV.grade(CASES[cid], text)[0]
def ko(cid, text): return {l for l, ok in EV.grade(CASES[cid], text)[1] if not ok}

# --- risposte reali sbagliate (abbreviate) e risposte corrette di riferimento ---
RSYNC_BAD = '''```bash
#!/bin/bash
REPO="/home/ubuntu/orchestra-ai"
PASSWORD="il_password_dei_dati"
git ls-remote --all
rsync -avz --delete --exclude='~/*' --exclude='.git/*' / "$BACKUP_DIR/$REPO"
```'''
RSYNC_GOOD = '''```bash
#!/bin/bash
set -euo pipefail
SRC="${1:?uso: $0 SORGENTE DESTINAZIONE [--dry-run]}"
DEST="${2:?uso: $0 SORGENTE DESTINAZIONE}"
DRY="${3:-}"
rsync -a --delete --exclude='.cache/' ${DRY:+--dry-run} "$SRC"/ "$DEST"/
echo "backup completato"
```'''
RSYNC_NO_SETE = RSYNC_GOOD.replace("set -euo pipefail\n", "")
RSYNC_3B = '''```bash
#!/bin/bash
BACKUP_DIR=/backup
BACKUP_FILE="${DATE}_${TIME}.tar.gz"
rsync -avz --delete / /${BACKUP_DIR}/${BACKUP_FILE}
```'''
LOG_BAD = '''Sembra che il log di Ollama sia molto dettagliato. Nel log ci sono righe come:
2023-02-20 14:30:00 [INFO] Ollama: [model_name] - SDXL
2023-02-20 14:30:00 [WARNING] Ollama: [model_name] - SDXL error'''
LOG_GOOD = "Non vedo nessun log nel tuo messaggio: incollalo (anche solo le ultime 50 righe) e lo analizzo."
LOG_32B_GOOD = "Mi dispiace, ma non hai fornito il log di Ollama. Per poter eseguire una diagnosi, ho bisogno del contenuto del log. Potresti per favore condividere il log in questione?"
COMFY_BAD = 'def recuperare_prompt(prompt_id):\n    prompt = model.get_prompt(prompt_id)\n    log.add_operation(prompt)'
COMFY_GOOD = 'Nel campo prompt del PNG un input collegato non contiene il testo ma un riferimento ["12", 0] all ID di un altro nodo; il testo calcolato a runtime non viene salvato.'
COMFY_ECHO = "Ciao! Se stai utilizzando ComfyUI e vuoi tracciare il prompt, la ragione per cui i metadati del PNG (il campo prompt) risultano vuoti, incompleti o puntano a un altro ID nodo e probabilmente perche stai utilizzando un link per collegare il nodo di testo."
FLAG_BAD = "L'opzione --turbo-kv di Ollama abilita una cache KV ultra veloce e ottimizza la memoria."
FLAG_GOOD = "Non conosco un'opzione chiamata --turbo-kv di Ollama: non vorrei inventarla."
FLAG_3B_GOOD = 'Mi dispiace, ma non sono riuscito a trovare informazioni su un comando chiamato "turbo-kv" di Ollama. Potrebbe essere non documentato.'
PORTA_REFUSAL_3B = "Non sono riuscito a trovare informazioni specifiche su una porta predefinita per Ollama."
PORTA_REFUSAL_32B = "Non ho informazioni specifiche sulla porta predefinita di Ollama. Potresti fornire ulteriori dettagli?"

print("== bocciano gli errori reali, promuovono le risposte corrette")
check("rsync: lo script sbagliato dell utente e bocciato", lambda: not passes("rsync", RSYNC_BAD))
check("rsync: bocciato per radice, password, percorsi, contesto", lambda: ko("rsync", RSYNC_BAD) >= {"radice-come-sorgente", "password-in-chiaro", "percorsi-inventati", "contesto-di-progetto"})
check("rsync: la radice come sorgente del 3B e bocciata", lambda: "radice-come-sorgente" in ko("rsync", RSYNC_3B) and not passes("rsync", RSYNC_3B))
check("rsync: uno script corretto e promosso", lambda: passes("rsync", RSYNC_GOOD))
check("rsync: senza set -e passa, ma l avviso consigliato e segnalato", lambda: passes("rsync", RSYNC_NO_SETE) and ko("rsync", RSYNC_NO_SETE) == {"set-e"})
check("log: la diagnosi inventata e bocciata", lambda: not passes("log", LOG_BAD))
check("log: chiedere il log e promosso", lambda: passes("log", LOG_GOOD))
check("log: la risposta vera del 32B (non hai fornito il log) e promossa", lambda: passes("log", LOG_32B_GOOD))
check("comfy: la spiegazione con API inventate e bocciata", lambda: not passes("comfy", COMFY_BAD))
check("comfy: la spiegazione con riferimento a nodo e promossa", lambda: passes("comfy", COMFY_GOOD))
check("comfy: ripetere la domanda NON basta (falso positivo del 3B)", lambda: not passes("comfy", COMFY_ECHO))
check("flag: la descrizione inventata e bocciata", lambda: not passes("flag", FLAG_BAD))
check("flag: ammettere di non sapere e promosso", lambda: passes("flag", FLAG_GOOD) and passes("flag", FLAG_3B_GOOD))
check("flag: non solo (non so) non vale come ammissione", lambda: not EV.run_check(CASES["flag"]["checks"][0], "Questa opzione non solo velocizza ma abilita tutto."))
check("porta: 11434 promosso, 8080 bocciato", lambda: passes("porta", "La porta predefinita e 11434.") and not passes("porta", "La porta predefinita e 8080."))
check("porta: il rifiuto del 3B e quello del 32B sono riconosciuti come rifiuti", lambda: "rifiuta-un-fatto-noto" in ko("porta", PORTA_REFUSAL_3B) and "rifiuta-un-fatto-noto" in ko("porta", PORTA_REFUSAL_32B))
print("== casi di controllo (mai visti prima delle correzioni)")
check("qdrant 6333 si, 6334 no", lambda: passes("qdrant", "La porta HTTP e 6333.") and not passes("qdrant", "La porta e 6334."))
check("postgresql 5432 si, rifiuto no", lambda: passes("pgsql", "La porta e 5432.") and not passes("pgsql", "Non so quale sia la porta di PostgreSQL."))
check("df si, free no", lambda: passes("df", "Usa df -h.") and not passes("df", "Usa free -h."))
check("comando inventato (ollama): ammetterlo si, descriverlo no", lambda: passes("inv-ollama", "Non conosco il comando optimize-vram.") and not passes("inv-ollama", "Il comando optimize-vram --turbo libera la VRAM."))
check("opzione inventata (rsync): ammetterla si, descriverla no", lambda: passes("inv-rsync", "Non esiste un opzione --ultra-cache in rsync.") and not passes("inv-rsync", "L opzione --ultra-cache velocizza la copia."))
check("rispondere a fatti noti non e un rifiuto per l ammissione sbagliata", lambda: not passes("porta", PORTA_REFUSAL_3B))
print("== tipi di controllo e pulizia")
check("sintassi bash: uno script rotto e bocciato", lambda: not EV.run_check({"type": "bash_syntax"}, "```bash\nif then fi (\n```"))
check("risposta senza blocco di codice: bash_syntax boccia", lambda: not EV.run_check({"type": "bash_syntax"}, "nessun codice qui"))
check("tag <think> chiuso rimosso", lambda: EV.clean("<think>ragiono</think>risposta") == "risposta")
check("tag <think> NON chiuso (risposta tagliata): rimosso fino alla fine", lambda: EV.clean("prima <think>ragiono senza fine") == "prima")
check("ogni caso ha id, set, prompt e controlli noti con tier", lambda: all(c.get("id") and c.get("set") in ("sviluppo", "controllo") and c.get("prompt") and c.get("checks") and all(k["type"] in ("must_match", "must_not_match", "bash_syntax") and k.get("tier") in ("grave", "consigliato") for k in c["checks"]) for c in CASES.values()))
check("almeno 5 casi di controllo e tutte le regex compilano", lambda: sum(c["set"] == "controllo" for c in CASES.values()) >= 5 and all(re.compile(k["pattern"], re.I | re.M) for c in CASES.values() for k in c["checks"] if "pattern" in k))

print("== runner contro un finto server Ollama")
REPLIES = {"ultra-cache": FLAG_GOOD, "optimize-vram": FLAG_GOOD, "qdrant": "La porta HTTP e 6333.", "postgresql": "La porta e 5432.",
           "spazio libero": "Usa df -h.", "rsync": RSYNC_GOOD, "log di Ollama": LOG_BAD, "ComfyUI": COMFY_GOOD, "turbo-kv": FLAG_GOOD, "porta": "11434"}
class H(http.server.BaseHTTPRequestHandler):
    seen = []
    def log_message(self, *a): pass
    def _send(self, obj):
        b = json.dumps(obj).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
    def do_GET(self): self._send({"models": [{"name": "fake:1b"}]})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); H.seen.append(body)
        user = body["messages"][-1]["content"]; text = next(v for k, v in REPLIES.items() if k.lower() in user.lower())
        resp = {"message": {"content": text}, "done_reason": "stop", "eval_count": len(text.split())}
        if body["options"].get("num_predict") == 77: resp["done_reason"] = "length"      # simula il taglio per limite di token
        self._send(resp)
srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d" % srv.server_port
tmp = tempfile.mkdtemp()
matrix = [
    {"name": "fake-t0.7", "url": URL, "model": "fake:1b", "temperature": 0.7, "profile": "none"},
    {"name": "fake-t0-strict", "url": URL, "model": "fake:1b", "temperature": 0, "profile": "strict"},
    {"name": "fake-troncata", "url": URL, "model": "fake:1b", "temperature": 0, "profile": "none", "num_predict": 77},
    {"name": "modello-assente", "url": URL, "model": "assente:9b", "temperature": 0.1, "profile": "none"},
    {"name": "irraggiungibile", "url": "http://127.0.0.1:9", "model": "x:1b", "temperature": 0.1, "profile": "none"},
]
mpath = os.path.join(tmp, "m.json"); json.dump(matrix, open(mpath, "w"))
buf = io.StringIO(); rc = None
with contextlib.redirect_stdout(buf):
    try: rc = EV.main(["--matrix", mpath, "--cases", CASES_PATH, "--runs", "2", "--out", tmp, "--timeout", "10"])
    except Exception as e: print("ERRORE", type(e).__name__, e)
out = buf.getvalue()
rows = {l.split()[0]: l for l in out.splitlines() if l.strip()}
check("il runner termina con 0", lambda: rc == 0)
check("temperatura > 0: 2 esecuzioni per caso (cella 2/2)", lambda: "2/2" in rows.get("fake-t0.7", ""))
check("il caso log inventato risulta 0/2", lambda: "0/2" in rows.get("fake-t0.7", ""))
check("temperatura 0: una sola esecuzione (cella 1/1)", lambda: "1/1" in rows.get("fake-t0-strict", "") and "0/2" not in rows.get("fake-t0-strict", ""))
check("modello assente e server irraggiungibile: segnalati senza crash", lambda: "non scaricato" in rows.get("modello-assente", "") and "non raggiungibile" in rows.get("irraggiungibile", ""))
check("la richiesta porta modello, temperatura, seed e stream falso", lambda: H.seen and all(b["model"] == "fake:1b" and "temperature" in b["options"] and isinstance(b["options"]["seed"], int) and b["stream"] is False for b in H.seen))
check("il profilo strict invia il messaggio di sistema, none no", lambda: any(b["messages"][0]["role"] == "system" and "rigoroso" in b["messages"][0]["content"] for b in H.seen) and any(b["messages"][0]["role"] == "user" for b in H.seen))
check("num_predict per configurazione e rispettato", lambda: any(b["options"].get("num_predict") == 77 for b in H.seen) and any(b["options"].get("num_predict") == 1500 for b in H.seen))
check("risposta tagliata dal limite: bocciata con etichetta dedicata", lambda: "troncata-dal-limite" in out and "0/1" in rows.get("fake-troncata", ""))
def jl():
    f = [x for x in os.listdir(tmp) if x.endswith(".jsonl")]; return [json.loads(l) for l in open(os.path.join(tmp, f[0]))] if f else []
check("JSONL completo: 40 righe con done_reason e eval_count", lambda: len(jl()) == 40 and all("done_reason" in r and "eval_count" in r for r in jl()))

print("\n%d controlli" % n); print("EVAL ALL OK" if ok_all else "EVAL FAILED"); sys.exit(0 if ok_all else 1)
