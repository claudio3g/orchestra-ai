# AI Bootstrap — Orchestra AI

> **Ultimo aggiornamento:** 2026-10-02T17:16:29Z
> **Branch:** `main`
> **Repo:** claudio3g/orchestra-ai

---

## ⚠️ REGOLE FONDAMENTALI (leggere prima di tutto)

### Anti-hallucination policy

1. **Non inventare MAI** contenuti di file, struttura di directory o nomi di file.
2. **La lista autorevole è nella sezione "File tracciati" qui sotto**. Nessun file esiste al di fuori di quella lista.
3. **Ogni affermazione sul contenuto del repository deve essere supportata da un fetch effettivo**.
4. **LLM NON sanno decodificare base64 in modo affidabile**. NON usare l'API GitHub che ritorna base64. Usa uno dei metodi plain-text della sezione successiva.
5. **Se non riesci a leggere un file, dì esplicitamente "non ho potuto leggere X"**. Mai inventare.

---

## 1. Come accedere ai file (metodi plain-text)

### Metodo 1 — jsDelivr CDN (consigliato, plain text)

    https://cdn.jsdelivr.net/gh/claudio3g/orchestra-ai@main/<path>

Esempio:

    https://cdn.jsdelivr.net/gh/claudio3g/orchestra-ai@main/README.it.md

### Metodo 2 — Jina Reader (plain text, converte qualsiasi URL)

    https://r.jina.ai/https://raw.githubusercontent.com/claudio3g/orchestra-ai/main/<path>

### Metodo 3 — GitHub API (RESTITUISCE BASE64, sconsigliato)

    https://api.github.com/repos/claudio3g/orchestra-ai/contents/<path>

Il campo `content` è base64. Da usare solo se gli altri metodi falliscono e SOLO con decodifica programmatica (non manuale).

---

## 2. File tracciati (GROUND TRUTH)

**Nessun file esiste al di fuori di questa lista. Se un file non è qui, NON ESISTE.**

| Path | Byte |
|------|------|
| `.github/workflows/ai-commit.yml` | 8448 |
| `.gitignore` | 1446 |
| `AI_BOOTSTRAP.md` | 8048 |
| `AI_CTX_config.md` | 7568 |
| `AI_CTX_core.md` | 84605 |
| `AI_CTX_knowledge_index.md` | 662 |
| `AI_CTX_pipelines.md` | 210241 |
| `AI_CTX_rag.md` | 116131 |
| `AI_CTX_scripts.md` | 75468 |
| `AI_MANIFEST.md` | 6378 |
| `AI_READ_PROTOCOL.md` | 1110 |
| `README.it.md` | 21134 |
| `README.md` | 26536 |
| `docs/VERSIONING.md` | 4281 |
| `docs/archive/ORCHESTRA_HANDOFF_v8.md` | 24158 |
| `document-ai/AI_WORKFLOW.md` | 1012 |
| `document-ai/config/Modelfile-blender` | 371 |
| `document-ai/config/docker-compose.yml` | 1193 |
| `document-ai/config/docker_daemon.json` | 128 |
| `document-ai/config/orchestra.env.example` | 1963 |
| `document-ai/config/ufw_rules_export.txt` | 1935 |
| `document-ai/config/valves_ai_router.json` | 20 |
| `document-ai/config/valves_image_loop.json` | 2 |
| `document-ai/config/valves_orchestra_manifold.example.json` | 926 |
| `document-ai/config/valves_rag_filter.json` | 296 |
| `document-ai/knowledge/ARCHITETTURA_AGENTI_E_MODELLI.md` | 8796 |
| `document-ai/knowledge/Arduino_Nano3_0.pdf` | 164658 |
| `document-ai/knowledge/Handoff tecnico - backup pCloud da Raspberry Pi V.1.0.docx` | 11944 |
| `document-ai/knowledge/MACRO-AREA-Mansione-Responsabile-Gradopreparazione-Impattoefficienza.xlsx` | 7587 |
| `document-ai/knowledge/ORCHESTRA_3090_MIGRAZIONE.md` | 4240 |
| `document-ai/knowledge/ORCHESTRA_HANDOFF_v9.md` | 17465 |
| `document-ai/knowledge/hardware-report.md` | 82491 |
| `document-ai/knowledge/rasdom1-pi4_v4.0.md` | 12259 |
| `document-ai/routing_snapshots/routing_20260505_191823.json` | 413412 |
| `document-ai/scripts/download_lcm_lora.sh` | 10612 |
| `document-ai/scripts/egpu_check.sh` | 1934 |
| `document-ai/scripts/generate_ai_context.sh` | 3990 |
| `document-ai/scripts/orchestra_bench_models.sh` | 5076 |
| `document-ai/scripts/orchestra_gpu_env.sh` | 7308 |
| `document-ai/scripts/orchestra_install_guide.sh` | 9699 |
| `document-ai/scripts/orchestra_power.sh` | 8658 |
| `document-ai/scripts/orchestra_smoke_test.sh` | 5657 |
| `document-ai/scripts/orchestra_sync.sh` | 7497 |
| `document-ai/scripts/patch_required_models.sh` | 2121 |
| `document-ai/scripts/pattern_logger.py` | 828 |
| `document-ai/scripts/setup_security.sh` | 11133 |
| `logs/patterns.jsonl` | 22372 |
| `ollama/Modelfile-blender` | 371 |
| `ollama/Modelfile-orchestra` | 1537 |
| `ollama/docker-compose.yml` | 1193 |
| `ollama/pipelines/embedding_utils.py` | 19415 |
| `ollama/pipelines/embedding_utils/valves.json` | 2 |
| `ollama/pipelines/github_tools/valves.json` | 2 |
| `ollama/pipelines/image_loop.py` | 45219 |
| `ollama/pipelines/image_loop/valves.json` | 2 |
| `ollama/pipelines/orchestra_bootstrap.py` | 6486 |
| `ollama/pipelines/orchestra_bootstrap/valves.json` | 375 |
| `ollama/pipelines/orchestra_evolver.py` | 42452 |
| `ollama/pipelines/orchestra_evolver/valves.json` | 2 |
| `ollama/pipelines/orchestra_manifold.py` | 72912 |
| `ollama/pipelines/orchestra_manifold/valves.json` | 2 |
| `ollama/pipelines/pattern_logger.py` | 1276 |
| `ollama/pipelines/pattern_logger/valves.json` | 2 |
| `ollama/pipelines/rag_filter.py` | 17384 |
| `ollama/pipelines/rag_filter/valves.json` | 193 |
| `ollama/pipelines/requirements.txt` | 38 |
| `rag/Dockerfile` | 691 |
| `rag/docker-compose.prod.yml` | 996 |
| `rag/patch_async_index.py` | 8204 |
| `rag/patch_endpoints.py` | 2505 |
| `rag/patch_job_index.py` | 7869 |
| `rag/pattern_logger.py` | 828 |
| `rag/rag_indexer_lib.py` | 33768 |
| `rag/rag_patch_2.py` | 1389 |
| `rag/rag_patch_3.py` | 4072 |
| `rag/rag_service.py` | 55047 |
| `rag/requirements.txt` | 102 |
| `start_ai_stack.sh` | 25664 |
| `start_comfyui.sh` | 1303 |
| `tests/helpers/stubs/curl` | 2372 |
| `tests/helpers/stubs/docker` | 2301 |
| `tests/helpers/stubs/mount` | 19 |
| `tests/helpers/stubs/mountpoint` | 19 |
| `tests/helpers/stubs/nvidia-smi` | 2556 |
| `tests/helpers/stubs/openssl` | 82 |
| `tests/helpers/stubs/pgrep` | 19 |
| `tests/helpers/stubs/pkill` | 19 |
| `tests/helpers/stubs/python` | 399 |
| `tests/helpers/stubs/sleep` | 19 |
| `tests/helpers/stubs/ss` | 19 |
| `tests/helpers/stubs/sudo` | 19 |
| `tests/helpers/stubs/systemctl` | 19 |
| `tests/helpers/stubs/zramctl` | 30 |
| `tests/helpers/stubs_power/sudo` | 47 |
| `tests/helpers/tiny.py` | 441 |
| `tests/run_all.sh` | 1876 |
| `tests/test_bench.sh` | 3639 |
| `tests/test_bootstrap.py` | 3413 |
| `tests/test_image_loop.py` | 11252 |
| `tests/test_launcher.sh` | 13927 |
| `tests/test_manifold.py` | 7686 |
| `tests/test_power.sh` | 4300 |
| `tests/test_smoke.sh` | 3890 |
| `tests/test_sync.sh` | 6322 |
| `tests/test_vram_daemon.py` | 1748 |
| `tests/test_vram_endpoint.py` | 2811 |
| `workflows/sd15.json` | 1488 |
| `workflows/sdxl_base.json` | 1502 |

