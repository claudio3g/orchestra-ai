#!/bin/bash
# =============================================================================
# orchestra_diag.sh - report di stato COMPATTO e di SOLA LETTURA, da allegare in chat
# -----------------------------------------------------------------------------
# SCOPO: ridurre il copia-incolla e il consumo di token. Un solo comando scrive un file con:
#   - un RIEPILOGO di ~8 righe (di norma basta incollare solo quello);
#   - i DETTAGLI (git, GPU, container, servizi, modelli caricati, errori recenti), con tetti di righe.
# GARANZIE:
#   - SOLA LETTURA: scrive solo il file di output (piu' una cartella temporanea poi rimossa).
#     Nessun fetch (salvo --fetch), nessun avvio/arresto di servizi, nessuna modifica a git o Docker.
#   - SEGRETI: .orchestra_token e orchestra.env NON vengono mai letti (si dichiara solo se esistono);
#     inoltre l'intero output passa da un filtro che oscura token, chiavi e header Authorization.
#   - COMPATTO: ogni sezione ha un tetto di righe, il file non supera ~160 righe.
#   - ROBUSTO: se nvidia-smi, docker o curl mancano o falliscono lo dichiara e prosegue (esce con 0).
# USO:  bash document-ai/scripts/orchestra_diag.sh [--no-smoke] [--fetch] [--sudo-log] [FILE_DI_OUTPUT]
#   --no-smoke  non esegue orchestra_smoke_test.sh
#   --sudo-log  legge il log del kernel tramite sudo (solo lettura; serve se il tuo utente non lo legge)
#   --fetch     esegue prima "git fetch" per mostrare avanti/indietro rispetto a origin
#               (aggiorna solo i riferimenti remoti, mai i file di lavoro)
# Test: tests/test_diag.sh
# =============================================================================
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${ORCHESTRA_REPO:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
RUN_SMOKE=1; DO_FETCH=0; OUT=""; SUDO=""
for a in "$@"; do
  case "$a" in
    --no-smoke) RUN_SMOKE=0 ;;
    --fetch)    DO_FETCH=1 ;;
    --sudo-log) SUDO="sudo" ;;
    -*)         echo "opzione sconosciuta: $a" >&2; exit 2 ;;
    *)          OUT="$a" ;;
  esac
done
OUT="${OUT:-$HOME/orchestra-diag-$(date +%Y%m%d_%H%M%S).txt}"

RAG_URL="${ORCHESTRA_RAG_URL:-http://127.0.0.1:6335}"
MAIN_URL="${ORCHESTRA_OLLAMA_URL:-http://127.0.0.1:11435}"
AUX_URL="${ORCHESTRA_OLLAMA_AUX_URL:-http://127.0.0.1:11436}"
COMFY_URL="${ORCHESTRA_COMFY_URL:-http://127.0.0.1:8188}"
PIPE_URL="${ORCHESTRA_PIPELINES_URL:-http://127.0.0.1:9099}"
QDRANT_URL="${ORCHESTRA_QDRANT_URL:-http://127.0.0.1:6333}"
CONTAINERS="ai-ollama-session ai-ollama-aux-session ai-qdrant-session ai-pipelines-session ai-webui-session"
SMOKE="${ORCHESTRA_SMOKE_SCRIPT:-$SCRIPT_DIR/orchestra_smoke_test.sh}"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
DET="$TMP/det"; SUM="$TMP/sum"; : > "$DET"; : > "$SUM"

sec()  { printf '\n--- %s ---\n' "$1" >> "$DET"; }
cap()  { head -n "$1"; }                                        # tetto di righe
line() { printf '%-10s: %s\n' "$1" "$2" >> "$SUM"; }            # riga del riepilogo
have() { command -v "$1" >/dev/null 2>&1; }
code() { curl -s -m 3 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || true; }   # vuoto se curl manca o fallisce

