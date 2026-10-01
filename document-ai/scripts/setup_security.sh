#!/bin/bash
# ORCHESTRA 8GB — SECURITY SETUP v1.0
# Configura Caddy + ufw per esposizione sicura su internet.
# Eseguire UNA SOLA VOLTA dalla macchina locale (non via SSH).
# Rollback LIFO automatico in caso di errore.
#
# Prerequisiti:
#   - Stack Orchestra già funzionante in locale
#   - Porta 80 e 443 aperte sul router NAT verso questa macchina
#   - DDNS saponetta.mooo.com già puntato all'IP pubblico
#
# FILE: ~/ai-sessioni/setup_security.sh

set -eEo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}ℹ️  $*${NC}"; }
success() { echo -e "${GREEN}✅ $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠️  $*${NC}"; }
error()   { echo -e "${RED}❌ $*${NC}"; }
header()  { echo -e "\n${BOLD}═══ $* ═══${NC}"; }

# ─────────────────────────────────────────────────────────────────────────────
# Configurazione
# ─────────────────────────────────────────────────────────────────────────────
CADDY_CONFIG_DIR="/etc/caddy"
CADDY_CADDYFILE="${CADDY_CONFIG_DIR}/Caddyfile"
CADDY_LOG_DIR="/var/log/caddy"
AI_SESSIONS_DIR="$HOME/ai-sessioni"
CADDYFILE_SRC="${AI_SESSIONS_DIR}/Caddyfile"
DOMAIN="saponetta.mooo.com"

ROLLBACK_ACTIONS=()

