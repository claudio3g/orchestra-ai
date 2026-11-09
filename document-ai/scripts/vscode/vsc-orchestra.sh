#!/bin/bash
# vsc-orchestra.sh
# Avvia VS Code con la configurazione di Continue appropriata alla modalità GPU.
# ─────────────────────────────────────────────────────────────────────────────
# Uso:
#   vsc-orchestra.sh              # rileva automaticamente
#   vsc-orchestra.sh normal       # forza NORMAL
#   vsc-orchestra.sh full         # forza FULL
#   vsc-orchestra.sh --dry-run    # mostra cosa farebbe, senza eseguire
#   vsc-orchestra.sh --help       # mostra questo aiuto
# ─────────────────────────────────────────────────────────────────────────────

set -u

# ─── Percorsi ───
WORKSPACE="$HOME/ai-sessioni"
CONTINUE_DIR="$HOME/.continue"
ENV_FILE="$HOME/.config/continue/env.sh"
DETECT_SCRIPT="$HOME/.local/bin/orchestra-detect-gpu.sh"
NORMAL_CONFIG="$CONTINUE_DIR/config.normal.yaml"
FULL_CONFIG="$CONTINUE_DIR/config.full.yaml"
ACTIVE_CONFIG="$CONTINUE_DIR/config.yaml"

# ─── Colori ───
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}ℹ️  $*${NC}"; }
ok()    { echo -e "${GREEN}✅ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $*${NC}"; }
err()   { echo -e "${RED}❌ $*${NC}"; }

# ─── Argomenti ───
FORCE_MODE=""
DRY_RUN=0

show_help() {
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        normal)    FORCE_MODE="normal"; shift ;;
        full)      FORCE_MODE="full"; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --help|-h) show_help ;;
        *)         err "Argomento non riconosciuto: $1"; exit 2 ;;
    esac
done

# ─── 1. Carica variabili d'ambiente ───
if [ -f "$ENV_FILE" ]; then
    . "$ENV_FILE"
    info "Variabili d'ambiente caricate da $ENV_FILE"
else
    warn "$ENV_FILE non trovato — DeepSeek non sarà disponibile"
fi

# ─── 2. Rileva modalità GPU ───
if [ -n "$FORCE_MODE" ]; then
    MODE="$FORCE_MODE"
    info "Modalità forzata dall'utente: $MODE"
else
    if [ ! -x "$DETECT_SCRIPT" ]; then
        err "$DETECT_SCRIPT non trovato o non eseguibile"
        exit 2
    fi
    if "$DETECT_SCRIPT"; then
        MODE="full"
    else
        MODE="normal"
    fi
fi

echo ""
info "Modalità selezionata: ${MODE^^}"
echo ""

# ─── 3. Scegli la configurazione ───
case "$MODE" in
    normal) SOURCE_CONFIG="$NORMAL_CONFIG" ;;
    full)   SOURCE_CONFIG="$FULL_CONFIG" ;;
    *)      err "Modalità sconosciuta: $MODE"; exit 2 ;;
esac

if [ ! -f "$SOURCE_CONFIG" ]; then
    err "File di configurazione non trovato: $SOURCE_CONFIG"
    exit 2
fi

# ─── 4. Backup della config attiva ───
if [ -f "$ACTIVE_CONFIG" ]; then
    BACKUP_NAME="${ACTIVE_CONFIG}.bak.$(date +%Y%m%d_%H%M%S)"
    if [ "$DRY_RUN" -eq 1 ]; then
        info "[DRY-RUN] Avrei fatto backup: $ACTIVE_CONFIG → $BACKUP_NAME"
    else
        cp "$ACTIVE_CONFIG" "$BACKUP_NAME"
        ok "Backup creato: $BACKUP_NAME"
    fi
fi

# ─── 5. Copia la configurazione scelta ───
if [ "$DRY_RUN" -eq 1 ]; then
    info "[DRY-RUN] Avrei copiato: $SOURCE_CONFIG → $ACTIVE_CONFIG"
else
    cp "$SOURCE_CONFIG" "$ACTIVE_CONFIG"
    ok "Configurazione attivata: $ACTIVE_CONFIG"
fi

# ─── 6. Verifica se VS Code è già in esecuzione ───
if pgrep -x "code" >/dev/null 2>&1; then
    warn "VS Code è già in esecuzione."
    warn "Per applicare la nuova configurazione, riavvialo."
    echo ""
    if [ "$DRY_RUN" -eq 0 ]; then
        info "Per riavviare: chiudi tutte le finestre di VS Code e rilancia questo script."
        exit 0
    fi
fi

# ─── 7. Lancia VS Code ───
if [ "$DRY_RUN" -eq 1 ]; then
    info "[DRY-RUN] Avrei lanciato: code $WORKSPACE"
    ok "Dry-run completato. Nessuna modifica effettuata."
    exit 0
fi

info "Lancio VS Code su $WORKSPACE..."
code "$WORKSPACE"
ok "VS Code avviato."
