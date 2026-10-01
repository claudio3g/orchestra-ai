# HANDOFF DOCUMENT — RASPBERRY PI 4 HOME SERVER
Versione: 4.0 — 1 Giugno 2026  
Obiettivo: Documento di contesto completo per proseguire la configurazione, manutenzione e troubleshooting in una nuova sessione AI, senza perdere stato.

---

## 0. ISTRUZIONI PER L'AI CHE LEGGE QUESTO DOCUMENTO

Sei un ingegnere senior AI con specializzazione in:
- Sistemi AI self-hosted con vincoli hardware severi (≤8 GB VRAM)
- Architetture RAG (Retrieval-Augmented Generation)
- LLM locali via Ollama
- Pipeline Python per OpenWebUI/Pipelines framework
- Reti e sicurezza (VPN, firewall, reverse proxy)

Il tuo approccio operativo deve essere:
- **Chiaro e semplice**: spiega brevemente in modo molto chiaro quello che fai
- **Conservativo**: non modificare ciò che funziona senza una motivazione tecnica precisa
- **Incrementale**: un cambiamento alla volta, testato prima di procedere
- **Documentato**: ogni modifica va spiegata nei commenti del codice
- **Verificato**: dopo ogni modifica, esegui uno smoke test prima di dichiarare successo

---

## 1. INFRASTRUTTURA FISICA E RETE

| Componente | Dettaglio |
|---|---|
| Dispositivo principale | Raspberry Pi 4 Model B — 4 GB RAM |
| Storage | SSD 90 GB collegato via USB (/dev/sda) |
| Sistema Operativo | Ubuntu 24.04.4 LTS (Noble Numbat), kernel 6.8.0-1053-raspi, arch aarch64 |
| Hostname | rasdom1-pi4 |
| Utente principale | ubuntu (con sudo) |
| Router | Fritz!Box 7530 AX, FritzOS 8.25, IP 192.168.1.1 |
| IP del Raspberry | 192.168.1.100/24 su interfaccia Wi-Fi wlan0 (IP statico) |
| Interfaccia Ethernet | eth0 spenta (NO-CARRIER) — failover LAN/Wi-Fi configurato e funzionante, non modificare |
| DNS attuale | /etc/resolv.conf punta a 127.0.0.1 (AdGuard Home) — file normale, non symlink |
| DNS di backup | Router Fritz!Box 192.168.1.1 (usato in emergenza) |

**Dispositivi aggiuntivi nella rete LAN (subnet 192.168.1.0/24):**
- Switch TP-Link TL-SG105E (192.168.1.10)
- NAS QNAP TS-235A (192.168.1.131)
- Router Zyxel VMG8825-T50K (192.168.1.19) — estensione Wi-Fi e 4 porte RJ45
- nix-i9 (192.168.1.51) — stack AI con Ollama, Open WebUI (esposto su internet via orchestra.tregambe.com su porte 80/443 tramite NPM sul Pi)
- xtream-pi (192.168.1.123) — precedentemente usato per cron DDNS, ora dismesso

**Configurazione Fritz!Box (completata e funzionante):**
- Fritz!Box distribuisce 192.168.1.100 come DNS primario ai client via DHCP
- IP statico del Pi prenotato via DHCP reservation
- Port forwarding 80 e 443 puntano a **192.168.1.100** (rasdom1-pi4 — NPM centralizzato)
- Server WireGuard® integrato attivo su porta UDP 51820
- DDNS configurato su freedns.afraid.org per aggiornare saponetta.mooo.com

---

## 2. REGOLE TASSATIVE — DA NON VIOLARE MAI

⚠️ Queste regole hanno priorità assoluta su qualsiasi altra considerazione.

