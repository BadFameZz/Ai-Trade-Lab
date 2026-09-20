# AI Trade Lab (Aitra) – v0.2.0 · privat

Privates Paper-Trading-Labor. Läuft in einem eigenen Debian-LXC auf Proxmox,
komplett lokal, ohne GitHub, ohne sylvron.de, ohne Cloud.

> Kein Trading-Bot mit Gewinngarantie. Aktuell: Dashboard, Persistenz, Risk Engine,
> Kill Switch, Health. Marktdaten, Paper-Ledger und Strategien folgen (Phase 2+).

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

## Sicherheit

- Live-Trading ist nicht implementiert und hart gesperrt (egal was in `.env` steht)
- Risk Engine ist deterministisch und von Strategien getrennt: Spot only, max. Position,
  max. Gesamtrisiko, Tagesverlustlimit (aktiviert automatisch den Kill Switch), kein Short
- Kill Switch aktivieren geht immer, **freigeben nur mit Admin-Token**
  (`pct exec <CTID> -- grep ADMIN_TOKEN /opt/ai-trade-lab/.env`)
- Container: Nicht-root, read-only, keine Capabilities, kein Zugriff auf den Proxmox-Host
- Das Dashboard hat **keinen Login** → nur im Heimnetz betreiben, keinen Port nach außen freigeben

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

1. Öffentliche Marktdaten (Binance Spot, nur lesend) inkl. Veraltet-Erkennung → Kill Switch
2. Paper-Ledger mit Gebühren, Slippage, Mindestordergrößen
3. Benchmark-Portfolios (BTC Buy & Hold, einfache Regelstrategie)
4. Erste deterministische Strategien + Backtests
