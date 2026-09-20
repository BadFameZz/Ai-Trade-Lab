#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  AI Trade Lab – Einzeiler-Bootstrap für Proxmox
#
#  Holt den fertigen Installer aus dem letzten GitHub-Release, prüft die
#  Prüfsumme und führt ihn aus:
#
#    bash -c "$(curl -fsSL https://raw.githubusercontent.com/BadFameZz/Ai-Trade-Lab/main/proxmox/bootstrap.sh)"
#
#  Argumente durchreichen (das _ wird zu $0):
#    bash -c "$(curl -fsSL …/bootstrap.sh)" _ --advanced
#    bash -c "$(curl -fsSL …/bootstrap.sh)" _ --update 121
#
#  Der Einzeiler setzt ein ÖFFENTLICHES Repo voraus. Bei einem privaten Repo
#  liefert weder raw.githubusercontent.com noch der Release-Download etwas
#  ohne Token; dann GH_TOKEN setzen – der steht damit aber in der
#  Shell-History und in der Prozessliste des Hosts.
# ─────────────────────────────────────────────────────────────────────────────
set -Eeuo pipefail

REPO="${AITRA_REPO:-BadFameZz/Ai-Trade-Lab}"
REF="${AITRA_RELEASE:-latest}"
ASSET="ai-trade-lab-install.sh"

if [[ -t 1 ]]; then B=$'\e[1m'; R=$'\e[31m'; C=$'\e[36m'; N=$'\e[0m'
else B=""; R=""; C=""; N=""; fi
say() { printf '  %s➜%s %s\n' "$C" "$N" "$*"; }
die() { printf '\n  %s%s✖  %s%s\n\n' "$B" "$R" "$*" "$N" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Bitte als root auf dem Proxmox-Host ausführen."
command -v curl >/dev/null      || die "curl fehlt: apt-get install -y curl"
command -v sha256sum >/dev/null || die "sha256sum fehlt (coreutils)."

TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
AUTH=()
[[ -n "$TOKEN" ]] && AUTH=(-H "Authorization: Bearer ${TOKEN}")

if [[ "$REF" == "latest" ]]; then
  BASE="https://github.com/${REPO}/releases/latest/download"
  API="https://api.github.com/repos/${REPO}/releases/latest"
else
  BASE="https://github.com/${REPO}/releases/download/${REF}"
  API="https://api.github.com/repos/${REPO}/releases/tags/${REF}"
fi

# Zwei Wege, weil GitHub sich unterschiedlich verhält:
# öffentlich  -> der schlichte Download-Pfad genügt
# privat      -> /releases/latest/download nimmt keinen Bearer-Token an,
#                dafür braucht es die API: erst Asset-ID, dann octet-stream.
hole() { # hole ASSETNAME ZIELDATEI
  local name="$1" ziel="$2" id
  if [[ -z "$TOKEN" ]]; then
    curl -fsSL -o "$ziel" "${BASE}/${name}"
    return
  fi
  id="$(curl -fsSL "${AUTH[@]}" -H 'Accept: application/vnd.github+json' "$API" |
        ASSET_NAME="$name" perl -0777 -ne 'while (m{"url"\s*:\s*"[^"]*/releases/assets/(\d+)".*?"name"\s*:\s*"([^"]+)"}gs) { if ($2 eq $ENV{ASSET_NAME}) { print $1; last } }')" || true
  [[ -n "$id" ]] || return 1
  curl -fsSL "${AUTH[@]}" -H 'Accept: application/octet-stream' \
       -o "$ziel" "https://api.github.com/repos/${REPO}/releases/assets/${id}"
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

say "Lade ${ASSET} aus ${REPO} (${REF}) …"
if ! hole "$ASSET" "${TMP}/${ASSET}"; then
  [[ -n "$TOKEN" ]] && die "Download fehlgeschlagen – Token abgelaufen oder ohne Leserecht auf ${REPO}?"
  die "Download fehlgeschlagen. Ist ${REPO} privat? Dann GH_TOKEN setzen, oder den Installer per scp übertragen."
fi

# Die Prüfsumme liegt neben der Datei und kommt aus derselben Quelle. Sie
# erkennt einen abgebrochenen Download, ersetzt aber KEINE Signatur: wer das
# Release austauschen kann, tauscht beides.
if hole "${ASSET}.sha256" "${TMP}/${ASSET}.sha256"; then
  ( cd "$TMP" && sha256sum -c "${ASSET}.sha256" >/dev/null ) \
    || die "Prüfsumme stimmt nicht – Download abgebrochen oder Datei verändert."
  say "Prüfsumme in Ordnung."
else
  printf '  %s!%s Keine Prüfsummendatei im Release – ungeprüft weiter.\n' "$R" "$N" >&2
fi

chmod +x "${TMP}/${ASSET}"
say "Starte Installation …"
exec bash "${TMP}/${ASSET}" "$@"
