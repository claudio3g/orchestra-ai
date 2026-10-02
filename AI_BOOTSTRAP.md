# AI Bootstrap — Orchestra AI

Documento di onboarding per agenti AI che operano su questo repository.

> **Ultimo aggiornamento:** 2026-10-02
> **Owner:** claudio3g
> **Repo:** claudio3g/orchestra-ai

---

## ⚠️ REGOLE FONDAMENTALI (leggere prima di tutto)

### Anti-hallucination policy

1. **Non inventare MAI** contenuti di file, struttura di directory o nomi di file.
2. **La lista autorevole dei file è in `AI_MANIFEST.md`**. Nessun file esiste al di fuori di quella lista. Se pensi che un file dovrebbe esistere ma non è nel manifest, **non esiste**.
3. **Ogni affermazione sul contenuto del repository deve essere supportata da un fetch effettivo**. Se non riesci a leggere un file, dì esplicitamente: "non ho potuto leggere X".
4. **Se una richiesta richiede di leggere più file di quanti riesci a fetchare**, dillo esplicitamente e chiedi quali prioritizzare. Non riempire i buchi con contenuti plausibili.
5. **Non citare file, directory o contenuti che non hai letto**. Se ti viene chiesto "leggi tutti i file", leggi il manifest, poi leggi i file uno per uno. Se non puoi, dichiaralo.

### Cosa fare se non puoi completare un task

- Dì: "Non posso completare X perché Y".
- Proponi un'alternativa: "Posso fare X1 o X2, quale preferisci?".
- **Mai** inventare per riempire il vuoto.

---

## 1. Come accedere al repository

### Metodo che funziona ✅

GitHub REST API:

    https://api.github.com/repos/claudio3g/orchestra-ai/contents/<path>

Risposta: JSON con campo `content` codificato base64. Decodifica:

    curl -s "https://api.github.com/repos/claudio3g/orchestra-ai/contents/README.it.md" | python3 -c "import sys, json, base64; print(base64.b64decode(json.load(sys.stdin)['content']).decode())"

### Metodo che può non funzionare ❌

    https://raw.githubusercontent.com/claudio3g/orchestra-ai/main/<path>

Può fallire per restrizioni di rete/proxy in alcuni ambienti AI.

---

## 2. File di onboarding (leggi in quest'ordine)

1. **`AI_BOOTSTRAP.md`** (questo file) — regole + metodo di accesso
2. **`AI_MANIFEST.md`** — inventario autorevole di TUTTI i file
3. **`AI_CONTEXT.md`** — bundle dei file chiave

URL API:

    https://api.github.com/repos/claudio3g/orchestra-ai/contents/AI_BOOTSTRAP.md
    https://api.github.com/repos/claudio3g/orchestra-ai/contents/AI_MANIFEST.md
    https://api.github.com/repos/claudio3g/orchestra-ai/contents/AI_CONTEXT.md

---

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

1. L'AI genera una patch in formato diff.
2. La patch viene inviata via `curl` a GitHub API.
3. Il workflow `ai-commit.yml` si attiva:
   - **Job `validate`**: verifica payload.
   - **Job `sandbox-test`**: applica la patch su ubuntu-24.04 ed esegue 5 test.
   - **Job `commit-push`**: se i test passano, esegue lo script di generazione (se presente), committa e pusha.

Trigger manuale:

    ~/ai-dispatch.sh <patch.diff> "<messaggio commit>" [branch]

---

## 5. Cose da NON fare

- ❌ `git reset --hard` senza backup
- ❌ `git push --force`
- ❌ committare: `~/.orchestra_github_token`, `.orchestra_token`, `.webui_secret_key`, `rag/.file_hash_cache.json`
- ❌ esporre i servizi pubblicamente

---

## 6. Comandi utili

    cd ~/ai-sessioni && git status && git log --oneline -5
    curl -s http://127.0.0.1:6335/health | jq
    curl -s http://127.0.0.1:6335/status | jq
    git bundle create ~/orchestra-backup-$(date +%F_%H%M).bundle --all
    ./document-ai/scripts/generate_ai_context.sh

---

## 7. Contesto hardware

- CPU: Intel i9 (nix-i9)
- GPU: RTX 3090 (24 GB) + RTX 4060 Laptop (8 GB)
- OS: Ubuntu 24.04
- Runtime AI: Ollama + Open WebUI + Qdrant + RAG custom
- Migrazione dual-GPU: in corso (branch `dual-gpu-step1`)

