# AI Context — Orchestra AI

Bundle dei file chiave del progetto, concatenati per un singolo fetch.

> Generato: 2026-10-02T17:07:37Z
> Branch: `main`

---

## File: `README.it.md`

```
# Orchestra AI

> 🇬🇧 [English version](README.md)

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

```

---

## File: `AI_BOOTSTRAP.md`

```
# AI Bootstrap — Orchestra AI

Documento di onboarding per agenti AI che operano su questo repository.

> **Ultimo aggiornamento:** 2026-10-02
> **Owner:** claudio3g
> **Repo:** claudio3g/orchestra-ai

---

## ⚠️ REGOLE FONDAMENTALI (leggere prima di tutto)

### Anti-hallucination policy

1. **Non inventare MAI** contenuti di file, struttura di directory o nomi di file.
2. **La lista autorevole dei file è in `AI_MANIFEST.md`**. Nessun file esiste al di fuori di quella lista. Se pensi che un file dovrebbe esistere ma non è nel manifest, **non esiste**.
3. **Ogni affermazione sul contenuto del repository deve essere supportata da un fetch effettivo**. Se non riesci a leggere un file, dì esplicitamente: "non ho potuto leggere X".
4. **Se una richiesta richiede di leggere più file di quanti riesci a fetchare**, dillo esplicitamente e chiedi quali prioritizzare. Non riempire i buchi con contenuti plausibili.
5. **Non citare file, directory o contenuti che non hai letto**. Se ti viene chiesto "leggi tutti i file", leggi il manifest, poi leggi i file uno per uno. Se non puoi, dichiaralo.

### Cosa fare se non puoi completare un task

- Dì: "Non posso completare X perché Y".
- Proponi un'alternativa: "Posso fare X1 o X2, quale preferisci?".
- **Mai** inventare per riempire il vuoto.

---

## 1. Come accedere al repository

### Metodo che funziona ✅

GitHub REST API:

    https://api.github.com/repos/claudio3g/orchestra-ai/contents/<path>

Risposta: JSON con campo `content` codificato base64. Decodifica:

    curl -s "https://api.github.com/repos/claudio3g/orchestra-ai/contents/README.it.md" | python3 -c "import sys, json, base64; print(base64.b64decode(json.load(sys.stdin)['content']).decode())"

### Metodo che può non funzionare ❌

    https://raw.githubusercontent.com/claudio3g/orchestra-ai/main/<path>

Può fallire per restrizioni di rete/proxy in alcuni ambienti AI.

---

## 2. File di onboarding (leggi in quest'ordine)

1. **`AI_BOOTSTRAP.md`** (questo file) — regole + metodo di accesso
2. **`AI_MANIFEST.md`** — inventario autorevole di TUTTI i file
3. **`AI_CONTEXT.md`** — bundle dei file chiave

URL API:

    https://api.github.com/repos/claudio3g/orchestra-ai/contents/AI_BOOTSTRAP.md
    https://api.github.com/repos/claudio3g/orchestra-ai/contents/AI_MANIFEST.md
    https://api.github.com/repos/claudio3g/orchestra-ai/contents/AI_CONTEXT.md

---

## 3. Percorsi reali del progetto

| Cosa | Percorso |
|------|----------|
| Repository locale | /home/claudio/ai-sessioni |
| Remote Git | git@github.com:claudio3g/orchestra-ai.git |
| Branch principale | main |
| Branch secondario | dual-gpu-step1 |
| Workflow AI | .github/workflows/ai-commit.yml |
| Script dispatch | ~/ai-dispatch.sh |
| Token GitHub | ~/.orchestra_github_token |
| RAG service | rag/rag_service.py (porta 6335) |

---

## 4. Workflow AI — commit automatico

1. L'AI genera una patch in formato diff.
2. La patch viene inviata via `curl` a GitHub API.
3. Il workflow `ai-commit.yml` si attiva:
   - **Job `validate`**: verifica payload.
   - **Job `sandbox-test`**: applica la patch su ubuntu-24.04 ed esegue 5 test.
   - **Job `commit-push`**: se i test passano, esegue lo script di generazione (se presente), committa e pusha.

Trigger manuale:

    ~/ai-dispatch.sh <patch.diff> "<messaggio commit>" [branch]

---

## 5. Cose da NON fare

- ❌ `git reset --hard` senza backup
- ❌ `git push --force`
- ❌ committare: `~/.orchestra_github_token`, `.orchestra_token`, `.webui_secret_key`, `rag/.file_hash_cache.json`
- ❌ esporre i servizi pubblicamente

---

## 6. Comandi utili

    cd ~/ai-sessioni && git status && git log --oneline -5
    curl -s http://127.0.0.1:6335/health | jq
    curl -s http://127.0.0.1:6335/status | jq
    git bundle create ~/orchestra-backup-$(date +%F_%H%M).bundle --all
    ./document-ai/scripts/generate_ai_context.sh

---

## 7. Contesto hardware

- CPU: Intel i9 (nix-i9)
- GPU: RTX 3090 (24 GB) + RTX 4060 Laptop (8 GB)
- OS: Ubuntu 24.04
- Runtime AI: Ollama + Open WebUI + Qdrant + RAG custom
- Migrazione dual-GPU: in corso (branch `dual-gpu-step1`)


```

