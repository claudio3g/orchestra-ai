# 🎼 ORCHESTRA — DOCUMENTO DI HANDOFF v9 (dual-GPU)

**Versione:** 9.0 · **Data:** 3 Ottobre 2026
**Stato:** implementato e coperto da 287 controlli automatici in simulazione (`bash tests/run_all.sh`).
**Da validare su hardware:** isolamento GPU, flash-attention con KV q8_0, pre-caricamento, power limit (vedi sezione 13).
**Sostituisce:** handoff v8 (sistema a GPU singola), archiviato in `docs/archive/ORCHESTRA_HANDOFF_v8.md`.

> ⚠️ **ISTRUZIONE CRITICA PER LA NUOVA AI**
> Leggi tutto prima di toccare file o scrivere codice. Il codice nel repository è la fonte di verità; questo handoff è la mappa.
> Regole anti-allucinazione e canale di scrittura: `AI_BOOTSTRAP.md`, `AI_READ_PROTOCOL.md`, `document-ai/AI_WORKFLOW.md`.
>
> **Approccio operativo:** conservativo (non cambiare ciò che funziona senza motivo tecnico) · incrementale (un cambiamento alla volta)
> · documentato (commenti nel codice) · verificato (smoke test prima di dichiarare successo).

---

## 1. Hardware e vincoli

| Componente | Specifica |
|-----------|-----------|
| OS | Ubuntu 24.04 LTS (host `nix-i9`, utente `claudio`, progetto `~/ai-sessioni/`) |
| CPU / RAM | Intel i9-13900HX (8P+16E, 32 thread) · 32 GB DDR5 + ZRAM |
| GPU **main** | NVIDIA RTX 3090, 24 GB, eGPU AOOSTAR AG02 su Thunderbolt 4 (link PCIe x4) |
| GPU **aux** | NVIDIA RTX 4060 Laptop, 8 GB (circa 7 GB liberi: il desktop ne usa circa 1.1 GB) |

Le due GPU **cooperano**, non sono alternative. Si identificano con l'**UUID** (`nvidia-smi -L`), mai con l'indice: nvidia-smi elenca la 4060 come 0 e la 3090 come 1, ma l'ordine CUDA predefinito è "più veloce prima".

### Vincoli non negoziabili
| Vincolo | Valore | Motivazione |
|---------|--------|-------------|
| Istanze Ollama | **una per GPU**, fissate con `--gpus device=<UUID>` | un'istanza su due GPU spezzerebbe i layer sul link TB4 (banda ≈ PCIe x4) |
| Modelli caricati sul main | fino a 2 (`OLLAMA_MAX_LOADED_MODELS=2` se la main ha ≥ 20 GB), 1 su GPU piccole | evita scambi continui sul link lento |
| Modelli sull'aux | 2 (coordinator ≈ 2 GB + vision) | 8 GB totali, ≈ 7 GB liberi |
| SDXL (ComfyUI) | ≈ 6.2 GB sulla GPU di `ORCHESTRA_COMFY_ROLE` (default `main`) | `image_loop` fa spazio scaricando gli LLM se serve |
| `qwen2.5-coder:32b` | ≈ 20 GB di pesi: solo con ≥ 20 GB sulla main; non convive con SDXL | scaricato dal launcher solo se la main ha ≥ 20000 MiB |
| Contesto del 32b | `num_ctx 12288` + KV `q8_0` | a 32768 la sola cache KV (f16) peserebbe ≈ 8.6 GB: non entra |
| Offload su RAM sull'eGPU | evitarlo | la banda TB4 lo penalizza: il modello deve stare tutto in VRAM |
| Scollegare la eGPU | solo a stack fermo | può bloccare driver e Docker |

**Degrado:** se la eGPU manca, `main` diventa la 4060 e `aux` resta vuota: valgono le soglie da 8 GB, un solo modello caricato, nessun Ollama aux (comportamento della v8).

### Soglie adattive (valves del manifold, valori di default)
| Decisione | Soglia | Esito |
|-----------|--------|-------|
| Testo, mode `quality`, VRAM main ≥ `vram_quality_full_mb` (11000) | | `qwen2.5-coder:14b` **tutto in GPU** (nessun `num_gpu`) |
| Testo, mode `quality`, RAM ≥ 8 GB, VRAM ≥ 4000 | sotto la soglia sopra | 14b con `num_gpu=20` (offload parziale, come v8) |
| Testo, VRAM ≥ 7000 / ≥ 5500 / altrimenti | | `qwen3.5:9b` / `llama3.1:8b` / `llama3.2:3b` |
| Vision, VRAM della GPU dove gira ≥ 5000 / ≥ 3000 / sotto | | `llava:7b` pieno / parziale / `moondream:v2` |
| Refine (`image_loop`), VRAM ≥ 6000 | | `qwen3.5:9b`; sotto: 14b con `num_gpu=12` |

