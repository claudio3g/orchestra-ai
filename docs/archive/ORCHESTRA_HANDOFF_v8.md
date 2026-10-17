> 📦 **ARCHIVIATO — sostituito da `document-ai/knowledge/ORCHESTRA_HANDOFF_v9.md`.**
> Descrive il sistema a GPU singola (RTX 4060 8 GB, un solo LLM in VRAM). Resta utile come
> riferimento per le parti invariate (RAG, Evolver, strutture dati). Spostato fuori da
> `document-ai/` perche' quella cartella e' indicizzata dal RAG e conteneva informazioni
> obsolete sull'hardware che il sistema avrebbe potuto restituire come contesto.

# 🎼 ORCHESTRA 8GB — DOCUMENTO DI HANDOFF v8

**Versione:** 8.0
**Data:** 15 Maggio 2026
**Stato sistema:** ✅ Stabile e operativo
**Baseline codice:** file `.py` aggiornati (Maggio 2026) — forniti separatamente

---

> ⚠️ **ISTRUZIONE CRITICA PER LA NUOVA AI**
>
> Leggi **TUTTO** questo documento dall'inizio alla fine prima di toccare qualsiasi file o scrivere qualsiasi codice. Ogni sezione è necessaria per capire le precedenti. Saltare sezioni causa errori garantiti.
>
> Il codice ufficiale è quello nei file `.py` forniti separatamente; questo handoff è la mappa, non il territorio.
>
> **Approccio operativo:**
> - **Conservativo**: non modificare ciò che funziona senza una motivazione tecnica precisa.
> - **Incrementale**: un cambiamento alla volta, testato prima di procedere.
> - **Documentato**: ogni modifica va spiegata nei commenti del codice e in questo handoff.
> - **Verificato**: dopo ogni modifica, esegui uno smoke test prima di dichiarare successo.

---

## 1. Identità e ruolo della nuova AI

Sei un ingegnere senior AI con specializzazione in:
- Sistemi AI self‑hosted con vincoli hardware severi (≤8 GB VRAM)
- Architetture RAG (Retrieval‑Augmented Generation)
- LLM locali via Ollama
- Pipeline Python per OpenWebUI/Pipelines framework
- ComfyUI per generazione immagini SDXL

Il progetto che stai gestendo è **Orchestra 8 GB**, un sistema AI locale orchestrato che:
- Riceve messaggi dall'utente via OpenWebUI
- Li arricchisce con contesto dalla knowledge base (RAG via Qdrant)
- Li instrada all'agente specializzato corretto (routing semantico via embedding)
- Genera risposte usando LLM locali via Ollama
- Può generare immagini SDXL tramite ComfyUI con un loop di raffinamento iterativo
- Si auto‑analizza e propone/applica miglioramenti conservativi (Evolver)

---

## 2. Hardware e vincoli assoluti

### Specifica macchina
| Componente | Specifica |
|-----------|-----------|
| OS | Ubuntu 24.04 LTS |
| CPU | Intel i9‑13900HX (8P+16E, 32 thread) |
| GPU | NVIDIA RTX 4060 Laptop 8 GB GDDR6 (~7.6 GB usabili) |
| RAM | 32 GB DDR5 + ZRAM (~32 GB compressi aggiuntivi) |
| Disco | NVMe ~384 GB |
| Utente | `claudio`, home `/home/claudio` |
| Directory progetto | `~/ai-sessioni/` |

### Vincoli hardware non negoziabili
| Vincolo | Valore | Motivazione tecnica |
|---------|--------|---------------------|
| Modelli LLM in VRAM simultanei | **1** | 8 GB VRAM — due modelli 7B+ causano OOM immediato |
| SDXL (ComfyUI) in VRAM | ~6.2 GB staged | Con `--normalvram`; non può coesistere con LLM 7B+ |
| qwen3.5:9b VRAM | ~6.6 GB | Compete direttamente con SDXL |
| llava:7b VRAM (full GPU) | ~4.7 GB | Entra solo dopo scarico SDXL |
| llama3.2:3b VRAM | ~2 GB | Coordinator, può restare in VRAM |
| qwen2.5‑coder:14b | 4.5 GB VRAM + 4.5 GB RAM | num_gpu=20 default, adattivo per RAM |

