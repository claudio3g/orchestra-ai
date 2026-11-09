# Orchestra AI

> 🇮🇹 [Versione italiana](README.it.md)

**Orchestra** is a self-hosted, local-first AI workspace built around **Ollama, Open WebUI, custom Pipelines, RAG and Qdrant**.

The project is designed to run AI services locally, keep the user's knowledge base under local control, and combine general-purpose LLMs, document retrieval, custom processing pipelines and persistent vector storage in a single environment.

> **Status:** active personal/local project  
> **Primary target:** Linux + NVIDIA GPU + Docker  
> **Repository:** https://github.com/claudio3g/orchestra-ai

---

## 🤖 HANDOFF PER AGENTI AI — leggere per primo

> **Stato al 9 novembre 2026** · Questa sezione è scritta in italiano (lingua di lavoro dell'utente) perché un agente AI possa **riprendere il lavoro da dove è stato lasciato** senza rileggere le conversazioni. Il resto del README (in inglese) descrive il progetto; il dettaglio storico completo (decisioni D1–D23, bug B1–B19, misure) è in [`docs/HANDOFF_PROSSIMA_SESSIONE.md`](docs/HANDOFF_PROSSIMA_SESSIONE.md).
>
> **Convenzioni.** «**Verificato**» = misurato sull'hardware reale dall'utente o dimostrato da un test. «**Simulato**» = coperto solo da test con stub (nessuna GPU/Docker/rete reali). «**Ipotesi**» = ragionamento non confermato. «**Fonte di terzi**» = dichiarazione non verificata da noi. **Questo file non contiene segreti e non deve mai contenerne** (nessun token, nessuna chiave API: nemmeno parziali).

### 0. Come usare questo documento (ordine di lettura)

1. Questa sezione (stato, regole, problemi aperti, prossimi passi).
2. [`docs/VSCODE_CONTINUE.md`](docs/VSCODE_CONTINUE.md) — integrazione VS Code + Continue (verificata il 9 novembre).
3. [`docs/HANDOFF_PROSSIMA_SESSIONE.md`](docs/HANDOFF_PROSSIMA_SESSIONE.md) — storia, decisioni, bug, misure, procedure (è lungo: consultarlo per argomento).
4. [`AI_BOOTSTRAP.md`](AI_BOOTSTRAP.md), [`AI_READ_PROTOCOL.md`](AI_READ_PROTOCOL.md), `document-ai/AI_WORKFLOW.md` — regole anti-allucinazione e canale di scrittura dell'utente.
5. `document-ai/knowledge/` — `ORCHESTRA_HANDOFF_v9.md` (architettura dual-GPU), `ARCHITETTURA_AGENTI_E_MODELLI.md` (decisione architetturale e modelli candidati), `ORCHESTRA_3090_MIGRAZIONE.md`, `hardware-report.md` (dump del 2026-03-31: **hardware superato**, vedi §3).
6. [`docs/VERSIONING.md`](docs/VERSIONING.md) — tag, rollback, regole per le modifiche dell'AI.

**Regola d'oro: verifica sempre prima di fidarti.** L'utente modifica spesso il sistema in locale e altre AI hanno fatto modifiche senza aggiornare i documenti. Cifre, versioni, percorsi e stato dei branch qui sotto sono quelli del 9 novembre 2026: riverificali (`git fetch --all --tags`, `git log`, `docker ps`, `ollama list`) prima di agire.

### 1. L'utente, le direttive e il metodo di lavoro

- **Utente:** Claudio (`claudio3g`), parla **italiano**; macchina `nix-i9`, utente di sistema `claudio`, progetto in `~/ai-sessioni`. Rispondere in italiano, in modo chiaro e diretto.
- **Ruolo richiesto all'AI:** ingegnere senior AI (sistemi self-hosted con vincoli hardware, RAG, Ollama, pipeline OpenWebUI, ComfyUI/SDXL).
- **Metodo (preferenze dichiarate):** *chiaro e semplice* · *conservativo* (non modificare ciò che funziona senza motivo tecnico preciso) · *incrementale* (un cambiamento alla volta, testato prima di procedere) · *documentato* (ogni modifica spiegata nei commenti del codice) · *verificato* (smoke test prima di dichiarare successo). Il vincolo storico «≤ 8 GB di VRAM» è **superato** dall'hardware attuale (24 + 8 GB), ma il metodo resta.
- **DIRETTIVA SUI MODELLI (9 novembre, da rispettare sempre):** i modelli devono girare **in locale** (Ollama). I modelli online (**DeepSeek**, poi eventualmente Claude) sono solo un **ripiego manuale per compiti eccessivamente complicati** per i modelli locali; **mai** il modello predefinito, mai proposti come soluzione ordinaria. Ordine: *prima locali, poi online*. *Errore da non ripetere:* il 9 novembre l'AI ha proposto la chat NORMAL su DeepSeek, contro questa direttiva; l'utente ha corretto.
- **Segreti:** la chiave DeepSeek sta in `~/.continue/.env` (permessi 600) e in `~/.config/continue/env.sh`, **fuori dal repository**; l'utente ha deciso che **resta invariata fino a progetto completato** (poi verrà ruotata: non ricordarlo prima). Non leggere, non stampare, non committare mai quei file, `.orchestra_token`, `.webui_secret_key`, `~/.orchestra_github_token`. L'utente ha esposto in passato token GitHub nelle chat: vanno **revocati/eliminati a progetto sistemato** (decisione sua). Prima di allegare o pubblicare file: `grep -n -E 'sk-[A-Za-z0-9]{10,}|github_pat_|ghp_' <file>`.
- **Onestà:** distinguere sempre misurato / simulato / ipotizzato / fonte di terzi; ammettere subito i propri errori; riportare quando una spiegazione proposta dall'utente (o dall'AI) non è sostenuta dai dati.
- **Consumo di token:** l'utente ha un account Claude gratuito: risposte brevi, file compatti, niente riletture inutili.

