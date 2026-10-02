# Orchestra AI

**Orchestra** is a self-hosted, local-first, multi-agent AI workspace built around **Ollama, Open WebUI, custom Pipelines, a RAG service, Qdrant and ComfyUI**, running on a laptop with **two NVIDIA GPUs** (an RTX 4060 internal + an RTX 3090 in a Thunderbolt 4 eGPU enclosure).

Models, documents, embeddings and vector data stay on the local machine. A set of specialised agents is selected per request, answers can be grounded in the user's own documentation (RAG), and images can be generated and critiqued in a loop (SDXL + vision model).

> **Status:** active personal/local project  
> **Target:** Linux + NVIDIA GPU(s) + Docker (the project documentation references Ubuntu 24.04)  
> **Repository:** https://github.com/claudio3g/orchestra-ai  
> **Dual-GPU migration:** in progress, one validated step at a time — see [Multi-GPU architecture](#multi-gpu-architecture)

---

## Table of contents

1. [Architecture overview](#architecture-overview)
2. [Components](#components)
3. [Multi-agent orchestration](#multi-agent-orchestration)
4. [RAG and knowledge base](#rag-and-knowledge-base)
5. [Image generation (ComfyUI)](#image-generation-comfyui)
6. [Multi-GPU architecture](#multi-gpu-architecture)
7. [Startup sequence](#startup-sequence)
8. [Networking and security](#networking-and-security)
9. [Repository layout](#repository-layout)
10. [Requirements](#requirements)
11. [Running and verifying](#running-and-verifying)
12. [Troubleshooting](#troubleshooting)
13. [Known limitations and repository hygiene](#known-limitations-and-repository-hygiene)
14. [Roadmap](#roadmap)
15. [Documentation index and contributing](#documentation-index-and-contributing)
16. [License](#license)

---

## Architecture overview

```text
                              ┌────────────────────┐
                              │     Open WebUI      │  :3001 (LAN IP, behind ufw/Caddy)
                              └─────────┬──────────┘
                                        │ OpenAI-compatible API + API key
                              ┌─────────▼──────────┐
                              │      Pipelines      │  :9099
                              │  manifold · filter  │
                              │  image_loop · evolver
                              └──┬─────┬──────┬────┘
              ┌──────────────────┘     │      └───────────────────┐
              │                        │                          │
   ┌──────────▼─────────┐   ┌──────────▼──────────┐   ┌───────────▼───────────┐
   │       Ollama        │   │    RAG Service       │   │       ComfyUI          │
   │ local LLM / vision  │   │  (host process)      │   │ (host process) SDXL    │
   │  :11435 → 11434     │   │  :6335               │   │  :8188                 │
   └──────────┬─────────┘   │  /search /index      │   └────────────────────────┘
              │             │  /vram  /health      │
   RTX 3090 (main)          └──────────┬──────────┘
   RTX 4060 (aux, planned)             │
                                  ┌────▼─────┐
                                  │  Qdrant   │  :6333
                                  │ `orchestra` (RAG) · `orchestra_routing`
                                  └──────────┘
```

Design choices worth knowing:

- **Ollama, Qdrant, Pipelines and Open WebUI run in Docker** (network `ollama_default`). **`rag_service.py` and ComfyUI run directly on the host**, because they need the host's NVIDIA tooling and Python environment.
- The Pipelines container cannot rely on the NVIDIA CLI by itself, so **VRAM is measured on the host by `rag_service` and served over HTTP at `/vram`** (see [VRAM monitoring](#vram-monitoring)). Model selection is adaptive: the manifold chooses models according to the VRAM and RAM that are actually free.
- Everything is started by one script, `start_ai_stack.sh`, which creates containers with `docker run` (the Compose files in the repository are **not** used by the launcher).

---

## Components

| Component | Runs as | Port | Bind / exposure | Role |
|---|---|---|---|---|
| Ollama | Docker `ai-ollama-session` | 11435 → 11434 | `127.0.0.1` | Local LLM and vision runtime. Models in volume `ollama-session` |
| Open WebUI | Docker `ai-webui-session` | 3001 → 8080 | LAN IP `192.168.1.51` (hard-coded in the launcher) | User interface; talks to Pipelines as an OpenAI-compatible backend |
| Pipelines | Docker `ai-pipelines-session` | 9099 | `127.0.0.1` | Manifold (agents), RAG filter, image loop, evolver |
| Qdrant | Docker `ai-qdrant-session` | 6333 | `127.0.0.1` | Vector store (volume `qdrant-data`) |
| RAG service | Host Python process (Flask) | 6335 | `0.0.0.0`, restricted by `ufw` | Indexing, semantic search, `/vram`, deploy hook |
| ComfyUI | Host Python process | 8188 | `0.0.0.0`, restricted by `ufw` | Diffusion backend (SDXL + LCM-LoRA) |

Ollama runtime settings applied by the launcher: `OLLAMA_KEEP_ALIVE=0`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_QUEUE=10`. The manifold overrides `keep_alive` per request for some models (e.g. 300 s for `llama3.1:8b` and `qwen3.5:9b`) to avoid reloading on closely spaced requests.

Models pulled automatically by the launcher (`REQUIRED_MODELS`):

| Model | Used for (default valves) |
|---|---|
| `llama3.2:3b` | coordinator, emergency fallback |
| `qwen3.5:9b` | fast text model |
| `llama3.1:8b` | fallback text model (partial GPU offload when VRAM is low) |
| `qwen2.5-coder:14b-instruct-q4_K_M` | quality / code model |
| `llava:7b` | vision (when enough VRAM is free) |
| `moondream:v2` | lightweight vision fallback |

---

## Multi-agent orchestration

The main pipeline is `ollama/pipelines/orchestra_manifold.py` (v3.8.2). It is exposed to Open WebUI as a model and works in three stages.

**1. Routing.** The user message is embedded with `paraphrase-multilingual-MiniLM-L12-v2` (fastembed) and compared against example phrases per agent, stored in the Qdrant collection `orchestra_routing`. The single nearest example wins if its similarity is above `routing_similarity_threshold` (default 0.45); otherwise — or if the routing collection or embedding model is unavailable — the request goes to the `coordinator`.

| Agent | Handles (from its routing examples) |
|---|---|
| `coordinator` | greetings and general conversation; default when routing is inconclusive |
| `linux_admin` | Linux administration: drivers, system commands, Docker, disks |
| `ml_engineer` | choosing models, quantisation, how much VRAM a model needs |
| `comfy_integrator` | ComfyUI workflows and API, LCM-LoRA usage |
| `design_critic` | analysis and quality assessment of generated images |
| `orchestra_dev` | reading, improving and proposing changes to this codebase (longer generation budget) |
| `reasoner` | algorithmic reasoning, complexity analysis, optimisation |
| `github_integrator` | GitHub issues, repository files, commits |

**2. Model selection.** `select_mode` / `select_text_model` / `select_vision_model` look at free RAM and free VRAM and pick a model and an Ollama `num_gpu` accordingly. The defaults in the code are tuned for **8 GB** of VRAM:

| Valve | Default | Meaning |
|---|---|---|
| `vram_fast_min_mb` | 7000 | enough VRAM for the fast model fully on GPU |
| `vram_fallback_min_mb` | 5500 | fall back to `llama3.1:8b` |
| `vram_partial_fallback_mb` | 3200 | `llama3.1:8b` with reduced `num_gpu` (22) |
| `vram_vision_full_mb` / `vram_vision_partial_mb` | 5000 / 3000 | `llava:7b` full / partial |
| `quality_num_gpu` | 20 | partial layer offload of the 14B quality model |

Below the smallest threshold the manifold selects the emergency model. With the 3090 as the VRAM reference these thresholds are met automatically, but the offload settings (`quality_num_gpu`, `fallback_partial_num_gpu`) still assume a small GPU and are scheduled for review (see [Roadmap](#roadmap)).

**3. Generation.** The response is streamed from Ollama with an explicit `num_predict` (`num_predict_default=2048`, `num_predict_dev=4096`), optional removal of `<think>` blocks, and an agent/session header in the reply.

Slash commands handled by the manifold:

| Command | Effect |
|---|---|
| `/generate <prompt>` | image generation loop (see [Image generation](#image-generation-comfyui)) |
| `/review` | system review: usage statistics from `patterns.jsonl` (messages, `/generate` calls, vision fallbacks, repeated commands), RAG chunk counts per domain, and improvement proposals |
| `/rag index` | admin only: asynchronous re-indexing of `document-ai/` (the manifold starts a job on `/index/async` and polls it) |
| `/evolve <subcommand>` | conservative self-improvement module (`orchestra_evolver.py`) |

`/evolve` is deliberately restrictive: level 0 only proposes; higher levels (admin) may update valves, routing examples in Qdrant, or Python code, always with an automatic backup, `ast.parse` validation, one file per cycle, a 30 % change limit and rollback on failed smoke tests. Subcommands: `status`, `analyze`, `apply-code N`, `rollback`, `update-routing`, `routing-history`, `routing-restore <file>`, `apply-config`.

Events (messages, commands, fallbacks) are appended to `logs/patterns.jsonl` by `pattern_logger.py` and are the input of `/evolve analyze`.

---

## RAG and knowledge base

```text
Documents ──▶ document-ai/ ──▶ RAG service ──▶ embeddings (nomic-embed-text-v1.5)
                                                    │
                                                    ▼
User message ──▶ rag_filter (Pipelines) ──▶ Qdrant `orchestra` ──▶ context injected into the prompt
```

- **Knowledge sources:** `document-ai/knowledge/` (PDF, DOCX, XLSX, Markdown, text). Because this directory is indexed, **documents such as the hardware report and the handoff are retrieved as context** — keep them accurate.
- **Embeddings:** `nomic-ai/nomic-embed-text-v1.5` via fastembed for retrieval; a separate multilingual MiniLM model for agent routing (both managed as lazy singletons in `embedding_utils.py`, with a two-level embedding cache: memory + on-disk shelve).
- **Retrieval filter (`rag_filter.py` v1.6.0):** embeds the query, searches the `orchestra` collection and injects the best chunks. Defaults: `top_k=6`, `min_score=0.45`, `collection_name=orchestra`. For questions about the physical hardware (a curated keyword list), the filter keeps only the chunks relevant to the area asked about (RAM, CPU, GPU, disk, temperature) instead of the generic top-k.
- **Context size:** `max_context_chars` has a default of 12000 in the code, but the valve files in the repository set 7500 (`ollama/pipelines/rag_filter/valves.json`) and 3000 (`document-ai/config/valves_rag_filter.json`). The values saved in the Open WebUI valves panel are the ones that apply at runtime.
- **Automatic indexing:** at startup the launcher indexes the knowledge base when the collection is empty.

RAG service API (`rag/rag_service.py`, port 6335):

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | liveness |
| `/status` | GET | total chunks in the `orchestra` collection and counts per `domain` (503 if Qdrant is unreachable) |
| `/search` | POST | semantic search |
| `/index` | POST | synchronous indexing |
| `/index/async`, `/index/status/<job_id>`, `/index/jobs` | POST / GET | asynchronous indexing jobs |
| `/vram` | GET | free VRAM per GPU, with `main`/`aux` roles (see below) |
| `/deploy` | POST | deploy hook, protected by `DEPLOY_TOKEN` |

`DEPLOY_TOKEN` defaults to `change-me-in-production` and the service prints a warning if it is not changed; the launcher passes the generated Pipelines token.

---

## Image generation (ComfyUI)

ComfyUI is part of the working system, not an optional extra. The `/generate` command runs the loop implemented in `ollama/pipelines/image_loop.py` (v2.7.0):

1. A **draft** is generated through the ComfyUI HTTP API with SDXL (`sd_xl_base_1.0`) plus `lcm-lora-sdxl`, the `lcm` sampler and very few steps (draft default: 512×512, 4 steps).
2. A **vision model** (`llava:7b` or `moondream:v2`, chosen from the free VRAM) critiques the draft.
3. A text model **refines the prompt** from the critique; further drafts may follow.
4. A **final render** is produced from the best prompt. If later drafts fail, the best draft so far is kept.

Workflow templates live in `workflows/` (`sdxl_base.json`, `sd15.json`). `start_comfyui.sh` starts ComfyUI with `--force-fp16 --dont-upcast-attention --lowvram --cpu-vae` and listens on `0.0.0.0:8188`.

Two integration details that are easy to miss:

- **Different ComfyUI addresses:** Open WebUI is configured with `COMFYUI_BASE_URL=http://172.17.0.1:8188` (default bridge) while the `image_loop` valve `comfyui_url` defaults to `http://172.19.0.1:8188` (the `ollama_default` network gateway). Both are covered by the `ufw` rules.
- **Shared Python environment:** the launcher starts `rag_service.py` inside **ComfyUI's virtualenv** (`$HOME/ai-sessioni/ComfyUI/venv`). The RAG service therefore requires ComfyUI to be installed even if image generation is not used.

Because SDXL and the LLMs currently share one GPU, `image_loop` frees and waits for VRAM between phases. With the planned two-GPU split this coordination largely disappears (see [Roadmap](#roadmap)).

---

## Multi-GPU architecture

### Hardware

| GPU | Where | VRAM | `nvidia-smi` index (observed) | Intended role |
|---|---|---|---|---|
| NVIDIA GeForce RTX 3090 | external, AOOSTAR AG02 eGPU over **Thunderbolt 4** | 24 GB | 1 | **main** — large models, specialist agents, quality/refine |
| NVIDIA GeForce RTX 4060 Laptop | internal | 8 GB (~7 GB free: the desktop uses part of it) | 0 | **aux** — coordinator and lightweight models |

The two GPUs are **cooperating devices in one multi-agent system, not alternatives**. Each Ollama instance is pinned to **one** GPU: a single instance spanning both would split layers across the Thunderbolt link, whose bandwidth is roughly PCIe x4. Separate instances give predictable, truly parallel agents.

### Roles and environment variables

| Variable | Value | Effect |
|---|---|---|
| `ORCHESTRA_GPU_MAIN` | GPU **UUID** (`nvidia-smi -L`) | GPU whose free VRAM drives model selection; Ollama is pinned to it |
| `ORCHESTRA_GPU_AUX` | GPU **UUID** | secondary GPU (reported by `/vram`; used by the planned second Ollama instance) |
| `ORCHESTRA_GPU_ID` | UUID or index | legacy alias of `ORCHESTRA_GPU_MAIN` |

`start_ai_stack.sh` detects the roles itself (`detect_gpu_roles`): variables already set are kept if the UUID is currently present, otherwise **main = the GPU with the most VRAM** and **aux = the next one**. Use UUIDs, not indexes: indexes can change when the eGPU is re-plugged.

If the eGPU is disconnected the system **degrades gracefully**: the 4060 becomes `main`, `aux` stays empty and behaviour matches the former single-GPU setup (the 24 GB thresholds simply do not apply).

### VRAM monitoring

```text
nvidia-smi ──▶ rag_service  GET /vram ──▶ embedding_utils daemon thread (~5 s) ──▶ manifold / image_loop
 (host)        roles + per-GPU free/total      cached snapshot per role              get_vram_free_mb()  (= main)
                                                                                      get_gpu_free_mb("aux")
```

Before the multi-GPU change, with two GPUs visible `nvidia-smi --query-gpu=memory.free` returned two lines, the integer parsing failed and `/vram` always answered its 2000 MB fallback, so the manifold permanently selected the emergency 3B model. `/vram` now returns every GPU:

```bash
curl -s localhost:6335/vram | python3 -m json.tool
```

```json
{
  "vram_free_mb": 23848,          // legacy fields: refer to the `main` GPU
  "vram_total_mb": 24576,
  "gpu_name": "NVIDIA GeForce RTX 3090",
  "gpu_id": "GPU-…",
  "source": "nvidia-smi",         // or "fallback" (2000 MB) if nvidia-smi fails
  "roles_mode": "env",            // "env" = roles from variables, "auto" = most VRAM wins
  "gpus": [ { "index": "0", "uuid": "GPU-…", "name": "…4060…", "free_mb": 7046, "total_mb": 8188, "role": "aux" },
            { "index": "1", "uuid": "GPU-…", "name": "…3090…", "free_mb": 23848, "total_mb": 24576, "role": "main" } ]
}
```

`/vram?gpu=aux` (or `?gpu=<index|uuid>`) moves the top-level fields to that GPU, which is handy for debugging. The daemon in `embedding_utils.py` (v2.0.3) exposes `get_vram_free_mb()` (unchanged, = main), `get_gpu_free_mb(role)` and `get_gpu_snapshot()`. It stays compatible with an older `rag_service` (the per-role snapshot is simply empty).

### Migration status

| Step | Change | Status |
|---|---|---|
| 0 | `document-ai/scripts/egpu_check.sh` — read-only diagnostics (Thunderbolt, UUIDs, PCIe link, Docker runtime) | done |
| 1 | `/vram` with main/aux roles; daemon snapshot per role | done, validated on hardware |
| 2a | launcher: role detection; Ollama pinned to the main GPU (container recreated if it was created with `--gpus all`; the `ollama-session` model volume is untouched); built-in check that Ollama sees exactly one GPU | implemented (branch `dual-gpu-step2a`), awaiting hardware validation |
| 2b | second Ollama instance on the aux GPU (own port) | planned — depends on the final role split |
| 3 | manifold: separate backends per role (`ollama_url_main` / `ollama_url_aux`) | planned |
| 4 | thresholds, `keep_alive`, offload valves per role; remaining `subprocess` fallbacks | planned |
| 5 | ComfyUI pinned to a GPU; simplify `image_loop` VRAM coordination | planned |
| 6–7 | hardware report and agent prompts updated (still say "RTX 4060 8 GB"); handoff v9 | planned |

Planned role split (to be confirmed): **3090** → specialist agents, quality/refine models and SDXL; **4060** → coordinator (`llama3.2:3b`, always loaded) and vision. The detailed plan is in `document-ai/knowledge/ORCHESTRA_3090_MIGRAZIONE.md`.

### eGPU notes (Thunderbolt 4)

- **Link:** the eGPU negotiates PCIe **x4** (observed). `pcie.link.gen.current` reads Gen 1 at idle because the link down-clocks to save power — measure it **under load** (`nvidia-smi --query-gpu=pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current --format=csv`).
- **Performance profile:** loading a model into VRAM is slower than on a native slot; inference is close to native as long as the **whole model fits in VRAM**. Avoid offloading layers to system RAM on the eGPU, and prefer longer `keep_alive` for models on `main`.
- **Device ordering:** `nvidia-smi` lists the 4060 as index 0 and the 3090 as index 1, but CUDA's default order is *fastest first*, so inside CUDA processes (e.g. ComfyUI) the indexes can be swapped. Set `CUDA_DEVICE_ORDER=PCI_BUS_ID` before using indexes, or use UUIDs. Docker `--gpus device=<UUID>` is not affected.
- **Hot-unplug:** stop the stack (Ctrl-C on the launcher) before disconnecting the enclosure; unplugging under load can hang the driver or Docker.
- **Power and heat:** watch `power.draw` and `temperature.gpu` during the first long runs (`egpu_check.sh` prints both).

---

## Startup sequence

```bash
cd ~/ai-sessioni
./start_ai_stack.sh
```

The launcher (v3.8):

1. Detects the GPU roles and exports `ORCHESTRA_GPU_MAIN` / `ORCHESTRA_GPU_AUX`.
2. Stops previous Orchestra containers and kills residual `rag_service.py` processes.
3. Checks/mounts the configured external SSD and the zram swap (optional).
4. Creates required directories and the Docker network `ollama_default`.
5. Starts Ollama pinned to the main GPU (recreating it if needed) and waits until it answers.
6. Checks that Ollama sees exactly **one** GPU.
7. Pulls missing models.
8. Starts Qdrant, then the Pipelines container, then Open WebUI.
9. Synchronises the Pipelines API token into Open WebUI's database.
10. Starts the RAG service (inheriting the GPU variables).
11. Checks RAG health and indexes the knowledge base if the collection is empty.

Containers use `--restart no`; on interruption the launcher stops everything it started.

Paths and addresses currently **hard-coded** in the script, to adapt on another machine: the clone location `$HOME/ai-sessioni` (also used for ComfyUI and the secrets), the SSD mount `EXTERNAL_DISK_MOUNT`, and the Open WebUI bind address `192.168.1.51`.

---

## Networking and security

Services reach each other by container name on `ollama_default` (e.g. `http://ai-ollama-session:11434`, `http://ai-qdrant-session:6333`). Pipelines reaches the host-side RAG service through `host.docker.internal:6335`.

| Service | Listening on | External access |
|---|---|---|
| Ollama, Qdrant, Pipelines | `127.0.0.1` | none |
| RAG service (6335) | `0.0.0.0` | `ufw` allows only the Docker bridges `172.17.0.0/16` and `172.19.0.0/16`, denies everyone else |
| ComfyUI (8188) | `0.0.0.0` | same pattern: allowed from `172.19.0.0/16`, denied elsewhere |
| Open WebUI (3001) | `192.168.1.51` | direct access denied by `ufw`; published through a **Caddy reverse proxy (HTTPS, ports 80/443)** |

Because the RAG service and ComfyUI bind to all interfaces by design (containers must reach them on the host), **the firewall is part of the security model**. `document-ai/scripts/setup_security.sh` configures Caddy and `ufw` to expose the stack on the internet (ports 80/443 forwarded to this machine, with automatic rollback on error), so Open WebUI should be treated as an **internet-facing service**: keep strong credentials, keep Open WebUI updated and review the `ufw` rules after every change. A snapshot of the rules is in `document-ai/config/ufw_rules_export.txt`; the Caddy configuration is not versioned in this repository. `/vram` is read-only and unauthenticated, like the other RAG endpoints except `/deploy`.

Secrets:

- The Pipelines API token is generated once (`openssl rand -hex 32`) into `~/ai-sessioni/.orchestra_token` (`chmod 600`) and used between Open WebUI and Pipelines.
- `~/ai-sessioni/.webui_secret_key` is Open WebUI's secret.
- Never commit them. `.gitignore` is a **whitelist** (`/*` ignored; only `ollama/`, `document-ai/`, `logs/`, `rag/`, `workflows/` re-enabled) and also blocks `*.key`, `*.token`, `.env*`, `.*_token*`, `.*_secret*`. Add a `!/…` rule when you need to track a new root-level file.
- Regenerate any secret that was ever exposed (including in a chat or a shared terminal).

---

## Repository layout

```text
orchestra-ai/
├── start_ai_stack.sh            # launcher v3.8 (containers, GPU roles, RAG service)
├── start_comfyui.sh             # ComfyUI launcher (host process)
├── README.md
├── .gitignore                   # whitelist
│
├── ollama/
│   ├── docker-compose.yml       # Ollama + Open WebUI only; NOT used by the launcher
│   ├── Modelfile-blender
│   └── pipelines/
│       ├── orchestra_manifold.py   # routing, agents, model selection, commands
│       ├── rag_filter.py           # retrieval + context injection
│       ├── image_loop.py           # SDXL/LCM generation loop (utility module)
│       ├── orchestra_evolver.py    # /evolve (utility module)
│       ├── embedding_utils.py      # embeddings, caches, VRAM daemon (utility module)
│       ├── pattern_logger.py       # JSONL event log
│       ├── requirements.txt
│       └── <module>/valves.json    # default valves per module
│
├── rag/
│   ├── rag_service.py              # Flask API (index, search, /vram, /deploy)
│   ├── rag_indexer_lib.py
│   ├── Dockerfile, docker-compose.prod.yml, requirements.txt   # containerised alternative
│   └── patch_*.py, rag_patch_*.py  # one-off patch scripts (historical)
│
├── document-ai/
│   ├── knowledge/                  # indexed knowledge base (also the handoff and hardware report)
│   ├── config/                     # valve examples, ufw export, docker daemon config, Modelfile
│   ├── scripts/                    # egpu_check.sh, install/security/model scripts
│   └── routing_snapshots/          # snapshots of the routing examples (/evolve)
│
├── workflows/                      # ComfyUI templates: sdxl_base.json, sd15.json
└── logs/                           # rag_service.log, patterns.jsonl
```

`embedding_utils.py`, `image_loop.py` and `orchestra_evolver.py` are **utility modules, not pipelines**: they intentionally do not define a `Pipeline` class, so the Pipelines framework does not load them as standalone pipelines.

---

## Requirements

- Linux with Docker (the launcher uses `docker run`; Docker Compose is only needed for the optional compose files)
- NVIDIA driver and **NVIDIA Container Toolkit** (`--gpus device=<UUID>` support)
- For the eGPU: a working Thunderbolt authorisation (e.g. `boltctl`) and a driver that sees both GPUs (`nvidia-smi -L`)
- Python 3 with the ComfyUI virtualenv (also used by the RAG service), `curl`, `openssl`
- `zramctl` (optional), plenty of disk for models (the launcher can mount an external SSD)
- A ComfyUI installation under `$HOME/ai-sessioni/ComfyUI` with `sd_xl_base_1.0` and `lcm-lora-sdxl` (see `document-ai/scripts/download_lcm_lora.sh`)

Exact versions are intentionally not pinned here.

---

## Running and verifying

First-time GPU check (read-only):

```bash
bash document-ai/scripts/egpu_check.sh     # prints UUIDs and suggested ORCHESTRA_GPU_* exports
```

Start and verify:

```bash
./start_ai_stack.sh

docker ps                                          # containers up
curl http://127.0.0.1:11435/                       # Ollama
curl http://127.0.0.1:6333/healthz                 # Qdrant
curl http://127.0.0.1:6335/health                  # RAG service
curl -s localhost:6335/vram | python3 -m json.tool # per-GPU VRAM and roles
docker exec ai-ollama-session nvidia-smi -L        # must list exactly ONE GPU (the main one)
docker exec ai-ollama-session ollama ps            # loaded model: PROCESSOR should read 100% GPU
docker exec ai-ollama-session ollama list          # installed models
tail -f logs/rag_service.log                       # RAG service log
```

Smoke test after any change: `/vram` shows both GPUs with the right roles, Ollama sees one GPU, a prompt to the coordinator is answered, and (if you touched images) `/generate` completes.

---

## Troubleshooting

| Symptom | Likely cause | Check / fix |
|---|---|---|
| Manifold always picks `llama3.2:3b` | `/vram` is returning the 2000 MB fallback | `curl localhost:6335/vram`: `source` must be `nvidia-smi`; check `logs/rag_service.log` |
| `/vram` shows the 4060 as `main` | eGPU not visible, or `ORCHESTRA_GPU_MAIN` holds a stale UUID | `nvidia-smi -L`, re-run `egpu_check.sh`, re-plug/authorise the enclosure |
| Ollama sees 2 GPUs | container created earlier with `--gpus all` and not recreated | restart through the launcher; or `docker rm -f ai-ollama-session` (model volume is kept) |
| Launcher warns "UUID not present" | `ORCHESTRA_GPU_*` set to an index or an old UUID | unset them or use the UUIDs from `nvidia-smi -L` |
| Wrong GPU used by ComfyUI | CUDA ordering differs from `nvidia-smi` | `CUDA_DEVICE_ORDER=PCI_BUS_ID`, then select by index (planned step 5) |
| Model loads slowly after plugging the eGPU | Thunderbolt bandwidth | expected; raise `keep_alive` for `main` models |
| RAG answers mention "RTX 4060 8 GB" | outdated `hardware-report.md` was indexed | update the report and re-index (planned step 6) |
| Open WebUI cannot talk to Pipelines | token mismatch | the launcher re-aligns it; check `.orchestra_token` and the launcher output |
| "Port 6335 already in use" | previous `rag_service.py` still running | the launcher kills residual processes; otherwise `pkill -f rag_service.py` |
| Driver/Docker hangs after unplugging | eGPU removed under load | stop the stack first; reboot or reload the driver |

---

## Known limitations and repository hygiene

- Several values are hard-coded for one machine (`$HOME/ai-sessioni`, the SSD mount, `192.168.1.51`, Docker gateway addresses). Move them to a `.env` file before sharing the setup.
- The remaining `subprocess` VRAM fallbacks in `orchestra_manifold.py` and `image_loop.py` still parse `nvidia-smi` as a single line (they only run if `embedding_utils` cannot be imported). Planned for step 4.
- Valve defaults differ between the code, `valves.json` files and `document-ai/config/` (e.g. `max_context_chars`, `vram_fallback_min_mb`). The Open WebUI valves panel is the source of truth at runtime.
- `document-ai/knowledge/ORCHESTRA_HANDOFF_v8.md` predates some code versions (e.g. `rag_service` v1.5.x, `embedding_utils` v2.0.x) and still describes the 8 GB single-GPU design. It will be replaced by a v9.
- Housekeeping candidates: `pattern_logger.py` exists in three places (`ollama/pipelines/`, `rag/`, `document-ai/scripts/`); `ollama/pipelines/github_tools/valves.json` has no matching module; `rag/.file_hash_cache.json` is a runtime cache that is tracked; the `rag/*patch*.py` files are historical one-off scripts.
- Three Compose files (`ollama/`, `document-ai/config/`, `rag/docker-compose.prod.yml`) describe partial or alternative setups and are not used by `start_ai_stack.sh`.

---

## Roadmap

- [x] Multi-GPU VRAM monitoring with `main` / `aux` roles
- [x] Ollama pinned to the 3090 (awaiting hardware validation)
- [ ] Second Ollama instance on the 4060 and per-role routing in the manifold
- [ ] Per-role thresholds, `keep_alive` and offload settings (full-GPU quality model on the 3090)
- [ ] ComfyUI pinned to a GPU; remove VRAM hand-offs between SDXL and the LLMs
- [ ] GPU-aware agent assignment (coordinator/vision on aux, heavy agents on main)
- [ ] Refresh the hardware report, agent prompts and handoff (v9); re-index the knowledge base
- [ ] Environment-based configuration instead of hard-coded paths and addresses
- [ ] Better observability: per-GPU metrics in `/status`, richer logging

---

## Documentation index and contributing

| Document | Content |
|---|---|
| `document-ai/knowledge/ORCHESTRA_3090_MIGRAZIONE.md` | dual-GPU migration plan, problems found, risks (Italian) |
| `document-ai/knowledge/ORCHESTRA_HANDOFF_v8.md` | architecture handoff for the single-GPU design |
| `document-ai/knowledge/hardware-report.md` | hardware report (to be updated for the dual-GPU setup) |
| `document-ai/scripts/egpu_check.sh` | read-only GPU / Thunderbolt diagnostics |

Working conventions used for the migration:

- **One change at a time**, each on its own branch and commit, with an explanation in the code comments (changes are tagged `EGPU-01`, `EGPU-02`, …).
- **Be conservative:** do not change what works without a concrete technical reason; keep the previous behaviour as the default when a new variable is not set.
- **Smoke test before declaring success:** every step lists a command whose output must be checked (see [Running and verifying](#running-and-verifying)).
- Merge pull requests with **"Create a merge commit"** or **"Rebase and merge"**, not "Squash", to keep one commit per logical change.

---

## License

No license is currently specified. If the repository is meant for public reuse, add an explicit license file before publishing a stable release.
