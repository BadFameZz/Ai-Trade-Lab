#!/usr/bin/env bash
# shellcheck disable=SC2034  # Werte fuer den gesourcten Installer, hier "unbenutzt"
# Treibt die Ausgabeschicht von installer.sh mit erfundenen Kommandos, ohne
# Proxmox. Zeigt, wie Banner, Boxen, Schritte, Spinner und fail() aussehen.
#
#   bash proxmox/test-output.sh          # am Terminal, mit Spinner
#   bash proxmox/test-output.sh | cat    # ohne TTY, als Klartext
#   bash proxmox/test-output.sh fail     # Fehlerfall, prueft
#   bash proxmox/test-output.sh align    # Rahmenausrichtung, prueft
#   bash proxmox/test-output.sh update   # Update- und Rollback-Wege, prueft
set -Eeuo pipefail
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$0")/.."

export AITRA_LIB_ONLY=1
export AITRA_LOG="${TMPDIR:-/tmp}/aitra-test.log"
# Positionsparameter leeren: der gesourcte Installer sieht sonst unsere
# Argumente und bricht mit "Unbekannte Option" ab.
ARGS=("$@")
set --
# shellcheck source=/dev/null
source proxmox/installer.sh
set -- ${ARGS[@]+"${ARGS[@]}"}

APP_VERSION="0.2.0-test"
CTID=121; CT_HOSTNAME="ai-trade-lab"; CORES=4; MEMORY=8192; DISK=32
STORAGE="local-lvm"; BRIDGE="vmbr0"; NET="dhcp"; GATEWAY=""; VLAN=""
MODE="install"; APP_PORT=8787; APP_DIR="/opt/ai-trade-lab"; ASSUME_YES=1
SCRIPT_PATH="/root/ai-trade-lab-install.sh"

log_init

# Ersetzt pveversion/hostname, damit die Kontextzeile ohne Proxmox etwas zeigt.
host_line() {
  printf '  %spve-manager/8.4.1/1b2d3c4e (running kernel: 6.8.12-4-pve) · Host pve · Storage %s · Bridge %s%s\n\n' \
         "$D" "$STORAGE" "$BRIDGE" "$N"
}

# Erfundene Arbeitsschritte: schreiben ins Protokoll und brauchen Zeit.
fake() {
  local secs="$1"; shift
  local line
  for line in "$@"; do
    echo "$line" >>"$LOG"
    sleep "$secs"
  done
}
boom() {
  echo "E: Unable to locate package docker-ce" >>"$LOG"
  echo "E: Failed to fetch https://download.docker.com/linux/debian/dists/trixie/InRelease" >>"$LOG"
  return 100
}
pct() { return 1; }   # "CT existiert nicht" – fail() zeigt den Hinweis dann nicht

# ── Ausrichtungsprüfung ──────────────────────────────────────────────────────
# Wird als "bash proxmox/test-output.sh align" aufgerufen. Baut Boxen mit
# absichtlich fiesen Werten (Mehrbyte-Zeichen, Umlaute) und prüft, dass alle
# Rahmenzeilen exakt gleich breit sind.
align_check() {
  local out rc=0
  out="$(
    box "Ausrichtung" \
      "ASCII"     "abcdefghij" \
      "Mittelpkt" "PAPER · Live LOCKED" \
      "Viele"     "a · b · c · d · e" \
      "Umlaute"   "Grösse: 32 GB, überprüft" \
      "Gemischt"  "läuft · 100 USDC · ✔" \
      "Lang"      "vmbr0, 192.168.178.50/24, GW 192.168.178.1, VLAN 10"
  )"
  printf '%s\n' "$out"

  # Drei Riegel statt einem. "Alle Zeilen gleich breit" allein ist grün, wenn
  # box() überhaupt nichts ausgibt – null Zeilen sind trivial gleich breit.
  # Und die Sollbreite 72 folgt unabhängig aus dem längsten Wert (54 Zeichen
  # + KEYW 13 + 4 Rahmen + 1 Trenner), hängt also nicht an _clen allein.
  local nlines widths
  # grep -c liefert bei null Treffern Exit 1; ohne || true bräche errexit hier
  # ab und der Test stürbe stumm, statt den Grund zu nennen.
  nlines="$(printf '%s\n' "$out" | grep -c . || true)"
  widths="$(printf '%s\n' "$out" | sed 's/\x1b\[[0-9;]*m//g' \
            | while IFS= read -r l; do _clen "$l"; done | sort -u | tr '\n' ' ')"
  widths="${widths% }"
  printf '\n  Rahmenzeilen: %s (erwartet 8)\n' "$nlines"
  printf '  Zeilenbreiten (Zeichen, ohne Farbcodes): %s (erwartet 72)\n' "$widths"
  if [[ "$nlines" -ne 8 ]]; then
    printf '  ERGEBNIS: FEHLER – %s statt 8 Rahmenzeilen\n' "$nlines"; rc=1
  elif [[ "$widths" != "72" ]]; then
    printf '  ERGEBNIS: FEHLER – Rahmen verrutscht\n'; rc=1
  else
    printf '  ERGEBNIS: 8 Rahmenzeilen, alle exakt 72 Zeichen\n'
  fi
  return $rc
}

