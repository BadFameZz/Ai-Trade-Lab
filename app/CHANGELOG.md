# Changelog

## 0.3.0 – 2026-09-21
- Marktdaten: öffentliche, nur lesende Binance-Spot-Endpunkte (`api.binance.com`),
  Kerzen für BTCUSDC und BNBUSDC im 15m-Intervall, alle 60 s abgerufen
- Paper-Ledger führt Kasse und Positionen aus echten Fills; `cash` in `/api/status`
  kommt jetzt aus dem Journal, nicht mehr aus einer abgeleiteten Formel (B-3)
- Benchmark BTC Buy & Hold, gerechnet durch dasselbe Ledger wie die Strategie
- Dashboard: Portfolio-Chart gegen Benchmark, offene Positionen, Trefferquote,
  Max Drawdown, schwebende Vorschläge
- `python -m aitra.backfill` für historische Kerzen, `python -m aitra.replay`
  unverändert für den Zeitraffer
- **Sicherheitshinweis:** Der Container baut jetzt ausgehende HTTPS-Verbindungen zu
  `api.binance.com` auf (öffentliche Marktdaten, nur lesend). Es werden ausschließlich
  Symbol, Intervall und Zeitraum übertragen — keine Kontodaten, keine Kennungen, keine
  API-Schlüssel; solche existieren im Projekt nicht
- **Handgriff für bestehende Installationen:** Ein Update überschreibt die `.env` nicht
  (dort steht der Admin-Token). Wer von v0.2.x aktualisiert, läuft mit
  `STARTING_BALANCE=100` weiter — deutlich unter dem jetzt vorgesehenen Handelsfenster.
  Auf dem Zielcontainer einmalig:
  ```bash
  pct exec <CTID> -- sed -i 's/^STARTING_BALANCE=.*/STARTING_BALANCE=10000/' /opt/ai-trade-lab/.env
  pct exec <CTID> -- docker compose -f /opt/ai-trade-lab/docker-compose.yml restart
  ```
  Wird das übersehen, warnt die App seit dieser Version selbst: ein zu enges
  Handelsfenster erzeugt beim Start ein `NARROW_TRADING_WINDOW`-Ereignis (sichtbar unter
  „Protokolle" im Dashboard und über `GET /api/events`)
- Keine neue Laufzeitabhängigkeit — `requirements.txt` unverändert bei `flask==3.1.3`
  und `gunicorn==26.2.0`

## 0.2.1 – 2026-09-20
- Erste Installation auf echter Proxmox-Hardware gelaufen (CT 107, Debian 13, healthy)
- Installer nennt am Ende `pct enter <CTID>`: der Container hat kein Root-Passwort,
  die Konsole im Webinterface fragt sonst nach einem Login, den es nicht gibt
- Installer warnt am Ende, wenn der Container eine global routbare IPv6-Adresse
  bekommen hat — auf einem Dashboard ohne Login die gefährlichste Überraschung
- README: IPv6-Abschnitt samt Prüfbefehl und Gegenprobe
- Keine Änderung an der App selbst

## 0.2.0 – 2026-09-20
- Persistenz: SQLite (WAL) mit versionierten Migrationen unter `data/aitra.db`
- Decision Journal wird dauerhaft gespeichert, inkl. Ergebnis der Risk Engine
- Echte Risk Engine (deterministisch, getrennt von Strategien): Spot only, Max-Position, Gesamtrisiko, Tagesverlust, kein Short, Symbolprüfung
- Kill Switch: Aktivieren ohne Token, Freigeben nur mit Admin-Token; Tagesverlustlimit aktiviert ihn automatisch
- `/api/health`, `/api/version`, `/api/events`, `/api/risk/check` (Trockenlauf, Admin-Token)
- Live-Trading hart gesperrt, egal was in der `.env` steht
- Produktionsserver (gunicorn), Container läuft als Nicht-root, read-only, ohne Capabilities, mit Healthcheck
- Neues Dashboard (Aitra-Design) mit Live-Status, Risk Engine, Health, Journal und Systemprotokoll
- 18 automatische Tests

## 0.1.0 – 2026-09-19
- Grundgerüst: Flask, Dashboard, Status- und Decision-API, Docker
