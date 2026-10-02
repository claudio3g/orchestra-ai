# AI Context - Core

> Generato: 2026-10-02T19:27:33Z
> Branch: main

---

## File: .github/workflows/ai-commit.yml (8448 byte)

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

## File: .gitignore (1321 byte)

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
!/AI_READ_PROTOCOL.md
!/AI_CTX_core.md
!/AI_CTX_rag.md
!/AI_CTX_pipelines.md
!/AI_CTX_scripts.md
!/AI_CTX_config.md
!/AI_CTX_knowledge_index.md
```

## File: README.it.md (14171 byte)

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

## File: README.md (20043 byte)

```
# Orchestra AI

> 🇮🇹 [Versione italiana](README.it.md)

**Orchestra** is a self-hosted, local-first AI workspace built around **Ollama, Open WebUI, custom Pipelines, RAG and Qdrant**.

The project is designed to run AI services locally, keep the user's knowledge base under local control, and combine general-purpose LLMs, document retrieval, custom processing pipelines and persistent vector storage in a single environment.

> **Status:** active personal/local project  
> **Primary target:** Linux + NVIDIA GPU + Docker  
> **Repository:** https://github.com/claudio3g/orchestra-ai

---

## Overview

Orchestra combines several open-source components into one local AI stack:

```text
                         ┌─────────────────────┐
                         │     Open WebUI       │
                         │   User interface     │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │      Pipelines       │
                         │ custom AI processing  │
                         └──────────┬──────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
          ┌─────────▼─────────┐           ┌────────▼────────┐
          │      Ollama       │           │   RAG Service   │
          │ Local LLM runtime │           │ document search │
          └─────────┬─────────┘           └────────┬────────┘
                    │                              │
                    │                       ┌──────▼──────┐
                    │                       │   Qdrant    │
                    │                       │ vector DB   │
                    │                       └─────────────┘
                    │
             Local AI models
```

The main components are intentionally separated:

- **Open WebUI** provides the user-facing interface.
- **Ollama** runs local language and vision models.
- **Pipelines** provides project-specific AI processing and routing.
- **RAG Service** manages document ingestion and retrieval.
- **Qdrant** stores vectorized document data.
- **document-ai** contains the knowledge base and related configuration.
- **start_ai_stack.sh** orchestrates the local environment.

---

## Main features

### Local LLM inference

Orchestra uses Ollama as its local model runtime.

The current launcher defines the following models:

- `llama3.2:3b`
- `qwen3.5:9b`
- `llama3.1:8b`
- `qwen2.5-coder:14b-instruct-q4_K_M`
- `llava:7b`
- `moondream:v2`

Models are stored in a persistent Docker volume so they do not need to be downloaded on every startup.

---

### Custom AI Pipelines

The project contains custom pipeline components under:

```text
ollama/pipelines/
```

including:

```text
embedding_utils.py
image_loop.py
orchestra_evolver.py
orchestra_manifold.py
pattern_logger.py
rag_filter.py
```

These components extend the standard Ollama/Open WebUI workflow with project-specific processing.

The Pipelines service is exposed locally on port `9099`.

---

### RAG and knowledge base

Orchestra includes a dedicated RAG service.

The knowledge base is stored under:

```text
document-ai/
```

with the knowledge documents located under:

```text
document-ai/knowledge/
```

The repository includes support for a mixed knowledge base containing, for example:

- PDF
- DOCX
- XLSX
- Markdown
- text and other document sources

The RAG service communicates with Qdrant for vector storage and retrieval.

The main RAG components are located under:

```text
rag/
```

including:

```text
rag_service.py
rag_indexer_lib.py
requirements.txt
```

The service exposes its local API on port `6335`.

---

## Knowledge indexing

At startup, Orchestra checks the RAG service and its Qdrant collection.

If the RAG service is available and no document chunks are currently indexed, the launcher automatically starts the indexing process.

The relevant flow is:

```text
Documents
    │
    ▼
document-ai/
    │
    ▼
RAG Service
    │
    ▼
Indexing / embeddings
    │
    ▼
Qdrant
    │
    ▼
Semantic retrieval
    │
    ▼
AI pipeline / model
```

This allows the local AI system to use the user's own documentation and technical knowledge as contextual information.

---

## Docker architecture

The project uses Docker containers for the principal AI services.

### Ollama

```text
Container: ai-ollama-session
Host port: 11435
Container port: 11434
```

Persistent model storage:

```text
ollama-session:/root/.ollama
```

### Open WebUI

```text
Container: ai-webui-session
Host port: 3001
Container port: 8080
```

Persistent application data:

```text
webui-session:/app/backend/data
```

### Pipelines

```text
Container: ai-pipelines-session
Host port: 9099
Container port: 9099
```

The pipeline container receives access to:

- pipeline source files
- logs
- document-ai
- NVIDIA GPU runtime

### Qdrant

```text
Container: ai-qdrant-session
Host port: 6333
```

Persistent vector database storage:

```text
qdrant-data:/qdrant/storage
```

### RAG Service

```text
Host: 127.0.0.1
Port: 6335
```

The RAG service is implemented in Python and can also be run independently from its Docker Compose definition.

---

## Startup architecture

The main entry point is:

```text
start_ai_stack.sh
```

The launcher performs the following operations:

1. Stops previous Orchestra containers.
2. Terminates residual RAG processes.
3. Checks/mounts the configured external SSD.
4. Checks the local zram configuration.
5. Creates required directories.
6. Creates the Docker network when required.
7. Starts Ollama.
8. Waits for Ollama to become available.
9. Downloads missing required models.
10. Starts Qdrant.
11. Starts the Pipelines container.
12. Starts Open WebUI.
13. Synchronizes the Pipelines API token with Open WebUI.
14. Starts the RAG service.
15. Checks RAG health.
16. Automatically starts indexing when the vector database is empty.

The launcher also performs cleanup when interrupted.

---

## Security model

Orchestra is designed primarily for local use.

The current configuration includes several local-only bindings:

```text
127.0.0.1:11435
127.0.0.1:6333
127.0.0.1:6335
```

The Pipelines service uses a generated persistent API token stored in:

```text
.orchestra_token
```

The launcher creates the token with:

```text
openssl rand -hex 32
```

and restricts the token file to the user with:

```text
chmod 600
```

The token is then used for authenticated communication between Open WebUI and Pipelines.

### Important

Do not commit the following files to a public repository:

```text
.orchestra_token
.webui_secret_key
```

Any existing secrets should be regenerated if they have ever been exposed publicly.

---

## Directory structure

The relevant project structure is:

```text
orchestra-ai/
│
├── document-ai/
│   ├── config/
│   ├── knowledge/
│   ├── routing_snapshots/
│   └── scripts/
│
├── ollama/
│   ├── docker-compose.yml
│   ├── Modelfile-blender
│   └── pipelines/
│       ├── embedding_utils.py
│       ├── image_loop.py
│       ├── orchestra_evolver.py
│       ├── orchestra_manifold.py
│       ├── pattern_logger.py
│       ├── rag_filter.py
│       └── requirements.txt
│
├── rag/
│   ├── Dockerfile
│   ├── docker-compose.prod.yml
│   ├── rag_service.py
│   ├── rag_indexer_lib.py
│   └── requirements.txt
│
├── workflows/
│
├── logs/
│
├── start_ai_stack.sh
├── start_comfyui.sh
└── README.md
```

---

## GPU support

Orchestra is designed to use NVIDIA GPUs through Docker/NVIDIA Container Toolkit.

The current launcher exposes the available NVIDIA GPUs to the Ollama and Pipelines containers.

The current development hardware uses:

- **NVIDIA GeForce RTX 3090 — 24 GB VRAM**
- **NVIDIA GeForce RTX 4060 Laptop GPU — 8 GB VRAM**

The intended architecture is to use the RTX 3090 as the primary high-VRAM AI accelerator while retaining the RTX 4060 for smaller models and secondary workloads.

GPU-specific model/process routing is an active area of development and should not be assumed to be fully automatic unless explicitly configured in the current launcher.

---

## Networking

The main Docker network used by the launcher is:

```text
ollama_default
```

The services communicate internally using their container names.

Examples:

```text
http://ai-ollama-session:11434
http://ai-pipelines-session:9099
```

The RAG service communicates with Qdrant through:

```text
http://ai-qdrant-session:6333
```

---

## Environment and runtime requirements

The current stack is intended for a Linux workstation with:

- Linux
- Docker
- Docker Compose
- NVIDIA driver
- NVIDIA Container Toolkit
- NVIDIA GPU
- Python 3
- `curl`
- `openssl`
- `zramctl` (optional)
- sufficient RAM and storage for local models and documents

The exact versions are intentionally not hard-coded in this README because they depend on the host installation.

---

## Starting Orchestra

From the repository root:

```bash
cd ~/ai-sessioni
./start_ai_stack.sh
```

The script is designed to manage the required containers itself.

Check the services with:

```bash
docker ps
```

Check Ollama:

```bash
curl http://127.0.0.1:11435/
```

Check Qdrant:

```bash
curl http://127.0.0.1:6333/healthz
```

Check RAG:

```bash
curl http://127.0.0.1:6335/health
```

---

## Model management

The launcher checks the required model list and pulls missing models automatically.

To inspect installed models:

```bash
docker exec ai-ollama-session ollama list
```

To manually pull a model:

```bash
docker exec ai-ollama-session ollama pull <model>
```

---

## Logs

Project logs are stored under:

```text
logs/
```

The RAG service log is:

```text
logs/rag_service.log
```

Pipeline pattern logging is stored in:

```text
logs/patterns.jsonl
```

---

## Configuration files

Important configuration files include:

```text
ollama/docker-compose.yml
rag/docker-compose.prod.yml
document-ai/config/docker-compose.yml
start_ai_stack.sh
```

The main runtime configuration is currently centralized in `start_ai_stack.sh`.

---

## ComfyUI scope

ComfyUI exists in the wider local environment, but it is **not part of the current Orchestra AI core scope**.

The Orchestra architecture documented here focuses on:

- Ollama
- Open WebUI
- Pipelines
- RAG
- Qdrant
- document-ai
- local knowledge management

ComfyUI should therefore be considered an external/legacy integration and is not required to understand or operate the core Orchestra AI architecture.

---

## Design principles

Orchestra follows these principles:

### Local-first

Models, documents, embeddings and vector data are intended to remain under local control whenever possible.

### Modular

Each major capability is separated into an independently manageable component.

### Persistent knowledge

The knowledge base and vector database persist independently from individual AI sessions.

### Reproducible startup

The launcher attempts to bring the complete environment to a known state instead of requiring every container to be started manually.

### Resource-aware

The system is designed for workstation-class hardware rather than requiring a cloud AI infrastructure.

### Extensible

New models, pipelines, document processors and AI workflows can be added without replacing the entire stack.

---

## Current development direction

The project is evolving toward a multi-GPU local AI architecture.

The target architecture is:

```text
                    ORCHESTRA
                        │
          ┌─────────────┴─────────────┐
          │                           │
   RTX 3090 — 24 GB              RTX 4060 — 8 GB
   Primary AI workloads          Secondary workloads
          │                           │
   Large / reasoning             Small / lightweight
   coding models                 agents and models
   RAG-heavy tasks               auxiliary processing
```

The goal is to exploit both GPUs rather than treating the RTX 4060 as a fallback device.

GPU-to-model and GPU-to-agent routing will be implemented explicitly so that high-VRAM workloads can be kept on the RTX 3090 while smaller workloads can use the RTX 4060.

---

## Roadmap

Planned or ongoing work includes:

- explicit multi-GPU model routing
- GPU-aware agent assignment
- improved agent/model orchestration
- continued RAG optimization
- improved document ingestion
- resource-aware model loading
- better observability and logging
- further separation between core Orchestra services and optional integrations

---

## Project philosophy

Orchestra is not intended to be another hosted AI service.

The objective is to build a **private, local AI environment** in which:

```text
models
documents
knowledge
retrieval
agents
pipelines
and infrastructure
```

remain under the user's control.

The project favors open-source components, local execution, modular architecture and the ability to replace individual components without rebuilding the whole system.
---

## AI Workflow — Automatic commits

This repository supports **automatic AI-generated commits** via GitHub Actions `repository_dispatch`. The system is designed to be safe, verifiable, and non-destructive.

### Flow

1. The AI generates a patch in diff format.
2. The patch is sent via `curl` to the GitHub API.
3. The `ai-commit.yml` workflow is triggered:
   - **Job `validate`**: checks that the payload is valid.
   - **Job `sandbox-test`**: applies the patch in a sandbox and runs tests.
   - **Job `commit-push`**: if tests pass, commits and pushes.
4. The commit appears on GitHub with the author `github-actions[bot]`.

### Manual trigger

    ~/ai-dispatch.sh <patch.diff> "<commit message>" [branch]

Example:

    ~/ai-dispatch.sh /tmp/ai_patch.diff "docs: update README" main

### Safeguards

- The patch is **validated** before being applied:
  - non-empty payload
  - valid base64
  - correct diff format (`diff --git` as first line)
  - `git apply --check` (dry-run) without conflicts
- Tests run in an isolated **sandbox** on `ubuntu-24.04`.
- The commit happens **only** if all tests pass.
- If tests fail, the repository remains unchanged.

### Tests run by the workflow

| Test | What it checks | Blocking |
|------|----------------|----------|
| Python syntax | `python -m py_compile` on all `.py` | yes |
| Bash syntax | `bash -n` on all `.sh` | yes |
| YAML syntax | parsing with `pyyaml` | yes |
| JSON syntax | parsing with `json` | yes |
| Shellcheck | critical errors in bash scripts | no (warning) |

### Files involved

- **Workflow**: `.github/workflows/ai-commit.yml`
- **Local script**: `~/ai-dispatch.sh`
- **GitHub token**: `~/.orchestra_github_token` (fine-grained PAT, `contents:write` permission)

---

## RAG Service API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Service status, deps, embed model |
| `/status` | GET | Collection statistics (`total_chunks`, `by_domain`) |
| `/index` | POST | Start indexing (async) |
| `/vram` | GET | Free VRAM (multi-GPU with main/aux roles) |
| `/deploy` | POST | Safe file deployment (whitelist) |