### Soglie VRAM per selezione adattiva modelli
**Per modelli vision (llava:7b):**
- `≥ 5000 MB` → llava:7b tutto GPU
- `3000–5000 MB` → llava:7b con num_gpu proporzionale (4–31 layer su GPU, resto CPU/ZRAM)
- `< 3000 MB` → fallback a moondream:v2 (~1.7 GB)

**Per modelli di refinement:**
- `≥ 6000 MB` → qwen3.5:9b tutto GPU
- `< 6000 MB` → qwen2.5‑coder:14b con num_gpu=12 (4.5 GB VRAM + CPU/ZRAM offload)

**Per modelli testo (select_text_model):**
- Modalità `quality` + RAM≥8 GB + VRAM≥4 GB → qwen2.5‑coder:14b (num_gpu=20)
- Modalità `quality/fast` + VRAM≥7000 → qwen3.5:9b
- Modalità `quality/fast` + VRAM≥5500 → llama3.1:8b
- VRAM < 5500 → llama3.2:3b (emergency)

---

## 3. Architettura generale dello stack

```
Utente (browser)
│
▼
Caddy (HTTPS :443) ← solo punto d'ingresso internet
│
▼
OpenWebUI (Docker :3001) v0.9.2
│ ← seleziona "🎼 Orchestra"
▼
Pipelines Framework (Docker :9099)
├── [FILTER] rag_filter.py v1.6.1 ← inietta contesto Qdrant (+ function detection)
│ orchestra_evolver.py v1.1 ← osservatore passivo
│
└── [MANIFOLD] orchestra_manifold.py v3.8.1 ← routing + selezione modello
    │
    ├── /generate → image_loop.py (v2.7.0) [lazy import]
    │   ├── ComfyUI (host :8188) ← genera draft SDXL
    │   ├── llava:7b / moondream:v2 ← analisi immagine
    │   └── qwen3.5:9b / qwen2.5‑coder:14b ← raffinamento prompt
    │
    ├── /rag → rag_service.py (host :6335)
    ├── /review → analisi patterns.jsonl + LLM
    ├── /evolve → orchestra_evolver.py [lazy import]
    │
    └── [messaggio normale]
        ├── Qdrant (Docker :6333) ← routing semantico
        │   collection: orchestra_routing
        └── Ollama (Docker :11435 host / :11434 interno)
            ├── llama3.2:3b (coordinator, keep_alive=600s)
            ├── qwen3.5:9b (specialista, keep_alive=0)
            ├── llama3.1:8b (fallback, keep_alive=0)
            ├── qwen2.5‑coder:14b (quality, keep_alive=0)
            ├── llava:7b (vision, keep_alive=0)
            └── moondream:v2 (vision fallback, keep_alive=0)
```

### Princìpi architetturali
1. **Un solo LLM in VRAM alla volta**: regola assoluta, gestita dal sistema di selezione adattiva.
2. **Lazy loading**: image_loop e orchestra_evolver importati solo quando servono.
3. **Fallback sempre presenti**: ogni operazione critica ha almeno un piano B.
4. **Nessuna dipendenza circolare**: i moduli non si importano a vicenda a livello di import Python.
5. **Stato minimo**: quasi stateless tra richieste; persistenza su file JSONL.
6. **VRAM Monitor centralizzato**: `embedding_utils` v2.0.0 fornisce un daemon per letture a 0 ms di latenza.

---

## 4. Servizi, porte e networking

| Servizio | Tipo | Porta host | Porta interna | Note |
|----------|------|-----------|---------------|------|
| Ollama | Docker | `127.0.0.1:11435` | `11434` | Backend LLM principale |
| OpenWebUI | Docker | `127.0.0.1:3001` | `8080` | Interfaccia utente web (v0.9.2) |
| Pipelines | Docker | `127.0.0.1:9099` | `9099` | Framework pipeline AI |
| Qdrant | Docker | `127.0.0.1:6333` | `6333` | Vector DB (RAG + routing) |
| ComfyUI | Host/venv | `0.0.0.0:8188` | — | Su host, non Docker |
| RAG Service | Host/venv | `0.0.0.0:6335` | — | Flask, indicizzazione docs |
| Caddy | Host | `0.0.0.0:80,443` | — | Reverse proxy TLS |