# Prüft den Fehlerpfad, statt ihn nur vorzuführen. Der ERR-Trap des Installers
# bleibt dabei ausdrücklich scharf: das neue step() verlässt sich allein auf
# ihn. Wer ihn im Harnisch löscht, testet einen Pfad, den es nicht mehr gibt.
fail_check() {
  local out rc=0 ok=1
  # Der Fehlschlag MUSS in einem eigenen Prozess laufen. Stünde er hier als
  # out="$( step … )" || rc=$?, wuerde bash errexit auch innerhalb der
  # Kommandosubstitution abschalten – dann meldet der Schritt "ok" und der
  # Test prueft nichts. Genau dieselbe Falle wie in step() selbst.
  out="$( bash "$SELF" __boomrun 2>&1 )" || rc=$?
  printf '%s\n' "$out"
  printf '\n'
  [[ $rc -eq 1 ]] || { printf '  ERGEBNIS: FEHLER – Exitcode %s statt 1\n' "$rc"; ok=0; }
  grep -q '✖' <<<"$out" || { printf '  ERGEBNIS: FEHLER – fail() hat kein ✖ ausgegeben\n'; ok=0; }
  # Muss auf die Meldung von fail() treffen, nicht auf die Schrittanzeige –
  # sonst ist der Riegel gruen, obwohl STEP_CUR fehlt.
  grep -q '✖.*Schritt fehlgeschlagen: App ausrollen' <<<"$out" || { printf '  ERGEBNIS: FEHLER – Meldung nennt den Schritt nicht\n'; ok=0; }
  grep -q 'docker-ce' <<<"$out" || { printf '  ERGEBNIS: FEHLER – Protokollauszug fehlt\n'; ok=0; }
  (( ok )) || return 1
  printf '  ERGEBNIS: bricht mit 1 ab, nennt den Schritt, zeigt das Protokoll\n'
}

# ── Update- und Rollback-Wege ────────────────────────────────────────────────
# Der Rollback ist der Weg, der einem Nutzer real wehtun kann, und er lief
# bisher ungeprüft. Hier werden die Container-nahen Funktionen ersetzt und
# update() einmal pro Szenario durchgespielt – jeweils im eigenen Prozess,
# damit der ERR-Trap scharf bleibt.
_update_stubs() {
  # Global, nicht local: die Stubs unten lesen es erst beim Aufruf, da ist
  # eine local-Variable dieser Funktion laengst weg (und set -u schlaegt zu).
  AITRA_FALL="$1"
  MODE="update"; CTID=121; ASSUME_YES=1
  pct() {
    case "$1" in
      status) echo "status: running" ;;
      config) echo "tags: ai-trade-lab;paper" ;;
      *) return 0 ;;
    esac
  }
  in_ct()     { echo "in_ct: ${1//$'\n'/ }" >>"$LOG"; return 0; }
  in_ct_out() { echo "0.1.0"; }
  _update_backup()  { echo "BACKUP gelaufen"; }
  _update_rollback() {
    echo "ROLLBACK gelaufen"
    [[ "$AITRA_FALL" == "rollback_kaputt" ]] && return 9
    return 0
  }
  deploy_app() {
    echo "DEPLOY versucht"
    [[ "$AITRA_FALL" == "deploy_kaputt" ]] && return 7
    return 0
  }
  ct_ip() { echo "192.168.1.74"; }
  case "$AITRA_FALL" in
    hc_kaputt)       wait_healthy() { [[ -f "$LOG.hc" ]] && return 0; : > "$LOG.hc"; return 1; } ;;
    rollback_kaputt) wait_healthy() { return 1; } ;;
    *)               wait_healthy() { return 0; } ;;
  esac
}

