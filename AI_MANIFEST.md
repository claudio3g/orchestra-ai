# AI Manifest — Orchestra AI

Inventario autorevole dei file tracciati nel repository.

> **REGOLA**: nessun file esiste al di fuori di questa lista. Non inventare path, non assumere file non elencati.

> Generato: 2026-10-02T19:15:42Z
> Branch: `main`
> Rigenerazione: `document-ai/scripts/generate_ai_context.sh`

## Accesso ai file

Per leggere un file:

    https://api.github.com/repos/claudio3g/orchestra-ai/contents/<path>

Il campo `content` è base64. Decodifica:

    curl -s "<url>" | python3 -c "import sys,json,base64; print(base64.b64decode(json.load(sys.stdin)[\"content\"]).decode())"

## File tracciati

| Path | Byte | SHA breve |
|------|------|-----------|
| `.github/workflows/ai-commit.yml` | 8448 | `13dbe34c` |
| `.gitignore` | 1321 | `a0ef57bd` |
| `AI_BOOTSTRAP.md` | 6105 | `8815383c` |
| `AI_CONTEXT.md` | 43691 | `b39edbb2` |
| `AI_MANIFEST.md` | 857 | `bd3f4a3c` |
| `README.it.md` | 14171 | `dbeb0085` |
| `README.md` | 20043 | `4b2f16d0` |
| `document-ai/AI_WORKFLOW.md` | 1012 | `1627b54f` |
| `document-ai/config/Modelfile-blender` | 371 | `c24cb7f0` |
| `document-ai/config/docker-compose.yml` | 791 | `a63d941f` |
| `document-ai/config/docker_daemon.json` | 128 | `3c40a987` |
| `document-ai/config/ufw_rules_export.txt` | 1935 | `28daee6d` |
| `document-ai/config/valves_ai_router.json` | 20 | `7a0b0871` |
| `document-ai/config/valves_image_loop.json` | 2 | `9e26dfee` |
| `document-ai/config/valves_orchestra_manifold.example.json` | 699 | `9cbeb73c` |
| `document-ai/config/valves_rag_filter.json` | 296 | `81493724` |
| `document-ai/knowledge/Arduino_Nano3_0.pdf` | 164658 | `1a8a5ffb` |
| `document-ai/knowledge/Handoff tecnico - backup pCloud da Raspberry Pi V.1.0.docx` | 11944 | `ebf08c8e` |
| `document-ai/knowledge/MACRO-AREA-Mansione-Responsabile-Gradopreparazione-Impattoefficienza.xlsx` | 7587 | `d9410164` |
| `document-ai/knowledge/ORCHESTRA_3090_MIGRAZIONE.md` | 4710 | `76524954` |
| `document-ai/knowledge/ORCHESTRA_HANDOFF_v8.md` | 23720 | `fad1f0d9` |
| `document-ai/knowledge/hardware-report.md` | 80481 | `82bd7db2` |
| `document-ai/knowledge/rasdom1-pi4_v4.0.md` | 12259 | `164e7dce` |
| `document-ai/routing_snapshots/routing_20260505_191823.json` | 413412 | `310b7f88` |
| `document-ai/scripts/download_lcm_lora.sh` | 10616 | `656fe74c` |
| `document-ai/scripts/egpu_check.sh` | 1934 | `86c0c3db` |
| `document-ai/scripts/generate_ai_context.sh` | 2148 | `c5c34934` |
| `document-ai/scripts/orchestra_install_guide.sh` | 9699 | `96bb2bcf` |
| `document-ai/scripts/patch_required_models.sh` | 2121 | `cf640c88` |
| `document-ai/scripts/pattern_logger.py` | 828 | `0679d783` |
| `document-ai/scripts/setup_security.sh` | 11141 | `588fa00e` |
| `logs/patterns.jsonl` | 22372 | `34b23ff6` |
| `ollama/Modelfile-blender` | 371 | `c24cb7f0` |
| `ollama/docker-compose.yml` | 791 | `a63d941f` |
| `ollama/pipelines/embedding_utils.py` | 19415 | `10fecb60` |
| `ollama/pipelines/embedding_utils/valves.json` | 2 | `9e26dfee` |
| `ollama/pipelines/github_tools/valves.json` | 2 | `9e26dfee` |
| `ollama/pipelines/image_loop.py` | 31942 | `1a887a97` |
| `ollama/pipelines/image_loop/valves.json` | 2 | `9e26dfee` |
| `ollama/pipelines/orchestra_evolver.py` | 42460 | `c1a0248e` |
| `ollama/pipelines/orchestra_evolver/valves.json` | 2 | `9e26dfee` |
| `ollama/pipelines/orchestra_manifold.py` | 66003 | `ad644915` |
| `ollama/pipelines/orchestra_manifold/valves.json` | 2 | `9e26dfee` |
| `ollama/pipelines/pattern_logger.py` | 1280 | `8bc3f419` |
| `ollama/pipelines/pattern_logger/valves.json` | 2 | `9e26dfee` |
| `ollama/pipelines/rag_filter.py` | 17388 | `223c1e9e` |
| `ollama/pipelines/rag_filter/valves.json` | 193 | `23fe682c` |
| `ollama/pipelines/requirements.txt` | 38 | `447a42af` |
| `rag/Dockerfile` | 691 | `65e61da8` |
| `rag/docker-compose.prod.yml` | 996 | `c4b9ec58` |
| `rag/patch_async_index.py` | 8204 | `870b4e5d` |
| `rag/patch_endpoints.py` | 2505 | `6ffde7b8` |
| `rag/patch_job_index.py` | 7869 | `c01ae137` |
| `rag/pattern_logger.py` | 828 | `0679d783` |
| `rag/rag_indexer_lib.py` | 33772 | `3c7941f2` |
| `rag/rag_patch_2.py` | 1389 | `d5da4345` |
| `rag/rag_patch_3.py` | 4072 | `8ec44752` |
| `rag/rag_service.py` | 55047 | `b77639c5` |
| `rag/requirements.txt` | 102 | `fef8a2b2` |
| `start_ai_stack.sh` | 13391 | `30dfd8a0` |
| `start_comfyui.sh` | 266 | `63db9110` |
| `workflows/sd15.json` | 1488 | `4b949955` |
| `workflows/sdxl_base.json` | 1502 | `023641d0` |

## Totale

- File tracciati: 63