Examples:

    curl -s http://127.0.0.1:6335/health | jq
    curl -s http://127.0.0.1:6335/status | jq
    curl -s http://127.0.0.1:6335/vram | jq

---

## RAG Collection Status

Current `total_chunks: 719` distribution by domain:

| Domain | Chunks |
|--------|--------|
| knowledge | 355 |
| system | 163 |
| routing_snapshots | 157 |
| scripts | 34 |
| config | 10 |

---

## Active branches

| Branch | Purpose | Status |
|--------|---------|--------|
| `main` | main stable line | active |
| `dual-gpu-step1` | dual-GPU migration 3090 + 4060 | in development |

---

## Troubleshooting

### `git status` shows `rag/.file_hash_cache.json` as modified

**Cause:** the RAG service continuously rewrites this cache file. If not ignored by git, it blocks pull/checkout and creates noise.

**Solution (already applied):**

- `.gitignore` contains `rag/.file_hash_cache.json`
- the file has been removed from tracking with `git rm --cached`

If it reappears as modified, check with:

    git check-ignore -v rag/.file_hash_cache.json

It should return the `.gitignore` rule. If it does not, the rule has been lost.

### `git pull` blocked by local modifications

If an error like "Your local changes would be overwritten" appears, it means a tracked file has been modified at runtime. The typical candidate is the RAG cache (see above). Other times it may be a log file that should not be tracked.

### AI workflow fails with "payload empty"

The `validate` job rejected the patch because it is empty. Check the `/tmp/ai_patch.diff` file: if it is 0 bytes, regenerate it.

### AI workflow fails on "git apply --check"

The patch is not applicable to the current state of the branch. Typical causes:

- the branch has advanced after the patch was generated
- the context of the modified lines no longer matches

Regenerate the patch from the updated branch.

### The RAG service does not restart

    cd ~/ai-sessioni
    nohup python rag_service.py > logs/rag_service.log 2>&1 &
    sleep 3
    ps aux | grep rag_service | grep -v grep
    curl -s http://127.0.0.1:6335/health

Check the log in `logs/rag_service.log`.

---

## Reference for AI agents

This section is meant to be read by an AI agent that must operate on the repository without human context.

### Real paths

| Item | Path |
|------|------|
| Local repository | `/home/claudio/ai-sessioni` |
| Remote | `git@github.com:claudio3g/orchestra-ai.git` |
| AI workflow | `.github/workflows/ai-commit.yml` |
| Dispatch script | `~/ai-dispatch.sh` |
| GitHub token | `~/.orchestra_github_token` |
| RAG service | `rag/rag_service.py` |
| Knowledge base | `document-ai/knowledge/` |

### Conventions

- **DO NOT** assume GPU routing is automatic: it is in development.
- **Always read** `start_ai_stack.sh` for the updated runtime configuration.
- **Verify** the presence of `~/.orchestra_github_token` before calling `ai-dispatch.sh`.
- **Do not commit**: `~/.orchestra_github_token`, `.orchestra_token`, `.webui_secret_key`, `rag/.file_hash_cache.json`.
- **Respect** local bindings (`127.0.0.1` for most services).

### Useful commands

    # Repository status
    cd ~/ai-sessioni && git status && git log --oneline -5

    # Test AI flow (empty patch, must fail in validate)
    > /tmp/ai_patch.diff
    ~/ai-dispatch.sh /tmp/ai_patch.diff "test validation"

    # Verify RAG
    curl -s http://127.0.0.1:6335/health | jq
    curl -s http://127.0.0.1:6335/status | jq

    # Full backup
    git bundle create ~/orchestra-backup-$(date +%F_%H%M).bundle --all

### What NOT to do

- Do NOT run `git reset --hard` without a backup.
- Do NOT run `git push --force` (use `--force-with-lease` if necessary).
- Do NOT commit cache files (`file_hash_cache.json`, `__pycache__`, `*.pyc`).
- Do NOT modify `.gitignore` without checking the impact on the whitelist.
- Do NOT expose services publicly without modifying the bindings.

---

## License

No license is currently specified in this README.

If the repository is intended for public reuse, add an explicit license file before publishing a stable release.
```

## File: start_ai_stack.sh (13391 byte)

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

## File: start_comfyui.sh (266 byte)

```
#!/bin/bash
cd ~/ai-sessioni/ComfyUI
source venv/bin/activate
export OLLAMA_HOST=http://host.docker.internal:11435
python main.py \
  --listen 0.0.0.0 \
  --port 8188 \
  --force-fp16 \
  --dont-upcast-attention \
  --lowvram \
  --preview-method auto \
  --cpu-vae
```

