# AI Context - Core

> Generato: 2026-10-08T06:39:58Z
> Branch: dual-gpu-final

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

## File: .gitignore (1446 byte)

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
!/tests/
!/docs/

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
!/ollama/Modelfile-orchestra
!ollama/pipelines/orchestra_bootstrap
!ollama/pipelines/orchestra_bootstrap.py
```

## File: README.it.md (22077 byte)

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
- **Docker** (Compose opzionale: il launcher usa `docker run`)
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
5. Rileva i ruoli GPU (`main` = 3090, `aux` = 4060, per UUID) e, se richiesto, applica un profilo di potenza.
6. Avvia Ollama main fissato alla 3090 (ricreato se il container era vecchio), attende che risponda e scarica i modelli mancanti (il 32b solo con ≥ 20 GB sulla main).
7. Avvia Ollama aux fissato alla 4060 con i soli modelli leggeri (coordinator + vision).
8. Avvia Qdrant, Pipelines (ricreato se cambiano le variabili GPU), Open WebUI.
9. Allinea il token Pipelines nel database di Open WebUI.
10. Avvia il servizio RAG (nel venv di ComfyUI) e, se la collezione Qdrant è vuota, lancia l'indicizzazione.
11. Avvia ComfyUI in primo piano, fissato alla GPU del ruolo `ORCHESTRA_COMFY_ROLE`.

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

Il portatile ha **due GPU che cooperano** in un sistema multi-agente (non sono alternative):

| Ruolo | GPU | VRAM | Collegamento | Ospita |
|-------|-----|------|--------------|--------|
| `main` | RTX 3090 | 24 GB | eGPU AOOSTAR AG02 su Thunderbolt 4 (PCIe x4) | agenti specialisti, quality 14B, 32B (≥ 20 GB), refine, SDXL (default) |
| `aux` | RTX 4060 Laptop | 8 GB (≈ 7 GB liberi) | interna | coordinator `llama3.2:3b`, vision (`llava:7b`, `moondream:v2`) |

### Come funziona la cooperazione

- **Ruoli per UUID.** `ORCHESTRA_GPU_MAIN` / `ORCHESTRA_GPU_AUX` (UUID da `nvidia-smi -L`); il launcher li rileva (main = più VRAM) e li esporta. Gli indici non si usano: cambiano se la eGPU viene ricollegata e CUDA ordina i dispositivi "più veloce prima".
- **Un Ollama per GPU.** `ai-ollama-session` (main, porta 11435) e `ai-ollama-aux-session` (aux, porta 11436), ciascuno fissato con `--gpus device=<UUID>` e con volume modelli proprio. Un'unica istanza su due GPU spezzerebbe i layer sul link Thunderbolt.
- **Backend per modello.** Manifold e `image_loop` inviano all'Ollama aux i modelli in `aux_models` (coordinator e vision) e tutto il resto al main. Il main conserva **tutti** i modelli: se l'aux è irraggiungibile la richiesta viene rifatta sul main automaticamente.
- **Modelli adattivi.** Il modello quality gira interamente in GPU quando il main ha ≥ 11000 MB liberi (nessun offload su CPU); con GPU piccole il comportamento da 8 GB resta identico. Le soglie della vision usano la VRAM della GPU dove gira la vision.
- **Keep-alive per ruolo** (i modelli aux restano pronti; i modelli di testo del main restano caricati con VRAM abbondante) e, su Ollama, flash attention con cache KV `q8_0` (`ORCHESTRA_FLASH_ATTENTION=0` la disattiva). Con ≥ 20 GB sul main possono restare caricati due modelli.
- **Generazione immagini.** ComfyUI è fissato alla GPU di `ORCHESTRA_COMFY_ROLE` (default `main`). Con VRAM abbondante, o con ComfyUI e LLM su GPU diverse, SDXL resta caricato tra un draft e l'altro; vision e refine vengono pre-caricati in parallelo al primo draft; un LLM grande residente (es. il 32B) viene scaricato per fare spazio a SDXL quando serve.
- **Degrado.** Con la eGPU scollegata la 4060 diventa `main`, non c'è Ollama aux e vale il comportamento da 8 GB.

### Configurazione

Tutte le variabili sono opzionali; si impostano in `orchestra.env` (ignorato da git, vedi `document-ai/config/orchestra.env.example`).

| Variabile | Default | Effetto |
|-----------|---------|---------|
| `ORCHESTRA_GPU_MAIN`, `ORCHESTRA_GPU_AUX` | rilevate | UUID delle due GPU |
| `ORCHESTRA_AUX_OLLAMA` | `1` | `0` = nessun Ollama sulla 4060 (es. 4060 solo per ComfyUI) |
| `ORCHESTRA_COMFY_ROLE` | `main` | GPU di ComfyUI/SDXL: `main` o `aux` |
| `ORCHESTRA_FLASH_ATTENTION`, `ORCHESTRA_KV_CACHE_TYPE` | `1`, `q8_0` | flash attention e tipo di cache KV di Ollama |
| `ORCHESTRA_POWER_PROFILE` | non impostata | `eco` / `balanced` / `performance` all'avvio |
| `COMFY_EXTRA_ARGS` | non impostata | argomenti aggiuntivi per ComfyUI |
| `ORCHESTRA_CONTEXT_LENGTH` | `8192` | contesto predefinito di Ollama (`OLLAMA_CONTEXT_LENGTH`), uguale al `context_length` del manifold. La cache KV cresce di `contesto x richieste parallele`: un default più grande può spostare layer su CPU anche con 24 GB |
| `ORCHESTRA_PULL_IMAGES` | `0` | `1` scarica l'ultima immagine Ollama all'avvio e ricrea i container Ollama creati con la vecchia (i modelli nuovi possono richiedere un Ollama recente: `412 ... requires a newer version`) |
| `ORCHESTRA_RECREATE_OLLAMA` | `0` | `1` forza una volta la ricreazione dei container Ollama (i volumi dei modelli non si toccano) |
| `ORCHESTRA_OLLAMA_IMAGE`, `ORCHESTRA_QDRANT_IMAGE`, `ORCHESTRA_PIPELINES_IMAGE` | automatica | immagine usata quando un container viene (ri)creato; di norma quella del container esistente o una già presente in locale. Il container sostituito resta come `<nome>.bak` e viene ripristinato da solo se il nuovo non parte |
| `ORCHESTRA_MAIN_PARALLEL`, `ORCHESTRA_AUX_PARALLEL` | `1` | `OLLAMA_NUM_PARALLEL` di ogni Ollama (la KV cache cresce di `num_ctx x parallel`); misurare prima di alzarlo |
| `ORCHESTRA_HEAVY_MODEL` | non impostata | modello pesante aggiuntivo da scaricare (es. `qwen3.6:27b`); saltato sotto 20 GB, download non fatale |

**Agenti e modelli.** La decisione di progetto (un agente forte sulla 3090, uno strato di agenti piccoli sempre attivo sulla 4060, parallelismo solo per compiti parallelizzabili), i modelli LLM candidati per 24 GB e le raccomandazioni sui modelli ComfyUI per la 3090 sono in `document-ai/knowledge/ARCHITETTURA_AGENTI_E_MODELLI.md`. Versioni, tag e procedure di rollback: `docs/VERSIONING.md`.

`qwen2.5-coder:32b` (≈ 20 GB) si scarica solo se la GPU main ha ≥ 20 GB. `ollama/Modelfile-orchestra` usa `num_ctx 12288` (con 32768 la sola cache KV a f16 aggiungerebbe ≈ 8,6 GB e non entrerebbe nei 24 GB).

### API `/vram`

```bash
curl -s localhost:6335/vram | python3 -m json.tool
```

I campi di primo livello (`vram_free_mb`, `source`, ...) si riferiscono alla GPU `main`; `gpus[]` elenca tutte le GPU con il ruolo; `/vram?gpu=aux` sposta i campi di primo livello sulla GPU aux.

### Note sulla eGPU

- Il link Thunderbolt si comporta come PCIe x4: caricare un modello è più lento, l'inferenza è quasi nativa finché **l'intero modello sta in VRAM**. Evitare l'offload di layer sulla RAM di sistema.
- `pcie.link.gen.current` a riposo mostra Gen 1 (il link scende di frequenza): misurarlo sotto carico.
- Fermare lo stack prima di scollegare l'enclosure.

### Verifica, test e consumi

```bash
bash document-ai/scripts/orchestra_sync.sh [branch-o-tag]      # allinea questa cartella al remoto in sicurezza (prima il backup, nessun lavoro locale perso)
bash document-ai/scripts/egpu_check.sh                      # diagnostica in sola lettura, stampa gli UUID
bash document-ai/scripts/orchestra_smoke_test.sh --load     # ruoli, una GPU per container, la memoria cresce sulla GPU giusta, 100% GPU
bash tests/run_all.sh                                       # 332 controlli simulati (nessuna GPU, Docker o rete toccati)
bash document-ai/scripts/orchestra_bench_models.sh --parallel "1 2 3" MODELLO   # token/s per flusso e totali con N richieste simultanee, VRAM, 100% GPU
bash document-ai/scripts/orchestra_power.sh status          # watt, limiti, P-state per GPU
bash document-ai/scripts/orchestra_power.sh bench eco balanced performance   # token/s, watt medi, token per joule
```

I power limit (`orchestra_power.sh profile eco|balanced|performance`, `restore`) sono percentuali del limite predefinito di ogni GPU (70 / 85 / 100 %), richiedono `sudo -n` e non sono persistenti al riavvio. La generazione di testo è in gran parte limitata dalla banda di memoria, quindi abbassare il limite di solito costa poche prestazioni, ma **va misurato con `bench` prima di adottare un profilo**. I modelli piccoli sulla 4060 evitano inoltre di svegliare la 3090.

### Branch attivi

| Branch | Scopo | Stato |
|--------|-------|-------|
| `main` | linea principale, stabile | attivo |
| `dual-gpu-final` | dual-GPU: ruoli, Ollama aux, routing per ruolo, ComfyUI fissato, consumi, test | in revisione |

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

- **Il routing GPU è configurato dal launcher** (ruoli per UUID, un Ollama per GPU) e dai valve del manifold (`ollama_url_aux`, `aux_models`): eseguire `orchestra_smoke_test.sh` prima di dare per scontato su quale GPU sta un modello.
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

L'architettura multi-GPU è implementata (vedi [GPU e multi-GPU](#gpu-e-multi-gpu)) ed è coperta da una suite di test simulata; resta da validare end-to-end sull'hardware (`orchestra_smoke_test.sh --load`, `orchestra_power.sh bench`).

- Validazione su hardware: isolamento GPU, flash attention con KV `q8_0`, pre-caricamento, profili di potenza, ComfyUI sulla 3090
- Usare il modello 32B (`ollama/Modelfile-orchestra`) per `orchestra_dev` / `reasoner` quando la GPU main è libera
- Metriche per GPU in `/status` e nei log dei pattern
- Spostare i valori specifici della macchina nel launcher (`192.168.1.51`, percorsi) in `orchestra.env`
- Indurire il workflow AI (passare `client_payload` tramite `env:` invece di interpolarlo nello script)
- Ottimizzazione RAG (chunking, batch adattivo) e miglioramento dell'ingestione documenti
- ComfyUI è parte del sistema funzionante (`/generate`, `image_loop`): si tratta come servizio integrato, non come integrazione esterna

---

## Licenza

Progetto personale. Nessuna licenza formale al momento.
```

