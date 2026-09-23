# SPEC — Teilprojekt D: Lernender Agent (D0 · D1 · D2 · D3)

- **Projekt:** AI Trade Lab (Aitra), Worktree `Ai-Trade-Lab-a1`, Branch `feature/teilprojekt-a2`
- **Ausgangsversion im Repo:** 0.3.0 (`app/VERSION:1`)
- **Ausgangsversion auf der Hardware:** **0.2.0** (CT 107 — siehe 2.2, das ist der Anlass für D0)
- **Zielversion:** 0.4.0
- **Fassung:** 1 vom 2026-09-23
- **Grundlage:** `docs/recherche/2026-09-22-grundlagen-lernender-agent.md`
- **Zugehörige Entscheidungen:** E-001 … E-010, insbesondere E-001, E-003, E-004, E-006, E-008
- **Umfang:** **D0 + D1 + D2 + D3.** D4 und D5 sind Nicht-Ziele (1.1).

Jede Zahl in diesem Dokument ist am Code, an einem Dokument oder an einer fremden, als
solcher gekennzeichneten Messung belegt. Wo eine Zahl fehlt, steht **offen** — nicht ein
geschätzter Wert.

---

## 1. Ziel

Aitra kann heute Vorschläge prüfen, buchen und im Zeitraffer durchrechnen — aber niemand
**erzeugt** Vorschläge. `run_replay()` nimmt eine `decide_fn` entgegen
(`app/aitra/replay.py:60`), und die einzigen, die es im Paket gibt, sind zwei Attrappen
(`_wait_fn`, `replay.py:191-193`, und `_takt_fn`, `replay.py:196-209`). Live gibt es
überhaupt keinen Aufrufer: der Poller löst nur schwebende Vorschläge auf
(`poller.py:147-150`), er trifft keine Entscheidung.

Teilprojekt D schließt genau diese Lücke, in vier Schritten:

| Schritt | Ziel |
|---|---|
| **D0** | Der Bestand kommt auf die Hardware. 0.3.0 auf CT 107, die offenen Messungen A-10b/A-19a/A-19b nachgeholt, 400 Tage Kerzen zurückgefüllt. **Ohne D0 hat D2 keine Daten und D3 keinen Betriebsort.** |
| **D1** | `decide_fn` bekommt den Portfolio-Zustand als zweites Argument (Recherche, Entscheidung 2/Option 2 und Entscheidung 5/Option 2). Eine Signaturänderung, isoliert, mit eigenem Rot-Nachweis. |
| **D2** | Eine feste Regelstrategie plus **Parametersuche** über den Zeitraffer (Recherche, Entscheidung 1/Option 1), mit Walk-Forward (Entscheidung 3), Alpha gegen BTC Buy & Hold als Hauptzahl und Max Drawdown als Nebenbedingung (Entscheidung 4), sowie einer periodischen Neuoptimierung auf wachsender Historie („Nachlernen"). |
| **D3** | Der **Live-Runner**: der fehlende Prozess, der dieselbe `decide_fn` periodisch mit frischen Kerzen aufruft und das Ergebnis durch das Nadelöhr schickt (E-003). |

### 1.1 Nicht-Ziele (ausdrücklich, mit Begründung)

| Nicht in D | Warum / wohin |
|---|---|
| **D4 — Mehrsymbol-Zeitraffer** | `run_replay()` ist strukturell einsymbolig, mit hartem `ValueError` dagegen (`replay.py:84-90`). Die Erweiterung fasst den Kern von A1 an und verdient eine eigene Spec. D arbeitet auf **einem** Symbol. |
| **D5 — Echtgeld** | Größte Sicherheitsrelevanz im Projekt (Recherche, Abschnitt 3). Abschnitt 12 sagt, was D1–D3 für D5 offenhält und was nicht. |
| **Echtes Online-Lernen aus realisierten Gewinnen** | **Ehrlichkeitspflicht, hier ausdrücklich:** Die Gittersuche bewertet *hypothetische* Läufe auf Kerzen — sie fragt „was wäre gewesen, wenn dieser Parametersatz gehandelt hätte". Sie bewertet **nicht** die real gebuchten Fills des Agenten. Ein Verfahren, das aus den tatsächlich realisierten Gewinnen lernt, ist Option 2 der Recherche (Policy-Gradient) und ist **nicht beauftragt**. Wer D2 „der Agent lernt aus seinen Trades" nennt, sagt mehr, als gebaut wird: er lernt aus der Kurshistorie, in der seine Trades vorkamen. |
| **Jede neue Laufzeitabhängigkeit** | E-004 bleibt in Kraft. `app/requirements.txt` bleibt bei zwei Zeilen (`flask==3.1.3`, `gunicorn==26.2.0`, gemessen `requirements.txt:1-2`). Kein `numpy`, kein `torch`, kein `stable-baselines3`. Kriterium **D-16**. |
| **Neuronale Netze, RL-Framework, Gym-Environment** | Recherche, Entscheidung 1: nur mit einer gesonderten Entscheidung, die E-004 bewusst aufhebt. Die gibt es nicht. |
| **Eine zweite Strategiefamilie** | D2 baut **genau eine** (gleitende Durchschnitte, 6.1). Jeder weitere Parameterraum vergrößert nur die Menge, gegen die sich die Suche selbst überanpassen kann. YAGNI. |
| **Optimierung der Risikogrenzen** | Die Suche darf `max_position_pct`, `max_daily_loss_pct`, `max_total_exposure_pct`, `fee_bps`, `slippage_bps`, `starting_balance` **nicht** verändern. Ein Agent optimiert gnadenlos gegen das, was er darf (E-003). Kriterium **D-19** misst das. |
| **HTTP-Endpunkt, der eine Suche startet** | Dieselbe Begründung wie für den Replay in A (A-Spec 11.2): ein Endpunkt auf einem Dashboard ohne Login, der Millionen Entscheidungen anstößt, ist ein DoS-Hebel. Die Suche läuft nur per CLI/Timer. |
| **Schemaänderung** | D braucht **keine** neue Migration. Der laufende Parametersatz liegt in der bestehenden `state`-Tabelle (`db.py:182-192`), Ereignisse in `events` (`db.py:174-179`). Aktueller Stand: **4 Migrationen** (`db.py:8-132`), Schema-Version 4. |
| **Änderung an Ledger, Sizing, Risk Engine, Füllmodell** | D fügt eine Schicht **über** dem Nadelöhr hinzu, nicht darin. `ledger.py`, `sizing.py`, `risk.py`, `money.py`, `execute.py` bleiben unberührt. |
| **Automatisierter Browsertest** | L-1 aus der A-Spec bleibt offen und bleibt benannt. |
| **Automatisches Überschreiben der `.env`** | Unverändert: dort steht der Admin-Token (`installer.sh:533-538`). D0 nennt den Handgriff, es erzwingt ihn nicht. |

---

## 2. Gemessene Ausgangslage

### 2.1 Bestandscode

Zeilenzahlen gemessen am 2026-09-23 über `rg -c '^'` je Datei in `app/aitra/`
(dieselbe Zählweise, die `tests/test_modulgroesse.py:24` benutzt, ±1 bei fehlendem
Zeilenumbruch am Dateiende):

| Modul | Zeilen | Was D davon betrifft |
|---|---:|---|
| `replay.py` | **298** | **Nur 2 Zeilen unter der harten 300er-Marke** (`test_modulgroesse.py:9`). D1 ändert hier die `decide_fn`-Signatur. Lösung ohne Teilung: die zwei CLI-Attrappen und `STRATEGIEN` (`replay.py:190-216`, ~27 Zeilen) wandern nach `strategie.py` — siehe 4.3. |
| `execute.py` | 253 | Bleibt unverändert. `execute_proposal()` rechnet `valuation`/`pf` selbst (`execute.py:113-115`); D1 rechnet denselben Zustand eine Anweisung früher noch einmal (5.1). |
| `risk.py` | 99 | `PortfolioState` hat vier Felder (`risk.py:28-32`): `equity`, `start_of_day_equity`, `exposure_pct`, `position_pct_by_symbol`. Genau dieser Typ wird an `decide_fn` gereicht — **kein neuer Typ**. |
| `ledger.py` | 272 | `to_portfolio_state()` (`ledger.py:229-236`) ist die Brücke; `restore()` (`ledger.py:220-227`) ist das Muster für Zustand über Neustarts. |
| `poller.py` / `pollstate.py` | 270 / 124 | Unverändert. `pollstate.py:24` importiert `replay._utc_date` (N-5) — das muss nach 4.3 weiter auflösen. |
| `web.py` | 250 | Unverändert. Verträge in 6.3. `GET /api/market/candles` (`web.py:225-236`) ist **unauthentifiziert** (kein `authorized()`-Aufruf in dieser Route). |
| `dashboard.py` | 226 | `build_live_ledger()` (`dashboard.py:99-112`) rekonstruiert den Live-Ledger aus dem Journal — D3 benutzt genau diese Funktion. `last_prices()` (`dashboard.py:115-124`) ist **defekt**, siehe B-D1. |
| `config.py` | 246 | Neue Felder kommen **hinten mit Vorgabewert** (B-5 der A-Spec), jedes mit eigenem Leser (`_bool`, `_int_range`, `_num`, Muster `config.py:135-167`). |
| `db.py` | 228 | Unverändert. 4 Migrationen (`db.py:8-132`). |
| `store.py` | 134 | `get_candles()` sortiert **`ORDER BY open_time ASC LIMIT ?`** (`store.py:71`) — Wurzel von B-D1. |
| `store_run.py` | 224 | `get_equity_curve()` (`store_run.py:145-157`) ist die Quelle für die Kennzahlen in D2. |
| `marketdata.py` | 181 | `Candle` (`marketdata.py:36-47`), `SqliteSource` (`marketdata.py:90-101`), `SimClock`/`WallClock`. |
| `benchmark.py` | 108 | `BuyAndHold` — der Vergleichsmaßstab für `alpha_pct`. Unverändert. |
| `backfill.py` | 145 | `python -m aitra.backfill --symbol … --interval … --days …` — D0 füllt damit 400 Tage. |
| `binance.py` | 280 | Unverändert. Das Härtungsmuster (Timeouts, kein Redirect, Host-Allowlist) wird in `apiclient.py` wiederverwendet, nicht kopiert-geändert. |

**Testlage heute:** 285 passed, 0 failed
(`docs/abnahme/2026-09-22-teilprojekt-a2-live-und-dashboard.md`, Abschnitt „Testsuite").

**Tempo heute (Kriterium A-9), gemessen in `python:3.12-slim`, je drei Läufe**
(`docs/abnahme/2026-09-21-teilprojekt-a1-offline-engine.md:32-35`):

| Datenbank | Dauer für 35.040 Kerzen | Entscheidungen/s |
|---|---|---|
| Datei | 6,56 / 6,49 / 6,49 s | 5.341 / 5.400 / 5.398 |
| `:memory:` | 2,52 / 2,53 / 2,52 s | **13.925 / 13.866 / 13.909** |

**Wichtig und ehrlich:** Diese Messung stammt aus dem Container **auf dem
Entwicklungsrechner**, nicht aus CT 107 (2 Kerne, F-1). Die A-9-Untergrenze für „den
Container" lautet ≥ 876 Entscheidungen/s. Welche Rate CT 107 wirklich schafft, ist
**offen (F-D-3)** und entscheidet über die Vorgaben der Suche (6.4, D-25).

### 2.2 Die Hardware — der neue Befund vom 2026-09-23

**Vom Auftraggeber gemessen, nicht von mir** (ich habe keinen Zugriff auf 192.168.178.110):

```
$ curl -s http://192.168.178.110:8787/api/status
{"cash":100.0,"daily_loss_pct":0.0,"daily_pnl":0.0,"equity":100.0,"exposure_pct":0,
 "kill_switch":false,"limits":{...},"live_locked":true,"mode":"PAPER","pnl":0.0,
 "positions":[],"risk":"NORMAL","starting_balance":100.0,"trades_total":0,"version":"0.2.0"}
```

Daraus folgt, am Code gegengeprüft:

1. **Auf CT 107 läuft 0.2.0, nicht 0.3.0.** Die gesamte A1/A2-Arbeit ist nie ausgerollt
   worden. Das erklärt, warum A-19a/A-19b und A-10b in der Abnahme als offen stehen.
2. `starting_balance` ist `100.0` — und **als JSON-Zahl**, nicht als kanonischer Text.
   0.3.0 liefert dort `money.to_text(...)`, also die Zeichenkette `"10000.00000000"`
   (`dashboard.py:176`, E-007). Die Ausgabe ist damit ein zweiter, unabhängiger Beleg für
   0.2.0.
3. Die 0.3.0-Felder fehlen vollständig: `market_data.{status,age_s}` (K-2,
   `dashboard.py:77-96`), `benchmark`, `hit_rate_pct`, `max_drawdown_pct`, `alpha_pct`
   (`dashboard.py:182-185`).

**Eine Folge, die im dokumentierten Handgriff fehlt.** README:38-46 und
`app/CHANGELOG.md:41-51` nennen für das Update von 0.2.x auf 0.3.0 **nur**
`STARTING_BALANCE`. Das genügt nicht: Die `.env` eines 0.2.x-Containers kennt **keine**
`MARKET_*`-Schlüssel, der Installer fasst sie beim Update nicht an
(`installer.sh:533`), und die Vorgabe im Code ist `MARKET_DATA_ENABLED=false`
(`config.py:45`, `config.py:209`). **Nach dem Update pollt der Container also nichts** —
keine Kerzen, kein Benchmark, keine Equity-Kurve, und D2/D3 stünden ohne Daten da.
→ Befund **B-D3**, Kriterium **D-3**.

### 2.3 Befunde aus dem Bestand

Vier Befunde, alle beim Lesen für diese Spec entstanden, alle außerhalb des
Schreibauftrags gefunden — deshalb hier **gemeldet**, nicht behoben.

---

**B-D1 · `ORDER BY … ASC LIMIT n` liefert die ÄLTESTEN n Zeilen — an vier Stellen ist
der NEUESTE gemeint.** *(blockierend für D3, latent gefährlich schon in D0)*

`store.get_candles()` baut `… ORDER BY open_time ASC LIMIT ?` (`store.py:71`),
`store_run.get_equity_curve()` baut `… ORDER BY ts_ms ASC LIMIT ?`
(`store_run.py:146-148`). Vier Aufrufer behandeln das Ergebnis als „die neuesten":

| Stelle | Aufruf | Gemeint | Tatsächlich |
|---|---|---|---|
| `dashboard.py:121-123` | `get_candles(..., limit=1)`, dann `rows[-1].close` | „Letzter bekannter Schlusskurs" (Docstring `dashboard.py:116`) | der Schlusskurs der **allerersten je gespeicherten** Kerze |
| `web.py:194,197-198` | dito, dann `ref_price`/`ts_ms` | Referenzpreis der Entscheidungskerze | derselbe uralte Preis — **er geht in `sizing.size_order()` und bestimmt die Ordermenge** |
| `web.py:230` | `get_candles(..., limit ≤ 1000)` | die jüngste Historie | die **ältesten** bis zu 1.000 Kerzen |
| `web.py:242` | `get_equity_curve(..., limit ≤ 5000)` | die jüngste Kurve fürs Dashboard | die **ältesten** Punkte; der Chart friert nach 500 Punkten (≈ 5 Tage bei 15m) ein |

**Warum es niemand gemerkt hat:** `test_api.py:169-176` legt **genau eine** Kerze an
(`assert len(body) == 1`). Bei einer Zeile sind älteste und neueste dieselbe. Auch A-19a
liefe grün: bei zwei bis drei Kerzen ist die Abweichung im Cent-Bereich.

**Warum es zählt:** Sobald der Poller läuft, wächst die Tabelle. Der Bewertungspreis der
Live-Equity bleibt dann für immer der Kurs der Inbetriebnahme. `daily_loss_pct()` rechnet
gegen eine falsche Equity und kann einen **echten** Kill Switch auslösen — dieselbe
Fehlerklasse wie V-2 aus dem A2-Gesamtreview (dort 812,88 USDC Scheinverlust). Und die
Ordermenge aus `size_order()` hängt an `ref_price`: steht dort ein um Faktor *k*
veralteter Preis, ist die gefüllte Notional um Faktor *k* daneben — die Prozentgrenze der
Risk Engine schützt davor nicht, weil sie vor dem Sizing greift.

**Was D3 daran hindert:** Der Agent bekäme in D1 einen `PortfolioState` aus dem
**neuesten** Kurs, die Risk Engine prüfte gegen einen aus dem **ältesten**. Die
Live-Parität (Kriterium D-29) wäre konstruktionsbedingt unerreichbar.
→ Kriterium **D-34**, heute **rot**, mit der Aufgabe, die es einlöst.

---

**B-D2 · `proxmox/installer.sh:567` nennt dem Nutzer „100 USDC virtuell".**
Der Abschlussbildschirm der Installation zeigt eine Zahl, die seit 0.3.0 falsch ist
(`STARTING_BALANCE=10000`, `.env.example:1`, `config.py:195`). Das ist kein Kommentar,
sondern ein Text, der beim Nutzer ankommt. → Kriterium **D-9**.

---

**B-D3 · Der dokumentierte Update-Handgriff ist unvollständig.** Siehe 2.2, Punkt „Eine
Folge, die im dokumentierten Handgriff fehlt". → Kriterium **D-3**, und die
Dokumentation (README, CHANGELOG) ist in D0 nachzuziehen.

---

**B-D4 · `GET /api/market/candles` hat heute keinen einzigen Aufrufer.**
Gemessen: `app/static/index.html` ruft `/api/status`, `/api/decisions`, `/api/events` und
`/api/equity-curve` auf (Zeilen 225, 257, 274, 284, 319) — **nicht** `/api/market/candles`.
Der Endpunkt ist damit heute unauthentifizierte, ungenutzte Angriffsfläche. D3 entscheidet,
ob er der Lastpfad wird (Ansatz 1 in Abschnitt 11) oder weiter ungenutzt bleibt
(Ansatz 2, Empfehlung). Bewertung in 9.2.

---

## 3. Zuschnitt

| Schritt | Inhalt | Kriterien |
|---|---|---|
| **D0** | 0.3.0 auf CT 107 ausliefern, `.env` vollständig nachziehen, A-19a/A-19b/A-10b messen, 400 Tage zurückfüllen | **D-1 … D-10** |
| **D1** | Portfolio-Zustand an `decide_fn`; CLI-Strategien nach `strategie.py` | **D-11 … D-15** |
| **D2** | Regelstrategie, Kennzahlen, Walk-Forward, Zufallssuche, Nachlernen, Umschaltregel | **D-16 … D-25** |
| **D3** | Live-Runner, HTTP-Client, Zustand über Neustarts | **D-26 … D-37** |

**Reihenfolge ist zwingend:** D1 vor D2 (die Suche optimiert die neue Signatur), D2 vor D3
(der Runner braucht einen Parametersatz), D0 vor allem (D2 braucht Kerzen, D3 braucht einen
laufenden 0.3.0-Container).

---

## 4. Module und Dateien

### 4.1 Neu

Alle neuen Module liegen in `app/aitra/`, folgen dem Bestandsstil
(`from __future__ import annotations`, frozen dataclasses, deutschsprachige Docstrings)
und hängen **nur** von der Standardbibliothek und voneinander ab.

| Modul | ~Zeilen | Verantwortung (genau eine) | Hängt ab von | Schritt |
|---|---:|---|---|---|
| `strategie.py` | ~150 | Regelstrategien als `decide_fn`-Fabriken, `Params`, `GITTER`. **Rein:** kein Netz, keine DB, keine Uhr, kein Zufall — prüfbar wie A-8b | `risk`, `marketdata`, `money` | D1/D2 |
| `kennzahlen.py` | ~120 | `return_pct`, `max_drawdown_pct`, `alpha_pct`, `hit_rate_pct`, `roundtrips` aus Equity-Kurve und Fills. Alles `Decimal`, kein `float` im Geldweg (V-6 Teil 2) | `money`, `ledger` | D2 |
| `walkforward.py` | ~90 | Fensterarithmetik: Trainings-/Testfenster, der feste Entwicklungs-Holdout, `ueberlappt_holdout()` | — (stdlib) | D2 |
| `suche.py` | ~190 | Zufallssuche mit Budget, Auswahlregel, Bericht, `__main__`-CLI | `replay`, `strategie`, `kennzahlen`, `walkforward`, `agentparams`, `store` | D2 |
| `agentparams.py` | ~70 | Der laufende Parametersatz: lesen aus `state`, schreiben mit Ereignis. Die Naht zwischen „rechnen" und „festschreiben" | `db`, `strategie`, `money` | D2 |
| `apiclient.py` | ~110 | **Die einzige Stelle mit ausgehendem HTTP zur eigenen API.** Host-Allowlist, Timeouts, kein Redirect, Größenlimit, Token nur im Header | — (stdlib) | D3 |
| `runner.py` | ~170 | Der Live-Zyklus: neue Kerze → `decide_fn` → Vorschlag; Zustand über Neustarts; `__main__`-CLI | `apiclient`, `strategie`, `agentparams`, `dashboard`, `store`, `config` | D3 |

**Summe geschätzt: ~900 neue Zeilen.** Jedes Modul bleibt unter dem 200-Zeilen-Richtwert
(4.4).

Neue Testdateien: `test_strategie.py`, `test_kennzahlen.py`, `test_walkforward.py`,
`test_suche.py`, `test_agentparams.py`, `test_apiclient.py`, `test_runner.py`.

### 4.2 Geändert

| Datei | Änderung | Warum |
|---|---|---|
| `app/aitra/replay.py` | `decide_fn`-Typ und Aufruf (D1); `_wait_fn`/`_takt_fn`/`STRATEGIEN` wandern nach `strategie.py` und werden re-exportiert | Signaturänderung ist D1. Der Umzug hält das Modul unter der harten Marke (4.3) |
| `app/aitra/config.py` | Neue Felder **hinten, mit Vorgabewert** (B-5), je mit eigenem Leser | 6.4 |
| `app/tests/test_modulgroesse.py` | Neuer Trennungswächter `test_replay_und_strategie_sind_wirklich_getrennt` | Vierte Naht, dasselbe Muster wie N-1 (`test_modulgroesse.py:140-179`) |
| `app/tests/test_replay.py` | Aufrufe der geänderten Signatur; der Vertrag „einsymbolig" (`test_replay.py:736`) bleibt | D1 |
| `app/docker-compose.yml` | `HOME: /tmp` für den bestehenden Dienst; zweiter Dienst `ai-trade-lab-agent` (nur bei Ansatz 1/2, Abschnitt 11) | 9.3 |
| `app/.env.example` | Die neuen Schlüssel aus 6.4, alle mit den Vorgaben, die den Lieferzustand **inert** lassen | Lieferzustand handelt nicht von selbst |
| `proxmox/installer.sh` | Zeile 567: das konfigurierte Kapital statt „100 USDC virtuell"; Token-Hinweis um den zweiten Pfad ergänzt (9.1) | B-D2, Sicherheitspunkt 4 |
| `README.md`, `app/CHANGELOG.md`, `app/VERSION` | 0.4.0; der vollständige Update-Handgriff (B-D3); der Agent als eigener Abschnitt | D0 |
| `app/requirements.txt` | **unverändert** | E-004, Kriterium D-16 |

### 4.3 Warum `replay.py` nicht geteilt wird

`replay.py` liegt bei **298** Zeilen, die harte Marke ist **300**
(`test_modulgroesse.py:9`), und die A-Spec hält ausdrücklich fest: „jede weitere Zeile hier
braucht vorherige Rücksprache, nicht stillschweigendes Teilen" (A-Spec 3.1b).

D1 braucht hier rund **+9 Zeilen** (zwei Rechenzeilen, ein Import, sechs Zeilen Docstring,
der das E-006-Verhältnis erklärt). Eine Teilung entlang der Naht Engine/CLI würde den
Aufrufbefehl `python -m aitra.replay` ändern — ein Befehl, der in README, CHANGELOG und
A-Spec 9.2 steht und beim Nutzer ankommt.

**Stattdessen:** Die beiden CLI-Attrappen und das `STRATEGIEN`-Verzeichnis
(`replay.py:190-216`, rund **27** Zeilen) wandern nach `strategie.py` — dorthin, wo
Strategien ab D2 ohnehin wohnen. `replay.py` re-exportiert `STRATEGIEN`, damit
`--strategie wait|takt` (`replay.py:253-255`) und A-8c unverändert funktionieren.

Rechnung: 298 − 27 + 9 = **280 Zeilen**. Zwanzig Zeilen Luft statt zwei, ohne
Befehlsänderung und ohne fünftes Modul.

Zwei Dinge, die dabei nicht kaputtgehen dürfen und deshalb gemessen werden:

- `pollstate.py:24` importiert `replay._utc_date` (N-5). `_utc_date` bleibt in
  `replay.py` — der Import muss weiter auflösen (Teil von D-15).
- Ein Re-Export ist genau die Falle, vor der N-1 warnt
  (`test_modulgroesse.py:140-158`): eine später im Modul definierte Funktion gleichen
  Namens überschattet den Import **lautlos**. Deshalb der neue symmetrische
  Trennungswächter mit Identitätsprüfung (Kriterium **D-14**).

### 4.4 Die 200/300-Zeilen-Regel und ihr Wächter

`tests/test_modulgroesse.py` erzwingt zweierlei:

1. **Kein Modul in `app/aitra/` über 300 Zeilen** (`test_modulgroesse.py:12-27`), mit einer
   Zusicherung über die Prüffläche (`>= 11` Module).
2. **Jedes Modul über 200 Zeilen braucht eine Tabellenzeile in Abschnitt 3.1b der
   A-Spec** — und zwar als **erste Spalte einer Tabellenzeile**
   (`test_modulgroesse.py:67-71`). Der Pfad ist hart verdrahtet auf
   `docs/specs/2026-09-20-teilprojekt-a-marktdaten-ledger-benchmark.md`
   (`test_modulgroesse.py:30-33`).

**Folge für D, als Entwurfsvorgabe:** Jedes neue Modul bleibt **≤ 200 Zeilen**. Dann muss
weder die A-Spec nachträglich ergänzt noch der Wächter angefasst werden. Wer diese Grenze
reißt, teilt das Modul — er weicht nicht den Wächter auf. Kriterium **D-14** misst es.

---

## 5. Datenfluss

### 5.1 D1 — Der Portfolio-Zustand im Zeitraffer, ohne einen Blick auf die Füllkerze

Heute (`replay.py:116-141`) sieht eine Iteration so aus:

```
current = candles[t-1]                       # die ENTSCHEIDUNGSkerze
marks   = {symbol: current.close}
proposal = decide_fn(candles[:t])            # replay.py:128
execute_proposal(proposal, ctx, marks=marks, ts_ms=current.close_time,
                 ref_price=current.close, next_candle=candles[t])
```

`execute_proposal()` rechnet daraus intern `valuation = ledger.mark(marks, ts_ms)` und
`pf = ledger.to_portfolio_state(valuation, sod)` (`execute.py:113-114`).

**Nach D1:**

```
current = candles[t-1]
marks   = {symbol: current.close}
v       = ledger.mark(marks, ts_ms=current.close_time)      # NEU
pf      = ledger.to_portfolio_state(v, sod_equity)          # NEU
proposal = decide_fn(candles[:t], pf)                       # GEÄNDERT
execute_proposal(...)                                       # unverändert
```

**Warum der übergebene Zustand keine Information aus der Füllkerze tragen *kann* —
strukturell, nicht durch Disziplin:**

1. `PortfolioState` hat genau vier Felder (`risk.py:28-32`). Drei davon kommen aus
   `Valuation` (`ledger.py:215-218`), eines (`start_of_day_equity`) aus `sod_equity`.
2. `Valuation` entsteht ausschließlich aus `self._cash`, `self._positions` und **`marks`**
   (`ledger.py:195-218`). `marks` wird hier aus `current.close` gebaut — also aus
   `candles[t-1]`.
3. `candles[t]` wird in dieser Iteration **nur** an `execute_proposal(next_candle=…)`
   gereicht und dort nur an `Ledger.apply()`, das der Kerze ausschließlich `open` und
   `open_time` entnimmt (`ledger.py:126`, `ledger.py:165`).
4. Zwischen der Berechnung von `pf` und dem Aufruf von `execute_proposal()` verändert
   nichts den Ledger. Deshalb ist der `pf`, den `decide_fn` sieht, **feldweise derselbe**,
   den `RiskEngine.check()` gleich darauf prüft. Kriterium **D-12** misst diese Identität,
   Kriterium **D-13** stellt sicher, dass diese Messung nicht stumpf ist.

**Kosten:** ein zusätzlicher `mark()`-Aufruf je Kerze. `mark()` ist O(Positionen) und ohne
Netz/DB. Kriterium **D-15** misst, dass A-9 danach weiter gilt.

### 5.2 D2 — Suche, Walk-Forward, Nachlernen

```
python -m aitra.suche --db data/aitra.db --symbol BTCUSDC [--nachlernen | --baseline]

  walkforward.fenster(daten_von, daten_bis)   ->  [Fenster(train_von, train_bis,
                                                           test_von, test_bis), …]
  für jedes Fenster:
      für jede Ziehung p aus GITTER (deterministisch, fester Seed):
          decide_fn = strategie.baue("sma_crossover", p)
          ergebnis  = replay.run_replay(kerzen[train], decide_fn, …, conn=:memory:)
          score_is  = kennzahlen.aus_lauf(...).alpha_pct
          Budget erschöpft?  -> abbrechen, bisher Bewertetes zählt
      bester_is = argmax(score_is)
      ergebnis_oos = run_replay(kerzen[test], baue(bester_is), …)
      kennzahlen_oos = {alpha_pct, max_drawdown_pct, hit_rate_pct,
                        roundtrips, kill_switch_engagements}

  Aggregation über die Fenster  ->  Kandidat
  Umschaltregel (7.2)           ->  agentparams.schreibe_params()  +  Ereignis
```

**Drei Zeiträume, die nie durcheinandergeraten dürfen:**

| Zeitraum | Definition | Wer ihn anfassen darf |
|---|---|---|
| **Training** | alles vor dem jeweiligen Testfenster, höchstens `SUCHE_TRAIN_MAX_TAGE` zurück (wachsend — genau das, was der Betrieb auch tut) | die Suche, beliebig oft |
| **Betriebs-OOS** | das Testfenster des Walk-Forward, `SUCHE_OOS_TAGE` lang, **nach** dem Trainingsfenster | nur zur Bewertung des bereits gewählten Kandidaten, nie zur Auswahl |
| **Entwicklungs-Holdout** | ein **fester** Kalenderzeitraum am Ende der zurückgefüllten Historie, als Konstante in `walkforward.py` festgeschrieben | **während der gesamten Entwicklung von D: niemand.** Er wird genau einmal angefasst, für die Baseline-Messung in 7.1 |

Der Entwicklungs-Holdout liegt am Ende der **historischen** Daten; der Betrieb erzeugt
Kerzen **danach**. Beide können sich deshalb nie überlappen. Kriterium **D-18** misst es
mit einem Wächter, der jedes von der Suche bewertete Fenster mitschreibt.

**Nachlernen im Betrieb:** derselbe Ablauf, angestoßen alle `SUCHE_INTERVALL_H` Stunden,
über alle inzwischen dazugekommenen Kerzen. Der neue Parametersatz ersetzt den laufenden
**nur** nach der Regel in 7.2, und jede Umschaltung schreibt genau ein Ereignis
`AGENT_PARAMS_SWITCHED` nach `events` — sichtbar unter „Protokolle" und über
`GET /api/events` (`web.py:147-150`).

### 5.3 D3 — Der Live-Runner-Zyklus

Alle `AGENT_POLL_S` Sekunden, für `AGENT_SYMBOL`:

```
1. Kerzen holen
   Ansatz 2 (Empfehlung):  store.get_candles(conn_ro, symbol, interval,
                                             start_ms = jetzt - N*interval_ms,
                                             limit = N)
   Ansatz 1:               GET /api/market/candles?symbol=…&interval=…&limit=N
   -> history: list[Candle],  aufsteigend,  ausschliesslich geschlossene Kerzen

2. Ist die neueste Kerze neu?
   neueste.close_time > letzte_entscheidung_ms   ?   sonst: Zyklus endet hier.

3. Schwebt schon etwas fuer dieses Symbol?
   GET /api/decisions?limit=200 -> eine eigene Zeile mit
   pending_since_ms IS NOT NULL  ->  Zyklus endet hier (kein Stau, E-006)

4. Portfolio-Zustand bauen (dieselben zwei Zeilen wie im Zeitraffer, 5.1)
   ledger = dashboard.build_live_ledger(conn_ro, cfg)     # A-14-Muster
   marks  = {symbol: neueste.close}                       # NEUESTE Kerze (B-D1!)
   pf     = ledger.to_portfolio_state(ledger.mark(marks, neueste.close_time), sod)

5. Entscheiden — dieselbe Funktion wie im Zeitraffer
   proposal = decide_fn(history, pf)
   proposal.action == "WAIT"  ->  nichts senden, Zyklus endet

6. Durch das Nadeloehr (E-003)
   POST /api/risk/check   Header X-Admin-Token
   {symbol, action, position_pct, confidence, strategy_version, reason}

7. Antwort verarbeiten
   200 status="pending_fill"  ->  letzte_entscheidung_ms := neueste.close_time
   200 status="rejected"      ->  Code merken, Zyklus endet (kein Nachlegen)
   200 code="KILL_SWITCH"     ->  Backoff, siehe 8
   401                        ->  siehe 9.1 (Rotation)
   503                        ->  keine Marktdaten; Backoff
```

**Zustand über Neustarts — ohne eigene Datei, analog `Ledger.restore()`:**
Der Runner führt genau eine Zahl mit, `letzte_entscheidung_ms`. Beim Start liest er
`GET /api/decisions?limit=200` (absteigend nach `id`, `db.py:225-227`), nimmt die neueste
Zeile mit **seiner eigenen** `strategy_version` und setzt `letzte_entscheidung_ms` auf
deren `ts`.

Das genügt, weil eine Entscheidung zur Kerze *C* frühestens zu deren `close_time`
geschrieben wird und die nächste Kerze *C′* eine `close_time` genau ein Intervall später
hat — bei 15m also 900 s. Die Sekundenauflösung von `ts` (`db.py:135-136`) ist damit um
drei Größenordnungen feiner als der Abstand, den sie unterscheiden muss. Kriterium
**D-28** misst es.

*Die teurere Alternative* wäre eine neue Spalte `decisions.decision_candle_open_time`
(Migration 5). Sie wäre präziser und für D5 ohnehin nötig (Abschnitt 12) — aber sie kostet
eine Schemaänderung, und D kommt ohne aus. Entscheidung: vorerst ohne, mit dem Vermerk in
Abschnitt 12, dass D5 hier nachrüsten muss.

### 5.4 Die Parität, auf die es ankommt

Der ganze Sinn von D1 ist, dass **dieselbe** `decide_fn` in beiden Welten dasselbe tut.
Beide Aufrufstellen bauen ihre Argumente nach derselben Regel:

| | Zeitraffer (`replay.py`) | Live (`runner.py`) |
|---|---|---|
| `history` | `candles[:t]`, letzte Kerze ist die Entscheidungskerze | die N neuesten **geschlossenen** Kerzen |
| `marks` | `{symbol: candles[t-1].close}` | `{symbol: neueste.close}` |
| `pf` | `ledger.to_portfolio_state(ledger.mark(marks, …), sod)` | dieselbe Zeile, auf dem aus dem Journal rekonstruierten Ledger |
| Füllkerze | `candles[t]`, nur an `Ledger.apply()` | existiert noch nicht → `pending_fill` (E-006) |

Kriterium **D-29** misst diese Parität so, wie A-8 sie für die Engine misst: dieselben 500
Kerzen, dieselbe `decide_fn`, zwei Wege, **ein SHA-256** über die Vorschlagsfolge.

---

## 6. Schnittstellen

### 6.1 `strategie.py`

```python
@dataclass(frozen=True)
class Params:
    """Ein Parametersatz der Regelstrategie 'sma_crossover'.

    Enthaelt ausschliesslich Strategieparameter. Keine Risikogrenze steht hier
    und darf hier je stehen (E-003, Kriterium D-19)."""
    sma_kurz: int             # Kerzen, 2 … 48
    sma_lang: int             # Kerzen, 12 … 384, muss > sma_kurz sein
    position_pct: float       # 0 < x <= 25; die Risk Engine deckelt zusaetzlich
    mindest_abstand_bps: int  # 0 … 200; eine Kreuzung zaehlt erst ab diesem Abstand


def baue(name: str, p: Params) -> Callable[[Sequence[Candle], PortfolioState], Proposal]:
    """Erzeugt eine decide_fn. Rein: keine Uhr, kein Netz, kein Zufall, keine DB."""


GITTER: dict[str, tuple] = {
    "sma_kurz":            (4, 8, 12, 16, 20, 24),
    "sma_lang":            (48, 72, 96, 144, 192),
    "position_pct":        (2.0, 5.0, 8.0, 10.0),
    "mindest_abstand_bps": (0, 10, 25, 50),
}   # 6 x 5 x 4 x 4 = 480 gueltige Kombinationen

STANDARD: Params            # der Satz, mit dem ein frischer Container laeuft
STRATEGIEN: dict[str, Callable[[Sequence[Candle], PortfolioState], Proposal]]
    # 'wait' und 'takt' aus replay.py, unveraendert in ihrem Verhalten (A-8c)
```

Die Regel selbst (`sma_crossover`): `SMA(sma_kurz)` über `close` der letzten `sma_kurz`
Kerzen, `SMA(sma_lang)` entsprechend. Kreuzt kurz über lang und ist der relative Abstand
≥ `mindest_abstand_bps`, folgt `BUY position_pct`; kreuzt kurz unter lang, folgt
`SELL min(position_pct, gehaltener Anteil)` — die gehaltene Größe kommt aus
`pf.position_pct_by_symbol`, **das ist genau der Nutzen von D1**. Sonst `WAIT`. Reicht die
Historie nicht für `sma_lang` Kerzen, `WAIT`.

### 6.2 Die übrigen neuen Signaturen

```python
# replay.py — GEAENDERT (D1)
def run_replay(
    candles: Sequence[Candle],
    decide_fn: Callable[[Sequence[Candle], PortfolioState], Proposal],   # <- zweites Argument
    cfg: Config, specs: Mapping[str, money.SymbolSpec],
    fee_bps: float, slippage_bps: float, benchmark_symbol: str,
    run_id: str, conn: sqlite3.Connection | None = None,
) -> ReplayResult: ...
# ReplayResult bleibt unveraendert (replay.py:30-45), inklusive
# kill_switch_engagements (replay.py:37) — E-008 verlangt, dass diese Zahl
# in jeder Bewertung mitlaeuft.

# kennzahlen.py
@dataclass(frozen=True)
class Kennzahlen:
    return_pct: Decimal
    benchmark_return_pct: Decimal
    alpha_pct: Decimal              # Prozentpunkte (A-Spec 7.1)
    max_drawdown_pct: Decimal
    hit_rate_pct: Decimal | None    # None bei < 10 Roundtrips (7.3)
    roundtrips: int
    kill_switch_engagements: int    # aus ReplayResult durchgereicht, nie weggelassen

def aus_lauf(conn, run_id: str, ergebnis: ReplayResult,
             startkapital: Decimal) -> Kennzahlen: ...

# walkforward.py
@dataclass(frozen=True)
class Fenster:
    train_von_ms: int; train_bis_ms: int
    test_von_ms: int;  test_bis_ms: int

HOLDOUT_VON_MS: int   # Konstante, in D2 einmalig gesetzt (F-D-1), danach nie wieder
HOLDOUT_BIS_MS: int

def fenster(daten_von_ms: int, daten_bis_ms: int, oos_tage: int,
            train_max_tage: int, anzahl: int) -> list[Fenster]: ...
def ueberlappt_holdout(f: Fenster) -> bool: ...

# suche.py
@dataclass(frozen=True)
class Suchergebnis:
    kandidat: Params
    kennzahlen_oos: Kennzahlen
    bewertete_ziehungen: int
    budget_erschoepft: bool
    fenster: int

def suche(conn, symbol: str, cfg: Config, *, ziehungen: int, budget_s: float,
          seed: int, uhr: Callable[[], float]) -> Suchergebnis: ...
def ist_besser(neu: Kennzahlen, alt: Kennzahlen | None, cfg: Config,
               bewertete_ziehungen: int) -> tuple[bool, str]: ...

# agentparams.py
def lese_params(conn) -> Params: ...            # state['agent_params'], sonst STANDARD
def schreibe_params(conn, neu: Params, alt: Params | None, grund: str) -> None:
    """Schreibt state['agent_params'] und genau EIN Ereignis
    AGENT_PARAMS_SWITCHED mit altem und neuem Satz."""

# apiclient.py
class ApiClient:
    def __init__(self, base_url: str, token: str, timeout_s: float) -> None: ...
    def decisions(self, limit: int = 200) -> list[dict]: ...
    def risk_check(self, proposal: Proposal, strategy_version: str,
                   reason: str) -> dict: ...
    def candles(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        """Nur in Ansatz 1 benutzt (Abschnitt 11)."""

# runner.py
def zyklus(rc: RunnerContext) -> RunnerOutcome: ...   # genau EIN Durchlauf, ohne Uhr
def run_forever(rc: RunnerContext, stop_event: threading.Event) -> None: ...
def main(argv: list[str] | None = None) -> int: ...
```

`zyklus()` bekommt die Uhr über `rc.clock` (Protokoll `marketdata.Clock`) und ruft niemals
selbst `time.time()` — dasselbe Muster wie `poller.poll_once()` (`poller.py:5-9`). Nur
`run_forever()`/`main()` erzeugen eine echte Wartezeit.

### 6.3 Endpunkt-Verträge (Bestand, unverändert — hier festgehalten, weil D3 sich darauf stützt)

**`POST /api/risk/check`** (`web.py:177-221`)

| | |
|---|---|
| Authentifizierung | Header `X-Admin-Token`, verglichen mit `hmac.compare_digest` (`web.py:87-89`). Ohne/falsch → **401** |
| Content-Type | muss `application/json` sein, sonst **415** (`web.py:91-94`) |
| Körper | `symbol`, `action`, `position_pct`, optional `confidence`, `strategy_version` (auf 40 Zeichen gekürzt, `web.py:209`), `reason` (auf 500 gekürzt, `web.py:210`) |
| Unparsbare Werte | **422** (`web.py:191-192`) |
| Keine Marktdaten und `action != "WAIT"` | **503**, `{"ok":false,"error":"Keine Marktdaten für X"}` (`web.py:195-196`) |
| Erfolg | **200**, `{ok, decision_id, approved, code, reason, status, expected_fill_after_ms}` |
| `status` | `"filled" \| "rejected" \| "pending_fill" \| "no_order"` (`execute.py:54`). **Live immer** `pending_fill`, weil `next_candle=None` fest verdrahtet ist (`web.py:210`) — ein Fill kommt frühestens im nächsten Poll-Zyklus (E-006, bis ~16 min) |
| `expected_fill_after_ms` | `rows[-1].close_time + 1` (`web.py:220`) — **heute aus der ältesten Kerze**, siehe B-D1 |

**`GET /api/decisions?limit=N`** (`web.py:142-145`): `N` auf 1…500 gedeckelt,
`ORDER BY id DESC` (`db.py:227`). **Unauthentifiziert.**

**`GET /api/market/candles?symbol&interval&limit`** (`web.py:225-236`): `limit` auf 1…1000
gedeckelt. **Unauthentifiziert** (kein `authorized()` in dieser Route). Liefert je Kerze
`{open_time, close_time, open, high, low, close, volume}`, Geld als Zeichenkette (E-007) —
**ohne** `symbol`, `interval`, `closed`. Ein Aufrufer muss diese drei selbst ergänzen.
Reihenfolge: aufsteigend ab der **ältesten** Zeile (B-D1).

**`GET /api/status`** (`web.py:136-140` → `dashboard.build_status`): liefert `equity`,
`cash`, `positions`, `exposure_pct`, `market_data.{status,age_s}` — aber **nicht**
`position_pct_by_symbol` und **nicht** `start_of_day_equity`. Ein `PortfolioState` lässt
sich daraus **nicht** vollständig rekonstruieren; das ist mit ein Grund für die Empfehlung
von Ansatz 2 (Abschnitt 11).

### 6.4 Neue Konfigurationsschlüssel

Alle Felder kommen **hinten** an `Config` (`config.py:33-56`) mit Vorgabewert (B-5), jedes
mit eigenem Leser und eigener Fehlermeldung (Muster `config.py:135-167`).

| Variable | Vorgabe | Grenzen | Zweck |
|---|---|---|---|
| `AGENT_ENABLED` | `false` | bool | Startet den Live-Runner. Vorgabe **aus**, damit der Lieferzustand nicht von selbst handelt — dieselbe Begründung wie `MARKET_DATA_ENABLED` (`config.py:209`) |
| `AGENT_SYMBOL` | `BTCUSDC` | `SYMBOL_RE`, muss in `MARKET_SYMBOLS` vorkommen | Das **eine** Symbol, auf dem der Agent arbeitet (D4 ist Nicht-Ziel) |
| `AGENT_STRATEGY` | `sma_crossover` | feste Menge aus `strategie.STRATEGIEN` | Strategiefamilie |
| `AGENT_HISTORY_N` | `400` | 50 … 1000 | Wie viele Kerzen `decide_fn` sieht. Untergrenze aus `max(GITTER["sma_lang"]) = 192`, mal 2 für den Anlauf, aufgerundet |
| `AGENT_POLL_S` | `60` | 10 … 300 | Zyklusabstand des Runners; entscheidet **nicht** über die Entscheidungsfrequenz (die hängt an der Kerze) |
| `AGENT_BASE_URL` | `http://127.0.0.1:8787` | Host-Allowlist `{127.0.0.1, localhost, ai-trade-lab}`; alles andere nur mit `https` | Adresse der eigenen API. Analog `_binance_base_url()` (`config.py:87-99`) |
| `AGENT_HTTP_TIMEOUT_S` | `10` | 1 … 60 | Verbindung und Lesen, wie in `binance.py` |
| `AGENT_SWITCH_MIN_ALPHA_PP` | **leer** | leer oder 0 … 100 | Mindestvorsprung in Prozentpunkten für eine Umschaltung. **Leer heißt: es wird nicht umgeschaltet.** Die Zahl kommt aus der Baseline-Messung (7.1), nicht aus einer Schätzung |
| `AGENT_MAX_DD_PCT` | **leer** | leer oder 0 … 100 | Obergrenze des Max Drawdown out-of-sample. Ebenfalls leer bis zur Messung |
| `SUCHE_INTERVALL_H` | `24` | 1 … 720 | Abstand zweier Nachlern-Läufe |
| `SUCHE_ZIEHUNGEN` | `120` | 10 … 5000 | Obergrenze der Zufallsziehungen je Fenster |
| `SUCHE_MIN_ZIEHUNGEN` | `60` | 10 … 5000, ≤ `SUCHE_ZIEHUNGEN` | **Unter dieser Zahl wird nie umgeschaltet.** Herleitung unten |
| `SUCHE_BUDGET_S` | `1800` | 60 … 86400 | Zeitbudget eines Nachlern-Laufs. Darüber bricht die Suche ab und schaltet **nicht** um |
| `SUCHE_OOS_TAGE` | `40` | 7 … 365 | Länge eines Betriebs-OOS-Fensters |
| `SUCHE_TRAIN_MAX_TAGE` | `180` | 30 … 3650 | Deckel des wachsenden Trainingsfensters |
| `SUCHE_FENSTER` | `4` | 1 … 12 | Zahl der Walk-Forward-Fenster |
| `SUCHE_SEED` | `20260923` | 0 … 2³² | Fester Seed der Zufallssuche. Ohne ihn ist kein Lauf wiederholbar (D-17) |

**Herleitung `SUCHE_MIN_ZIEHUNGEN = 60`, nicht geraten:** Bei *n* unabhängigen Ziehungen
aus dem Parameterraum liegt der Beste der Stichprobe mit Wahrscheinlichkeit 1 − 0,95ⁿ im
besten Zwanzigstel des Raums. Für *n* = 20 sind das 1 − 0,95²⁰ = **64,1 %** — zu wenig, um
eine Umschaltung zu rechtfertigen. Für *n* = 60 sind es 1 − 0,95⁶⁰ = **95,4 %**. Deshalb
60.

**Was die Vorgaben kosten, ehrlich gerechnet:** 4 Fenster × 120 Ziehungen × 180 Tage ×
96 Kerzen/Tag = **8.294.400 Entscheidungen** je Nachlern-Lauf. Damit die **Mindest**zahl
von 60 Ziehungen innerhalb von `SUCHE_BUDGET_S = 1.800 s` erreicht wird, muss der
Zielcontainer 4 × 60 × 17.280 / 1.800 = **2.304 Entscheidungen/s** schaffen. Die
A-9-Untergrenze (876/s) **reicht dafür nicht**; der Entwicklungscontainer maß
13.866–13.925/s (`:memory:`). Ob CT 107 mit zwei Kernen darüber liegt, ist **offen
(F-D-3)** und wird in D2 gemessen, **bevor** diese Vorgaben festgeschrieben werden.
Kriterium **D-25** ist genau diese Messung; das Budget (Kriterium **D-22**) ist das
Sicherheitsnetz, falls sie ungünstig ausfällt.

---

## 7. Wann ist ein Agent „besser"? (Recherche, Entscheidung 4)

### 7.1 Die Schwellenzahl wird gemessen, nicht gewählt

Eine konkrete Alpha-Schwelle ist heute nicht seriös festlegbar — dafür existiert keine
Messung im Projekt (Recherche, Entscheidung 4, und „Offene Punkte"). Sie wird deshalb
**nach** der ersten Baseline durch ein festes Verfahren bestimmt, nicht durch ein Urteil:

1. Die volle Gittersuche (alle 480 Kombinationen) läuft auf dem **Trainingsanteil** der
   zurückgefüllten Historie, also auf allem **vor** `HOLDOUT_VON_MS`. Bester Satz nach
   `alpha_pct` in-sample.
2. **K = 30** Parametersätze werden zufällig und unabhängig aus demselben Gitter gezogen
   und **alle** auf dem Entwicklungs-Holdout ausgewertet. Das ist die Streuung, die die
   Suche allein durch Zufall erzeugt.
3. **`AGENT_SWITCH_MIN_ALPHA_PP` := die Standardabweichung σ dieser 30 Alpha-Werte**, auf
   zwei Nachkommastellen. Begründung: Ein Vorsprung, der kleiner ist als die Streuung, die
   dieselbe Suche zufällig produziert, ist kein Beleg, sondern Rauschen.
4. **`AGENT_MAX_DD_PCT` := der Max Drawdown von BTC Buy & Hold über denselben
   Holdout-Zeitraum.** Begründung: Ein Agent darf nicht riskanter sein als schlichtes
   Halten — eine Zahl, die das Projekt misst, statt sie zu erfinden.
5. Beide Zahlen, der Holdout-Zeitraum, σ, K und die Alpha-Verteilung kommen nach
   `docs/abnahme/`. Erst danach werden die beiden Schlüssel in der `.env` gesetzt.

Bis dahin sind beide Schlüssel **leer**, und die Umschaltung ist **inert** — fail-closed.
Kriterium **D-23** misst beide Seiten dieser Inertheit.

### 7.2 Die Umschaltregel

Ein Kandidat ersetzt den laufenden Parametersatz nur, wenn **alle sechs** Bedingungen
gelten, jede auf dem Betriebs-OOS-Fenster gemessen:

| # | Bedingung | Warum |
|---|---|---|
| 1 | `AGENT_SWITCH_MIN_ALPHA_PP` und `AGENT_MAX_DD_PCT` sind gesetzt | fail-closed bis zur Messung aus 7.1 |
| 2 | `alpha_pct_neu ≥ alpha_pct_alt + AGENT_SWITCH_MIN_ALPHA_PP` | Hauptzahl (Entscheidung 4, Option 1) |
| 3 | `max_drawdown_pct_neu ≤ AGENT_MAX_DD_PCT` | Nebenbedingung (Option 2): nicht durch mehr Risiko gewinnen |
| 4 | `roundtrips_neu ≥ 10` | Option 3: ein Satz, der einmal zufällig richtig kauft, ist kein Gewinner (Herleitung 7.3) |
| 5 | `kill_switch_engagements_neu ≤ kill_switch_engagements_alt` | **E-008:** Ein Replay ist optimistischer als Live. Ein Satz, der im Zeitraffer glänzt, aber 40-mal angehalten worden wäre, ist kein guter Satz |
| 6 | `bewertete_ziehungen ≥ SUCHE_MIN_ZIEHUNGEN` | die Stichprobe muss etwas aussagen (6.4) |

Jede Umschaltung schreibt genau ein Ereignis `AGENT_PARAMS_SWITCHED` (severity `INFO`) mit
altem und neuem Satz und den vier Zahlen, die sie getragen haben. Jede **abgelehnte**
Umschaltung schreibt höchstens einmal je Nachlern-Lauf `AGENT_PARAMS_KEPT` mit der
Bedingung, die gefallen ist. Kriterium **D-24**.

### 7.3 Herleitung der Mindestzahl an Roundtrips

`hit_rate_pct` ist ein Anteil. Bei *n* geschlossenen Roundtrips und einem wahren Anteil von
0,5 beträgt der Standardfehler 0,5/√n: bei *n* = 10 sind das **15,8 Prozentpunkte**, bei
*n* = 25 noch 10,0. Unterhalb von 10 Roundtrips ist die Trefferquote als Zahl wertlos —
deshalb liefert `kennzahlen.aus_lauf()` dort `hit_rate_pct = None` statt eines Werts, der
Genauigkeit vortäuscht, und die Umschaltregel verlangt ≥ 10.

---

## 8. Fehlerbehandlung

| Fall | Verhalten |
|---|---|
| Zu wenig Historie für `sma_lang` | `decide_fn` liefert `WAIT`. Nie eine Order auf einer halb gefüllten Kennzahl |
| `run_replay()` wirft (zu wenige Kerzen, `replay.py:81-82`) | Die Suche zählt die Ziehung als ungültig, läuft weiter, protokolliert die Zahl ungültiger Ziehungen im Bericht |
| Suchbudget erschöpft | Abbruch nach der laufenden Ziehung, `budget_erschoepft=True`, **keine** Umschaltung, Ereignis `SUCHE_BUDGET_EXHAUSTED` |
| Weniger als `SUCHE_MIN_ZIEHUNGEN` bewertet | keine Umschaltung, Ereignis `AGENT_PARAMS_KEPT` mit dem Grund |
| Parametersatz in `state` unlesbar/unvollständig | Rückfall auf `strategie.STANDARD`, Ereignis `AGENT_PARAMS_INVALID` (WARN). Nie ein halb gelesener Satz |
| Runner: HTTP-Timeout / Verbindungsfehler | Zyklus endet, exponentieller Backoff 60 → 120 → 240 → 600 s (Deckel), dasselbe Muster wie `poller.BACKOFF_STEPS_S` (`poller.py:39`) |
| Runner: **401** | **Kein blinder Wiederholungsversuch.** Höchstens 3 Versuche in 15 min, dann Ruhezustand und Ereignis `AGENT_AUTH_FAILED` (WARN) je Übergang. Ein Runner, der einen rotierten Token im Minutentakt gegen die eigene API wirft, ist ein Angriff auf sich selbst |
| Runner: **503** „Keine Marktdaten" | Backoff, kein Nachlegen. Der Kill Switch des Pollers ist der zuständige Wächter, nicht der Runner |
| Antwort `code == "KILL_SWITCH"` | Zyklus endet, Backoff auf 600 s. Der Runner löst den Kill Switch **nie** selbst — Freigabe nur durch einen Menschen mit Token (E-005, A-Spec 8.2) |
| Antwort `status == "rejected"` | Code protokollieren, **nicht** mit kleinerer Größe nachlegen. Wer nach einer Ablehnung sofort eine kleinere Order schickt, umgeht die Risk Engine schrittweise |
| Schon ein schwebender Vorschlag für das Symbol | kein neuer Vorschlag (5.3, Schritt 3). E-006 warnt ausdrücklich vor dem Stau |
| Runner-Prozess stirbt | `restart: unless-stopped` im Compose; der Zustand wird beim Start aus dem Journal rekonstruiert (5.3) |
| Antwort kein JSON / falsche Struktur / zu groß | verwerfen, Ausnahmetyp protokollieren, **nie** der Rohtext — wie `binance.py` (A-Spec 8.3) |

---

## 9. Sicherheit

### 9.1 Der Admin-Token

**Wo er liegt.** Zwei Orte, in dieser Reihenfolge (`config._admin_token`,
`config.py:170-184`):

1. Umgebungsvariable `ADMIN_TOKEN`, mindestens 24 Zeichen (`config.py:173-175`). Der
   Installer erzeugt ihn bei der **Erstinstallation** und schreibt ihn in die `.env`
   (`installer.sh:533-538`, danach `chmod 600`).
2. Ist `ADMIN_TOKEN` **leer**, erzeugt die App einmalig einen Token und legt ihn unter
   `data/admin_token` mit `0600` ab (`config.py:176-184`).

**Wie er in den Runner kommt.** Über `config.load().admin_token` — **dieselbe Funktion,
dieselbe Reihenfolge wie die App**. Ausdrücklich **nicht** durch Parsen der `.env` und
**nicht** als Kommandozeilenargument (das stünde in jedem Prozesslisting). Im Container
kommt er über `env_file: .env`, das der Dienst mit der App teilt — es entsteht **kein
zweiter Aufbewahrungsort**. Kriterium **D-31**.

**Warum das der vierte Punkt aus dem v0.2.0-Audit ist.** README:54 und `installer.sh:569`
nennen als Weg zum Token `grep ADMIN_TOKEN /opt/ai-trade-lab/.env`. Ist die Zuweisung leer,
liefert dieser Befehl `ADMIN_TOKEN=` — und wer daraus schließt, es gebe keinen Token, setzt
womöglich einen neuen. Damit **entwertet er den bestehenden**, weil die Umgebungsvariable
Vorrang hat (`config.py:171-175`): der Token in `data/admin_token` gilt ab dann nicht mehr,
ohne dass irgendwo etwas davon steht. Das ist keine Kosmetik. → **D0** ergänzt beide Texte
um den zweiten Pfad.

**Rotation.** Wird `ADMIN_TOKEN` geändert und die App neu gestartet, laufen alle Aufrufe
des Runners in **401**. Verhalten: höchstens 3 Versuche in 15 min, dann Ruhezustand plus
Ereignis (Abschnitt 8). Der Runner **fällt nicht auf einen alten Token zurück** und
speichert keinen. Kriterium **D-32**.

**Wo er nirgends auftaucht.** Nur im Kopf `X-Admin-Token`, nie in einer URL, nie in einem
Log, nie in einem Ereignistext. Kriterium **D-30** misst das über einen vollständigen
Lauf.

### 9.2 Bewertung: `GET /api/market/candles` ist unauthentifiziert

**Befund, belegt:** `web.py:225-236` prüft `authorized()` nicht. Dasselbe gilt für
`/api/status`, `/api/decisions`, `/api/events`, `/api/equity-curve`, `/api/health` — das
ganze Dashboard hat bewusst keinen Login (A-Spec, Nicht-Ziele: „nur im Heimnetz
betreiben").

**Vertraulichkeit: kein Schaden.** Der Endpunkt gibt öffentliche Binance-Kursdaten zurück,
die jeder direkt bei Binance abholen kann. Keine Kontodaten, keine Kennungen, keine
Schlüssel — solche existieren im Projekt nicht (E-005).

**Verfügbarkeit: ein Hebel, und es ist derselbe, den die A-Spec anderswo verweigert hat.**
`limit` ist auf 1.000 gedeckelt (`web.py:229`), jede Anfrage liest bis zu 1.000 Zeilen,
wandelt sie in `Decimal` und wieder in Text. Genau mit dieser Begründung hat A einen
HTTP-Endpunkt fürs Replay abgelehnt (A-Spec 11.2). Der Unterschied ist die Größenordnung:
1.000 Zeilen gegen 35.040 Kerzen mit voller Engine.

**Bewertung für D:** Der Endpunkt hat heute **keinen einzigen Aufrufer** (B-D4). Wird er
in D3 zum Lastpfad (Ansatz 1), gibt es ab dann rund 1.440 Abrufe/Tag, jeder mit
vollständigem Query-String im gunicorn-Zugriffslog (`Dockerfile:23`,
`--access-logfile -`) — Lärm, kein Geheimnisabfluss, da der Token im Kopf steht. **Wird
Ansatz 2 gewählt (Empfehlung, Abschnitt 11), bleibt der Endpunkt ohne Aufrufer, und D
vergrößert die Angriffsfläche an dieser Stelle um exakt null.** Das ist ein Argument für
Ansatz 2, kein Grund, den Endpunkt in D zu ändern: ihn zu authentifizieren wäre eine
Änderung am Dashboard-Vertrag und gehört nicht in dieses Teilprojekt.

### 9.3 Die vier offenen Punkte aus dem v0.2.0-Audit — welche D0/D3 berühren

| Punkt | Am Code belegt | Berührt D? | Vorschlag |
|---|---|---|---|
| **gunicorn loggt vollständige Query-Strings** | `Dockerfile:23`: `--access-logfile -` | **nur bei Ansatz 1.** Dann 1.440 Zeilen/Tag zusätzlich; Logrotation ist gesetzt (`docker-compose.yml:21-25`, 10 MB × 3). Kein Token in der Query — D3 sendet ihn ausschließlich im Kopf | **Keine Änderung in D.** Bei Ansatz 2 entfällt der Punkt ganz |
| **Keine Signatur für das Release-Asset** | `build.sh:15` erzeugt nur `…​.sha256` neben der Datei | **ja, D0.** Genau dieses Asset wird auf CT 107 ausgerollt. Eine Prüfsumme auf demselben Weg wie die Datei belegt Übertragungsfehler, nicht Manipulation | **Keine Signatur in D** (eigene Aufgabe, eigenes Schlüsselmaterial). Stattdessen **D-7**: die Prüfsumme wird **auf dem Mac erzeugt und im Container gegengeprüft** — dieselbe Datei, zwei Wege, ein Vergleich |
| **Fehlendes `HOME=/tmp`** | `docker-compose.yml` setzt nur `TRADING_MODE` (Zeilen 8-9); `Dockerfile:10` legt den Benutzer mit `--no-create-home` an, `HOME` zeigt damit auf ein Verzeichnis, das es nicht gibt, auf einem `read_only: true`-Dateisystem (`docker-compose.yml:14`) | **ja, D0 — und D3 erbt es**, sobald ein zweiter Dienst in derselben Compose-Datei entsteht | `HOME: /tmp` in `environment:` **beider** Dienste. `tmpfs: /tmp` existiert bereits (`docker-compose.yml:15-16`). Kriterium **D-8** |
| **Token-Hinweis zeigt ins Leere** | README:54, `installer.sh:569`, gegen `config.py:170-184` | **ja, D3 unmittelbar** — der Runner braucht den Token (9.1) | Beide Texte um den zweiten Pfad ergänzen (D0); der Runner liest den Token über `config.load()`, nie aus der `.env` (Kriterium **D-31**) |

### 9.4 Was sich nicht ändert

Kill Switch, CSP und Sicherheitskopfzeilen (`web.py:96-107`), Nicht-root, `read_only`,
`cap_drop: ALL`, `no-new-privileges`, die Live-Sperre (`config.live_locked`), die
Binance-Host-Allowlist (`config.py:20`, `config.py:87-99`). D fügt **keine** Ausnahme
hinzu und **keine** neue ausgehende Verbindung ins Internet: der einzige neue Netzverkehr
ist HTTP innerhalb des Docker-Netzes bzw. über die Loopback-Adresse.

---

## 10. Abnahmekriterien

Jedes Kriterium nennt eine Messung, eine Schwelle, den Befehl und den eingebauten Fehler,
der es **rot** machen muss. `pytest`-Befehle laufen aus `app/`, `curl`/`pct`-Befehle vom
Mac gegen CT 107 (192.168.178.110).

### D0 — Die Auslieferung

**D-1 · Auf der Hardware läuft die ausgelieferte Version.**
`curl -s http://192.168.178.110:8787/api/version | jq -r '.version'` → `0.3.0` nach der
D0-Auslieferung, `0.4.0` am Ende von D. **Zwei Messpunkte, Schwelle: exakte
Zeichenkettengleichheit.**
*Rot:* Den Container nicht aktualisieren → `0.2.0` (der heutige Stand, 2.2).

**D-2 · Die Hardware rechnet mit dem entschiedenen Startkapital.**
Drei Messungen, alle drei müssen stimmen:
`pct exec 107 -- grep '^STARTING_BALANCE=' /opt/ai-trade-lab/.env` → `STARTING_BALANCE=10000`;
`curl -s …/api/status | jq -r '.starting_balance'` → **`10000.00000000`** (Zeichenkette,
E-007, `dashboard.py:176`);
`curl -s …/api/events?limit=200 | jq '[.[] | select(.event=="NARROW_TRADING_WINDOW")] | length'` → **0**.
*Rot:* `STARTING_BALANCE=100` lassen → dritte Messung wird 1 (`web.py:40-46`), erste und
zweite weichen ab.
*Anmerkung:* Heute ist dieses Kriterium rot — es misst genau den Handgriff aus
`CHANGELOG.md:41-51`.

**D-3 · Marktdaten laufen, und zwar aus der `.env` heraus (B-D3).**
`pct exec 107 -- grep -c '^MARKET_DATA_ENABLED=true' /opt/ai-trade-lab/.env` → **1**;
danach `curl -s …/api/status | jq -r '.market_data.status'` → **`ok`** und
`… | jq '.market_data.age_s'` → **Zahl ≥ 0 und ≤ 900** (15m, K-5).
*Rot:* Die `MARKET_*`-Zeilen weglassen (der heutige Zustand nach einem Update) → `status`
ist `not_configured`, `age_s` ist `null`.

**D-4 · A-19a, der Rauchlauf mit 1m-Kerzen, auf echter Hardware.**
Unverändert die acht Messungen aus A-Spec A-19a (`MARKET_INTERVAL=1m`, `MARKET_POLL_S=15`,
nach höchstens 150 s: `age_s ≤ 150`; `benchmark.equity` Zahl > 0; `points | length ≥ 2`;
`status == "pending_fill"`; danach `trades_total == 1` und `positions | length == 1`;
`cash < 10000` und `> 8900`; `/api/health` → 200; `grep -c "api/equity-curve"
app/static/index.html ≥ 1`).
*Rot:* `MARKET_DATA_ENABLED=false` → fünf der acht Messungen fallen.
*Ehrlich dazugesagt:* Dieser Rauchlauf bemerkt **B-D1 nicht** — bei zwei bis drei Kerzen
sind älteste und neueste praktisch derselbe Preis. Dafür ist **D-34** zuständig.

**D-5 · A-19b, Produktionskonfiguration mit 15m-Kerzen.**
`MARKET_SYMBOLS=BTCUSDC,BNBUSDC`, `MARKET_INTERVAL=15m`, `MARKET_POLL_S=60`. Nach höchstens
150 s: `market_data.status == "ok"`, `age_s ≤ 900`, `benchmark.equity` Zahl > 0,
`points | length ≥ 1`, HTTP 200. Ein Fill wird hier nicht erwartet.
*Rot:* Die intervallrelative Schwelle (`marketdata.py:168-169`) entfernen → `stale` und
HTTP 503.

**D-6 · A-10b, Speicher im 24-h-Dauerbetrieb.**
`docker stats --no-stream --format '{{.MemUsage}}' ai-trade-lab` nach ≥ 24 h Laufzeit mit
zwei Symbolen: **RSS < 512 MiB** (25 % von 2.048 MiB, F-1).
Zusätzlich dieselbe Messung zu Beginn und nach 24 h: **Zuwachs < 64 MiB** — damit das
Kriterium nicht allein durch einen ohnehin niedrigen Absolutwert grün wird.
*Rot:* Alle geholten Kerzen zusätzlich in einer Modulvariablen sammeln → der Zuwachs wird
über 24 h sichtbar.

**D-7 · Die Auslieferung kommt unverfälscht an.**
`sha256sum dist/ai-trade-lab-install.sh` auf dem Mac,
`pct exec 107 -- sha256sum /tmp/ai-trade-lab-install.sh` und die mitgelieferte
`.sha256`-Datei liefern **dieselbe Zeichenkette**. **Drei Werte, ein Hash.**
*Rot:* Ein Byte im übertragenen Skript ändern → zwei verschiedene Hashes.
*Was dieses Kriterium ausdrücklich NICHT leistet:* Es belegt Übertragungsintegrität, nicht
Herkunft. Eine Signatur bleibt eine eigene, gemeldete Aufgabe (9.3).

**D-8 · `HOME` zeigt auf ein beschreibbares Verzeichnis.**
`pct exec 107 -- docker compose -f /opt/ai-trade-lab/docker-compose.yml exec -T ai-trade-lab printenv HOME`
→ **`/tmp`**, für **jeden** Dienst der Compose-Datei.
*Rot:* Die Zeile weglassen → `/home/aitra`, ein Verzeichnis, das es auf einem
`read_only`-Dateisystem nicht gibt.

**D-9 · Der Installer nennt dem Nutzer das richtige Kapital (B-D2).**
`grep -c '100 USDC virtuell' proxmox/installer.sh` → **0**, und
`grep -c 'STARTING_BALANCE' proxmox/installer.sh` → **≥ 1** (die Zahl kommt aus der
Konfiguration, statt im Skript zu stehen).
*Rot:* `installer.sh:567` unverändert lassen → erste Messung 1.

**D-10 · 400 Tage Historie liegen auf der Hardware.**
Im Container gezählt, für `BTCUSDC`/`15m`: **≥ 38.000 Zeilen** (400 × 96 = 38.400, minus
Lücken und Binance-Wartungsfenster), und
`max(close_time) − min(close_time) ≥ 395 Tage`.
*Rot:* `--days 7` statt `--days 400` → rund 672 Zeilen.

### D1 — Der Portfolio-Zustand

**D-11 · Jeder Aufruf bekommt einen Portfolio-Zustand, und zwar den der
Entscheidungskerze.**
Replay über **8.640 Kerzen** (90 Tage 15m, wie A-7). Für jeden Aufruf von `decide_fn` gilt:
`isinstance(pf, risk.PortfolioState)` und
`pf.equity == ledger.mark({symbol: history[-1].close}, …).equity` — in **8640/8640**
Aufrufen. Zusätzlich unverändert A-7:
`history[-1].open_time == kerze_t.open_time − 900_000`.
`python -m pytest -q tests/test_replay.py::test_d11_portfolio_zustand_je_aufruf`
*Rot:* `marks` aus `candles[t].close` statt `candles[t-1].close` bauen → 0/8640.

**D-12 · Der Agent sieht exakt das, was die Risk Engine prüft.**
Im selben Lauf wird `RiskEngine.check` umschlossen und der übergebene `PortfolioState`
mitgeschrieben. Für alle vier Felder (`equity`, `start_of_day_equity`, `exposure_pct`,
`position_pct_by_symbol`) gilt Gleichheit: **0 Abweichungen bei 8.640 Aufrufen × 4
Feldern**.
*Rot:* In `run_replay()` `sod_equity` durch `cfg.starting_balance` ersetzen → alle
`start_of_day_equity`-Vergleiche weichen ab, sobald der erste Tageswechsel passiert ist.

**D-13 · Die Prüffläche ist nicht stumpf.**
Derselbe Lauf mit einer Kerzenreihe, in der `candles[t-1].close != candles[t].open` gilt
und dauerhaft eine Position gehalten wird. Gemessen wird, in wie vielen Schritten sich der
„richtige" `pf` vom „falschen" (aus der Füllkerze gebauten) **messbar unterscheidet**:
**≥ 8.554 von 8.640 (99 %)**.
*Warum dieses Kriterium:* Ohne es wären D-11 und D-12 auch auf einer Reihe grün, in der
beide Werte zufällig gleich sind — ein Wächter, der nichts unterscheiden kann, bewacht
nichts.
*Rot:* Eine konstante Kerzenreihe (`close == open` überall) verwenden → 0 von 8.640; das
Kriterium fällt und meldet damit seine eigene Blindheit.

**D-14 · Die Naht `replay.py` / `strategie.py` und die Modulgrößen.**
`python -m pytest -q tests/test_modulgroesse.py` → **0 failed**, darunter der neue
symmetrische Wächter mit Identitätsprüfung der re-exportierten Namen (`STRATEGIEN`,
`_wait_fn`, `_takt_fn`).
Zusätzlich gemessen über `rg -c '^' app/aitra/*.py`: **kein neues Modul über 200 Zeilen**,
`replay.py` **≤ 285** (heute 298, Rechnung in 4.3), **kein** Modul über 300.
*Rot 1:* Eine byte-gleiche Zweitfassung von `_takt_fn` in `replay.py` definieren → der
Identitätsteil des Wächters fällt (genau der Fund N-1, `test_modulgroesse.py:140-158`).
*Rot 2:* Ein neues Modul auf 210 Zeilen aufblähen → die Größenmessung fällt.

**D-15 · Der Bestand bleibt grün, und A-9 bleibt es auch.**
`python -m pytest -q` → **0 failed**, insbesondere A-7, A-8, A-8b, A-8c, A-12b und der
Import `pollstate → replay._utc_date` (N-5).
A-9 neu gemessen nach D1, drei Läufe, 35.040 Kerzen, `:memory:`: **≥ 2.920
Entscheidungen/s** auf dem Entwicklungsrechner (A-9-Schwelle; vor D1 gemessen waren
13.866–13.925/s — der dritte `mark()`-Aufruf je Kerze darf davon höchstens 79 % kosten).
*Rot:* `time.sleep(0.002)` je Kerze → rund 500/s.

### D2 — Baseline, Suche, Nachlernen

**D-16 · Keine neue Abhängigkeit.**
`sha256sum app/requirements.txt` ist identisch zu 0.3.0, und
`docker compose exec ai-trade-lab pip list --format=freeze | wc -l` wächst um **0**.
*Rot:* `numpy` eintragen → Hash weicht ab.

**D-17 · Zwei Läufe der Suche, ein Ergebnis.**
Dieselbe Suche zweimal im selben Prozess und zweimal in getrennten Prozessen mit
unterschiedlichem `PYTHONHASHSEED`: **vier identische SHA-256** über die serialisierte
Liste `(Params, alpha_pct, max_drawdown_pct)` aller bewerteten Ziehungen, in derselben
Reihenfolge.
*Rot:* Über ein `set` von `Params` iterieren statt über eine sortierte Liste → vier
verschiedene Hashes.

**D-18 · Der Entwicklungs-Holdout wird nie zur Parameterwahl angefasst.**
Ein Wächter schreibt jedes von der Suche ausgewertete Fenster `(von_ms, bis_ms)` mit. Nach
einem vollen Walk-Forward-Lauf gilt: **0 von ≥ 12 ausgewerteten Fenstern** überlappen
`[HOLDOUT_VON_MS, HOLDOUT_BIS_MS]`. Die Zahl der ausgewerteten Fenster wird mitgeprüft
(**≥ 12**, also 4 Fenster × Training und Test und mindestens ein Kandidatenlauf), damit das
Kriterium nicht auf einer leeren Liste grün wird.
*Rot:* `SUCHE_FENSTER` so setzen, dass das letzte Testfenster in den Holdout reicht → ≥ 1
Überlappung.

**D-19 · Die Suche fasst keine Risikogrenze an.**
`set(GITTER) ∩ {"max_position_pct","max_daily_loss_pct","max_total_exposure_pct",
"fee_bps","slippage_bps","starting_balance"}` → **leer**, und über einen vollen Suchlauf
gilt: die `Config`, mit der jeder `run_replay()`-Aufruf läuft, ist in diesen sechs Feldern
**feldweise identisch** zur Ausgangs-`Config` — **0 Abweichungen bei ≥ 240 Läufen**.
*Rot:* `"max_position_pct": (10, 15, 20)` ins Gitter aufnehmen → beide Messungen fallen.

**D-20 · `kill_switch_engagements` steht in jeder Bewertung (E-008).**
Jede Zeile des Suchberichts trägt die Zahl. Über einen konstruierten Lauf über 5 Tage
(480 Kerzen), dessen Equity an Tag 1 um 3 % fällt (Limit 2 %), gilt: **≥ 1** Auslösung
wird ausgewiesen, und ein Kandidat mit mehr Auslösungen als der laufende wird nach Regel 5
(7.2) **abgelehnt**. **Zwei Messpunkte**, davon einer mit `engagements > 0` — sonst zeigte
das Feld nur „immer 0" an und wäre kein Wächter.
*Rot:* Das Feld aus dem Bericht entfernen → erste Messung fällt; Regel 5 weglassen → der
zweite Kandidat wird angenommen.

**D-21 · Die Kennzahlen rechnen exakt, ohne `float` im Geldweg.**
Für eine fest hinterlegte Equity-Kurve aus 5 Punkten, deren Werte in `float` nachweislich
abweichen, gilt: `max_drawdown_pct == Decimal("…")` **exakt**, ohne Toleranz, und
`alpha_pct == return_pct − benchmark_return_pct` exakt.
*Rot:* In `kennzahlen.py` einmal über `float(...)` rechnen → die exakte Gleichheit fällt
(dieselbe Begründung wie V-6 Teil 2 der A-Spec).

**D-22 · Das Budget greift und schaltet nicht um.**
Suche mit `SUCHE_BUDGET_S=5` und einer künstlich langsamen `decide_fn`: der Lauf endet nach
**≤ 7 s**, meldet `budget_erschoepft=True`, `bewertete_ziehungen < SUCHE_MIN_ZIEHUNGEN`,
schreibt **genau 1** Ereignis `SUCHE_BUDGET_EXHAUSTED` und lässt `state['agent_params']`
**unverändert** (byte-gleich).
*Rot:* Die Budgetprüfung entfernen → der Lauf braucht ein Vielfaches und schaltet um.

**D-23 · Ohne gemessene Schwelle wird nicht umgeschaltet (fail-closed).**
Mit leerem `AGENT_SWITCH_MIN_ALPHA_PP`: auch ein offensichtlich besserer Kandidat
(`alpha_pct` doppelt so hoch) führt zu **0** Umschaltungen und **genau 1** Ereignis
`AGENT_PARAMS_KEPT` mit dem Grund `SWITCH_NOT_CONFIGURED`. Mit gesetzter Schwelle und
demselben Kandidaten: **genau 1** Umschaltung. **Zwei Messpunkte.**
*Rot:* Der leeren Zuweisung einen Vorgabewert `0` geben → der erste Messpunkt schaltet um.

**D-24 · Die Umschaltung ist sichtbar und einmalig.**
Bei einer Umschaltung: `GET /api/events` enthält **genau 1** `AGENT_PARAMS_SWITCHED`, der
Text nennt alten und neuen Satz sowie `alpha_pct`, `max_drawdown_pct`, `roundtrips`,
`kill_switch_engagements`; `state['agent_params']` trägt danach den neuen Satz. Bei einem
schlechteren Kandidaten: **0** solche Ereignisse und ein unveränderter Satz.
**Zwei Messpunkte.**
*Rot:* Bedingung 2 aus 7.2 entfernen → auch der schlechtere Kandidat erzeugt ein Ereignis.

**D-25 · Das Suchtempo auf CT 107 — gemessen, Schwelle aus der Rechnung in 6.4.**
Ein Nachlern-Lauf mit den Vorgaben aus 6.4, drei Läufe, gemessen als
`Summe der decide_fn-Aufrufe / Laufzeit`: **≥ 2.304 Entscheidungen/s**, damit 60 Ziehungen
in 1.800 s fertig werden.
**Dieses Kriterium ist heute nicht erfüllbar prüfbar** — die Rate von CT 107 ist ungemessen
(F-D-3), und die A-9-Untergrenze (876/s) läge darunter.
*Die Aufgabe, die es einlöst:* „Suchtempo auf CT 107 messen und
`SUCHE_TRAIN_MAX_TAGE`/`SUCHE_ZIEHUNGEN`/`SUCHE_FENSTER` daraus setzen" — **erste Aufgabe
von D2**, vor dem Festschreiben der Vorgaben. Fällt die Messung unter 2.304/s, werden die
**Vorgaben** angepasst, nicht die Schwelle: die Schwelle ist aus `SUCHE_MIN_ZIEHUNGEN` und
`SUCHE_BUDGET_S` abgeleitet und wandert mit diesen mit.
*Rot:* `SUCHE_TRAIN_MAX_TAGE=360` setzen → die geforderte Rate verdoppelt sich auf
4.608/s.

### D3 — Der Live-Runner

**D-26 · Ein Vorschlag je Kerze, nicht je Zyklus.**
Simulierte 24 h mit `SimClock`, `MARKET_INTERVAL=15m`, `AGENT_POLL_S=60`, einer
`decide_fn`, die immer `BUY` liefert, und einem HTTP-Doppel: **genau 96**
`POST /api/risk/check` (24 × 4), nicht 1.440.
*Rot:* Die Prüfung `neueste.close_time > letzte_entscheidung_ms` entfernen → 1.440.

**D-27 · Kein zweiter Vorschlag, solange einer schwebt (E-006).**
Mit einer eigenen Zeile mit `pending_since_ms IS NOT NULL` im Journal: über **30**
simulierte Zyklen **0** weitere POSTs für dieses Symbol. Nach dem Auflösen der schwebenden
Zeile und einer neuen Kerze: **genau 1**. **Zwei Messpunkte.**
*Rot:* Schritt 3 aus 5.3 weglassen → 30 POSTs — genau der Stau, vor dem E-006 warnt.

**D-28 · Ein Neustart verliert nichts und verdoppelt nichts.**
Der Runner entscheidet auf Kerze C, wird beendet, neu gestartet und läuft weiter, ohne dass
eine neue Kerze eingetroffen ist: im Journal steht für C **genau 1** Zeile mit seiner
`strategy_version`, nicht 2. Nach dem Eintreffen von C′: **genau 2** Zeilen.
**Zwei Messpunkte.**
*Rot:* Die Rekonstruktion aus `GET /api/decisions` weglassen (`letzte_entscheidung_ms = 0`
beim Start) → 2 Zeilen für C.

**D-29 · Dieselbe `decide_fn`, zwei Welten, ein Hash.**
Dieselben 500 Kerzen, derselbe Parametersatz, derselbe Startzustand: einmal über
`run_replay()`, einmal über 500 Runner-Zyklen gegen ein HTTP-Doppel, das dieselben Kerzen
ausliefert. Verglichen wird ein SHA-256 über die Folge `(open_time, action, position_pct)`
aller Vorschläge: **zwei identische Hashes**.
*Rot 1:* Im Runner `history` um eine Kerze verschieben (`[:-1]` statt voll) → zwei
verschiedene Hashes.
*Rot 2:* `pf` im Runner aus `cfg.starting_balance` statt aus dem rekonstruierten Ledger
bauen → zwei verschiedene Hashes, sobald die erste Position steht.

**D-30 · Der Token taucht nirgends auf.**
Über einen vollständigen simulierten Lauf (≥ 96 Zyklen) gilt: `grep -c "$ADMIN_TOKEN"`
über die gesamte Standardausgabe, die Standardfehlerausgabe und alle geschriebenen
`events`-Zeilen → **0**; die Zahl der Anfragen **mit** dem Kopf `X-Admin-Token` ist gleich
der Zahl der POSTs (**96 von 96**); die Zahl der URLs, die den Token enthalten → **0**.
*Rot:* Den Token in eine Debug-Zeile schreiben → erste Messung ≥ 1.

**D-31 · Der Token kommt aus `config.load()`, nicht aus der `.env`.**
Mit **leerer** `ADMIN_TOKEN=`-Zuweisung und einem von der App erzeugten
`data/admin_token` (`config.py:176-184`) authentifiziert sich der Runner erfolgreich:
**1 von 1** POST mit HTTP 200 statt 401.
*Rot:* Den Token per `grep ADMIN_TOKEN .env` lesen → leer → 401. Genau das ist der vierte
Punkt aus dem v0.2.0-Audit (9.1).

**D-32 · Ein rotierter Token stoppt den Runner, statt ihn hämmern zu lassen.**
Nach einer Token-Rotation: **höchstens 3** POSTs in 15 simulierten Minuten, danach **0**
weitere; **genau 1** Ereignis `AGENT_AUTH_FAILED`.
*Rot:* Den Zähler weglassen → 15 POSTs in 15 min (bei `AGENT_POLL_S=60`).

**D-33 · Der Kill Switch hält den Runner an.**
Mit aktivem Kill Switch (`state.kill_switch = "1"`) über 10 simulierte Zyklen: **0** Fills,
höchstens **1** POST (der, der die Ablehnung einsammelt), danach Backoff; `state.kill_switch`
ist nach dem Lauf unverändert `"1"` — der Runner setzt ihn **nie** zurück.
*Rot:* Den Backoff nach `code == "KILL_SWITCH"` weglassen → 10 POSTs.

**D-34 · Der Bewertungskurs ist der neueste — heute ROT, und das ist der Punkt.**
Mit **drei** gespeicherten Kerzen (close 100, 200, 300) liefert `dashboard.last_prices()`
**300**, nicht 100; `GET /api/market/candles?limit=2` liefert die Kerzen mit close 200 und
300, nicht 100 und 200; `GET /api/equity-curve?limit=2` liefert die **jüngsten** zwei
Punkte. **Drei Messpunkte.**
**Dieses Kriterium ist heute rot.** Es benennt echte Schuld im Bestand: `store.py:71` und
`store_run.py:146-148` sortieren aufsteigend und deckeln mit `LIMIT`, vier Aufrufer
(`dashboard.py:121`, `web.py:194`, `web.py:230`, `web.py:242`) behandeln das Ergebnis als
„die neuesten" (B-D1).
*Die Aufgabe, die es einlöst:* „`get_candles`/`get_equity_curve` um eine Sortierrichtung
erweitern und die vier Aufrufstellen darauf umstellen" — **Voraussetzung für D3**, denn
ohne sie ist D-29 konstruktionsbedingt unerreichbar (5.4).
*Rot (nach der Behebung):* `ASC` wiederherstellen → alle drei Messungen fallen.

**D-35 · Der Speicher des zweiten Prozesses.**
`docker stats --no-stream` über beide Dienste nach ≥ 24 h: Runner-RSS **< 256 MiB**
(12,5 % von 2.048 MiB), Summe beider Dienste **< 768 MiB** (37,5 %).
*Herleitung:* A-10b gibt dem Worker 512 MiB (25 %); 256 MiB für den Runner lassen dem
Container über 60 % Luft — bei 177,30 MiB Grundlast (F-1).
*Rot:* Die volle Historie je Zyklus in einer Modulvariablen sammeln → Wachstum über 24 h
sichtbar.

**D-36 · Der Weg zum Nutzer: End-to-End auf CT 107.**
`AGENT_ENABLED=true`, `MARKET_INTERVAL=1m`, `AGENT_POLL_S=15`, ein Parametersatz, der
innerhalb weniger Kerzen handelt. Nach höchstens **600 s**:

| Messung | Befehl | Schwelle |
|---|---|---|
| Der Agent hat entschieden | `curl -s …/api/decisions?limit=100 \| jq '[.[] \| select(.strategy_version \| startswith("agent-"))] \| length'` | **≥ 1** |
| … und es wurde gebucht | `curl -s …/api/status \| jq '.trades_total'` | **≥ 1** |
| … und die Position steht | `curl -s …/api/status \| jq '.positions \| length'` | **≥ 1** |
| Der Runner lebt | `pct exec 107 -- docker compose … ps --format json \| jq -r '.[] \| select(.Service=="ai-trade-lab-agent") \| .State'` | `running` |
| Nichts ist stillschweigend abgestürzt | `curl -s …/api/events?limit=200 \| jq '[.[] \| select(.event=="POLL_CYCLE_EXCEPTION" or .event=="AGENT_CYCLE_EXCEPTION")] \| length'` | **0** |
| Health bleibt grün | `curl -s -o /dev/null -w '%{http_code}' …/api/health` | `200` |

**Sechs Messungen.** *Warum ein eigenes Kriterium:* Eine geprüfte `decide_fn` ohne Aufrufer
erfüllt nichts. D-29 misst die Funktion, D-36 misst den Weg.
*Rot:* `AGENT_ENABLED=false` → die ersten drei Messungen 0, die vierte `exited`.

**D-37 · Die Testsuite als Ganzes.**
`cd app && python -m pytest -q` → **0 failures, 0 errors**, Anzahl Tests **≥ 328**.
*Herleitung der Zahl:* 285 heute (A2-Abnahme) + mindestens ein Test je pytest-gestütztem
Kriterium (D-11 … D-34 ohne die drei Hardware-Kriterien darin: 22) + mindestens drei
Einheitentests je neuem Modul (7 × 3 = 21) = 328.
`./build.sh` läuft bis zum Ende durch und erzeugt `dist/ai-trade-lab-install.sh` mit
passender `.sha256`; das Budget des `-m "not slow"`-Laufs bleibt **≤ 20 s** (heute 11,0 s
bei 153 Tests, `docs/abnahme/2026-09-21-teilprojekt-a1-offline-engine.md:24`).
*Rot:* Einen der obigen Tests scheitern lassen → `build.sh` bricht wegen `set -e`
(`build.sh:3`) ab.

---

## 11. Drei Ansätze für den Live-Runner — und eine Empfehlung

Die offene Architekturfrage in D ist nicht die Lernlogik (durch Entscheidung 1 gesetzt),
sondern **woher der Runner seine Kerzen und seinen Portfolio-Zustand nimmt.** Der
schreibende Weg ist in allen drei Ansätzen derselbe: `POST /api/risk/check` mit
Admin-Token, weil E-003 verlangt, dass jede Buchung durch dasselbe Nadelöhr geht.

### Ansatz 1 — Eigener Prozess, alles über HTTP

Kerzen über `GET /api/market/candles`, Portfolio-Zustand über `GET /api/status`.

| Dafür | Dagegen |
|---|---|
| Keine DB-Kopplung; der Runner könnte auf einem anderen Rechner laufen | **`GET /api/market/candles` liefert heute die ÄLTESTEN Kerzen** (B-D1) — ohne Behebung unbrauchbar |
| Genau die Schnittstelle, die auch Teilprojekt B benutzen würde | **`GET /api/status` liefert `position_pct_by_symbol` und `start_of_day_equity` nicht** (6.3). Ein vollständiger `PortfolioState` lässt sich daraus nicht bauen — D-29 wäre unerreichbar, ohne einen neuen Endpunkt zu schaffen |
| Der Runner braucht keinen Zugriff auf das Datenverzeichnis | Macht einen unauthentifizierten Endpunkt zum Lastpfad (9.2) und füllt das gunicorn-Zugriffslog (9.3) |

### Ansatz 2 — Eigener Prozess, Kerzen und Ledger lokal, Buchung über HTTP *(Empfehlung)*

Der Runner öffnet `data/aitra.db` lesend, holt die Kerzen über
`store.get_candles(start_ms=…)` und rekonstruiert den Ledger über
`dashboard.build_live_ledger()` — dieselbe Funktion, die `/api/status` benutzt
(`dashboard.py:99-112`). Nur der Vorschlag geht über HTTP.

| Dafür | Dagegen |
|---|---|
| **Der `PortfolioState` entsteht aus denselben zwei Zeilen wie im Zeitraffer** (5.4) — das macht D-29 überhaupt erreichbar | Zweiter SQLite-Leser auf derselben Datei; `database is locked` wird eine Betriebsart (WAL trägt es, `timeout=10` ist gesetzt, `db.py:155`) |
| `start_ms` umgeht B-D1 im Lesepfad: `get_candles(start_ms = jetzt − N·Intervall)` liefert die **jüngsten** N (`store.py:65-72`) | Der Runner braucht das Datenverzeichnis, läuft also auf demselben Host |
| Die Angriffsfläche wächst um **null**: kein neuer Endpunkt, kein neuer unauthentifizierter Aufrufer (9.2) | `db.connect()` setzt `PRAGMA journal_mode=WAL` (`db.py:157`) und braucht dafür Schreibrecht — der Runner mountet das Volume **rw** und benutzt eine eigene, ausschließlich lesende Verbindung |
| Ereignisse (`AGENT_*`) landen direkt in `events` und damit unter „Protokolle" — ohne neuen Schreibendpunkt | |
| Der Parametersatz kommt aus `state` (`agentparams.py`), wo die Suche ihn hinterlegt — kein Umweg | |

### Ansatz 3 — Zweiter Thread im gunicorn-Worker, `execute_proposal()` direkt

| Dafür | Dagegen |
|---|---|
| Kein Token, kein HTTP, keine zweite Verbindung — die **vollständigste** E-001-Parität, die es geben kann | Der Agent lebt im Webprozess: sein Speicher zählt gegen A-10b (512 MiB), und ein Fehler in ihm trifft das Dashboard |
| Rund 40 Zeilen weniger Code | Verschärft die ungeschriebene `--workers 1`-Kopplung (A-Spec 13, Ansatz 1) — jetzt mit **zwei** Hintergrundaufgaben |
| | Umgeht `POST /api/risk/check` und damit den Weg, den B später ebenfalls nehmen wird — zwei Aufrufmuster statt einem |
| | Für D5 der falsche Ort: Geld bewegender Code gehört nicht in denselben Prozess wie die Weboberfläche |

### Empfehlung: **Ansatz 2**

Drei Gründe, in dieser Reihenfolge:

1. **Ansatz 1 ist heute nicht baubar, ohne zwei fremde Baustellen aufzumachen.** Er
   bräuchte die Behebung von B-D1 **und** einen neuen Endpunkt, der einen vollständigen
   `PortfolioState` liefert. Der zweite wäre eine echte Erweiterung der öffentlichen,
   unauthentifizierten Oberfläche — genau das, was D nicht tun soll.
2. **Ansatz 2 macht die Live-Parität erreichbar.** Der `PortfolioState` entsteht aus
   denselben zwei Zeilen wie im Zeitraffer (5.4). Ohne das ist D-29 nicht messbar, und ohne
   D-29 ist „derselbe Agent live und im Zeitraffer" eine Behauptung.
3. **Ansatz 3 ist die bessere Parität, aber der schlechtere Ort.** Er spart Code, bezahlt
   ihn aber mit dem Speicherbudget des Webprozesses und mit einem zweiten Aufrufmuster
   neben dem, das B später ohnehin braucht. Und er baut die Schicht, die D5 als eigenen,
   isolierten Prozess dringend brauchen wird, an genau der falschen Stelle auf.

**Kosten bei Irrtum:** Erweist sich der zweite SQLite-Leser als Problem (`database is
locked` im Alltag statt als Ausnahme), ist die Rücknahme **billig**: der Lesepfad wandert
auf `GET /api/market/candles` (Ansatz 1), sobald B-D1 behoben ist — der Rest des Runners
bleibt, weil er ohnehin über `apiclient.py` spricht. Erwiese sich der eigene Prozess als zu
teuer (D-35 fällt), wäre der Weg zu Ansatz 3 dagegen **teuer**: Token, HTTP-Client und
Zyklussteuerung entfielen ersatzlos, aber die Zustandsführung müsste neu gedacht werden.

**Abweichung, ausdrücklich benannt:** Der Auftraggeber hat den Zyklus als
`GET /api/market/candles → decide_fn → POST /api/risk/check` skizziert — das ist Ansatz 1.
Diese Spec empfiehlt Ansatz 2 aus den drei belegten Gründen oben. **Das ist eine
Entscheidung für den Orchestrator, nicht für mich** (Abschnitt 14, E-D-1).

---

## 12. Was D1–D3 für D5 offenhält — und was nicht

**Offen gehalten:**

| Entwurfsentscheidung | Warum sie D5 hilft |
|---|---|
| Der Runner ist ein **eigener Prozess** (Ansatz 2) | Genau dort wird D5 den signierten Order-Client beheimaten müssen — nicht im Webprozess. Die Prozessgrenze existiert dann schon, inklusive Neustartverhalten und eigenem Speicherbudget (D-35) |
| Jede Buchung geht durch **`execute_proposal()`** (E-003) | D5 tauscht das Füllmodell **hinter** dem Nadelöhr aus. Der Runner, `strategie.py` und die Suche sehen davon nichts |
| `decide_fn` ist **rein** und bekommt den Zustand gereicht | D5 wechselt die **Quelle** des `PortfolioState` (Journal → abgeglichener Börsenkontostand), nicht seinen **Typ**. `risk.PortfolioState` bleibt unverändert |
| Der Agent lebt mit **`pending_fill`** (E-006, bis ~16 min) | Echte Market-Orders haben ebenfalls eine Antwortverzögerung. Ein Agent, der auf sofortige Fills gebaut wäre, müsste für D5 umgeschrieben werden |
| **Ein Vorschlag je Kerze** (D-26) | Deckelt die Orderfrequenz strukturell auf 96/Tag bei 15m — die Grundlage jeder Ratenbudget- und Idempotenzrechnung in D5 |
| **Keine neue Abhängigkeit** (D-16) | Die Security-Pipeline von D5 prüft dieselbe kleine Software-Stückliste wie heute |

**Was D5 nachrüsten muss — hier benannt, nicht weggeredet:**

| Lücke | Warum sie in D nicht geschlossen wird |
|---|---|
| **Kein Idempotenzschlüssel.** Der Runner erkennt seine eigene letzte Entscheidung über `decisions.ts` (5.3), nicht über eine ID | Für Papier genügt das (Sekundenauflösung gegen 900 s Kerzenabstand). Für Echtgeld **nicht**: dort braucht jede Order eine `clientOrderId` und eine „bei Zweifel nachfragen, nie blind erneut senden"-Regel (Recherche, Abschnitt 3). Die saubere Vorbereitung wäre eine Spalte `decisions.decision_candle_open_time` (Migration 5) — bewusst nicht in D, weil D ohne Schemaänderung auskommt |
| **Portfolio-Zustand kommt aus dem eigenen Journal**, nie aus einem Börsenkontostand | Der Abgleich ist ein eigener Baustein von D5. D1 verbaut ihn nicht — nur die Quelle wechselt |
| **Einsymbolig** | Ein Parametersatz, der auf BTCUSDC optimiert wurde, sagt nichts über die Allokation zwischen zwei Assets. Wer D5 auf zwei Symbolen will, braucht vorher D4 |
| **Die Suche bewertet hypothetische Läufe**, nicht realisierte Fills (1.1) | Vor Echtgeld gehört eine Messung dazwischen: weicht die real gebuchte Equity-Kurve des Agenten von der zeitgleich gerechneten Replay-Kurve ab — und um wie viel? Diese Zahl existiert heute nicht und wäre die ehrlichste Voraussetzung für D5 |

---

## 13. Offene Fragen

| Nr. | Frage | Was sie beantworten würde | Wo die Antwort hingehört |
|---|---|---|---|
| **F-D-1** | Welcher Kalenderzeitraum ist der **Entwicklungs-Holdout**? | Ein Blick auf `min/max(close_time)` der zurückgefüllten Kerzen nach D-10. Regel: die letzten 90 Tage der Historie | `walkforward.HOLDOUT_VON_MS/BIS_MS` als Konstante **und** in diese Spec, 5.2 |
| **F-D-2** | Wie groß sind `AGENT_SWITCH_MIN_ALPHA_PP` und `AGENT_MAX_DD_PCT`? | Die Baseline-Messung nach dem Verfahren in 7.1 (volle Gittersuche + 30 Zufallsziehungen auf dem Holdout) | `docs/abnahme/`, dann `.env` und `.env.example`. **Bis dahin leer — es wird nicht umgeschaltet** |
| **F-D-3** | Wie viele Entscheidungen je Sekunde schafft **CT 107** (2 Kerne)? | Ein `run_replay()` über 35.040 Kerzen im Container auf CT 107, drei Läufe, `:memory:` und Datei | **D-25**, und daraus `SUCHE_TRAIN_MAX_TAGE`, `SUCHE_ZIEHUNGEN`, `SUCHE_FENSTER`. Die Vorgaben aus 6.4 verlangen ≥ 2.304/s; die A-9-Untergrenze (876/s) reicht nicht |
| **F-D-4** | Wird **B-D1 behoben** — und in welchem Schritt (D0 oder als erste D3-Aufgabe)? | Eine Entscheidung, keine Messung. Ohne Behebung ist D-29 unerreichbar und die Live-Equity dauerhaft falsch | Orchestrator, Abschnitt 14, E-D-2 |
| **F-D-5** | Läuft der Nachlern-Lauf als **Compose-Dienst mit eigener Schleife** oder als **Timer im CT** (`systemd`/`cron`)? | Eine Betriebsentscheidung. Der Dienst ist selbsttragend, der Timer fasst `installer.sh` an. Kein Unterschied für den Code: `python -m aitra.suche --nachlernen` ist derselbe Befehl | Orchestrator, Abschnitt 14, E-D-3 |
| **F-D-6** | Wie viele **Roundtrips** erzeugt die Regelstrategie auf realen Daten überhaupt? | Der erste Baseline-Lauf. Erzeugt sie über 40 Tage weniger als 10 (7.3), ist die Umschaltregel strukturell blockiert und `SUCHE_OOS_TAGE` muss steigen | `docs/abnahme/`, danach ggf. `SUCHE_OOS_TAGE` |
| **F-D-7** | Soll der Runner bei `MARKET_DATA_ENABLED=false` überhaupt starten? | Heute wäre die Antwort auf `POST /api/risk/check` dauerhaft **503** (`web.py:195-196`), der Runner liefe also im Leerlauf | Kleinentscheidung, hier getroffen — siehe 14, „Selbst entschieden" |

---

## 14. Entscheidungen, die der Orchestrator treffen muss

> **Drei Stück. Keine davon habe ich selbst entschieden, keine davon geraten.**

**E-D-1 · Ansatz 1 oder Ansatz 2 für den Live-Runner (Abschnitt 11).**
Der Auftrag skizziert den Zyklus über `GET /api/market/candles` (Ansatz 1). Diese Spec
empfiehlt **Ansatz 2** (Kerzen und Ledger lokal, nur die Buchung über HTTP), weil Ansatz 1
zwei fremde Baustellen aufmacht: B-D1 muss behoben sein, **und** es bräuchte einen neuen
Endpunkt, der einen vollständigen `PortfolioState` liefert — `GET /api/status` liefert
`position_pct_by_symbol` und `start_of_day_equity` nicht (6.3). Ohne den wäre Kriterium
D-29 (Live-Parität) konstruktionsbedingt unerreichbar.
*Was von der Antwort abhängt:* 4.1 (ob `apiclient.candles()` gebraucht wird), 5.3, 9.2,
11 — und ob D den unauthentifizierten Endpunkt zum Lastpfad macht.

**E-D-2 · Wird B-D1 in D behoben, und wenn ja, in welchem Schritt? (F-D-4)**
`store.get_candles()`/`store_run.get_equity_curve()` sortieren aufsteigend und deckeln mit
`LIMIT`; vier Aufrufer behandeln das Ergebnis als „die neuesten" (2.3). Der Befund liegt
**außerhalb meines Auftrags** — deshalb gemeldet, nicht behoben. Drei Wege:
(a) in **D0** beheben (dann ist die Hardware sofort richtig, und D-34 wird grün, bevor D3
beginnt), (b) als **erste D3-Aufgabe** (dann bleibt D0 reine Auslieferung), (c) **nicht in
D** (dann bleibt D-34 rot und Ansatz 1 ist endgültig ausgeschlossen).
*Meine Einschätzung, nicht meine Entscheidung:* (a) — der Fehler verfälscht ab dem Moment,
in dem D0 die Marktdaten einschaltet, jede Live-Equity und jede live bemessene Ordermenge.

**E-D-3 · Wo läuft der Nachlern-Lauf? (F-D-5)**
Eigener Compose-Dienst mit eigener Schleife (selbsttragend, aber ein dritter
Container-Prozess) oder `systemd`-Timer/`cron` im CT (schlanker, fasst aber
`installer.sh` an). Der Code ist in beiden Fällen derselbe Befehl.

### Selbst entschieden (Kleinkram, hier vermerkt)

| Entscheidung | Warum sie klein ist |
|---|---|
| `replay.py` wird **nicht geteilt**; stattdessen wandern die CLI-Attrappen nach `strategie.py` (4.3) | Keine Befehlsänderung beim Nutzer, 20 statt 2 Zeilen Luft, und die Strategien landen dort, wo Strategien hingehören |
| Alle neuen Module bleiben **≤ 200 Zeilen** (4.4) | Dann muss weder die A-Spec ergänzt noch der Wächter angefasst werden |
| Der Runner führt **keine eigene Zustandsdatei**, sondern rekonstruiert aus dem Journal (5.3) | Dasselbe Muster wie `Ledger.restore()`/A-14; kein neuer Zustand, der driften kann |
| **Genau eine** Strategiefamilie in D2 (1.1) | YAGNI, und jeder zusätzliche Parameterraum vergrößert die Überanpassung |
| Der Runner startet auch ohne Marktdaten, protokolliert einmal `AGENT_IDLE_NO_MARKET_DATA` und wartet mit 600 s Backoff (F-D-7) | Ein Prozess, der beim Start wegen einer Konfiguration stirbt, sieht im `docker ps` aus wie ein Absturz |
| Neue Ereignisnamen: `AGENT_PARAMS_SWITCHED`, `AGENT_PARAMS_KEPT`, `AGENT_PARAMS_INVALID`, `AGENT_AUTH_FAILED`, `AGENT_CYCLE_EXCEPTION`, `AGENT_IDLE_NO_MARKET_DATA`, `SUCHE_BUDGET_EXHAUSTED` | Benennung im Stil des Bestands (`MARKET_DATA_STALE`, `POLL_CYCLE_EXCEPTION`, `NARROW_TRADING_WINDOW`) |

---

*Ende der Spec. **37 Abnahmekriterien**, **7 offene Fragen**, **3 Entscheidungen** für den
Orchestrator. Blockierend sind E-D-1 (Zuschnitt von D3) und E-D-2 (B-D1); F-D-1 bis F-D-3
blockieren den Baubeginn von D0 und D1 nicht, wohl aber das Festschreiben der Suchvorgaben
in D2.*