### Nomi DNS interni ai container Docker
| Nome DNS | Risolve a |
|----------|-----------|
| `ai-ollama-session` | Container Ollama |
| `ai-qdrant-session` | Container Qdrant |
| `host.docker.internal` | IP del host → ComfyUI e RAG service |

### Sicurezza rete
- `ufw` blocca tutto il traffico esterno tranne porte 80 e 443
- Caddy gestisce HTTPS con Let's Encrypt (dominio: `saponetta.mooo.com`)
- Tutte le porte dei servizi interni sono su `127.0.0.1`
- ComfyUI su `0.0.0.0` ma protetto da ufw; accessibile solo dai container Docker

---

## 5. Modelli LLM installati

| Modello | VRAM | Ruolo | keep_alive | Note |
|---------|------|-------|-----------|------|
| `llama3.2:3b` | ~2 GB | Coordinator | **600s** | Sempre in VRAM, risposte veloci |
| `qwen3.5:9b` | ~6.6 GB | Specialista principale | 0 | Agenti tecnici con RAG |
| `llama3.1:8b` | ~5 GB | Fallback testo | 0 | Se VRAM < 7 GB per fast |
| `qwen2.5‑coder:14b-instruct-q4_K_M` | 4.5+4.5 GB | Quality mode / Evolver | 0 | Offload CPU/ZRAM con num_gpu=20 |
| `llava:7b` | ~4.7 GB | Vision primario | 0 | Analisi immagini nel loop |
| `moondream:v2` | ~1.7 GB | Vision fallback | 0 | Quando VRAM < 3 GB dopo ComfyUI |

---

## 6. Flusso messaggi completo

### Flusso standard (messaggio testuale normale)

```
Utente invia messaggio via OpenWebUI

[FILTER] rag_filter.py.inlet() (v1.6.1):
a. Estrae testo dell'ultimo messaggio utente
b. Salta se: /generate, /rag, /evolve, /review, messaggio interno OWUI
c. Se la query contiene parole chiave hardware, NON la salta anche se corta
   e inietta forzatamente i chunk pertinenti di hardware-report.md.
d. Calcola embedding asincrono (fastembed, CPU) con cache a 3 livelli
   (locale + L1 memory + L2 shelve di embedding_utils).
e. Cerca in Qdrant collection "orchestra" (top_k=6, min_score=0.45)
f. Se query menziona file .py/.sh/.md → ricerca esatta per source name
g. Se query è hardware-related → aggiunge i chunk di "hardware-report.md"
h. Rileva richieste di funzioni specifiche ("mostrami la funzione X")
   e cerca direttamente per function_name nei metadati del chunk.
i. Formatta contesto (max 12000 char, chunk esatti prima, semantici dopo)
j. Inietta blocco "📚 CONTESTO DALLA KNOWLEDGE BASE" nel system message
k. Restituisce body modificato al framework Pipelines

[MANIFOLD] orchestra_manifold.py.pipe() (v3.8.1):
a. Estrae testo pulito (rimuove prefissi OWUI: "query:", "search:")
b. Salta se request interna OWUI (generate title, follow-up, ecc.)
c. Legge stats sistema: RAM via /proc/meminfo, VRAM via daemon embedding_utils
d. Seleziona mode: quality (RAM≥8GB) / fast (RAM≥4GB) / emergency
e. Identifica comandi speciali: /generate, /rag, /review, /evolve
f. Controlla presenza RAG context (cerca RAG_MARKER nel system message)
g. Se NON c'è RAG:
   - Inietta warning "Nessuna fonte verificata"
   - Log evento "ungrounded_response"
   - Se query tecnica → forza qwen3.5:9b (force_fast=True)
h. Routing semantico via Qdrant "orchestra_routing"
   - Restituisce agente + confidence score (barra ◼◻ a 5 livelli)
i. Selezione modello finale in base a: agente + RAG + mode + VRAM + RAM
   - Con RAG: coordinator usa llama3.2:3b (leggero), altri agenti usano qwen3.5:9b
   - Senza RAG: routing → coordinator/altri → model_coordinator / select_text_model
j. Costruisce messaggi Ollama (estrae contesto RAG, converte immagini base64)
k. Streaming risposta via /api/chat Ollama
l. Filtra tag <think>...</think> in streaming
```

