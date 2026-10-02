# AI Workflow — Commit automatici

Questo repository supporta commit automatici generati da AI tramite repository_dispatch.

## Flusso

1. L'AI genera una patch in formato diff
2. La patch viene inviata via curl a GitHub API
3. Il workflow ai-commit.yml si attiva:
   - Job validate: controlla che il payload sia valido
   - Job sandbox-test: applica la patch in un runner isolato ed esegue i test
   - Job commit-push: se i test passano, committa e pusha

## Trigger manuale

    ~/ai-dispatch.sh /path/to/patch.diff "Messaggio commit" [branch]

## Sicurezze

- La patch e validata prima di essere applicata (base64, formato diff, dry-run)
- I test girano in sandbox su ubuntu-24.04
- Il commit avviene solo se tutti i test passano
- Se i test falliscono, il repository resta invariato

## Test eseguiti

- Python syntax: python -m py_compile su tutti i .py
- Bash syntax: bash -n su tutti gli .sh
- YAML syntax: parsing con pyyaml
- JSON syntax: parsing con json
- Shellcheck: errori gravi negli script bash