1. Mai modificare i file in `/etc/netplan/`, `/etc/wpa_supplicant/`, `/etc/systemd/network/` senza backup e piano di rollback
2. Mai eseguire `netplan apply` o riavviare servizi di rete senza verifica preventiva
3. Non rimuovere i volumi Docker `adguard_data` e `adguard_conf` — contengono la configurazione AdGuard già funzionante
4. Qualsiasi nuovo container che necessiti della porta 53 deve essere coordinato con AdGuard (unico servizio DNS attivo)
5. Prima di riavviare il sistema, verificare che tutti i container critici (soprattutto AdGuard) siano funzionanti e che `/etc/resolv.conf` punti a un resolver valido
6. Non modificare la configurazione di rete di nix-i9 oltre a quanto già fatto — il binding `192.168.1.51:3001` per Open WebUI è protetto da UFW (solo 192.168.1.100 autorizzato)
7. Caddy su nix-i9 è stato **fermato e disabilitato** — non riavviarlo, NPM sul Pi gestisce tutto il traffico web

---

## 3. DOCKER & CONTAINER (rasdom1-pi4)

**Versioni installate:**
- Docker Engine Community 29.4.3
- Docker Compose plugin v5.1.3
- Docker Buildx v0.33.0

**Stack gestito con Docker Compose** (`/home/ubuntu/docker-compose.yml`, owned da root — usare sudo per modifiche):

| Nome | Immagine | Porte | Note |
|---|---|---|---|
| homepage | ghcr.io/gethomepage/homepage:latest | 3080:3000 | Dashboard unificata, env file protetto |
| portainer | portainer/portainer-ce:latest | 127.0.0.1:8000:8000, 9000:9000 | Gestione container — edge port solo localhost |
| watchtower | ghcr.io/containrrr/watchtower:latest | nessuna | Aggiornamento immagini automatico (24h, cleanup) |
| adguardhome | adguard/adguardhome:latest | 53:53/tcp+udp, 192.168.1.100:3000:80/tcp | DNS primario — UI solo su IP Pi |
| homeassistant | homeassistant/home-assistant:stable | network: host (8123) | Domotica |
| netdata | netdata/netdata:stable | network: host (19999) | Monitoraggio — ⚠️ P4 aperto: binding da restringere |
| nginx-proxy-manager | jc21/nginx-proxy-manager:latest | 80:80, 443:443, 192.168.1.100:81:81 | Reverse proxy centralizzato — admin solo su IP Pi |

**Volumi Docker esistenti:**
- portainer_data
- adguard_data **(CRITICO)**
- adguard_conf **(CRITICO)**
- homeassistant_data
- netdataconfig
- netdatalib
- netdatacache

---

## 4. DOMINI E DNS

- `tregambe.com` — IP statico 79.98.45.14, hosting sito web (gestito via Plesk)
- `saponetta.mooo.com` — DDNS (freedns.afraid.org) aggiornato dal Fritz!Box, punta all'IP dinamico WAN
- `orchestra.tregambe.com` — CNAME → saponetta.mooo.com — gestito da NPM sul Pi, proxy verso OpenWebUI su nix-i9
- `myhome.tregambe.com` — CNAME → saponetta.mooo.com — endpoint VPN WireGuard (non è un sito web)

**Record DNS su Plesk (tregambe.com):**
```
orchestra.tregambe.com.   CNAME   saponetta.mooo.com.
myhome.tregambe.com.      CNAME   saponetta.mooo.com.
```

**DDNS su Fritz!Box:**
- URL: http://sync.afraid.org/u/gwzbZ6s8knHhg9at7DuTG2qy/
- Dominio: saponetta.mooo.com
- Funzionante, aggiornamento automatico ad ogni cambio IP

---

## 5. VPN WIREGUARD (Fritz!Box)

- Server WireGuard integrato nel Fritz!Box, attivo su porta UDP 51820
- Endpoint per i client: `myhome.tregambe.com:51820`
- Configurazione QR code/file già distribuita ai dispositivi (cellulare testato con successo)
- Accesso remoto a tutti i servizi interni senza esporre porte web
- Funzionante e verificato
- **Nota**: `myhome.tregambe.com` su browser non risponde (corretto — è solo un endpoint VPN, non un sito web)

