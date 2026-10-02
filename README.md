# Orchestra AI

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
Host: 127.0.0.1
Port: 6335
```

The RAG service is implemented in Python and can also be run independently from its Docker Compose definition.

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

The current configuration includes several local-only bindings:

```text
127.0.0.1:11435
127.0.0.1:6333
127.0.0.1:6335
```

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

Orchestra is designed to use NVIDIA GPUs through Docker/NVIDIA Container Toolkit.

The current launcher exposes the available NVIDIA GPUs to the Ollama and Pipelines containers.

The current development hardware uses:

- **NVIDIA GeForce RTX 3090 — 24 GB VRAM**
- **NVIDIA GeForce RTX 4060 Laptop GPU — 8 GB VRAM**

The intended architecture is to use the RTX 3090 as the primary high-VRAM AI accelerator while retaining the RTX 4060 for smaller models and secondary workloads.

GPU-specific model/process routing is an active area of development and should not be assumed to be fully automatic unless explicitly configured in the current launcher.

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
- Docker Compose
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

ComfyUI exists in the wider local environment, but it is **not part of the current Orchestra AI core scope**.

The Orchestra architecture documented here focuses on:

- Ollama
- Open WebUI
- Pipelines
- RAG
- Qdrant
- document-ai
- local knowledge management

ComfyUI should therefore be considered an external/legacy integration and is not required to understand or operate the core Orchestra AI architecture.

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

The project is evolving toward a multi-GPU local AI architecture.

The target architecture is:

```text
                    ORCHESTRA
                        │
          ┌─────────────┴─────────────┐
          │                           │
   RTX 3090 — 24 GB              RTX 4060 — 8 GB
   Primary AI workloads          Secondary workloads
          │                           │
   Large / reasoning             Small / lightweight
   coding models                 agents and models
   RAG-heavy tasks               auxiliary processing
```

The goal is to exploit both GPUs rather than treating the RTX 4060 as a fallback device.

GPU-to-model and GPU-to-agent routing will be implemented explicitly so that high-VRAM workloads can be kept on the RTX 3090 while smaller workloads can use the RTX 4060.

---

## Roadmap

Planned or ongoing work includes:

- explicit multi-GPU model routing
- GPU-aware agent assignment
- improved agent/model orchestration
- continued RAG optimization
- improved document ingestion
- resource-aware model loading
- better observability and logging
- further separation between core Orchestra services and optional integrations

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

## License

No license is currently specified in this README.

If the repository is intended for public reuse, add an explicit license file before publishing a stable release.
