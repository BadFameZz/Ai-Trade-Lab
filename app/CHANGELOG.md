# Changelog

## 0.3.1 – 2026-09-23

**Zwei Fehler, die stillschweigend falsche Zahlen erzeugten — beide mit Rot-Nachweis:**

- **Die Kerzenabfrage lieferte die ältesten statt der jüngsten Zeilen** (B-D1).
  `store.get_candles()` sortierte `ORDER BY open_time ASC LIMIT ?`, während vier Aufrufer
  `rows[-1]` als „die neueste" lasen — `dashboard.last_prices()` (bewertet die gesamte
  Live-Equity), `POST /api/risk/check` (`ref_price` und damit die **Ordermenge**),
  `GET /api/market/candles` und `GET /api/equity-curve`. Auf der Anlage gemessen: der
  Endpunkt lieferte Kerzen vom 2025-08-19 zu 115.560,01 statt der aktuellen 85.454,11,
  rund **35 % daneben**. Im Test beziffert: ein Buchverlust von **30.105,90 USDC** wurde
  als `pnl 0,00` gemeldet und blieb damit für die Tagesverlustgrenze — und den Kill
  Switch — unsichtbar; die Ordermenge fiel **26 %** zu klein aus.
  Neuer Vertrag: **die jüngsten N, aufsteigend ausgeliefert.** Kein Aufrufer musste
  angefasst werden. Dreizehn Monate unentdeckt, weil der Test genau **eine** Kerze anlegte
  — da sind älteste und neueste dieselbe Zeile.
- **Der Benchmark kaufte nach jedem Neustart erneut aus vollem Startkapital** (B-D5).
  `BuyAndHold` führte „habe ich schon gekauft?" nur im Arbeitsspeicher, entgegen A-14 und
  entgegen dem eigenen Docstring von `build_context()`. Dreimal in der laufenden Anlage
  reproduziert; die gemeldete Benchmark-Equity lag dadurch **1,36 %** zu hoch — genau die
  Zahl, gegen die künftige Strategien gemessen werden. Jetzt wird beim Start das Journal
  gelesen (`ledger.restore()`, `_bought = bool(fills)`). Der Zeitraffer bleibt unberührt,
  weil er seinen Lauf immer mit null Fills anlegt.

**Sicherheit:**

- **Die Portfreigabe ist jetzt an IPv4 gebunden** (`0.0.0.0:8787:8787` statt `8787:8787`).
  Ohne Host-Adresse legt Docker zwei Listener an — `0.0.0.0:8787` **und** `[::]:8787`,
  letzterer umfasst die global routbare IPv6-Adresse des Containers. Dahinter antwortet
  ein Dashboard **ohne Login** mit Kontostand, Positionen und Handelshistorie. Gemessen:
  vorher HTTP 200 über die globale IPv6-Adresse, nachher keine Verbindung; der Zugang über
  IPv4 bleibt unverändert. Ein Test nagelt die Bindung fest, und `build.sh` fährt ihn vor
  jedem Bündel — ein Rückfall blockiert damit die Auslieferung.
  **Wer v0.3.0 installiert hat, sollte aktualisieren:** dieses Release lauscht auf IPv6.

**Suite:** 285 → **308** bestanden, je dreimal gemessen, kein Flattern.

## In 0.3.0 enthalten, hier nachgetragen – Gesamt-Fixrunde Teilprojekt A2

**Drei Blocker (ohne diese Runde ist der Lieferzustand nicht betriebsfähig):**
- Der Poller schaltete sich im Auslieferungszustand nach 60 s selbst ab: der
  zwischengespeicherte `server_time()`-Wert ging gegen die aktuelle Uhr in die
  Veraltet-Erkennung, der gemeldete Uhrversatz wuchs um eine Sekunde pro Sekunde und
  setzte den Kill Switch dauerhaft (B-1)
- Der Livepfad füllte schwebende Vorschläge auf einer Kerze, die **vor** der
  Entscheidung geöffnet hatte — zu einem Preis, den die Strategie beim Entscheiden
  schon kannte (E-006, B-2)
- Kill Switch fiel, und im selben Zyklus wurde trotzdem gebucht (B-3)

**Vor Echtgeld:**
- Tagesverlustgrenze ist wieder eine Tagesgrenze: `sod_equity`/`sod_date` werden am
  UTC-Tageswechsel gesetzt, nach derselben Regel wie im Zeitraffer (V-1, Spec 9.1/E-008)
- Kasse und Positionen des Dashboards kommen aus einem Schnappschuss (V-2)
- Der Kill Switch bewaffnet sich nach manuellem Release neu, solange die Störung
  anhält (V-3)
- Neustart im Netzausfall meldet `stale` statt `ok` (V-4)
- `CANDLE_RETENTION_DAYS` wirkt jetzt tatsächlich (V-7, A-16)
- Ein Equity-Punkt je Kerze statt je Poll (V-6)
- `/api/status` zeigt `market_data.{status, age_s}` (K-2)

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
  API-Schlüssel für Binance; solche existieren im Projekt nicht. Davon unberührt ist
  der Admin-Token für das eigene Dashboard (README, Abschnitt „Sicherheit")
- **Handgriff für bestehende Installationen:** Ein Update überschreibt die `.env` nicht
  (dort steht der Admin-Token). Wer von v0.2.x aktualisiert, läuft mit
  `STARTING_BALANCE=100` weiter — deutlich unter dem jetzt vorgesehenen Handelsfenster.
  Auf dem Zielcontainer einmalig:
  ```bash
  pct exec <CTID> -- sed -i 's/^STARTING_BALANCE=.*/STARTING_BALANCE=10000/' /opt/ai-trade-lab/.env
  pct exec <CTID> -- docker compose -f /opt/ai-trade-lab/docker-compose.yml up -d
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
