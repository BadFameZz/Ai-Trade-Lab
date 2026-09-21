# AI Trade Lab (Aitra) – v0.3.0

Privates Paper-Trading-Labor. Läuft in einem eigenen Debian-LXC auf Proxmox. Fast
komplett lokal: Konfiguration, Datenbank, Dashboard und Ausführung bleiben auf dem
eigenen Server. Eine Ausnahme seit v0.3.0: Für Kursdaten ruft die App die öffentlichen,
lesenden Endpunkte von `api.binance.com` ab. Dabei werden ausschließlich Symbol,
Intervall und Zeitraum übertragen — **keine Kontodaten, keine Kennungen, keine
API-Schlüssel für Binance**; solche existieren im Projekt nicht. Davon unberührt ist
der Admin-Token für das eigene Dashboard, siehe Abschnitt „Sicherheit". Der Quellcode
liegt in einem privaten GitHub-Repo; im Betrieb ruft die App nichts davon ab.

> Kein Trading-Bot mit Gewinngarantie. Aktuell: Dashboard, Persistenz, Risk Engine,
> Kill Switch, Health, Marktdaten (Binance, nur lesend), Paper-Ledger mit Benchmark.
> Eigene Strategien und Backtests folgen (Phase 2+).

## Installation (vom Mac aus)

```bash
scp dist/ai-trade-lab-install.sh root@PVE-IP:/root/
ssh root@PVE-IP
bash /root/ai-trade-lab-install.sh              # Standard: 4 CPU / 8 GB / 32 GB, DHCP
bash /root/ai-trade-lab-install.sh --advanced   # alles einzeln abfragen
```

Der Installer erkennt freie CT-ID, Storage und Bridge automatisch, lädt das aktuelle
Debian-Template, legt einen **unprivilegierten** LXC an, installiert Docker und startet
die App. Am Ende zeigt er die Dashboard-URL.

## Update

```bash
bash ai-trade-lab-install.sh --update <CTID>
```
Backup → neue Version → Healthcheck → bei Fehler automatischer Rollback.
Daten (`data/`) und `.env` bleiben erhalten. Die letzten 5 Backups liegen im CT unter
`/opt/ai-trade-lab-backups/`.

**Handgriff nach dem Sprung von v0.2.x auf v0.3.0:** Ein Update überschreibt die `.env`
nicht (dort steht der Admin-Token). Bestandscontainer laufen danach mit
`STARTING_BALANCE=100` weiter — die App warnt seit v0.3.0 selbst darüber
(`NARROW_TRADING_WINDOW` unter „Protokolle"), aber wer es sofort beheben will:

```bash
pct exec <CTID> -- sed -i 's/^STARTING_BALANCE=.*/STARTING_BALANCE=10000/' /opt/ai-trade-lab/.env
pct exec <CTID> -- docker compose -f /opt/ai-trade-lab/docker-compose.yml up -d
```

## Sicherheit

- Live-Trading ist nicht implementiert und hart gesperrt (egal was in `.env` steht)
- Risk Engine ist deterministisch und von Strategien getrennt: Spot only, max. Position,
  max. Gesamtrisiko, Tagesverlustlimit (aktiviert automatisch den Kill Switch), kein Short
- Kill Switch aktivieren geht immer, **freigeben nur mit Admin-Token**
  (`pct exec <CTID> -- grep ADMIN_TOKEN /opt/ai-trade-lab/.env`)
- **Der Kill Switch löst sich nicht von selbst.** Veralten die Marktdaten (Netzausfall,
  Binance nicht erreichbar, Uhr weit daneben), setzt der Poller ihn — und er bleibt gesetzt,
  auch nachdem die Störung vorbei ist, bis jemand mit dem Admin-Token freigibt. Das ist
  beabsichtigt: eine Sicherung, die sich selbst zurücksetzt, hat schon einmal nicht
  gesichert. Für den Alleinbetrieb heißt das aber: ein Ausfall um 03:00 Uhr legt das
  Papierhandeln still, bis Sie freigeben. Nach dem Freigeben läuft es sofort weiter, sofern
  `GET /api/status` unter `market_data.status` wieder `ok` zeigt; steht dort noch `stale`,
  setzt der nächste Poll-Zyklus den Kill Switch erneut.
- Container: Nicht-root, read-only, keine Capabilities, kein Zugriff auf den Proxmox-Host
- Das Dashboard hat **keinen Login** → nur im Heimnetz betreiben, keinen Port nach außen freigeben
- **Achtung IPv6:** An einem Dual-Stack-Anschluss bekommt der Container zusätzlich zur
  `192.168.x.y` automatisch eine global routbare IPv6-Adresse — und die ist, anders als die
  private IPv4, potenziell aus dem Internet erreichbar. Der Installer zeigt sie am Ende an.
  Prüfen vom Mobilfunk aus, mit ausgeschaltetem WLAN:
  `http://[DEINE-IPV6]:8787` — lädt dort nichts, blockt der Router. Gegenprobe, ob das Handy
  überhaupt IPv6 hat: test-ipv6.com. Ein negativer Test ohne IPv6 beweist nichts.
- Ausgehender Netzverkehr beschränkt sich auf `api.binance.com` (und den Ausweichhost
  `data-api.binance.vision`), nur HTTPS, mit Host-Allowlist und ohne Weiterleitungen

## API

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/status` | Portfolio, Limits, Kill Switch |
| GET | `/api/health` | Health (200 = healthy, 503 = degraded) |
| GET | `/api/decisions` | Decision Journal |
| GET | `/api/events` | Systemprotokoll |
| POST | `/api/kill-switch/engage` | Kill Switch an |
| POST | `/api/kill-switch/release` | Kill Switch aus (Header `X-Admin-Token`) |
| POST | `/api/risk/check` | Vorschlag prüfen + ins Journal schreiben (Header `X-Admin-Token`) |

Beispiel:
```bash
curl -X POST http://CT-IP:8787/api/risk/check \
  -H "X-Admin-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDC","action":"BUY","position_pct":8,"confidence":71,"reason":"Trend bullish"}'
```

## Entwicklung

```bash
cd app && pip install -r requirements.txt pytest && python -m pytest -q
./build.sh      # Tests + Installer neu bauen → dist/ai-trade-lab-install.sh
```

## Nächste Schritte (Roadmap aus der Projektdoku)

Seit v0.3.0 umgesetzt: öffentliche Marktdaten (Binance Spot, nur lesend) inkl.
Veraltet-Erkennung → Kill Switch, Paper-Ledger mit Gebühren/Slippage/
Mindestordergrößen, Benchmark-Portfolio (BTC Buy & Hold).

1. Erste deterministische Strategien + Backtests
