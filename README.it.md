# Orchestra AI

**Orchestra** è uno stack AI self-hosted, local-first, costruito attorno a **Ollama, Open WebUI, Pipelines personalizzate, RAG e Qdrant**. Il progetto è pensato per eseguire servizi AI in locale, mantenere la knowledge base sotto il controllo dell'utente, e combinare modelli LLM general-purpose, retrieval documentale, pipeline di elaborazione custom e storage vettoriale persistente in un unico ambiente.

> **Stato:** progetto personale/locale attivo
> **Target primario:** Linux + GPU NVIDIA + Docker
> **Repository:** https://github.com/claudio3g/orchestra-ai

---

## Panoramica

Orchestra separa intenzionalmente i propri componenti in modo da poter isolare e sostituire ogni pezzo senza impatti sugli altri.

                         ┌─────────────────────┐
                         │     Open WebUI       │
                         │   User interface     │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │      Pipelines       │
                         │  custom AI processing│
                         └──────────┬──────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
          ┌─────────▼─────────┐           ┌────────▼────────┐
          │      Ollama       │           │   RAG Service   │
          │  Local LLM runtime│           │  document search│
          └─────────┬─────────┘           └────────┬────────┘
                    │                              │
                    │                       ┌──────▼──────┐
                    │                       │   Qdrant    │
                    │                       │  vector DB  │
                    │                       └─────────────┘
                    │
             Local AI models

### Componenti

- **Open WebUI** — interfaccia utente (porta `3001`).
- **Ollama** — runtime LLM locale (porta `11435`).
- **Pipelines** — elaborazione AI e routing specifico del progetto (porta `9099`).
- **RAG Service** — ingestione documenti e retrieval semantico (porta `6335`, Python, fuori Docker).
- **Qdrant** — database vettoriale persistente (porta `6333`).
- **document-ai** — knowledge base, config, script e routing snapshot.

---

## Modelli LLM richiesti

`start_ai_stack.sh` verifica e scarica automaticamente i seguenti modelli in Ollama:

| Modello | Ruolo |
|---------|-------|
| `llama3.2:3b` | generale, veloce |
| `qwen3.5:9b` | generale, qualità media |
| `llama3.1:8b` | generale bilanciato |
| `qwen2.5-coder:14b-instruct-q4_K_M` | codice |
| `llava:7b` | vision |
| `moondream:v2` | vision leggero |

I modelli sono salvati nel volume Docker `ollama-session`.

---

## Requisiti

- **OS**: Linux (Ubuntu 24.04 testato)
- **Docker** + Docker Compose
- **Driver NVIDIA** + NVIDIA Container Toolkit
- **Python 3.12**
- Utilità: `curl`, `openssl`, `zramctl` (opzionale)
- **GPU consigliate**: RTX 3090 (24 GB) + RTX 4060 Laptop (8 GB)

---

## Struttura del repository

    orchestra-ai/
    ├── .github/
    │   └── workflows/
    │       └── ai-commit.yml            # workflow di commit automatico AI
    ├── document-ai/
    │   ├── config/                      # valves, config orchestra
    │   ├── knowledge/                   # PDF, MD, DOCX, XLSX (knowledge base)
    │   ├── routing_snapshots/           # snapshot di routing
    │   ├── scripts/                     # script operativi (es. egpu_check.sh)
    │   └── system/
    │       └── orchestra_manifold.py
    ├── ollama/
    │   ├── docker-compose.yml
    │   ├── Modelfile-blender
    │   └── pipelines/                   # pipeline custom Open WebUI
    │       ├── embedding_utils.py
    │       ├── image_loop.py
    │       ├── orchestra_evolver.py
    │       ├── orchestra_manifold.py
    │       ├── pattern_logger.py
    │       ├── rag_filter.py
    │       └── requirements.txt
    ├── rag/
    │   ├── Dockerfile
    │   ├── docker-compose.prod.yml
    │   ├── rag_service.py               # servizio HTTP RAG
    │   ├── rag_indexer_lib.py
    │   └── requirements.txt
    ├── workflows/
    │   ├── sd15.json
    │   └── sdxl_base.json
    ├── logs/
    │   ├── rag_service.log
    │   └── patterns.jsonl
    ├── start_ai_stack.sh                # launcher principale (eseguibile)
    ├── start_comfyui.sh
    ├── README.md                        # versione inglese (principale)
    └── README.it.md                     # questo file (versione italiana)

---

## Avvio dello stack

### Comando principale

    cd ~/ai-sessioni
    ./start_ai_stack.sh

### Cosa fa il launcher

1. Ferma eventuali container e processi RAG residui.
2. Monta SSD esterno e configura zram (se configurato).
3. Crea le directory necessarie.
4. Crea/verifica la rete Docker `ollama_default`.
5. Avvia Ollama, attende che sia disponibile, scarica i modelli mancanti.
6. Avvia Qdrant, Pipelines, Open WebUI.
7. Allinea il token Pipelines nel database di Open WebUI.
8. Avvia il servizio RAG e, se la collezione Qdrant è vuota, lancia l'indicizzazione.

