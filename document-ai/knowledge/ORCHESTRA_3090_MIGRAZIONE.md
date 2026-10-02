# Orchestra dual-GPU — RTX 3090 (eGPU AOOSTAR AG02, TB4) + RTX 4060 (interna), sistema multi-agente

Stato: PASSO 1 pronto e testato in simulazione · PASSI 2-7 uno alla volta, ognuno con smoke test.
Le due GPU NON sono alternative: lavorano insieme, con ruoli diversi. Variabili: `ORCHESTRA_GPU_MAIN` (3090), `ORCHESTRA_GPU_AUX` (4060), valori = UUID da `nvidia-smi -L`.

## 1. Stato rilevato nel repo (commit 10fdc0e)
- Stack: Ollama + OpenWebUI (compose), Pipelines (manifold, rag_filter, image_loop, evolver), rag_service su host :6335, ComfyUI su host :8188, Qdrant.
- Ogni scelta di modello dipende dalla VRAM LIBERA letta da `rag_service /vram` → daemon `embedding_utils` → soglie in manifold/image_loop. Oggi esiste UN solo numero di VRAM e UN solo Ollama (`ollama_url`).
- Deriva doc/codice: rag_service v1.5.0 (handoff: v1.3.1), embedding_utils v2.0.2 (handoff: v2.0.0), `max_context_chars` 7500/3000 (handoff: 12000), `rag_timeout_s` esempio 300 (handoff: 600).
- Non versionati: Qdrant, container Pipelines, Caddy, `start_ai_stack.sh`.

## 2. Architettura target (da confermare: allocazione ruoli)
Due istanze Ollama, ciascuna fissata a UNA GPU (`device_ids` = UUID). Motivo: una sola istanza con entrambe le GPU spezzerebbe i modelli a layer tra le due, con la banda TB4 (~PCIe x4) come collo di bottiglia; istanze separate = agenti realmente paralleli e prevedibili.

| Ruolo | GPU | Ollama | Cosa ospita (stime VRAM dall'handoff) |
|-------|-----|--------|---------------------------------------|
| main | 3090 24 GB | :11435 | agenti specialisti (qwen3.5:9b ~6.6), quality (14B Q4 ~9), refine, SDXL ComfyUI (~6.2) |
| aux | 4060 8 GB | :11436 | coordinator llama3.2:3b (~2, sempre caricato) + vision (moondream ~1.7 / llava:7b ~4.7) |

Conseguenze: coordinator e routing non aspettano più i modelli grandi; vision gira su aux mentre main genera; la regola storica "1 solo LLM alla volta" diventa "per GPU" e su main cade quasi del tutto. Alternativa B: aux = solo ComfyUI/SDXL, tutti gli LLM su main (più semplice, 4060 meno sfruttata).

## 3. Problemi trovati
| # | Gravità | Problema | Passo |
|---|---------|----------|-------|
| A | CRITICO | Con 2 GPU `/vram` va in ValueError → sempre 2000 MB → sempre llama3.2:3b | **1 (fatto)** |
| B | Alto | Il compose Ollama ha `count: 1`: non sceglie né la 3090 né la 4060 | 2 |
| C | Alto | Il manifold ha un solo `ollama_url`: serve instradare per ruolo (main/aux) | 3 |
| D | Alto | ComfyUI va fissato a una GPU (`CUDA_DEVICE_ORDER=PCI_BUS_ID`, `--cuda-device N`) | 5 |
| E | Medio | Soglie VRAM/keep_alive ragionano su 8 GB e su un solo numero: vanno separate per ruolo | 4 |
| F | Medio | Prompt agenti e `hardware-report.md` dicono "RTX 4060 8GB" → il RAG risponde col vecchio hardware | 6 |
| G | Basso | Fallback subprocess in manifold/image_loop con `int(out.strip())` (si attivano solo se embedding_utils non importa) | 4 |

## 4. Piano (un cambiamento alla volta)
0. `bash document-ai/scripts/egpu_check.sh` → conferma 2 GPU visibili, copia i due UUID, link PCIe eGPU ~Gen3 x4.
1. **Monitor VRAM multi-GPU** (questo commit): `/vram` espone `gpus[]` con ruolo main/aux; i campi storici = main. Daemon: `get_gpu_free_mb("main"|"aux")`, `get_gpu_snapshot()`. Compatibile con vecchio rag_service. Smoke: `curl -s localhost:6335/vram | python3 -m json.tool` → due GPU con ruoli giusti; con eGPU scollegata degrada a 4060 come "main".
2. Compose: due istanze Ollama con `device_ids` (UUID) e porte distinte. Smoke: `docker exec <istanza> nvidia-smi -L` vede una sola GPU; `ollama ps` → 100% GPU.
3. Manifold: valves `ollama_url_main` / `ollama_url_aux` + scelta del backend per agente (default = comportamento attuale su main). Smoke: coordinator risponde da aux, specialista da main (log `[ORCHESTRA]`).
4. Soglie e keep_alive per ruolo (valves) + fallback subprocess. Valori da validare in test.
5. ComfyUI sulla GPU scelta e adattamento di `image_loop` (niente più `free_comfyui_vram`/attese VRAM se SDXL e LLM sono su GPU diverse). Smoke: `/generate` completo.
6. Aggiornare `hardware-report.md` e prompt agenti, poi re-index RAG.
7. Handoff v9.

## 5. Rischi specifici
- eGPU su TB4: caricamento modelli più lento, inferenza quasi invariata se il modello sta tutto in VRAM (evitare offload su RAM). Mantenere i modelli caricati (`keep_alive` alto su main).
- Scollegare la eGPU con servizi attivi può bloccare driver/Docker: fermare prima gli stack. L'indice GPU può cambiare → usare UUID.
- Alimentazione/temperatura: monitorare `power.draw` e `temperature.gpu` nello smoke test.
- Con la eGPU assente il sistema degrada (4060 = main) ma le soglie 24 GB non valgono: i passi 3-4 devono prevedere questo caso.
