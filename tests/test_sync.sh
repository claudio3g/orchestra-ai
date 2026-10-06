#!/bin/bash
# Test di orchestra_sync.sh su repository locali SPORCHI costruiti dalla cronologia reale del repo.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
S="$REPO/document-ai/scripts/orchestra_sync.sh"; ok=1; n=0
pass() { n=$((n+1)); echo "PASS $*"; }; fail() { n=$((n+1)); echo "FAIL $*"; ok=0; }
chk()  { if eval "$2"; then pass "$1"; else fail "$1   [$2]"; fi; }
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
T="$(mktemp -d)"; export ORCHESTRA_BACKUP_DIR="$T/bk"
HEADC="$(git -C "$REPO" rev-parse HEAD)"
# commit "vecchio": il primo nella storia con un launcher diverso da quello attuale E con almeno un file
# aggiunto dopo (esclusi i file AI_* rigenerati dal bot): serve per costruire collisioni vere.
BASE=""; ADDED=""
for c in $(git -C "$REPO" rev-list HEAD | sed 1d | head -150); do
  [ "$(git -C "$REPO" rev-parse "$c:start_ai_stack.sh" 2>/dev/null)" != "$(git -C "$REPO" rev-parse "HEAD:start_ai_stack.sh")" ] || continue
  a="$(git -C "$REPO" diff --name-only --diff-filter=A "$c" HEAD | grep -v '^AI_' | head -1)"
  [ -n "$a" ] && { BASE="$c"; ADDED="$a"; break; }
done
[ -n "$BASE" ] || { echo "storia insufficiente per il test (clone shallow?)"; exit 0; }
git clone -q --bare "$REPO" "$T/origin.git" && git -C "$T/origin.git" branch -f testbranch "$HEADC"
mk() { rm -rf "$T/$1"; git clone -q "$T/origin.git" "$T/$1" 2>/dev/null; git -C "$T/$1" switch -q -c "$2" "$BASE"; }
sync() { ( cd "$T/$1" && bash "$S" --no-tests "${@:2}" ) > "$T/out.log" 2>&1; echo $?; }

echo "== A. merge andato in conflitto (file con marcatori)"; mk A work
# modifica locale proprio su una riga che il remoto ha cambiato (altrimenti il merge non va in conflitto)
OLDLINE="$(git -C "$REPO" diff -U0 "$BASE" HEAD -- start_ai_stack.sh | grep -m1 '^-[^-]' | cut -c2-)"
python3 - "$T/A/start_ai_stack.sh" "$OLDLINE" <<'PYX'
import sys
p, old = sys.argv[1], sys.argv[2]
s = open(p, encoding="utf-8").read()
assert old in s
open(p, "w", encoding="utf-8").write(s.replace(old, old + "  # LOCALE", 1))
PYX
git -C "$T/A" commit -qam "modifica locale"
git -C "$T/A" merge -q origin/testbranch >/dev/null 2>&1
chk "precondizione: merge in conflitto con marcatori nel launcher" '[ -f "$T/A/.git/MERGE_HEAD" ] && grep -q "^<<<<<<< " "$T/A/start_ai_stack.sh"'
rc=$(sync A testbranch)
chk "esce con 0"                                '[ "$rc" = 0 ]'
chk "annuncia ALLINEAMENTO OK"                  'grep -q "ALLINEAMENTO OK" "$T/out.log"'
chk "su testbranch = remoto"                    '[ "$(git -C "$T/A" rev-parse HEAD)" = "$HEADC" ] && [ "$(git -C "$T/A" rev-parse --abbrev-ref HEAD)" = testbranch ]'
chk "merge in corso annullato"                  '[ ! -f "$T/A/.git/MERGE_HEAD" ]'
chk "nessun marcatore nel launcher"             '! grep -q "^<<<<<<< " "$T/A/start_ai_stack.sh"'
chk "launcher identico al remoto"               '[ "$(git -C "$T/A" hash-object "$T/A/start_ai_stack.sh")" = "$(git -C "$REPO" rev-parse HEAD:start_ai_stack.sh)" ]'
BK=$(ls -d "$T"/bk/*/ | head -1)
chk "backup: bundle con i commit locali"       'git bundle verify "$BK/refs.bundle" >/dev/null 2>&1'
chk "backup: il commit locale e' nel bundle"   'git bundle list-heads "$BK/refs.bundle" 2>/dev/null | grep -q refs/heads/work'

echo "== B. modifiche locali, file non tracciati in collisione, file privati"; mk B main-old; rm -rf "$T/bk"
echo "# modifica locale a mano" >> "$T/B/README.md"
mkdir -p "$(dirname "$T/B/$ADDED")"; echo "contenuto locale" > "$T/B/$ADDED"
echo "copia vecchia" > "$T/B/ollama/pipelines/embedding_utils.py.v01" 2>/dev/null || { mkdir -p "$T/B/ollama/pipelines"; echo "copia vecchia" > "$T/B/ollama/pipelines/embedding_utils.py.v01"; }
echo "SEGRETO=1" > "$T/B/orchestra.env"
rc=$(sync B testbranch)
chk "esce con 0"                                '[ "$rc" = 0 ]'
chk "su testbranch = remoto"                    '[ "$(git -C "$T/B" rev-parse HEAD)" = "$HEADC" ]'
BK=$(ls -d "$T"/bk/*/ | head -1)
chk "il file in collisione e' nel backup con il suo contenuto" '[ "$(cat "$BK/collisioni/$ADDED" 2>/dev/null)" = "contenuto locale" ]'
chk "il file del remoto ha preso il suo posto"  '[ "$(git -C "$T/B" hash-object "$T/B/$ADDED")" = "$(git -C "$REPO" rev-parse HEAD:$ADDED)" ]'
chk "file .v01 non tracciato LASCIATO dov era"  '[ "$(cat "$T/B/ollama/pipelines/embedding_utils.py.v01")" = "copia vecchia" ]'
chk "file privato ignorato (orchestra.env) intatto" '[ "$(cat "$T/B/orchestra.env")" = "SEGRETO=1" ]'
chk "modifica locale al README messa da parte (stash)" 'git -C "$T/B" stash list | grep -q "orchestra-sync" && git -C "$T/B" stash show -p stash@{0} | grep -q "modifica locale a mano"'
chk "backup: patch con la modifica locale"      'grep -q "modifica locale a mano" "$BK/tracked-changes.patch"'
chk "backup: archivio dei file locali"          '[ -s "$BK/files.tgz" ] && tar tzf "$BK/files.tgz" | grep -q "embedding_utils.py.v01"'
rc=$(sync B testbranch)
chk "seconda esecuzione: idempotente (rc 0, nessuna collisione)" '[ "$rc" = 0 ] && grep -q "nessuna collisione" "$T/out.log" && grep -q "ALLINEAMENTO OK" "$T/out.log"'

