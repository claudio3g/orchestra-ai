# Architettura degli agenti e scelta dei modelli (decisione di ottobre 2026)

Stato: **decisione di progetto + raccomandazioni**, basate su fonti pubbliche consultate il 4 ottobre 2026 e su
misure da fare sulla macchina. Le cifre di VRAM e le prestazioni citate sono dichiarazioni di terzi o stime:
vanno verificate con `orchestra_bench_models.sh` e `orchestra_smoke_test.sh --load` prima di adottarle.

## 1. Decisione

**Un agente forte sulla RTX 3090, uno strato di agenti piccoli sempre disponibile sulla RTX 4060, parallelismo
solo dove il compito e' davvero parallelizzabile.**

| GPU | Ruolo | Cosa ospita |
|-----|-------|-------------|
| 3090 (24 GB) | "cervello" principale | un modello di classe 27B (circa 17-18 GB a Q4) con contesto moderato; in "modalita' immagine" il modello di immagini di massima qualita' |
| 4060 (8 GB, circa 7 liberi) | strato sempre attivo | coordinator `llama3.2:3b`, vision, un modello 7-9B come critico/worker, embedding; resta reattivo anche mentre la 3090 genera immagini |

Si scarta come impostazione predefinita lo "sciame" di molti agenti con modelli diversi: peggiora i compiti
sequenziali (la maggior parte di quelli di Orchestra: codice, amministrazione di sistema, sviluppo) e costa
VRAM e banda senza un guadagno dimostrato. Si scarta anche l ipotesi "un solo agente sulla 4060": spreca
l hardware piu' utile per gli agenti sempre attivi.

## 2. Perche' (evidenze)

**Agenti multipli vs agente singolo.** Sintesi di studi riportati da fonti secondarie (Google Research, Anthropic,
MAST, Cemri et al. 2025; i testi originali non sono stati riletti qui):
- a parita' di calcolo, un singolo agente eguaglia o supera spesso il multi-agente;
- il multi-agente aiuta su compiti **parallelizzabili e di ampiezza** (+81% su un benchmark finanziario
  parallelizzabile, +90,2% nel sistema di ricerca di Anthropic) e peggiora i compiti **sequenziali** (fino a -70%);
- costo: circa 15 volte i token, overhead di coordinamento e 14 modalita' di fallimento (propagazione delle
  allucinazioni da un agente al successivo, specifiche ignorate, verifica debole);
- schema che funziona: le **scritture restano a un solo agente**; gli altri contribuiscono con intelligenza
  (ricerca, revisione, controllo), non con azioni.

