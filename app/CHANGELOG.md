# Changelog

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