---

## File: `.github/workflows/ai-commit.yml`

```
name: AI Commit & Push

on:
  repository_dispatch:
    types: [ai-update]

permissions:
  contents: write

concurrency:
  group: ai-commit-${{ github.event.client_payload.branch || 'main' }}
  cancel-in-progress: false

jobs:
  # ============================================================
  # JOB 0: Validazione del payload
  # ============================================================
  validate:
    runs-on: ubuntu-24.04
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.client_payload.branch || 'main' }}
          token: ${{ secrets.ORCHESTRA_PAT }}

      - name: Validazione payload
        run: |
          set -euo pipefail

          PATCH_B64='${{ github.event.client_payload.patch }}'
          BRANCH='${{ github.event.client_payload.branch || 'main' }}'

          echo "=== Validazione payload ==="
          echo "Branch target: $BRANCH"

          # 1. Patch non vuota
          if [ -z "$PATCH_B64" ]; then
            echo "❌ ERRORE: payload 'patch' vuoto"
            exit 1
          fi

          # 2. Base64 valido
          if ! echo "$PATCH_B64" | base64 -d > /tmp/ai_patch.diff 2>/dev/null; then
            echo "❌ ERRORE: base64 non valido"
            exit 1
          fi

          # 3. Formato diff
          if ! head -1 /tmp/ai_patch.diff | grep -q "^diff --git"; then
            echo "❌ ERRORE: la patch non inizia con 'diff --git'"
            echo "Prima riga:"
            head -1 /tmp/ai_patch.diff
            exit 1
          fi

          # 4. Statistiche
          FILES_COUNT=$(grep -c "^diff --git" /tmp/ai_patch.diff || echo 0)
          echo "✅ Patch valida: $FILES_COUNT file toccati"
          echo ""
          echo "=== Anteprima patch ==="
          head -20 /tmp/ai_patch.diff

          # 5. Dry-run apply
          echo ""
          echo "=== Dry-run git apply ==="
          if ! git apply --check /tmp/ai_patch.diff 2>&1; then
            echo "❌ ERRORE: la patch non è applicabile allo stato attuale del branch"
            echo "   Possibili cause: conflitti con modifiche recenti, contesto non corrispondente"
            exit 1
          fi
          echo "✅ Patch applicabile senza conflitti"

  # ============================================================
  # JOB 1: Sandbox + Test
  # ============================================================
  sandbox-test:
    needs: validate
    runs-on: ubuntu-24.04
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.client_payload.branch || 'main' }}
          token: ${{ secrets.ORCHESTRA_PAT }}

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: |
          set -euo pipefail
          pip install --quiet pyyaml
          if [ -f rag/requirements.txt ]; then
            pip install --quiet -r rag/requirements.txt
          fi
          if [ -f ollama/pipelines/requirements.txt ]; then
            pip install --quiet -r ollama/pipelines/requirements.txt
          fi
          sudo apt-get update -qq
          sudo apt-get install -y -qq shellcheck

      - name: Applica patch
        run: |
          set -euo pipefail
          echo "${{ github.event.client_payload.patch }}" | base64 -d > /tmp/ai_patch.diff
          git apply /tmp/ai_patch.diff
          echo "✅ Patch applicata"
          echo ""
          echo "=== File modificati ==="
          git diff --stat

      - name: Test 1 — Python syntax
        run: |
          set -euo pipefail
          echo "=== Test Python syntax ==="
          FAIL=0
          COUNT=0
          while IFS= read -r f; do
            COUNT=$((COUNT + 1))
            python -m py_compile "$f" || { echo "❌ $f"; FAIL=1; }
          done < <(find . -name "*.py" \
            -not -path "./.git/*" \
            -not -path "./ComfyUI/*" \
            -not -path "./.fastembed_cache*/*" \
            -not -path "*/venv/*" \
            -not -path "*/__pycache__/*")
          echo "Controllati $COUNT file Python"
          [ $FAIL -eq 0 ] && echo "✅ Python OK" || exit 1

      - name: Test 2 — Bash syntax
        run: |
          set -euo pipefail
          echo "=== Test Bash syntax ==="
          FAIL=0
          COUNT=0
          while IFS= read -r f; do
            COUNT=$((COUNT + 1))
            bash -n "$f" || { echo "❌ $f"; FAIL=1; }
          done < <(find . -name "*.sh" -not -path "./.git/*" -not -path "./ComfyUI/*")
          echo "Controllati $COUNT script Bash"
          [ $FAIL -eq 0 ] && echo "✅ Bash OK" || exit 1

      - name: Test 3 — YAML syntax
        run: |
          set -euo pipefail
          echo "=== Test YAML syntax ==="
          FAIL=0
          COUNT=0
          while IFS= read -r f; do
            COUNT=$((COUNT + 1))
            python -c "import yaml; yaml.safe_load(open('$f'))" 2>/dev/null || { echo "❌ $f"; FAIL=1; }
          done < <(find . \( -name "*.yml" -o -name "*.yaml" \) -not -path "./.git/*" -not -path "./ComfyUI/*")
          echo "Controllati $COUNT file YAML"
          [ $FAIL -eq 0 ] && echo "✅ YAML OK" || exit 1

      - name: Test 4 — JSON syntax
        run: |
          set -euo pipefail
          echo "=== Test JSON syntax ==="
          FAIL=0
          COUNT=0
          while IFS= read -r f; do
            COUNT=$((COUNT + 1))
            python -c "import json; json.load(open('$f'))" 2>/dev/null || { echo "❌ $f"; FAIL=1; }
          done < <(find . -name "*.json" \
            -not -path "./.git/*" \
            -not -path "./ComfyUI/*" \
            -not -path "./.fastembed_cache*/*" \
            -not -path "*/node_modules/*")
          echo "Controllati $COUNT file JSON"
          [ $FAIL -eq 0 ] && echo "✅ JSON OK" || exit 1

      - name: Test 5 — Shellcheck (warning, non bloccante)
        run: |
          echo "=== Shellcheck (solo errori) ==="
          find . -name "*.sh" -not -path "./.git/*" -not -path "./ComfyUI/*" \
            -exec shellcheck --severity=error {} + || true
          echo "ℹ️  Shellcheck completato (i warning non bloccano)"
        continue-on-error: true

  # ============================================================
  # JOB 2: Commit e Push (solo se i test passano)
  # ============================================================
  commit-push:
    needs: [validate, sandbox-test]
    runs-on: ubuntu-24.04
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.client_payload.branch || 'main' }}
          token: ${{ secrets.ORCHESTRA_PAT }}

      - name: Applica patch
        run: |
          set -euo pipefail
          echo "${{ github.event.client_payload.patch }}" | base64 -d > /tmp/ai_patch.diff
          git apply /tmp/ai_patch.diff

      - name: Riepilogo modifiche
        run: |
          set -euo pipefail
          echo "=== Statistiche ==="
          git diff --stat
          echo ""
          echo "=== Diff (prime 300 righe) ==="
          git diff | head -300

      - name: Generate AI context
        run: |
          set -euo pipefail
          if [ -f document-ai/scripts/generate_ai_context.sh ]; then
            chmod +x document-ai/scripts/generate_ai_context.sh
            ./document-ai/scripts/generate_ai_context.sh
            echo "✅ AI context generato"
          else
            echo "ℹ️  Script di generazione non presente, skip"
          fi

      - name: Configure Git
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

      - name: Commit e push
        run: |
          set -euo pipefail
          git add -A

          # Se non c'è nulla da committare (patch "vuota" dopo apply), esci pulito
          if git diff --cached --quiet; then
            echo "⚠️  Nessuna modifica da committare (patch senza effetti reali)"
            exit 0
          fi

          BRANCH='${{ github.event.client_payload.branch || 'main' }}'
          MSG='${{ github.event.client_payload.message || '🤖 AI: aggiornamento automatico' }}'

          git commit -m "$MSG"
          git push origin "$BRANCH"

          echo ""
          echo "✅ Push completato su $BRANCH"
          echo "   Commit: $(git rev-parse --short HEAD)"

```

