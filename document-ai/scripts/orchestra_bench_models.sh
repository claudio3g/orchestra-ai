#!/bin/bash
# =====================================================================
# orchestra_bench_models.sh — velocita' e concorrenza dei modelli su un backend Ollama
#
# Serve a decidere CON DATI MISURATI tra "un solo agente forte" e "piu' agenti in parallelo":
# per ogni modello misura la velocita' di un singolo flusso e il throughput AGGREGATO con N
# richieste simultanee sullo stesso modello (i pesi sono letti una volta sola, ogni slot ha la
# sua cache KV). Mostra anche se il modello sta tutto in GPU e la VRAM usata.
#
# Uso:   bash orchestra_bench_models.sh [opzioni] MODELLO [MODELLO...]
#   --role main|aux     backend da misurare (default main = 3090, porta 11435; aux = 4060, 11436)
#   --url URL           URL Ollama alternativo
#   --tokens N          token generati per richiesta (default 200)
#   --parallel "1 2 4"  livelli di concorrenza (default "1 2")
# Esempio: bash orchestra_bench_models.sh --parallel "1 2 3" qwen3.6:27b qwen2.5-coder:14b-instruct-q4_K_M
#
# Nota: per vedere uno scaling reale l'istanza Ollama deve avere OLLAMA_NUM_PARALLEL >= N
# (launcher: ORCHESTRA_MAIN_PARALLEL / ORCHESTRA_AUX_PARALLEL). Ollama fissa a 1 slot alcune
# architetture (multimodali, ad attenzione ricorrente): se lo speedup resta ~1.0x e' un
# indizio che il modello non scala in parallelo su Ollama. Lo script scarica il modello a fine misura.
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/orchestra_gpu_env.sh"
ROLE=main; URL=""; TOKENS=200; PARS="1 2"; MODELS=()
while [ $# -gt 0 ]; do case "$1" in
    --role) ROLE="$2"; shift 2;; --url) URL="$2"; shift 2;; --tokens) TOKENS="$2"; shift 2;;
    --parallel) PARS="$2"; shift 2;; -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) MODELS+=("$1"); shift;; esac; done
[ ${#MODELS[@]} -gt 0 ] || { echo "Uso: $0 [--role main|aux] [--parallel \"1 2 4\"] MODELLO [MODELLO...]  (-h per l'aiuto)"; exit 2; }
case "$ROLE" in
    aux)  URL="${URL:-http://127.0.0.1:11436}"; CONT="${OLLAMA_AUX_CONTAINER:-ai-ollama-aux-session}";;
    main) URL="${URL:-http://127.0.0.1:11435}"; CONT="${OLLAMA_CONTAINER:-ai-ollama-session}";;
    *) echo "Ruolo non valido: $ROLE (main|aux)"; exit 2;;
esac
command -v nvidia-smi >/dev/null 2>&1 && detect_gpu_roles >/dev/null 2>&1
UUID="$(gpu_for_role "$ROLE")"
mem_used() { [ -n "$UUID" ] && nvidia-smi -i "$UUID" --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' '; }
gen() { # <modello> <json options extra> <keep_alive> <prompt>
    curl -s -m 900 "$URL/api/generate" -d "{\"model\":\"$1\",\"prompt\":\"$4\",\"stream\":false,\"keep_alive\":$3,\"options\":{$2}}"; }

NP="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$CONT" 2>/dev/null | sed -n 's/^OLLAMA_NUM_PARALLEL=//p')"
echo "Backend: ${ROLE} (${URL}) · contenitore ${CONT} · OLLAMA_NUM_PARALLEL=${NP:-?} · ${TOKENS} token/richiesta · GPU ${UUID:-?}"
printf '%-36s %3s %10s %10s %8s %9s  %s\n' modello N "tok/s flusso" "tok/s totale" speedup "VRAM MiB" processore
rc=0
for M in "${MODELS[@]}"; do
    gen "$M" '"num_predict":1' '"10m"' "ok" >/dev/null 2>&1
    PROC="$(docker exec "$CONT" ollama ps 2>/dev/null | awk -v m="$M" '$1==m' | grep -Eo '[0-9]+% (GPU|CPU)|[0-9]+%/[0-9]+% CPU/GPU' | head -1)"
    VRAM="$(mem_used)"
    base=""
    for N in $PARS; do
        [ -n "$NP" ] && [ "$N" -gt "$NP" ] && echo "  ATTENZIONE: N=${N} > OLLAMA_NUM_PARALLEL=${NP}: le richieste in eccesso vengono messe in coda (alza ORCHESTRA_*_PARALLEL)" >&2
        T="$(mktemp -d)"
        for i in $(seq 1 "$N"); do
            ( gen "$M" "\"num_predict\":${TOKENS},\"temperature\":0" '"10m"' "Scrivi una funzione Python che ordina una lista (variante ${i}) e spiegala passo per passo." > "$T/$i.json" ) &
        done
        wait
        ROW="$(python3 - "$T" <<'PY'
import glob, json, sys
n = 0; tot = 0; per = []; durs = []
for f in glob.glob(sys.argv[1] + "/*.json"):
    try:
        d = json.load(open(f)); c = d["eval_count"]; e = d["eval_duration"] / 1e9; t = d["total_duration"] / 1e9
    except Exception:
        continue
    n += 1; tot += c; per.append(c / e); durs.append(t)
if n == 0: print("ERR")
else: print(f"{sum(per)/n:.1f} {tot/max(durs):.1f} {n}")
PY
)"
        rm -rf "$T"
        if [ "$ROW" = "ERR" ]; then
            printf '%-36s %3s %10s %10s %8s %9s  %s\n' "$M" "$N" "errore" "errore" "-" "${VRAM:--}" "${PROC:--}"; rc=1; continue
        fi
        per=$(echo "$ROW" | awk '{print $1}'); agg=$(echo "$ROW" | awk '{print $2}')
        [ -z "$base" ] && base="$agg"
        printf '%-36s %3s %10s %10s %7sx %9s  %s\n' "$M" "$N" "$per" "$agg" "$(awk -v a="$agg" -v b="$base" 'BEGIN{printf "%.2f", a/b}')" "${VRAM:--}" "${PROC:--}"
    done
    case "$PROC" in *CPU*) echo "  ATTENZIONE: ${M} NON e' al 100% in GPU (${PROC}): parte dei layer e' su CPU, le prestazioni calano molto";; esac
    gen "$M" '' 0 "" >/dev/null 2>&1      # scarica il modello: la misura successiva parte pulita
done
exit $rc
