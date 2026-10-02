# AI Bootstrap — Orchestra AI

Documento di onboarding per agenti AI che operano su questo repository.

> **Ultimo aggiornamento:** 2026-10-02
> **Owner:** claudio3g
> **Repo:** claudio3g/orchestra-ai

---

## 1. Come accedere al repository (per un'AI)

### Metodo che funziona ✅

GitHub REST API:

    https://api.github.com/repos/claudio3g/orchestra-ai/contents/<path>

Risposta: JSON con campo `content` codificato in base64. Per decodificare:

    curl -s "https://api.github.com/repos/claudio3g/orchestra-ai/contents/README.it.md" | python3 -c "import sys, json, base64; print(base64.b64decode(json.load(sys.stdin)['content']).decode())"

Per una directory, ritorna una lista di file con campi path, name, type, size.

### Metodo che NON funziona ❌

Raw URL diretto:

    https://raw.githubusercontent.com/claudio3g/orchestra-ai/main/<path>

Può fallire per restrizioni di rete/proxy nell'ambiente AI.

### Pattern per leggere l'intero repository

1. GET `/contents/` per la lista radice
2. Per ogni entry con `type: file`, GET `/contents/<path>` e decodifica base64
3. Per ogni entry con `type: dir`, ricorre su `/contents/<path>/`
4. Accumula i contenuti in una mappa path -> contenuto

Punto di partenza consigliato per capire il progetto:

- `README.it.md` — descrizione italiana completa
- `README.md` — descrizione inglese completa
- `AI_BOOTSTRAP.md` — questo file (istruzioni per AI)
- `start_ai_stack.sh` — launcher principale
- `rag/rag_service.py` — servizio RAG
- `.github/workflows/ai-commit.yml` — workflow di commit automatico

---

## 2. Percorsi reali del progetto

| Cosa | Percorso |
|------|----------|
| Repository locale | /home/claudio/ai-sessioni |
| Remote Git | git@github.com:claudio3g/orchestra-ai.git |
| Branch principale | main |
| Branch secondario | dual-gpu-step1 (migrazione dual-GPU) |
| Workflow AI | .github/workflows/ai-commit.yml |
| Script dispatch | ~/ai-dispatch.sh |
| Token GitHub | ~/.orchestra_github_token (fine-grained PAT) |
| RAG service | rag/rag_service.py (porta 6335) |
| Knowledge base | document-ai/knowledge/ |

---

## 3. Workflow AI - commit automatico

Il repository supporta commit automatici generati da AI tramite GitHub Actions repository_dispatch.

### Flusso

1. L'AI genera una patch in formato diff.
2. La patch viene inviata via curl a GitHub API (POST /repos/claudio3g/orchestra-ai/dispatches).
3. Il workflow .github/workflows/ai-commit.yml si attiva:
   - Job validate: verifica payload (base64, formato diff, dry-run git apply --check).
   - Job sandbox-test: applica la patch su ubuntu-24.04 ed esegue 5 test (Python, Bash, YAML, JSON, Shellcheck).
   - Job commit-push: se tutti i test passano, committa e pusha con autore github-actions[bot].
4. Se i test falliscono, il repository resta invariato.

### Trigger manuale

    ~/ai-dispatch.sh <patch.diff> "<messaggio commit>" [branch]

### Cosa fa ~/ai-dispatch.sh

1. Legge il token da ~/.orchestra_github_token
2. Codifica la patch in base64
3. Invia POST a GitHub API con event_type=ai-update
4. Verifica la risposta HTTP (204 = successo)

---

## 4. Cose da NON fare

- NON eseguire git reset --hard senza backup
- NON eseguire git push --force (usare --force-with-lease se necessario)
- NON committare: ~/.orchestra_github_token, .orchestra_token, .webui_secret_key, rag/.file_hash_cache.json
- NON modificare .gitignore senza verificare l'impatto sulla whitelist
- NON esporre i servizi pubblicamente senza modificare i binding (restano su 127.0.0.1)

---

## 5. Comandi utili

    # Stato del repository
    cd ~/ai-sessioni && git status && git log --oneline -5

    # Verifica servizi
    curl -s http://127.0.0.1:6335/health | jq
    curl -s http://127.0.0.1:6335/status | jq

    # Backup completo
    git bundle create ~/orchestra-backup-$(date +%F_%H%M).bundle --all

    # Riavvio RAG service
    nohup python rag_service.py > logs/rag_service.log 2>&1 &

---

## 6. Struttura del repository

    orchestra-ai/
    ├── .github/workflows/ai-commit.yml
    ├── document-ai/          # knowledge base, config, script
    ├── ollama/               # docker-compose, pipelines
    ├── rag/                  # servizio RAG
    ├── workflows/            # workflow ComfyUI (esterno)
    ├── logs/
    ├── start_ai_stack.sh
    ├── start_comfyui.sh
    ├── README.md             # inglese
    ├── README.it.md          # italiano
    └── AI_BOOTSTRAP.md       # questo file

---

## 7. Come usare questo file

All'inizio di ogni nuova sessione con un'AI:

1. Incolla all'AI questo URL:
   https://api.github.com/repos/claudio3g/orchestra-ai/contents/AI_BOOTSTRAP.md
2. L'AI lo legge, decodifica base64, e ha il contesto completo.
3. Da lì può leggere altri file con lo stesso pattern.

Se l'AI non riesce a leggere l'URL, incolla direttamente il contenuto di questo file.

---

## 8. Contesto hardware

- CPU: Intel i9 (nix-i9)
- GPU: RTX 3090 (24 GB) + RTX 4060 Laptop (8 GB)
- OS: Ubuntu 24.04
- Runtime AI: Ollama + Open WebUI + Qdrant + RAG custom
- Migrazione dual-GPU: in corso (branch dual-gpu-step1)

---

## 9. Ultime modifiche significative

| Data | Cosa |
|------|------|
| 2026-10-02 | Riparazione commit spazzatura su main, fix .gitignore (cache RAG, whitelist README), workflow AI robusto con 3 job e 5 test, README.it.md, link cross-language, AI_BOOTSTRAP.md |
