#!/usr/bin/env python3
"""
orchestra_eval.py - banco di prova per misurare l affidabilita dei modelli locali (profilo rigore, passo B0).

COSA FA: invia ad Ollama i casi di document-ai/eval/cases.json con varie configurazioni (modello,
temperatura, profilo di sistema) e conta quante risposte superano controlli DETERMINISTICI (regex e
bash -n): nessun LLM fa da giudice. Stampa una tabella compatta e salva le risposte complete in un
file JSONL (default ~/orchestra-eval/), cosi non servono incolla lunghi in chat.
LIVELLI: i controlli "grave" decidono se una risposta e promossa; quelli "consigliato" (es. set -e) sono
solo segnalati. Una risposta tagliata dal limite di token e bocciata con l etichetta troncata-dal-limite.
LIMITI: misura modello + temperatura + profilo di sistema, NON la pipeline completa di Orchestra
(instradamento, RAG, bootstrap). Poche esecuzioni per cella: indicano una tendenza, non una statistica.
SICUREZZA: nessun segreto letto; l unica scrittura e il file dei risultati.
USO: python3 document-ai/scripts/orchestra_eval.py [--matrix F] [--cases F] [--runs 3] [--timeout 300]
                                                   [--num-predict 1500] [--only id,id] [--out DIR]
"""
import argparse, json, os, re, subprocess, sys, tempfile, time, urllib.request

# Profili di sistema. "strict" e volutamente GENERICO: non contiene la soluzione dei casi di prova
# (niente "teaching to the test"), altrimenti la misura sarebbe gonfiata.
PROFILES = {
    "none": "",
    "strict": ("Sei un assistente tecnico rigoroso. Regole: 1) Usa solo informazioni presenti nel messaggio "
               "o che conosci con certezza. 2) Se manca un dato necessario (un log, un file, un output), dillo "
               "e chiedilo: non inventarlo. 3) Se non conosci un opzione, un comando o una versione, scrivi che "
               "non lo conosci invece di descriverla. 4) Non inventare percorsi, utenti o password: usa variabili "
               "configurabili. 5) Per operazioni su file o sistema prevedi precauzioni (modalita di prova, "
               "controlli sugli errori). 6) Risposte brevi e verificabili."),
}

