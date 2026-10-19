#!/bin/bash
# Test di orchestra_diag.sh: report COMPATTO, di SOLA LETTURA, senza segreti.
# Tutto gira in un ambiente isolato (HOME, repo git finto, stub di nvidia-smi/docker/curl/lspci):
# non tocca il sistema reale. Motivo del tool: meno copia-incolla e meno token in chat.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
D="$REPO/document-ai/scripts/orchestra_diag.sh"; ok=1; n=0; ORIG_PATH="$PATH"
pass() { n=$((n+1)); echo "PASS $*"; }; fail() { n=$((n+1)); echo "FAIL $*"; ok=0; }
chk()  { if eval "$2"; then pass "$1"; else fail "$1   [$2]"; fi; }

mk() { # ambiente isolato con segreti finti, che lo script NON deve mai leggere ne stampare
  T="$(mktemp -d)"; export HOME="$T/home"; mkdir -p "$HOME" "$T/bin" "$T/repo"
  ( cd "$T/repo" && git init -q && git config user.email t@t && git config user.name t \
    && echo a > start_ai_stack.sh && git add start_ai_stack.sh && git commit -qm init )
  echo "SEGRETO_TOKEN_123" > "$T/repo/.orchestra_token"
  echo "ORCHESTRA_PAT=ghp_SEGRETOSEGRETOSEGRETO123456" > "$T/repo/orchestra.env"
  cat > "$T/bin/nvidia-smi" <<'EOS'
#!/bin/bash
case "$*" in
  *query-compute-apps*) echo "GPU-3090-UUID, 19711, python, 7002 MiB";;
  *query-gpu*) echo "0, NVIDIA GeForce RTX 4060 Laptop GPU, 12, 8188, 48, 2.37, P8, 8, 1"
               echo "1, NVIDIA GeForce RTX 3090, 7121, 24576, 46, 32.25, P8, 4, 1";;
  *) echo tabella;;
esac
EOS
  cat > "$T/bin/docker" <<'EOS'
#!/bin/bash
case "$1" in
  ps) for c in ai-ollama-session ai-ollama-aux-session ai-qdrant-session ai-pipelines-session ai-webui-session; do echo "$c|Up 2 hours|img"; done;;
  logs) for i in $(seq 1 300); do echo "ERROR connessione fallita $i Authorization: Bearer abcDEF123456SECRET token=github_pat_11ZZZZ_valoresegreto"; done;;
esac
EOS
  cat > "$T/bin/curl" <<'EOS'
#!/bin/bash
case "$*" in
  *"-w"*) echo 200;;
  *api/ps*) echo '{"models":[{"name":"qwen2.5-coder:32b","size_vram":20000000000}]}';;
  *) echo '{}';;
esac
EOS
  cat > "$T/bin/lspci" <<'EOS'
#!/bin/bash
echo "00:02.0 VGA compatible controller: Intel Corporation Raptor Lake-S UHD Graphics"
echo "3b:00.0 VGA compatible controller: NVIDIA Corporation GA102 [GeForce RTX 3090]"
EOS
  printf '#!/bin/bash\nexit 1\n' > "$T/bin/journalctl"; cp "$T/bin/journalctl" "$T/bin/dmesg"
  printf '#!/bin/bash\necho "Risultato: 18 ok, 0 falliti"\n' > "$T/smoke.sh"
  chmod +x "$T/bin/"* "$T/smoke.sh"
  export PATH="$T/bin:$ORIG_PATH" ORCHESTRA_REPO="$T/repo" ORCHESTRA_SMOKE_SCRIPT="$T/smoke.sh" ORCHESTRA_KERNLOG="$T/kern.log"
}
snap() { ( cd "$T/repo" && git status --porcelain; git rev-parse HEAD; find . -type f -not -path './.git/*' | sort | xargs sha256sum ); }