### 2. Stato del repository al 9 novembre 2026

| Elemento | Stato |
|----------|-------|
| **Ramo principale** | **`main`** (`ddf69b1`, tag annotato **`punto-zero-2026-11-09`**: è il **punto di partenza** del progetto da qui in avanti). Dal 9 novembre è l'**unico ramo sul remoto** (verificato con `git ls-remote`): tutto il lavoro di `dual-gpu-final`, `feat-*`, `fix-vision-esempio` e `allinea-locale-nov` è stato unito (PR #3 con «Rebase and merge», albero identico verificato). Le modifiche passano da un **ramo nuovo + PR** che l'utente unisce. |
| Identificativi dei commit | Il rebase ha **cambiato gli SHA**: gli hash citati in questo documento e nell'handoff storico per il lavoro fino al 9 novembre (`8b857dc`, `78c160e`, `f8d1d2c`, …) non sono antenati di `main`, ma restano raggiungibili dai tag `dual-gpu-rc*` e `archivio/allinea-locale-nov`. |
| PR | #1, #3, #4 unite; **#2 chiusa senza unione** (superata, i suoi commit sono in `main`). Nessuna PR aperta. Merge consigliato: «Rebase and merge» o «Create a merge commit», **mai Squash** (si perderebbero i commit separati voluti per il rollback). |
| Tag annotati | `punto-zero-2026-11-09` (= `main` unificato), `pre-dual-gpu`, `dual-gpu-rc1` … `dual-gpu-rc10`, `archivio/allinea-locale-nov`, `archivio/dual-gpu-step1`, `archivio/dual-gpu-step2a` (conservano i rami cancellati). Rollback: `punto-zero-2026-11-09`, poi `rc9` (`94cc871`), più indietro `rc8` (`449b7c0`). **`rc7` non avvia le versioni recenti di ComfyUI** (B16). |
| Cartella locale dell'utente | Su `main` (`ddf69b1`), allineata con `orchestra_sync.sh main` il 9 novembre alle 16:39 (backup in `~/orchestra-backup-locale/20261109_163942`). I test finali **sono stati saltati** sul PC (mancano `flask pydantic requests`). File locali non tracciati noti: `document-ai/knowledge/HANDOFF_PROSSIMA_SESSIONE.md` (copia obsoleta da eliminare), `orchestra.env`, `ComfyUI/`, `.orchestra_token`, `.webui_secret_key`. |
| Test | `bash tests/run_all.sh` — vedi §12 per l'esito dell'ultima esecuzione. Tutti i test sono **simulati** (stub). |

**Canali di scrittura per un'AI.** Da una sessione Claude Code: clonare, lavorare su un **ramo nuovo** e fare `git push` (funziona); **non** si possono cancellare rami né usare le API di scrittura (PR, merge, segreti): le fa l'utente su GitHub. Il clone della sessione è *shallow*: un hook può segnalare «commit non pubblicati» anche se il remoto è identico → verificare con `git ls-remote origin <ramo>` e **non** fare push duplicati. Canale alternativo storico: workflow `ai-dispatch` (`.github/workflows/ai-commit.yml`, segreto `ORCHESTRA_PAT`, verificato valido a ottobre; `branch` sempre esplicito, messaggi senza apostrofi; i file `AI_MANIFEST.md`, `AI_CTX_*.md` e la tabella di `AI_BOOTSTRAP.md` sono **generati**: non modificarli a mano).

**Regole di pubblicazione.** Commit piccoli e separati (`git add` con percorsi espliciti, mai `git add -A` generico), messaggi senza apostrofi/virgolette, test verdi prima del push, **mai `push --force`**, mai `git reset --hard` senza backup, tag annotato per ogni stato rilevante. `.gitignore` è una **lista bianca** (`/*` ignorato): un nuovo file in root o una nuova cartella richiede una regola `!/…`.

### 3. Hardware e ambiente (9 novembre 2026)

