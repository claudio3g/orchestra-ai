"""Test dell allineamento del token Pipelines nel database di Open WebUI (passo 5 di start_ai_stack.sh).

Estrae dal launcher lo script Python che gira dentro il container e lo esegue su due database SQLite finti:
il formato fino a Open WebUI 0.9 (riga id=1, JSON nella colonna data) e quello dalla 0.13 (una riga per voce,
chiavi in openai.api_keys). Solo libreria standard: niente Docker, nessun dato reale.
"""
import json, os, re, sqlite3, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent; REPO = HERE.parent
sys.path.insert(0, str(HERE / "helpers"))
from tiny import check, summary

src = (REPO / "start_ai_stack.sh").read_text(encoding="utf-8")
m = re.search(r'docker exec "\$WEBUI_CONTAINER" python3 -c "\n(.*?)\n" && success "Token pipelines', src, re.S)
check("lo script di allineamento si trova nel launcher", m is not None)
code = m.group(1) if m else ""
TOKEN = "TOKEN-NUOVO"

def esegui(db):
    c = code.replace("${_TOKEN}", TOKEN).replace("/app/backend/data/webui.db", db)
    r = subprocess.run([sys.executable, "-c", c], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr

def db_vecchio(keys, urls):
    p = tempfile.mktemp(suffix=".db"); c = sqlite3.connect(p)
    c.execute("CREATE TABLE config (id INTEGER PRIMARY KEY, data JSON, version INTEGER)")
    c.execute("INSERT INTO config VALUES (1, ?, 0)", (json.dumps({"openai": {"api_keys": keys, "api_base_urls": urls}, "altro": 1}),))
    c.commit(); c.close(); return p

def db_nuovo(keys, urls):
    p = tempfile.mktemp(suffix=".db"); c = sqlite3.connect(p)
    c.execute('CREATE TABLE config ("key" TEXT NOT NULL PRIMARY KEY, value JSON NOT NULL, updated_at BIGINT)')
    if keys is not None:
        c.execute("INSERT INTO config VALUES ('openai.api_keys', ?, 1)", (json.dumps(keys),))
    c.execute("INSERT INTO config VALUES ('openai.api_base_urls', ?, 1)", (json.dumps(urls),))
    c.execute("INSERT INTO config VALUES ('openai.enable', 'true', 1)")
    c.commit(); c.close(); return p

def chiavi(p):
    c = sqlite3.connect(p); cols = [r[1] for r in c.execute("PRAGMA table_info(config)")]
    if "data" in cols: return json.loads(c.execute("SELECT data FROM config WHERE id=1").fetchone()[0])["openai"]["api_keys"]
    r = c.execute("SELECT value FROM config WHERE key='openai.api_keys'").fetchone()
    return json.loads(r[0]) if r else None

URLS = ["http://ai-pipelines-session:9099", "https://api.deepseek.com/v1"]
for nome, crea in (("formato 0.9 (colonna data)", db_vecchio), ("formato 0.13 (key/value)", db_nuovo)):
    p = crea(["TOKEN-VECCHIO", "CHIAVE-DEEPSEEK", "ORFANA"], URLS)
    rc, out = esegui(p)
    check(f"{nome}: termina senza errori", rc == 0, out)
    check(f"{nome}: token nuovo in posizione 0, DeepSeek tenuta, orfana tolta", chiavi(p) == [TOKEN, "CHIAVE-DEEPSEEK"], chiavi(p))
    check(f"{nome}: nel log nessun valore di chiave", not any(k in out for k in ("TOKEN-VECCHIO", "CHIAVE-DEEPSEEK", TOKEN)), out)
    rc, out = esegui(p)
    check(f"{nome}: secondo giro senza modifiche", rc == 0 and "nessuna modifica" in out and chiavi(p) == [TOKEN, "CHIAVE-DEEPSEEK"], out)
    os.unlink(p)

p = db_vecchio(["T"], URLS); c = sqlite3.connect(p); rc, out = esegui(p)
check("formato 0.9: le altre impostazioni restano", json.loads(c.execute("SELECT data FROM config WHERE id=1").fetchone()[0]).get("altro") == 1)
c.close(); os.unlink(p)
p = db_nuovo(None, URLS); rc, out = esegui(p)
check("formato 0.13 senza chiavi salvate: nessun errore e nulla scritto", rc == 0 and chiavi(p) is None and "nulla da allineare" in out, out)
os.unlink(p)

# B23: controllo (solo lettura) che il modello del manifold abbia Chiamata Funzione = Legacy.
m = re.search(r'# B23:.*?docker exec "\$WEBUI_CONTAINER" python3 -c "\n(.*?)\n" \|\| warn "Impostare in Open WebUI', src, re.S)
check("B23: il controllo del modello si trova nel launcher", m is not None)
code_b23 = m.group(1) if m else ""

def esegui_b23(db):
    r = subprocess.run([sys.executable, "-c", code_b23.replace("/app/backend/data/webui.db", db)], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr

def db_modelli(params):
    p = tempfile.mktemp(suffix=".db"); c = sqlite3.connect(p)
    c.execute("CREATE TABLE model (id TEXT PRIMARY KEY, user_id TEXT, base_model_id TEXT, name TEXT, meta TEXT, params TEXT)")
    c.execute("INSERT INTO model VALUES ('altro', 'u', NULL, 'Altro', '{}', ?)", (json.dumps({"function_calling": "legacy"}),))
    if params is not None:
        c.execute("INSERT INTO model VALUES ('orchestra_manifold.orchestra', 'u', NULL, 'Orchestra', '{}', ?)", (json.dumps(params),))
    c.commit(); c.close(); return p

for nome, params, atteso in (("legacy", {"function_calling": "legacy"}, 0), ("nativa", {"function_calling": "native"}, 3),
                             ("predefinita (nessun valore)", {}, 3), ("modello non salvato", None, 3)):
    p = db_modelli(params)
    prima = open(p, "rb").read()
    rc, out = esegui_b23(p)
    check(f"B23 {nome}: codice di uscita {atteso}", rc == atteso, out)
    check(f"B23 {nome}: il database non viene modificato", open(p, "rb").read() == prima)
    os.unlink(p)
p = tempfile.mktemp(suffix=".db"); sqlite3.connect(p).close(); rc, out = esegui_b23(p)
check("B23 database senza tabella model (Open WebUI vecchia): avviso, nessun errore Python", rc == 3 and "Traceback" not in out, out)
os.unlink(p)
summary("WEBUI TOKEN")