## 3. Percorsi reali del progetto

| Cosa | Percorso |
|------|----------|
| Repository locale | /home/claudio/ai-sessioni |
| Remote Git | git@github.com:claudio3g/orchestra-ai.git |
| Branch principale | main |
| Branch secondario | dual-gpu-step1 |
| Workflow AI | .github/workflows/ai-commit.yml |
| Script dispatch | ~/ai-dispatch.sh |
| Token GitHub | ~/.orchestra_github_token |
| RAG service | rag/rag_service.py (porta 6335) |

---

## 4. Workflow AI — commit automatico

1. L'AI genera una patch in formato diff
2. La patch viene inviata via `curl` a GitHub API
3. Il workflow `.github/workflows/ai-commit.yml` si attiva:
   - `validate`: verifica payload
   - `sandbox-test`: applica la patch ed esegue 5 test
   - `commit-push`: se i test passano, esegue questo script, committa, pusha

Trigger manuale:

    ~/ai-dispatch.sh <patch.diff> "<messaggio>" [branch]

---

## 5. Cose da NON fare

- ❌ `git reset --hard` senza backup
- ❌ `git push --force` (usare `--force-with-lease`)
- ❌ committare: token, segreti, `rag/.file_hash_cache.json`
- ❌ esporre i servizi pubblicamente
- ❌ **usare l'API GitHub per leggere file** (base64 non decodificabile)
- ❌ **inventare file non presenti nella sezione 2**

---

## 6. Contesto hardware

- CPU: Intel i9 (nix-i9)
- GPU: RTX 3090 (24 GB) + RTX 4060 Laptop (8 GB)
- OS: Ubuntu 24.04
- Runtime AI: Ollama + Open WebUI + Qdrant + RAG custom
- Dual-GPU: 3090 = ruolo `main` (modelli pesanti, SDXL), 4060 = ruolo `aux` (coordinator, vision); due istanze Ollama (porte 11435 e 11436). Dettagli: `document-ai/knowledge/ORCHESTRA_HANDOFF_v9.md`

