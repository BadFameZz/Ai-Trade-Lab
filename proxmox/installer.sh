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
#
#  Abbrechen: Strg-C beendet geordnet und zeigt, falls schon ein Container
#  angelegt wurde, den passenden 'pct destroy'-Befehl. Ein 'kill <pid>' von
#  außen wirkt dagegen erst, wenn das gerade laufende Kommando zurückkehrt
#  (pct create, apt-get, docker compose) – das kann Minuten dauern und ist
#  kein Hänger. Wer sofort abbrechen muss, nimmt 'kill -9'; dann entfällt
#  allerdings der Aufräum-Hinweis.
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
# Volles Protokoll aller langlaufenden Kommandos. Der Spinner speist seine
# Unterzeile daraus, und fail() zeigt im Fehlerfall das Ende der Datei.
LOG="${AITRA_LOG:-/var/log/ai-trade-lab-install.log}"

if [[ -t 1 ]]; then
  TTY=1
  B=$'\e[1m'; D=$'\e[2m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; C=$'\e[36m'; N=$'\e[0m'
else
  TTY=0
  B=""; D=""; G=""; Y=""; R=""; C=""; N=""
fi

W=56          # Mindestbreite der Boxen; lange Werte weiten sie auf
KEYW=13       # Spaltenbreite der Schlüssel in einer Box
TITLEW=34     # Spaltenbreite der Schrittnamen
# Schrittnamen bleiben ASCII: der Spinner richtet sie mit printf '%-*s' aus,
# und printf zählt Bytes. In den Boxen ist das egal, die rechnen über _clen.

# LOG_READY entscheidet, ob fail() das Protokoll überhaupt anfassen darf.
# Ohne dieses Flag zeigte ein Abbruch in preflight() das Ende eines ALTEN
# Protokolls vom vorigen Lauf – eine falsche Fährte.
LOG_READY=0

# AITRA_LOG kommt aus der Umgebung und landet in einer root-Shell als Ziel von
# ":>" und chmod. Ungeprüft heißt das: beliebige Datei leeren und auf 600
# setzen. Deshalb Pfad einschränken, Symlinks ablehnen und die Datei unter
# umask 077 anlegen – "chmod danach" lässt sonst ein Fenster mit 0644 offen.
log_init() {
  local want="/var/log/ai-trade-lab-install.log"
  # AITRA_LOG ist ein Testhaken und darf im Echtbetrieb nicht ziehen. Ein
  # case-Glob taugt hier nicht: "/var/log/*" matcht auch
  # "/var/log/../../etc/passwd", weil * über / hinweggeht. Deshalb exakter
  # Stringvergleich, und zusätzlich jedes ".." abweisen.
  if [[ -z "${AITRA_LIB_ONLY:-}" && "$LOG" != "$want" ]]; then
    LOG="$(mktemp)"; LOG_READY=1; return 0
  fi
  case "$LOG" in *..*) LOG="$(mktemp)"; LOG_READY=1; return 0 ;; esac
  # rm vor dem Anlegen: sonst hilft [[ -L ]] nicht gegen einen Hardlink, und
  # zwischen Prüfung und Öffnen passt ein untergeschobener Symlink.
  # set -C öffnet mit O_EXCL und erledigt Symlink wie Wettlauf in einem Zug.
  rm -f -- "$LOG" 2>/dev/null || true
  ( umask 077; set -C; : > "$LOG" ) 2>/dev/null || LOG="$(mktemp)"
  # umask wirkt nur beim Anlegen. Existiert die Datei schon, behielte sie
  # ihren Modus – deshalb zusätzlich hart setzen.
  chmod 600 "$LOG" 2>/dev/null || true
  LOG_READY=1
}

warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*" >&2; }
detail() { printf '         %s%s%s\n' "$D" "$*" "$N"; }

_rep() { local i s=''; for ((i=0; i<$1; i++)); do s+="$2"; done; printf '%s' "$s"; }

# Zeichen zählen, nicht Bytes – und zwar ohne sich auf die Locale zu verlassen.
# ${#s} liefert unter LANG=C Bytes, dann rutscht jeder Rahmen mit einem '·'
# oder Umlaut im Wert nach links. In UTF-8 ist jedes Folgebyte 10xxxxxx
# (0x80–0xBF) Teil des vorigen Zeichens; wer die wegwirft, zählt Zeichen.
_clen() { printf '%s' "$1" | LC_ALL=C tr -d '\200-\277' | LC_ALL=C wc -c | tr -d ' '; }

_pad() {
  local s="$1" w="$2" n
  n=$(( w - $(_clen "$s") ))
  (( n > 0 )) || n=0
  printf '%s%s' "$s" "$(_rep "$n" ' ')"
}

banner() {
  printf '\n'
  printf '  %s%s▄▀█ █ ▀█▀ █▀█ ▄▀█%s   %sAI Trade Lab%s\n' "$B" "$C" "$N" "$B" "$N"
  printf '  %s%s█▀█ █  █  █▀▄ █▀█%s   %sv%s · Paper Trading · Live LOCKED%s\n\n' \
         "$B" "$C" "$N" "$D" "$APP_VERSION" "$N"
}

# box "Titel" "Schlüssel" "Wert" ["Schlüssel" "Wert" …]
#
# Die Box misst sich an ihrem längsten Wert, statt lange Werte abzuschneiden:
# eine halbe IP-Adresse im Rahmen hilft niemandem. W ist nur die Mindestbreite.
# Bei statischer IP mit Gateway und VLAN wird die Netzwerk-Zeile über 50
# Zeichen lang – genau dafür wächst sie.
box() {
  local title="$1"; shift
  local -a keys=() vals=()
  while (( $# >= 2 )); do keys+=("$1"); vals+=("$2"); shift 2; done

  # inner = Anzahl der Striche zwischen ╰ und ╯. Eine Inhaltszeile misst
  # │ + 2 + KEYW + 1 + Wert + 1 + │, also braucht der Wert inner-KEYW-4.
  local inner=$(( W - 2 )) i n
  for i in "${!keys[@]}"; do
    n=$(( KEYW + 4 + $(_clen "${vals[i]}") ))
    (( n > inner )) && inner=$n
  done
  n=$(( $(_clen "$title") + 5 ))
  (( n > inner )) && inner=$n

  printf '  %s╭─ %s %s╮%s\n' "$D" "$title" "$(_rep $(( inner - 3 - $(_clen "$title") )) '─')" "$N"
  for i in "${!keys[@]}"; do
    printf '  %s│%s  %s %s %s│%s\n' "$D" "$N" \
           "$(_pad "${keys[i]}" "$KEYW")" "$(_pad "${vals[i]}" $(( inner - KEYW - 4 )))" "$D" "$N"
  done
  printf '  %s╰%s╯%s\n' "$D" "$(_rep "$inner" '─')" "$N"
}

rule() { printf '  %s%s%s\n' "$D" "$(_rep "$W" '─')" "$N"; }

# ── Schritte mit Spinner ─────────────────────────────────────────────────────
STEP_N=0
STEP_TOTAL=0
STEP_TITLE=""
_SPIN_PID=""

_spin_loop() {
  local title="$1" t0="$2" n="$3" total="$4"
  # Array statt Substring: ${s:i:1} zaehlt nur in einer UTF-8-Locale Zeichen,
  # sonst Bytes – auf einem Proxmox-Host ohne gesetztes LANG gaebe das
  # Zeichensalat statt Spinner.
  local -a frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
  local i=0 el last
  while :; do
    el=$(( $(date +%s) - t0 ))
    last=""
    if [[ -s "$LOG" ]]; then
      last="$(tail -n1 "$LOG" 2>/dev/null | tr -d '\r' | tr -cd '[:print:] ' | cut -c1-46)"
    fi
    printf '\r\e[2K  %s[%d/%d]%s  %-*s %s%s%s %4ss' \
           "$D" "$n" "$total" "$N" "$TITLEW" "$title" "$C" "${frames[i%10]}" "$N" "$el"
    printf '\n\e[2K%s' "${last:+         ${D}└ ${last}${N}}"
    printf '\e[1A\r'
    i=$(( i + 1 ))
    sleep 0.12
  done
}

_spin_stop() {
  [[ -n "$_SPIN_PID" ]] || return 0
  kill "$_SPIN_PID" 2>/dev/null || true
  # Nicht unbegrenzt warten: hängt das Terminal (stehende SSH-Sitzung), sitzt
  # der Spinner im write() fest, überlebt das TERM und wait bliebe ewig stehen.
  local i
  for i in 1 2 3 4 5; do kill -0 "$_SPIN_PID" 2>/dev/null || break; sleep 0.1; done
  kill -9 "$_SPIN_PID" 2>/dev/null || true
  wait "$_SPIN_PID" 2>/dev/null || true
  _SPIN_PID=""
  printf '\r\e[2K\n\e[2K\e[1A\r'
}

_STEP_T0=0

_step_begin() {
  STEP_N=$(( STEP_N + 1 ))
  STEP_TITLE="$1"
  _STEP_T0=$(date +%s)
  if (( TTY )); then
    _spin_loop "$1" "$_STEP_T0" "$STEP_N" "$STEP_TOTAL" &
    _SPIN_PID=$!
  else
    printf '  [%d/%d] %s …\n' "$STEP_N" "$STEP_TOTAL" "$1"
  fi
}

_step_end() {
  local rc="$1" el=$(( $(date +%s) - _STEP_T0 )) mark col
  _spin_stop
  if (( rc == 0 )); then mark='✔'; col="$G"; else mark='✖'; col="$R"; fi
  if (( TTY )); then
    printf '  %s[%d/%d]%s  %s %s%s%s %4ss\n' \
           "$D" "$STEP_N" "$STEP_TOTAL" "$N" "$(_pad "$STEP_TITLE" "$TITLEW")" "$col" "$mark" "$N" "$el"
  else
    printf '  [%d/%d] %s — %s (%ss) %s\n' "$STEP_N" "$STEP_TOTAL" "$STEP_TITLE" \
           "$( ((rc==0)) && echo ok || echo FEHLER )" "$el" "$(date +%H:%M:%S)"
  fi
}

# step "Titel" befehl [args…] – das Kommando läuft im aktuellen Shell-Kontext,
# nicht in einer Subshell, damit Funktionen wie ensure_template() weiterhin
# globale Variablen setzen können. Gedreht wird stattdessen im Hintergrund.
#
# WICHTIG: Der Aufruf steht bewusst NICHT als linker Operand eines || da.
# Ein "$@" || rc=$? würde errexit und den ERR-Trap im gesamten Rumpf der
# aufgerufenen Funktion abschalten – dann liefe create_ct nach einem
# gescheiterten 'pct create' weiter in die Netzwerk-Warteschleife und meldete
# nach 120 s die falsche Ursache. Stattdessen bleibt errexit aktiv, der
# ERR-Trap landet in fail(), und fail() schließt die offene Schrittzeile rot ab.
STEP_OPEN=0
STEP_CUR=""

step() {
  local title="$1"; shift
  _step_begin "$title"
  STEP_OPEN=1
  STEP_CUR="$title"
  "$@"
  STEP_OPEN=0
  STEP_CUR=""
  _step_end 0
}

# Wie step(), aber ein Fehlschlag bricht nicht ab – der Aufrufer entscheidet.
# Nur für Funktionen benutzen, die ihre Fehler selbst behandeln und einen
# Rückgabewert liefern (wait_healthy, _update_rollback): innerhalb von "$@"
# ist errexit hier tatsächlich abgeschaltet, das ist für diese gewollt.
step_soft() {
  local title="$1"; shift
  _step_begin "$title"
  local rc=0
  "$@" || rc=$?
  _step_end "$rc"
  return "$rc"
}

fail() {
  # Zuerst die Fallen lösen: scheitert unten die tail-Pipeline (unlesbares
  # Protokoll), riefe der ERR-Trap sonst fail() ein zweites Mal auf und
  # überspränge dabei den pct-destroy-Hinweis – also genau die Information,
  # wegen der dieser Block existiert.
  trap - ERR EXIT INT TERM
  if (( STEP_OPEN )); then STEP_OPEN=0; _step_end 1; else _spin_stop; fi
  printf '\n  %s%s✖  %s%s\n' "$B" "$R" "$*" "$N"
  if (( LOG_READY )) && [[ -s "${LOG:-}" ]]; then
    printf '\n  %sLetzte Zeilen aus %s:%s\n' "$D" "$LOG" "$N"
    tail -n 20 "$LOG" 2>/dev/null | tr -cd '[:print:]\n' | sed 's/^/    /' || true
  fi
  ct_hint
  printf '\n'
  exit 1
}

# Einen halb fertigen Container nicht ungefragt wegräumen – darin können
# bereits Daten liegen. Nur sagen, dass er da ist und wie man ihn loswird.
# Eigene Funktion, damit auch der Abbruch per Strg-C den Hinweis zeigt.
ct_hint() {
  [[ "${MODE:-install}" == "install" && -n "${CTID:-}" ]] || return 0
  pct status "$CTID" >/dev/null 2>&1 || return 0
  printf '\n  %sCT %s wurde bereits angelegt und läuft noch.%s\n' "$Y" "$CTID" "$N"
  printf '  Aufräumen (löscht den Container samt Inhalt):\n'
  printf '    %spct destroy %s --force%s\n' "$B" "$CTID" "$N"
}

die() { fail "$*"; }

# Ohne STEP_CUR wäre die Meldung immer "Zeile 199: \"$@\"" – die Zeile in
# step(), die bei jedem Fehlschlag dieselbe ist und nichts verrät.
trap 'fail "${STEP_CUR:+Schritt fehlgeschlagen: $STEP_CUR — }Zeile ${LINENO}: ${BASH_COMMAND}"' ERR
trap '_spin_stop' EXIT
# Bei Strg-C läuft der EXIT-Trap nicht; ohne dies bliebe der Spinner als
# verwaister Hintergrundprozess stehen und schriebe weiter ins Terminal.
trap '_spin_stop; printf "\n"; ct_hint; exit 130' INT TERM

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
}

# Kontextzeile unter dem Banner – erst nach detect() aufrufen, sie zeigt
# Storage und Bridge.
host_line() {
  printf '  %s%s · Host %s · Storage %s · Bridge %s%s\n\n' \
         "$D" "$(pveversion | head -n1)" "$(hostname)" "${STORAGE:-?}" "${BRIDGE:-?}" "$N"
}

valid_int() { [[ "$1" =~ ^[0-9]+$ ]]; }

# Rückfrage vor einem Eingriff. Ohne Terminal und ohne -y wird abgebrochen,
# statt ungefragt einen Container anzulegen.
confirm() {
  (( ASSUME_YES )) && return 0
  [[ -r /dev/tty ]] || die "Kein Terminal für die Rückfrage. Entweder interaktiv starten oder mit -y bestätigen."
  local a=""
  read -r -p "  Fortfahren? [J/n] " a </dev/tty || true
  [[ -z "$a" || "$a" =~ ^[JjYy]$ ]] || die "Abgebrochen."
  printf '\n'
}

ask() { # ask VAR "Frage" – übernimmt Enter als Standard
  local var="$1" prompt="$2" cur="${!1}" ans=""
  [[ -r /dev/tty ]] || die "--advanced braucht ein Terminal."
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
  # GATEWAY landet in der --net0-Optionsliste: ein "1.2.3.4,firewall=0" würde
  # dort die NIC-Firewall des Containers abschalten, ohne dass es auffällt.
  [[ -z "$GATEWAY" || "$GATEWAY" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || die "Ungültiges Gateway: $GATEWAY"
  [[ -z "$DNS"     || "$DNS"     =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || die "Ungültiger DNS-Server: $DNS"
  if pct status "$CTID" >/dev/null 2>&1; then die "CT $CTID existiert bereits. Andere ID wählen oder --update $CTID nutzen."; fi
}

summary() {
  box "Installation" \
    "CT-ID"     "${CTID}" \
    "Hostname"  "${CT_HOSTNAME}" \
    "CPU / RAM" "${CORES} Kerne / ${MEMORY} MB" \
    "Disk"      "${DISK} GB auf ${STORAGE}" \
    "Netzwerk"  "${BRIDGE}, ${NET}${GATEWAY:+, GW ${GATEWAY}}${VLAN:+, VLAN ${VLAN}}" \
    "Typ"       "unprivilegiert, nesting+keyctl" \
    "Modus"     "PAPER · Live LOCKED"
  printf "\n"
  confirm
}

# ── Container ────────────────────────────────────────────────────────────────
ensure_template() {
  pveam update >>"$LOG" 2>&1 || echo "Hinweis: Template-Liste nicht aktualisierbar, nutze vorhandene." >>"$LOG"
  TEMPLATE="$(pveam available --section system 2>/dev/null | awk '{print $2}' | grep -E '^debian-1[2-9]-standard_.*amd64' | sort -V | tail -n1 || true)"
  if [[ -z "$TEMPLATE" ]]; then
    TEMPLATE="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk '{print $1}' | grep -oE 'debian-1[2-9]-standard_[^ ]+' | sort -V | tail -n1 || true)"
  fi
  [[ -n "$TEMPLATE" ]] || die "Kein Debian-12/13-Template gefunden."
  if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
    echo "Lade Template $TEMPLATE …" >>"$LOG"
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE" >>"$LOG" 2>&1
  fi
}

create_ct() {
  local net="name=eth0,bridge=${BRIDGE},ip=${NET}"
  [[ -n "$GATEWAY" ]] && net+=",gw=${GATEWAY}"
  [[ -n "$VLAN" ]] && net+=",tag=${VLAN}"
  local extra=()
  [[ -n "$DNS" ]] && extra+=(--nameserver "$DNS")

  echo "Erstelle CT $CTID …" >>"$LOG"
  pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
    --hostname "$CT_HOSTNAME" --cores "$CORES" --memory "$MEMORY" --swap "$SWAP" \
    --rootfs "${STORAGE}:${DISK}" --net0 "$net" \
    --unprivileged 1 --features nesting=1,keyctl=1 --onboot 1 --ostype debian \
    --tags "ai-trade-lab;paper" \
    --description "AI Trade Lab ${APP_VERSION} – privat, Paper-Trading. Dashboard: Port ${APP_PORT}" \
    "${extra[@]}" >>"$LOG" 2>&1
  pct start "$CTID" >>"$LOG" 2>&1

  echo "Warte auf Netzwerk und DNS im Container …" >>"$LOG"
  for _ in $(seq 1 60); do
    if pct exec "$CTID" -- getent hosts deb.debian.org >>"$LOG" 2>&1; then return 0; fi
    sleep 2
  done
  die "Container hat kein Netzwerk/DNS. Bridge, DHCP bzw. Gateway prüfen."
}

# Kommando im Container, Ausgabe ins Protokoll (für Seiteneffekte).
in_ct() { pct exec "$CTID" -- bash -c "$1" >>"$LOG" 2>&1; }
# Kommando im Container, Ausgabe auf stdout (für abgefragte Werte).
in_ct_out() { pct exec "$CTID" -- bash -c "$1" 2>>"$LOG"; }

install_docker() {
  in_ct '
    set -e
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -q
    apt-get install -y -q ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
    apt-get install -y -q unattended-upgrades || true
  '
}

# ── App ──────────────────────────────────────────────────────────────────────
# die() hier beendet sofort, auch unter step_soft – und das ist richtig:
# zu diesem Zeitpunkt ist im Container noch nichts verändert, es gibt also
# nichts zurückzurollen. Bitte nicht "reparieren".
#
# Die Rückgabewerte hier explizit prüfen: im Update-Pfad läuft deploy_app
# unter step_soft, dort ist errexit im Rumpf abgeschaltet. Ohne die Prüfungen
# wäre das letzte Kommando das rm, und ein leeres Bundle liefe stumm weiter
# bis zum tar im Container.
push_bundle() {
  local tmp rc=0
  tmp="$(mktemp)"
  payload_to "$tmp" || rc=$?
  (( rc == 0 )) && [[ -s "$tmp" ]] || { rm -f "$tmp"; die "App-Bundle konnte nicht aus dem Skript gelesen werden."; }
  pct push "$CTID" "$tmp" /tmp/aitra-bundle.tar.gz >>"$LOG" 2>&1 \
    || { rm -f "$tmp"; die "Bundle-Übertragung in CT ${CTID} fehlgeschlagen."; }
  rm -f "$tmp"
}

deploy_app() {
  echo "Rolle AI Trade Lab ${APP_VERSION} aus …" >>"$LOG"
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
    # Zweiter Versuch: ein abgerissener Registry-Pull soll die Installation
    # nicht kippen. Das stand vorher als || hinter dem --quiet-pull-Aufruf.
    docker compose up -d --build || docker compose up -d --build
  "
}

wait_healthy() {
  for _ in $(seq 1 60); do
    if in_ct "curl -fsS http://127.0.0.1:${APP_PORT}/api/health >/dev/null"; then return 0; fi
    sleep 2
  done
  # Damit fail() etwas Brauchbares zeigen kann, statt nur apt-Zeilen von vorhin.
  echo "--- Healthcheck nach 120 s ohne Antwort. Container-Logs: ---" >>"$LOG"
  in_ct "docker logs --tail 40 ai-trade-lab" || true
  return 1
}

ct_ip() { in_ct_out "hostname -I | awk '{print \$1}'"; }

finish() {
  local ip; ip="$(ct_ip)"
  printf '\n'; rule
  printf '  %s%s✔  AI Trade Lab %s läuft%s\n\n' "$B" "$G" "$APP_VERSION" "$N"
  box "Zugang" \
    "Dashboard" "http://${ip}:${APP_PORT}" \
    "Container" "CT ${CTID} (${CT_HOSTNAME})" \
    "Modus"     "PAPER · Live LOCKED" \
    "Kapital"   "100 USDC virtuell"
  printf '\n  %sAdmin-Token (zum Freigeben des Kill Switch):%s\n' "$D" "$N"
  printf '    pct exec %s -- grep ADMIN_TOKEN %s/.env\n\n' "$CTID" "$APP_DIR"
  printf '  %sUpdate%s      bash %s --update %s\n' "$D" "$N" "$(basename "$SCRIPT_PATH")" "$CTID"
  printf '  %sBackup%s      vzdump %s --mode snapshot\n' "$D" "$N" "$CTID"
  printf '  %sApp-Logs%s    pct exec %s -- docker logs --tail 50 ai-trade-lab\n' "$D" "$N" "$CTID"
  printf '  %sProtokoll%s   %s\n\n' "$D" "$N" "$LOG"
}

# ── Update mit Backup + Rollback ─────────────────────────────────────────────
update() {
  valid_int "$CTID" || die "--update braucht eine CT-ID"
  pct status "$CTID" >/dev/null 2>&1 || die "CT $CTID existiert nicht."
  pct config "$CTID" | grep -q 'ai-trade-lab' || die "CT $CTID sieht nicht nach AI Trade Lab aus (Tag fehlt)."
  [[ "$(pct status "$CTID" | awk '{print $2}')" == "running" ]] || pct start "$CTID"

  # Der Wert kommt aus einer Datei IM Container und wird unten in einen
  # String interpoliert, den pct exec als root ausführt. Ein VERSION mit
  # $(…) darin wäre damit Codeausführung – also prüfen, nicht vertrauen.
  local old; old="$(in_ct_out "cat ${APP_DIR}/VERSION 2>/dev/null || echo unbekannt")"
  [[ "$old" =~ ^[0-9A-Za-z._-]{1,32}$ ]] || old="unbekannt"
  box "Update" \
    "Container" "CT ${CTID}" \
    "Version"   "${old} → ${APP_VERSION}" \
    "Sicherung" "/opt/ai-trade-lab-backups (letzte 5)"
  printf "\n"
  confirm

  local ts; ts="$(date +%Y%m%d-%H%M%S)"
  STEP_TOTAL=3
  step "Backup anlegen" _update_backup "$ts" "$old"
  # step_soft statt step: scheitert das Ausrollen selbst, liegt der neue Stand
  # schon halb im Container. Mit step würde der ERR-Trap hier abbrechen und am
  # Rollback vorbeilaufen – obwohl --help "mit Backup + Rollback" verspricht.
  if ! step_soft "App ausrollen" deploy_app; then
    warn "Ausrollen fehlgeschlagen – Rollback auf ${old} …"
    _rollback "$old"
  fi
  _update_verify "$ts" "$old"
}

_update_backup() {
  local ts="$1" old="$2"
  # umask 077: der Tarball enthält die .env mit dem ADMIN_TOKEN und landet
  # sonst als 0644 in einem 0755-Verzeichnis – und wandert so in jedes vzdump.
  in_ct "
    set -e
    umask 077
    mkdir -p /opt/ai-trade-lab-backups
    chmod 700 /opt/ai-trade-lab-backups
    tar -czf /opt/ai-trade-lab-backups/${ts}-v${old}.tar.gz -C /opt ai-trade-lab
    rm -rf ${APP_DIR}.prev && mkdir -p ${APP_DIR}.prev
    tar -cf - -C ${APP_DIR} --exclude=./data --exclude=./.env . | tar -xf - -C ${APP_DIR}.prev
    ls -1t /opt/ai-trade-lab-backups/*.tar.gz | tail -n +6 | xargs -r rm -f
  "
}

# Der Healthcheck darf hier nicht über step() laufen: schlägt er fehl, ist das
# kein Abbruch, sondern der Einstieg in den Rollback.
_update_verify() {
  local ts="$1" old="$2"
  if step_soft "Healthcheck" wait_healthy; then
    in_ct "docker image prune -f || true"
    detail "Sicherung: /opt/ai-trade-lab-backups/${ts}-v${old}.tar.gz (im CT)"
    finish
    return 0
  fi
  warn "Healthcheck fehlgeschlagen – Rollback auf ${old} …"
  _rollback "$old"
}

# Gemeinsamer Rollback für beide Wege, die dorthin führen: ein gescheitertes
# Ausrollen und ein roter Healthcheck. Baut das alte Image neu und wartet
# erneut auf Gesundheit – zusammen mehrere Minuten, deshalb mit eigenen
# Schritten statt stillem Bildschirm. Endet immer per die().
_rollback() {
  local old="$1"
  # Noch zwei Schritte: der Rollback selbst und der Healthcheck danach.
  # Fest auf 5 waere falsch, wenn schon das Ausrollen scheitert – dann
  # endet der Lauf bei [4/5] und die 5 kommt nie.
  STEP_TOTAL=$(( STEP_N + 2 ))
  step_soft "Rollback auf ${old}" _update_rollback || true
  if step_soft "Healthcheck nach Rollback" wait_healthy; then
    die "Rollback auf ${old} erfolgreich – das Update wurde verworfen."
  fi
  die "Rollback fehlgeschlagen. Sicherung liegt unter /opt/ai-trade-lab-backups/ im CT."
}

_update_rollback() {
  in_ct "
    set -e
    cd ${APP_DIR}
    find . -mindepth 1 -maxdepth 1 ! -name data ! -name .env -exec rm -rf {} +
    tar -cf - -C ${APP_DIR}.prev . | tar -xf - -C ${APP_DIR}
    docker compose up -d --build
  "
}

# ── Ablauf ───────────────────────────────────────────────────────────────────
main() {
  # preflight zuerst: log_init schreibt als root und darf erst laufen, wenn
  # geprüft ist, dass wir überhaupt auf einem Proxmox-Host sind. Dass fail()
  # vorher kein altes Protokoll zeigt, regelt LOG_READY statt der Reihenfolge.
  preflight
  log_init
  banner
  if [[ "$MODE" == "update" ]]; then update; return; fi
  detect
  host_line
  [[ $ADVANCED -eq 1 ]] && advanced
  validate
  summary

  STEP_TOTAL=5
  step "Debian-Template"     ensure_template
  detail "$TEMPLATE"
  step "Container anlegen"   create_ct
  step "Docker installieren" install_docker
  detail "$(in_ct_out 'docker --version')"
  step "App ausrollen"       deploy_app
  step "Healthcheck"         wait_healthy
  finish
}

# Testhaken: erlaubt es, nur die Ausgabeschicht zu laden, ohne zu installieren.
# Wird von proxmox/test-output.sh benutzt.
# Die zweite Bedingung ist wichtig: ohne sie beendet sich ein AUSGEFÜHRTER
# Installer wortlos mit Exit 0, sobald AITRA_LIB_ONLY in der Umgebung steht –
# von Hand fällt das auf, in einer Automatisierung wäre es ein falsches
# "erfolgreich". So wirkt der Haken nur beim Sourcen.
if [[ -n "${AITRA_LIB_ONLY:-}" && "${BASH_SOURCE[0]}" != "$0" ]]; then
  return 0
fi

main "$@"
exit 0
__AITRA_PAYLOAD__
