#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
MAX_FILE_SIZE=100000

rm -f AI_CONTEXT.md

# Elenco file del repository: tracciati + NUOVI non ancora in staging (rispettando .gitignore).
# Il workflow applica la patch con `git apply` (senza staging), esegue questo script e solo dopo
# fa `git add -A`: con `git ls-files` i file aggiunti dalla patch comparivano nel manifest con
# un commit di ritardo. Qui si usa lo stesso criterio di `git add -A`. I file cancellati dalla
# patch (ancora nell'indice) vengono scartati dal controllo di esistenza.
list_files() {
  git ls-files --cached --others --exclude-standard -- "$@" | sort -u | while IFS= read -r f; do
    if [ -f "$f" ]; then printf '%s\n' "$f"; fi
  done
}

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
  list_files | while IFS= read -r f; do
    size=$(stat -c%s "$f" 2>/dev/null || echo "?")
    sha=$(git hash-object "$f" | cut -c1-8)
    printf '| `%s` | %s | `%s` |\n' "$f" "$size" "$sha"
  done
  echo ""
  echo "**Totale: $(list_files | wc -l) file tracciati.**"
} > AI_MANIFEST.md
echo "OK: AI_MANIFEST.md"

# Sezione "2. File tracciati (GROUND TRUTH)" di AI_BOOTSTRAP.md: rigenerata dall'elenco reale
# (era scritta a mano e restava indietro: citava AI_CONTEXT.md, ormai inesistente). Le altre
# sezioni non vengono toccate.
if [ -f AI_BOOTSTRAP.md ] && grep -q '^## 2\. File tracciati' AI_BOOTSTRAP.md; then
  list_tmp="$(mktemp)"
  {
    echo '| Path | Byte |'
    echo '|------|------|'
    list_files | while IFS= read -r f; do
      printf '| `%s` | %s |\n' "$f" "$(stat -c%s "$f" 2>/dev/null || echo '?')"
    done
  } > "$list_tmp"
  awk -v list="$list_tmp" '
    /^## 2\. File tracciati/ {
      print; print ""
      print "**Nessun file esiste al di fuori di questa lista. Se un file non è qui, NON ESISTE.**"
      print ""
      while ((getline line < list) > 0) print line
      print ""; skip=1; next }
    /^## 3\./ { skip=0 }
    !skip { print }
  ' AI_BOOTSTRAP.md > AI_BOOTSTRAP.md.new && mv AI_BOOTSTRAP.md.new AI_BOOTSTRAP.md
  rm -f "$list_tmp"
  echo "OK: AI_BOOTSTRAP.md (sezione file tracciati)"
fi

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
    done < <(list_files "$@")
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
  list_files "document-ai/knowledge/" | while IFS= read -r f; do
    size=$(stat -c%s "$f" 2>/dev/null || echo "?")
    printf '| `%s` | %s |\n' "$f" "$size"
  done
} > AI_CTX_knowledge_index.md
echo "OK: AI_CTX_knowledge_index.md"

echo "Done."