# Filtro finale: oscura token, chiavi e header Authorization (difesa in piu', oltre al non leggere i segreti).
redact() {
  sed -E \
    -e 's/github_pat_[A-Za-z0-9_]+/[TOKEN]/g' \
    -e 's/gh[pousr]_[A-Za-z0-9]{20,}/[TOKEN]/g' \
    -e 's/sk-[A-Za-z0-9_-]{16,}/[TOKEN]/g' \
    -e 's/(Bearer|Basic)[[:space:]]+[A-Za-z0-9._~+\/=-]+/\1 [TOKEN]/Ig' \
    -e "s/((KEY|TOKEN|SECRET|PASSWORD|PASSWD|_PAT)[A-Za-z0-9_]*[[:space:]]*[=:][[:space:]]*)[^[:space:],;\"']+/\1[OSCURATO]/Ig"
}

# --- git (nessun fetch senza --fetch) ----------------------------------------
sec "git ($REPO)"
if have git && git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  G() { git -C "$REPO" "$@" 2>/dev/null; }
  [ "$DO_FETCH" = 1 ] && G fetch -q --all --tags
  BR="$(G rev-parse --abbrev-ref HEAD)"; TIP="$(G rev-parse --short HEAD)"
  TAGS="$(G tag --points-at HEAD | paste -sd, -)"
  DIRTY="$(G status --porcelain | wc -l | tr -d ' ')"; STASH="$(G stash list | wc -l | tr -d ' ')"
  AB="$(G rev-list --left-right --count 'HEAD...@{u}' | awk '{print "avanti "$1" / indietro "$2}')"
  line git "branch ${BR:-?} | tip ${TIP:-?} | tag ${TAGS:--} | modifiche locali $DIRTY | stash $STASH | ${AB:-upstream n.d.}"
  {
    G log --oneline -3; echo; G status -sb | cap 12; echo; echo "hash (12 car.) dei file chiave:"
    for f in start_ai_stack.sh ollama/pipelines/orchestra_manifold.py ollama/pipelines/image_loop.py \
             ollama/pipelines/orchestra_bootstrap.py rag/rag_service.py document-ai/scripts/orchestra_gpu_env.sh \
             ollama/Modelfile-orchestra; do
      [ -f "$REPO/$f" ] && printf '%s %s\n' "$(sha256sum "$REPO/$f" | cut -c1-12)" "$f"
    done
  } >> "$DET"
else
  line git "non e' un repository git (REPO=$REPO)"; echo "n.d." >> "$DET"
fi

# --- GPU ---------------------------------------------------------------------
sec "GPU"
if have nvidia-smi && Q="$(nvidia-smi --query-gpu=index,name,memory.used,memory.total,temperature.gpu,power.draw,pstate,pcie.link.width.current,pcie.link.gen.current --format=csv,noheader,nounits 2>/dev/null)" && [ -n "$Q" ]; then
  N="$(echo "$Q" | wc -l | tr -d ' ')"
  S="$(echo "$Q" | awk -F', ' '{n=$2; sub(/NVIDIA GeForce /,"",n); printf "%s%s %s/%s MiB %sC %sW", (NR>1?" | ":""), n, $3, $4, $5, $6}')"
  line gpu "$N NVIDIA: $S"
  {
    echo "idx, nome, usata MiB, totale MiB, temp C, potenza W, pstate, PCIe larghezza, PCIe gen"; echo "$Q"; echo
    echo "processi sulle GPU (uuid, pid, nome, MiB):"
    nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv,noheader 2>/dev/null | cap 10
  } >> "$DET"
else
  line gpu "non disponibile (nvidia-smi assente o non risponde)"; echo "n.d." >> "$DET"
fi
have lspci && { echo; echo "schede video (lspci):"; lspci 2>/dev/null | grep -Ei 'vga|3d|display' | cut -c1-110 | cap 5; } >> "$DET"