| Voce | Valore |
|------|--------|
| Macchina | Acer Predator Helios Neo 16 (PHN16-71), hostname `nix-i9`; Ubuntu 24.04 LTS in dual boot con Windows 11 |
| CPU / RAM | Intel i9-13900HX, 32 GB |
| GPU **main** | NVIDIA **RTX 3090 24 GB**, eGPU **AOOSTAR AG02** su **Thunderbolt 4** (PCIe x4) |
| GPU **aux** | NVIDIA **RTX 4060 Laptop 8 GB**, interna (≈ 1,1 GB occupati dal desktop) |
| Driver | NVIDIA **595.58.03 (CUDA 13.2)** (dal documento di migrazione disco del 6 nov; il report hardware del 31 marzo diceva 590.48.01) |
| Ollama | immagine `ollama/ollama:latest`; versione 0.40.0 rilevata nelle misure di ottobre (una versione più vecchia dava HTTP 412 su `qwen3.8:27b`): riverificare con `docker exec ai-ollama-session ollama --version` |
| Indici GPU | `nvidia-smi`: 4060 = 0, 3090 = 1; CUDA ordina «più veloce prima»: **il progetto usa gli UUID** (vedi sotto l'eccezione `ai-tools`). |
| Rete | Open WebUI su IP LAN fisso `192.168.1.51:3001`; reverse proxy `192.168.1.101:81`; RAG (6335) e ComfyUI (8188) su `0.0.0.0` protetti da `ufw` (solo bridge Docker) |

**Dischi (documento utente del 6 novembre «HANDOFF-disk-nvme-2», non nel repo; i nomi `nvme0n1`/`nvme1n1` si scambiano a ogni riavvio → ovunque si usano gli UUID):**
- **Disco 1 (sistema, Micron 3400, ≈ 954 GB):** EFI, partizioni Windows 11, root Linux `ext4` ≈ 391 GB su `/` (il 9 novembre `/dev/nvme0n1p5` 384 G, **50% usato dopo la pulizia**, 185 G liberi).
- **Disco 2 (dati, Crucial P5 Plus, ≈ 931 GB, ex disco USB):** partizione **ext4 621 GB, label `data`, UUID `f8b4bd92-1b7d-4a9a-a9e1-8ca180e6b873`, montata su `/mnt/data`** (`defaults,noatime`, riga in `/etc/fstab`); in fondo al disco partizione **NTFS 300 GB** (label `data`, UUID `01ED820B0D193A56`) **non montata al boot** (la gestisce udisks2 quando serve; label uguale ma filesystem diverso). BitLocker su Windows: non verificato.
- **Uso di `/mnt/data`:** 206 GB usati; modelli Ollama **136 GB** in `/mnt/data/ollama-models/ollama-session-ext`, modelli ComfyUI ≈ 71 GB in `/mnt/data/comfyui-models`. Symlink: `~/ai-sessioni/ai-data` → `/mnt/data`; `~/ai-sessioni/ComfyUI/models` → `/mnt/data/comfyui-models`.
- **Volume aux:** `ollama-aux-session` è un volume Docker sul **disco di sistema** (modelli leggeri soltanto).
- **Docker recuperabile non ancora ripulito** (serve il consenso dell'utente): ≈ 8,7 GB di build cache e ≈ 7 GB di due tag inutilizzati di `open-webui`. `/home` (≈ 73 GB) non ispezionato.
- **Eccezione UUID/indice (container `ai-tools-ollama`, parte del progetto `ai-tools` dell'utente, porta 11499, store dei modelli condiviso con `ai-ollama-session`):** dopo il riavvio l'assegnazione GPU per UUID falliva («unknown device»); risolto con `sudo nvidia-ctk runtime configure --runtime=docker`, `sudo systemctl restart docker` e ricreando il container con `--gpus '"device=1"'` (indice della 3090). Se il launcher dovesse mostrare lo stesso errore con gli UUID, la prima cosa da provare è la riconfigurazione del runtime. Il documento non descrive alcuna modifica al collegamento Thunderbolt.
- All'avvio del PC **nessun container Ollama parte da solo**: bisogna lanciare gli script in `~/ai-sessioni`.

### 4. Architettura e modalità FULL / NORMAL

```text
Open WebUI :3001 ──► Pipelines :9099 (manifold, image_loop, bootstrap, rag_filter, evolver)
                         │                         │
        ┌────────────────┴───────┐        RAG service :6335 (host, venv di ComfyUI) ──► Qdrant :6333
        │                        │
 Ollama MAIN :11435 (3090)   Ollama AUX :11436 (4060)        ComfyUI :8188 (SDXL, sulla GPU main)
 modelli pesanti, failover   coordinator + vision
```

- **Ruoli per UUID:** main = GPU con più VRAM (3090), aux = la successiva (4060). Una istanza Ollama **per GPU** (`ai-ollama-session` :11435, `ai-ollama-aux-session` :11436), `OLLAMA_KEEP_ALIVE=0` sul main (il modello si scarica subito dopo ogni richiesta: una chiamata a freddo può richiedere ~10 s), 10 minuti sull'aux; `MAX_LOADED_MODELS=2` sul main se ≥ 20 GB, altrimenti 1; aux 2. Il main conserva **tutti** i modelli e fa da failover dell'aux.
- **Modalità FULL** = 2 GPU visibili (3090 + 4060). **Modalità NORMAL** = 1 GPU (eGPU scollegata o caduta): la 4060 diventa **main** su `:11435`, **non esiste Ollama aux** (`aux_ollama_enabled` richiede `ORCHESTRA_GPU_AUX` non vuoto, **verificato nel codice**) e c'è **un solo modello caricato alla volta**. Chat e autocomplete in NORMAL non possono quindi coesistere.
- **Container e porte:** `ai-ollama-session` 11435, `ai-ollama-aux-session` 11436, `ai-webui-session` 3001→8080, `ai-pipelines-session` 9099, `ai-qdrant-session` 6333; RAG 6335 e ComfyUI 8188 sono processi host. Rete `ollama_default`. Collection Qdrant: `orchestra` (RAG), `orchestra_routing`. Il launcher **ferma i container all'avvio e all'uscita**.
- **Launcher `start_ai_stack.sh` (v3.9 + modifiche locali dell'utente del 6–7 nov):** `EXTERNAL_DISK_MOUNT="/mnt/data"`; store dei modelli `OLLAMA_MODELS_SRC="${ORCHESTRA_MODELS_SRC:-/mnt/data/ollama-models/ollama-session-ext}"` (**rollback senza toccare il file:** `ORCHESTRA_MODELS_SRC=ollama-session`, il vecchio volume Docker); `HOST_IP=$(ip route get 1.1.1.1 | awk '{print $7; exit}')` (fragile: non gestisce l'assenza di rotta); health check di Open WebUI con `docker exec … curl http://localhost:8080/health`. Modelli: `REQUIRED_MODELS` = `llama3.2:3b`, `qwen3.5:9b`, `llama3.1:8b`, `qwen2.5-coder:14b-instruct-q4_K_M`, `qwen2.5-coder:32b` (solo con ≥ 20 GB sul main), `llava:7b`, `moondream:v2`; `AUX_MODELS` = `llama3.2:3b`, `moondream:v2`, `llava:7b` (devono coincidere col valve `aux_models`); `ORCHESTRA_HEAVY_MODEL` aggiunge un modello pesante (download non fatale).
- **Librerie e componenti (versioni verificate nei sorgenti):** `orchestra_gpu_env.sh` (logica GPU condivisa), `orchestra_manifold.py` v3.9.0, `image_loop.py` v2.8.0, `embedding_utils.py` v2.0.3, `orchestra_bootstrap.py` v1.1.0, `rag_filter.py` v1.6.0, `rag_service.py` v1.5.1, `Modelfile-orchestra` (`num_ctx 12288`). Tutte le variabili opzionali e i valve sono nella sezione *GPU support* di questo README, in `docs/HANDOFF_PROSSIMA_SESSIONE.md` §10 e in `document-ai/config/orchestra.env.example`.

### 5. Modelli

**Installati (9 novembre, `ollama list`).** *Main* (store su `/mnt/data`): `qwen3.8:27b` (17 GB), `qwen3:30b-a3b` (18 GB, MoE), `qwen3.5:9b` (6,6 GB; **compare due volte**, id `2e16a80fe3d7` e `c97eb11d70b1`, e una voce `llamacpp:c97eb11d…` 6,6 GB di origine da chiarire), `orchestra:latest` (19 GB), `qwen2.5-coder:32b` (19 GB), `deepseek-r1:7b/14b/32b` (4,7/9,0/19 GB), `qwen2.5:32b` (19 GB), `llama3.2:3b` (2,0 GB), `qwen2.5-coder:14b-instruct-q4_K_M` (9,0 GB), `moondream:v2` (1,7 GB), `llava:7b` (4,7 GB), `llama3.1:8b` (4,9 GB). *Aux* (volume su disco di sistema): `llava:7b`, `moondream:v2`, `llama3.2:3b`. **`qwen2.5-coder:1.5b`** (autocomplete di Continue) è stato scaricato sull'aux (`ollama pull`, riuscito). Candidati alla rimozione, **solo con consenso**: `deepseek-r1` ×3 e `qwen2.5:32b` (≈ 52 GB insieme), duplicato `qwen3.5:9b`.

**Misure sull'hardware (utente, serie 2–3 di ottobre; dettagli e caveat in `docs/HANDOFF_PROSSIMA_SESSIONE.md` §7).**

| Modello | Flussi | tok/s per flusso | tok/s totali | Note |
|---------|--------|------------------|--------------|------|
| `qwen2.5-coder:14b-instruct-q4_K_M` | 1 / 2 / 3 | 73,2 / 66,7 / 56,7 | 72 / 129 / 165 | 100% GPU, 11 GB, contesto 8192; scala in modo regolare (2,24–2,28× a 3 flussi) |
| `qwen3:30b-a3b` (MoE, ~3B attivi) | 1 / 2 / 3 | 167,4 / 125,8 / 101,9 | 148 / 238,5 / 166,5 | calo a 3 flussi: **causa non provata** (la spiegazione «Thunderbolt saturo» NON è sostenuta dai dati: serve `--repeat 3 --telemetry`) |
| `qwen3.8:27b` | 1 / 2 | 26,4 / 23,9 | 23,5 / 22,0 | 80% GPU per 7 GB occupati da ComfyUI (B14): **non è un giudizio sul modello** |

**Banco di prova (`orchestra_eval.py`, 18 ottobre, 2 esecuzioni per cella, controlli poi calibrati):** 3B 40%, 32B 40%, MoE 60%. Fatti osservati: il 3B rifiuta anche fatti noti, a temperatura 0,1 propone `rsync` con `/` come sorgente, il MoE può essere troncato dal limite di token mentre ragiona. Il banco misura modello+temperatura+profilo, **non** la pipeline completa. Decisione ancora da prendere sui numeri: passo B1 (`strict_mode` nel manifold, «profilo rigore»).

**Test con Continue (9 novembre):** `llama3.1:8b` su `:11435`: risposta alla domanda `def somma(a, b)` corretta in ~1 s (3090 presente e contenitore main; **non** è una misura della sola 4060); richiesta a freddo ~10,6 s (KEEP_ALIVE=0). `ollama show` dichiara `tools` per `llama3.1:8b` e `qwen2.5-coder:14b-instruct-q4_K_M` (quest'ultimo anche `insert`).

### 6. VS Code + Continue (verificato il 9 novembre; dettagli in `docs/VSCODE_CONTINUE.md`)

- Script nel repo in `document-ai/scripts/vscode/`: `vsc-orchestra.sh [normal|full] [--dry-run]` (rileva le GPU con `orchestra-detect-gpu.sh`, ≥ 2 GPU = FULL, fa backup di `~/.continue/config.yaml`, copia la config della modalità, avvia `code ~/ai-sessioni`), `orchestra-detect-gpu.sh`, `continue/config.full.yaml`, `continue/config.normal.yaml` (senza segreti), `settings.workspace.json`. **Copie attive sul PC:** `~/.local/bin/vsc-orchestra.sh`, `~/.local/bin/orchestra-detect-gpu.sh`, `~/.continue/config.{normal,full}.yaml` (`~/.continue/config.yaml` è **generato**: non modificarlo a mano). `~/.bashrc` (riga ~160) carica `~/.config/continue/env.sh`.
- **FULL:** chat/edit/apply `qwen2.5-coder:14b-instruct-q4_K_M` su `:11435`; autocomplete `qwen2.5-coder:1.5b` su **`:11436`** (aux; verificato: FIM con parametro `suffix`, ~0,4 s a caldo; prima puntava per errore a `:11435`). **NORMAL:** chat/edit/apply `llama3.1:8b` su **`:11435`**, **senza autocomplete** (un solo modello per volta sulla 4060; misura da fare prima di reintrodurlo). **DeepSeek** (`deepseek-flash`, anche `deepseek-v4-pro`) è presente solo come voce **da scegliere a mano** nell'interfaccia.
- **Trappole verificate:** (1) `${VAR}` nello YAML di Continue **non viene espanso** (inviato in chiaro → 401): sintassi valida `apiKey: ${{ secrets.DEEPSEEK_API_KEY }}` con la chiave in `~/.continue/.env`; (2) gli id dei modelli DeepSeek sono `deepseek-flash` e `deepseek-v4-pro` (i vecchi `deepseek-v4-flash` non esistono); (3) VS Code va **riavviato** per applicare una nuova config; (4) in modalità **Agent** il modello può chiamare `search_web` (servizio web di Continue, non locale: «Failed to fetch web context»): con i modelli locali usare la modalità **Chat**, oppure Agent con `search_web` **escluso** e gli strumenti su file/terminale su **«chiedi prima»**; (5) l'incidente B19 (ENOSPC) ha reso inutilizzabile anche `code --list-extensions`.
- **Prova Agent (9 novembre, cartella `~/prova-agent`, poi cancellata):** `llama3.1:8b` ha creato e letto `ciao.txt` scegliendo da solo gli strumenti giusti; Continue ha chiesto l'autorizzazione e l'utente ha confermato. **Non provati:** modifica di un file esistente, comandi nel terminale, uso del 14B in Agent. `search_web` è stato messo su «escluso» dall'utente (impostazione di Continue sul PC, non nel repo).
- **Claude (gratuito):** *Claude Code per VS Code* richiede un piano Claude a pagamento o un account Console (il piano gratuito di claude.ai non basta); Claude **dentro Continue** richiederebbe una API key Console a consumo (prezzi/crediti **non verificati**; id modello secondo la scheda interna: `claude-haiku-5-5`, `claude-sonnet-5-5`). Non configurato: eventuale passo successivo, dopo i modelli locali.

### 7. Stabilità Thunderbolt / eGPU (PROBLEMA APERTO — regola: niente carichi lunghi sulla 3090)

Il giornale di systemd **non** è persistente: la fonte utile è `/var/log/kern.log*`. Conteggio **di righe** per giorno (comando: `sudo sh -c 'zcat -f /var/log/kern.log*' | grep -E 'Xid|GSP RM|AER: (Correctable|Uncorrectable|Fatal)' | …`, eseguito dall'utente il 9 novembre; una caduta può produrre più righe):

| Giorno | AER corretti | AER **fatali** | `Xid` | `GSP RM heartbeat` timeout |
|--------|------:|------:|------:|------:|
| 13 ott | – | **2** | 3 | 3 |
| 15 ott | 715 | – | – | 188 |
| 16 ott | – | **1** | 2 | 12 |
| 17 ott | – | – | – | 6 |
| 18 ott | 10 | **1** | 3 | 89 |
| 19 ott | 4 | **1** | 3 | – |
| 21 ott | – | – | – | 22 |
| 30 ott | 3 | – | – | – |
| 31 ott | 1 | – | – | – |
| 3 nov | 1 | – | – | – |
| 4 nov | 1 | – | – | – |
| 9 nov | 5 | – | – | – |

- **Letture:** gli incidenti gravi (errore PCIe fatale sul ponte Thunderbolt, `Xid 79` «GPU has fallen off the bus», `Xid 154` riavvio richiesto) sono stati **più d'uno: 13, 16, 18, 19 ottobre** (l'handoff di ottobre ne descriveva uno solo, il 18 alle 19:03). Dopo il 21 ottobre non risultano più eventi gravi; restano solo errori PCIe **corretti** e solo dalla 3090 (`3b:00.0`), mai dalla 4060. Il banco di prova **non** era in esecuzione durante l'incidente del 18 (l'ultimo file è delle 13:15).
- **Causa: NON identificata.** Ipotesi non verificate: cavo/connettore/porta Thunderbolt, alimentazione o calore, gestione energetica Thunderbolt/PCIe, accensione continua della eGPU (~15 giorni a metà ottobre). Cosa è cambiato tra il 19 e il 30 ottobre: **l'utente non l'ha ancora detto** (domanda aperta). Il miglioramento **precede** la migrazione dei dischi del 6 novembre. Il documento sui dischi non riporta modifiche al collegamento.
- **Strumenti:** `bash document-ai/scripts/orchestra_diag.sh [--sudo-log]` riassume gli eventi hardware delle ultime 24 ore (sola lettura, segreti oscurati). **Regola vigente:** nessun benchmark lungo sulla 3090 finché non ci sono test brevi puliti: prima `orchestra_smoke_test.sh --load`, poi **una** corsa breve con `kern.log` sotto osservazione, **fermarsi al primo `Xid`/timeout GSP**.

### 8. Cronologia sintetica del lavoro (dettagli in `docs/HANDOFF_PROSSIMA_SESSIONE.md` §4 e §6)

- **Ottobre:** migrazione dual-GPU (ruoli per UUID, un Ollama per GPU, failover, ricreazione transazionale dei container), tag rc1–rc10, suite di test simulati, `orchestra_diag.sh`, banco di prova `orchestra_eval.py`, bug B1–B18, incidenti Thunderbolt (§7).
- **6–7 novembre:** migrazione dei dati sul nuovo disco `/mnt/data`; l'utente aggiorna `start_ai_stack.sh` (nuovo punto di mount e store dei modelli).
- **9 novembre (questa sessione):** ripresa e verifica delle credenziali/canali (accesso al repo con rami nuovi; nessuna cancellazione di rami); allineamento del repo alla versione locale dell'utente; **incidente B19** (disco di sistema pieno: i test copiavano l'intero albero di lavoro in `/tmp`, 185 GB in cartelle `tmp.*/home`; corretto e verificato, vedi §10); `patterns.jsonl` tolto dal versionamento (conteneva testi di richieste dell'utente in un repo pubblico: la storia resta pubblica); integrazione VS Code + Continue (autocomplete locale, DeepSeek con sintassi corretta, NORMAL tutto locale, prova Agent); questo README.

### 9. Decisioni di progetto e bug (indice; la motivazione completa è nel documento di handoff)

**Decisioni (D1–D23).** D1 le due GPU **cooperano** (main/aux) · D2 identificazione per **UUID** · D3 ruoli automatici con degrado · D4 un Ollama per GPU · D5 backend per modello con failover sul main · D6 `/vram` retro-compatibile · D7 selezione modelli adattiva (14B tutto in GPU se ≥ 11000 MB liberi) · D8 flash attention + KV `q8_0`, `NUM_PARALLEL=1`, `OLLAMA_CONTEXT_LENGTH=8192` · D9 32B solo con ≥ 20 GB, `num_ctx 12288` · D10 `image_loop` cooperativo (ComfyUI svuotato a fine generazione sulle GPU ≥ 16 GB) · D11 ComfyUI fissato alla GPU di ruolo, **mai `--normalvram`** · D12 ricreazione dei container transazionale (`<nome>.bak`) · D13 launcher robusto (lib condivisa, guardia `bash`, `orchestra.env`) · D14 strumenti `egpu_check`, `smoke_test`, `bench`, `power`, `sync` · D15 test con stub che impongono i vincoli reali · D16 **un agente forte sulla 3090 + agenti piccoli sulla 4060 + parallelismo solo per compiti parallelizzabili** · D17 modelli candidati (fonti di terzi) · D18 `orchestra_bootstrap` come **filtro** · D19 documentazione allineata al codice · D20 versioning/canale di scrittura · D21 esposizione di rete solo documentata · D22 energia opt-in e misurata · D23 «qwen 3.8 image» **non confermato**.

**Bug corretti (B1–B19).** B1 `/vram` sempre 2000 MB con 2 GPU · B2 il 32B non veniva mai scaricato · B3 `keep_alive` ignorato · B4 bootstrap non era un filtro · B5 `num_ctx` 32768 troppo grande · B6 manifest del workflow in ritardo · B7 **`docker run requires at least 1 argument`** (difetto latente del launcher originale) · B8 `orchestra_sync` poteva perdere file ignorati · B9 falsi allarmi di sintassi · B10 quoting/`wait` nel bench · B11 stub permissivi · B12 `sh` invece di `bash` · B13 contesto predefinito che spostava layer su CPU · B14 ComfyUI occupa 7 GB e il 27B scende all'80% · B15 Ollama troppo vecchio (HTTP 412) · B16 `--normalvram` rimosso da ComfyUI · B17 esempio nel prompt vision contaminava `/generate` (verificato su hardware su 2 prompt: campione piccolo) · **B18 aperto**: `test_image_loop` «evict aux» instabile (1 volta su 20–40) · B19 disco pieno per i test (§10).

### 10. Strumenti, comandi e suite di test

```bash
cd ~/ai-sessioni
bash document-ai/scripts/orchestra_sync.sh [ramo-o-tag]      # allineamento locale sicuro: backup, mai push/reset/cancellazioni
bash start_ai_stack.sh                                       # avvio (ferma i container all'uscita); variabili opzionali in orchestra.env
bash document-ai/scripts/orchestra_smoke_test.sh [--load] [--free-comfy]   # ruoli, una sola GPU per container, memoria sulla GPU giusta, 100% GPU
bash document-ai/scripts/orchestra_diag.sh [--no-smoke] [--sudo-log]       # report compatto di sola lettura (incl. eventi PCIe/Xid)
bash document-ai/scripts/orchestra_bench_models.sh --free-comfy --parallel "1 2 3" --tokens 600 --repeat 3 --telemetry MODELLO
python3 document-ai/scripts/orchestra_eval.py ...            # banco di prova deterministico (casi in document-ai/eval/)
document-ai/scripts/vscode/vsc-orchestra.sh [normal|full] [--dry-run]      # (copia in ~/.local/bin) VS Code + Continue
bash tests/run_all.sh                                        # suite simulata (richiede bash, python3, flask pydantic requests)
```

- **Suite di test:** `tests/` (`test_launcher.sh` S1–S20 + controllo dimensione copia, `test_manifold.py`, `test_image_loop.py`, `test_bootstrap.py`, `test_vram_*.py`, `test_power.sh`, `test_bench.sh`, `test_sync.sh`, `test_comfy_flags.sh`, `test_eval.py`, `test_diag.sh`, `test_smoke.sh`, `check_syntax.sh`). **Lezione B19:** i test devono funzionare e **ripulire su una cartella REALE** (con `ComfyUI/` e venv da ~9 GB), non solo nella sandbox: usano `tests/helpers/tmpclean.sh` (`mk_tmp`, pulizia garantita all'uscita) e `test_launcher.sh` copia solo i file del progetto (`git ls-files -co --exclude-standard`, max 2 MiB l'uno, guardia a 50 MiB totali). Dopo ogni esecuzione sul PC dell'utente controllare `/tmp` (`ls /tmp | grep -c '^tmp\.'` deve dare 0) e `df -h /`. `test_sync.sh` in HEAD detached dà «branch named testbranch already exists»: è fragilità dell'ambiente, passa da un ramo con nome.
- **Regole dei test:** ogni nuovo comportamento ha il suo test; lo stub deve **imporre i vincoli reali** di docker/Ollama (immagine obbligatoria, JSON valido, ecc.); dopo ogni bug reale scrivere il test che lo riproduce e verificare che **fallisca** col codice vecchio.

### 11. Cosa NON è verificato (elenco aggiornato)

- Prova **reale della modalità NORMAL** con la sola 4060 (launcher con 3090 scollegata): tempi di caricamento di `llama3.1:8b`, comportamento con un solo modello caricato; il test del 9 novembre è stato fatto con la 3090 presente.
- Agent di Continue: modifica di file esistenti, comandi nel terminale, affidabilità del 14B (FULL) e di un 8B (NORMAL) nelle chiamate agli strumenti.
- Il calo a 3 flussi del MoE 30B, il link PCIe **sotto carico**, throttling; `orchestra_power.sh` e `--telemetry` **mai eseguiti** su hardware; rilascio di ComfyUI a fine `/generate` reale; flash attention + KV `q8_0` con i modelli vision; failover aux→main spegnendo l'aux; degrado con eGPU scollegata e ripristino `.bak` (solo simulati).
- Qualità del MoE rispetto a un denso da 27B; scaling del 27B in parallelo (misura falsata da B14); consumi.
- Cifre di VRAM dei modelli ComfyUI, caratteristiche dei modelli LLM, studi sul multi-agente: **fonti di terzi**. Workflow ComfyUI per Qwen-Image: **non costruiti** (D23 non confermata).
- Cosa è cambiato il 19–30 ottobre sul collegamento Thunderbolt (§7).

### 12. Esito dei test eseguiti in questa sessione

`bash tests/run_all.sh` eseguito il 9 novembre 2026 nella sandbox dell'AI (**simulato**, nessuna GPU/Docker reali) sul contenuto di `main` `ddf69b1`: **TUTTI I TEST OK**, 456 controlli (`/vram` 9, daemon VRAM 2, manifold 41, image_loop 65, bootstrap 19, launcher S1-S20 108, flag ComfyUI 8, diag 39, eval 38, power 26, bench 44, sync 33, smoke 24) più il controllo di sintassi di 26 script bash e 24 file Python tracciati. **Attenzione:** in un clone *shallow* `test_sync.sh` si salta da solo ed esce verde (riga `(ok)` senza conteggio): serve la storia completa. **`/tmp` non resta del tutto pulito:** `tests/test_eval.py` (riga 116) lascia a ogni esecuzione una cartella `tmpXXXXXXXX` (≈ 24 KB) che il controllo `grep '^tmp\.'` non vede; correzione proposta, non ancora fatta. **Non eseguita sul PC dell'utente** (mancano `flask pydantic requests`).

### 13. Lavoro in sospeso e prossimi passi (in ordine di priorità)

**P0 — con l'utente, subito**
1. **Prova reale NORMAL:** con la 3090 scollegata (o `ORCHESTRA_*` per simulare 1 GPU) avviare `vsc-orchestra.sh normal` e misurare chat locale + tempo di caricamento; decidere se reintrodurre un autocomplete in NORMAL (misurare prima).
2. **Dopo l'unificazione (fatta il 9 novembre):** aggiornare le copie in `document-ai/system/` e pulire i chunk obsoleti nel RAG (l'indicizzatore **non** rimuove i chunk di file cancellati: snapshot Qdrant, `DELETE :6333/collections/orchestra`, cancellare `rag/.file_hash_cache.json`, riavviare).
3. **Pulizia locale (utente):** cancellare `document-ai/knowledge/HANDOFF_PROSSIMA_SESSIONE.md` (copia obsoleta non tracciata, indicizzata dal RAG); ripristinare `~/patterns.jsonl.bak` in `~/ai-sessioni/logs/patterns.jsonl`.

**P1 — miglioramenti del launcher (uno alla volta, ciascuno con test):** `HOST_IP` robusto (prendere il token dopo `src`, ripiego senza rotta); variabile `ORCHESTRA_DATA_MOUNT` (via `orchestra.env`) al posto di `/mnt/data` fisso; `sleep` di attesa tornato a 5; eventuali `ORCHESTRA_EXTRA_MAIN_MODELS`/`ORCHESTRA_EXTRA_AUX_MODELS`; **una sola fonte** (`orchestra.env`) per modelli e porte, condivisa da launcher e `vsc-orchestra.sh`; aggiornare gli stub dei test (`ip`, `docker exec curl`) quando si cambia. Valutare l'aggiunta a `vsc-orchestra.sh` di un controllo che i server `:11435`/`:11436` rispondano.

**P2 — stabilità eGPU (§7):** raccogliere dall'utente cosa è cambiato tra il 19 e il 30 ottobre; sequenza di prova graduale e **con interruzione al primo errore**; solo dopo, le misure P3.

**P3 — misure su hardware (nessuna richiede codice nuovo):** `orchestra_bench_models.sh --free-comfy --parallel "1 2 3" --tokens 600 --repeat 3 --telemetry qwen3:30b-a3b` (il calo è reale?); idem per `qwen3.8:27b` a 100% GPU; un `/generate` reale e `nvidia-smi` a fine lavoro (`comfy_free_on_finish`, B14); modelli vision con FA + KV `q8_0`; failover aux→main; `orchestra_power.sh bench`; **modelli candidati per la chat FULL** (`qwen3:30b-a3b`, `qwen3.8:27b`, `qwen2.5-coder:32b` contro il 14B attuale) confrontati con `orchestra_eval.py` e con compiti reali valutati dall'utente; eventuale modello per il codice sotto i 5 GB in NORMAL (es. `qwen2.5-coder:7b`, **non installato**).

**P4 — sviluppo (decisione dell'utente, ancora non presa):** (A) modalità **«team»** (`/team`, pianificatore + fino a 3 lavoratori sul 14B + revisore, «scrive un solo agente», costo ≈ 15× i token) *consigliata come primo passo*; (B) pipeline immagini a due stadi (bozze sulla 4060, render finale sulla 3090; richiede la conferma del modello, D23); **profilo rigore** (`strict_mode`: modelli grandi per i compiti tecnici, temperatura bassa, regola «non lo so») dopo i numeri del banco di prova; riallineamento dei punti tecnici che l'utente elencherà.

**P5 — igiene e sicurezza:** indurre `ai-commit.yml` (passare `client_payload` via `env:`, `branch` obbligatorio; richiede permessi dedicati e consenso); esposizione di rete (RAG/ComfyUI su `0.0.0.0` + `ufw`: decidere con l'utente se legarli al bridge Docker, nessuna modifica senza consenso); pulizia Docker (build cache ≈ 8,7 GB, tag `open-webui` inutilizzati ≈ 7 GB), ispezione di `/home` (73 GB), rimozione dei modelli inutilizzati (§5), chiarire `llamacpp:c97eb11d…` e il duplicato `qwen3.5:9b`; isolare B18; eliminare i branch obsoleti `dual-gpu-step1`/`step2a` (decisione dell'utente); `pattern_logger.py` duplicato in 3 posti.

**P6 — chiusura del progetto (decisioni dell'utente):** ruotare la chiave DeepSeek; revocare/eliminare i token GitHub e il segreto `ORCHESTRA_PAT` non più necessari.

### 14. Insidie e lezioni apprese (le più importanti)

- **`ensure_container` riusa i container esistenti:** cambiare `--gpus`, `-e` o immagine non ha effetto finché non si ricrea (usare gli helper `recreate_*`, transazionali). Le variabili d'ambiente si fissano alla creazione. Non rimuovere `--gpus all` e il mount di `nvidia-smi` dal container Pipelines (serve al daemon VRAM).
- **Il RAG indicizza `document-ai/` ricorsivamente** (anche `.py`, `.sh`, `.json`, `.md`) e non rimuove i chunk dei file cancellati: i documenti obsoleti restano come contesto. Per questo handoff vecchi stanno in `docs/archive/` e non in `document-ai/knowledge/`.
- **`num_ctx` diverso tra richieste forza il ricaricamento** del modello: nelle misure usare quello del manifold (8192). Il link PCIe a riposo (Gen1) **non** è quello sotto carico.
- **Con GPU singola da 8 GB** il pre-caricamento parallelo non deve rubare VRAM a SDXL; ComfyUI occupa ≈ 7 GB con SDXL caricato.
- **Processo:** non scrivere cifre a memoria nei documenti (verificare da `git`/dai log); non usare `pkill -f <nome-script>` (uccide la shell dello strumento); heredoc annidati con delimitatori diversi; costrutti bash non vanno in `sh`; `echo "-e"` stampa una riga vuota; il fetch web può restituire pagine in cache (in dubbio, clonare con git); un test va provato su una **cartella reale** (B19).
- **Mai:** scrivere su `main`; unire la PR; Squash; `push --force`; mettere segreti in file, commit o output; modificare a mano i file generati; proporre modelli online come predefiniti (§1); toccare la configurazione di rete senza consenso.

### 15. Checklist per i primi minuti di una nuova sessione

1. Leggere §1–§2 e §7 di questa sezione; `git fetch --all --tags` e verificare il tip di `main` e le PR (l'utente può aver unito o pushato altro). Il clone della sessione va fatto con la storia completa, o `test_sync.sh` viene saltato (§12).
2. Eseguire `bash tests/run_all.sh` (deve essere verde) **prima** di toccare qualcosa; controllare che `/tmp` resti pulito.
3. **Chiedere all'utente:** cosa è cambiato in locale dall'ultimo handoff? Cosa è cambiato il 19–30 ottobre sul Thunderbolt? Ha eseguito le misure P3? Quale sviluppo sceglie (team / immagini)? Conferma D23?
4. Lavorare per passi piccoli e verificati; dichiarare sempre cosa è misurato, simulato o ipotizzato; seguire le regole di pubblicazione di §2.
5. A fine sessione **aggiornare questa sezione** (stato, tabella eventi §7, elenco §11–§13) e `docs/HANDOFF_PROSSIMA_SESSIONE.md`.

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
- `qwen2.5-coder:32b` (only pulled when the main GPU has at least 20 GB)
- `llava:7b`
- `moondream:v2`
- plus an optional heavy model via `ORCHESTRA_HEAVY_MODEL`

Models of the main Ollama are stored on the data disk (`/mnt/data/ollama-models/ollama-session-ext`, override with `ORCHESTRA_MODELS_SRC`; the previous Docker volume `ollama-session` is the rollback), so they do not need to be downloaded on every startup. The aux Ollama keeps its light models in the Docker volume `ollama-aux-session`.

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
bash tests/run_all.sh                                       # 455 simulated checks (no GPU, Docker or network touched)
bash document-ai/scripts/orchestra_bench_models.sh --parallel "1 2 3" MODEL   # tokens/s per stream and aggregate with N concurrent requests, VRAM, 100% GPU (add --free-comfy if ComfyUI holds VRAM, --repeat 3 for medians, --telemetry for GPU/PCIe/throttle data)
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
- It is pinned to the GPU of `ORCHESTRA_COMFY_ROLE` with `CUDA_VISIBLE_DEVICES=<UUID>`. On the 3090 no memory flag is passed (VAE stays on GPU); on small GPUs `--cpu-vae` is used (plus `--lowvram` in `start_comfyui.sh`). `--normalvram` is no longer passed: it was removed from ComfyUI (it was the default).
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
| `main` | **the only branch since 9 Nov 2026** (tag `punto-zero-2026-11-09`): dual-GPU work, evaluation harness, hardware diagnostics, launcher aligned to the local version, VS Code/Continue integration, test-disk fix (B19), AI handoff in this README | active |

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

This section is meant to be read by an AI agent that must operate on the repository without human context. **The up-to-date handoff (state, rules, open problems, next steps) is the section [HANDOFF PER AGENTI AI](#-handoff-per-agenti-ai--leggere-per-primo) at the top of this file**; the table below is older and some entries (e.g. the dispatch script and token file) describe the user's local setup and must be verified.

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