La VRAM della vision è quella **dell'aux** se la vision gira lì (`get_gpu_free_mb("aux")`).

---

## 2. Architettura e cooperazione tra GPU

```
 Open WebUI ─▶ Pipelines (manifold · rag_filter · image_loop · evolver · bootstrap filter)
                  │ backend scelto per modello (_backend_for / _url_for)
      ┌───────────┼─────────────────────────────┐
      ▼                                           ▼
 Ollama MAIN  :11435  (RTX 3090)          Ollama AUX  :11436 (RTX 4060)
 specialisti · quality 14b · 32b · refine  coordinator llama3.2:3b · vision (llava/moondream)
      ▲  failover automatico se l'aux cade           (volume proprio ollama-aux-session)
 ComfyUI :8188  (GPU di ORCHESTRA_COMFY_ROLE, default main)      RAG service :6335 (host) → /vram per ruolo
```

- **Ruoli:** `ORCHESTRA_GPU_MAIN` e `ORCHESTRA_GPU_AUX` (UUID). Il launcher li rileva (main = più VRAM) e li esporta; `orchestra.env` (non versionato) permette override.
- **Backend per modello:** `aux_models` = `llama3.2:3b, moondream:v2, llava:7b` (nel manifold e in `image_loop`; devono coincidere con `AUX_MODELS` del launcher). Il main conserva **tutti** i modelli: se l'aux non risponde il manifold ricade sul main senza errori (verifica di salute in cache per 20 s; se l'aux risulta irraggiungibile quando si apre la connessione, la richiesta viene rifatta sul main; un errore a risposta già iniziata non viene rifatto).
- **Pre-caricamento (`image_loop`):** durante i draft di ComfyUI vengono caricati in parallelo vision (aux) e refine (main), solo se non rubano VRAM a SDXL (mai con GPU singola da 8 GB).
- **SDXL persistente:** ComfyUI viene svuotato (`/free`) solo se condivide la GPU col modello che sta per girare **e** la VRAM non basta. Con 24 GB liberi o GPU diverse resta caricato tra un draft e l'altro.
- **Spazio per SDXL:** se un LLM grande occupa la GPU di ComfyUI (es. il 32b), `image_loop` lo scarica via `/api/ps` prima di iniziare (`evict_llms_for_comfy`).
- **Keep-alive per ruolo:** aux ≥ `keep_alive_aux_s` (1800); main ≥ `keep_alive_main_s` (900) se la VRAM libera ≥ 11000 MB; altrimenti i valori storici (0/600/300).
- **Ollama:** `OLLAMA_FLASH_ATTENTION=1` e `OLLAMA_KV_CACHE_TYPE=q8_0` (dimezzano la cache del contesto; `ORCHESTRA_FLASH_ATTENTION=0` li disattiva).
- **Energia:** i modelli piccoli girano sulla 4060 (più efficiente per carichi leggeri); `orchestra_power.sh` imposta e misura i power limit.

### Monitor VRAM (invariato nel principio, esteso ai ruoli)
`nvidia-smi` → `rag_service` `GET /vram` (campi storici = main + array `gpus[]` con ruolo) → daemon di `embedding_utils` (≈ 5 s, `get_vram_free_mb()` = main, `get_gpu_free_mb("main"|"aux")`) → manifold e `image_loop`. Fallback `subprocess` solo se `embedding_utils` non è importabile. Con rag_service vecchio lo snapshot per ruolo è vuoto (compatibile).

---

## 3. Servizi e porte