# --- eventi hardware nel log del kernel (ultime 24 ore) ------------------------
# Perche: un collegamento Thunderbolt/eGPU che si degrada lascia tracce ore prima della caduta (errori PCIe
# "Correctable", poi "Fatal", poi Xid 79 "GPU has fallen off the bus": vedi incidente del 18 ottobre 2026).
# Qui vengono contati, cosi si vedono in anticipo. Fonti, nell ordine: kern.log, journalctl -k, dmesg (la prima
# che restituisce righe). Con --sudo-log la lettura passa da sudo (solo lettura). "Xid" vale solo se preceduto
# da "NVRM:", per non confonderlo con la scheda di rete (r8169 stampa "XID 641"). Test: tests/test_diag.sh.
sec "eventi hardware nel log del kernel (ultime 24 ore)"
KLOG="${ORCHESTRA_KERNLOG:-/var/log/kern.log}"
CUTOFF="$(date -d '24 hours ago' +%Y-%m-%dT%H:%M 2>/dev/null)"
HW_SRC=""
get_klog() {
  local out
  out="$($SUDO cat "$KLOG" 2>/dev/null)"
  if [ -n "$out" ]; then HW_SRC="kern.log"; printf '%s\n' "$out"; return 0; fi
  if have journalctl; then
    out="$($SUDO journalctl -k -o short-iso --since '24 hours ago' --no-pager -q 2>/dev/null)"
    if [ -n "$out" ]; then HW_SRC="journalctl"; printf '%s\n' "$out"; return 0; fi
  fi
  if have dmesg; then
    out="$($SUDO dmesg --time-format iso 2>/dev/null)"
    if [ -n "$out" ]; then HW_SRC="dmesg"; printf '%s\n' "$out"; return 0; fi
  fi
  return 1
}
if get_klog > "$TMP/klog" && [ -n "$CUTOFF" ]; then
  awk -v cut="$CUTOFF" 'substr($0,1,16) >= cut' "$TMP/klog" \
    | grep -E 'AER:.*error message received from|NVRM: Xid|GSP RM heartbeat timed out|Unable to change power state|kernel: Linux version' > "$TMP/hw" || true
  HWSUM="$(awk '
    /Linux version/ { b++; next }
    /NVRM: Xid/ { if (match($0, /\): [0-9]+/)) x[substr($0, RSTART+3, RLENGTH-3)]++; next }
    /GSP RM heartbeat timed out/ { g++; next }
    /Unable to change power state/ { d++; next }
    /AER:.*error message received from/ {
      dev = "?"; if (match($0, /received from [0-9a-f:.]+/)) { dev = substr($0, RSTART+14, RLENGTH-14); sub(/^0000:/, "", dev) }
      if ($0 ~ /Uncorrectable/) u[dev]++; else c[dev]++
      next }
    END { for (k in c) print "C", k, c[k]; for (k in u) print "U", k, u[k]; for (k in x) print "X", k, x[k]
          if (g) print "G", g, ""; if (d) print "D", d, ""; if (b) print "B", b, "" }' "$TMP/hw" | sort)"
  hwlist() { echo "$HWSUM" | awk -v p="$1" '$1 == p { s = s (s ? ", " : "") $2 " x" $3; t += $3 } END { if (t) printf "%d (%s)", t, s }'; }
  hwnum()  { echo "$HWSUM" | awk -v p="$1" '$1 == p { print $2 }'; }
  C="$(hwlist C)"; U="$(hwlist U)"; X="$(hwlist X)"; G="$(hwnum G)"; D="$(hwnum D)"; B="$(hwnum B)"
  EV=""; add() { EV="$EV${EV:+ | }$1"; }
  [ -n "$C" ] && add "AER corretti $C"; [ -n "$U" ] && add "AER NON corretti $U"; [ -n "$X" ] && add "Xid $X"
  [ -n "$G" ] && add "GSP timeout $G"; [ -n "$D" ] && add "Thunderbolt D3hot $D"
  TAIL=""; [ -n "$B" ] && TAIL=" | avvii $B"; [ -n "$U$X$D" ] && TAIL="$TAIL | ATTENZIONE"
  if [ -z "$EV" ]; then line hardware "nessun evento hardware nelle ultime 24h ($HW_SRC)$TAIL"
  else line hardware "24h ($HW_SRC): $EV$TAIL"; fi
  grep -vE 'Linux version' "$TMP/hw" | tail -n 10 | cut -c1-190 >> "$DET"
else
  line hardware "log del kernel non leggibile (prova --sudo-log)"; echo "n.d." >> "$DET"
fi

# --- container ---------------------------------------------------------------
sec "container"
PS=""
if have docker && PS="$(docker ps --format '{{.Names}}|{{.Status}}|{{.Image}}' 2>/dev/null)"; then
  UP=0; TOT=0
  for c in $CONTAINERS; do TOT=$((TOT+1)); echo "$PS" | cut -d'|' -f1 | grep -qx "$c" && UP=$((UP+1)); done
  line container "$UP/$TOT attivi"
  echo "$PS" | grep -E '^ai-' | cut -c1-120 | cap 8 >> "$DET"