def clean(text):
    """Toglie il ragionamento ad alta voce dei modelli: blocchi <think>...</think> e, se la risposta e stata
    tagliata dal limite di token, un <think> rimasto aperto (da li alla fine non c e risposta)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    return re.sub(r"<think>.*\Z", "", text, flags=re.S).strip()

def extract_bash(text):
    return "\n".join(re.findall(r"```(?:bash|sh|shell)?[ \t]*\n(.*?)```", text, flags=re.S | re.I))

def run_check(check, text):
    t = check["type"]
    if t == "must_match":
        return re.search(check["pattern"], text, re.I | re.M) is not None
    if t == "must_not_match":
        return re.search(check["pattern"], text, re.I | re.M) is None
    if t == "bash_syntax":
        code = extract_bash(text)
        if not code.strip():
            return False
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write(code); path = f.name
        try:
            return subprocess.run(["bash", "-n", path], capture_output=True, timeout=10).returncode == 0
        finally:
            os.unlink(path)
    raise ValueError("tipo di controllo sconosciuto: %s" % t)

def grade(case, text):
    """Promossa se passano tutti i controlli di livello 'grave'; quelli 'consigliato' sono solo segnalati."""
    text = clean(text)
    res = [(c.get("label", c["type"]), run_check(c, text)) for c in case["checks"]]
    ok = all(r for c, (_, r) in zip(case["checks"], res) if c.get("tier", "grave") == "grave")
    return ok, res

def http_json(url, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def has_model(names, model):
    return model in names or (":" not in model and model + ":latest" in names)

def chat(cfg, prompt, seed, timeout, num_predict):
    """Restituisce (testo, meta). meta contiene done_reason ("length" = tagliata dal limite) ed eval_count."""
    msgs = []
    sysmsg = PROFILES.get(cfg.get("profile", "none"), "")
    if sysmsg:
        msgs.append({"role": "system", "content": sysmsg})
    msgs.append({"role": "user", "content": prompt})
    body = {"model": cfg["model"], "messages": msgs, "stream": False,
            "options": {"temperature": cfg["temperature"], "seed": seed, "num_predict": num_predict}}
    if "think" in cfg:                      # solo per i modelli che ragionano (es. qwen3): think=false
        body["think"] = cfg["think"]
    r = http_json(cfg["url"].rstrip("/") + "/api/chat", body, timeout)
    return r["message"]["content"], {"done_reason": r.get("done_reason"), "eval_count": r.get("eval_count")}

def main(argv=None):
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    ap = argparse.ArgumentParser(description="Banco di prova deterministico per i modelli locali")
    ap.add_argument("--matrix", default=os.path.join(root, "document-ai", "eval", "matrix.json"))
    ap.add_argument("--cases", default=os.path.join(root, "document-ai", "eval", "cases.json"))
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--num-predict", type=int, default=1500)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=os.path.expanduser("~/orchestra-eval"))
    a = ap.parse_args(argv)
    cases = json.load(open(a.cases, encoding="utf-8"))["cases"]
    if a.only:
        keep = set(a.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    matrix = json.load(open(a.matrix, encoding="utf-8"))
    os.makedirs(a.out, exist_ok=True)
    jpath = os.path.join(a.out, "eval-%s.jsonl" % time.strftime("%Y%m%d_%H%M%S"))
    table = []
    with open(jpath, "w", encoding="utf-8") as jf:
        for cfg in matrix:
            name = cfg["name"]
            try:
                names = {m["name"] for m in http_json(cfg["url"].rstrip("/") + "/api/tags").get("models", [])}
            except Exception as e:
                table.append((name, "non raggiungibile (%s)" % type(e).__name__, None, None)); continue
            if not has_model(names, cfg["model"]):
                table.append((name, "modello assente (non scaricato): %s" % cfg["model"], None, None)); continue
            runs = 1 if float(cfg["temperature"]) == 0 else a.runs    # a temperatura 0 le ripetizioni sono inutili
            npred = cfg.get("num_predict", a.num_predict)
            cells, fails, adv = {}, {}, {}
            for case in cases:
                tiers = {c.get("label", c["type"]): c.get("tier", "grave") for c in case["checks"]}
                passed = 0
                for i in range(runs):
                    seed = 1000 + i; meta = {}
                    try:
                        text, meta = chat(cfg, case["prompt"], seed, a.timeout, npred)
                        ok, res = grade(case, text); err = None
                        if meta.get("done_reason") == "length":      # risposta incompleta: non conta come promossa
                            ok = False; res = res + [("troncata-dal-limite", False)]
                    except Exception as e:
                        text, ok, res, err = "", False, [("errore-richiesta", False)], "%s: %s" % (type(e).__name__, str(e)[:80])
                    passed += ok
                    for lab, good in res:
                        if not good:
                            bucket = fails if tiers.get(lab, "grave") == "grave" else adv
                            k = case["id"] + "/" + lab; bucket[k] = bucket.get(k, 0) + 1
                    jf.write(json.dumps({"config": name, "case": case["id"], "run": i, "seed": seed, "ok": ok,
                                         "checks": dict(res), "done_reason": meta.get("done_reason"),
                                         "eval_count": meta.get("eval_count"), "error": err, "response": text},
                                        ensure_ascii=False) + "\n")
                    jf.flush()
                cells[case["id"]] = (passed, runs)
            table.append((name, cells, fails, adv))
    ids = [c["id"] for c in cases]
    cw = {i: max(len(i) + 2, 8) for i in ids}
    w = max([16] + [len(r[0]) + 2 for r in table])
    print("configurazione".ljust(w) + "".join(i.ljust(cw[i]) for i in ids) + "totale")
    for name, cells, _, _ in table:
        if isinstance(cells, str):
            print(name.ljust(w) + cells); continue
        tp = sum(p for p, _ in cells.values()); tn = sum(n for _, n in cells.values())
        print(name.ljust(w) + "".join(("%d/%d" % cells[i]).ljust(cw[i]) for i in ids) + "%d/%d (%d%%)" % (tp, tn, round(100 * tp / max(tn, 1))))
    for name, _, fails, adv in table:
        if fails:
            print("  - %s: %s" % (name, ", ".join("%s x%d" % kv for kv in sorted(fails.items(), key=lambda kv: -kv[1])[:4])))
        if adv:
            print("  ~ %s (consigliato): %s" % (name, ", ".join("%s x%d" % kv for kv in sorted(adv.items(), key=lambda kv: -kv[1])[:3])))
    print("risposte complete: %s" % jpath)
    return 0

if __name__ == "__main__":
    sys.exit(main())
