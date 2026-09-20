#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  AI Trade Lab – Proxmox LXC Installer (privat, offline)
#  Version: __VERSION__
#
#  Läuft auf dem Proxmox-Host. Erstellt einen eigenen Debian-LXC, installiert
#  Docker darin und startet AI Trade Lab. Die App steckt komplett in diesem
#  Skript – kein GitHub, kein Token, kein externer Download der App nötig.
#
#  Nutzung:
#    bash ai-trade-lab-install.sh                 # Standard-Installation
#    bash ai-trade-lab-install.sh --advanced      # Einstellungen abfragen
#    bash ai-trade-lab-install.sh --update 120    # CT 120 aktualisieren
#    bash ai-trade-lab-install.sh --help
# ─────────────────────────────────────────────────────────────────────────────
set -Eeuo pipefail

APP_VERSION="__VERSION__"
APP_DIR="/opt/ai-trade-lab"
APP_PORT=8787

# Standardwerte (per Flag oder --advanced änderbar)
CTID=""
CT_HOSTNAME="ai-trade-lab"
CORES=4
MEMORY=8192
SWAP=512
DISK=32
STORAGE=""
BRIDGE=""
NET="dhcp"
GATEWAY=""
VLAN=""
DNS=""
ASSUME_YES=0
ADVANCED=0
MODE="install"

# ── Ausgabe ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; C=$'\e[36m'; N=$'\e[0m'; else B=""; G=""; Y=""; R=""; C=""; N=""; fi
info() { printf '%s➜%s %s\n' "$C" "$N" "$*"; }
ok()   { printf '%s✔%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%s!%s %s\n' "$Y" "$N" "$*" >&2; }
die()  { printf '%s✖ %s%s\n' "$R" "$*" "$N" >&2; exit 1; }
trap 'die "Abbruch in Zeile $LINENO: $BASH_COMMAND"' ERR

usage() {
  cat <<USAGE
${B}AI Trade Lab ${APP_VERSION} – Proxmox LXC Installer${N}

  --advanced            Alle Einstellungen interaktiv abfragen
  --ctid ID             Container-ID (Standard: nächste freie)
  --hostname NAME       Hostname (Standard: ${CT_HOSTNAME})
  --cores N             CPU-Kerne (Standard: ${CORES})
  --memory MB           RAM in MB (Standard: ${MEMORY})
  --disk GB             Disk in GB (Standard: ${DISK})
  --storage NAME        Storage für das Root-FS (Standard: automatisch)
  --bridge NAME         Netzwerk-Bridge (Standard: vmbr0 bzw. erste gefundene)
  --ip CIDR|dhcp        z. B. 192.168.1.50/24 (Standard: dhcp)
  --gw IP               Gateway (nur bei statischer IP)
  --vlan TAG            VLAN-Tag
  --dns IP              DNS-Server
  --update CTID         Bestehende Installation aktualisieren (mit Backup + Rollback)
  -y, --yes             Ohne Rückfrage installieren
  -h, --help            Diese Hilfe
USAGE
}

# ── Argumente ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --advanced) ADVANCED=1 ;;
    --ctid) CTID="$2"; shift ;;
    --hostname) CT_HOSTNAME="$2"; shift ;;
    --cores) CORES="$2"; shift ;;
    --memory) MEMORY="$2"; shift ;;
    --disk) DISK="$2"; shift ;;
    --storage) STORAGE="$2"; shift ;;
    --bridge) BRIDGE="$2"; shift ;;
    --ip) NET="$2"; shift ;;
    --gw) GATEWAY="$2"; shift ;;
    --vlan) VLAN="$2"; shift ;;
    --dns) DNS="$2"; shift ;;
    --update) MODE="update"; CTID="$2"; shift ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; die "Unbekannte Option: $1" ;;
  esac
  shift
done

# ── Payload (App-Bundle) ─────────────────────────────────────────────────────
payload_to() {
  # schreibt das eingebettete App-Archiv nach $1
  sed -n '/^__AITRA_PAYLOAD__$/,$p' "$SCRIPT_PATH" | tail -n +2 | base64 -d > "$1"
}

