# Orchestra AI

> 🇮🇹 [Versione italiana](README.it.md)

**Orchestra** is a self-hosted, local-first AI workspace built around **Ollama, Open WebUI, custom Pipelines, RAG and Qdrant**.

The project is designed to run AI services locally, keep the user's knowledge base under local control, and combine general-purpose LLMs, document retrieval, custom processing pipelines and persistent vector storage in a single environment.

> **Status:** active personal/local project  
> **Primary target:** Linux + NVIDIA GPU + Docker  
> **Repository:** https://github.com/claudio3g/orchestra-ai

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
- `llava:7b`
- `moondream:v2`

Models are stored in a persistent Docker volume so they do not need to be downloaded on every startup.

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
bash tests/run_all.sh                                       # 303 simulated checks (no GPU, Docker or network touched)
bash document-ai/scripts/orchestra_bench_models.sh --parallel "1 2 3" MODEL   # tokens/s per stream and aggregate with N concurrent requests, VRAM, 100% GPU
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
- It is pinned to the GPU of `ORCHESTRA_COMFY_ROLE` with `CUDA_VISIBLE_DEVICES=<UUID>`. On the 3090 the VAE stays on GPU; on small GPUs the historical flags (`--normalvram`/`--lowvram` with `--cpu-vae`) are kept.
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
| `main` | main stable line | active |
| `dual-gpu-final` | dual-GPU: roles, aux Ollama, per-role routing, ComfyUI pinning, power, tests | in review |

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

This section is meant to be read by an AI agent that must operate on the repository without human context.

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