### Flusso /generate (generazione immagine SDXL)

```
orchestra_manifold.handle_generate(prompt)
  Acquisisce _sdxl_lock (non‑blocking, rifiuta se già occupato)
  Carica image_loop.py via importlib (lazy, cached)
  Delega a image_loop.pipe()

image_loop.pipe() → generate() [generator] (v2.7.0):
[OPZIONALE] Pre‑flight (default OFF): llama3.2:3b ottimizza il prompt
[LOOP DRAFT — max 3 iterazioni]:
  a. generate_image(512×512, 4 step LCM, seed casuale) via ComfyUI API
  b. free_comfyui_vram()
  c. Misura VRAM libera (daemon embedding_utils) → seleziona vision model
  d. analyze_image_vision() con llava:7b / moondream:v2
     Prompt strutturato, temperatura 0.2, parsing JSON+regex robusto
  e. Se score peggiora → ripristina miglior prompt
  f. Se score ≥ 9 → early stop
  g. Raffinamento prompt con qwen3.5:9b / qwen2.5‑coder:14b
[GENERAZIONE FINALE]:
  Attende VRAM ≥ 6000 MB (max 30s)
  generate_image(1024×1024, 6-12 step LCM, best_prompt)
  Restituisce URL diretto via Caddy (no base64 inline)
```

---

## 7. Componenti e file del progetto

### Struttura directory
```
~/ai-sessioni/
├── start_ai_stack.sh
├── logs/
│   ├── patterns.jsonl
│   ├── evolution_proposals.jsonl
│   ├── orchestra_state.json
│   ├── rag_service.log
│   └── backups/
│       └── *.bak
├── document-ai/
│   ├── knowledge/
│   │   └── hardware-report.md
│   ├── system/           ← COPIE dei sorgenti Python (per RAG)
│   ├── scripts/
│   ├── config/
│   ├── image/
│   ├── routing_snapshots/
│   ├── 3d/
│   └── audio/
├── ollama/pipelines/          ← montata nel container Pipelines
│   ├── orchestra_manifold.py
│   ├── rag_filter.py
│   ├── image_loop.py
│   ├── orchestra_evolver.py
│   ├── embedding_utils.py
│   ├── pattern_logger.py
│   └── requirements.txt
├── rag/
│   ├── rag_service.py
│   └── rag_indexer_lib.py
└── ComfyUI/
    └── ...
```

### Versioni correnti dei file (15 Maggio 2026)

| File | Versione | Tipo | Stato |
|------|---------|------|-------|
| `orchestra_manifold.py` | **v3.8.1** | manifold | ✅ Stabile (rag_timeout_s=600) |
| `rag_filter.py` | **v1.6.1** | filter | ✅ Stabile (+ function detection) |
| `image_loop.py` | **v2.7.0** | pipe (lazy) | ✅ Stabile |
| `orchestra_evolver.py` | **v1.1** | module (lazy) | ✅ Stabile |
| `embedding_utils.py` | **v2.0.0** | utility | ✅ Stabile (VRAM daemon + cache) |
| `pattern_logger.py` | **v1.0** | utility | ✅ Stabile |
| `rag_indexer_lib.py` | **v2.1.1** | libreria RAG | ✅ Stabile (AST chunking) |
| `rag_service.py` | **v1.3.1** | servizio RAG | ✅ Stabile (BATCH_SIZE=8) |

⚠️ **ATTENZIONE**: `orchestra_evolver.py` e `image_loop.py` sono importati dinamicamente dal manifold, **NON** devono essere registrati come pipeline autonome.

---

## 8. Sistema RAG

### Architettura
- **Indicizzazione**: `document-ai/` → `rag_indexer_lib.py` → fastembed nomic-embed-text-v1.5 (768d) → Qdrant collection "orchestra"
- **Retrieval**: query utente → fastembed (con cache a 3 livelli) → Qdrant top_k=6 → iniezione nel system message
- **AST Chunking**: per file `.py`, ogni funzione/metodo/classe diventa un chunk separato con metadati (`function_name`, `start_line`, `end_line`, `type`)
- **Function Detection**: `rag_filter.py` riconosce richieste di funzioni specifiche e cerca per `function_name`

