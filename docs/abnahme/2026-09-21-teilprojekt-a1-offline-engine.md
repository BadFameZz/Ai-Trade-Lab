# Abnahme — Teilprojekt A1: Offline-Engine

**Datum:** 2026-09-21 · **Branch:** `feature/teilprojekt-a1` · **Spec:** `docs/specs/2026-09-20-teilprojekt-a-marktdaten-ledger-benchmark.md`

Gemessen, nicht behauptet. Alle Zahlen stammen aus Läufen im Container
`python:3.12-slim`; wo drei Läufe genannt sind, wurden drei gefahren.

## Umfang

Gebaut: `money.py`, `store.py` (+ Migrationen 2 und 3), `marketdata.py`, `ledger.py`,
`sizing.py`, `execute.py`, `benchmark.py`, `replay.py`. Geändert: `config.py`, `risk.py`,
`db.py`, `build.sh`. **Keine neue Laufzeitabhängigkeit** — `requirements.txt` unverändert
bei `flask==3.1.3` und `gunicorn==26.2.0`.

Nicht in A1: Netzzugriff (`binance.py`), Livebetrieb (`poller.py`), Dashboard-Endpunkte,
`backfill.py`. Das ist Teilprojekt A2.

## Testlage

| Messung | Wert |
|---|---|
| Volle Suite | **157 passed** (25,11 s), vom Orchestrator selbst nachgemessen |
| Ohne `slow` | **153 passed** |
| `build.sh` (Budget 12 s) | 11,0 s |
| Ausgeführte Rot-Nachweise | über 40, je Commit wörtlich protokolliert |

## Tempo (Kriterium A-9)

35.040 Kerzen — ein Jahr bei 15 Minuten — bei realistischer Handelsfrequenz
(3.764 Fills, 1.241 Ablehnungen), je drei Läufe:

| Datenbank | Dauer | Entscheidungen/s | Schwelle |
|---|---|---|---|
| Datei (`tmp_path`) | 6,56 / 6,49 / 6,49 s | **5.341 / 5.400 / 5.398** | ≥ 876 |
| `:memory:` | 2,52 / 2,53 / 2,52 s | 13.925 / 13.866 / 13.909 | ≥ 876 |

Vor dem Sammelschreiben lag die Dateimessung bei 12,86 s. Die Schwelle wurde
**nicht angepasst** — das hat die Nachprüfung eigens verifiziert.

## Buchhaltung (Kriterien A-1, A-2)

| Messung | Ergebnis |
|---|---|
| Fills im Identitätslauf | 9.989 von 10.000 Versuchen |
| `equity − (cash + Σ qty·mark)` | exakt `0`, ohne Toleranz, je Fill |
| `cash_end − (cash_start − Σ net(BUY) + Σ net(SELL))` | exakt `0` — unabhängig aus den Fill-Rückgaben rekonstruiert |
| `cash ≥ 0` und `qty ≥ 0` | in der Schleife geprüft, nicht nur am Ende |

Die zweite Prüfung ist die beweiskräftige: die erste ist tautologisch, weil beide Seiten
aus denselben Objekten stammen. Belegt durch einen Kontrast-Rot-Nachweis — mit
eingebautem Gebührenfehler blieb Prüfung 1 über alle 10.000 Iterationen grün, nur
Prüfung 2 fiel.

## Ergebnis des Zeitraffers

Handrechnung über vier Kerzen, Erwartung aus der Spec hergeleitet und von der
Nachprüfung unabhängig bestätigt:

| Größe | Wert |
|---|---|
| `final_equity` (Strategie) | 11.050 |
| `benchmark_final_equity` (BTC Buy & Hold) | 12.100 |

## Grenzwerte, beidseitig gemessen

| Schwelle | unterhalb | oberhalb |
|---|---|---|
| Veraltet-Warnung, 15m | 1.349 s → `ok` | 1.351 s → `warn` |
| Veraltet-Kill, 15m | 2.699 s → `warn` | 2.701 s → `stale` |
| Uhrversatz | 29 / 30 / −30 s | 31 / −31 s |
| Verfall schwebender Vorschläge | 1.799 s gültig | 1.801 s verfallen |

## Sicherheit und Herkunft

| Prüfung | Ergebnis |
|---|---|
| `gitleaks` über Arbeitsbaum | keine Funde |
| Geld als `float` im Produktivpfad | keiner (nur Prozentsätze) |
| Geld in SQLite als `REAL` | keiner, alles TEXT (E-007) |
| Pfad an `execute_proposal()` vorbei zu `Ledger.apply()` | keiner — Laufzeit-Wächter über `patch.object` und `inspect.stack()`, prüft den unmittelbaren Aufrufer, fail-closed |
| Blick in die Zukunft | keiner im Replay-Pfad; `decide_fn(candles[:t])`, Referenzpreis aus der Entscheidungskerze |

## Was offen bleibt

- **E-010 — Blocker für A2:** `resolve_pending()` *bewertet* weiterhin gegen die Füllkerze.
  In A1 unerreichbar (kein Livepfad), muss vor `poller.py` gelöst sein, sonst ist A-8
  konstruktionsbedingt unerreichbar.
- **`store.py` mit 318 Zeilen** über der projektinternen Marke von 300. Teilung ist
  erste Aufgabe in A2, Naht benannt: Marktdaten gegen Lauf und Ledger.
- **Nicht geprüft:** ein Lauf gegen echte Binance-Daten. A1 ist netzfrei.