# ── Checks ───────────────────────────────────────────────────────────────────
preflight() {
  [[ $EUID -eq 0 ]] || die "Bitte als root auf dem Proxmox-Host ausführen."
  for c in pct pveam pvesh pvesm pveversion base64; do
    command -v "$c" >/dev/null || die "'$c' nicht gefunden – läuft das wirklich auf einem Proxmox-Host?"
  done
  SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
  [[ -f "$SCRIPT_PATH" ]] && grep -q '^__AITRA_PAYLOAD__$' "$SCRIPT_PATH" \
    || die "Skript bitte als Datei starten (bash ai-trade-lab-install.sh), nicht per Pipe."
  info "Proxmox: $(pveversion | head -n1)"
}

valid_int() { [[ "$1" =~ ^[0-9]+$ ]]; }

ask() { # ask VAR "Frage" – übernimmt Enter als Standard
  local var="$1" prompt="$2" cur="${!1}" ans
  read -r -p "  ${prompt} [${cur:-leer}]: " ans </dev/tty || true
  [[ -n "$ans" ]] && printf -v "$var" '%s' "$ans"
  return 0
}

# ── Automatische Erkennung ───────────────────────────────────────────────────
detect() {
  [[ -n "$CTID" ]] || CTID="$(pvesh get /cluster/nextid)"
  if [[ -z "$STORAGE" ]]; then
    # aktives Storage mit Root-FS-Support und dem meisten freien Platz
    STORAGE="$(pvesm status -content rootdir 2>/dev/null | awk 'NR>1 && $3=="active" {print $6, $1}' | sort -nr | awk 'NR==1{print $2}')"
    [[ -n "$STORAGE" ]] || die "Kein aktives Storage für Container gefunden (content: rootdir)."
  fi
  TEMPLATE_STORAGE="$(pvesm status -content vztmpl 2>/dev/null | awk 'NR>1 && $3=="active" {print $1; exit}')"
  [[ -n "$TEMPLATE_STORAGE" ]] || die "Kein Storage für Container-Templates gefunden (content: vztmpl)."
  if [[ -z "$BRIDGE" ]]; then
    if ip link show vmbr0 >/dev/null 2>&1; then BRIDGE="vmbr0"
    else BRIDGE="$(ip -o link show type bridge | awk -F': ' 'NR==1{print $2}')"; fi
    [[ -n "$BRIDGE" ]] || die "Keine Netzwerk-Bridge gefunden."
  fi
}

advanced() {
  echo; printf '%sErweiterte Einstellungen%s (Enter = Vorschlag übernehmen)\n' "$B" "$N"
  ask CTID "Container-ID"; ask CT_HOSTNAME "Hostname"; ask CORES "CPU-Kerne"; ask MEMORY "RAM (MB)"
  ask DISK "Disk (GB)"; ask STORAGE "Storage"; ask BRIDGE "Bridge"; ask NET "IPv4 (CIDR oder dhcp)"
  if [[ "$NET" != "dhcp" ]]; then ask GATEWAY "Gateway"; fi
  ask VLAN "VLAN-Tag (leer = keins)"; ask DNS "DNS-Server (leer = vom Host)"
}

validate() {
  valid_int "$CTID" && (( CTID >= 100 )) || die "Ungültige CT-ID: $CTID"
  [[ "$CT_HOSTNAME" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$ ]] || die "Ungültiger Hostname: $CT_HOSTNAME"
  valid_int "$CORES" && (( CORES >= 1 )) || die "Ungültige CPU-Anzahl"
  valid_int "$MEMORY" && (( MEMORY >= 1024 )) || die "RAM muss mindestens 1024 MB sein"
  valid_int "$DISK" && (( DISK >= 8 )) || die "Disk muss mindestens 8 GB sein"
  pvesm status | awk 'NR>1{print $1}' | grep -qx "$STORAGE" || die "Storage '$STORAGE' existiert nicht."
  ip link show "$BRIDGE" >/dev/null 2>&1 || die "Bridge '$BRIDGE' existiert nicht."
  if [[ "$NET" != "dhcp" ]]; then
    [[ "$NET" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$ ]] || die "IP bitte als CIDR angeben, z. B. 192.168.1.50/24"
    [[ -n "$GATEWAY" ]] || die "Bei statischer IP wird ein Gateway (--gw) benötigt."
  fi
  [[ -z "$VLAN" ]] || valid_int "$VLAN" || die "Ungültiger VLAN-Tag"
  if pct status "$CTID" >/dev/null 2>&1; then die "CT $CTID existiert bereits. Andere ID wählen oder --update $CTID nutzen."; fi
}

