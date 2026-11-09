# VS Code + Continue in Orchestra

Stato verificato il 2026-11-09. Gli script e le configurazioni vivono in
`document-ai/scripts/vscode/`; le copie ATTIVE stanno sul PC dell'utente
(vedi "Installazione"). Direttiva: **modelli locali per primi**; DeepSeek e solo
una scelta MANUALE per compiti troppo difficili per Ollama.

## Come e collegato
| Modalita | Quando | Chat/edit/apply | Autocomplete |
|---|---|---|---|
| FULL | 2 GPU (3090 + 4060) | `qwen2.5-coder:14b-instruct-q4_K_M` su `:11435` (main, 3090) | `qwen2.5-coder:1.5b` su `:11436` (aux, 4060) |
| NORMAL | 1 GPU (4060) | `llama3.1:8b` su `:11435` (la 4060 diventa main) | nessuno (vedi sotto) |

- `vsc-orchestra.sh [normal|full] [--dry-run]` rileva le GPU con
  `orchestra-detect-gpu.sh` (>=2 GPU = FULL), copia la config scelta su
  `~/.continue/config.yaml` (con backup `.bak.<data>`) e lancia VS Code.
- La chiave DeepSeek sta in `~/.continue/.env` (permessi 600) e viene letta da
  Continue con `apiKey: ${{ secrets.DEEPSEEK_API_KEY }}`. Il file
  `~/.config/continue/env.sh` e caricato da `~/.bashrc` (riga ~160).
  **Nessuna chiave e nel repository.**

## Perche NORMAL non ha autocomplete e usa :11435
Con la 3090 assente il launcher assegna il ruolo main alla 4060 e **non avvia
l'Ollama aux** (`aux_ollama_enabled` richiede `ORCHESTRA_GPU_AUX` non vuoto), quindi
`:11436` non esiste. Sulla 4060 resta caricato 1 solo modello alla volta: chat e
autocomplete si scaricherebbero a vicenda a ogni pausa di digitazione.
Se si vuole l'autocomplete anche in NORMAL va misurato prima (non deciso).

## Trappole gia incontrate
- `${VAR}` nello YAML di Continue NON viene espanso (inviato letteralmente -> 401).
  Sintassi valida: `${{ secrets.NOME }}`.
- Modelli DeepSeek disponibili: `deepseek-flash`, `deepseek-v4-pro`.
- FIM (autocomplete) funziona con `qwen2.5-coder:1.5b` (parametro `suffix`).
- VS Code deve essere riavviato per applicare una nuova config.
- Incidente B19: le prove automatiche copiavano il progetto in /tmp e hanno
  riempito il disco (ENOSPC); corretto in `tests/test_launcher.sh` + `tests/helpers/tmpclean.sh`.
- Claude Code per VS Code richiede piano Claude a pagamento o account Console: il
  piano gratuito di claude.ai non basta. Claude in Continue richiederebbe una API key
  Console (a consumo): opzionale, non configurato.

## Installazione (sul PC)
```bash
R=~/ai-sessioni/document-ai/scripts/vscode
cp $R/vsc-orchestra.sh $R/orchestra-detect-gpu.sh ~/.local/bin/
cp $R/continue/config.normal.yaml $R/continue/config.full.yaml ~/.continue/
cp $R/settings.workspace.json ~/ai-sessioni/.vscode/settings.json   # solo se vuoi
```

## Da fare
Una sola fonte per modelli e porte (orchestra.env) condivisa da launcher e script;
misurare modelli alternativi per NORMAL/FULL con `orchestra_eval.py`.