## File: README.md (27414 byte)

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
Host: 0.0.0.0  (started by the launcher; reachable only from the Docker bridge networks thanks to ufw)
Port: 6335
```

The RAG service is a Python (Flask) process started on the host by the launcher **inside ComfyUI's virtualenv**, so ComfyUI must be installed even if image generation is not used. `rag/docker-compose.prod.yml` describes an alternative containerised run that the launcher does not use.

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

The current configuration includes these local-only bindings:

```text
127.0.0.1:11435   Ollama (main GPU)
127.0.0.1:11436   Ollama (aux GPU)
127.0.0.1:6333    Qdrant
127.0.0.1:9099    Pipelines
```

The RAG service (6335) and ComfyUI (8188) listen on `0.0.0.0` by design, because the containers reach them through the Docker bridge: **the firewall is part of the security model** (`document-ai/config/ufw_rules_export.txt`). Open WebUI is bound to a fixed LAN address in the launcher and can be published through a reverse proxy (`document-ai/scripts/setup_security.sh`), so it should be treated as an internet-facing service.

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

Orchestra uses NVIDIA GPUs through Docker and the NVIDIA Container Toolkit. The development machine has **two GPUs that cooperate** in one multi-agent system (they are not alternatives):

| Role | GPU | VRAM | Link | Hosts |
|------|-----|------|------|-------|
| `main` | RTX 3090 | 24 GB | eGPU AOOSTAR AG02 on Thunderbolt 4 (PCIe x4) | specialist agents, quality 14B, 32B (≥ 20 GB), refine, SDXL (default) |
| `aux` | RTX 4060 Laptop | 8 GB (≈ 7 GB free) | internal | coordinator `llama3.2:3b`, vision (`llava:7b`, `moondream:v2`) |

### How the cooperation works

- **Roles by UUID.** `ORCHESTRA_GPU_MAIN` / `ORCHESTRA_GPU_AUX` (UUIDs from `nvidia-smi -L`). The launcher detects them (main = most VRAM) and exports them. Indexes are never used: they change when the eGPU is re-plugged, and CUDA orders devices "fastest first".
- **One Ollama per GPU.** `ai-ollama-session` (main, port 11435) and `ai-ollama-aux-session` (aux, port 11436), each pinned with `--gpus device=<UUID>` and with its own model volume. One instance spanning both GPUs would split layers over the Thunderbolt link.
- **Backend per model.** The manifold and `image_loop` send the models listed in `aux_models` (coordinator and vision) to the aux Ollama and everything else to the main one. The main Ollama keeps **all** models, so if the aux is unreachable the request is retried on the main automatically.
- **Adaptive models.** The quality model runs fully on GPU when the main has ≥ 11000 MB free (no CPU offload); on small GPUs the previous 8 GB behaviour is unchanged. Vision thresholds use the VRAM of the GPU where vision runs.
- **Keep-alive per role** (aux models stay ready; main text models stay loaded when VRAM is plentiful) and, on Ollama, flash attention with a `q8_0` KV cache (`ORCHESTRA_FLASH_ATTENTION=0` disables it). With ≥ 20 GB on the main, two models can stay loaded.
- **Image generation.** ComfyUI is pinned to the GPU of `ORCHESTRA_COMFY_ROLE` (default `main`). With enough free VRAM, or when ComfyUI and the LLMs are on different GPUs, SDXL stays loaded between drafts; vision and refine models are pre-loaded in parallel with the first draft; a large resident LLM (e.g. the 32B) is unloaded to make room for SDXL when needed.
- **Graceful degradation.** If the eGPU is missing, the 4060 becomes `main`, there is no aux Ollama and the 8 GB behaviour applies.

### Configuration

All variables are optional; set them in `orchestra.env` (git-ignored, see `document-ai/config/orchestra.env.example`).

| Variable | Default | Effect |
|----------|---------|--------|
| `ORCHESTRA_GPU_MAIN`, `ORCHESTRA_GPU_AUX` | auto-detected | GPU UUIDs for the two roles |
| `ORCHESTRA_AUX_OLLAMA` | `1` | `0` = no Ollama on the 4060 (e.g. 4060 only for ComfyUI) |
| `ORCHESTRA_COMFY_ROLE` | `main` | GPU of ComfyUI/SDXL: `main` or `aux` |
| `ORCHESTRA_FLASH_ATTENTION`, `ORCHESTRA_KV_CACHE_TYPE` | `1`, `q8_0` | Ollama flash attention and KV cache type |
| `ORCHESTRA_POWER_PROFILE` | unset | `eco` / `balanced` / `performance` power limits at start |
| `COMFY_EXTRA_ARGS` | unset | extra ComfyUI arguments |
| `ORCHESTRA_CONTEXT_LENGTH` | `8192` | default Ollama context (`OLLAMA_CONTEXT_LENGTH`), equal to the manifold `context_length`. The KV cache grows with `context x parallel requests`: a larger default can push layers to CPU even on 24 GB |
| `ORCHESTRA_PULL_IMAGES` | `0` | `1` pulls the latest Ollama image at start and recreates Ollama containers created with the old one (new models can need a recent Ollama: `412 ... requires a newer version`) |
| `ORCHESTRA_RECREATE_OLLAMA` | `0` | `1` forces recreating the Ollama containers once (model volumes are untouched) |
| `ORCHESTRA_OLLAMA_IMAGE`, `ORCHESTRA_QDRANT_IMAGE`, `ORCHESTRA_PIPELINES_IMAGE` | auto | image used when a container is (re)created; by default taken from the existing container or a local image. A container being replaced is kept as `<name>.bak` and restored automatically if the new one fails to start |
| `ORCHESTRA_MAIN_PARALLEL`, `ORCHESTRA_AUX_PARALLEL` | `1` | `OLLAMA_NUM_PARALLEL` of each Ollama (KV cache grows with `num_ctx x parallel`); measure before raising |
| `ORCHESTRA_HEAVY_MODEL` | unset | extra heavy model to pull (e.g. `qwen3.6:27b`); skipped below 20 GB, download is non-fatal |

**Agents and models.** The design decision (one strong agent on the 3090, an always-on small-agent layer on the 4060, parallelism only for parallelizable work), the current local LLM candidates for 24 GB and the ComfyUI model recommendations for the 3090 are in `document-ai/knowledge/ARCHITETTURA_AGENTI_E_MODELLI.md`. Versions, tags and rollback procedures: `docs/VERSIONING.md`.

`qwen2.5-coder:32b` (≈ 20 GB) is pulled only when the main GPU has ≥ 20 GB. `ollama/Modelfile-orchestra` uses `num_ctx 12288` (a 32768 context would add ≈ 8.6 GB of KV cache at f16 and not fit in 24 GB).

### `/vram` API

```bash
curl -s localhost:6335/vram | python3 -m json.tool
```

Top-level fields (`vram_free_mb`, `source`, ...) refer to the `main` GPU; `gpus[]` lists every GPU with its role; `/vram?gpu=aux` moves the top-level fields to the aux GPU.

### eGPU notes

- The Thunderbolt link behaves like PCIe x4: loading a model is slower, inference is close to native while the **whole model fits in VRAM**. Avoid offloading layers to system RAM.
- `pcie.link.gen.current` reads Gen 1 at idle (the link down-clocks); measure it under load.
- Stop the stack before unplugging the enclosure.

---

## Verification, tests and power

```bash
bash document-ai/scripts/orchestra_sync.sh [branch-or-tag]     # safely align this folder to the remote (backup first, never loses local work)
bash document-ai/scripts/egpu_check.sh                      # read-only diagnostics, prints UUIDs
bash document-ai/scripts/orchestra_smoke_test.sh --load     # roles, one GPU per container, memory grows on the right GPU, 100% GPU
bash tests/run_all.sh                                       # 332 simulated checks (no GPU, Docker or network touched)
bash document-ai/scripts/orchestra_bench_models.sh --parallel "1 2 3" MODEL   # tokens/s per stream and aggregate with N concurrent requests, VRAM, 100% GPU
bash document-ai/scripts/orchestra_power.sh status          # watts, limits, P-state per GPU
bash document-ai/scripts/orchestra_power.sh bench eco balanced performance   # tokens/s, average watts, tokens per joule
```

Power limits (`orchestra_power.sh profile eco|balanced|performance`, `restore`) are percentages of each GPU's default limit (70 / 85 / 100 %), need `sudo -n` and are not persistent across reboots. Text generation is mostly memory-bound, so a lower limit usually costs little throughput, but **measure with `bench` before adopting a profile**. Small models on the 4060 also avoid waking the 3090.

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
- Docker (Compose is optional: the launcher uses `docker run`)
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

ComfyUI **is part of the working system**: the `/generate` command runs `ollama/pipelines/image_loop.py`, which drives ComfyUI (SDXL + LCM-LoRA, templates in `workflows/`) with a vision/refine loop; the `comfy_integrator` and `design_critic` agents cover it.

- ComfyUI runs as a **host process on port 8188**. The launcher starts it in the foreground at the end (step 8); `start_comfyui.sh` is a standalone alternative.
- It is pinned to the GPU of `ORCHESTRA_COMFY_ROLE` with `CUDA_VISIBLE_DEVICES=<UUID>`. On the 3090 the VAE stays on GPU; on small GPUs the historical flags (`--normalvram`/`--lowvram` with `--cpu-vae`) are kept.
- Open WebUI uses `COMFYUI_BASE_URL=http://172.17.0.1:8188`, while the `image_loop` valve `comfyui_url` defaults to `http://172.19.0.1:8188` (the `ollama_default` gateway); both are covered by the `ufw` rules.
- The RAG service runs in ComfyUI's virtualenv.

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