### Verifica dei servizi

    docker ps
    curl http://127.0.0.1:11435/         # Ollama
    curl http://127.0.0.1:6333/healthz   # Qdrant
    curl http://127.0.0.1:6335/health    # RAG Service

---

## AI Workflow — Commit automatici

Questo repository supporta **commit automatici generati da AI** tramite `repository_dispatch` di GitHub Actions. Il sistema è progettato per essere sicuro, verificabile e non distruttivo.

### Flusso

1. L'AI genera una patch in formato diff.
2. La patch viene inviata via `curl` a GitHub API.
3. Il workflow `ai-commit.yml` si attiva:
   - **Job `validate`**: controlla che il payload sia valido.
   - **Job `sandbox-test`**: applica la patch in sandbox ed esegue i test.
   - **Job `commit-push`**: se i test passano, committa e pusha.
4. Il commit appare su GitHub con autore `github-actions[bot]`.

### Trigger manuale

    ~/ai-dispatch.sh <patch.diff> "<messaggio commit>" [branch]

Esempio:

    ~/ai-dispatch.sh /tmp/ai_patch.diff "docs: aggiorna README" main

### Sicurezze

- La patch viene **validata** prima di essere applicata:
  - payload non vuoto
  - base64 valido
  - formato diff corretto (`diff --git` come prima riga)
  - `git apply --check` (dry-run) senza conflitti
- I test girano in **sandbox** isolata su `ubuntu-24.04`.
- Il commit avviene **solo** se tutti i test passano.
- Se i test falliscono, il repository resta invariato.

### Test eseguiti dal workflow

| Test | Cosa controlla | Bloccante |
|------|----------------|-----------|
| Python syntax | `python -m py_compile` su tutti i `.py` | sì |
| Bash syntax | `bash -n` su tutti gli `.sh` | sì |
| YAML syntax | parsing con `pyyaml` | sì |
| JSON syntax | parsing con `json` | sì |
| Shellcheck | errori gravi negli script bash | no (warning) |

### File coinvolti

- **Workflow**: `.github/workflows/ai-commit.yml`
- **Script locale**: `~/ai-dispatch.sh`
- **Token GitHub**: `~/.orchestra_github_token` (fine-grained PAT, permessi `contents:write`)

---

## RAG & Knowledge base

### Documenti

I documenti da indicizzare vanno in `document-ai/knowledge/`. Formati supportati:

- PDF (`pdfplumber`, `pypdf`)
- DOCX (`python-docx`)
- XLSX (`openpyxl`)
- Markdown, testo semplice

### Indicizzazione

- **Automatica** all'avvio dello stack, se la collezione Qdrant è vuota.
- **Manuale** via API: `POST /index`.

### Configurazione

| Parametro | Valore |
|-----------|--------|
| Collezione Qdrant | `orchestra` |
| Dimensioni vettori | 768 |
| Similarità | coseno |
| Modello di embedding | `nomic-ai/nomic-embed-text-v1.5` |
| Runtime embedding | `fastembed` |
| Docs root | `/home/claudio/ai-sessioni/document-ai` |

### Stato attuale della collezione

Distribuzione `total_chunks: 719` per dominio:

| Dominio | Chunk |
|---------|-------|
| knowledge | 355 |
| system | 163 |
| routing_snapshots | 157 |
| scripts | 34 |
| config | 10 |

---

## API RAG

| Endpoint | Metodo | Descrizione |
|----------|--------|-------------|
| `/health` | GET | Stato del servizio, deps, embed model |
| `/status` | GET | Statistiche collezione (`total_chunks`, `by_domain`) |
| `/index` | POST | Avvia indicizzazione (asincrona) |
| `/vram` | GET | VRAM libera (multi-GPU con ruoli main/aux) |
| `/deploy` | POST | Deploy sicuro di file (whitelist) |

Esempi:

    curl -s http://127.0.0.1:6335/health | jq
    curl -s http://127.0.0.1:6335/status | jq
    curl -s http://127.0.0.1:6335/vram | jq

---

## GPU e multi-GPU

- **Hardware di sviluppo:** RTX 3090 (24 GB) + RTX 4060 Laptop (8 GB)
- **Architettura attuale:** la 3090 è il dispositivo primario per carichi pesanti; la 4060 per modelli piccoli.
- **Routing GPU-modello/agente:** in fase di sviluppo (branch `dual-gpu-step1`).
- Il servizio RAG espone `/vram` per leggere la VRAM libera tramite `nvidia-smi`, con ruoli `main`/`aux`.

### Branch attivi