summary() {
  cat <<SUM

${B}AI Trade Lab ${APP_VERSION} – Installation${N}
  CT-ID        ${CTID}
  Hostname     ${CT_HOSTNAME}
  CPU / RAM    ${CORES} Kerne / ${MEMORY} MB
  Disk         ${DISK} GB auf ${STORAGE}
  Netzwerk     ${BRIDGE}, ${NET}${GATEWAY:+, GW ${GATEWAY}}${VLAN:+, VLAN ${VLAN}}
  Typ          unprivilegiert, nesting+keyctl (für Docker), Autostart an
  Modus        PAPER · Live LOCKED

SUM
  if [[ $ASSUME_YES -eq 0 ]]; then
    local a; read -r -p "Fortfahren? [J/n] " a </dev/tty || true
    [[ -z "$a" || "$a" =~ ^[JjYy]$ ]] || die "Abgebrochen."
  fi
}

# ── Container ────────────────────────────────────────────────────────────────
ensure_template() {
  info "Suche aktuelles Debian-Template …"
  pveam update >/dev/null 2>&1 || warn "Template-Liste konnte nicht aktualisiert werden – nutze vorhandene."
  TEMPLATE="$(pveam available --section system 2>/dev/null | awk '{print $2}' | grep -E '^debian-1[2-9]-standard_.*amd64' | sort -V | tail -n1 || true)"
  if [[ -z "$TEMPLATE" ]]; then
    TEMPLATE="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk '{print $1}' | grep -oE 'debian-1[2-9]-standard_[^ ]+' | sort -V | tail -n1 || true)"
  fi
  [[ -n "$TEMPLATE" ]] || die "Kein Debian-12/13-Template gefunden."
  if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
    info "Lade $TEMPLATE …"
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE" >/dev/null
  fi
  ok "Template: $TEMPLATE"
}

create_ct() {
  local net="name=eth0,bridge=${BRIDGE},ip=${NET}"
  [[ -n "$GATEWAY" ]] && net+=",gw=${GATEWAY}"
  [[ -n "$VLAN" ]] && net+=",tag=${VLAN}"
  local extra=()
  [[ -n "$DNS" ]] && extra+=(--nameserver "$DNS")

  info "Erstelle CT $CTID …"
  pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
    --hostname "$CT_HOSTNAME" --cores "$CORES" --memory "$MEMORY" --swap "$SWAP" \
    --rootfs "${STORAGE}:${DISK}" --net0 "$net" \
    --unprivileged 1 --features nesting=1,keyctl=1 --onboot 1 --ostype debian \
    --tags "ai-trade-lab;paper" \
    --description "AI Trade Lab ${APP_VERSION} – privat, Paper-Trading. Dashboard: Port ${APP_PORT}" \
    "${extra[@]}" >/dev/null
  pct start "$CTID"
  ok "CT $CTID läuft"

  info "Warte auf Netzwerk …"
  for _ in $(seq 1 60); do
    if pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1; then ok "Netzwerk bereit"; return 0; fi
    sleep 2
  done
  die "Container hat kein Netzwerk/DNS. Bridge, DHCP bzw. Gateway prüfen."
}

in_ct() { pct exec "$CTID" -- bash -c "$1"; }

install_docker() {
  info "Installiere Docker im Container (dauert 1–3 Minuten) …"
  in_ct '
    set -e
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg >/dev/null
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
    systemctl enable --now docker >/dev/null
    apt-get install -y -qq unattended-upgrades >/dev/null || true
  '
  ok "Docker: $(in_ct 'docker --version')"
}

# ── App ──────────────────────────────────────────────────────────────────────
push_bundle() {
  local tmp; tmp="$(mktemp)"
  payload_to "$tmp"
  pct push "$CTID" "$tmp" /tmp/aitra-bundle.tar.gz
  rm -f "$tmp"
}

deploy_app() {
  info "Installiere AI Trade Lab ${APP_VERSION} …"
  push_bundle
  in_ct "
    set -e
    mkdir -p ${APP_DIR}/data
    tar -xzf /tmp/aitra-bundle.tar.gz -C ${APP_DIR}
    rm -f /tmp/aitra-bundle.tar.gz
    cd ${APP_DIR}
    if [ ! -f .env ]; then
      cp .env.example .env
      TOKEN=\$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
      sed -i \"s/^ADMIN_TOKEN=.*/ADMIN_TOKEN=\${TOKEN}/\" .env
    fi
    chmod 600 .env
    chown -R 10001:10001 data
    docker compose up -d --build --quiet-pull >/dev/null 2>&1 || docker compose up -d --build
  "
}