The multi-GPU architecture is implemented (see [GPU support](#gpu-support)): two Ollama instances (3090 `main`, 4060 `aux`), per-role backends with failover in the manifold and `image_loop`, GPU-pinned ComfyUI, per-role VRAM monitoring and an opt-in power profile. It is covered by a simulated test suite and still has to be validated end-to-end on the hardware (`orchestra_smoke_test.sh --load`, `orchestra_power.sh bench`).

## Roadmap

- Validate on hardware: GPU isolation, flash attention with `q8_0` KV, pre-loading, power profiles, ComfyUI on the 3090
- Use the 32B model (`ollama/Modelfile-orchestra`) for `orchestra_dev` / `reasoner` when the main GPU is free
- Per-GPU metrics in `/status` and in the pattern logs
- Move the machine-specific values of the launcher (`192.168.1.51`, paths) into `orchestra.env`
- Harden the AI workflow (pass `client_payload` through `env:` instead of interpolating it into the script)
- Continued RAG optimization and improved document ingestion

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
| `dual-gpu-final` | dual-GPU: roles, aux Ollama, per-role routing, ComfyUI pinning, power, tests | in review |

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

- **GPU routing is configured by the launcher** (roles by UUID, one Ollama per GPU) and by the manifold valves (`ollama_url_aux`, `aux_models`); run `orchestra_smoke_test.sh` before assuming a model sits on the intended GPU.
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

## File: start_ai_stack.sh (29848 byte)

```
#!/bin/bash
# Se lanciato con `sh start_ai_stack.sh` (su Ubuntu e' dash) ricade in bash: lo script usa array e
# altre estensioni di bash e con dash darebbe un errore di sintassi che sembra un file corrotto.
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
# ORCHESTRA — AI LOCAL STACK LAUNCHER v3.9
# -------------------------------------------------
# Changelog v3.9 (dual-GPU completo):
#   - EGPU-02b Secondo Ollama (ai-ollama-aux-session, porta 11436) fissato alla GPU aux
#     (4060) con volume proprio `ollama-aux-session` e solo i modelli leggeri
#     (coordinator + vision). Disattivabile con ORCHESTRA_AUX_OLLAMA=0.
#   - EGPU-04 Ollama: flash attention + cache KV q8_0 (ORCHESTRA_FLASH_ATTENTION,
#     ORCHESTRA_KV_CACHE_TYPE), 2 modelli caricabili sulla main se ha >= 20 GB, container
#     ricreati se queste variabili cambiano. Il 32b si scarica solo con >= 20 GB sulla main.
#   - FIX: il controllo "modello gia' presente" confrontava solo il nome prima dei due punti:
#     con qwen2.5-coder:14b installato il 32b non veniva mai scaricato. Ora nome:tag esatto.
#   - FIX: creare/ricreare Ollama, Qdrant e Pipelines falliva ("docker run requires at least 1
#     argument"): nel launcher originale quelle chiamate non avevano l immagine (i container
#     preesistevano e non si passava mai dal ramo di creazione). Ora l immagine e quella del container
#     esistente, o una gia presente in locale, o il default (ORCHESTRA_*_IMAGE per forzarla).
#     La ricreazione e transazionale: il container vecchio diventa <nome>.bak e viene ripristinato
#     da solo se la creazione fallisce.
#   - ORCHESTRA_PULL_IMAGES=1 aggiorna l immagine Ollama (modelli nuovi richiedono Ollama recente) e ricrea
#     i container creati con la vecchia; ORCHESTRA_RECREATE_OLLAMA=1 forza la ricreazione. OLLAMA_CONTEXT_LENGTH
#     (ORCHESTRA_CONTEXT_LENGTH, default 8192 = contesto del manifold) evita che il contesto predefinito
#     di Ollama gonfi la cache KV (contesto x parallelismo) e sposti layer su CPU. Versione Ollama nel log.
#   - ARCH-01 ORCHESTRA_MAIN_PARALLEL / ORCHESTRA_AUX_PARALLEL (OLLAMA_NUM_PARALLEL, default 1) e
#     ORCHESTRA_HEAVY_MODEL (modello pesante a scelta, download non fatale).
#   - EGPU-06 ORCHESTRA_POWER_PROFILE=eco|balanced|performance (opzionale) applica i power
#     limit via document-ai/scripts/orchestra_power.sh; senza variabile nulla cambia.
#   - Logica GPU spostata in document-ai/scripts/orchestra_gpu_env.sh (condivisa con
#     start_comfyui.sh e orchestra_smoke_test.sh). Se manca, si torna al comportamento
#     storico (--gpus all).
#   - Il container Pipelines viene ricreato se cambiano ORCHESTRA_GPU_MAIN/AUX,
#     OLLAMA_AUX_URL o ORCHESTRA_COMFY_ROLE (le variabili d'ambiente sono fissate
#     alla creazione). Le dipendenze pip vengono reinstallate all'avvio.
#   - File opzionale orchestra.env nella root del repo (ignorato da git) per i
#     valori locali: vedi document-ai/config/orchestra.env.example.
#
# Changelog v3.8 (dual-GPU: RTX 3090 eGPU + RTX 4060):
#   - EGPU-02 Rileva i ruoli GPU (main = piu' VRAM = 3090, aux = 4060) e li esporta
#     in ORCHESTRA_GPU_MAIN / ORCHESTRA_GPU_AUX (UUID). Valori gia' impostati
#     nell'ambiente vengono rispettati se la GPU e' presente.
#   - Ollama fissato alla sola GPU main (--gpus device=<UUID>) e ricreato se il
#     container esistente era stato creato con --gpus all (volume modelli intatto).
#   - Smoke test integrato: verifica che Ollama veda UNA sola GPU.
#   - rag_service eredita le variabili (/vram multi-GPU); passate anche al
#     container Pipelines (fallback L2) alla prossima (ri)creazione.
#
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
OLLAMA_AUX_CONTAINER="ai-ollama-aux-session"
WEBUI_CONTAINER="ai-webui-session"
PIPELINES_CONTAINER="ai-pipelines-session"
QDRANT_CONTAINER="ai-qdrant-session"
NETWORK="ollama_default"

OLLAMA_PORT=11435
OLLAMA_AUX_PORT=11436
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
    "qwen2.5-coder:32b"
    "llava:7b"
    "moondream:v2"
)

# Modelli serviti dall'Ollama aux (4060): coordinator + vision, leggeri.
# Devono coincidere con il valve `aux_models` del manifold e di image_loop.
# Modelli pesanti: scaricati solo se la GPU main ha almeno HEAVY_MIN_VRAM_MB di VRAM
# (il 32B Q4 pesa ~20 GB su disco e non sta in una GPU da 8 GB).
HEAVY_MODELS=("qwen2.5-coder:32b")
HEAVY_MIN_VRAM_MB=20000
# ARCH-01: modello pesante ALTERNATIVO/AGGIUNTIVO scelto dall utente (es. qwen3.6:27b, ~17 GB,
# oggi indicato come riferimento per 24 GB). Se impostato viene scaricato con le stesse regole dei
# modelli pesanti (solo con VRAM sufficiente) e il download e NON fatale: un tag errato non
# blocca il resto dello stack. Senza la variabile il comportamento e quello di prima.
if [ -n "${ORCHESTRA_HEAVY_MODEL:-}" ]; then
    HEAVY_MODELS+=("${ORCHESTRA_HEAVY_MODEL}")
    REQUIRED_MODELS+=("${ORCHESTRA_HEAVY_MODEL}")
fi

AUX_MODELS=(
    "llama3.2:3b"
    "moondream:v2"
    "llava:7b"
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
# EGPU-02/02b — Ruoli GPU main (3090) / aux (4060): libreria condivisa
# ------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Valori locali opzionali (UUID GPU, ORCHESTRA_AUX_OLLAMA, ORCHESTRA_COMFY_ROLE...).
# shellcheck disable=SC1091
[ -f "$SCRIPT_DIR/orchestra.env" ] && . "$SCRIPT_DIR/orchestra.env"
GPU_LIB="$SCRIPT_DIR/document-ai/scripts/orchestra_gpu_env.sh"
if [ -f "$GPU_LIB" ]; then
    # shellcheck disable=SC1090
    . "$GPU_LIB"
else
    warn "Libreria GPU non trovata (${GPU_LIB}): comportamento storico (--gpus all, nessun Ollama aux)"
    detect_gpu_roles()        { :; }
    recreate_if_not_pinned()  { :; }
    recreate_if_env_stale()   { :; }
    aux_ollama_enabled()      { return 1; }
    container_gpu_count()     { echo 0; }
    gpu_for_role()            { :; }
    gpu_total_mb()            { :; }
    comfyui_gpu_setup()       { COMFY_MEM_ARGS=(--normalvram --cpu-vae); }
    pick_image()              { echo "$3"; }
    retire_container()        { docker rm -f "$1" >/dev/null; }
    restore_container()       { return 1; }
    discard_backup()          { :; }
    image_id_of()             { :; }
    recreate_if_image_outdated() { :; }
    ollama_has_model()        { docker exec "$1" ollama list 2>/dev/null | grep -q "${2%%:*}"; }
fi
detect_gpu_roles

# EGPU-06: profilo di potenza OPZIONALE (eco | balanced | performance). Senza la variabile non
# cambia nulla. Imposta i power limit delle GPU (richiede sudo senza password; i limiti tornano
# al predefinito al riavvio o con `orchestra_power.sh restore`). Misura prima con
# `orchestra_power.sh bench eco balanced performance`.
if [ -n "${ORCHESTRA_POWER_PROFILE:-}" ]; then
    POWER_SCRIPT="$SCRIPT_DIR/document-ai/scripts/orchestra_power.sh"
    if [ -f "$POWER_SCRIPT" ]; then
        info "Profilo di potenza: ${ORCHESTRA_POWER_PROFILE}"
        bash "$POWER_SCRIPT" profile "$ORCHESTRA_POWER_PROFILE" \
            || warn "Profilo di potenza non applicato (servono privilegi: sudo visudo -> NOPASSWD per nvidia-smi)"
    else
        warn "orchestra_power.sh non trovato: profilo di potenza ignorato"
    fi
fi

# ------------------------------------------------------------
# Funzioni di pulizia
# ------------------------------------------------------------
stop_containers() {
    info "Arresto dei container Docker..."
    for container in "$OLLAMA_CONTAINER" "$OLLAMA_AUX_CONTAINER" "$QDRANT_CONTAINER" "$PIPELINES_CONTAINER" "$WEBUI_CONTAINER"; do
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
        if docker run -d --name "$name" --restart no "$@" >/dev/null; then
            success "Container ${name} creato e avviato"
            discard_backup "$name"
        else
            warn "Creazione di ${name} FALLITA"
            if restore_container "$name"; then
                warn "Ripristinato il container precedente ${name} (configurazione vecchia): controlla l errore sopra"
            else
                return 1
            fi
        fi
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
header "▶  ORCHESTRA v3.9 — Avvio stack (nessuna persistenza dopo l'uscita)"

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
# EGPU-02: ensure_container RIUSA i container esistenti: ricreiamo Ollama se non e' fissato
# alla GPU main (volume modelli `ollama-session` intatto). Vedi orchestra_gpu_env.sh.
# L immagine va scelta PRIMA di ricreare i container (che rimuove quello esistente). Il launcher
# originale non passava l immagine per Ollama, Qdrant e Pipelines (container sempre preesistenti):
# ricrearli falliva con "docker run requires at least 1 argument".
OLLAMA_IMAGE="${ORCHESTRA_OLLAMA_IMAGE:-$(pick_image "$OLLAMA_CONTAINER" ollama/ollama ollama/ollama:latest)}"
info "Immagine Ollama: ${OLLAMA_IMAGE}"
# ORCHESTRA_PULL_IMAGES=1 scarica prima la versione piu recente dell immagine Ollama. Serve per i modelli
# nuovi ("412 ... requires a newer version of Ollama"). Se l immagine cambia, i container Ollama creati con
# la vecchia vengono ricreati (etichetta orchestra.image_id); i volumi con i modelli non si toccano.
if [ "${ORCHESTRA_PULL_IMAGES:-0}" = "1" ]; then
    info "Aggiorno l immagine ${OLLAMA_IMAGE} (ORCHESTRA_PULL_IMAGES=1)..."
    docker pull "$OLLAMA_IMAGE" >/dev/null 2>&1 || warn "pull di ${OLLAMA_IMAGE} fallito: uso l immagine locale"
fi
OLLAMA_IMAGE_ID="$(image_id_of "$OLLAMA_IMAGE")"
OLLAMA_LABEL=(); [ -n "$OLLAMA_IMAGE_ID" ] && OLLAMA_LABEL=(--label "orchestra.image_id=${OLLAMA_IMAGE_ID}")
recreate_if_not_pinned "$OLLAMA_CONTAINER" "${ORCHESTRA_GPU_MAIN:-}"

# EGPU-04: ottimizzazione VRAM/cooperazione.
#  - Flash attention + cache KV in q8_0: dimezza la memoria del contesto (con qwen2.5-coder
#    32b e num_ctx 12288 la KV passa da ~3 a ~1.5 GB, stime da verificare con `ollama ps`).
#    Disattivabili con ORCHESTRA_FLASH_ATTENTION=0 (la KV torna f16).
#  - Con >= 20 GB sulla main possono restare caricati 2 modelli (es. quality + fast): niente
#    scambi continui sul link Thunderbolt. Con GPU piccole resta 1, come prima.
OLLAMA_FA="${ORCHESTRA_FLASH_ATTENTION:-1}"
if [ "$OLLAMA_FA" = "1" ]; then OLLAMA_KV="${ORCHESTRA_KV_CACHE_TYPE:-q8_0}"; else OLLAMA_KV="f16"; fi
MAIN_TOTAL_MB=""
[ -n "${ORCHESTRA_GPU_MAIN:-}" ] && MAIN_TOTAL_MB="$(gpu_total_mb "$ORCHESTRA_GPU_MAIN")"
# ARCH-01: richieste in parallelo per modello caricato (stessi pesi, una cache KV per slot).
# Default 1 (come prima). La memoria della KV cresce di num_ctx x parallel: misurare con
# document-ai/scripts/orchestra_bench_models.sh prima di alzarlo.
OLLAMA_MAIN_PAR="${ORCHESTRA_MAIN_PARALLEL:-1}"
OLLAMA_AUX_PAR="${ORCHESTRA_AUX_PARALLEL:-1}"
# Contesto predefinito di Ollama = quello del manifold (valve context_length 8192). Senza, le versioni
# recenti scelgono il default in base alla VRAM (fino a decine di migliaia di token) e la cache KV, che
# cresce di contesto x richieste parallele, puo spingere i layer su CPU anche con 24 GB.
OLLAMA_CTX="${ORCHESTRA_CONTEXT_LENGTH:-8192}"
OLLAMA_MAIN_LOADED=1
[ -n "$MAIN_TOTAL_MB" ] && [ "$MAIN_TOTAL_MB" -ge "$HEAVY_MIN_VRAM_MB" ] && OLLAMA_MAIN_LOADED=2
info "Ollama main: modelli caricabili=${OLLAMA_MAIN_LOADED}, richieste parallele=${OLLAMA_MAIN_PAR}, contesto=${OLLAMA_CTX}, flash-attention=${OLLAMA_FA}, KV=${OLLAMA_KV}"
# Le variabili d'ambiente sono fissate alla creazione: ricrea se sono cambiate.
recreate_if_env_stale "$OLLAMA_CONTAINER" \
    "OLLAMA_MAX_LOADED_MODELS=${OLLAMA_MAIN_LOADED}" "OLLAMA_NUM_PARALLEL=${OLLAMA_MAIN_PAR}" \
    "OLLAMA_CONTEXT_LENGTH=${OLLAMA_CTX}" \
    "OLLAMA_FLASH_ATTENTION=${OLLAMA_FA}" "OLLAMA_KV_CACHE_TYPE=${OLLAMA_KV}"
recreate_if_image_outdated "$OLLAMA_CONTAINER" "$OLLAMA_IMAGE"
OLLAMA_GPU_ARG=(--gpus all)   # fallback storico se nvidia-smi non e' disponibile
[ -n "${ORCHESTRA_GPU_MAIN:-}" ] && OLLAMA_GPU_ARG=(--gpus "device=${ORCHESTRA_GPU_MAIN}")
ensure_container "$OLLAMA_CONTAINER" \
    --network "$NETWORK" "${OLLAMA_GPU_ARG[@]}" \
    -v ollama-session:/root/.ollama \
    -p "127.0.0.1:${OLLAMA_PORT}:11434" \
    -e OLLAMA_HOST=0.0.0.0:11434 \
    -e OLLAMA_KEEP_ALIVE=0 \
    -e OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAIN_LOADED}" \
    -e OLLAMA_FLASH_ATTENTION="${OLLAMA_FA}" \
    -e OLLAMA_KV_CACHE_TYPE="${OLLAMA_KV}" \
    -e OLLAMA_NUM_PARALLEL="${OLLAMA_MAIN_PAR}" \
    -e OLLAMA_CONTEXT_LENGTH="${OLLAMA_CTX}" \
    -e OLLAMA_MAX_QUEUE=10 \
    "${OLLAMA_LABEL[@]}" \
    "$OLLAMA_IMAGE"

OLLAMA_READY=false
for i in $(seq 1 30); do
    curl -sf "http://127.0.0.1:${OLLAMA_PORT}/" >/dev/null 2>&1 && OLLAMA_READY=true && break
    sleep 2
done
if [ "$OLLAMA_READY" = true ]; then
    success "Ollama pronto"
    info "Versione Ollama: $(docker exec "$OLLAMA_CONTAINER" ollama --version 2>/dev/null | tail -1)"
    # EGPU-02 smoke test: Ollama deve vedere UNA sola GPU (la main).
    if [ -n "${ORCHESTRA_GPU_MAIN:-}" ]; then
        OLLAMA_GPUS=$(container_gpu_count "$OLLAMA_CONTAINER")
        if [ "$OLLAMA_GPUS" = "1" ]; then
            success "Ollama vede 1 sola GPU (main)"
        else
            warn "Ollama vede ${OLLAMA_GPUS} GPU (atteso 1): controlla --gpus / ORCHESTRA_GPU_MAIN"
        fi
    fi
    for model in "${REQUIRED_MODELS[@]}"; do
        # Modelli pesanti: solo se la GPU main li regge (VRAM nota e sufficiente, o non rilevabile).
        if [[ " ${HEAVY_MODELS[*]} " == *" ${model} "* ]] \
                && [ -n "$MAIN_TOTAL_MB" ] && [ "$MAIN_TOTAL_MB" -lt "$HEAVY_MIN_VRAM_MB" ]; then
            info "Salto ${model}: GPU main ${MAIN_TOTAL_MB} MiB < ${HEAVY_MIN_VRAM_MB} MiB"
            continue
        fi
        # EGPU-04: confronto esatto nome:tag (il vecchio grep sul solo nome saltava il 32b).
        if [[ " ${HEAVY_MODELS[*]} " == *" ${model} "* ]]; then
            # modelli pesanti: un tag errato o un download interrotto non deve fermare lo stack
            ollama_has_model "$OLLAMA_CONTAINER" "$model" \
                || docker exec "$OLLAMA_CONTAINER" ollama pull "$model" \
                || warn "Download di ${model} fallito (modello pesante): proseguo senza"
        else
            ollama_has_model "$OLLAMA_CONTAINER" "$model" \
                || docker exec "$OLLAMA_CONTAINER" ollama pull "$model"
        fi
    done
else
    warn "Ollama non risponde — continuo"
fi

header "2️⃣b Ollama AUX (127.0.0.1:${OLLAMA_AUX_PORT}) — GPU aux"
# EGPU-02b: seconda istanza Ollama, fissata alla GPU aux. Ospita i modelli leggeri
# (coordinator + vision) cosi' gli agenti pesanti sulla main non li fanno aspettare.
# Volume proprio: nessuna condivisione di file con l'Ollama main. Il main conserva
# comunque TUTTI i modelli: se l'aux non risponde il manifold ricade sul main.
OLLAMA_AUX_URL=""
if aux_ollama_enabled; then
    OLLAMA_AUX_IMAGE="$(pick_image "$OLLAMA_AUX_CONTAINER" ollama/ollama "$OLLAMA_IMAGE")"
    OLLAMA_AUX_LABEL=(); OLLAMA_AUX_IMAGE_ID="$(image_id_of "$OLLAMA_AUX_IMAGE")"
    [ -n "$OLLAMA_AUX_IMAGE_ID" ] && OLLAMA_AUX_LABEL=(--label "orchestra.image_id=${OLLAMA_AUX_IMAGE_ID}")
    recreate_if_not_pinned "$OLLAMA_AUX_CONTAINER" "$ORCHESTRA_GPU_AUX"
    recreate_if_env_stale "$OLLAMA_AUX_CONTAINER" \
        "OLLAMA_NUM_PARALLEL=${OLLAMA_AUX_PAR}" "OLLAMA_CONTEXT_LENGTH=${OLLAMA_CTX}" \
        "OLLAMA_FLASH_ATTENTION=${OLLAMA_FA}" "OLLAMA_KV_CACHE_TYPE=${OLLAMA_KV}"
    recreate_if_image_outdated "$OLLAMA_AUX_CONTAINER" "$OLLAMA_AUX_IMAGE"
    ensure_container "$OLLAMA_AUX_CONTAINER" \
        --network "$NETWORK" --gpus "device=${ORCHESTRA_GPU_AUX}" \
        -v ollama-aux-session:/root/.ollama \
        -p "127.0.0.1:${OLLAMA_AUX_PORT}:11434" \
        -e OLLAMA_HOST=0.0.0.0:11434 \
        -e OLLAMA_KEEP_ALIVE=10m \
        -e OLLAMA_MAX_LOADED_MODELS=2 \
        -e OLLAMA_FLASH_ATTENTION="${OLLAMA_FA}" \
        -e OLLAMA_KV_CACHE_TYPE="${OLLAMA_KV}" \
        -e OLLAMA_NUM_PARALLEL="${OLLAMA_AUX_PAR}" \
        -e OLLAMA_CONTEXT_LENGTH="${OLLAMA_CTX}" \
        -e OLLAMA_MAX_QUEUE=10 \
        "${OLLAMA_AUX_LABEL[@]}" \
        "$OLLAMA_AUX_IMAGE"

    OLLAMA_AUX_READY=false
    for i in $(seq 1 30); do
        curl -sf "http://127.0.0.1:${OLLAMA_AUX_PORT}/" >/dev/null 2>&1 && OLLAMA_AUX_READY=true && break
        sleep 2
    done
    if [ "$OLLAMA_AUX_READY" = true ]; then
        success "Ollama aux pronto"
        OLLAMA_AUX_GPUS=$(container_gpu_count "$OLLAMA_AUX_CONTAINER")
        if [ "$OLLAMA_AUX_GPUS" = "1" ]; then
            success "Ollama aux vede 1 sola GPU (aux)"
        else
            warn "Ollama aux vede ${OLLAMA_AUX_GPUS} GPU (atteso 1): controlla --gpus / ORCHESTRA_GPU_AUX"
        fi
        for model in "${AUX_MODELS[@]}"; do
            ollama_has_model "$OLLAMA_AUX_CONTAINER" "$model" \
                || docker exec "$OLLAMA_AUX_CONTAINER" ollama pull "$model" \
                || warn "Download di ${model} sull'aux fallito (il manifold usera' il main)"
        done
        OLLAMA_AUX_URL="http://${OLLAMA_AUX_CONTAINER}:11434"
    else
        warn "Ollama aux non risponde — il manifold usera' solo il main"
    fi
else
    info "Ollama aux non attivo (nessuna GPU aux oppure ORCHESTRA_AUX_OLLAMA=0)"
fi
# Variabili lette dal container Pipelines (manifold e image_loop).
export OLLAMA_AUX_URL
export ORCHESTRA_COMFY_ROLE="${ORCHESTRA_COMFY_ROLE:-main}"

header "3️⃣  Qdrant (127.0.0.1:${QDRANT_PORT})"
QDRANT_IMAGE="${ORCHESTRA_QDRANT_IMAGE:-$(pick_image "$QDRANT_CONTAINER" qdrant/qdrant qdrant/qdrant:latest)}"
ensure_container "$QDRANT_CONTAINER" \
    --network "$NETWORK" \
    -v qdrant-data:/qdrant/storage \
    -p "127.0.0.1:${QDRANT_PORT}:6333" \
    "$QDRANT_IMAGE"

QDRANT_READY=false
for i in $(seq 1 15); do
    curl -sf "http://127.0.0.1:${QDRANT_PORT}/healthz" >/dev/null 2>&1 && \
        QDRANT_READY=true && break
    sleep 2
done
[ "$QDRANT_READY" = true ] && success "Qdrant pronto" || warn "Qdrant non risponde"

header "4️⃣  Pipelines (127.0.0.1:${PIPELINES_PORT})"
PIPELINES_IMAGE="${ORCHESTRA_PIPELINES_IMAGE:-$(pick_image "$PIPELINES_CONTAINER" ghcr.io/open-webui/pipelines ghcr.io/open-webui/pipelines:main)}"
# EGPU-02b: le variabili d'ambiente sono fissate alla creazione del container.
recreate_if_env_stale "$PIPELINES_CONTAINER" \
    ORCHESTRA_GPU_MAIN ORCHESTRA_GPU_AUX OLLAMA_AUX_URL ORCHESTRA_COMFY_ROLE
ensure_container "$PIPELINES_CONTAINER" \
    --gpus all \
    -v "${PIPELINES_DIR}:/app/pipelines" \
    -v "${HOME}/ai-sessioni:/app/ai:ro" \
    -v "${LOGS_DIR}:/app/logs" \
    -v "${DOCS_ROOT}:/app/document-ai" \
    -v /usr/bin/nvidia-smi:/usr/bin/nvidia-smi:ro \
    -p "127.0.0.1:${PIPELINES_PORT}:9099" \
    -e PIPELINES_API_KEY="$PIPELINES_API_KEY" \
    -e ORCHESTRA_GPU_MAIN="${ORCHESTRA_GPU_MAIN:-}" \
    -e ORCHESTRA_GPU_AUX="${ORCHESTRA_GPU_AUX:-}" \
    -e OLLAMA_AUX_URL="${OLLAMA_AUX_URL:-}" \
    -e ORCHESTRA_COMFY_ROLE="${ORCHESTRA_COMFY_ROLE:-main}" \
    -e PATTERN_LOG_PATH="/app/logs/patterns.jsonl" \
    -e DOCS_ROOT="/app/document-ai" \
    -e PIPELINES_REQUIREMENTS_PATH="/app/pipelines/requirements.txt" \
    "$PIPELINES_IMAGE"

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


# EGPU-05: ComfyUI sulla GPU del ruolo ORCHESTRA_COMFY_ROLE (default main = 3090).
# Sulla 3090 il VAE resta su GPU; sulle GPU piccole restano i flag storici
# (--normalvram --cpu-vae). COMFY_EXTRA_ARGS permette flag aggiuntivi senza toccare lo script.
comfyui_gpu_setup --normalvram

# shellcheck disable=SC2086
python main.py \
    --listen 0.0.0.0 --port "$COMFYUI_PORT" \
    --force-fp16 --dont-upcast-attention \
    "${COMFY_MEM_ARGS[@]}" \
    --preview-method auto ${COMFY_EXTRA_ARGS:-}

# Quando ComfyUI termina, lo script arriva qui e poi esegue il trap cleanup.
# I container e il RAG service vengono fermati dalla funzione cleanup.
```

## File: start_comfyui.sh (1303 byte)

```
#!/bin/bash
# Con `sh` (dash) ricade in bash: lo script usa array.
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"
# start_comfyui.sh — avvio STANDALONE di ComfyUI (alternativa al passo 8 di start_ai_stack.sh)
#
# EGPU-05: ComfyUI viene fissato alla GPU del ruolo ORCHESTRA_COMFY_ROLE (main = 3090,
# aux = 4060; default main) tramite CUDA_VISIBLE_DEVICES=<UUID>. Sulla 3090 il VAE resta
# su GPU; sulle GPU piccole restano i flag storici (--lowvram --cpu-vae).
# Variabili opzionali: ORCHESTRA_COMFY_ROLE, ORCHESTRA_GPU_MAIN/AUX (UUID), COMFY_EXTRA_ARGS.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/orchestra.env" ] && . "$SCRIPT_DIR/orchestra.env"
if [ -f "$SCRIPT_DIR/document-ai/scripts/orchestra_gpu_env.sh" ]; then
    . "$SCRIPT_DIR/document-ai/scripts/orchestra_gpu_env.sh"
    detect_gpu_roles
    comfyui_gpu_setup --lowvram
else
    echo "[warn] libreria GPU non trovata: flag storici, GPU predefinita" >&2
    COMFY_MEM_ARGS=(--lowvram --cpu-vae)
fi

cd ~/ai-sessioni/ComfyUI
source venv/bin/activate
export OLLAMA_HOST=http://host.docker.internal:11435
# shellcheck disable=SC2086
python main.py \
  --listen 0.0.0.0 \
  --port 8188 \
  --force-fp16 \
  --dont-upcast-attention \
  "${COMFY_MEM_ARGS[@]}" \
  --preview-method auto ${COMFY_EXTRA_ARGS:-}
```