### Modelli di embedding
| Modello | Dimensioni | Uso | Dove gira | RAM circa |
|---------|-----------|-----|-----------|-----------|
| `nomic-ai/nomic-embed-text-v1.5` | 768d | RAG (retrieval) | CPU in-process Pipelines | ~500MB |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 384d | Routing agenti | CPU in-process Pipelines | ~470MB |

### Collection Qdrant
| Collection | Dimensioni | Contenuto | Usata da |
|-----------|-----------|-----------|---------|
| `orchestra` | 768d | Chunk documenti knowledge base (incluso codice Python) | rag_filter.py |
| `orchestra_routing` | 384d | Frasi esempio per ogni agente + esempi auto‑generati | orchestra_manifold.py |

### Soglie e parametri RAG
| Parametro | Valore | Significato |
|-----------|--------|-------------|
| `top_k` | 6 | Chunk semantici massimi per query |
| `min_score` | 0.45 | Soglia similarità coseno minima |
| `max_context_chars` | 12000 | Budget caratteri contesto iniettato |
| `routing_threshold` | 0.45 | Soglia routing agenti |
| `BATCH_SIZE` | 8 | Chunk per batch durante indicizzazione (ridotto per OOM) |

### Procedura di indicizzazione
1. Copiare i file `.py` aggiornati in `~/ai-sessioni/document-ai/system/`
2. Assicurarsi che il RAG service sia attivo: `curl -s http://localhost:6335/health`
3. Se non attivo:
   ```bash
   cd ~/ai-sessioni/rag
   source ~/ai-sessioni/ComfyUI/venv/bin/activate
   nohup python rag_service.py > ../logs/rag_service.log 2>&1 &
   ```
4. Eseguire:
   ```bash
   curl -X POST http://localhost:6335/index --max-time 3600
   ```
5. Verificare:
   ```bash
   curl -s http://localhost:6335/status | python3 -m json.tool
   ```

---

## 9. Generazione immagini — Image Loop (v2.7.0)

### Workflow ComfyUI (nodi)
- Checkpoint: `sd_xl_base_1.0.safetensors`
- LCM-LoRA: `lcm-lora-sdxl.safetensors` (strength 1.0)
- Prompt negativo fisso, KSamplerAdvanced con sampler=lcm, scheduler=sgm_uniform, cfg=1.5

### Parametri per fase
| Fase | Risoluzione | Step LCM | CFG | Seed | Note |
|------|------------|---------|-----|------|------|
| Draft (iter 1‑3) | 512×512 | 4 | 1.5 | casuale | ~6s/draft |
| Finale standard | 1024×1024 | 6 | 1.5 | casuale | ~20s |
| Finale high‑quality (score≥9) | 1024×1024 | 12 | 1.5 | riutilizzato | ~35s |