---

## File: `.gitignore`

```
# =====================================================================
# .gitignore - Orchestra AI (LISTA BIANCA, cartella per cartella)
# Tutto e' ignorato tranne cio' che e' esplicitamente abilitato qui sotto.
# Per abilitare una nuova cartella: aggiungi una riga  !/nomecartella/
# =====================================================================
/*
!/.gitignore
!/.github/

# --- Cartelle abilitate ---
!/ollama/
!/document-ai/
!/logs/
!/rag/
!/workflows/

# --- Rete di sicurezza: valida DENTRO le cartelle abilitate ---
# Segreti e credenziali
*.apikey
*.key
*.pem
*.env
.env.*
!.env.example
*.secret
*.token
secrets/
.*_token*
.*_secret*

# Modelli, cache di embedding, pesi
.fastembed_cache*/
**/models--*/
**/.cache/
*.gguf
*.safetensors
*.onnx
*.bin
*.pack

# Log, backup, temporanei, runtime
*.log
*.bak
*.bak.*
*.swp
*.tmp
*~
__pycache__/
*.pyc
venv/
.venv/

# Pipeline rotte
ollama/pipelines/failed/
document-ai/config/valves_orchestra_manifold.json

# --- Cache generata a runtime dal RAG service ---
/rag/.file_hash_cache.json

# --- Documentazione principale (whitelisted dalla regola /*) ---
!/README.md
!/README.it.md
!/AI_BOOTSTRAP.md
!/AI_MANIFEST.md
!/AI_CONTEXT.md

```