echo "== scenario sano"; mk; before="$(snap)"
out="$(bash "$D" "$T/out.txt" 2>&1)"; rc=$?; after="$(snap)"; O="$T/out.txt"
chk "esce con 0"                                   '[ $rc = 0 ]'
chk "crea il file di output non vuoto"             '[ -s "$O" ]'
chk "il RIEPILOGO e la prima riga"                 'head -n 1 "$O" | grep -q RIEPILOGO'
chk "riepilogo git: branch e tip"                  'grep -qE "^git +: branch .* \| tip [0-9a-f]+" "$O"'
chk "riepilogo GPU: 2 NVIDIA con la 3090"          'grep -qE "^gpu +: 2 NVIDIA: .*RTX 3090 7121/24576 MiB 46C" "$O"'
chk "riepilogo container 5/5"                      'grep -qE "^container +: 5/5 attivi" "$O"'
chk "riepilogo servizi tutti OK"                   'grep -E "^servizi +:" "$O" | grep -q "ollama-main OK" && ! grep -E "^servizi" "$O" | grep -q KO'
chk "smoke riportato dallo script"                 'grep -qE "^smoke +: 18 ok, 0 falliti" "$O"'
chk "modelli caricati nei dettagli"                'grep -q "qwen2.5-coder:32b" "$O"'
chk "schede video da lspci (Intel inclusa)"        'grep -q "Intel Corporation" "$O"'
chk "stampa il riepilogo a video"                  'echo "$out" | grep -q RIEPILOGO'
echo "== sola lettura e segreti"
chk "sola lettura: repo invariato"                 '[ "$before" = "$after" ]'
chk "non legge .orchestra_token"                   '! grep -q SEGRETO_TOKEN_123 "$O"'
chk "dichiara solo la presenza dei file sensibili" 'grep -q ".orchestra_token presente" "$O" && grep -q "orchestra.env presente" "$O"'
chk "oscura Bearer e github_pat nei log"           '! grep -q "abcDEF123456SECRET" "$O" && ! grep -q "github_pat_11ZZZZ" "$O"'
echo "== compattezza (300 righe di errore per container)"
chk "file di al massimo 165 righe"                 '[ "$(wc -l < "$O")" -le 165 ]'
chk "sezione errori di al massimo 20 righe"        '[ "$(awk "/^--- errori/{f=1;next} /^--- /{f=0} f" "$O" | grep -c .)" -le 20 ]'
chk "il riepilogo conta gli errori (3 x 5)"        'grep -qE "^errori +: 15 righe" "$O"'
echo "== --no-smoke e percorso predefinito"
bash "$D" --no-smoke "$T/o2.txt" >/dev/null 2>&1
chk "smoke non eseguito con --no-smoke"            'grep -qE "^smoke +: non eseguito" "$T/o2.txt"'
bash "$D" --no-smoke >/dev/null 2>&1
chk "senza argomento scrive in HOME"               'ls "$HOME"/orchestra-diag-*.txt >/dev/null 2>&1'
echo "== nvidia-smi e docker che falliscono"; mk
printf '#!/bin/bash\nexit 9\n' > "$T/bin/nvidia-smi"; cp "$T/bin/nvidia-smi" "$T/bin/docker"
bash "$D" --no-smoke "$T/o3.txt" >/dev/null 2>&1; rc=$?
chk "esce comunque con 0"                          '[ $rc = 0 ]'
chk "gpu: non disponibile"                         'grep -qE "^gpu +: non disponibile" "$T/o3.txt"'
chk "container: non disponibile"                   'grep -qE "^container +: non disponibile" "$T/o3.txt"'

