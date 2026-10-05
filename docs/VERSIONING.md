# Versioning e rollback

Ogni stato stabile del progetto e' un **tag annotato** (cosi' si puo' sempre tornare indietro) e ogni
modifica dell AI passa da una **branch** con commit separati e una pull request. `main` non viene mai
riscritto: niente `push --force`.

## Tag

| Tag | Punto | Significato |
|-----|-------|-------------|
| `pre-dual-gpu` | `f135a06` (main) | stato PRIMA della migrazione dual-GPU: launcher v3.8, GPU singola |
| `dual-gpu-rc1` | `9b771b1` (branch `dual-gpu-final`, PR 2) | release candidate 1: 226 controlli simulati, da validare su hardware |

Convenzione: `vMAIOR.MINORE.PATCH` solo dopo la validazione su hardware (`orchestra_smoke_test.sh --load`);
`*-rcN` per le release candidate; `pre-<nome>` prima di una migrazione. I tag si creano annotati
(`git tag -a NOME COMMIT -m "cosa, test eseguiti, stato di validazione"`).

## Rollback

**1. Tornare a uno stato precedente senza perdere nulla (consigliato)**
```bash
git fetch --tags
git checkout pre-dual-gpu            # stato detached, solo lettura/prova
git switch -c rollback-prova         # per lavorarci sopra
```

**2. Annullare un merge gia' fatto su main (storia intatta)**
```bash
git revert -m 1 <hash-del-merge>     # crea un commit che annulla il merge; poi push normale
```

**3. Annullare UN solo componente**
```bash
git checkout pre-dual-gpu -- start_ai_stack.sh start_comfyui.sh     # esempio: solo il launcher
git revert <hash-del-singolo-commit>                                # oppure un commit specifico
```

**4. Dopo il rollback del launcher: riallineare i container.** I container conservano le variabili con cui
sono stati creati e il launcher vecchio li riusa. Per tornare alla configurazione precedente:
```bash
docker rm -f ai-ollama-session ai-ollama-aux-session ai-pipelines-session   # i volumi dei modelli NON vengono toccati
bash start_ai_stack.sh                                                      # il launcher vecchio li ricrea come prima
```
Il volume `ollama-aux-session` (modelli della 4060) puo' restare o essere rimosso con `docker volume rm`.

## Aggiornare la copia locale (pull sicuro)

Non servono `git pull` o `git stash` a mano: `orchestra_sync.sh` salva prima un backup in
`~/orchestra-backup-locale/<data>/`, annulla un merge rimasto a meta (causa tipica di un file con marcatori
`<<<<<<<` che sembra "corrotto"), mette da parte le modifiche ai file tracciati, sposta in backup solo i file non
tracciati che collidono con il remoto, passa al branch o al tag richiesto e verifica commit, sintassi di tutti gli
script, `start_ai_stack.sh` identico al remoto e test. Non fa mai push ne `reset --hard`.

```bash
cd ~/ai-sessioni
bash document-ai/scripts/orchestra_sync.sh                 # branch dual-gpu-final
bash document-ai/scripts/orchestra_sync.sh dual-gpu-rc2    # oppure un tag (stato detached, sola lettura)
```
La prima volta, se lo script non e ancora nella tua copia, scaricalo e controllalo prima di eseguirlo:
```bash
curl -fsSL -o /tmp/orchestra_sync.sh https://raw.githubusercontent.com/claudio3g/orchestra-ai/dual-gpu-final/document-ai/scripts/orchestra_sync.sh
less /tmp/orchestra_sync.sh && bash /tmp/orchestra_sync.sh
```

## Dati non versionati (backup prima di cambiare)

| Dato | Dove | Backup |
|------|------|--------|
| Modelli Ollama | volumi `ollama-session`, `ollama-aux-session` | si riscaricano; non servono backup |
| Indice RAG | volume `qdrant-data` | `curl -X POST http://127.0.0.1:6333/collections/orchestra/snapshots` |
| Valves salvati in Open WebUI | database di Open WebUI | esportare dal pannello valves prima di modifiche ai valve |
| Segreti | `.orchestra_token`, `.webui_secret_key`, `orchestra.env` | copia privata fuori dal repository |

## Regole per le modifiche dell AI

- Commit via `ai-dispatch` (`repository_dispatch`, evento `ai-update`) **sempre con `branch` esplicito**:
  senza il campo il workflow scrive su `main`.
- Messaggi di commit senza apostrofi e virgolette (il workflow li interpola in uno script shell).
- Patch piccole (il `client_payload` ha un limite di dimensione): un cambiamento logico per volta.
- Prima di ogni commit: `bash tests/run_all.sh` deve restare verde.
- Il merge in `main` lo decide l utente (consigliato: "Create a merge commit" o "Rebase and merge", mai Squash).
