# Cartelle temporanee delle prove: SEMPRE rimosse all'uscita (anche con errore o Ctrl-C).
# MOTIVO (9 nov 2026): le prove creavano cartelle con mktemp -d e non le toglievano mai; unite alla copia dell
# intero albero di lavoro (test_launcher) hanno riempito il disco di sistema dell'utente (185 GB in /tmp).
# Uso: dopo la definizione di HERE, `. "$HERE/helpers/tmpclean.sh"` e poi `$(mk_tmp)` al posto di `$(mktemp -d)`.
# Il registro e un file, cosi la registrazione funziona anche dentro $(...) e sottoshell.
_TMP_REG="$(mktemp)"
mk_tmp() { local d; d="$(mktemp -d)" || return 1; printf '%s\n' "$d" >> "$_TMP_REG"; printf '%s\n' "$d"; }
_tmp_cleanup() { local d; while IFS= read -r d; do [ -n "$d" ] && [ -d "$d" ] && rm -rf -- "$d"; done < "$_TMP_REG"; rm -f -- "$_TMP_REG"; }
trap _tmp_cleanup EXIT