### Miglioramenti chiave (v2.6.0 + v2.7.0)
- **VRAM daemon** (EVO-01): lettura VRAM a 0 ms, senza subprocess on‑demand.
- **Robustezza loop** (FIX-01): draft failure non abortisce l'intero loop.
- **Parsing JSON vision** (FIX-02): priorità al blocco ```json, parsing regex robusto.
- **Scarto draft peggiorativi**: ripristina il miglior prompt per l'iterazione successiva.
- **Divisione per zero guard** (FIX-03): nelle funzioni di selezione modelli.

---

## 10. Sistema di auto-evoluzione — Evolver (v1.1)

| Comando | Descrizione | Ruolo |
|---------|-------------|-------|
| `/evolve status` | Stato sistema, proposte, backup | Tutti |
| `/evolve analyze` | Analisi patterns.jsonl + proposte LLM | Tutti |
| `/evolve apply-code N` | Applica proposta N (backup+validazione+smoke test) | Admin |
| `/evolve rollback` | Ripristina ultimo backup | Admin |
| `/evolve update-routing` | Aggiorna esempi Qdrant da query non coperte | Admin |
| `/evolve routing-history` | Elenca snapshot di routing | Tutti |
| `/evolve routing-restore <file>` | Ripristina snapshot routing | Admin |
| `/evolve apply-config` | Guida modifica Valves OpenWebUI | Admin |

---

## 11. Strutture dati

- `patterns.jsonl`: eventi di sistema (user_message, ungrounded_response, generate_command, vision_fallback, comfyui_error, …)
- `evolution_proposals.jsonl`: proposte di evoluzione
- `orchestra_state.json`: stato dell'Evolver (cicli, modifiche applicate)
- Snapshot routing in `~/document-ai/routing_snapshots/routing_YYYYMMDD_HHMMSS.json`

---

## 12. Istruzioni operative

### Avvio stack completo
```bash
cd ~/ai-sessioni
bash start_ai_stack.sh
# ComfyUI e RAG service partono automaticamente con lo script
```

### Riavvio solo Pipelines
```bash
docker restart ai-pipelines-session && sleep 15
docker logs ai-pipelines-session --tail 30
```

### Riavvio solo RAG Service
```bash
cd ~/ai-sessioni/rag
source ~/ai-sessioni/ComfyUI/venv/bin/activate
nohup python rag_service.py > ../logs/rag_service.log 2>&1 &
cd ~
```

### Indicizzazione knowledge base
```bash
# Via OpenWebUI (admin): /rag index
# Via terminale:
curl -X POST http://localhost:6335/index --max-time 3600
```

### Verifica stato RAG
```bash
curl -s http://localhost:6335/status | python3 -m json.tool
```

### Controllare VRAM in tempo reale
```bash
watch -n 2 nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv
```

---

## 13. Vincoli assoluti

❌ NON usare `-e RESET_PIPELINES_DIR=true` — cancella tutti i file `.py`.
❌ NON modificare file Python senza backup manuale.
❌ NON introdurre nuovi file `.py` in `pipelines/` senza necessità.
❌ NON avviare due modelli LLM 7B+ contemporaneamente.
❌ NON avviare ComfyUI mentre un LLM 7B+ è in VRAM.
❌ NON rimuovere il mount nvidia-smi o `--gpus all` dal container Pipelines.

---

## 14. Task completati nell'ultimo ciclo evolutivo

| ID | Problema | Soluzione | File |
|----|----------|-----------|------|
| BUG-01 | ClassDef non ricorsiva (1 chunk monolitico) | Ricorsione nei metodi della classe | rag_indexer_lib v2.1.0 |
| BUG-02 | Infinite loop nel sub-chunking | Guardia `if sub_end >= total: break` | rag_indexer_lib v2.1.0 |
| BUG-03 | Chunk duplicato alla fine del testo | `if end >= n: break` dopo chunk | rag_indexer_lib v2.1.0 |
| BUG-04 | Scansione interrotta da un file problematico | Try/except per-file | rag_indexer_lib v2.1.0 |
| BUG-05 | Metadati AST scartati in upsert | `_make_payload()` include `function_name` etc. | rag_service v1.3.0 |
| BUG-06 | Client Qdrant zombie | Probe `get_collections()` prima di salvare | rag_service v1.3.0 |
| BUG-07 | Deploy senza rollback | Backup timestampato + ripristino su errore | rag_service v1.3.0 |
| BUG-08 | OOM killer su embedding batch grandi | BATCH_SIZE ridotto a 8 | rag_service v1.3.1 |
| FEAT-01 | Function detection nel filter | Pattern regex + ricerca per `function_name` | rag_filter v1.6.1 |
| FEAT-02 | Indicizzazione timeout aumentato | `rag_timeout_s` da 300 a 600 | manifold v3.8.1 |
| FEAT-03 | Nome semplice nei metadati | `function_name` senza prefisso classe | rag_indexer_lib v2.1.1 |

---

## 15. Osservazioni dall'analisi del codice (Maggio 2026)

1. **VRAM a freddo** – Nei primi secondi dopo l'avvio del container, il daemon VRAM non ha ancora completato il primo polling e restituisce 2000 MB. Impatto trascurabile.
2. **RAM limitata per RAG service** – Il servizio di indicizzazione usa fastembed su CPU e può consumare fino a 1.5 GB RAM. Con BATCH_SIZE=8 e ZRAM, il sistema è stabile ma va tenuto sotto controllo.
3. **Duplicazione logica selezione vision** – Sia il manifold che image_loop implementano la stessa logica. Centralizzare in futuro.
4. **Timeout ComfyUI attesa VRAM** – Usa contatore iterazioni invece di tempo reale.

---

## 16. Roadmap futura

| Priorità | Task | Dettaglio |
|----------|------|-----------|
| Immediato | Prompt ORCHESTRA_DEV arricchito | Migliorare qualità risposte analisi codice |
| Breve termine | Code Context Filter | Iniezione automatica codice da disco (Fase 2) |
| | /analyze command | Analisi approfondita file Python (Fase 3) |
| | pdfplumber | Sostituisce pypdf per estrazione PDF |
| Medio termine | Web Search (SearXNG) | Arricchimento risposte con web |
| | Chunking adattivo RAG | Chunk più piccoli per codice Python |
| Lungo termine | Evolver v2 | Auto-analisi basata su pattern_log |
| | Function Calling | Quando Ollama supporta KV-cache offload stabile |
| | Docker Compose | Centralizzare configurazione container |

---

## Appendice A — Variabili d'ambiente container Pipelines

```bash
PIPELINES_DIR=/app/pipelines
PATTERN_LOG_PATH=/app/logs/patterns.jsonl
FASTEMBED_CACHE=/app/pipelines/.fastembed_cache
OLLAMA_URL=http://ai-ollama-session:11434
DOCS_ROOT=/app/document-ai
PIPELINES_REQUIREMENTS_PATH=/app/pipelines/requirements.txt
# RESET_PIPELINES_DIR=true   # MAI!
```

## Appendice B — Valves principali

### orchestra_manifold.py (v3.8.1)
| Valve | Default | Descrizione |
|-------|---------|-------------|
| `ollama_url` | `http://ai-ollama-session:11434` | URL Ollama |
| `model_quality` | `qwen2.5-coder:14b-instruct-q4_K_M` | Modello quality mode |
| `model_fast` | `qwen3.5:9b` | Modello fast |
| `model_coordinator` | `llama3.2:3b` | Modello coordinator |
| `show_agent_header` | `true` | Mostra header agente |
| `strip_thinking_tags` | `true` | Rimuove tag `<think>` |
| `debug_log` | `false` | Log dettagliato |
| `routing_similarity_threshold` | `0.45` | Soglia routing |
| `rag_timeout_s` | **600** | Timeout indicizzazione RAG |

