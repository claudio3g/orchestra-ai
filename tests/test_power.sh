#!/bin/bash
# Test di orchestra_power.sh con nvidia-smi/curl simulati (nessuna GPU reale toccata).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
P="$REPO/document-ai/scripts/orchestra_power.sh"; ok=1; n=0
pass() { n=$((n+1)); echo "PASS $*"; }; fail() { n=$((n+1)); echo "FAIL $*"; ok=0; }
chk()  { if eval "$2"; then pass "$1"; else fail "$1   [$2]"; fi; }
T="$(mktemp -d)"; export STUB_STATE="$T/state"; export STUB_BIN="$HERE/helpers/stubs"; mkdir -p "$STUB_STATE"
export PATH="$HERE/helpers/stubs_power:$HERE/helpers/stubs:$PATH"
export ORCHESTRA_POWER_LOG="$T/power.csv"
lim() { cat "$STUB_STATE/limit_GPU-3090-UUID" 2>/dev/null || echo 350; }
reset() { rm -f "$STUB_STATE"/limit_* "$STUB_STATE/pl.log"; unset ORCHESTRA_POWER_MAIN_W ORCHESTRA_POWER_AUX_W FAKE_MIN_3090; }

echo "== status"; reset; out=$(bash "$P" status 2>&1)
chk "elenca la 3090 come main"            'echo "$out" | grep -q "main .*RTX 3090"'
chk "elenca la 4060 come aux"             'echo "$out" | grep -q "aux .*4060"'
chk "mostra limite e default (350)"       'echo "$out" | grep -q "350"'

echo "== profili (percentuale del limite predefinito 350 W)"
reset; bash "$P" profile eco >/dev/null 2>&1;         chk "eco = 70% → 245 W"          '[ "$(lim)" = 245 ]'
reset; bash "$P" profile balanced >/dev/null 2>&1;    chk "balanced = 85% → 298 W"     '[ "$(lim)" = 298 ]'
reset; bash "$P" profile performance >/dev/null 2>&1; chk "performance = 100% → 350 W" '[ "$(lim)" = 350 ]'
reset; FAKE_MIN_3090=280 bash "$P" profile eco >/dev/null 2>&1; chk "limite minimo del driver rispettato (280)" '[ "$(lim)" = 280 ]'
reset; ORCHESTRA_POWER_MAIN_W=260 bash "$P" profile eco >/dev/null 2>&1; chk "override ORCHESTRA_POWER_MAIN_W=260" '[ "$(lim)" = 260 ]'
reset; out=$(bash "$P" profile eco 2>&1); rc=$?
chk "4060 non modificabile: avviso, non errore (rc=0)" '[ $rc = 0 ] && echo "$out" | grep -q "impossibile impostare"'
chk "la 3090 viene comunque impostata"   '[ "$(lim)" = 245 ]'
bash "$P" profile turbo >/dev/null 2>&1; chk "profilo sconosciuto → rc=2" '[ $? = 2 ]'
reset; bash "$P" profile eco >/dev/null 2>&1; bash "$P" restore >/dev/null 2>&1; chk "restore → limite predefinito 350 W" '[ "$(lim)" = 350 ]'

echo "== log e report"; reset; rm -f "$ORCHESTRA_POWER_LOG"
bash "$P" log --interval 5 --count 3 >/dev/null 2>&1
chk "CSV con intestazione"                'head -1 "$ORCHESTRA_POWER_LOG" | grep -q "^timestamp,role,uuid,power_w"'
chk "3 campioni x 2 GPU = 6 righe dati"   '[ "$(tail -n +2 "$ORCHESTRA_POWER_LOG" | wc -l)" = 6 ]'
rep=$(bash "$P" report 2>&1)
chk "report: media main = 210 W (60% di 350)" 'echo "$rep" | grep -E "^main" | grep -q "210.0"'
chk "report: energia main = 0.875 Wh (210 W x 15 s)" 'echo "$rep" | grep -E "^main" | grep -q "0.875"'
chk "report: totale presente"             'echo "$rep" | grep -q "TOTALE energia"'
chk "report senza log → errore chiaro"    '! bash "$P" report /nonesiste 2>&1 | grep -q "TOTALE"'

echo "== benchmark token/joule"; reset
out=$(ORCHESTRA_BENCH_RUNS=2 bash "$P" bench performance eco 2>&1)
perf=$(echo "$out" | awk '$1=="performance"'); eco=$(echo "$out" | awk '$1=="eco"')
chk "riga performance presente"           '[ -n "$perf" ]'
chk "riga eco presente"                   '[ -n "$eco" ]'
tps_p=$(echo "$perf" | awk '{print $3}'); tps_e=$(echo "$eco" | awk '{print $3}')
w_p=$(echo "$perf" | awk '{print $4}');   w_e=$(echo "$eco" | awk '{print $4}')
tj_p=$(echo "$perf" | awk '{print $5}');  tj_e=$(echo "$eco" | awk '{print $5}')
chk "performance: 65 tok/s, 210 W"        '[ "$tps_p" = "65.0" ] && [ "$w_p" = "210.0" ]'
chk "eco: 54.5 tok/s, 147 W"              '[ "$tps_e" = "54.5" ] && [ "$w_e" = "147.0" ]'
chk "eco: meno watt, piu' token per joule" 'awk -v a="$tj_e" -v b="$tj_p" "BEGIN{exit !(a>b)}" && awk -v a="$w_e" -v b="$w_p" "BEGIN{exit !(a<b)}"'
chk "limite originale ripristinato dopo il bench" '[ "$(lim)" = 350 ]'
chk "messaggio di ripristino"             'echo "$out" | grep -q "ripristinato"'
reset; echo 300 > "$STUB_STATE/limit_GPU-3090-UUID"; ORCHESTRA_BENCH_RUNS=1 bash "$P" bench eco >/dev/null 2>&1
chk "bench riporta il limite di PARTENZA (300), non il predefinito" '[ "$(lim)" = 300 ]'

echo; echo "$n controlli"; [ $ok = 1 ] && echo "POWER ALL OK" || echo "POWER FAILED"; [ $ok = 1 ]