echo "== B2. file locale IGNORATO in collisione col remoto (git switch lo sovrascriverebbe in silenzio)"; mk B2 main-old; rm -rf "$T/bk"
echo "$ADDED" >> "$T/B2/.git/info/exclude"; mkdir -p "$(dirname "$T/B2/$ADDED")"; echo "dato locale ignorato" > "$T/B2/$ADDED"
chk "precondizione: il file e ignorato da git"  '[ -n "$(git -C "$T/B2" check-ignore "$ADDED")" ]'
rc=$(sync B2 testbranch); BK=$(ls -d "$T"/bk/*/ | head -1)
chk "esce con 0"                                '[ "$rc" = 0 ]'
chk "il file IGNORATO e stato salvato nel backup col suo contenuto" '[ "$(cat "$BK/collisioni/$ADDED" 2>/dev/null)" = "dato locale ignorato" ]'
chk "poi il file del remoto ha preso il suo posto" '[ "$(git -C "$T/B2" hash-object "$T/B2/$ADDED")" = "$(git -C "$REPO" rev-parse HEAD:$ADDED)" ]'

echo "== C. branch locale con commit non pubblicati: nessuna perdita"; mk C testbranch
echo "lavoro locale" > "$T/C/ollama/lavoro.txt"; git -C "$T/C" add ollama/lavoro.txt; git -C "$T/C" commit -qm "lavoro locale non pubblicato"
rc=$(sync C testbranch)
chk "si ferma (rc != 0) con messaggio chiaro"   '[ "$rc" != 0 ] && grep -q "ha commit che il remoto non ha" "$T/out.log"'
chk "il commit locale e' ancora li'"            '[ "$(git -C "$T/C" log -1 --format=%s)" = "lavoro locale non pubblicato" ]'
chk "indica come ripartire dal remoto"          'grep -q "git switch -c testbranch-allineato" "$T/out.log"'

echo "== D. destinazione = tag"; mk D main-old; git -C "$T/origin.git" tag -a t1 "$(git -C "$REPO" rev-parse HEAD~1)" -m t1
rc=$(sync D t1)
chk "esce con 0 e va sul tag (detached)"        '[ "$rc" = 0 ] && [ "$(git -C "$T/D" rev-parse HEAD)" = "$(git -C "$REPO" rev-parse HEAD~1)" ] && [ "$(git -C "$T/D" describe --tags)" = t1 ]'

echo "== E. riferimento sconosciuto"; mk E main-old
rc=$(sync E nonesiste)
chk "rc != 0 e elenca branch e tag disponibili" '[ "$rc" != 0 ] && grep -q "riferimento sconosciuto" "$T/out.log" && grep -q "testbranch" "$T/out.log"'
chk "fuori da un repository: errore chiaro"     '( cd / && bash "$S" 2>&1 | grep -q "Non sei in un repository git" )'

echo "== F. controllo di sintassi: solo file tracciati, nessuna scrittura nell albero"; mk F testbranch >/dev/null 2>&1; git -C "$T/F" switch -q testbranch 2>/dev/null || git -C "$T/F" switch -q -c testbranch origin/testbranch
mkdir -p "$T/F/tests" "$T/F/document-ai/system"; cp "$REPO/tests/check_syntax.sh" "$T/F/tests/"
echo "questo non e python valido" > "$T/F/document-ai/system/vecchia_copia.py"; echo 'if then fi (' > "$T/F/document-ai/system/copia.sh"
( cd "$T/F" && bash tests/check_syntax.sh ) > "$T/syn.log" 2>&1; rc=$?
chk "ignora i file locali non tracciati anche se non validi (rc 0)" '[ $rc = 0 ] && ! grep -q "FALLIT" "$T/syn.log"'
chk "non crea __pycache__ nell albero"          '[ -z "$(find "$T/F" -name __pycache__ -not -path "*/.git/*" | head -1)" ]'
echo "def rotta(:" >> "$T/F/rag/rag_service.py"
( cd "$T/F" && bash tests/check_syntax.sh ) > "$T/syn.log" 2>&1; rc=$?
chk "rileva un file TRACCIATO con sintassi errata (rc 1)" '[ $rc = 1 ] && grep -q "sintassi python FALLITA: rag/rag_service.py" "$T/syn.log"'

echo; echo "$n controlli"; [ $ok = 1 ] && echo "SYNC ALL OK" || echo "SYNC FAILED"; [ $ok = 1 ]
