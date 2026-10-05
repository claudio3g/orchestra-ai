#!/bin/bash
# =====================================================================
# orchestra_sync.sh — allinea in modo SICURO la cartella locale al remoto (branch o tag)
#
# Uso (dalla cartella del repository):   bash document-ai/scripts/orchestra_sync.sh [riferimento]
#   riferimento   branch (default: dual-gpu-final) oppure tag (es. dual-gpu-rc2, pre-dual-gpu)
#   --repo DIR    repository da allineare (default: quello in cui ti trovi)
#   --no-tests    salta la suite di test finale
#
# Cosa fa, in ordine, SENZA perdere nulla:
#   1. salva un backup in ~/orchestra-backup-locale/<data>/: stato, modifiche, tutti i file locali
#      modificati o non tracciati e un bundle git con tutti i riferimenti locali;
#   2. annulla un merge/rebase/cherry-pick rimasto a meta (e' la causa tipica di un file con
#      marcatori <<<<<<< che sembra "corrotto"; il backup contiene lo stato con i marcatori);
#   3. mette da parte (stash) le modifiche locali ai file tracciati e sposta in backup i file non
#      tracciati che collidono con quelli del remoto; gli altri file locali NON vengono toccati
#      (copie in document-ai/system, .v01, orchestra.env, segreti...);
#   4. scarica il remoto e passa al riferimento richiesto (rifiuta se il branch locale ha commit
#      che il remoto non ha);
#   5. verifica: commit uguale al remoto, nessun marcatore di conflitto, sintassi di tutti gli
#      script bash/python, start_ai_stack.sh identico al remoto, permessi eseguibili, test.
# Non fa mai push, reset --hard o cancellazioni.
# =====================================================================
REF="dual-gpu-final"; RUN_TESTS=1; REPO=""
while [ $# -gt 0 ]; do case "$1" in
    --repo) REPO="$2"; shift 2;; --no-tests) RUN_TESTS=0; shift;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) REF="$1"; shift;; esac; done
if [ -n "$REPO" ]; then cd "$REPO" || { echo "cartella non trovata: $REPO"; exit 1; }; fi
TOP="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -n "$TOP" ] || { echo "Non sei in un repository git: spostati in ~/ai-sessioni oppure usa --repo"; exit 1; }
cd "$TOP" || exit 1
GD="$(git rev-parse --git-dir)"; TS="$(date +%Y%m%d_%H%M%S)"
BK="${ORCHESTRA_BACKUP_DIR:-$HOME/orchestra-backup-locale}/$TS"; mkdir -p "$BK"
FAIL=0
okc()  { echo "  ✔ $*"; }
badc() { echo "  ✘ $*"; FAIL=1; }

echo "== 1. Stato locale =="
echo "  repository: $TOP"
echo "  branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD 2>/dev/null)"
git status --short > "$BK/status.txt"; echo "  modifiche locali: $(wc -l < "$BK/status.txt") voci"
git diff HEAD > "$BK/tracked-changes.patch" 2>/dev/null
git bundle create "$BK/refs.bundle" --all >/dev/null 2>&1
git ls-files -m -o -u --exclude-standard | sort -u > "$BK/files.list"
if [ -s "$BK/files.list" ]; then tar czf "$BK/files.tgz" -T "$BK/files.list" 2>/dev/null; fi
echo "  backup: $BK"

echo "== 2. Operazioni rimaste a meta =="
INPROG=0
if [ -f "$GD/MERGE_HEAD" ];       then echo "  merge in corso: lo annullo (il backup contiene lo stato attuale)"; git merge --abort 2>/dev/null || git reset -q --merge; INPROG=1; fi
if [ -d "$GD/rebase-merge" ] || [ -d "$GD/rebase-apply" ]; then echo "  rebase in corso: lo annullo"; git rebase --abort 2>/dev/null; INPROG=1; fi
if [ -f "$GD/CHERRY_PICK_HEAD" ]; then echo "  cherry-pick in corso: lo annullo"; git cherry-pick --abort 2>/dev/null; INPROG=1; fi
[ $INPROG = 0 ] && echo "  nessuna"
if [ -n "$(git ls-files -u)" ]; then echo "  file ancora in conflitto: ripristino l'indice"; git reset -q; fi

echo "== 3. Modifiche locali ai file tracciati =="
if ! git diff --quiet HEAD 2>/dev/null; then
    git stash push -q -m "orchestra-sync $TS" && echo "  messe da parte: $(git stash list | head -1)"
    echo "  per rivederle:  git stash show -p stash@{0}     (non vengono riapplicate in automatico)"
else echo "  nessuna"; fi

