#!/bin/bash
# =====================================================================
# orchestra_power.sh — consumi energetici delle GPU (3090 main + 4060 aux)
#
#   status                  potenza, limiti, P-state, carico e temperatura per GPU
#   profile <p>             imposta il power limit: eco | balanced | performance
#   restore                 ripristina i limiti predefiniti del costruttore
#   log [--interval S] [--count N]   campiona i watt in logs/power.csv (Ctrl-C per fermare)
#   report [file]           potenza media e Wh per GPU dal log
#   bench [p1 p2 ...]       confronta i profili: token/s, watt medi, token per joule
#
# Profili = percentuale del limite PREDEFINITO della GPU, limitata a [min, max] del driver:
#   eco 70% · balanced 85% · performance 100%   (override: ORCHESTRA_POWER_MAIN_W / _AUX_W)
# Nota: la generazione di testo e' limitata dalla banda di memoria, quindi ridurre il limite
# di potenza tende a costare poche prestazioni: va pero' MISURATO con `bench` sulla tua
# macchina prima di adottare un profilo. I limiti non sono persistenti: tornano al
# predefinito al riavvio. Richiede privilegi per -pl (usa `sudo -n`, mai interattivo).
# =====================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/orchestra_gpu_env.sh"
LOG_FILE="${ORCHESTRA_POWER_LOG:-$REPO_ROOT/logs/power.csv}"
OLLAMA_BENCH_URL="${ORCHESTRA_BENCH_URL:-http://127.0.0.1:11435}"
BENCH_MODEL="${ORCHESTRA_BENCH_MODEL:-qwen2.5-coder:14b-instruct-q4_K_M}"
BENCH_RUNS="${ORCHESTRA_BENCH_RUNS:-3}"
BENCH_PROMPT="Scrivi in Python una funzione che calcola i numeri primi fino a N con il crivello di Eratostene, spiegando ogni passaggio."

command -v nvidia-smi >/dev/null 2>&1 || { echo "nvidia-smi non trovato"; exit 1; }
detect_gpu_roles >/dev/null 2>&1

SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo -n"
# q: valore di un campo; toglie solo gli spazi ai bordi (i nomi GPU contengono spazi).
q()   { nvidia-smi -i "$1" --query-gpu="$2" --format=csv,noheader,nounits 2>/dev/null | head -1 | sed 's/^ *//;s/ *$//'; }
uuid_of() { gpu_for_role "$1"; }
# GPU su cui operare: ORCHESTRA_POWER_ROLES (default "main aux"), solo se assegnate.
roles() { local r; for r in main aux; do [ -n "$(uuid_of "$r")" ] || continue
            case " ${ORCHESTRA_POWER_ROLES:-main aux} " in *" $r "*) echo "$r";; esac; done; }

pct_of() { case "$1" in eco) echo 70;; balanced) echo 85;; performance|default) echo 100;; *) echo ""; esac; }

# calc_limit <uuid> <percentuale>  → watt interi, limitati a [min,max]
calc_limit() {
    local def min max
    def=$(q "$1" power.default_limit); min=$(q "$1" power.min_limit); max=$(q "$1" power.max_limit)
    awk -v d="$def" -v p="$2" -v mn="$min" -v mx="$max" 'BEGIN{w=int(d*p/100+0.5); if(w<mn)w=mn; if(w>mx)w=mx; print w}'
}

# set_limit <ruolo> <uuid> <watt>  → 0 se ok; non fatale se la GPU non lo supporta (laptop)
set_limit() {
    local role="$1" uuid="$2" watt="$3" cur
    cur=$(q "$uuid" power.limit)
    if $SUDO nvidia-smi -i "$uuid" -pl "$watt" >/dev/null 2>&1; then
        echo "  ${role}: power limit ${cur} W → ${watt} W"; return 0
    fi
    echo "  ${role}: impossibile impostare ${watt} W (GPU laptop con limite bloccato, o privilegi mancanti: riprova con sudo)" >&2
    return 1
}

cmd_status() {
    printf '%-5s %-30s %8s %8s %8s %-6s %5s %6s %9s\n' ruolo gpu "W ora" "limite" "default" pstate "util%" "temp" "mem MiB"
    for r in $(roles); do u=$(uuid_of "$r")
        printf '%-5s %-30s %8s %8s %8s %-6s %5s %6s %9s\n' "$r" "$(q "$u" name | cut -c1-30)" \
            "$(q "$u" power.draw)" "$(q "$u" power.limit)" "$(q "$u" power.default_limit)" \
            "$(q "$u" pstate)" "$(q "$u" utilization.gpu)" "$(q "$u" temperature.gpu)" "$(q "$u" memory.used)"
    done
}

cmd_profile() {
    local p="${1:-}" pct; pct=$(pct_of "$p")
    [ -n "$pct" ] || { echo "Profilo sconosciuto '${p}': usa eco | balanced | performance"; return 2; }
    echo "Profilo ${p} (${pct}% del limite predefinito):"
    local r u w ov rc=0
    for r in $(roles); do u=$(uuid_of "$r")
        ov="ORCHESTRA_POWER_$(echo "$r" | tr a-z A-Z)_W"; w="${!ov:-}"
        [ -n "$w" ] || w=$(calc_limit "$u" "$pct")
        set_limit "$r" "$u" "$w" || rc=0   # la GPU non modificabile non e' un errore globale
    done
    return $rc
}

cmd_restore() { echo "Ripristino i limiti predefiniti:"; for r in $(roles); do u=$(uuid_of "$r"); set_limit "$r" "$u" "$(q "$u" power.default_limit)" || true; done; }