---

## File: `start_ai_stack.sh`

```
#!/bin/bash
# ORCHESTRA 8GB — AI LOCAL STACK LAUNCHER v3.7
# -------------------------------------------------
# Changelog v3.7:
#   - Garantisce che nessun processo (container Docker o servizio RAG)
#     rimanga in esecuzione dopo l'uscita dello script.
#   - Ferma tutti i container all'avvio (pulizia da esecuzioni precedenti interrotte).
#   - Uccide i processi residui di rag_service.py all'avvio e all'uscita.
#   - I container usano `--restart no` (nessuna persistenza al boot).

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}ℹ️  $*${NC}"; }
success() { echo -e "${GREEN}✅ $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠️  $*${NC}"; }
error()   { echo -e "${RED}❌ $*${NC}"; }
header()  { echo -e "\n${BOLD}$*${NC}"; }

# ------------------------------------------------------------
# Variabili di configurazione
# ------------------------------------------------------------
OLLAMA_CONTAINER="ai-ollama-session"
WEBUI_CONTAINER="ai-webui-session"
PIPELINES_CONTAINER="ai-pipelines-session"
QDRANT_CONTAINER="ai-qdrant-session"
NETWORK="ollama_default"

OLLAMA_PORT=11435
WEBUI_PORT=3001
PIPELINES_PORT=9099
COMFYUI_PORT=8188
QDRANT_PORT=6333
RAG_SERVICE_PORT=6335

COMFYUI_DIR="$HOME/ai-sessioni/ComfyUI"
PIPELINES_DIR="$HOME/ai-sessioni/ollama/pipelines"
RAG_DIR="$HOME/ai-sessioni/rag"
DOCS_ROOT="$HOME/ai-sessioni/document-ai"
LOGS_DIR="$HOME/ai-sessioni/logs"
EXTERNAL_DISK_MOUNT="/media/claudio/01ED820B0D193A56"

REQUIRED_MODELS=(
    "llama3.2:3b"
    "qwen3.5:9b"
    "llama3.1:8b"
    "qwen2.5-coder:14b-instruct-q4_K_M"
    "llava:7b"
    "moondream:v2"
)

DOMAIN_DIRS=(system comfy image 3d audio)
RAG_SERVICE_PID=""

# ------------------------------------------------------------
# Gestione token persistente (generato una volta)
# ------------------------------------------------------------
TOKEN_FILE="$HOME/ai-sessioni/.orchestra_token"

generate_token() {
    openssl rand -hex 32 | tr -d '\n'
}

if [ -f "$TOKEN_FILE" ]; then
    PIPELINES_API_KEY=$(cat "$TOKEN_FILE")
    info "Token caricato da $TOKEN_FILE"
else
    if [ -n "$PIPELINES_API_KEY" ]; then
        if [ "$PIPELINES_API_KEY" = "ai-local-secure-key" ]; then
            warn "Token di default rilevato. Lo rendo persistente in $TOKEN_FILE."
            echo "$PIPELINES_API_KEY" > "$TOKEN_FILE"
            chmod 600 "$TOKEN_FILE"
        else
            echo "$PIPELINES_API_KEY" > "$TOKEN_FILE"
            chmod 600 "$TOKEN_FILE"
            info "Token da ambiente persistito in $TOKEN_FILE"
        fi
    else
        PIPELINES_API_KEY=$(generate_token)
        echo "$PIPELINES_API_KEY" > "$TOKEN_FILE"
        chmod 600 "$TOKEN_FILE"
        success "Nuovo token generato e salvato in $TOKEN_FILE"
        warn "⚠️  IMPORTANTE: configura manualmente questa chiave in OpenWebUI:"
        info "   1. Vai su Admin Panel → Settings → Connections"
        info "   2. Modifica la connessione esistente con:"
        info "      - URL: http://ai-pipelines-session:9099"
        info "      - Autenticazione: Bearer"
        info "      - API Key: $PIPELINES_API_KEY"
        info "      - Provider Type: OpenAI"
        info "      - API Type: Chat Completions"
        info "   3. Salva e ricarica le pipeline."
    fi
fi

export PIPELINES_API_KEY

# ------------------------------------------------------------
# Funzioni di pulizia
# ------------------------------------------------------------
stop_containers() {
    info "Arresto dei container Docker..."
    for container in "$OLLAMA_CONTAINER" "$QDRANT_CONTAINER" "$PIPELINES_CONTAINER" "$WEBUI_CONTAINER"; do
        docker stop "$container" 2>/dev/null || true
    done
}

kill_rag_services() {
    info "Terminazione eventuali processi RAG service residui..."
    pkill -f "rag_service.py" 2>/dev/null || true
}

cleanup() {
    echo ""
    warn "Interruzione — fermando servizi..."
    kill_rag_services
    stop_containers
    exit 0
}
trap cleanup SIGINT SIGTERM

# Pulizia iniziale: assicuriamoci che non ci siano residui di esecuzioni precedenti
stop_containers
kill_rag_services

# ------------------------------------------------------------
# Funzioni di utilità
# ------------------------------------------------------------
ensure_container() {
    local name="$1"; shift
    if docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
        docker start "$name" >/dev/null 2>&1
        info "Container ${name} avviato (esistente)"
    else
        docker run -d --name "$name" --restart no "$@" >/dev/null
        success "Container ${name} creato e avviato"
    fi
    docker network inspect "$NETWORK" \
        --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null \
        | grep -q "$name" || docker network connect "$NETWORK" "$name" 2>/dev/null || true
}

mount_external_disk() {
    header "0️⃣  SSD Esterno"
    mountpoint -q "$EXTERNAL_DISK_MOUNT" && success "SSD esterno già montato" && return 0
    mount "$EXTERNAL_DISK_MOUNT" 2>/dev/null && success "SSD esterno montato" || \
        warn "SSD esterno non montato"
}

setup_zram() {
    header "0️⃣  zram swap"
    zramctl 2>/dev/null | grep -q "/dev/zram" && success "zram già attivo" && return 0
    systemctl list-unit-files 2>/dev/null | grep -q zramswap && \
        { sudo systemctl start zramswap && success "zramswap avviato"; } || \
        warn "zramswap non configurato"
}

# ------------------------------------------------------------
# Avvio dello stack
# ------------------------------------------------------------
header "▶  ORCHESTRA 8GB v3.7 — Avvio stack (nessuna persistenza dopo l'uscita)"

mount_external_disk
setup_zram

header "0️⃣  Directory"
mkdir -p "$LOGS_DIR" "$PIPELINES_DIR" "$RAG_DIR"
for d in "${DOMAIN_DIRS[@]}"; do mkdir -p "$DOCS_ROOT/$d"; done
success "Struttura directory pronta"

header "0️⃣  Cleanup ComfyUI"
COMFYUI_PID=$(pgrep -f "python main.py" 2>/dev/null || true)
[ -n "$COMFYUI_PID" ] && {
    kill "$COMFYUI_PID" 2>/dev/null || true; sleep 2
    kill -9 "$COMFYUI_PID" 2>/dev/null || true
}
ss -tlnp | grep -q ":${COMFYUI_PORT}" && { error "Porta ${COMFYUI_PORT} occupata"; exit 1; }
rm -f "${COMFYUI_DIR}/user/comfyui.db-wal" "${COMFYUI_DIR}/user/comfyui.db-shm" 2>/dev/null || true

header "1️⃣  Rete Docker"
docker network ls --format '{{.Name}}' | grep -q "^${NETWORK}$" || \
    docker network create "$NETWORK"
success "Rete ${NETWORK} pronta"

header "2️⃣  Ollama (127.0.0.1:${OLLAMA_PORT})"
ensure_container "$OLLAMA_CONTAINER" \
    --network "$NETWORK" --gpus all \
    -v ollama-session:/root/.ollama \
    -p "127.0.0.1:${OLLAMA_PORT}:11434" \
    -e OLLAMA_HOST=0.0.0.0:11434 \
    -e OLLAMA_KEEP_ALIVE=0 \
    -e OLLAMA_MAX_LOADED_MODELS=1 \
    -e OLLAMA_NUM_PARALLEL=1 \
    -e OLLAMA_MAX_QUEUE=10

OLLAMA_READY=false
for i in $(seq 1 30); do
    curl -sf "http://127.0.0.1:${OLLAMA_PORT}/" >/dev/null 2>&1 && OLLAMA_READY=true && break
    sleep 2
done
if [ "$OLLAMA_READY" = true ]; then
    success "Ollama pronto"
    for model in "${REQUIRED_MODELS[@]}"; do
        model_name="${model%%:*}"
        docker exec "$OLLAMA_CONTAINER" ollama list 2>/dev/null | \
            grep -q "$model_name" || docker exec "$OLLAMA_CONTAINER" ollama pull "$model"
    done
else
    warn "Ollama non risponde — continuo"
fi

header "3️⃣  Qdrant (127.0.0.1:${QDRANT_PORT})"
ensure_container "$QDRANT_CONTAINER" \
    --network "$NETWORK" \
    -v qdrant-data:/qdrant/storage \
    -p "127.0.0.1:${QDRANT_PORT}:6333"

QDRANT_READY=false
for i in $(seq 1 15); do
    curl -sf "http://127.0.0.1:${QDRANT_PORT}/healthz" >/dev/null 2>&1 && \
        QDRANT_READY=true && break
    sleep 2
done
[ "$QDRANT_READY" = true ] && success "Qdrant pronto" || warn "Qdrant non risponde"

header "4️⃣  Pipelines (127.0.0.1:${PIPELINES_PORT})"
ensure_container "$PIPELINES_CONTAINER" \
    --gpus all \
    -v "${PIPELINES_DIR}:/app/pipelines" \
    -v "${LOGS_DIR}:/app/logs" \
    -v "${DOCS_ROOT}:/app/document-ai" \
    -v /usr/bin/nvidia-smi:/usr/bin/nvidia-smi:ro \
    -p "127.0.0.1:${PIPELINES_PORT}:9099" \
    -e PIPELINES_API_KEY="$PIPELINES_API_KEY" \
    -e PATTERN_LOG_PATH="/app/logs/patterns.jsonl" \
    -e DOCS_ROOT="/app/document-ai" \
    -e PIPELINES_REQUIREMENTS_PATH="/app/pipelines/requirements.txt"

success "Pipelines pronto"

header "5️⃣  OpenWebUI (192.168.1.51:${WEBUI_PORT})"
ensure_container "$WEBUI_CONTAINER" \
    -v webui-session:/app/backend/data \
    -p "192.168.1.51:${WEBUI_PORT}:8080" \
    -e ENABLE_PIPELINES=true \
    -e PIPELINES_API_KEY="$PIPELINES_API_KEY" \
    -e OLLAMA_BASE_URL="http://${OLLAMA_CONTAINER}:11434" \
    -e PIPELINES_URL="http://${PIPELINES_CONTAINER}:${PIPELINES_PORT}" \
    -e OPENAI_API_BASE_URLS="http://ai-pipelines-session:9099" \
    -e OPENAI_API_KEYS="$PIPELINES_API_KEY" \
    -e COMFYUI_BASE_URL="http://172.17.0.1:${COMFYUI_PORT}" \
    -e WEBUI_SECRET_KEY="$(cat ~/ai-sessioni/.webui_secret_key)" \
    ghcr.io/open-webui/open-webui:main

success "OpenWebUI pronto"

# Auto-allineamento token pipelines nel database OpenWebUI
info "Attendo avvio OpenWebUI..."
for i in $(seq 1 30); do
    curl -sf "http://192.168.1.51:3001/health" >/dev/null 2>&1 && break
    sleep 2
done
sleep 5
_TOKEN=$(cat "$TOKEN_FILE")
docker exec "$WEBUI_CONTAINER" python3 -c "
import sqlite3, json
conn = sqlite3.connect('/app/backend/data/webui.db')
cur = conn.cursor()
cur.execute('SELECT data FROM config WHERE id=1')
row = cur.fetchone()
if row:
    data = json.loads(row[0])
    openai_cfg = data.get('openai', {})
    keys = openai_cfg.get('api_keys', [])
    urls = openai_cfg.get('api_base_urls', [])
    token = '${_TOKEN}'
 
    # Costruisce il set di chiavi legittime:
    # - token corrente (posizione 0, per Pipelines)
    # - tutte le chiavi in posizione >= 1 che corrispondono a un URL (es. DeepSeek)
    # Le chiavi orfane (senza URL corrispondente) vengono rimosse.
    legitimate = []
 
    # Posizione 0: sempre il token Pipelines corrente
    legitimate.append(token)
 
    # Posizioni 1+: mantieni solo le chiavi che hanno un URL associato
    for i, url in enumerate(urls[1:], start=1):
        if i < len(keys) and keys[i] and keys[i] != token:
            legitimate.append(keys[i])
 
    if keys != legitimate:
        print(f'[ORCHESTRA] Token prima: {keys}')
        print(f'[ORCHESTRA] Token dopo:  {legitimate}')
        data['openai']['api_keys'] = legitimate
        cur.execute('UPDATE config SET data=? WHERE id=1', (json.dumps(data),))
        conn.commit()
        print('[ORCHESTRA] Token pipelines allineato nel DB OpenWebUI')
    else:
        print('[ORCHESTRA] Token già allineato — nessuna modifica')
conn.close()
" && success "Token pipelines allineato" || warn "Auto-allineamento token fallito — verifica manualmente"

header "6️⃣  RAG Service (127.0.0.1:${RAG_SERVICE_PORT})"
if [ ! -f "$RAG_DIR/rag_service.py" ]; then
    warn "rag_service.py non trovato — RAG service non avviato"
else
    if ss -tlnp | grep -q "127.0.0.1:${RAG_SERVICE_PORT}"; then
        warn "Porta ${RAG_SERVICE_PORT} già in uso"
    else
        cd "$RAG_DIR"
        source "${COMFYUI_DIR}/venv/bin/activate"
        RAG_DOCS_DIR="$DOCS_ROOT" \
        QDRANT_URL="http://127.0.0.1:${QDRANT_PORT}" \
        RAG_SERVICE_PORT="$RAG_SERVICE_PORT" \
        RAG_SERVICE_HOST="0.0.0.0" \
        DEPLOY_TOKEN="$PIPELINES_API_KEY" \
        nohup python rag_service.py > "${LOGS_DIR}/rag_service.log" 2>&1 &
        RAG_SERVICE_PID=$!
        sleep 2
        if kill -0 "$RAG_SERVICE_PID" 2>/dev/null; then
            success "RAG Service avviato (PID: ${RAG_SERVICE_PID})"
        else
            warn "RAG Service non avviato — controlla: tail -20 ${LOGS_DIR}/rag_service.log"
            RAG_SERVICE_PID=""
        fi
        cd "$HOME"
    fi
fi

# Indicizzazione automatica se Qdrant è vuoto
if [ -n "$RAG_SERVICE_PID" ]; then
    for i in $(seq 1 10); do
        curl -sf "http://127.0.0.1:${RAG_SERVICE_PORT}/health" >/dev/null 2>&1 && break
        sleep 2
    done
    if curl -sf "http://127.0.0.1:${RAG_SERVICE_PORT}/status" | grep -q '"total_chunks":0'; then
        info "RAG: nessun documento indicizzato, avvio indicizzazione in background..."
        curl -X POST "http://127.0.0.1:${RAG_SERVICE_PORT}/index" > /dev/null 2>&1 &
    fi
fi

header "7️⃣  NPM — verifica"
if curl -sf http://192.168.1.101:81 >/dev/null 2>&1; then
    success "NPM raggiungibile su rasdom1-pi4"
else
    warn "NPM non raggiungibile — verifica rasdom1-pi4:81"
fi

header "8️⃣  ComfyUI (host, foreground)"
echo ""
success "Stack completo:"
info "  OpenWebUI  → https://orchestra.tregambe.com"
info "  ComfyUI    → http://127.0.0.1:${COMFYUI_PORT}"
info "  Qdrant     → http://127.0.0.1:${QDRANT_PORT}"
info "  RAG API    → http://127.0.0.1:${RAG_SERVICE_PORT}/health"
echo ""

cd "$COMFYUI_DIR"
source venv/bin/activate
export OLLAMA_HOST="http://127.0.0.1:${OLLAMA_PORT}"

# export OLLAMA_HOST="http://172.17.0.1:${OLLAMA_PORT}"


python main.py \
    --listen 0.0.0.0 --port "$COMFYUI_PORT" \
    --force-fp16 --dont-upcast-attention \
    --normalvram \
    --preview-method auto --cpu-vae

# Quando ComfyUI termina, lo script arriva qui e poi esegue il trap cleanup.
# I container e il RAG service vengono fermati dalla funzione cleanup.

```

---