---

## 6. REVERSE PROXY CENTRALIZZATO (NPM su rasdom1-pi4)

**Stato: ✅ Completato e funzionante**

**Architettura:**
```
Internet → Fritz!Box (80/443) → rasdom1-pi4 NPM → nix-i9:3001 (OpenWebUI)
```

**Proxy host configurati su NPM:**

| Dominio | Upstream | SSL | Note |
|---|---|---|---|
| orchestra.tregambe.com | 192.168.1.51:3001 | Let's Encrypt ✅ | WebSocket abilitato |

**Regole di sicurezza su NPM (Advanced → Custom Nginx) per orchestra.tregambe.com:**
```nginx
# Blocco path API interne
location ~* ^/(ollama|pipelines|rag) {
    return 403 "Accesso non consentito";
}
location ~* ^/api/(tags|generate|chat|ps) {
    return 403 "Accesso non consentito";
}
location ~* ^/v1/models {
    return 403 "Accesso non consentito";
}

# Endpoint ComfyUI
location /comfyui/view {
    rewrite ^/comfyui(/.*)$ $1 break;
    proxy_pass http://192.168.1.51:8188;
    proxy_read_timeout 30s;
}

# Header di sicurezza
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;
more_clear_headers Server;
more_clear_headers X-Powered-By;
```

**saponetta.mooo.com**: nessun proxy host configurato su NPM — NPM rifiuta la connessione SSL (comportamento deliberato).

---

## 7. STACK AI — nix-i9 (192.168.1.51)

**Script di avvio:** `~/ai-sessioni/start_ai_stack.sh` (v3.7)  
**Compose file:** `~/ai-sessioni/ollama/docker-compose.yml` (non usato direttamente — lo stack è gestito dallo script)

**Modifiche applicate nella sessione 1 Giugno 2026:**
1. Binding OpenWebUI cambiato da `127.0.0.1:3001` a `192.168.1.51:3001` nella funzione `ensure_container` dello script
2. Aggiunta immagine `ghcr.io/open-webui/open-webui:main` alla chiamata `ensure_container` del WebUI (era mancante)
3. Caddy fermato (`sudo systemctl stop caddy`) e disabilitato (`sudo systemctl disable caddy`)

**Container attivi durante sessione:**

| Container | Binding | Note |
|---|---|---|
| ai-ollama-session | 127.0.0.1:11435 | GPU, modelli su volume ollama-session |
| ai-qdrant-session | 127.0.0.1:6333 | Vector DB per RAG |
| ai-pipelines-session | 127.0.0.1:9099 | Pipeline OpenWebUI |
| ai-webui-session | 192.168.1.51:3001 | OpenWebUI — raggiungibile solo da Pi |

**UFW su nix-i9 (regole chiave):**
```
[ 5] 3001/tcp   ALLOW IN   192.168.1.100   # NPM rasdom1-pi4 → OpenWebUI
[ 6] 3001       DENY IN    Anywhere
[ 3] 80/tcp     ALLOW IN   Anywhere        # HTTP - ACME (ora inutile, Caddy disabilitato)
[ 4] 443/tcp    ALLOW IN   Anywhere        # HTTPS (ora inutile, Caddy disabilitato)
```

**⚠️ P3 aperto**: RAG service (6335) e ComfyUI (8188) ancora su `0.0.0.0` — UFW li nega ma il binding va ristretto nello script.

**⚠️ Regole UFW 3 e 4** (80/443 ALLOW Anywhere) ora inutili con Caddy disabilitato — da rimuovere nella prossima sessione.

---

## 8. BACKUP AUTOMATICO (pCloud cifrato + notifiche Telegram)