cmd_log() {
    local interval=5 count=0 n=0 r u ts
    while [ $# -gt 0 ]; do case "$1" in --interval) interval="$2"; shift 2;; --count) count="$2"; shift 2;; *) shift;; esac; done
    mkdir -p "$(dirname "$LOG_FILE")"
    [ -s "$LOG_FILE" ] || echo "timestamp,role,uuid,power_w,limit_w,util_pct,mem_used_mib,pstate,temp_c,interval_s" > "$LOG_FILE"
    echo "Campionamento ogni ${interval}s su ${LOG_FILE} (Ctrl-C per fermare)"
    while true; do
        ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        for r in $(roles); do u=$(uuid_of "$r")
            echo "${ts},${r},${u},$(q "$u" power.draw),$(q "$u" power.limit),$(q "$u" utilization.gpu),$(q "$u" memory.used),$(q "$u" pstate),$(q "$u" temperature.gpu),${interval}" >> "$LOG_FILE"
        done
        n=$((n+1)); [ "$count" -gt 0 ] && [ "$n" -ge "$count" ] && break
        sleep "$interval"
    done
}

cmd_report() {
    local f="${1:-$LOG_FILE}"
    [ -s "$f" ] || { echo "Nessun log in ${f}: esegui prima 'log'"; return 1; }
    awk -F, 'NR>1 { n[$2]++; s[$2]+=$4; wh[$2]+=$4*$10/3600; if($4>mx[$2])mx[$2]=$4; if(first==""||$1<first)first=$1; if($1>last)last=$1 }
      END { printf "Periodo: %s → %s\n%-6s %8s %10s %10s %12s\n","" first, last, "ruolo","campioni","media W","picco W","energia Wh";
            tot=0; for(r in n){ printf "%-6s %8d %10.1f %10.1f %12.3f\n", r, n[r], s[r]/n[r], mx[r], wh[r]; tot+=wh[r] }
            printf "TOTALE energia: %.3f Wh\n", tot }' "$f"
}

# media dei watt di un file di campioni (uno per riga)
mean_w() { awk '{s+=$1;n++} END{ if(n) printf "%.1f", s/n; else print 0 }' "$1"; }

cmd_bench() {
    local profiles=("$@"); [ ${#profiles[@]} -gt 0 ] || profiles=(current)
    local u; u=$(uuid_of main); [ -n "$u" ] || { echo "GPU main non assegnata"; return 1; }
    local orig; orig=$(q "$u" power.limit)
    echo "Benchmark: modello ${BENCH_MODEL} su ${OLLAMA_BENCH_URL}, ${BENCH_RUNS} esecuzioni per profilo (GPU main)"
    printf '%-12s %8s %9s %9s %10s\n' profilo "limite W" "tok/s" "media W" "tok/joule"
    # Ripristino garantito anche con Ctrl-C o errori. Le variabili sono espanse ORA (trap tra
    # doppi apici): $u e $orig sono locali e a fine funzione non esisterebbero piu'.
    trap "$SUDO nvidia-smi -i '$u' -pl '$orig' >/dev/null 2>&1" EXIT
    local p lim run tps_all samples out tps avg
    for p in "${profiles[@]}"; do
        # il benchmark misura la GPU main: non toccare l'aux
        [ "$p" = current ] || ORCHESTRA_POWER_ROLES=main cmd_profile "$p" >/dev/null 2>&1 || true
        lim=$(q "$u" power.limit); samples=$(mktemp); tps_all=$(mktemp)
        # riscaldamento: carica il modello (non misurato)
        curl -s "${OLLAMA_BENCH_URL}/api/generate" -d "{\"model\":\"${BENCH_MODEL}\",\"prompt\":\"ok\",\"stream\":false,\"options\":{\"num_predict\":1}}" >/dev/null
        for run in $(seq 1 "$BENCH_RUNS"); do
            q "$u" power.draw >> "$samples"
            out=$(curl -s "${OLLAMA_BENCH_URL}/api/generate" -d "{\"model\":\"${BENCH_MODEL}\",\"prompt\":\"${BENCH_PROMPT}\",\"stream\":false,\"options\":{\"num_predict\":256,\"temperature\":0}}")
            q "$u" power.draw >> "$samples"
            echo "$out" | python3 -c 'import sys,json
d=json.load(sys.stdin); print(d["eval_count"]/(d["eval_duration"]/1e9))' >> "$tps_all" 2>/dev/null
        done
        tps=$(sort -n "$tps_all" | awk '{a[NR]=$1} END{ if(NR) printf "%.1f", a[int((NR+1)/2)]; else print 0 }')
        avg=$(mean_w "$samples")
        printf '%-12s %8s %9s %9s %10s\n' "$p" "$lim" "$tps" "$avg" "$(awk -v t="$tps" -v w="$avg" 'BEGIN{ if(w>0) printf "%.3f", t/w; else print "n/d" }')"
        rm -f "$samples" "$tps_all"
    done
    $SUDO nvidia-smi -i "$u" -pl "$orig" >/dev/null 2>&1 && trap - EXIT
    echo "Limite originale (${orig} W) ripristinato."
}

case "${1:-status}" in
    status)  cmd_status ;;
    profile) shift; cmd_profile "$@" ;;
    restore) cmd_restore ;;
    log)     shift; cmd_log "$@" ;;
    report)  shift; cmd_report "$@" ;;
    bench)   shift; cmd_bench "$@" ;;
    *) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