echo "== 4. Allineamento a '$REF' =="
git fetch --all --tags --prune -q 2>&1 | sed 's/^/  /'
if git rev-parse -q --verify "refs/tags/$REF" >/dev/null; then KIND=tag; TARGET="refs/tags/$REF"
elif git rev-parse -q --verify "refs/remotes/origin/$REF" >/dev/null; then KIND=branch; TARGET="origin/$REF"
else echo "  riferimento sconosciuto: $REF"; echo "  branch: $(git branch -r | sed 's|origin/||;s/ //g;/HEAD/d' | tr '\n' ' ')"; echo "  tag: $(git tag | tr '\n' ' ')"; exit 1; fi
COLL="$BK/collisioni"; ncoll=0
while IFS= read -r f; do
    if git cat-file -e "$TARGET:$f" 2>/dev/null; then mkdir -p "$COLL/$(dirname "$f")"; mv "$f" "$COLL/$f"; ncoll=$((ncoll+1)); echo "  spostato in backup (collideva col remoto): $f"; fi
done < <(git ls-files --others --exclude-standard)
[ $ncoll = 0 ] && echo "  nessuna collisione di file non tracciati"
if [ "$KIND" = tag ]; then
    git switch -q --detach "$TARGET" && echo "  su tag $REF (detached, sola lettura)"
else
    if git show-ref -q --verify "refs/heads/$REF"; then
        git switch -q "$REF" || { echo "  switch fallito"; exit 1; }
        if ! git merge --ff-only -q "$TARGET" 2>/dev/null; then
            echo "  ✘ il branch locale '$REF' ha commit che il remoto non ha (o e' divergente): mi fermo."
            echo "    Nulla e' stato perso: $BK/refs.bundle contiene tutti i commit locali."
            echo "    Per ripartire dal remoto in un nuovo branch:  git switch -c ${REF}-allineato --track $TARGET"
            exit 1
        fi
    else
        git switch -q -c "$REF" --track "$TARGET" || { echo "  switch fallito"; exit 1; }
    fi
    echo "  su branch $REF = $TARGET"
fi

echo "== 5. Verifiche =="
[ "$(git rev-parse HEAD)" = "$(git rev-parse "$TARGET^{commit}")" ] && okc "commit locale = remoto ($(git rev-parse --short HEAD))" || badc "il commit locale non coincide col remoto"
if git grep -q -I -E '^(<<<<<<< |>>>>>>> )' -- ':!*.md' ':!AI_CTX_*' 2>/dev/null; then badc "marcatori di conflitto presenti:"; git grep -n -I -E '^(<<<<<<< |>>>>>>> )' -- ':!*.md' ':!AI_CTX_*' | head -5; else okc "nessun marcatore di conflitto nei file tracciati"; fi
bad=0; while IFS= read -r f; do bash -n "$f" 2>/dev/null || { badc "sintassi bash non valida: $f"; bad=1; }; done < <(git ls-files '*.sh')
[ $bad = 0 ] && okc "sintassi bash valida in tutti gli script ($(git ls-files '*.sh' | wc -l))"
if command -v python3 >/dev/null; then bad=0
    while IFS= read -r f; do python3 -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" "$f" 2>/dev/null || { badc "sintassi python non valida: $f"; bad=1; }; done < <(git ls-files '*.py')
    [ $bad = 0 ] && okc "sintassi python valida in tutti i file ($(git ls-files '*.py' | wc -l))"; fi
for L in start_ai_stack.sh start_comfyui.sh; do
    [ -f "$L" ] || continue
    [ "$(git hash-object "$L")" = "$(git rev-parse "$TARGET:$L")" ] && okc "$L identico al remoto ($(wc -l < "$L") righe)" || badc "$L diverso dal remoto"
    [ -x "$L" ] || { chmod +x "$L" && okc "$L reso eseguibile"; }
done
if [ "$RUN_TESTS" = 1 ] && [ -f tests/run_all.sh ]; then
    if python3 -c "import flask, pydantic, requests" 2>/dev/null; then
        res="$(bash tests/run_all.sh 2>&1 | tail -3)"; echo "$res" | grep -q "TUTTI I TEST OK" && okc "suite di test: TUTTI I TEST OK" || { badc "suite di test fallita:"; echo "$res" | sed 's/^/      /'; }
    else echo "  - test saltati: servono flask, pydantic, requests (pip install flask pydantic requests)"; fi
fi
echo; if [ $FAIL = 0 ]; then echo "ALLINEAMENTO OK — backup in $BK"; else echo "ALLINEAMENTO CON PROBLEMI — vedi sopra; backup in $BK"; fi
[ $FAIL = 0 ]