else
  PS=""; line container "non disponibile (docker assente o non risponde)"; echo "n.d." >> "$DET"
fi

# --- servizi e modelli caricati ----------------------------------------------
sec "servizi (codice HTTP)"
SV=""
for e in "ollama-main|$MAIN_URL/api/version" "ollama-aux|$AUX_URL/api/version" "rag|$RAG_URL/health" \
         "pipelines|$PIPE_URL/" "qdrant|$QDRANT_URL/" "comfyui|$COMFY_URL/system_stats"; do
  nm="${e%%|*}"; u="${e#*|}"; c="$(code "$u")"
  case "$c" in 2??|3??) r=OK ;; *) r=KO ;; esac
  SV="$SV${SV:+ | }$nm $r"; echo "$nm $u -> ${c:-n.d.}" >> "$DET"
done
line servizi "$SV"
ps_models() { # $1 = etichetta, $2 = url di Ollama
  curl -s -m 3 "$2/api/ps" 2>/dev/null | python3 -c '
import sys, json
try:
    for m in json.load(sys.stdin).get("models", []):
        print("%s %s %.1f GB VRAM" % (sys.argv[1], m.get("name"), m.get("size_vram", 0) / 1e9))
except Exception:
    pass' "$1" 2>/dev/null | cap 6
}
sec "modelli caricati in VRAM (main / aux)"
{ ps_models main "$MAIN_URL"; ps_models aux "$AUX_URL"; } >> "$DET"

# --- smoke test (opzionale) --------------------------------------------------
sec "smoke test"
if [ "$RUN_SMOKE" = 1 ] && [ -f "$SMOKE" ]; then
  TO=""; have timeout && TO="timeout 120"
  SM="$($TO bash "$SMOKE" 2>&1)"; R="$(echo "$SM" | grep -E 'Risultato:' | tail -n 1)"
  line smoke "${R#*Risultato: }"; [ -z "$R" ] && line smoke "nessun risultato (vedi dettagli)"
  { echo "$R"; echo "$SM" | grep '✘' | cap 10; } >> "$DET"
elif [ "$RUN_SMOKE" = 1 ]; then
  line smoke "script non trovato ($SMOKE)"
else
  line smoke "non eseguito (--no-smoke)"
fi

# --- errori recenti nei log (solo container attivi, max 3 righe ciascuno) ------
sec "errori recenti nei log (ultime 200 righe, max 3 per container)"
NERR=0
if have docker && [ -n "$PS" ]; then
  for c in $CONTAINERS; do
    echo "$PS" | cut -d'|' -f1 | grep -qx "$c" || continue
    E="$(docker logs --tail 200 "$c" 2>&1 | grep -iE 'error|traceback|exception|failed|errore|fallit' | tail -n 3 | cut -c1-200)"
    [ -n "$E" ] && { printf '[%s]\n%s\n' "$c" "$E" >> "$DET"; NERR=$((NERR + $(echo "$E" | wc -l))); }
  done
fi
line errori "$NERR righe di errore recenti nei log (max 3 per container)"

# --- file sensibili: solo presenza, contenuto MAI letto ------------------------
sec "file sensibili (solo presenza, contenuto mai letto)"
P=""
for f in .orchestra_token orchestra.env; do
  if [ -e "$REPO/$f" ]; then P="$P${P:+ | }$f presente"; else P="$P${P:+ | }$f assente"; fi
done
line segreti "$P"; echo "$P" >> "$DET"

# --- assemblaggio: riepilogo in testa, dettagli con tetto, filtro segreti -------
{
  echo "=== RIEPILOGO orchestra_diag v1 - $(date '+%Y-%m-%d %H:%M:%S') ==="
  cat "$SUM"
  echo "=== DETTAGLI (da incollare solo se richiesti) ==="
  head -n 150 "$DET"
} | redact > "$OUT" 2>/dev/null || { echo "impossibile scrivere $OUT" >&2; exit 1; }

sed '/^=== DETTAGLI/q' "$OUT" | sed '$d'
echo "File completo: $OUT  (allegalo solo se serve; di norma basta incollare il riepilogo qui sopra)"
exit 0