### rag_filter.py (v1.6.1)
| Valve | Default | Descrizione |
|-------|---------|-------------|
| `enabled` | `true` | Abilita RAG filter |
| `top_k` | `6` | Chunk massimi per query |
| `min_score` | `0.45` | Soglia similarità |
| `max_context_chars` | `12000` | Budget contesto |
| `debug_log` | `false` | Log dettagliato |

### image_loop.py (v2.7.0)
| Valve | Default | Descrizione |
|-------|---------|-------------|
| `draft_max_iter` | `3` | Iterazioni massime loop |
| `early_stop_score` | `9` | Score minimo per early stop |
| `preflight_enabled` | `false` | Ottimizzazione pre‑prompt |

---

## Appendice C — Checklist pre-modifica codice

Prima di modificare qualsiasi file Python:

- [ ] 1. Ho letto la sezione 13 (vincoli assoluti)?
- [ ] 2. Ho creato un backup manuale?
      ```bash
      cp ~/ai-sessioni/ollama/pipelines/FILE.py \
         ~/ai-sessioni/logs/backups/FILE.py.$(date +%Y%m%d_%H%M%S).bak
      ```
- [ ] 3. Ho verificato la sintassi con `ast.parse`?
      ```bash
      python3 -c "import ast; ast.parse(open('FILE.py').read()); print('OK')"
      ```
- [ ] 4. Ho riavviato Pipelines e letto i log?
      ```bash
      docker restart ai-pipelines-session && sleep 15
      docker logs ai-pipelines-session --tail 30
      ```
- [ ] 5. Ho testato il comportamento manualmente in OpenWebUI?
- [ ] 6. Se ho modificato file Python, ho aggiornato le copie in `document-ai/system/` e re-indicizzato?
- [ ] 7. Se la modifica è critica, ho creato uno snapshot di routing?

      /evolve update-routing