| Servizio | Contenitore / processo | Porta | Bind |
|----------|------------------------|-------|------|
| Ollama main | `ai-ollama-session` | 11435 → 11434 | `127.0.0.1` |
| Ollama aux | `ai-ollama-aux-session` | 11436 → 11434 | `127.0.0.1` |
| Open WebUI | `ai-webui-session` | 3001 → 8080 | IP LAN `192.168.1.51` |
| Pipelines | `ai-pipelines-session` | 9099 | `127.0.0.1` (mount `~/ai-sessioni` → `/app/ai:ro`) |
| Qdrant | `ai-qdrant-session` | 6333 | `127.0.0.1` |
| RAG service | processo host (venv di ComfyUI) | 6335 | `0.0.0.0`, limitato da `ufw` ai bridge Docker |
| ComfyUI | processo host (avviato dal launcher) | 8188 | `0.0.0.0`, limitato da `ufw` |

Le variabili d'ambiente di un container sono fissate alla creazione: il launcher ricrea Ollama (main/aux) e Pipelines se cambiano `ORCHESTRA_GPU_*`, `OLLAMA_AUX_URL`, `ORCHESTRA_COMFY_ROLE`, flash-attention/KV o il numero di modelli caricabili (i volumi nominati non vengono toccati). Pipelines riceve anche `OLLAMA_AUX_URL`.

---

## 4. Modelli

| Modello | VRAM (stima) | Backend | Ruolo |
|---------|--------------|---------|-------|
| `llama3.2:3b` | ≈ 2 GB | aux (riserva: main) | coordinator, emergenza, preflight |
| `moondream:v2` | ≈ 1.7 GB | aux | vision di riserva |
| `llava:7b` | ≈ 4.7 GB | aux | vision primario |
| `qwen3.5:9b` | ≈ 6.6 GB | main | specialista, refine |
| `llama3.1:8b` | ≈ 5 GB | main | testo di riserva |
| `qwen2.5-coder:14b-instruct-q4_K_M` | ≈ 9 GB | main | quality, evolver |
| `qwen2.5-coder:32b` | ≈ 20 GB + KV | main (solo ≥ 20 GB) | modello base di `Modelfile-orchestra` (`num_ctx 12288`); oggi nessun agente lo usa |

Il controllo "modello presente" del launcher confronta ora **nome:tag esatto** (prima il 32b non veniva mai scaricato perché il nome `qwen2.5-coder` era già presente).

---

## 5. Componenti e versioni correnti

| File | Versione | Note |
|------|----------|------|
| `start_ai_stack.sh` | v3.9 | ruoli GPU, Ollama main/aux, ricreazione per env, power opzionale, ComfyUI sulla GPU del ruolo |
| `start_comfyui.sh` | — | avvio standalone; stessa logica GPU (`--lowvram` sulle GPU piccole) |
| `document-ai/scripts/orchestra_gpu_env.sh` | — | libreria condivisa (ruoli, ricreazione condizionale, `ollama_has_model`, `comfyui_gpu_setup`) |
| `ollama/pipelines/orchestra_manifold.py` | v3.9.0 | backend per ruolo, quality in GPU, keep-alive per ruolo |
| `ollama/pipelines/image_loop.py` | v2.8.0 | vedi sezione 7 |
| `ollama/pipelines/embedding_utils.py` | v2.0.3 | snapshot VRAM per ruolo |
| `ollama/pipelines/rag_filter.py` | v1.6.0 | invariato |
| `ollama/pipelines/orchestra_bootstrap.py` | v1.1.0 | **filtro** (`type="filter"`), contesto compatto ≈ 2.9 KB (era ≈ 11.4 KB) |
| `rag/rag_service.py` | v1.5.1 | `/vram` multi-GPU |
| `ollama/pipelines/orchestra_evolver.py` | — | invariato (vedi v8 archiviata) |

Script operativi (`document-ai/scripts/`): `orchestra_sync.sh` (allineamento sicuro della copia locale), `egpu_check.sh` (diagnostica), `orchestra_bench_models.sh` (velocità e concorrenza per modello), `orchestra_smoke_test.sh` (verifica isolamento), `orchestra_power.sh` (consumi), `generate_ai_context.sh` (rigenera manifest, bundle e la sezione file di `AI_BOOTSTRAP.md`, includendo i file nuovi non ancora in staging).
Suite di test: `tests/` (`bash tests/run_all.sh`): endpoint `/vram`, daemon, manifold, image_loop, bootstrap, launcher (11 scenari), consumi, smoke test.

---

## 6. Flusso dei messaggi

