# HANDOFF PER LA PROSSIMA SESSIONE — Orchestra AI (dual-GPU)

**Data del documento:** 10 ottobre 2026 (orologio di sistema) · **Repository:** `claudio3g/orchestra-ai` (pubblico)
**Scopo:** permettere a una nuova sessione (AI o persona) di riprendere il lavoro senza rileggere la conversazione:
stato esatto, ogni scelta con la sua motivazione, ogni bug trovato, cosa e verificato e cosa NO, cosa resta da fare.

> **Convenzioni di questo documento.** «**Verificato**» = misurato sull hardware reale dall utente o dimostrato da un test
> automatico. «**Simulato**» = coperto solo dai test con stub (nessuna GPU, Docker o rete reali). «**Ipotesi**» = ragionamento
> non confermato. «**Fonte di terzi**» = dichiarazione trovata in articoli o schede di modelli e NON verificata da noi.
> Questo documento non contiene segreti: nessun token, nessuna chiave. Non aggiungerne mai.

## 0. AGGIORNAMENTO DI STATO — 15 ottobre 2026 (prevale sulle sezioni 3.3, 3.5 e P0 dove sono in contrasto)

**Stato attuale: tag `dual-gpu-rc9` su `dual-gpu-final`.** Le sezioni seguenti descrivono lo stato del 10 ottobre e sono in parte superate; fa fede questo blocco.

- **rc7 esiste** (`34be42f`). **rc8** (`449b7c0`) corregge l avvio di ComfyUI (B16: `--normalvram` rimosso da ComfyUI). **rc9** corregge `/generate` che perdeva il soggetto dell utente (B17: esempio concreto nel prompt di analisi vision).
- **Canale `ai-dispatch` verificato:** `ORCHESTRA_PAT` valido (HTTP 204, job validate, sandbox-test e commit-push in `success`). Il punto P0 (a) e chiuso; il push diretto resta alternativa.
- **Validato sull hardware (utente, 15 ottobre):** ComfyUI parte senza errori di argomenti e smoke test 18/18; `/generate` su 2 prompt con immagini corrette e metadati PNG coerenti con i log. Campione piccolo: dimostra che il guasto specifico e sparito, non la robustezza generale.
- **Suite:** 400 controlli simulati, verde. Il controllo `evict aux` di `test_image_loop` e instabile (B18).
- **Aperti, non toccati:**
  - B18 (test instabile, da isolare).
  - Punteggi dell analisi vision rumorosi (stesso prompt: 1/10 e 7/10) e riferiti alla bozza, non all immagine finale quando il seed e nuovo.
  - Ipotesi non verificata: `refine_prompt` (qwen3.5:9b) risponde vuoto e il codice ricade sul `next_prompt` dell analisi (raffinamenti identici in iterazioni diverse).
  - Prefisso inutile «An SDXL-generated image of» nei prompt affinati.
  - **Profilo rigore** (obiettivo: lavori tecnici che non devono fallire): instradare i compiti tecnici verso modelli piu grandi, temperatura bassa e seed fisso, regola «non lo so» quando manca un dato, nessun contesto di progetto sulle richieste generiche. Motivo: tre richieste tecniche reali hanno avuto risposte inventate dal coordinator (llama3.2:3b, temperatura 0,7): uno script rsync che copia `/` con `--delete`, la diagnosi di un log mai fornito, una spiegazione inesistente sui metadati ComfyUI. La temperatura da sola non basta: a 0 la risposta sbagliata resterebbe sbagliata in modo ripetibile.
  - I punti tecnici da riallineare che l utente elenchera.
- **Ripresa:** l utente elenca i punti da riallineare; poi profilo rigore e valutazione su esempi reali.
- **Strumenti e regole di lavoro (16 ottobre):** `document-ai/scripts/orchestra_diag.sh` produce un report di stato compatto, di sola lettura, con segreti oscurati: di norma si incolla solo il RIEPILOGO in testa (opzioni `--no-smoke`, `--fetch`). Regola: l utente ha un account Claude gratuito, il consumo di token va contenuto (risposte brevi, file compatti, niente riletture inutili). Punti dell utente: (1) GPU e monitor, chiuso: il desktop sulla 3090 occupa 68 MiB, nessuna modifica; (4) VS Code: Claude Code richiede piano a pagamento o account Console; alternative locali (Cline, Aider con Ollama) da valutare solo dopo aver misurato Orchestra (profilo rigore); (3) temperatura (esiste gia il profilo di potenza) e (2) modelli: da analizzare.
- **Rollback:** `rc8` (`449b7c0`). Attenzione: `rc7` NON avvia le versioni recenti di ComfyUI.

## 0. Riassunto in dieci righe

1. Obiettivo: far lavorare **insieme** una RTX 3090 (eGPU AOOSTAR AG02 su Thunderbolt 4) e una RTX 4060 Laptop interna in un
   sistema multi-agente locale (Ollama + Open WebUI + Pipelines + RAG + Qdrant + ComfyUI).
