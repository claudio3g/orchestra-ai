#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
MAX_FILE_SIZE=100000

rm -f AI_CONTEXT.md

is_includable() {
  local f="$1"
  [ -f "$f" ] || return 1
  local size
  size=$(stat -c%s "$f" 2>/dev/null || echo 0)
  [ "$size" -gt "$MAX_FILE_SIZE" ] && return 1
  case "$f" in
    *.pdf|*.docx|*.xlsx|*.png|*.jpg|*.jpeg|*.gif|*.zip|*.tar|*.gz|*.gguf|*.safetensors|*.onnx|*.bin|*.pyc|*.jsonl) return 1 ;;
  esac
  return 0
}

{
  echo "# AI Manifest - Orchestra AI"
  echo ""
  echo "> Generato: $NOW"
  echo "> Branch: $BRANCH"
  echo ""
  echo "| Path | Byte | SHA breve |"
  echo "|------|------|-----------|"
  git ls-files | while IFS= read -r f; do
    [ -f "$f" ] || continue
    size=$(stat -c%s "$f" 2>/dev/null || echo "?")
    sha=$(git hash-object "$f" | cut -c1-8)
    printf '| `%s` | %s | `%s` |\n' "$f" "$size" "$sha"
  done
  echo ""
  echo "**Totale: $(git ls-files | wc -l) file tracciati.**"
} > AI_MANIFEST.md
echo "OK: AI_MANIFEST.md"

emit_bundle() {
  local out="$1"
  local title="$2"
  shift 2
  {
    echo "# $title"
    echo ""
    echo "> Generato: $NOW"
    echo "> Branch: $BRANCH"
    echo ""
    echo "---"
    echo ""
    while IFS= read -r f; do
      is_includable "$f" || continue
      local size
      size=$(stat -c%s "$f")
      echo "## File: $f ($size byte)"
      echo ""
      echo '```'
      cat "$f"
      echo '```'
      echo ""
    done < <(git ls-files -- "$@" | sort -u)
  } > "$out"
  echo "OK: $out"
}

emit_bundle "AI_CTX_core.md" "AI Context - Core" "README.md" "README.it.md" ".gitignore" ".github/workflows/" "start_ai_stack.sh" "start_comfyui.sh"
emit_bundle "AI_CTX_rag.md" "AI Context - RAG" "rag/"
emit_bundle "AI_CTX_pipelines.md" "AI Context - Pipelines" "ollama/"
emit_bundle "AI_CTX_scripts.md" "AI Context - Scripts" "document-ai/scripts/"
emit_bundle "AI_CTX_config.md" "AI Context - Config" "document-ai/config/"

{
  echo "# AI Context - Knowledge Index"
  echo ""
  echo "> Generato: $NOW"
  echo ""
  echo "| File | Byte |"
  echo "|------|------|"
  git ls-files "document-ai/knowledge/" | while IFS= read -r f; do
    [ -f "$f" ] || continue
    size=$(stat -c%s "$f" 2>/dev/null || echo "?")
    printf '| `%s` | %s |\n' "$f" "$size"
  done
} > AI_CTX_knowledge_index.md
echo "OK: AI_CTX_knowledge_index.md"

echo "Done."