1. `rag_filter` (inlet) inietta il contesto RAG; il filtro `orchestra_bootstrap` aggiunge le regole compatte del progetto.
2. Il manifold instrada all'agente (routing per embedding, soglia 0.45, altrimenti `coordinator`).
3. `select_mode` (RAM) → `select_text_model(mode, stats)` con la VRAM **del main** → `(modello, opzioni)`.
4. `stream_ollama` sceglie il backend (`_backend_for`), applica il keep-alive per ruolo e fa streaming; failover aux→main se l'aux è irraggiungibile prima del primo output.
5. Il banner mostra la VRAM del main e, se attiva, `(+aux X GB)`.

### `/generate` (image_loop v2.8.0)
Preflight (coordinator su aux) → eventuale scarico degli LLM se manca spazio per SDXL → pre-caricamento paralleli → fino a 3 draft SDXL+LCM (512×512, 4 step) con analisi vision (aux) e refine (main) → render finale 1024×1024. Alla fine (anche in caso di errore) i modelli tenuti in memoria dal loop vengono scaricati. `keep_alive` è ora un parametro di primo livello (prima era dentro `options`, dove Ollama lo ignora).

---

## 7. RAG (invariato nel funzionamento)

- Indicizzazione: `document-ai/` (ricorsiva: **anche** `.py`, `.sh`, `.json`) → `rag_indexer_lib.py` → `nomic-embed-text-v1.5` (768d) → collection `orchestra`. Routing: `paraphrase-multilingual-MiniLM-L12-v2` (384d) → `orchestra_routing`.
- Il dominio `system` contiene **copie del codice** in `document-ai/system/` (non versionate, copiate a mano o con lo script locale di sincronizzazione): dopo ogni modifica al codice vanno aggiornate e re-indicizzate, altrimenti l'agente `orchestra_dev` vede codice vecchio.
- L'indicizzazione è incrementale per hash (`rag/.file_hash_cache.json`, non più versionato) e **non rimuove i chunk dei file cancellati o spostati**. Per ripulire: `curl -X DELETE http://127.0.0.1:6333/collections/orchestra`, cancellare `rag/.file_hash_cache.json`, riavviare il launcher (indicizza da solo se la collection è vuota) oppure `/rag index`.
- Soglie: `top_k=6`, `min_score=0.45`, `max_context_chars` 12000 nel codice (i file valves del repository usano 7500/3000: vale il pannello valves di Open WebUI).

---

## 8. Operazioni

```bash
cd ~/ai-sessioni && bash start_ai_stack.sh                 # avvio completo (Ollama main+aux, Qdrant, Pipelines, WebUI, RAG, ComfyUI)
bash document-ai/scripts/egpu_check.sh                     # prima volta: UUID e link PCIe
bash document-ai/scripts/orchestra_smoke_test.sh --load    # verifica: ruoli, isolamento, 100% GPU
bash tests/run_all.sh                                      # suite di test (simulata, nessuna GPU toccata)
bash document-ai/scripts/orchestra_power.sh bench eco balanced performance   # misura token/s, watt e token/joule
docker restart ai-pipelines-session && sleep 15 && docker logs ai-pipelines-session --tail 30   # solo Pipelines
curl -s localhost:6335/vram | python3 -m json.tool         # VRAM per ruolo
```
Variabili utili (in `orchestra.env`, vedi `document-ai/config/orchestra.env.example`): `ORCHESTRA_MAIN_PARALLEL` / `ORCHESTRA_AUX_PARALLEL` (richieste parallele per modello), `ORCHESTRA_HEAVY_MODEL` (modello pesante a scelta), `ORCHESTRA_AUX_OLLAMA=0` (4060 senza Ollama), `ORCHESTRA_COMFY_ROLE=aux|main`, `ORCHESTRA_FLASH_ATTENTION=0`, `ORCHESTRA_POWER_PROFILE=eco|balanced|performance`, `COMFY_EXTRA_ARGS`.

**Varianti di ripartizione:** (a) *default*: coordinator e vision su aux, tutto il resto + SDXL su main; (b) 4060 solo per ComfyUI: `ORCHESTRA_AUX_OLLAMA=0 ORCHESTRA_COMFY_ROLE=aux` — il 32b ha tutta la 3090.

---

## 9. Vincoli assoluti

