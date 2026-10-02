# AI Read Protocol - Orchestra AI

Protocollo di lettura a strati per agenti AI che operano sul repository.

## Layer 0 - Bootstrap (sempre)

File: AI_BOOTSTRAP.md

Regole anti-allucinazione, metodi di lettura, lista file (ground truth).

## Layer 1 - Manifest (sempre)

File: AI_MANIFEST.md

Inventario file con size + SHA.

## Layer 2 - Bundle per dominio (al bisogno)

| File | Contenuto |
|------|-----------|
| AI_CTX_core.md | README, workflow, .gitignore, launcher |
| AI_CTX_rag.md | rag/ completa |
| AI_CTX_pipelines.md | ollama/ completa |
| AI_CTX_scripts.md | document-ai/scripts/ completa |
| AI_CTX_config.md | document-ai/config/ completa |
| AI_CTX_knowledge_index.md | Solo nomi/size dei file in knowledge/ |

Esclusi: ComfyUI/, PDF, DOCX, XLSX, snapshot JSON, log.

## Layer 3 - File singoli (al bisogno)

Qualsiasi file tracciato.

## Layer 4 - MCP GitHub (solo Ollama Tier 2+)

Server MCP github-mcp (container Docker, porta 3102).
Solo per modelli con tool calling: qwen2.5-coder:14b+, qwen2.5-coder:32b, command-r:35b.

## Rigenerazione

    ./document-ai/scripts/generate_ai_context.sh