2. Ruoli: **main = 3090** (modelli pesanti, SDXL), **aux = 4060** (coordinator, vision). Una istanza Ollama **per GPU**.
3. Tutto il lavoro e sul branch **`dual-gpu-final`** (PR **#2** aperta, **non unita**); `main` e ancora a **`f135a06`** (invariato).
4. Rollback: tag annotati `pre-dual-gpu` (= main prima di tutto), `dual-gpu-rc1` ... `dual-gpu-rc6`. Procedure in `docs/VERSIONING.md`.
5. Validazione su hardware: smoke test **24/24**; 14B al **100% GPU** (73 tok/s, 165 tok/s totali con 3 richieste).
6. Suite di test simulata: **400 controlli**, tutti verdi (`bash tests/run_all.sh`).
7. **Due commit locali NON ancora su GitHub** al momento di questo documento (vedi sezione 3.3): bench con telemetria e docs.
8. **Il canale `ai-dispatch` e rotto** finche l utente non aggiorna il segreto del repository `ORCHESTRA_PAT` (sezione 3.5).
9. Decisione architetturale: **un agente forte sulla 3090 + strato di agenti piccoli sempre attivo sulla 4060 + parallelismo solo
   per compiti parallelizzabili** (sezione 5, D16). Misure recenti la sostengono per il 14B; il 30B MoE e da rimisurare.
10. Prossimo passo da decidere con l utente: **modalita «team»** (pianificatore + lavoratori paralleli sul 14B + revisore) oppure
    **pipeline immagini a due stadi con Qwen-Image** (sezione 15).

## 1. L utente, le sue preferenze e il metodo di lavoro

- **Utente:** Claudio (`claudio3g`), parla **italiano**; macchina `nix-i9` (hostname), utente di sistema `claudio`, progetto in
  `~/ai-sessioni`. Rispondere in italiano, tono chiaro e diretto.
- **Preferenze dichiarate (userPreferences):** ruolo «ingegnere senior AI» su sistemi self-hosted, RAG, Ollama, pipeline
  OpenWebUI, ComfyUI/SDXL. Metodo: **chiaro e semplice**; **conservativo** (non modificare cio che funziona senza motivo tecnico);
  **incrementale** (un cambiamento alla volta, testato); **documentato** (modifiche spiegate nei commenti); **verificato**
  (smoke test prima di dichiarare successo). Nota: il testo delle preferenze cita il vincolo storico «≤ 8 GB di VRAM»: e **superato**
  dall hardware attuale (24 GB + 8 GB), ma il metodo resta valido.
- **Come ha lavorato l utente in questa sessione:** corregge quando l AI sbaglia (es. «le GPU non sono alternative»), porta dati
  reali dall hardware, decide lui i merge. Ha chiesto esplicitamente di: usare il versioning per poter fare rollback, far fare all AI
  i commit direttamente, tenere il token fino a fine sistemazione e poi eliminarlo, allineare la cartella locale prima di procedere.
- **Regola di onesta che l utente ha apprezzato implicitamente (e da mantenere):** distinguere dato misurato da ipotesi; dichiarare
  quando un risultato e solo simulato; ammettere subito un errore proprio (in questa sessione l AI ha introdotto e poi corretto diversi
  difetti: vedi sezione 6) e riportare quando una spiegazione proposta dall utente NON e sostenuta dai dati (sezione 7.4).

## 2. Hardware e ambiente (fatti misurati dall utente)

| Voce | Valore |
|------|--------|
| OS | Ubuntu 24.04 (dai documenti del progetto) |
| CPU / RAM | Intel i9-13900HX (8P+16E, 32 thread), 32 GB |
| GPU **main** | NVIDIA RTX 3090 24 GB (24576 MiB), eGPU **AOOSTAR AG02** su **Thunderbolt 4**, link PCIe **x4** |
| GPU **aux** | NVIDIA RTX 4060 Laptop 8 GB (8188 MiB), interna; a riposo circa 1,1 GB sono usati dal desktop |
| Indici `nvidia-smi` | 4060 = indice 0, 3090 = indice 1 (ma CUDA ordina «piu veloce prima»: **usare sempre gli UUID**) |
| Driver | NVIDIA 590.48.01, CUDA 13.1 (dal report hardware del 2026-03-31) |
| Docker | runtime `nvidia` + dispositivi CDI (`nvidia.com/gpu` per indice, UUID, `all`) |
| Ollama (container) | versione **0.40.0** dopo l aggiornamento dell immagine (era troppo vecchia per `qwen3.8:27b`: errore 412) |
| Misure a riposo (egpu_check) | 3090: 19,9-29,5 W, 46-50 C, link Gen1 x4 (**a riposo il link scende di frequenza**); 4060: 6,4-6,5 W, 55-57 C, Gen1 x8 |
| Rete | Open WebUI su IP LAN fisso `192.168.1.51:3001` (fisso nel launcher); un reverse proxy su `192.168.1.101:81` e controllato dal launcher; dominio pubblico configurato nel launcher |

Le identita (UUID) delle GPU non sono riportate qui: si ottengono con `bash document-ai/scripts/egpu_check.sh`.

## 3. Stato esatto del repository e di GitHub

### 3.1 Branch, PR, tag
- **`main`** = `f135a06` («feat(ai): Step 4 — Modelfile-orchestra + pipeline orchestra_bootstrap con mount /app/ai»). **Non toccato** da questa sessione.
- **`dual-gpu-final`** (remoto) = `edf1a36` («docs: seconda serie di misure...»): **29 commit** sopra `pre-dual-gpu`, di cui 11 firmati
  «Claude» (pubblicati con push diretto) e 18 dal bot `github-actions[bot]` (pubblicati via `ai-dispatch`).
- **PR #2** (`dual-gpu-final` → `main`): **aperta, non unita**. https://github.com/claudio3g/orchestra-ai/pull/2 . Con commenti per ogni rc.
  Merge consigliato: «Create a merge commit» o «Rebase and merge», **mai Squash** (si perderebbero i commit separati, voluti per il rollback).
- Esistono ancora i branch remoti storici `dual-gpu-step1` e `dual-gpu-step2a` (pubblicati in fasi precedenti; il contenuto di step1 e
  gia in `main` via rebase con hash diversi `2710c49`, `cca1204`, `316af36`). `dual-gpu-step2a` e **obsoleto**: contiene un README
  riscritto poi superato. Non unirlo.

| Tag (annotato) | Commit | Significato |
|----------------|--------|-------------|
| `pre-dual-gpu` | `f135a06` | `main` prima della migrazione: launcher v3.8, GPU singola |
| `dual-gpu-rc1` | `9b771b1` | prima RC (PR 2): 226 controlli simulati |
| `dual-gpu-rc2` | `43fec01` | + parallelismo/modello pesante configurabili, bench, decisione architetturale, versioning |
| `dual-gpu-rc3` | `440336b` | + `orchestra_sync.sh`, guardia `sh`→bash, controllo sintassi solo tracciati (287 controlli) |
| `dual-gpu-rc4` | `f135093` | + fix «docker run senza immagine», ricreazione transazionale, sync con file ignorati |
| `dual-gpu-rc5` | `ababbb6` | + `ORCHESTRA_PULL_IMAGES`, `OLLAMA_CONTEXT_LENGTH`, bench/smoke con `num_ctx` (332 controlli) |
| `dual-gpu-rc6` | `edf1a36` | + ComfyUI svuotato a fine generazione, `--free-comfy` (346 controlli); **validata su hardware** |

Il tag **`dual-gpu-rc7` non esiste ancora**: va creato dopo la pubblicazione dei due commit pendenti (3.3).

### 3.2 Cosa e cambiato rispetto a `pre-dual-gpu`
70 file, +5671/−552 righe (`git diff --shortstat pre-dual-gpu origin/dual-gpu-final`, esclusi i 2 commit pendenti). File nuovi principali: `document-ai/scripts/{orchestra_gpu_env,orchestra_power,orchestra_smoke_test,
orchestra_bench_models,orchestra_sync,egpu_check}.sh`, l intera cartella `tests/`, `docs/VERSIONING.md`, `docs/archive/`,
`document-ai/knowledge/{ORCHESTRA_HANDOFF_v9,ARCHITETTURA_AGENTI_E_MODELLI}.md`. File riscritti: `start_ai_stack.sh` (v3.9),
`orchestra_manifold.py` (v3.9.0), `image_loop.py` (v2.8.0), `orchestra_bootstrap.py` (v1.1.0), `embedding_utils.py` (v2.0.3),
`rag_service.py` (v1.5.1), `README.md`, `README.it.md`, `Modelfile-orchestra`.

### 3.3 Commit che erano pendenti e come sono stati pubblicati
Quando il dispatch e fallito (3.5) esistevano due commit locali sopra `edf1a36`: **(1)** `feat(bench): ripetizioni con mediana, avviso richieste brevi e telemetria GPU e PCIe e token per joule`
(`orchestra_bench_models.sh`: `--repeat`, `--median`, `--telemetry`, avviso richieste brevi, trap Ctrl-C; stub `nvidia-smi`/`curl`; `test_bench.sh`) e **(2)** `docs: terza serie di misure con analisi del calo del MoE a 3 flussi, conteggio test` (360 controlli).
Sono stati pubblicati con **push diretto** del branch insieme a questo documento, a un piccolo aggiustamento dell etichetta di `run_all.sh` e alla rigenerazione del contesto AI (`chore(ai)`); il tag **`dual-gpu-rc7`** punta al tip risultante.
**Verificare sempre con `git log origin/dual-gpu-final -8` e `git tag`**: se il push non fosse riuscito, questi commit non sono su GitHub e vanno rigenerati dalle sezioni 11 (bench) e 7 (misure).

### 3.4 Bot, workflow e file generati
- `.github/workflows/ai-commit.yml` («AI Commit & Push»): evento `repository_dispatch` tipo `ai-update` con `client_payload` = {`patch` (base64 di un `diff --git`),
  `branch` (**se manca il default e `main`!**), `message`}. Job: `validate` → `sandbox-test` (py_compile, `bash -n`, YAML, JSON, shellcheck non bloccante) → `commit-push`
  (committa come `github-actions[bot]` **e rigenera** `AI_MANIFEST.md`, `AI_CTX_*.md`, e dopo le nostre modifiche anche la sezione «File tracciati» di `AI_BOOTSTRAP.md`).
- I file `AI_MANIFEST.md`, `AI_CTX_*.md` e la tabella di `AI_BOOTSTRAP.md` sono **generati**: non modificarli a mano.
- `generate_ai_context.sh` e stato corretto in questa sessione (vedi B6).

### 3.5 Token e segreto del workflow (ATTENZIONE)
- Il workflow esegue il checkout con `secrets.ORCHESTRA_PAT`. Nell ultimo tentativo il run ha fallito **al passo «Checkout repository»** (job `validate`),
  quindi il segreto contiene con ogni probabilita il token precedente, scaduto. **L utente deve aggiornare il segreto** in
  *GitHub → Settings → Secrets and variables → Actions → `ORCHESTRA_PAT`* con un token valido. L AI **non puo** farlo (il token fine-grained fornito non ha accesso ai segreti: HTTP 403, voluto per il principio del minimo privilegio).
- L utente ha fornito all AI token fine-grained limitati al solo repository (Contents e Pull requests in lettura/scrittura). L ultimo funzionava per push e API, ma **non** per il checkout del workflow.
  **Mai** scrivere un token in file, commit o messaggi; usarlo solo come variabile d ambiente temporanea (askpass) e rimuoverlo dalla sandbox. L utente ha deciso di **tenere il token fino a fine sistemazione del progetto e poi eliminarlo**.
- Fino al ripristino del segreto, **pubblicare con push diretto** del branch (consentito dal token) e rigenerare localmente il contesto con `generate_ai_context.sh` prima di committare.

## 4. Cronologia della sessione (in ordine, con l esito di ogni fase)

| Fase | Cosa e successo | Esito |
|------|-----------------|-------|
| A. Prima analisi | Richiesta: analizzare `claudio3g/orchestra-ai`, integrarlo con i dati di progetto e creare una versione che usi la 3090 via eGPU. Il fetch web mostro il repository **vuoto** (pagina in cache: il commit era di pochi minuti prima); l utente invio uno screenshot; il **clone con git** funziono. Letti: handoff v8, report hardware, compose, valves, pipeline | repository compreso |
| B. Bug critico e primo passo | Trovato che con **2 GPU** `nvidia-smi --query-gpu=memory.free` stampa 2 righe: `int(out.strip())` andava in ValueError e `/vram` rispondeva sempre **2000 MB** → il manifold sceglieva sempre `llama3.2:3b`. Primo tentativo con una sola `ORCHESTRA_GPU_ID` | **L utente corresse**: le GPU non sono alternative ma cooperano → ruoli main/aux (D1). Passo 1 validato su hardware, unito dall utente con rebase |
| C. File dell utente | L utente carico `start_ai_stack.sh`, `start_comfyui.sh`, `README.md`; passo 2a (Ollama fissato alla 3090) e prima riscrittura del README (branch `dual-gpu-step2a`, poi superata) | il README di `main` era diverso: vedi D19 |
| D. Rilettura di `main` | Scoperto: workflow `ai-commit.yml`, `AI_BOOTSTRAP.md`, `AI_MANIFEST.md`, `AI_CTX_*`, `orchestra_bootstrap.py`, `Modelfile-orchestra`, `qwen2.5-coder:32b` nel launcher. Trovati difetti nelle novita dell utente (B4, B5) e contraddizioni README/codice | allineamento concordato |
| E. Ottimizzazione completa | Su richiesta («ottimizza codice, VRAM, cooperazione, poi consumi»): manifold v3.9.0, image_loop v2.8.0, launcher v3.9, lib condivisa, power, smoke, tests, docs, handoff v9 → branch `dual-gpu-final`, PR #2, tag rc1 | pubblicato (push diretto) |
| F. Decisioni dell utente | Conferma del canale `ai-dispatch` + **versioning per rollback**; «correggi» bootstrap/Modelfile; domanda su modelli ComfyUI per la 3090; **domanda architetturale** (swarm sulla 3090 vs 32B + un agente sulla 4060) | ricerca web, decisione D16/D17, tag, dispatch, rc2 |
| G. «Launcher corrotto?» | L utente segnalo `start_ai_stack.sh` sospetto di corruzione. Verificato: il file sul remoto era integro; riprodotti i due casi plausibili (merge in conflitto con marcatori `<<<<<<<`; lancio con `sh`/dash) | `orchestra_sync.sh`, guardia `sh`, rc3 |
| H. Primo avvio vero | `docker run requires at least 1 argument`: **difetto latente del launcher originale** (B7), attivato dalla ricreazione | rc4 |
| I. Validazione | smoke test 23/24; qwen3.8 → HTTP 412 (Ollama vecchio); 14B al 15% su CPU per contesto predefinito (B13) | rc5 |
| J. Seconda serie | smoke 24/24, 14B 100% GPU; 27B all 80% GPU per 7 GB di ComfyUI (B14) | rc6 |
| K. Terza serie | Dati del 30B MoE portati dall utente; spiegazione «Thunderbolt» **non sostenuta** (7.4); bench esteso con ripetizioni e telemetria; **token scaduto**, poi rigenerato; dispatch fallito al checkout (3.5) | commit locali pendenti (3.3) |

## 5. Decisioni di progetto, con motivazioni

**D1 — Le due GPU cooperano con ruoli `main` (3090) e `aux` (4060).** *Perche:* l obiettivo e un sistema multi-agente in cui entrambe lavorano; il primo
schema (una sola GPU di riferimento) trattava la 4060 come alternativa ed era sbagliato (correzione dell utente). *Dove:* `rag_service.py` (`_assign_roles`),
`orchestra_gpu_env.sh` (`detect_gpu_roles`).

**D2 — Le GPU si identificano per UUID, mai per indice.** *Perche:* `nvidia-smi` elenca 4060=0 e 3090=1, ma CUDA ordina «piu veloce prima» (indici invertiti nei processi CUDA);
inoltre gli indici cambiano se la eGPU viene ricollegata. ComfyUI e fissato con `CUDA_VISIBLE_DEVICES=<UUID>` + `CUDA_DEVICE_ORDER=PCI_BUS_ID`; Docker con `--gpus device=<UUID>`.

**D3 — Ruoli rilevati automaticamente (main = GPU con piu VRAM, aux = la successiva), con override da variabili e degrado.** *Perche:* funziona senza configurazione; se la eGPU e
scollegata la 4060 diventa `main` e il sistema si comporta come la v8 a 8 GB (nessun Ollama aux, soglie da 8 GB). Un UUID non piu presente viene scartato con avviso e ricalcolato.

**D4 — Una istanza Ollama per GPU (`ai-ollama-session` main :11435, `ai-ollama-aux-session` aux :11436, volumi separati `ollama-session` / `ollama-aux-session`).**
*Perche:* una sola istanza su due GPU spezzerebbe i layer sul link Thunderbolt (banda ≈ PCIe x4); istanze separate danno parallelismo vero e prevedibile. Il container **Pipelines**
mantiene `--gpus all` e il mount di `nvidia-smi` (vincolo della v8: serve al fallback L2 del daemon VRAM).

**D5 — Backend per modello: `aux_models` = `llama3.2:3b, moondream:v2, llava:7b` vanno sull aux, il resto sul main; il main conserva TUTTI i modelli e fa da failover.**
*Perche:* se la 4060 o il suo Ollama cadono, le richieste ripartono sul main senza errori (health check in cache 20 s; retry solo se la connessione fallisce **prima** che la risposta inizi).
Il launcher scarica sull aux solo i modelli leggeri (`AUX_MODELS`, deve coincidere col valve `aux_models`).

**D6 — `/vram` retro-compatibile.** I campi storici (`vram_free_mb`, `source`) si riferiscono a `main`; nuovo array `gpus[]` con ruolo; `/vram?gpu=aux|<uuid>` per debug; il daemon di
`embedding_utils` espone `get_gpu_free_mb(role)` e `get_gpu_snapshot()`; funziona anche con un `rag_service` vecchio (snapshot vuoto). *Perche:* il manifold esistente continua a funzionare senza modifiche.

**D7 — Selezione modelli.** `qwen2.5-coder:14b` **tutto in GPU** se la VRAM libera del main ≥ `vram_quality_full_mb` (11000); sotto soglia resta `num_gpu=20` (comportamento da 8 GB). La vision usa
la VRAM **dell aux** se gira li. Keep-alive per ruolo: aux ≥ 1800 s; main ≥ 900 s se la VRAM e abbondante. *Perche:* con 24 GB l offload parziale su CPU era uno spreco; ricaricare modelli sul link TB4 e lento.

**D8 — Impostazioni Ollama.** `OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0` (dimezzano la cache del contesto; disattivabili con `ORCHESTRA_FLASH_ATTENTION=0`);
`OLLAMA_MAX_LOADED_MODELS=2` sul main se ha ≥ 20 GB (altrimenti 1; aux sempre 2); `OLLAMA_NUM_PARALLEL` default **1** (configurabile: l aumento moltiplica la KV cache, va misurato);
`OLLAMA_CONTEXT_LENGTH=8192` (= `context_length` del manifold). *Perche del contesto:* le versioni recenti scelgono un contesto predefinito dipendente dalla VRAM; con 3 richieste parallele la KV
si alloca per contesto×3 e sposta layer su CPU (B13). **Da verificare su hardware:** flash attention con KV q8_0 sui modelli vision.

**D9 — Modello pesante.** `qwen2.5-coder:32b` (scelta dell utente, resta) scaricato solo se il main ha ≥ 20000 MiB. Nuova variabile `ORCHESTRA_HEAVY_MODEL` (es. `qwen3.8:27b`): scaricata con le stesse regole, **download non fatale**.
`Modelfile-orchestra`: `num_ctx` da 32768 a **12288**. *Perche:* con 64 layer × 8 teste KV × 128 dim, la KV f16 costa ≈ 256 KB/token → 8,6 GB a 32768 token (+ 20 GB di pesi = 28,6 GB, non entra nei 24 GB); a 12288 e 3,2 GB (1,6 GB con q8_0). Calcolo verificato aritmeticamente; i parametri del modello sono una stima.

**D10 — `image_loop` cooperativo (v2.8.0).** Backend per ruolo con failover; la VRAM per scegliere vision/refine si legge dalla GPU dove il modello gira; ComfyUI viene svuotato (`/free`) **solo** se condivide la GPU col modello e la VRAM non basta (SDXL resta
caricato tra un draft e l altro); pre-caricamento di vision (aux) e refine (main) in parallelo ai draft **solo se non ruba VRAM a SDXL** (mai con GPU singola da 8 GB); se un LLM grande occupa la GPU di ComfyUI viene scaricato via `/api/ps` prima di iniziare (`evict_llms_for_comfy`);
`keep_alive` passato come parametro di primo livello (B3); a fine loop i modelli tenuti vengono scaricati; **a fine generazione ComfyUI viene svuotato sulle GPU ≥ 16 GB** (`comfy_free_on_finish`, rc6, per B14). Con GPU piccole il comportamento resta storico.

**D11 — ComfyUI.** Fissato alla GPU di `ORCHESTRA_COMFY_ROLE` (default `main`). Sulle GPU ≥ 16 GB: nessun flag di memoria (VAE su GPU); su GPU piccole `--cpu-vae` (e `--lowvram` in `start_comfyui.sh`). **`--normalvram` non si passa piu:** e stato rimosso da ComfyUI (verificato: `grep normalvram ComfyUI/comfy/cli_args.py` senza risultati) e con quel flag ComfyUI non parte (B16). Il launcher lo avvia **in primo piano al passo 8**
(`start_comfyui.sh` e un avvio alternativo); anche `rag_service` gira **nel venv di ComfyUI** (dipendenza nascosta documentata nel README).

**D12 — Ricreazione dei container: transazionale, con immagine scelta dal sistema.** *Perche:* le variabili d ambiente e `--gpus` si fissano alla creazione; `ensure_container` **riusa** i container esistenti (`docker start`), quindi cambiare gli argomenti non basta.
Soluzione: il container da sostituire diventa `<nome>.bak` (non viene cancellato); se il nuovo parte il backup viene eliminato, se fallisce viene **ripristinato da solo**. L immagine e: quella del container esistente → un immagine locale dello stesso repository → il default standard
(`ollama/ollama:latest`, `qdrant/qdrant:latest`, `ghcr.io/open-webui/pipelines:main`); override con `ORCHESTRA_OLLAMA_IMAGE`/`_QDRANT_IMAGE`/`_PIPELINES_IMAGE`. Aggiornamento dell immagine Ollama: `ORCHESTRA_PULL_IMAGES=1` (pull + ricreazione dei container
con etichetta `orchestra.image_id` diversa; il confronto usa l etichetta e non l ID del container per evitare falsi positivi con certi archivi di immagini); `ORCHESTRA_RECREATE_OLLAMA=1` forza una volta (necessario per container creati prima dell etichetta). I **volumi dei modelli non vengono mai toccati**.

**D13 — Launcher robusto.** Logica GPU nella libreria `document-ai/scripts/orchestra_gpu_env.sh` (condivisa da launcher, `start_comfyui.sh`, smoke test, power, bench, sync); se la libreria manca il launcher ricade sul comportamento storico (`--gpus all`, nessun aux).
`orchestra.env` (nella root, ignorato da git) per i valori locali. Guardia `[ -n "$BASH_VERSION" ] || exec bash "$0" "$@"` (con `sh`/dash i costrutti bash davano `Syntax error: "(" unexpected`). Modelli obbligatori che falliscono continuano a fermare lo stack (come prima); i pesanti no.

**D14 — Strumenti (sezione 11).** Ognuno nato da un bisogno concreto: `egpu_check.sh` (UUID e link), `orchestra_smoke_test.sh` (verifica isolamento GPU reale: «la memoria cresce sulla GPU giusta e NON sull altra»), `orchestra_bench_models.sh` (decidere con dati tra un agente forte e agenti paralleli),
`orchestra_power.sh` (consumi), `orchestra_sync.sh` (aggiornamento locale sicuro).

**D15 — Test simulati con stub (nessuna GPU/Docker reali).** *Perche:* l AI non ha accesso all hardware; i test servono a non regredire e a riprodurre i guasti. **Lezione costosa:** uno stub permissivo nasconde i bug reali (B7, B10). Gli stub ora riproducono i vincoli di docker (immagine obbligatoria, JSON valido, rename, label, `ollama --version`, `ollama ps`).

**D16 — Decisione architetturale (domanda dell utente: swarm sulla 3090 oppure 32B sulla 3090 + un solo agente sulla 4060).** **Un agente forte sulla 3090 + strato di agenti piccoli sempre attivo sulla 4060 + parallelismo solo per compiti parallelizzabili.** *Motivi:*
(a) evidenze **di terzi** (riassunti secondari di Google Research, Anthropic, MAST/Cemri 2025; non riletti negli originali): a parita di calcolo un singolo agente eguaglia o supera il multi-agente; il multi-agente aiuta su compiti paralleli/di ampiezza (+81%, +90,2%) e peggiora quelli sequenziali (fino a −70%), costa ≈ 15× token, 14 modalita di fallimento; scritture a un solo agente;
(b) hardware: decoding limitato dalla banda di memoria, quindi piu richieste sullo STESSO modello residente sono economiche, due modelli grandi diversi no; KV cresce con `num_ctx × OLLAMA_NUM_PARALLEL`; alcune architetture sono fissate a 1 slot da Ollama;
(c) la 4060 ha memoria e banda proprie: parallelismo hardware reale e sistema reattivo mentre la 3090 e occupata (anche a generare immagini);
(d) i modelli ComfyUI di massima qualita (18-24 GB) non coesistono con un LLM da 17 GB sulla 3090: serve divisione di tempo («modalita LLM» / «modalita immagine»).
Scartati: lo «sciame» di modelli diversi come default; «un solo agente sulla 4060» (spreca la GPU piu adatta agli agenti sempre attivi). **Le misure (sezione 7) sostengono il 14B parallelo; il 30B MoE e da rimisurare.**

**D17 — Candidati modelli (fonti di terzi, ottobre 2026, NON verificati da noi).** LLM per 24 GB: `qwen3.6:27b` (≈17 GB), `qwen3.8:27b` (≈18 GB, input immagini; il tag **esiste: verificato**, l utente lo ha scaricato e misurato dopo l aggiornamento di Ollama; le altre caratteristiche sono di terzi), `qwen3-coder:30b` (MoE, 3,3B attivi), `devstral:24b`; `qwen2.5-coder:32b` e «generazione precedente».
ComfyUI per la 3090: **Qwen-Image / 2512** (20B, Apache 2.0, fp8 ≈ 18-24 GB: qualita generale e testo), **FLUX.2 [dev]** (32B, non commerciale, solo quantizzato: GGUF Q4_K_S ≈ 19 GB), Z-Image Turbo (≈ 16 GB), FLUX.2 klein 4B (8-13 GB), HiDream, SDXL, Wan 2.2 (video).
Note per la **3090 (Ampere):** l **FP8 serve solo a risparmiare memoria** (conversione software, nessuna velocita; in una misura riportata piu lento del bf16); per qualita GGUF Q8/Q6 dove entra, per velocita INT8; **NVFP4 solo Blackwell**. Dettagli e fonti: `ARCHITETTURA_AGENTI_E_MODELLI.md`.

**D18 — `orchestra_bootstrap.py` v1.1.0.** Era un modulo senza `type = "filter"`: il framework Pipelines (verificato leggendo `main.py` del repository `open-webui/pipelines`) applica `inlet` solo ai moduli con `type == "filter"` e legge `valves.pipelines`/`priority`; senza `type` veniva registrato come `pipe` e `pipes()` non e API di Pipelines.
Ora e un filtro; contesto **compatto** (≈ 2,9 KB contro ≈ 11,4 KB: −75%) perche iniettare 3-4 mila token in ogni messaggio e pesante con `context_length` 8192; arricchisce un system message gia presente (prima veniva saltato); iniezione idempotente. Elenco file e manifest includibili con `include_file_list`/`include_manifest`.

**D19 — Documentazione.** README (en e it) corretti sul codice reale: ComfyUI **integrato** (non «legacy»), RAG su `0.0.0.0` protetto da `ufw` (non `127.0.0.1`), WebUI su IP LAN fisso e pubblicata via reverse proxy, compose **non usati** dal launcher, RAG nel venv di ComfyUI. Handoff v8 (GPU singola) **archiviato in `docs/archive/`** perche
`document-ai/` e indicizzata **ricorsivamente** dal RAG e conteneva hardware obsoleto; `hardware-report.md` (dump automatico del 2026-03-31) con addendum datato; handoff **v9** come riferimento architetturale; `.gitignore` a whitelist esteso (`/tests/`, `/docs/`).

**D20 — Versioning e canale di scrittura.** Tag annotati per ogni stato; branch + PR; commit separati e annullabili; mai `push --force`. Canale `ai-dispatch` con `branch` **sempre esplicito**, messaggi **senza apostrofi/virgolette** (il workflow li interpola in uno script shell), patch piccole (limite del `client_payload`, non documentato con precisione). Fallback: push diretto del branch.

**D21 — Esposizione di rete: solo documentata, NON modificata.** Il README originale diceva «RAG su 127.0.0.1 / non esporre pubblicamente»; il launcher avvia il RAG con `RAG_SERVICE_HOST="0.0.0.0"`, WebUI su IP LAN fisso e pubblicata via reverse proxy (`setup_security.sh` configura Caddy+ufw). Non e stata cambiata alcuna configurazione di rete senza il consenso dell utente. **Punto aperto (15).**

**D22 — Energia.** Solo **opt-in** e **misurato**: profili `eco`/`balanced`/`performance` = 70/85/100% del limite predefinito (clamp ai limiti del driver), non persistenti al riavvio, `sudo -n`, la 4060 laptop di norma non permette `-pl`. Nessun risparmio e stato dichiarato senza misura (`orchestra_power.sh bench` calcola token/joule). L effetto «piccoli modelli sulla 4060 evitano di svegliare la 3090» e un ragionamento, non una misura.

**D23 — Interpretazione di «qwen 3.8 image».** Risposta dell utente alla domanda «Qwen-Image o FLUX.2 [dev] per ComfyUI?». Interpretata come: **LLM `qwen3.8:27b`** (con input di immagini) **+ Qwen-Image per ComfyUI**. **NON confermata dall utente.** I workflow ComfyUI per Qwen-Image **non sono stati costruiti** proprio per questo.

## 6. Bug trovati e corretti (causa → correzione)

| # | Bug | Causa | Correzione |
|---|-----|-------|------------|
| B1 | `/vram` rispondeva sempre 2000 MB con 2 GPU → manifold sempre su `llama3.2:3b` | `nvidia-smi --query-gpu=memory.free` stampa una riga per GPU; `int(out.strip())` in ValueError → fallback | parsing multi-riga, ruoli, array `gpus[]` (`rag_service` v1.5.1; fallback L2 in `embedding_utils`) |
| B2 | Il **32b non veniva mai scaricato** (bug del launcher originale) | controllo «modello presente» con `grep` sul solo nome prima dei `:` (`qwen2.5-coder`, gia presente il 14b). I miei primi test lo avevano mascherato (contavano 6 modelli invece di 7) | `ollama_has_model` con confronto esatto nome:tag |
| B3 | `keep_alive` ignorato da Ollama in `image_loop` | era dentro `options`; e un parametro di primo livello. Funzionava solo per `OLLAMA_KEEP_ALIVE=0` dell istanza | parametro top-level (`_keep_alive_for`) |
| B4 | `orchestra_bootstrap` non faceva da filtro e iniettava ≈ 11,4 KB per messaggio | senza `self.type = "filter"` il framework lo registra come pipe; il contesto superava le esigenze di `context_length` 8192 | v1.1.0 (D18) |
| B5 | `Modelfile-orchestra` con `num_ctx 32768` | KV f16 ≈ 8,6 GB + 20 GB di pesi > 24 GB → offload su CPU | `num_ctx 12288` con il calcolo nel commento |
| B6 | Workflow: i file nuovi comparivano nel manifest con un commit di ritardo; elenco file di `AI_BOOTSTRAP.md` stantio (citava `AI_CONTEXT.md`) | la patch e applicata con `git apply` (senza staging) prima di generare il contesto; `git ls-files` non vede i file nuovi; la tabella era manuale | `generate_ai_context.sh`: `list_files` = tracciati + nuovi non ignorati; la sezione «File tracciati» e rigenerata (idempotente dopo il secondo giro) |
| B7 | **`docker run requires at least 1 argument`** al primo avvio reale | nel launcher **originale** le chiamate `ensure_container` per Ollama, Qdrant e Pipelines non avevano l immagine (i container preesistevano: il ramo di creazione non era mai eseguito). La ricreazione che ho introdotto lo ha attivato. Open WebUI aveva l immagine. **I test non lo vedevano: lo stub di docker non verificava l immagine** | immagine scelta dal sistema; ricreazione transazionale (D12); stub severo; scenario S15/S16 |
| B8 | `orchestra_sync.sh` poteva perdere un file locale **ignorato** con lo stesso percorso di uno del remoto | rilevava solo i file non tracciati e NON ignorati; `git switch` sovrascrive in silenzio i file ignorati | collisioni calcolate dai percorsi dell albero di destinazione (`git ls-tree`), test dedicato (scenario B2) |
| B9 | `tests/run_all.sh` dava falsi allarmi sul PC dell utente e scriveva `__pycache__` | controllava la sintassi di tutti i file della cartella (anche copie locali non tracciate, es. `document-ai/system/`) con `py_compile` | `tests/check_syntax.sh`: solo file tracciati, `ast.parse`, nessuna scrittura |
| B10 | `orchestra_bench_models.sh`: (a) richieste con JSON malformato; (b) blocco con `--telemetry` | (a) `\\"` in un heredoc chiude la stringa; (b) `wait` senza argomenti attende anche il campionatore della telemetria (che non termina). Scoperti dai test (PROC vuoto; timeout) | quoting corretto; `wait` solo sui PID delle richieste; trap su Ctrl-C |
| B11 | Gli stub nascondevano difetti reali | `echo "-e"` stampa una riga vuota (perdeva il flag `-e`); stub di docker senza controllo dell immagine; stub di curl senza validazione del JSON | `printf`, controlli come docker/Ollama veri |
| B12 | `sh start_ai_stack.sh` dava `Syntax error: "(" unexpected` (sembrava un file corrotto) | su Ubuntu `sh` e dash; lo script usa array bash | guardia `exec bash` nei due launcher (con `sh -n` l errore resta: e solo un parse, non esegue la guardia) |
| B13 | 14B al **15%/85% CPU/GPU**, 15,6 GB, 17 tok/s (prima serie) | **ipotesi principale:** richieste senza `num_ctx` → contesto predefinito di Ollama × `NUM_PARALLEL=3` → KV gonfiata. Confermata **indirettamente**: dopo `OLLAMA_CONTEXT_LENGTH=8192` e `num_ctx` esplicito: 100% GPU, 73 tok/s. La causa non e stata isolata con i log | `ORCHESTRA_CONTEXT_LENGTH` (D8); bench/smoke con `num_ctx` |
| B14 | `qwen3.8:27b` all **80% GPU / 20% CPU** | a riposo la 3090 aveva gia **7025 MiB** occupati: ComfyUI con SDXL in VRAM; 18 GB + 7 GB > 24 GB (totale misurato 22464 MiB, coerente). **Non e un giudizio sul modello** | `comfy_free_on_finish` (rc6, simulato), `--free-comfy` nei tool. **Da validare con un `/generate` reale** |
| B15 | `qwen3.8:27b`: HTTP 412 «requires a newer version of Ollama» | immagine Ollama locale vecchia; il launcher usava quella presente | `ORCHESTRA_PULL_IMAGES=1`; dopo l aggiornamento Ollama 0.40.0 |
| B16 | ComfyUI non parte con il launcher | il launcher passava `--normalvram`, **rimosso da ComfyUI** (era il default): argparse rifiuta l argomento. Trovato dall utente (correzione locale), verificato con `grep normalvram ComfyUI/comfy/cli_args.py` senza risultati | niente piu `--normalvram`; `--lowvram` resta opzionale per `start_comfyui.sh`; `tests/test_comfy_flags.sh` (fallisce col codice vecchio) |
| B17 | `/generate` perde il soggetto (es. cubo rosso → rosa rossa) | il prompt di analisi vision conteneva un **esempio concreto** (rosa rossa, gocce di rugiada); llava:7b lo copiava e il ciclo usava il suo `next_prompt` come prompt finale. **Verificato:** il Prompt usato della chat coincide byte per byte con l esempio nel codice | prompt con soli segnaposto + guardia `_usable_prompt` (vuoto, <= 15 caratteri o con `<...>` → si tiene il prompt corrente); 9 test anti-contaminazione. **Verificato sull hardware il 15 ottobre 2026 su 2 prompt** (cubo rosso su fondo bianco, gatto nero su sedia di legno: immagini e metadati PNG coerenti con i log; campione piccolo). Aperti: refine_prompt (qwen3.5:9b) potrebbe rispondere vuoto e ricadere sul `next_prompt` (ipotesi, non verificata); il ciclo non ancora il soggetto originale |
| B18 | `test_image_loop`: il controllo «evict aux: usa l URL dell aux» fallisce a volte (circa 1 su 20-40) | preesistente: codice e test identici a rc7 | ipotesi non provata: un thread in background (VRAM daemon) interferisce con i mock di `requests`. **Da isolare** (ripetere centinaia di volte, filtrare le chiamate nel test) |

## 7. Misure sull hardware reale (portate dall utente)

### 7.1 Link e potenza a riposo (`egpu_check.sh`)
3090: link **Gen1 x4** a riposo (il link scende di frequenza: **non e il valore sotto carico**), 19,9-29,5 W; 4060: Gen1 x8, 6,4-6,5 W. **Il link sotto carico non e ancora stato misurato.**

### 7.2 Prima serie (contesto predefinito di Ollama, `NUM_PARALLEL=3`, immagine Ollama vecchia)
Smoke test **23/24**: coordinator sulla 4060 **678 → 3248 MiB** (3090 invariata a 279); quality sulla 3090 **279 → 15600 MiB** (4060 invariata). Unico ✘: il 14B non era al 100% GPU.
`qwen2.5-coder:14b-instruct-q4_K_M`: **15%/85% CPU/GPU**, 15598 MiB; per flusso 17,2 / 13,3 / 10,4 tok/s; totali 15,7 / 24,7 / 29,1 tok/s; speedup 1,00 / 1,57 / 1,85. `qwen3.8:27b`: non scaricato (412).

### 7.3 Seconda serie (Ollama **0.40.0**, `OLLAMA_CONTEXT_LENGTH=8192`, contesto 8192)
Smoke test **24/24**: coordinator **697 → 3361 MiB** (3090 invariata a 7025); quality **7025 → 18078 MiB** (4060 invariata a 697).

| Modello | Flussi | tok/s per flusso | tok/s totali | speedup | Note |
|---------|--------|------------------|--------------|---------|------|
| qwen2.5-coder:14b-instruct-q4_K_M | 1 | 73,2 | 72,3 | 1,00x | **100% GPU**, 11 GB (`ollama ps`), contesto 8192 |
| | 2 | 66,7 | 129,1 | 1,79x | |
| | 3 | 56,7 | 165,2 | 2,28x | ogni agente perde ≈ 22%, il sistema produce 2,3× i token |
| qwen3.8:27b | 1 | 26,4 | 23,5 | 1,00x | **20%/80% CPU/GPU**, 18 GB, 22464 MiB totali |
| | 2 | 23,9 | 22,0 | 0,94x | misura **non valida come giudizio sul modello** (B14) |

Il valore **7025 MiB gia occupati prima del carico** (dallo smoke test) e ComfyUI/SDXL: l inferenza e il totale 22464 MiB lo confermano.

### 7.4 Terza serie (riportata dall utente; ComfyUI svuotato, 100% GPU)
| Modello | Flussi | tok/s per flusso | tok/s totali | speedup |
|---------|--------|------------------|--------------|---------|
| qwen3:30b-a3b (MoE, 19 GB) | 1 | **167,4** | 148,2 | 1,00x |
| | 2 | 125,8 | **238,5** | 1,61x |
| | 3 | 101,9 | 166,5 | **1,12x** ← calo |
| qwen2.5-coder:14b (denso, 11 GB) | 1 | 73,7 | 72,6 | 1,00x |
| | 2 | 66,6 | 128,7 | 1,77x |
| | 3 | 55,9 | 162,7 | 2,24x |

**Solido:** il MoE a un flusso e circa il doppio del 14B (167 vs 74 tok/s; coerente con i ≈ 3B parametri attivi per token); il 14B scala in modo regolare e riproducibile fra le serie (2,24-2,28x a 3 flussi).
**Non sostenuta dai dati:** la spiegazione proposta («il link Thunderbolt satura a 3 flussi; gen1 x4 ≈ 1 GB/s»). Motivi: (1) con il modello interamente in VRAM il decoding legge i pesi dalla memoria della scheda (≈ 936 GB/s sulla 3090), non dal link: sul link passano il token in ingresso e i logit in uscita
(≈ 150 mila token di vocabolario → ordine di 0,6 MB per token: **stima**; qualche centinaio di MB/s anche con 3 flussi); (2) `gen.current=1` e stato letto **a riposo**; (3) il 14B, sullo stesso link, scala bene.
**Ipotesi alternative (non provate):** misura rumorosa (a 167 tok/s una richiesta da 200 token dura ≈ 1,2 s; l overhead fisso pesa sul totale; il bench ora ha `--repeat` e un avviso); instradamento degli esperti del MoE con batch piccoli (piu esperti letti con 3 flussi); limite di potenza o termico.
**Quindi «il punto di equilibrio e 2 flussi» NON e dimostrato.** Comando per decidere: `bash document-ai/scripts/orchestra_bench_models.sh --free-comfy --parallel "1 2 3" --tokens 600 --repeat 3 --telemetry qwen3:30b-a3b`
(riporta mediana con min-max, utilizzo, watt, clock, motivi di throttling, generazione/larghezza del link osservate, traffico PCIe rx/tx in MB/s, token/joule). Se il traffico PCIe resta ben sotto la capacita e non c e throttling, il limite non e il collegamento.

### 7.5 Cosa sostengono le misure
Sostengono: (a) isolamento GPU reale; (b) 14B parallelo come base per agenti concorrenti (≈ 165 tok/s totali con 3 flussi); (c) il MoE 30B come candidato per **velocita** dell agente forte.
NON sostengono ancora: qualita del MoE rispetto a un denso da 27B (3B attivi ≠ 27B densi: va confrontata sui compiti reali); scaling del 27B in parallelo (misura falsata da B14); consumi (mai misurati).

## 8. Cosa NON e verificato (elenco completo)

- Il calo del 30B MoE a 3 flussi: rumore o reale? causa? (7.4)
- Link PCIe **sotto carico** (generazione/larghezza/traffico) e throttling.
- Rilascio di ComfyUI a fine **`/generate` reale** (`comfy_free_on_finish`) e ritorno della VRAM per il 27B: solo simulato.
- Pre-caricamento (warm-up) e keep-alive: beneficio reale sul link TB4 non misurato; failover aux→main non provato spegnendo l Ollama aux; degrado con eGPU scollegata e ripristino `.bak`: solo simulati (l avvio reale dopo il fix B7 e riuscito: smoke test e benchmark hanno girato sui nuovi container).
- Flash attention + KV `q8_0` con i modelli **vision** (llava/moondream): non testati (il coordinator e il 14B si, 100% GPU).
- `orchestra_power.sh` (profili, `bench`, log) e `orchestra_bench_models.sh --telemetry`: **mai eseguiti su hardware** (nomi dei campi `nvidia-smi` per throttling/PCIe: coperti solo da stub; con driver 590 il campo recente e `clocks_event_reasons.active`, il bench prova a rilevarlo da `--help-query-gpu`).
- Parallelismo del 27B e del MoE su Ollama (alcune architetture ibride/ricorrenti sono fissate a 1 slot): da misurare a 100% GPU.
- Tutte le cifre di VRAM dei modelli ComfyUI, le caratteristiche dei modelli LLM e gli studi sul multi-agente: **fonti di terzi** (sezione 5, D16/D17). FLUX.2 [dev]: requisiti reali del modello «Mistral ≈ 24B» per il prompt non verificati.
- Interpretazione di «qwen 3.8 image» (D23): non confermata. **Workflow ComfyUI per Qwen-Image: non costruiti.**
- Esposizione di rete reale (RAG su `0.0.0.0` + `ufw`): descritta, non verificata ne cambiata.
- Il segreto `ORCHESTRA_PAT`: assunto scaduto in base al fallimento del checkout (log non leggibile con il token fornito).

## 9. Mappa del repository, componenti e servizi

### 9.1 Componenti e versioni (verificate nei sorgenti)
| File | Versione | Ruolo |
|------|----------|-------|
| `start_ai_stack.sh` | **v3.9** | launcher: ruoli GPU, Ollama main/aux, ricreazione transazionale, Qdrant, Pipelines, WebUI, RAG, ComfyUI in primo piano |
| `start_comfyui.sh` | — | avvio ComfyUI standalone (stessa logica GPU; `--lowvram` sulle GPU piccole) |
| `document-ai/scripts/orchestra_gpu_env.sh` | — | libreria condivisa (sourced): `detect_gpu_roles`, `gpu_for_role`, `gpu_total_mb`, `aux_ollama_enabled`, `recreate_if_not_pinned`, `recreate_if_env_stale`, `recreate_if_image_outdated`, `retire_container`/`restore_container`/`discard_backup`, `pick_image`, `image_id_of`, `ollama_has_model`, `container_gpu_count`, `comfyui_gpu_setup` |
| `ollama/pipelines/orchestra_manifold.py` | **v3.9.0** | routing, 8 agenti, selezione modelli adattiva, backend per ruolo con failover, comandi `/generate /review /rag /evolve` |
| `ollama/pipelines/image_loop.py` | **v2.8.0** | loop SDXL+LCM (draft 512×512 4 step; finale 1024×1024 6 step) con vision e refine |
| `ollama/pipelines/embedding_utils.py` | **v2.0.3** | embedding, cache, daemon VRAM (≈ 5 s) con snapshot per ruolo |
| `ollama/pipelines/orchestra_bootstrap.py` | **v1.1.0** | filtro: regole del progetto nel system message (compatto) |
| `ollama/pipelines/rag_filter.py` | v1.6.0 | recupero dal RAG e iniezione del contesto |
| `ollama/pipelines/orchestra_evolver.py` | non modificato (versione non riverificata) | `/evolve` |
| `rag/rag_service.py` | **v1.5.1** | Flask: `/health /status /search /index /index/async /vram /deploy` |
| `ollama/Modelfile-orchestra` | — | base `qwen2.5-coder:32b`, `num_ctx 12288` |

### 9.2 Cartelle (tracciate)
`(root)`: `README.md`, `README.it.md`, `AI_BOOTSTRAP.md` (regole per l AI + tabella file generata), `AI_MANIFEST.md`/`AI_CTX_*` (generati), `AI_READ_PROTOCOL.md`, `.gitignore` (**whitelist**: `/*` ignorato; riabilitate `ollama/ document-ai/ logs/ rag/ workflows/ tests/ docs/`; un nuovo file nella root richiede una regola `!/…`).
`.github/workflows/ai-commit.yml` · `docs/` (`VERSIONING.md`, `HANDOFF_PROSSIMA_SESSIONE.md`, `archive/ORCHESTRA_HANDOFF_v8.md`) · `document-ai/knowledge/` (**indicizzata dal RAG**: `ORCHESTRA_HANDOFF_v9.md`, `ARCHITETTURA_AGENTI_E_MODELLI.md`, `ORCHESTRA_3090_MIGRAZIONE.md`, `hardware-report.md`, e file dell utente non legati a questo lavoro) ·
`document-ai/scripts/` (strumenti, sezione 11) · `document-ai/config/` (esempi valves, `orchestra.env.example`, `ufw_rules_export.txt`, compose non usati) · `ollama/pipelines/` · `rag/` (contiene anche script di patch storici e `pattern_logger.py` duplicato) · `workflows/` (`sdxl_base.json`, `sd15.json`) · `tests/`.
Non tracciati (locali dell utente): `orchestra.env`, `.orchestra_token`, `.webui_secret_key`, `ComfyUI/`, `document-ai/system/` (copie del codice per il RAG), `rag/.file_hash_cache.json` (smesso di tracciare dall utente nel commit «chore(rag)»), `logs/*`.

### 9.3 Servizi, porte, volumi
| Servizio | Contenitore / processo | Porta | Bind |
|----------|------------------------|-------|------|
| Ollama main | `ai-ollama-session` (GPU main) | 11435 → 11434 | `127.0.0.1` |
| Ollama aux | `ai-ollama-aux-session` (GPU aux) | 11436 → 11434 | `127.0.0.1` |
| Open WebUI | `ai-webui-session` | 3001 → 8080 | IP LAN `192.168.1.51` (fisso nel launcher) |
| Pipelines | `ai-pipelines-session` (mount `~/ai-sessioni` → `/app/ai:ro`) | 9099 | `127.0.0.1` |
| Qdrant | `ai-qdrant-session` | 6333 | `127.0.0.1` |
| RAG service | processo host (venv di ComfyUI) | 6335 | `0.0.0.0` + `ufw` (solo bridge Docker 172.17/172.19) |
| ComfyUI | processo host (primo piano nel launcher) | 8188 | `0.0.0.0` + `ufw` |
Volumi nominati (mai toccati dalle ricreazioni): `ollama-session`, `ollama-aux-session`, `qdrant-data`. Rete Docker: `ollama_default`. Collection Qdrant: `orchestra` (RAG), `orchestra_routing`.

## 10. Configurazione

### 10.1 Variabili d ambiente (tutte opzionali; si mettono in `~/ai-sessioni/orchestra.env`, vedi `document-ai/config/orchestra.env.example`)
| Variabile | Default | Effetto |
|-----------|---------|---------|
| `ORCHESTRA_GPU_MAIN`, `ORCHESTRA_GPU_AUX` | auto (main = piu VRAM) | UUID dei ruoli (alias legacy `ORCHESTRA_GPU_ID` = main in rag_service/embedding_utils/manifold) |
| `ORCHESTRA_AUX_OLLAMA` | 1 | `0` = nessun Ollama sulla 4060 (variante «4060 solo ComfyUI») |
| `ORCHESTRA_COMFY_ROLE` | main | GPU di ComfyUI: `main`/`aux` |
| `COMFY_EXTRA_ARGS` | — | argomenti aggiuntivi per ComfyUI |
| `ORCHESTRA_FLASH_ATTENTION`, `ORCHESTRA_KV_CACHE_TYPE` | 1, q8_0 | flash attention e KV (con FA=0 la KV torna f16) |
| `ORCHESTRA_MAIN_PARALLEL`, `ORCHESTRA_AUX_PARALLEL` | 1, 1 | `OLLAMA_NUM_PARALLEL` (la KV cresce di `num_ctx × parallel`) |
| `ORCHESTRA_CONTEXT_LENGTH` | 8192 | `OLLAMA_CONTEXT_LENGTH` (= `context_length` del manifold) |
| `ORCHESTRA_HEAVY_MODEL` | — | modello pesante aggiuntivo (es. `qwen3.8:27b`), download non fatale, solo con ≥ 20 GB |
| `ORCHESTRA_PULL_IMAGES` | 0 | `1` = `docker pull` dell immagine Ollama e ricreazione dei container con etichetta diversa |
| `ORCHESTRA_RECREATE_OLLAMA` | 0 | `1` = ricreazione forzata (una volta) |
| `ORCHESTRA_OLLAMA_IMAGE`, `ORCHESTRA_QDRANT_IMAGE`, `ORCHESTRA_PIPELINES_IMAGE` | auto | forzano l immagine usata alla (ri)creazione |
| `ORCHESTRA_POWER_PROFILE` | — | `eco`/`balanced`/`performance` all avvio (richiede `sudo -n`); `ORCHESTRA_POWER_MAIN_W`/`_AUX_W` override in watt; `ORCHESTRA_POWER_ROLES`, `ORCHESTRA_POWER_LOG` |
| `ORCHESTRA_BENCH_MODEL/RUNS/URL` | 14B, 3, :11435 | solo per `orchestra_power.sh bench` |
| `ORCHESTRA_OLLAMA_URL`, `ORCHESTRA_OLLAMA_AUX_URL`, `ORCHESTRA_RAG_URL`, `ORCHESTRA_COMFY_URL` | localhost | URL usati da smoke test e bench |
| `ORCHESTRA_BACKUP_DIR` | `~/orchestra-backup-locale` | dove `orchestra_sync.sh` salva i backup |
Nel container Pipelines vengono passate: `ORCHESTRA_GPU_MAIN/AUX`, `OLLAMA_AUX_URL` (vuoto = aux disattivato), `ORCHESTRA_COMFY_ROLE`.

### 10.2 Valve nuovi (default nel codice; i valve salvati nel pannello di Open WebUI prevalgono a runtime)
- **manifold:** `ollama_url_aux` (env `OLLAMA_AUX_URL`), `aux_models`, `aux_health_ttl_s=20`, `vram_quality_full_mb=11000`, `keep_alive_aux_s=1800`, `keep_alive_main_s=900`. Preesistenti rilevanti: `context_length=8192`, `vram_fast_min_mb=7000`, `vram_fallback_min_mb=5500`, `vram_partial_fallback_mb=3200`, `vram_vision_full_mb=5000`, `vram_vision_partial_mb=3000`, `quality_num_gpu=20`, `routing_similarity_threshold=0.45`.
- **image_loop:** `ollama_url_aux`, `aux_models`, `aux_health_ttl_s`, `comfy_role`, `loop_keep_alive_s=300`, `warmup_enabled=true`, `warmup_main_min_free_mb=16000`, `final_min_free_mb=6000`, `evict_llms_for_comfy=true`, `comfy_free_on_finish=true`, `comfy_free_min_total_mb=16000`.
- **bootstrap:** `pipelines=["*"]`, `priority=0`, `enabled`, `sections`, `include_file_list=false`, `include_manifest=false`, `max_bytes=3500`.
- Discrepanza storica (non toccata): `max_context_chars` del `rag_filter` vale 12000 nel codice, 7500 e 3000 nei file `valves.json` del repo; a runtime vale il pannello di Open WebUI.

## 11. Strumenti (`document-ai/scripts/`) — uso e cosa garantiscono

| Script | Comando tipico | Cosa fa / garantisce |
|--------|----------------|----------------------|
| `egpu_check.sh` | `bash document-ai/scripts/egpu_check.sh` | sola lettura: GPU, UUID, righe `export ORCHESTRA_GPU_*` proposte, link PCIe, runtime Docker |
| `orchestra_smoke_test.sh` | `... [--load] [--free-comfy]` | ruoli, `/vram`, **una sola GPU** in ogni container, servizi, modelli, ComfyUI sulla GPU giusta; con `--load` carica un modello per backend con lo stesso `num_ctx` del manifold e verifica che la memoria cresca **sulla GPU attesa e non sull altra** e che sia **100% GPU**; avvisa se la GPU e gia occupata |
| `orchestra_bench_models.sh` | `... --parallel "1 2 3" --tokens 600 --repeat 3 --telemetry [--free-comfy] [--role aux] [--ctx 8192] MODELLO...` | tok/s per flusso e totali con N richieste, speedup, VRAM, processore (`ollama ps`); mediana con min-max; avvisa se il modello non e installato (con comando di pull) o se la richiesta e troppo breve; `--telemetry`: GPU %, watt, clock, throttling, link PCIe osservato, traffico rx/tx, token/J. Scarica il modello a fine misura |
| `orchestra_power.sh` | `status`, `profile eco\|balanced\|performance`, `restore`, `log`, `report`, `bench [profili]` | power limit in % del predefinito (70/85/100) con clamp; non persistente; `bench` ripristina il limite di **partenza** anche con Ctrl-C |
| `orchestra_sync.sh` | `bash document-ai/scripts/orchestra_sync.sh [branch-o-tag]` | allineamento locale sicuro (sezione 14); non fa mai push, reset --hard o cancellazioni |
| `generate_ai_context.sh` | (chiamato dal workflow) | rigenera manifest, bundle e tabella file di `AI_BOOTSTRAP.md` |

## 12. Suite di test (400 controlli, tutti simulati)

`bash tests/run_all.sh` (requisiti: `bash`, `python3`, `pip install flask pydantic requests`; nella sandbox `pip install --break-system-packages …`). **Verde al 100%** anche da clone pulito del remoto (verificato piu volte).

| Test | Controlli | Cosa copre |
|------|-----------|------------|
| `test_vram_endpoint.py` | 9 | `/vram`: ruoli auto/env/alias, `?gpu=`, degrado eGPU, fallback 2000 |
| `test_vram_daemon.py` | 2 | daemon contro rag_service nuovo e vecchio |
| `test_manifold.py` | 41 | backend per ruolo, failover, soglie 11000, keep-alive, vision, banner, fallback subprocess |
| `test_image_loop.py` | 65 | ruoli, keep-alive, `/free` solo se serve, warm-up sicuro con GPU singola, evict LLM, rilascio a fine loop, flusso completo |
| `test_bootstrap.py` | 19 | classificazione come filtro, contesto compatto, idempotenza, cache |
| `test_launcher.sh` | 107 | scenari S1-S20 sul launcher completo con docker/nvidia-smi/curl/python finti: avvio pulito, container preesistenti, idempotenza, eGPU assente, ruoli, aux non risponde, UUID obsoleto, lib assente, immagini, ricreazione transazionale e rollback, parallelismo, modello pesante, `sh`, contesto, aggiornamento immagine, power |
| `test_power.sh` | 26 | profili, clamp, override, 4060 non modificabile, log/report, bench tok/J, ripristino |
| `test_bench.sh` | 44 | aritmetica di throughput, `num_ctx`, modello mancante, errore API, `--free-comfy`, mediana, ripetizioni, telemetria, throttling, avviso richieste brevi |
| `test_sync.sh` | 32 | merge in conflitto, file in collisione (anche **ignorati**), file privati intatti, commit locali non pubblicati, tag, riferimento sconosciuto, controllo sintassi |
| `test_comfy_flags.sh` | 8 | flag di memoria di ComfyUI per 3090 / 4060 / nessuna GPU, con e senza `--lowvram`; nessuna riga di codice passa `--normalvram` |
| `test_diag.sh` | 23 | orchestra_diag.sh: riepilogo in testa, sola lettura, segreti mai letti e oscurati, file compatto, degrado con grazia senza nvidia-smi o docker |
| `test_smoke.sh` | 24 | sano e guasti che DEVONO essere rilevati (GPU doppia nel container, memoria che «perde», offload CPU, ComfyUI sulla GPU sbagliata, eGPU assente, VRAM gia occupata) |

**Stub (`tests/helpers/stubs/`, `stubs_power/`):** `docker` (stato in `$STUB_STATE`: un file per container con un argomento per riga, `.image`, `.bak`, `models/`, `calls.log`; **impone l immagine, supporta rename/label/ps/exec/inspect/images/pull**), `nvidia-smi` (4060 indice 0, 3090 indice 1, campi di potenza e telemetria, `dmon`, `-pl`), `curl` (JSON validato, `/vram`, `/system_stats`, `/free`, `/api/generate` con token/durate dipendenti dal power limit), `python`, `sleep` (no-op), ecc.
Manopole `FAKE_*`: `ONLY_4060, MIN_3090, PROC, LEAK, COMFY_NAME, IMAGES, RUN_FAIL, PULL_FAIL, PULL_UPDATES, OLLAMA_VERSION, API_ERROR, EVAL_TOKENS, THROTTLE, NEW_DRIVER, PCIE_RX, PCIE_TX, DMON_IDX` e `STUB_CURL_FAIL`.
**Regola:** ogni nuovo comportamento ha il suo test; **lo stub deve imporre i vincoli reali** (la lezione di B7/B10/B11); prima di pubblicare, suite da clone pulito.
Nota: l etichetta di `run_all.sh` ora dice «S1-S20».

## 13. Procedura di pubblicazione (sandbox dell AI)

1. **Prepara:** commit logici e separati con `git add -A -- <percorsi espliciti>` (mai `git add -A` generico: in questa sessione un `git commit` ha inglobato per errore file gia in staging); messaggi **senza apostrofi e senza virgolette**; `bash tests/run_all.sh` verde; clone pulito + suite.
2. **Canale preferito `ai-dispatch`** (richiede il segreto `ORCHESTRA_PAT` valido): per ogni commit `git show --binary --format= <hash> > patch.diff`; payload JSON `{"event_type":"ai-update","client_payload":{"patch":"<base64>","branch":"dual-gpu-final","message":"<messaggio>"}}`; `POST https://api.github.com/repos/claudio3g/orchestra-ai/dispatches` (atteso **HTTP 204**); poi attendere il run (`GET /repos/…/actions/runs?event=repository_dispatch`) e leggere i job (`…/runs/<id>/jobs`): devono essere `validate`, `sandbox-test`, `commit-push` = success.
   **Sempre `branch` esplicito.** Una patch alla volta (il bot committa e rigenera il contesto); payload usati finora ≤ 30 KB senza problemi (il limite preciso non e documentato).
3. **Fallback se il dispatch non parte:** push diretto del branch: `GIT_ASKPASS` con uno script temporaneo che stampa il token da variabile d ambiente (mai nel remote, nei file o nell output: filtrare con `sed`), `git push -u origin dual-gpu-final` (**mai `--force`**). Prima eseguire `generate_ai_context.sh` e committare i file generati (`chore(ai)`), come fa il bot.
4. **Dopo ogni pubblicazione:** clone fresco del branch remoto + `bash tests/run_all.sh`; tag annotato `dual-gpu-rcN` con messaggio (cosa, test, stato di validazione, rollback); commento sulla PR #2 (`POST /repos/…/issues/2/comments`); rimuovere token e askpass dalla sandbox e verificare con `grep -rIl github_pat_`.
5. **Mai:** scrivere su `main`; unire la PR; Squash; `push --force`; messaggi con apici; token in file/commit/output; modificare a mano i file generati.

## 14. Per l utente: allineare la cartella locale e rilanciare

```bash
cd ~/ai-sessioni
bash document-ai/scripts/orchestra_sync.sh                     # backup in ~/orchestra-backup-locale/<data>/, poi allineamento
# prima volta (script non ancora presente): curl -fsSL -o /tmp/orchestra_sync.sh https://raw.githubusercontent.com/claudio3g/orchestra-ai/dual-gpu-final/document-ai/scripts/orchestra_sync.sh ; less /tmp/orchestra_sync.sh ; bash /tmp/orchestra_sync.sh
ORCHESTRA_PULL_IMAGES=1 ORCHESTRA_RECREATE_OLLAMA=1 ORCHESTRA_HEAVY_MODEL=qwen3.8:27b ORCHESTRA_MAIN_PARALLEL=3 bash start_ai_stack.sh   # prima volta dopo gli aggiornamenti
bash document-ai/scripts/orchestra_smoke_test.sh --load [--free-comfy]
```
`orchestra_sync.sh` annulla un merge a meta, mette da parte (stash) le modifiche ai file tracciati, sposta in backup **solo** i file non tracciati/ignorati che collidono col remoto (gli altri file locali — `orchestra.env`, `document-ai/system/`, `.v01`, segreti — non vengono toccati), passa al branch o al tag, verifica commit/sintassi/launcher identico al remoto/permessi e lancia i test se `flask pydantic requests` sono installati. Un merge lasciato in conflitto produce marcatori `<<<<<<<` nei file: e la causa tipica di un launcher che «sembra corrotto».

## 15. Lavoro in sospeso e prossimi passi (in ordine di priorita)

**P0 — Sblocco del canale e pubblicazione (bloccante).** (a) L utente aggiorna il segreto `ORCHESTRA_PAT` (3.5) oppure autorizza esplicitamente il push diretto come procedura ordinaria. (b) Verificare che i commit pendenti e questo documento siano su GitHub (3.3) e che esista il tag `dual-gpu-rc7`.

**P1 — Misure su hardware ancora mancanti** (nessuna richiede codice nuovo):
1. `orchestra_bench_models.sh --free-comfy --parallel "1 2 3" --tokens 600 --repeat 3 --telemetry qwen3:30b-a3b` → il calo a 3 flussi e reale? e il link? e throttling? (7.4)
2. `... --free-comfy --parallel "1 2" qwen3.8:27b` → a 100% GPU: tok/s e speedup (se ≈ 1,0x il modello non scala in parallelo su Ollama → singolo agente forte).
3. Un `/generate` reale e poi `nvidia-smi --query-gpu=memory.used --format=csv`: la VRAM torna libera a fine generazione? (`comfy_free_on_finish`, B14)
4. Test dei modelli **vision** con flash attention + KV q8_0 (nessun errore, 100% GPU); test del **failover** (fermare `ai-ollama-aux-session` e verificare che il manifold risponda dal main); `orchestra_power.sh bench eco balanced performance` (token/joule): **adottare un profilo solo se i token/joule migliorano e i token/s restano accettabili**.

**P2 — Scelta del prossimo sviluppo (da decidere con l utente).** L utente non ha ancora scelto tra:
- **(A) Modalita «team» (consigliata come primo passo).** *Perche:* ha il numero di partenza misurato (3 flussi sul 14B ≈ 165 tok/s totali), non dipende da download grandi ne da scelte ancora non confermate (D23), e realizza il «piu agenti che collaborano contemporaneamente» nel modo che le evidenze sostengono (D16). *Schizzo di progetto (NON implementato):* comando `/team <compito>` nel manifold, non invasivo (flusso predefinito invariato);
  un **pianificatore** decide **esplicitamente** se il compito e parallelizzabile (altrimenti rimanda al singolo agente forte) e produce al massimo 3 sottocompiti indipendenti; fino a **3 lavoratori** in parallelo sullo **stesso** modello 14B del main (`ORCHESTRA_MAIN_PARALLEL=3`, contesto 8192); principio **«scrive un solo agente»**: i lavoratori producono analisi, solo il sintetizzatore finale redige/modifica;
  un **revisore** con il modello forte se la VRAM lo consente (altrimenti il 14B: modelli grandi diversi non coesistono con ComfyUI); tetti di token e timeout; avviso dell aumento di costo (≈ 15× token); valve `team_max_workers`, `team_model_worker`, `team_model_reviewer`; test con Ollama simulato come in `test_manifold.py`. Dipendenze: nessuna bloccante; migliora se P1.2 conferma lo scaling del 27B.
- **(B) Pipeline immagini a due stadi.** Bozze e giudizio visivo su un **secondo ComfyUI sulla 4060** (SDXL+LCM, gia in uso; porta es. 8189) e **render finale** sul ComfyUI della 3090 con il modello di punta; **un solo** scarico dell LLM per immagine (oggi, con ComfyUI sulla 3090, l LLM viene scaricato al primo `/generate` e ricaricato dopo). Richiede: **conferma del modello** (D23: Qwen-Image oppure FLUX.2 [dev] Q4), workflow JSON dedicati, verifica dei requisiti reali (RAM 32 GB, modello del prompt, download 20-40 GB), un secondo avvio ComfyUI nel launcher, test. Da costruire un passo alla volta.

**P3 — Qualita del MoE rispetto a un denso da 27B.** La velocita (167 vs 26-74 tok/s) non e qualita. Costruire con l utente un piccolo insieme di compiti reali (amministrazione Linux, codice, ML, revisione) e confrontare `qwen3:30b-a3b` / `qwen3.8:27b` / 14B: valutazione dell utente (non automatica). Solo dopo assegnare il modello all agente forte e a `orchestra_dev`/`reasoner`.

**P4 — Merge della PR #2.** Solo dopo P1 e per decisione dell utente; «Create a merge commit» o «Rebase and merge» (mai Squash). **Dopo il merge:** sincronizzare (`orchestra_sync.sh main`); aggiornare le copie in `document-ai/system/` (il RAG le usa per `orchestra_dev`); ripulire il RAG dai chunk obsoleti (l indicizzatore **non rimuove** i chunk di file cancellati/spostati): snapshot Qdrant (`POST :6333/collections/orchestra/snapshots`), poi `DELETE :6333/collections/orchestra`, cancellare `rag/.file_hash_cache.json`, riavviare (indicizza da solo se la collection e vuota) o `/rag index`.

**P5 — Indurire il workflow `ai-commit.yml`.** (i) `client_payload.message`/`patch`/`branch` sono interpolati direttamente in script shell (un apice nel messaggio lo rompe; rischio di iniezione per chi puo inviare dispatch): passarli tramite `env:`; (ii) `branch` ha default `main`: renderlo obbligatorio o rifiutare `main`; (iii) il checkout dipende dal segreto `ORCHESTRA_PAT`. Modificare un workflow richiede permessi dedicati: da concordare con l utente.

**P6 — Esposizione di rete.** Allineare README e launcher: oggi RAG (6335) e ComfyUI (8188) sono su `0.0.0.0` (necessario ai container tramite il bridge Docker) protetti da `ufw`; WebUI su IP LAN fisso e pubblicata via reverse proxy. Decidere con l utente se tenere cosi o legare i servizi all IP del bridge Docker; nessuna modifica senza consenso (D21).

**P7 — Rifiniture:** metriche per GPU in `/status` e nei log dei pattern; spostare in `orchestra.env` i valori fissi del launcher (`192.168.1.51`, `$HOME/ai-sessioni`, mount SSD); pulizia (branch `dual-gpu-step1`/`step2a` obsoleti — decisione dell utente; `pattern_logger.py` duplicato in 3 posti; `ollama/pipelines/github_tools/valves.json` senza modulo; script di patch storici in `rag/`; compose non usati).

**P8 — Chiusura:** l utente **elimina il token** (e ruota il segreto) quando il progetto e sistemato (decisione sua, sezione 3.5).

## 16. Insidie e lezioni apprese

### 16.1 Del progetto
- **`ensure_container` riusa i container esistenti.** Cambiare argomenti (`--gpus`, `-e`, immagine) non ha effetto finche il container non viene ricreato: usare gli helper `recreate_*` (transazionali) e ricordare che le variabili d ambiente sono fissate alla creazione.
- **Il ramo di creazione non era mai stato eseguito** nel launcher originale per Ollama/Qdrant/Pipelines (B7): qualunque modifica che faccia ricreare un container lo esercita. WebUI e l unico con l immagine nella chiamata.
- **`set -e` e liste `&&`:** un fallimento non finale di una lista `A && B` non ferma lo script, ma un `return 1` finale si; un modello obbligatorio che non si scarica ferma tutto (voluto).
- **`.gitignore` a whitelist:** i nuovi file nella root sono ignorati; `git add -A` non li vede; `tests/` e `docs/` sono stati riabilitati. Una prima volta `tests/` non e stata committata proprio per questo.
- **Il RAG indicizza `document-ai/` ricorsivamente (anche `.py`, `.sh`, `.json`)** e non rimuove i chunk dei file cancellati: documenti obsoleti restano come contesto finche non si ripulisce (P4). Il report hardware (dump del 2026-03-31) e stato lasciato intatto con un addendum in cima.
- **Il workflow applica la patch senza staging:** i file nuovi non erano nel manifest (B6); `branch` assente = `main`.
- **Ollama:** `OLLAMA_KEEP_ALIVE=0` e `MAX_QUEUE=10` restano sul main; il manifold passa `keep_alive` per richiesta. `num_ctx` diverso tra richieste forza il ricaricamento del modello: usare sempre quello del manifold (8192) nelle misure.
- **ComfyUI:** `POST /free` con `{"unload_models":true,"free_memory":true}`; sulla 3090 l FP8 non da velocita (Ampere); i checkpoint grandi passano dal link TB4 (caricamento lento, inferenza no).
- **Il link PCIe a riposo (Gen1) non e quello sotto carico:** non trarre conclusioni senza `--telemetry`.
- Nel container Pipelines **non** rimuovere `--gpus all` e il mount di `nvidia-smi` (vincolo della v8: serve al fallback del daemon VRAM).
- Ricreare Pipelines reinstalla le dipendenze pip all avvio (piu lento una volta).

### 16.2 Di processo (sandbox dell AI)
- **Non scrivere cifre a memoria nei documenti:** in questo stesso handoff una cifra (diffstat) era sbagliata e l ho corretta dopo la verifica. Verificare sempre da `git`/dai log; distinguere misura/stima/fonte di terzi.
- **Lo stub deve imporre i vincoli reali** (immagine di docker, JSON valido, `rename`, label): uno stub permissivo ha nascosto B7 e B10. Dopo ogni bug reale: scrivere il test che lo riproduce e verificare che **fallisca** con il codice vecchio.
- **Non usare `pkill -f <nome-script>`:** il nome compare nella riga di comando della shell dello strumento e la uccide (e successo). Usare `timeout` sui comandi lunghi (limite di 300 s per comando) e scrivere gli output intermedi su file.
- **Heredoc annidati con lo stesso delimitatore** si chiudono prima del tempo: usare delimitatori diversi (`PYEDIT`, `PYX`, `EOF`). **Costrutti bash (array, `<(…)`) in `sh` falliscono:** salvare gli script su file ed eseguirli con `bash`. `cut -c` su UTF-8 puo produrre output binario: estrarre righe con Python.
- `echo "-e"` stampa una riga vuota: usare `printf '%s\n'`. `\\"` dentro una stringa tra apici doppi chiude la stringa. `wait` senza argomenti attende anche i processi di sfondo (campionatore). Con `sleep` finto (no-op) i cicli di campionamento girano a vuoto: terminarli con `kill` dei PID.
- Il fetch web puo restituire pagine **in cache** (il repository sembro vuoto): in caso di dubbio clonare con git.
- Strumenti utili nella sandbox: `pip install --break-system-packages shellcheck-py flask pydantic requests`; `xxd` non c e; `git clone` da GitHub funziona (dominio consentito).
- **Disciplina di pubblicazione:** commit piccoli e separati, clone pulito + suite prima di ogni push, tag e commento PR dopo, pulizia del token (3.5, sezione 13).

## 17. Riferimenti nel repository (leggere in quest ordine)
1. `AI_BOOTSTRAP.md`, `AI_READ_PROTOCOL.md`, `document-ai/AI_WORKFLOW.md` — regole anti-allucinazione e canale di scrittura (dell utente).
2. **Questo documento.**
3. `document-ai/knowledge/ORCHESTRA_HANDOFF_v9.md` — riferimento architetturale (vincoli, soglie, servizi, valve) per il sistema dual-GPU.
4. `document-ai/knowledge/ARCHITETTURA_AGENTI_E_MODELLI.md` — decisione architetturale, modelli LLM/ComfyUI candidati con fonti, misure sul campo (sezioni 4b).
5. `docs/VERSIONING.md` — tag, rollback, aggiornamento locale, regole per le modifiche dell AI.
6. `README.md` / `README.it.md` — descrizione completa del progetto (en/it) con sezione GPU, test e consumi.
7. `document-ai/knowledge/ORCHESTRA_3090_MIGRAZIONE.md` — stato dei problemi di migrazione. `docs/archive/ORCHESTRA_HANDOFF_v8.md` — sistema a GPU singola (solo storico).
8. `document-ai/config/orchestra.env.example` — tutte le variabili opzionali.

## 18. Checklist per i primi minuti della nuova sessione
1. Leggere i punti 1-5 della sezione 17; non fidarsi di cifre di questo documento senza riverificarle se il repository e cambiato.
2. `git fetch --all --tags` e controllare: tip di `dual-gpu-final`, tag `dual-gpu-rc7`, stato di `main` (l utente puo aver unito la PR #2 o aver pushato altro), PR #2.
3. Eseguire `bash tests/run_all.sh` (deve essere verde) prima di toccare qualsiasi cosa.
4. **Chiedere all utente:** ha aggiornato `ORCHESTRA_PAT`? ha eseguito le misure P1? ha scelto tra team e pipeline immagini? conferma D23 («qwen 3.8 image»)? ha cambiato qualcosa in locale o su `main`?
5. Lavorare per passi piccoli e verificati; riportare sempre cosa e misurato, simulato o ipotizzato; usare la procedura della sezione 13 per pubblicare.