echo "== eventi hardware nel log del kernel (ultime 24 ore)"
mk; NOW="$(date +%Y-%m-%dT%H:%M:%S%:z)"; OLD="$(date -d '3 days ago' +%Y-%m-%dT%H:%M:%S%:z)"
K="$T/kern.log"; : > "$K"
AER="pcieport 0000:00:1a.0: AER:"
for i in 1 2; do echo "$NOW h kernel: $AER Correctable error message received from 0000:3b:00.0" >> "$K"; done
echo "$NOW h kernel: $AER Multiple Correctable error message received from 0000:3b:00.0" >> "$K"
echo "$NOW h kernel: $AER Correctable error message received from 0000:02:00.0" >> "$K"
echo "$NOW h kernel: $AER Uncorrectable (Fatal) error message received from 0000:02:00.0" >> "$K"
echo "$NOW h kernel: NVRM: Xid (PCI:0000:3b:00): 79, GPU has fallen off the bus." >> "$K"
echo "$NOW h kernel: NVRM: GPU 0000:3b:00.0: GPU has fallen off the bus." >> "$K"
echo "$NOW h kernel: NVRM: Xid (PCI:0000:3b:00): 154, GPU recovery action changed" >> "$K"
echo "$NOW h kernel: NVRM: Xid (PCI:0000:01:00): 154, GPU recovery action changed" >> "$K"
for i in 1 2; do echo "$NOW h kernel: NVRM: _kgspRpcRecvPoll: GSP RM heartbeat timed out" >> "$K"; done
echo "$NOW h kernel: NVRM: _kgspIsHeartbeatTimedOut: Heartbeat timed out, currentTimeMs 1" >> "$K"
echo "$NOW h kernel: pcieport 0000:03:01.0: Unable to change power state from D0 to D3hot, device inaccessible" >> "$K"
echo "$NOW h kernel: r8169 0000:6d:00.0 eth0: RTL8125B, 74:d4:dd:3b:82:f0, XID 641, IRQ 180" >> "$K"
echo "$NOW h kernel: Linux version 6.8.0 (buildd)" >> "$K"
for i in 1 2 3 4 5; do echo "$OLD h kernel: $AER Correctable error message received from 0000:3b:00.0" >> "$K"; done
echo "$OLD h kernel: NVRM: Xid (PCI:0000:3b:00): 79, GPU has fallen off the bus." >> "$K"
H="$T/h1.txt"; bash "$D" --no-smoke "$H" >/dev/null 2>&1
hw() { grep -E "^hardware +:" "$H"; }
chk "fonte: kern.log"                                          'hw | grep -q "24h (kern.log):"'
chk "AER corretti: 4 (gli eventi di 3 giorni fa sono ignorati)" 'hw | grep -q "AER corretti 4 (" && hw | grep -q "3b:00.0 x3" && hw | grep -q "02:00.0 x1"'
chk "AER non corretti: 1 su 02:00.0"                           'hw | grep -q "AER NON corretti 1 (02:00.0 x1)"'
chk "Xid: 3 (79 x1, 154 x2); la scheda di rete XID 641 non conta" 'hw | grep -q "Xid 3 (" && hw | grep -q "79 x1" && hw | grep -q "154 x2"'
chk "timeout GSP: 2 (la riga generica Heartbeat non conta)"    'hw | grep -q "GSP timeout 2"'
chk "Thunderbolt D3hot inaccessibile: 1"                       'hw | grep -q "Thunderbolt D3hot 1"'
chk "avvii: 1"                                                 'hw | grep -q "avvii 1"'
chk "eventi gravi: ATTENZIONE"                                 'hw | grep -q "ATTENZIONE"'
mk; echo "$NOW h kernel: $AER Correctable error message received from 0000:3b:00.0" > "$T/kern.log"
H="$T/h2.txt"; bash "$D" --no-smoke "$H" >/dev/null 2>&1
chk "solo errori corretti: segnalati senza ATTENZIONE"         'hw | grep -q "AER corretti 1" && ! hw | grep -q "ATTENZIONE"'
mk; echo "$NOW h kernel: usb 1-3: new device" > "$T/kern.log"
H="$T/h3.txt"; bash "$D" --no-smoke "$H" >/dev/null 2>&1
chk "nessun evento: lo dice"                                   'hw | grep -q "nessun evento hardware"'
mk; H="$T/h4.txt"; bash "$D" --no-smoke "$H" >/dev/null 2>&1
chk "nessuna fonte leggibile: lo dice e suggerisce --sudo-log"  'hw | grep -q "non leggibile" && hw | grep -q "sudo-log"'
mk; printf '#!/bin/bash\necho "%s h kernel: NVRM: Xid (PCI:0000:3b:00): 79, GPU has fallen off the bus."\n' "$NOW" > "$T/bin/journalctl"
H="$T/h5.txt"; bash "$D" --no-smoke "$H" >/dev/null 2>&1
chk "senza kern.log usa journalctl"                            'hw | grep -q "24h (journalctl):" && hw | grep -q "Xid 1 (79 x1)"'
mk; echo "$NOW h kernel: $AER Correctable error message received from 0000:3b:00.0" > "$T/kern.log"
printf '#!/bin/bash\necho used > "%s/sudo_used"\nexec "$@"\n' "$T" > "$T/bin/sudo"; chmod +x "$T/bin/sudo"
bash "$D" --no-smoke "$T/h6.txt" >/dev/null 2>&1
chk "senza --sudo-log non usa sudo"                            '[ ! -f "$T/sudo_used" ]'
bash "$D" --no-smoke --sudo-log "$T/h7.txt" >/dev/null 2>&1
chk "con --sudo-log legge il log tramite sudo"                 '[ -f "$T/sudo_used" ] && grep -q "AER corretti 1" "$T/h7.txt"'
mk; : > "$T/kern.log"; for i in $(seq 1 200); do echo "$NOW h kernel: $AER Correctable error message received from 0000:3b:00.$((i%8))" >> "$T/kern.log"; echo "$NOW h kernel: NVRM: Xid (PCI:0000:3b:00): $((70+i%20)), x" >> "$T/kern.log"; echo "$NOW h kernel: NVRM: _kgspRpcRecvPoll: GSP RM heartbeat timed out" >> "$T/kern.log"; done
bash "$D" --no-smoke "$T/h8.txt" >/dev/null 2>&1
chk "600 eventi: sezione dettagli di al massimo 12 righe"      '[ "$(awk "/^--- eventi hardware/{f=1;next} /^--- /{f=0} f" "$T/h8.txt" | grep -c .)" -le 12 ]'
chk "600 eventi: file di al massimo 165 righe"                 '[ "$(wc -l < "$T/h8.txt")" -le 165 ]'

echo; echo "$n controlli"; [ $ok = 1 ] && echo "DIAG ALL OK" || echo "DIAG FAILED"; [ $ok = 1 ]
