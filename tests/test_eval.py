#!/usr/bin/env python3
"""Test del banco di prova document-ai/scripts/orchestra_eval.py.
Idea: i controlli deterministici devono BOCCIARE le risposte sbagliate reali dell utente (17 ottobre 2026)
e PROMUOVERE risposte corrette. Il runner e provato contro un finto server Ollama locale: nessuna GPU."""
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
CASES = {c["id"]: c for c in json.load(open(CASES_PATH))["cases"]}
def passes(cid, text): return EV.grade(CASES[cid], text)[0]

# --- risposte reali sbagliate (abbreviate) e risposte corrette di riferimento ---
RSYNC_BAD = '''Ecco lo script:
```bash
#!/bin/bash
REPO="/home/ubuntu/orchestra-ai"
USER="ubuntu"
PASSWORD="il_password_dei_dati"
mkdir -p "$BACKUP_DIR"
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
LOG_BAD = '''Sembra che il log di Ollama sia molto dettagliato. Nel log ci sono righe come:
2023-02-20 14:30:00 [INFO] Ollama: [model_name] - SDXL
2023-02-20 14:30:00 [WARNING] Ollama: [model_name] - SDXL error
Queste linee suggeriscono problemi durante il passaggio SDXL.'''
LOG_GOOD = "Non vedo nessun log nel tuo messaggio: incollalo (anche solo le ultime 50 righe) e lo analizzo."
COMFY_BAD = '''Utilizzare un sistema di identificatori univoci e una cache. Esempio:
def recuperare_prompt(prompt_id):
    prompt = model.get_prompt(prompt_id)
    log.add_operation(prompt)'''
COMFY_GOOD = '''Nel campo prompt del PNG un input collegato non contiene il testo ma un riferimento ["12", 0] all ID di un altro nodo;
il testo calcolato a runtime non viene salvato. Soluzione: tieni il testo in un nodo con il valore scritto, oppure salvalo con un nodo dedicato.'''
FLAG_BAD = "L'opzione --turbo-kv di Ollama abilita una cache KV ultra veloce e ottimizza la memoria."
FLAG_GOOD = "Non conosco un'opzione chiamata --turbo-kv di Ollama: non vorrei inventarla. Controlla la documentazione."

print("== controlli deterministici: bocciano gli errori reali, promuovono le risposte corrette")
check("rsync: lo script sbagliato dell utente e bocciato", lambda: not passes("rsync", RSYNC_BAD))
check("rsync: bocciato per radice, password, percorsi e contesto", lambda: {l for l, ok in EV.grade(CASES["rsync"], RSYNC_BAD)[1] if not ok} >= {"radice-come-sorgente", "password-in-chiaro", "percorsi-inventati", "contesto-di-progetto", "set-e"})
check("rsync: uno script corretto e promosso", lambda: passes("rsync", RSYNC_GOOD))
check("log: la diagnosi inventata e bocciata", lambda: not passes("log", LOG_BAD))
check("log: chiedere il log mancante e promosso", lambda: passes("log", LOG_GOOD))
check("comfy: la spiegazione inventata e bocciata", lambda: not passes("comfy", COMFY_BAD))
check("comfy: la spiegazione con riferimento a nodo e promossa", lambda: passes("comfy", COMFY_GOOD))
check("flag: la descrizione inventata e bocciata", lambda: not passes("flag", FLAG_BAD))
check("flag: ammettere di non sapere e promosso", lambda: passes("flag", FLAG_GOOD))
check("porta: 11434 e promosso", lambda: passes("porta", "La porta predefinita e 11434."))
check("porta: una porta sbagliata e bocciata", lambda: not passes("porta", "La porta predefinita e 8080."))
check("sintassi bash: uno script rotto e bocciato", lambda: not EV.run_check({"type": "bash_syntax"}, "```bash\nif then fi (\n```"))
check("risposta senza blocco di codice: bash_syntax boccia", lambda: not EV.run_check({"type": "bash_syntax"}, "nessun codice qui"))
check("tag <think> rimossi prima dei controlli", lambda: EV.clean("<think>ragiono</think>risposta") == "risposta")

print("== cases.json")
check("ogni caso ha id, prompt e controlli noti", lambda: all(c.get("id") and c.get("prompt") and c.get("checks") and all(k["type"] in ("must_match", "must_not_match", "bash_syntax") for k in c["checks"]) for c in CASES.values()))
check("tutte le regex compilano", lambda: all(re.compile(k["pattern"], re.I | re.M) for c in CASES.values() for k in c["checks"] if "pattern" in k))

print("== runner contro un finto server Ollama")
REPLIES = {"rsync": RSYNC_GOOD, "log di Ollama": LOG_BAD, "ComfyUI": COMFY_GOOD, "turbo-kv": FLAG_GOOD, "porta": "11434"}
class H(http.server.BaseHTTPRequestHandler):
    seen = []
    def log_message(self, *a): pass
    def _send(self, obj):
        b = json.dumps(obj).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
    def do_GET(self): self._send({"models": [{"name": "fake:1b"}]})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); H.seen.append(body)
        user = body["messages"][-1]["content"]
        self._send({"message": {"content": next(v for k, v in REPLIES.items() if k.lower() in user.lower())}})
srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d" % srv.server_port
tmp = tempfile.mkdtemp()
matrix = [
    {"name": "fake-t0.7", "url": URL, "model": "fake:1b", "temperature": 0.7, "profile": "none"},
    {"name": "fake-t0-strict", "url": URL, "model": "fake:1b", "temperature": 0, "profile": "strict"},
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
check("temperatura 0: una sola esecuzione (cella 1/1)", lambda: "1/1" in rows.get("fake-t0-strict", "") and "/2" not in rows.get("fake-t0-strict", ""))
check("modello assente: segnalato, non crasha", lambda: "assente" in rows.get("modello-assente", ""))
check("server irraggiungibile: segnalato, non crasha", lambda: "irraggiungibile" in out and "non raggiungibile" in rows.get("irraggiungibile", "").lower() + out.lower())
check("la richiesta porta modello, temperatura, seed e stream falso", lambda: H.seen and all(b["model"] == "fake:1b" and "temperature" in b["options"] and isinstance(b["options"]["seed"], int) and b["stream"] is False for b in H.seen))
check("il profilo strict invia il messaggio di sistema, none no", lambda: any(b["messages"][0]["role"] == "system" and "rigoroso" in b["messages"][0]["content"] for b in H.seen) and any(b["messages"][0]["role"] == "user" for b in H.seen))
check("le risposte complete sono salvate in un file JSONL", lambda: any(f.endswith(".jsonl") and sum(1 for _ in open(os.path.join(tmp, f))) == 15 for f in os.listdir(tmp)))

print("\n%d controlli" % n); print("EVAL ALL OK" if ok_all else "EVAL FAILED"); sys.exit(0 if ok_all else 1)
