#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

REPO="claudio3g/orchestra-ai"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

{
  echo "# AI Manifest — Orchestra AI"
  echo ""
  echo "Inventario autorevole dei file tracciati nel repository."
  echo ""
  echo "> **REGOLA**: nessun file esiste al di fuori di questa lista. Non inventare path, non assumere file non elencati."
  echo ""
  echo "> Generato: $NOW"
  echo "> Branch: \`$BRANCH\`"
  echo "> Rigenerazione: \`document-ai/scripts/generate_ai_context.sh\`"
  echo ""
  echo "## Accesso ai file"
  echo ""
  echo "Per leggere un file:"
  echo ""
  echo '    https://api.github.com/repos/'"$REPO"'/contents/<path>'
  echo ""
  echo "Il campo \`content\` è base64. Decodifica:"
  echo ""
  echo '    curl -s "<url>" | python3 -c "import sys,json,base64; print(base64.b64decode(json.load(sys.stdin)[\"content\"]).decode())"'
  echo ""
  echo "## File tracciati"
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
  echo "## Totale"
  echo ""
  echo "- File tracciati: $(git ls-files | wc -l)"
} > AI_MANIFEST.md

KEY_FILES=(
  "README.it.md"
  "AI_BOOTSTRAP.md"
  ".github/workflows/ai-commit.yml"
  ".gitignore"
  "start_ai_stack.sh"
)

{
  echo "# AI Context — Orchestra AI"
  echo ""
  echo "Bundle dei file chiave del progetto, concatenati per un singolo fetch."
  echo ""
  echo "> Generato: $NOW"
  echo "> Branch: \`$BRANCH\`"
  echo ""
  echo "---"
  echo ""
  for f in "${KEY_FILES[@]}"; do
    if [ -f "$f" ]; then
      echo "## File: \`$f\`"
      echo ""
      echo '```'
      head -c 50000 "$f"
      echo ""
      echo '```'
      echo ""
      echo "---"
      echo ""
    fi
  done
} > AI_CONTEXT.md

echo "OK: AI_MANIFEST.md ($(wc -c < AI_MANIFEST.md) byte)"
echo "OK: AI_CONTEXT.md ($(wc -c < AI_CONTEXT.md) byte)"
