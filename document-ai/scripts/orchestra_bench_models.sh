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
#   --ctx N             contesto (num_ctx) di ogni richiesta (default 8192 = quello del manifold)
#   --free-comfy        svuota ComfyUI (/free) prima di misurare: SDXL tiene circa 7 GB di VRAM dopo l uso e
#                       un modello grande (es. 27B da 18 GB) finisce in parte su CPU (misura falsata)
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
FREE_COMFY=0; COMFY_URL="${ORCHESTRA_COMFY_URL:-http://127.0.0.1:8188}"
ROLE=main; URL=""; TOKENS=200; PARS="1 2"; MODELS=(); CTX="${ORCHESTRA_CONTEXT_LENGTH:-8192}"
while [ $# -gt 0 ]; do case "$1" in
    --role) ROLE="$2"; shift 2;; --url) URL="$2"; shift 2;; --tokens) TOKENS="$2"; shift 2;;
    --parallel) PARS="$2"; shift 2;; --ctx) CTX="$2"; shift 2;; --free-comfy) FREE_COMFY=1; shift;; -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
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
echo "Backend: ${ROLE} (${URL}) · contenitore ${CONT} · OLLAMA_NUM_PARALLEL=${NP:-?} · contesto ${CTX} · ${TOKENS} token/richiesta · GPU ${UUID:-?}"
echo "Ollama: $(docker exec "$CONT" ollama --version 2>/dev/null | tail -1)"
printf '%-36s %3s %10s %10s %8s %9s  %s\n' modello N "tok/s flusso" "tok/s totale" speedup "VRAM MiB" processore
rc=0
if [ "$FREE_COMFY" = 1 ]; then
    curl -s -X POST "$COMFY_URL/free" -H 'Content-Type: application/json' -d '{"unload_models":true,"free_memory":true}' >/dev/null 2>&1
    sleep 2; echo "ComfyUI svuotato (/free)"
fi
for M in "${MODELS[@]}"; do
    # Il modello deve esistere sul backend: altrimenti un errore chiaro con il comando per scaricarlo.
    if command -v docker >/dev/null 2>&1 && docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${CONT}$"; then
        if ! ollama_has_model "$CONT" "$M"; then
            echo "  ✘ ${M}: non installato su ${ROLE}. Scaricalo con: docker exec ${CONT} ollama pull ${M}" >&2
            echo "    (se risponde 412 'requires a newer version of Ollama': aggiorna l immagine con ORCHESTRA_PULL_IMAGES=1 bash start_ai_stack.sh)" >&2
            rc=1; continue
        fi
    fi
    PRE="$(mem_used)"
    if [ "${PRE:-0}" -gt 2000 ]; then
        echo "  ATTENZIONE: la GPU ha gia ${PRE} MiB occupati prima di caricare ${M} (ComfyUI con SDXL in VRAM?): un modello grande può finire in parte su CPU. Riprova con --free-comfy" >&2
    fi
    gen "$M" "\"num_ctx\":${CTX},\"num_predict\":1" '"10m"' "ok" >/dev/null 2>&1
    PROC_LINE="$(docker exec "$CONT" ollama ps 2>/dev/null | awk -v m="$M" '$1==m')"
    PROC="$(docker exec "$CONT" ollama ps 2>/dev/null | awk -v m="$M" '$1==m' | grep -Eo '[0-9]+% (GPU|CPU)|[0-9]+%/[0-9]+% CPU/GPU' | head -1)"
    VRAM="$(mem_used)"
    base=""
    for N in $PARS; do
        [ -n "$NP" ] && [ "$N" -gt "$NP" ] && echo "  ATTENZIONE: N=${N} > OLLAMA_NUM_PARALLEL=${NP}: le richieste in eccesso vengono messe in coda (alza ORCHESTRA_*_PARALLEL)" >&2
        T="$(mktemp -d)"
        for i in $(seq 1 "$N"); do
            ( gen "$M" "\"num_ctx\":${CTX},\"num_predict\":${TOKENS},\"temperature\":0" '"10m"' "Scrivi una funzione Python che ordina una lista (variante ${i}) e spiegala passo per passo." > "$T/$i.json" ) &
        done
        wait
        ROW="$(python3 - "$T" <<'PY'
import glob, json, sys
n = 0; tot = 0; per = []; durs = []; err = ""
for f in glob.glob(sys.argv[1] + "/*.json"):
    try:
        d = json.load(open(f))
        if "error" in d:
            err = str(d["error"])[:90]; continue
        c = d["eval_count"]; e = d["eval_duration"] / 1e9; t = d["total_duration"] / 1e9
    except Exception:
        continue
    n += 1; tot += c; per.append(c / e); durs.append(t)
if n == 0: print("ERR " + err)
else: print(f"{sum(per)/n:.1f} {tot/max(durs):.1f} {n}")
PY
)"
        rm -rf "$T"
        if [ "${ROW%% *}" = "ERR" ]; then
            [ -n "${ROW#ERR}" ] && echo "  Ollama risponde:${ROW#ERR}" >&2
            printf '%-36s %3s %10s %10s %8s %9s  %s\n' "$M" "$N" "errore" "errore" "-" "${VRAM:--}" "${PROC:--}"; rc=1; continue
        fi
        per=$(echo "$ROW" | awk '{print $1}'); agg=$(echo "$ROW" | awk '{print $2}')
        [ -z "$base" ] && base="$agg"
        printf '%-36s %3s %10s %10s %7sx %9s  %s\n' "$M" "$N" "$per" "$agg" "$(awk -v a="$agg" -v b="$base" 'BEGIN{printf "%.2f", a/b}')" "${VRAM:--}" "${PROC:--}"
    done
    [ -n "$PROC_LINE" ] && echo "  ollama ps: ${PROC_LINE}"
    case "$PROC" in *CPU*) echo "  ATTENZIONE: ${M} NON e' al 100% in GPU (${PROC}) con contesto ${CTX} e ${NP:-?} richieste parallele: parte dei layer e' su CPU, le prestazioni calano molto. Riduci --ctx o OLLAMA_NUM_PARALLEL (la cache KV cresce di contesto x parallelo)";; esac
    gen "$M" '' 0 "" >/dev/null 2>&1      # scarica il modello: la misura successiva parte pulita
done
exit $rc