# update_check "fall" "erwarteter Exitcode" "Muster…"
update_check() {
  local fall="$1" want_rc="$2"; shift 2
  local out rc=0 ok=1 muster
  rm -f "$AITRA_LOG.hc"
  out="$( bash "$SELF" __updaterun "$fall" 2>&1 )" || rc=$?
  printf '  %-18s Exit %s\n' "$fall" "$rc"
  [[ $rc -eq $want_rc ]] || { printf '    FEHLER – Exitcode %s statt %s\n' "$rc" "$want_rc"; ok=0; }
  for muster in "$@"; do
    grep -q -- "$muster" <<<"$out" || { printf '    FEHLER – fehlt: %s\n' "$muster"; ok=0; }
  done
  (( ok )) || { printf '%s\n' "$out" | sed 's/^/      | /'; return 1; }
}

update_suite() {
  local rc=0
  printf '  Update- und Rollback-Wege\n'
  # Alles gut: kein Rollback, finish.
  update_check alles_gut      0 'Dashboard' || rc=1
  # Ausrollen scheitert: MUSS in den Rollback laufen, nicht daran vorbei.
  update_check deploy_kaputt  1 'Rollback auf 0.1.0' 'ROLLBACK gelaufen' 'Update wurde verworfen' || rc=1
  # Healthcheck rot: Rollback, danach gesund.
  update_check hc_kaputt      1 'Rollback auf 0.1.0' 'ROLLBACK gelaufen' 'Update wurde verworfen' || rc=1
  # Rollback selbst scheitert: klare Ansage auf die Sicherung.
  update_check rollback_kaputt 1 'Rollback fehlgeschlagen' 'ai-trade-lab-backups' || rc=1
  (( rc == 0 )) && printf '  ERGEBNIS: alle vier Update-Wege wie erwartet\n'
  return $rc
}

if [[ "${1:-}" == "__updaterun" ]]; then
  _update_stubs "${2:-alles_gut}"
  update
  exit 0
fi

# Unterlauf für fail_check: ein einzelner Schritt, der scheitert – in einem
# eigenen Prozess, mit scharfem ERR-Trap und ohne || drumherum.
if [[ "${1:-}" == "__boomrun" ]]; then
  STEP_TOTAL=1
  step "App ausrollen" boom
  echo "UNERREICHBAR – step() hat den Fehler verschluckt"
  exit 0
fi

# align_check/fail_check geben bei einem Testfehler 1 zurück. Das ist ein
# Ergebnis, kein Absturz – deshalb hier abfangen statt den ERR-Trap zu löschen.
if [[ "${1:-}" == "align" ]]; then
  rc=0; align_check || rc=$?; exit $rc
fi
if [[ "${1:-}" == "fail" ]]; then
  rc=0; fail_check || rc=$?; exit $rc
fi
if [[ "${1:-}" == "update" ]]; then
  rc=0; update_suite || rc=$?; exit $rc
fi

banner
host_line
summary
STEP_TOTAL=5
step "Debian-Template"     fake 0.3 "downloading debian-13-standard_13.2-1_amd64.tar.zst" "ok"
detail "debian-13-standard_13.2-1_amd64.tar.zst"
step "Container anlegen"   fake 0.3 "Creating filesystem on local-lvm" "Starting CT 121" "waiting for DNS"
step "Docker installieren" fake 0.25 \
  "Get:1 http://deb.debian.org/debian trixie InRelease" \
  "Selecting previously unselected package containerd.io" \
  "Setting up containerd.io (1.7.28-1) ..." \
  "Setting up docker-ce (28.6.0-1~debian.13~trixie) ..."

step "App ausrollen" fake 0.3 "Building ai-trade-lab" "Successfully tagged ai-trade-lab:latest"
step "Healthcheck"    fake 0.3 "waiting for /api/health" "200 OK"
ct_ip() { echo "192.168.1.74"; }
finish