- Strumento: rclone v1.74.2
- Cartella remota: pcloud:/backup-rasdom1-pi4 (cifrata)
- Script: `/home/ubuntu/backup/backup.sh`
- Credenziali Telegram: `/home/ubuntu/backup/backup.env` (chmod 600)
- Cron: esecuzione giornaliera alle 4:00

---

## 9. UFW — rasdom1-pi4

```
[ 1] Anywhere     ALLOW IN   192.168.1.0/24   # LAN completa
[ 2] 22/tcp       ALLOW IN   192.168.1.0/24   # SSH
[ 3] 53/tcp       ALLOW IN   Anywhere          # DNS
[ 4] 53/udp       ALLOW IN   Anywhere          # DNS
[ 5] 3080/tcp     ALLOW IN   192.168.1.0/24   # Homepage
[ 6] 9000/tcp     ALLOW IN   192.168.1.0/24   # Portainer
[ 7] 3000/tcp     ALLOW IN   192.168.1.0/24   # AdGuard UI
[ 8] 8123/tcp     ALLOW IN   192.168.1.0/24   # Home Assistant
[ 9] 19999/tcp    ALLOW IN   192.168.1.0/24   # Netdata
[10] 80/tcp       ALLOW IN   Anywhere          # NPM HTTP
[11] 443/tcp      ALLOW IN   Anywhere          # NPM HTTPS
```

---

## 10. RIEPILOGO STATO COMPLETAMENTO

| Componente | Stato |
|---|---|
| Raspberry Pi 4 — OS e storage | ✅ Completato |
| Docker Engine + Compose | ✅ Completato |
| Portainer | ✅ Completato |
| Watchtower | ✅ Completato |
| AdGuard Home (DNS + UI) | ✅ Completato |
| Home Assistant | ✅ Completato |
| Netdata | ✅ Completato |
| Homepage Dashboard | ✅ Completato |
| Credenziali sicure (env file) | ✅ Completato |
| IP statico Pi (DHCP reservation Fritz) | ✅ Completato |
| UFW Firewall | ✅ Completato |
| Backup automatico (pCloud cifrato) | ✅ Completato |
| Notifiche Telegram (backup) | ✅ Completato |
| DDNS su Fritz!Box | ✅ Completato |
| VPN WireGuard (Fritz!Box) | ✅ Completato |
| Domini (orchestra, myhome) | ✅ Completato |
| Nginx Proxy Manager (installato) | ✅ Completato |
| Reverse Proxy centralizzato | ✅ Completato (1 Giugno 2026) |
| Sicurezza binding Docker (Pi) | ✅ Completato (1 Giugno 2026) |
| Caddy su nix-i9 | ✅ Fermato e disabilitato (1 Giugno 2026) |
| Regole sicurezza NPM (path blocking + header) | ✅ Completato (1 Giugno 2026) |
| RAG/ComfyUI binding su nix-i9 | 🟡 Parziale (UFW nega, binding da restringere) |
| Netdata binding su Pi | 🟡 Parziale (UFW limita LAN, binding da restringere) |
| UFW nix-i9 cleanup (regole 80/443 inutili) | 🔴 Da fare |

---

## 11. PROSSIMI PASSI (priorità)

1. **P3** — Restringere binding RAG service (6335) e ComfyUI (8188) da `0.0.0.0` a `127.0.0.1` nello script `start_ai_stack.sh` su nix-i9
2. **P4** — Restringere binding Netdata (19999) su rasdom1-pi4 (network_mode: host — da gestire via configurazione Netdata)
3. **Cleanup UFW nix-i9** — Rimuovere regole 3 e 4 (80/443 ALLOW Anywhere) ora inutili con Caddy disabilitato
4. **Opzionale** — Monitoraggio con Uptime Kuma
5. **Opzionale** — Migrazione backup su Restic o aggiunta retention remota

---

*Fine documento — versione 4.0 — 1 Giugno 2026*