wait_healthy() {
  info "Healthcheck …"
  for _ in $(seq 1 60); do
    if in_ct "curl -fsS http://127.0.0.1:${APP_PORT}/api/health >/dev/null" 2>/dev/null; then return 0; fi
    sleep 2
  done
  return 1
}

ct_ip() { in_ct "hostname -I | awk '{print \$1}'"; }

finish() {
  local ip; ip="$(ct_ip)"
  cat <<DONE

${G}${B}✔ AI Trade Lab ${APP_VERSION} läuft${N}

  Dashboard     ${B}http://${ip}:${APP_PORT}${N}
  Container     CT ${CTID} (${CT_HOSTNAME})
  Modus         PAPER · Live LOCKED · 100 USDC virtuell

  Admin-Token anzeigen (zum Freigeben des Kill Switch):
    pct exec ${CTID} -- grep ADMIN_TOKEN ${APP_DIR}/.env

  Update später:  bash $(basename "$SCRIPT_PATH") --update ${CTID}
  Backup:         vzdump ${CTID} --mode snapshot
  Logs:           pct exec ${CTID} -- docker logs --tail 50 ai-trade-lab

DONE
}

# ── Update mit Backup + Rollback ─────────────────────────────────────────────
update() {
  valid_int "$CTID" || die "--update braucht eine CT-ID"
  pct status "$CTID" >/dev/null 2>&1 || die "CT $CTID existiert nicht."
  pct config "$CTID" | grep -q 'ai-trade-lab' || die "CT $CTID sieht nicht nach AI Trade Lab aus (Tag fehlt)."
  [[ "$(pct status "$CTID" | awk '{print $2}')" == "running" ]] || pct start "$CTID"

  local old; old="$(in_ct "cat ${APP_DIR}/VERSION 2>/dev/null || echo unbekannt")"
  info "Update CT ${CTID}: ${old} → ${APP_VERSION}"
  if [[ $ASSUME_YES -eq 0 ]]; then
    local a; read -r -p "Fortfahren? [J/n] " a </dev/tty || true
    [[ -z "$a" || "$a" =~ ^[JjYy]$ ]] || die "Abgebrochen."
  fi

  local ts; ts="$(date +%Y%m%d-%H%M%S)"
  info "Backup von Daten und aktueller Version …"
  in_ct "
    set -e
    mkdir -p /opt/ai-trade-lab-backups
    tar -czf /opt/ai-trade-lab-backups/${ts}-v${old}.tar.gz -C /opt ai-trade-lab
    rm -rf ${APP_DIR}.prev && mkdir -p ${APP_DIR}.prev
    tar -cf - -C ${APP_DIR} --exclude=./data --exclude=./.env . | tar -xf - -C ${APP_DIR}.prev
    ls -1t /opt/ai-trade-lab-backups/*.tar.gz | tail -n +6 | xargs -r rm -f
  "
  ok "Backup: /opt/ai-trade-lab-backups/${ts}-v${old}.tar.gz (im Container)"

  deploy_app
  if wait_healthy; then
    ok "Update erfolgreich"
    in_ct "docker image prune -f >/dev/null 2>&1 || true"
    finish
  else
    warn "Healthcheck fehlgeschlagen – Rollback auf ${old} …"
    in_ct "
      set -e
      cd ${APP_DIR}
      find . -mindepth 1 -maxdepth 1 ! -name data ! -name .env -exec rm -rf {} +
      tar -cf - -C ${APP_DIR}.prev . | tar -xf - -C ${APP_DIR}
      docker compose up -d --build
    "
    wait_healthy && die "Rollback auf ${old} erfolgreich – Update wurde verworfen." \
                 || die "Rollback fehlgeschlagen. Backup liegt unter /opt/ai-trade-lab-backups/ im CT."
  fi
}

# ── Ablauf ───────────────────────────────────────────────────────────────────
main() {
  preflight
  if [[ "$MODE" == "update" ]]; then update; return; fi
  detect
  [[ $ADVANCED -eq 1 ]] && advanced
  validate
  summary
  ensure_template
  create_ct
  install_docker
  deploy_app
  wait_healthy || die "App startet nicht. Logs: pct exec ${CTID} -- docker logs ai-trade-lab"
  finish
}

main "$@"
exit 0
__AITRA_PAYLOAD__