❌ NON usare `-e RESET_PIPELINES_DIR=true` (cancella i `.py`). ❌ NON modificare file Python senza backup. ❌ NON introdurre nuovi `.py` in `pipelines/` senza necessità.
❌ NON usare `--gpus all` per Ollama: una istanza per GPU (fa eccezione il container Pipelines, che mantiene `--gpus all` e il mount di `nvidia-smi` per il fallback).
❌ NON avviare più modelli grandi di quanti la VRAM della **singola GPU** ne regga (la regola "un solo LLM" della v8 vale ora per GPU; sulla 3090 ne reggono due).
❌ NON scollegare la eGPU con lo stack attivo. ❌ NON usare indici GPU al posto degli UUID. ❌ NON modificare a mano `AI_MANIFEST.md`, `AI_CTX_*.md` (rigenerati dal workflow).
❌ NON mettere apostrofi o virgolette nei messaggi di commit inviati via `ai-dispatch` (il workflow li interpola in uno script shell).

---

## 10. Checklist pre-modifica

1. Ho letto i vincoli (sezione 9) e `AI_BOOTSTRAP.md`?
2. Backup: `cp FILE ~/ai-sessioni/logs/backups/FILE.$(date +%Y%m%d_%H%M%S).bak`
3. Sintassi: `python3 -c "import ast; ast.parse(open('FILE').read())"` / `bash -n FILE`
4. Test: `bash tests/run_all.sh` (deve restare verde; aggiungere test per ogni nuovo comportamento)
5. Riavvio Pipelines e lettura log; prova manuale in Open WebUI; `orchestra_smoke_test.sh --load` se si è toccato GPU/backend
6. Aggiornare `document-ai/system/` e re-indicizzare se si è modificato codice
7. Snapshot di routing se critico: `/evolve update-routing`

---

## 11. Valves aggiunti in v9

**Manifold:** `ollama_url_aux` (env `OLLAMA_AUX_URL`, vuoto = aux spento), `aux_models`, `aux_health_ttl_s=20`, `vram_quality_full_mb=11000`, `keep_alive_aux_s=1800`, `keep_alive_main_s=900`.
**image_loop:** `ollama_url_aux`, `aux_models`, `aux_health_ttl_s`, `comfy_role` (env `ORCHESTRA_COMFY_ROLE`), `loop_keep_alive_s=300`, `warmup_enabled=true`, `warmup_main_min_free_mb=16000`, `final_min_free_mb=6000`, `evict_llms_for_comfy=true`.
**bootstrap:** `pipelines=["*"]`, `priority`, `sections`, `include_file_list=false`, `include_manifest=false`, `max_bytes=3500`.

---

## 12. Come si scrive sul repository

Il canale previsto è `.github/workflows/ai-commit.yml` (`repository_dispatch`, evento `ai-update`): patch `diff --git` in base64, validazione, test (sintassi Python/Bash/YAML/JSON, shellcheck) e commit del bot sul branch indicato, con rigenerazione di manifest e bundle. Il `client_payload` ha un limite di dimensione: patch piccole, un cambiamento per volta. Dettagli in `document-ai/AI_WORKFLOW.md`.

---

## 13. Da validare sull'hardware (non verificabile in simulazione)

1. `orchestra_smoke_test.sh --load`: ogni modello cresce sulla GPU giusta e non sull'altra, 100% GPU.
2. Flash-attention + KV `q8_0`: qualità/velocità con i modelli in uso (`ORCHESTRA_FLASH_ATTENTION=0` per tornare indietro); `ollama ps` con il 32b a `num_ctx 12288`.
3. Tempi di caricamento sul link TB4 e beneficio reale di keep-alive e pre-caricamento.
4. Link PCIe sotto carico (`pcie.link.gen.current` a riposo è Gen1).
5. `orchestra_power.sh bench eco balanced performance`: adottare un profilo solo se i token/joule migliorano e i token/s restano accettabili.
6. ComfyUI sulla 3090 senza `--cpu-vae`: tempo del draft e del render finale.

## 14. Roadmap
- Decisione architetturale (un agente forte sulla 3090 + strato sempre attivo sulla 4060) e raccomandazioni sui modelli: `ARCHITETTURA_AGENTI_E_MODELLI.md`. Versioning e rollback: `docs/VERSIONING.md`.
- Pipeline immagini a due stadi (bozze su 4060, render finale sul modello top della 3090).
- Usare il 32b (`orchestra`) per `orchestra_dev`/`reasoner` quando la main è libera.
- Metriche per GPU in `/status` e nei log di pattern.
- Spostare i valori fissi del launcher (`192.168.1.51`, percorsi) in `orchestra.env`.
- Indurre il workflow: passare `client_payload` tramite `env:` invece di interpolarlo nello script.