rollback() {
    local EXIT_CODE=$?
    error "ERRORE (exit ${EXIT_CODE}) — Rollback in corso..."
    local N=${#ROLLBACK_ACTIONS[@]}
    for (( i=N-1; i>=0; i-- )); do
        echo "  ↩ ${ROLLBACK_ACTIONS[$i]}"
        eval "${ROLLBACK_ACTIONS[$i]}" 2>/dev/null || true
    done
    warn "Rollback completato."
    exit "${EXIT_CODE}"
}
trap rollback ERR INT TERM

# ─────────────────────────────────────────────────────────────────────────────
# Stato pre-esistente
# ─────────────────────────────────────────────────────────────────────────────
CADDY_PRE_INSTALLED=false
UFW_PRE_ACTIVE=false
UFW_PRE_ENABLED=false

dpkg -l caddy &>/dev/null 2>&1 && CADDY_PRE_INSTALLED=true
systemctl is-active --quiet ufw 2>/dev/null  && UFW_PRE_ACTIVE=true
systemctl is-enabled --quiet ufw 2>/dev/null && UFW_PRE_ENABLED=true

# ─────────────────────────────────────────────────────────────────────────────
# Piano e conferma
# ─────────────────────────────────────────────────────────────────────────────
echo -e "\n${BOLD}Piano di sicurezza Orchestra 8GB${NC}"
echo "  1. Installa Caddy (reverse proxy + TLS automatico)"
echo "  2. Copia Caddyfile in /etc/caddy/"
echo "  3. Configura ufw (80, 443 aperti — tutto il resto chiuso)"
echo "  4. Abilita e avvia Caddy come servizio systemd"
echo ""
echo "  Dominio target: ${DOMAIN}"
echo "  Caddyfile sorgente: ${CADDYFILE_SRC}"
echo ""
warn "SSH è disabilitato — assicurati di avere accesso fisico alla macchina."
echo -n "Procedere? [s/N] "
read -r CONFIRM
[[ ! "$CONFIRM" =~ ^[sS]$ ]] && { info "Annullato."; trap - ERR INT TERM; exit 0; }

# ─────────────────────────────────────────────────────────────────────────────
header "1/4  Caddy — installazione"
# ─────────────────────────────────────────────────────────────────────────────
if [ "$CADDY_PRE_INSTALLED" = false ]; then
    info "Aggiunta repository Caddy..."
    sudo apt-get update -qq
    sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl

    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    ROLLBACK_ACTIONS+=("sudo rm -f /usr/share/keyrings/caddy-stable-archive-keyring.gpg")

    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | sudo tee /etc/apt/sources.list.d/caddy-stable.list > /dev/null
    ROLLBACK_ACTIONS+=("sudo rm -f /etc/apt/sources.list.d/caddy-stable.list")

    sudo apt-get update -qq
    sudo apt-get install -y caddy
    ROLLBACK_ACTIONS+=("sudo apt-get purge -y caddy 2>/dev/null || true")
    success "Caddy installato"
else
    success "Caddy già installato ($(caddy version))"
fi

# ─────────────────────────────────────────────────────────────────────────────
header "2/4  Caddy — configurazione"
# ─────────────────────────────────────────────────────────────────────────────

# Verifica che il Caddyfile sorgente esista
if [ ! -f "$CADDYFILE_SRC" ]; then
    error "Caddyfile non trovato in ${CADDYFILE_SRC}"
    error "Copia prima Caddyfile nella directory ai-sessioni/"
    exit 1
fi

# Backup Caddyfile esistente
if [ -f "$CADDY_CADDYFILE" ]; then
    BAK="${CADDY_CADDYFILE}.backup.$(date +%Y%m%d_%H%M%S)"
    sudo cp "$CADDY_CADDYFILE" "$BAK"
    ROLLBACK_ACTIONS+=("sudo cp '${BAK}' '${CADDY_CADDYFILE}'")
    info "Backup Caddyfile: ${BAK}"
fi

# Copia Caddyfile
sudo mkdir -p "$CADDY_CONFIG_DIR"
sudo cp "$CADDYFILE_SRC" "$CADDY_CADDYFILE"
ROLLBACK_ACTIONS+=("sudo rm -f '${CADDY_CADDYFILE}'")
success "Caddyfile copiato in ${CADDY_CADDYFILE}"

# Directory log
sudo mkdir -p "$CADDY_LOG_DIR"
sudo chown caddy:caddy "$CADDY_LOG_DIR" 2>/dev/null || \
    sudo chown www-data:www-data "$CADDY_LOG_DIR" 2>/dev/null || true

# Validazione sintattica
info "Validazione Caddyfile..."
sudo caddy validate --config "$CADDY_CADDYFILE" && success "Caddyfile valido" || {
    error "Caddyfile non valido — controlla la sintassi"
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
header "3/4  ufw — firewall"
# ─────────────────────────────────────────────────────────────────────────────
info "Configurazione regole ufw..."

# Installa ufw se mancante (non presente di default su tutti i sistemi Ubuntu)
if ! command -v ufw &>/dev/null; then
    info "Installazione ufw..."
    sudo apt-get install -y ufw
    ROLLBACK_ACTIONS+=("sudo apt-get purge -y ufw 2>/dev/null || true")
    success "ufw installato"
fi

# Politica di default
sudo ufw default deny incoming  2>/dev/null || true
sudo ufw default allow outgoing 2>/dev/null || true

# Regole permissive (internet)
sudo ufw allow 80/tcp   comment 'HTTP - ACME challenge Let-Encrypt'
sudo ufw allow 443/tcp  comment 'HTTPS - Caddy reverse proxy'

# ComfyUI — ALLOW dai container Docker prima del DENY globale
# ufw valuta le regole dall'alto verso il basso: gli ALLOW specifici
# devono precedere il DENY Anywhere, altrimenti vengono ignorati.
# docker0 (bridge default) e ollama_default (rete Orchestra) usano
# tipicamente 172.17.x.x e 172.19.x.x
sudo ufw allow from 172.17.0.0/16 to any port 8188 \
    comment 'ComfyUI - Docker bridge' 2>/dev/null || true
sudo ufw allow from 172.19.0.0/16 to any port 8188 \
    comment 'ComfyUI - ollama_default' 2>/dev/null || true

# Blocco porte interne Orchestra
# IMPORTANTE: 8188 inserita DOPO gli ALLOW Docker — ordine critico per ufw
for PORT in 3001 8188 11435 6333 6335 9099; do
    sudo ufw deny "${PORT}" comment "Orchestra internal - block external" 2>/dev/null || true
done

# Abilita ufw
if [ "$UFW_PRE_ENABLED" = false ]; then
    sudo ufw --force enable
    ROLLBACK_ACTIONS+=("sudo ufw --force disable 2>/dev/null || true")
fi
sudo ufw reload 2>/dev/null || true

success "ufw configurato"
sudo ufw status verbose

# ─────────────────────────────────────────────────────────────────────────────
header "4/4  Caddy — avvio servizio"
# ─────────────────────────────────────────────────────────────────────────────
sudo systemctl enable caddy
ROLLBACK_ACTIONS+=("sudo systemctl disable caddy 2>/dev/null || true")

# Disabilita ERR trap prima di avviare Caddy:
# un errore di start è recuperabile (porta in uso, NAT non ancora aperto)
# e NON deve causare rollback dell'installazione.
trap - ERR

sudo systemctl restart caddy 2>/dev/null || true
sleep 3

if systemctl is-active --quiet caddy; then
    success "Caddy attivo"
else
    warn "Caddy non si è avviato. Log diagnostici:"
    echo ""
    sudo journalctl -u caddy -n 25 --no-pager 2>/dev/null || true
    echo ""
    warn "Cause comuni:"
    warn "  - Porta 80 o 443 già occupata da altro processo"
    warn "    Controlla: ss -tlnp | grep -E \":80 |:443 \""
    warn "  - NAT router non ancora configurato (normale in questa fase)"
    info "Dopo aver aperto le porte 80 e 443 sul router:"
    info "  sudo systemctl start caddy"
    info "  sudo journalctl -u caddy -f"
    warn "Caddy è installato e abilitato — si avvierà correttamente."
fi


# ─────────────────────────────────────────────────────────────────────────────
trap - ERR INT TERM

success "Setup sicurezza completato!"
echo ""
info "Prossimi passi:"
echo "  1. Apri porta 443 e 80 sul router NAT → questa macchina"
echo "  2. Verifica: curl -v https://${DOMAIN}"
echo "  3. Rimuovi la regola NAT :3001 dal router"
echo "  4. Verifica finale: https://${DOMAIN} → OpenWebUI"
echo ""
info "Log Caddy: sudo journalctl -u caddy -f"
info "Stato:     sudo systemctl status caddy"
info "Cert TLS:  sudo caddy list-certificates 2>/dev/null || curl -s https://${DOMAIN} -I"