**Hardware.** La generazione di testo e' limitata dalla banda di memoria: con piu' richieste sullo stesso modello
i pesi si leggono una volta sola e ogni richiesta aggiunge solo la propria cache KV. Per Ollama la memoria della KV
cresce con `num_ctx x OLLAMA_NUM_PARALLEL` (default 1) e alcune architetture (multimodali, ad attenzione
ricorrente) risultano fissate a un solo slot: quindi **piu' agenti sullo stesso modello residente sono economici
solo se il modello scala davvero in parallelo su Ollama**, e va misurato. Due modelli grandi diversi residenti
insieme si dividono tempo e memoria. La 4060 ha memoria e banda proprie: i suoi agenti non rallentano la 3090
(parallelismo hardware reale). Con un 27B (circa 17-18 GB) restano solo circa 5-6 GB sulla 3090: per
`OLLAMA_NUM_PARALLEL` 2-3 serve un contesto moderato e la cache KV q8_0 (gia' attiva nel launcher).

**Immagini e LLM sulla stessa 3090.** I modelli di immagini di massima qualita' (sezione 4) occupano da soli
18-24 GB: non coesistono con un LLM da 17 GB. Si lavora a **divisione di tempo** ("modalita' LLM" / "modalita'
immagine"): `image_loop` scarica gli LLM quando serve (gia' implementato) e la 4060 tiene attivo il sistema nel
frattempo.

## 3. Modelli di linguaggio per la 3090 (stato di ottobre 2026, da fonti di terzi)

| Modello (tag Ollama) | Dimensione Q4 | Note riportate dalle fonti |
|----------------------|---------------|-----------------------------|
| `qwen3.6:27b` | circa 17 GB | indicato come riferimento per 24 GB e per agenti di codice; contesto 262K; Apache 2.0 |
| `qwen3.8:27b` | circa 18 GB | tag piu' recente (meta' agosto 2026), con input di immagini |
| `qwen3-coder:30b` | circa 19 GB | MoE con 3,3 miliardi di parametri attivi: molto piu' veloce in generazione |
| `devstral:24b` | circa 14 GB | modello per agenti di sviluppo |
| `qwen2.5-coder:32b` | circa 20 GB | generazione precedente; resta indicato per il completamento FIM |

Raccomandazione: **provare `qwen3.6:27b` (e `qwen3.8:27b`) come modello pesante** impostando
`ORCHESTRA_HEAVY_MODEL` (il download e' non fatale e viene saltato con meno di 20 GB di VRAM), e **decidere con il
benchmark**. Un modello con input di immagini potrebbe sostituire anche la vision separata (llava), da validare.
I punteggi citati dalle fonti (es. SWE-bench) non sono verificati da noi.

```bash
# velocita' di un flusso e throughput aggregato con 1, 2, 3 richieste simultanee sulla 3090
ORCHESTRA_MAIN_PARALLEL=3 bash start_ai_stack.sh      # NUM_PARALLEL=3 sul main (poi in un altro terminale:)
bash document-ai/scripts/orchestra_bench_models.sh --parallel "1 2 3" qwen3.6:27b qwen2.5-coder:14b-instruct-q4_K_M
```
Criteri: il modello deve risultare **100% GPU**; se lo speedup a N=2 e' almeno circa 1,5x e la VRAM regge, ha senso
abilitare piu' agenti sullo stesso modello; se resta circa 1,0x il modello non scala in parallelo su Ollama e
conviene un singolo flusso (o un altro server come llama.cpp/vLLM).

## 4. Modelli ComfyUI per la 3090 (risposta alla domanda sul massimo risultato)

Fonti principali: guida "Best ComfyUI Models 2026" (verificata il 1 ottobre 2026 su model card e documentazione
Comfy), Black Forest Labs (repository FLUX.2) e guide di quantizzazione 2026. Le VRAM sono dichiarazioni dei
produttori o di Comfy, non benchmark nostri.

| Modello | Parametri | Su 24 GB | Licenza | Quando sceglierlo |
|---------|-----------|----------|---------|-------------------|
| **Qwen-Image** / Qwen-Image-2512 (+ Edit-2511) | 20B | fp8 circa 18-24 GB (il test di Comfy ha usato l 86% di una scheda da 24 GB) | Apache 2.0 | **qualita' generale piu' alta**, testo leggibile nelle immagini, editing; uso commerciale consentito |
| **FLUX.2 [dev]** | 32B | solo quantizzato: GGUF Q4_K_S circa 19 GB (dettagli fini piu' morbidi); Q5_K_M circa 24 GB e FP8/Q8 non entrano | non commerciale | **fotorealismo massimo**, anche multi-riferimento; usa un modello Mistral da circa 24B per il prompt: pesante in RAM/VRAM, verificare i requisiti |
| FLUX.2 [klein] 4B | 4B | circa 8-13 GB | Apache 2.0 | bozze e uso commerciale, generazione sotto il secondo |
| Z-Image Turbo | 6B | circa 16 GB | Apache 2.0 | velocita' e volume (8 passi) |
| FLUX.1 [dev] / Krea | 12B | file originale circa 23 GB | non commerciale | stile FLUX per progetti personali |
| HiDream-I1 | 17B | fp8 oltre 16 GB | MIT | alternativa ad alta qualita' |
| SDXL e fine-tune | 3,5B | circa 8 GB | Open RAIL++-M | ecosistema LoRA/ControlNet piu' ampio, anime |
| Qwen-Image-2.1 | 7B | n/d | licenza di ricerca (non commerciale) | uscito a settembre 2026 |
| Wan 2.2 (video) | 14B / 5B | 14B in fp8 su 24 GB; 5B con offloading | Apache 2.0 | video locale |

**Per il massimo risultato sulla 3090:** Qwen-Image (qualita' complessiva e testo, licenza aperta) oppure FLUX.2 [dev]
Q4 (fotorealismo, solo uso non commerciale). Come motore veloce restano Z-Image Turbo o FLUX.2 [klein] 4B.

**Note specifiche per la 3090 (architettura Ampere):**
- l **FP8 qui serve solo a risparmiare memoria**: i pesi vengono riconvertiti in software prima del calcolo, quindi
  non c e' guadagno di velocita' (in una misura riportata e' stato persino piu' lento del bf16);
- per la **qualita'** preferire GGUF Q8/Q6 dove il modello entra; per la **velocita'** l INT8 usa unita' native
  (nodo ComfyUI-INT8-Fast), con qualita' prossima a Q8;
- l NVFP4 richiede architettura Blackwell: non applicabile alla 3090;
- il caricamento dei checkpoint (20-40 GB) dal disco passa dal link Thunderbolt: la prima generazione dopo il
  cambio di modello e' lenta, poi il modello resta in VRAM.

## 4b. Misure sul campo (5 ottobre 2026, prime misure sulla macchina reale)

- **Smoke test `--load`:** 23 controlli su 24. Isolamento verificato: il coordinator sulla 4060 la porta da 678 a 3248 MiB
  senza che la 3090 cresca; il modello quality sulla 3090 da 279 a 15600 MiB senza che la 4060 cresca.
- **`qwen3.8:27b`:** il download e fallito con `412: requires a newer version of Ollama`. L immagine Ollama presente in
  locale era troppo vecchia: serve `ORCHESTRA_PULL_IMAGES=1` (aggiorna l immagine e ricrea i container Ollama).
- **`qwen2.5-coder:14b-instruct-q4_K_M` con `OLLAMA_NUM_PARALLEL=3`:** 17,2 tok/s per un flusso; totale 15,7 / 24,7 / 29,1
  tok/s con 1 / 2 / 3 richieste (speedup 1,00x / 1,57x / 1,85x), ma con **15%/85% CPU/GPU** e 15598 MiB di VRAM usati.
  **Misure non rappresentative:** un 14B Q4 pesa circa 9 GB e dovrebbe stare tutto in GPU. Causa probabile: la richiesta
  non indicava `num_ctx`, quindi Ollama usava il suo contesto predefinito; con 3 richieste parallele la cache KV si
  alloca per contesto x 3 e puo spostare layer su CPU. Il manifold usa 8192. Correzione (rc5): `OLLAMA_CONTEXT_LENGTH=8192`
  nel launcher e `num_ctx` esplicito nel benchmark e nello smoke test. **Da rimisurare.**
- Lo scaling con le richieste parallele e promettente (1,85x con 3 flussi pur con offload parziale): conferma che piu
  richieste sullo stesso modello caricato costano meno di modelli diversi. Va confermato con il modello tutto in GPU.

## 5. Prossimo incremento proposto (non ancora implementato)

**Pipeline a due stadi, per usare entrambe le GPU e far girare la 3090 solo dove serve:**
1. *Bozze* su un secondo ComfyUI sulla 4060 (SDXL + LCM, gia' in uso) con giudizio visivo e raffinamento del prompt;
2. *Render finale* sul ComfyUI della 3090 con il modello di massima qualita', preceduto da **un solo** scarico dell LLM
   per immagine (oggi, con ComfyUI sulla 3090, l LLM viene scaricato al primo `/generate` e ricaricato dopo).

Richiede: la scelta del modello finale (sezione 4), i workflow JSON dedicati e la verifica dei requisiti reali
(modello del prompt, RAM) sulla macchina. Va costruito e provato un passo alla volta.

## 6. Valori configurabili

`ORCHESTRA_MAIN_PARALLEL`, `ORCHESTRA_AUX_PARALLEL` (default 1), `ORCHESTRA_HEAVY_MODEL`, `ORCHESTRA_COMFY_ROLE`,
`ORCHESTRA_AUX_OLLAMA`: vedi `document-ai/config/orchestra.env.example`. Versioni e rollback: `docs/VERSIONING.md`.
