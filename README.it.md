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
bash tests/run_all.sh                                       # 307 controlli simulati (nessuna GPU, Docker o rete toccati)
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