| Branch | Scopo | Stato |
|--------|-------|-------|
| `main` | linea principale, stabile | attivo |
| `dual-gpu-step1` | migrazione dual-GPU 3090 + 4060 | in sviluppo |

---

## Log e monitoraggio

- **RAG service**: `logs/rag_service.log`
- **Pattern**: `logs/patterns.jsonl`
- **Docker**: `docker logs <container>`

Contenitori attesi:

- `ai-webui-session`
- `ai-pipelines-session`
- `ai-ollama-session`
- `ai-qdrant-session`

---

## Troubleshooting

### `git status` mostra `rag/.file_hash_cache.json` come modificato

**Causa:** il RAG service riscrive continuamente questo file di cache. Se non è ignorato da git, blocca pull/checkout e crea rumore.

**Soluzione (già applicata):**

- `.gitignore` contiene `rag/.file_hash_cache.json`
- il file è stato rimosso dal tracking con `git rm --cached`

Se riappare come modificato, verificare con:

    git check-ignore -v rag/.file_hash_cache.json

Deve restituire la regola `.gitignore`. Se non lo fa, la regola è stata persa.

### `git pull` blocca per modifiche locali

Se compare un errore tipo "Le tue modifiche locali sarebbero sovrascritte", significa che un file tracciato è stato modificato a runtime. Il candidato tipico è la cache RAG (vedi sopra). Altre volte può essere un file di log che non dovrebbe essere tracciato.

### Workflow AI fallisce con "payload 'patch' vuoto"

Il job `validate` ha rifiutato la patch perché vuota. Verificare il file `/tmp/ai_patch.diff`: se è 0 byte, rigenerarlo.

### Workflow AI fallisce su "git apply --check"

La patch non è applicabile allo stato attuale del branch. Cause tipiche:

- il branch è avanzato dopo la generazione della patch
- il contesto delle righe modificate non corrisponde più

Rigenerare la patch dal branch aggiornato.

### Il RAG service non riparte

    cd ~/ai-sessioni
    nohup python rag_service.py > logs/rag_service.log 2>&1 &
    sleep 3
    ps aux | grep rag_service | grep -v grep
    curl -s http://127.0.0.1:6335/health

Verificare il log in `logs/rag_service.log`.

---

## Riferimento per AI agents

Questa sezione è pensata per essere letta da un agente AI che debba operare sul repository senza contesto umano.

### Percorsi reali

| Cosa | Percorso |
|------|----------|
| Repository locale | `/home/claudio/ai-sessioni` |
| Remote | `git@github.com:claudio3g/orchestra-ai.git` |
| Workflow AI | `.github/workflows/ai-commit.yml` |
| Script dispatch | `~/ai-dispatch.sh` |
| Token GitHub | `~/.orchestra_github_token` |
| RAG service | `rag/rag_service.py` |
| Knowledge base | `document-ai/knowledge/` |

### Convenzioni

- **NON** assumere che il routing GPU sia automatico: è in sviluppo.
- **Leggere sempre** `start_ai_stack.sh` per la configurazione runtime aggiornata.
- **Verificare** la presenza di `~/.orchestra_github_token` prima di chiamare `ai-dispatch.sh`.
- **Non committare**: `~/.orchestra_github_token`, `.orchestra_token`, `.webui_secret_key`, `rag/.file_hash_cache.json`.
- **Rispettare** i binding locali (`127.0.0.1` per la maggior parte dei servizi).

### Comandi utili

    # Stato del repository
    cd ~/ai-sessioni && git status && git log --oneline -5

    # Test del flusso AI (patch vuota, deve fallire in validate)
    > /tmp/ai_patch.diff
    ~/ai-dispatch.sh /tmp/ai_patch.diff "test validazione"

    # Verifica RAG
    curl -s http://127.0.0.1:6335/health | jq
    curl -s http://127.0.0.1:6335/status | jq

    # Backup completo
    git bundle create ~/orchestra-backup-$(date +%F_%H%M).bundle --all

### Cosa NON fare

- NON eseguire `git reset --hard` senza backup.
- NON eseguire `git push --force` (usare `--force-with-lease` se necessario).
- NON committare file di cache (`file_hash_cache.json`, `__pycache__`, `*.pyc`).
- NON modificare `.gitignore` senza verificare l'impatto sulla whitelist.
- NON esporre i servizi pubblicamente senza modifiche ai binding.

---

## Direzioni di sviluppo

- Routing multi-GPU esplicito (3090 → carichi pesanti, 4060 → leggeri)
- Assegnazione GPU-aware degli agenti
- Ottimizzazione RAG (chunking, batch adattivo)
- Miglioramento dell'ingestione documenti
- Caricamento modelli consapevole delle risorse
- Osservabilità e logging avanzati
- Separazione tra core Orchestra e integrazioni opzionali (ComfyUI è esterno)

---

## Licenza

Progetto personale. Nessuna licenza formale al momento.
