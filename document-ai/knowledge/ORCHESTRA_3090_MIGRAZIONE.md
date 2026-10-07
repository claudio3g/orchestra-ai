# Orchestra dual-GPU — RTX 3090 (eGPU AOOSTAR AG02, TB4) + RTX 4060 (interna), sistema multi-agente

Stato: tutti i passi implementati e testati in simulazione (332 controlli); validazione sull'hardware in corso (vedi sezione 4).
Le due GPU NON sono alternative: lavorano insieme, con ruoli diversi. Variabili: `ORCHESTRA_GPU_MAIN` (3090), `ORCHESTRA_GPU_AUX` (4060), valori = UUID da `nvidia-smi -L`.

## 1. Stato iniziale (storico, commit 10fdc0e)
- Stack: Ollama + OpenWebUI (compose), Pipelines (manifold, rag_filter, image_loop, evolver), rag_service su host :6335, ComfyUI su host :8188, Qdrant.
- Ogni scelta di modello dipende dalla VRAM LIBERA letta da `rag_service /vram` → daemon `embedding_utils` → soglie in manifold/image_loop. Oggi esiste UN solo numero di VRAM e UN solo Ollama (`ollama_url`).
- Deriva doc/codice: rag_service v1.5.0 (handoff: v1.3.1), embedding_utils v2.0.2 (handoff: v2.0.0), `max_context_chars` 7500/3000 (handoff: 12000), `rag_timeout_s` esempio 300 (handoff: 600).
- Non versionati: Qdrant, container Pipelines, Caddy, `start_ai_stack.sh`.

## 2. Architettura (implementata)
Due istanze Ollama, ciascuna fissata a UNA GPU (`device_ids` = UUID). Motivo: una sola istanza con entrambe le GPU spezzerebbe i modelli a layer tra le due, con la banda TB4 (~PCIe x4) come collo di bottiglia; istanze separate = agenti realmente paralleli e prevedibili.

| Ruolo | GPU | Ollama | Cosa ospita (stime VRAM dall'handoff) |
|-------|-----|--------|---------------------------------------|
| main | 3090 24 GB | :11435 | agenti specialisti (qwen3.5:9b ~6.6), quality (14B Q4 ~9), refine, SDXL ComfyUI (~6.2) |
| aux | 4060 8 GB | :11436 | coordinator llama3.2:3b (~2, sempre caricato) + vision (moondream ~1.7 / llava:7b ~4.7) |

Conseguenze: coordinator e routing non aspettano più i modelli grandi; vision gira su aux mentre main genera; la regola storica "1 solo LLM alla volta" diventa "per GPU" e su main cade quasi del tutto. Alternativa B: aux = solo ComfyUI/SDXL, tutti gli LLM su main (più semplice, 4060 meno sfruttata).

## 3. Problemi trovati e stato (aggiornato a ottobre 2026)
| # | Gravità | Problema | Stato |
|---|---------|----------|-------|
| A | CRITICO | Con 2 GPU `/vram` andava in ValueError → sempre 2000 MB → sempre `llama3.2:3b` | RISOLTO (passo 1, validato su hardware) |
| B | Alto | `count: 1`/`--gpus all` non fissava la GPU di Ollama | RISOLTO: una istanza per GPU con `--gpus device=<UUID>`, ricreata se il container era vecchio |
| C | Alto | Il manifold aveva un solo `ollama_url` | RISOLTO: backend per ruolo (`ollama_url_aux`, `aux_models`) con failover sul main |
| D | Alto | ComfyUI non fissato a una GPU | RISOLTO: `CUDA_VISIBLE_DEVICES=<UUID>` + `ORCHESTRA_COMFY_ROLE` (launcher e `start_comfyui.sh`) |
| E | Medio | Soglie/keep-alive per 8 GB; quality con `num_gpu=20` anche sulla 3090 | RISOLTO: quality in GPU con VRAM ≥ 11000 MB, keep-alive per ruolo, flash-attention + KV q8_0 |
| F | Medio | Prompt e report dicevano "RTX 4060 8GB" | RISOLTO: prompt agenti, banner, addendum in `hardware-report.md`, handoff v9 |
| G | Basso | Fallback `subprocess` con `int(out.strip())` | RISOLTO (manifold e image_loop) |
| H | Medio | Il controllo modelli confrontava solo il nome prima dei `:` (il 32b non veniva scaricato) | RISOLTO: confronto esatto nome:tag |
| I | Medio | `keep_alive` in `options` di image_loop (ignorato da Ollama) | RISOLTO: parametro di primo livello |
| J | Medio | `orchestra_bootstrap` non era un filtro e iniettava ≈ 11 KB per messaggio | RISOLTO: v1.1.0, contesto compatto ≈ 2.9 KB |

## 4. Validazione
Simulazione: `bash tests/run_all.sh` (332 controlli). Hardware: `bash document-ai/scripts/orchestra_smoke_test.sh --load`
e le verifiche elencate nella sezione 13 dell'handoff v9. Consumi: `orchestra_power.sh bench`.

## 5. Rischi specifici (invariati)
- eGPU su TB4: caricamento modelli più lento, inferenza quasi invariata se il modello sta tutto in VRAM.
- Scollegare la eGPU con servizi attivi può bloccare driver/Docker: fermare prima lo stack. Usare gli UUID.
- Alimentazione/temperatura: monitorare con `orchestra_power.sh status` e `log`.
- Con la eGPU assente il sistema degrada (4060 = main) con le soglie da 8 GB.
