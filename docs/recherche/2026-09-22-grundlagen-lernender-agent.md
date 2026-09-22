# Recherche — Grundlagen für einen lernenden Handelsagenten

- **Datum:** 2026-09-22
- **Zweck:** Entscheidungsgrundlage für den Nutzer, keine Spec. Es wird nichts implementiert.
- **Bezug:** Teilprojekt A2 (abgeschlossen, 285 Tests), Branch `feature/teilprojekt-a2`
- **Kennzeichnung:** jede Aussage als **belegt** (Datei/Zeile), **gemessen** (Befehl+Ausgabe)
  oder **Einschätzung** markiert.

---

## 1. Was der Bestand dem Agenten schon bietet — und was fehlt

### 1.1 Zwei völlig verschiedene Schnittstellen, nicht eine

**Im Zeitraffer/DB-Replay** ist die Schnittstelle ein simpler In-Prozess-Funktionsaufruf.
`run_replay()` nimmt `decide_fn: Callable[[Sequence[Candle]], Proposal]` entgegen
(**belegt**, `app/aitra/replay.py:19,60`) und ruft sie bei jeder Kerze `t` auf:

```python
proposal = decide_fn(candles[:t])   # replay.py:128
```

- **Eingabe:** ausschließlich `Sequence[Candle]` — Symbol, Intervall, `open_time`, `close_time`,
  `open/high/low/close/volume`, `closed`. Kein Portfolio, keine Kasse, keine gehaltene Menge,
  keine Gebühr, kein Kill-Switch-Status, keine Konfidenz aus vorherigen Entscheidungen
  (**belegt**, `app/aitra/marketdata.py:36-47` — `Candle`-Felder).
- **Rückgabe:** `risk.Proposal(symbol, action, position_pct, confidence)`
  (**belegt**, `app/aitra/risk.py:19-24`). `action` ∈ `{BUY, SELL, WAIT}`, `position_pct` ist ein
  Anteil am **Gesamt**portfolio in Prozent, nicht an der Kasse und nicht am Symbol.
- Der Agent bekommt **nie** die Fill-Kerze zu sehen (`history[-1].open_time == t`, gefüllt wird
  auf `t+1`, E-006). Das ist strukturell erzwungen, nicht Disziplin des Agenten.
- **Der Agent weiß nicht, was er selbst hält oder was seine letzte Order bewirkt hat.**
  `decide_fn` bekommt keinerlei Rückmeldung über frühere `Proposal`s — weder ob sie gefüllt,
  abgelehnt oder noch schwebend waren, noch die aktuelle Positionsgröße. Ein Agent, der z. B.
  „nicht zweimal hintereinander kaufen, wenn schon 8 % Position da sind" lernen soll, muss sich
  diesen Zustand **selbst mitführen** — er bekommt ihn nicht gereicht. Das ist keine Kleinigkeit
  für RL: der Zustandsraum, den die Engine dem Agenten zeigt, ist nur die Marktseite, nicht die
  Portfolioseite.

**Live gibt es diese Schnittstelle nicht.** Es gibt keinen Aufrufer, der irgendeine `decide_fn`
in Schleife ruft. Der einzige Weg, wie ein Vorschlag ins System kommt, ist ein authentifizierter
HTTP-Aufruf:

```
POST /api/risk/check   (Header X-Admin-Token, JSON-Body: symbol, action, position_pct, confidence)
```

(**belegt**, `app/aitra/web.py:177-221`). Der Poller (`poller.py`) tut im Live-Betrieb genau
drei Dinge mit Entscheidungen: er löst **bereits genehmigte, schwebende** Vorschläge auf
(`execute.resolve_pending()`, `app/aitra/poller.py:149-150`), lässt sie verfallen
(`execute.expire_stale_pending()`, Zeile 147) und aktualisiert Kerzen/Kill-Switch/Equity. Er trifft
**keine** Entscheidung und ruft **kein** `decide_fn` auf. Ein Agent, der live handeln will, muss
also ein **eigener, separater Prozess** sein, der:

1. sich selbst Marktdaten besorgt — über `GET /api/market/candles?symbol=…&interval=…&limit=…`
   (**belegt**, `web.py:225-236`, **unauthentifiziert**, keine Prüfung von `authorized()` an
   dieser Route),
2. selbst Buch führt, was er zuletzt vorgeschlagen hat (das System reicht das nicht zurück),
3. den Admin-Token besitzt und ihn bei jedem `POST /api/risk/check` mitschickt,
4. mit `status: "pending_fill"` und `expected_fill_after_ms` umgehen kann (bis zu ~16 Minuten
   Verzögerung bei 15m, E-006) — die Antwort kommt asynchron, nicht im selben Aufruf.

### 1.2 Stimmt die Zusage aus Spec 9.2, dass derselbe Agent unverändert live und im
Zeitraffer läuft?

**Nur für einen Teil des Systems, nicht für den Agenten.** Spec 9.2 sagt wörtlich: „Identische
Reihenfolge wie live, identische Funktionen. Der einzige Unterschied ist die Uhr, die Quelle und
der lauf-lokale Kill Switch." Das ist **belegt und zutreffend** — aber es beschreibt die
**Engine** (`RiskEngine.check` → `sizing.size_order` → `Ledger.apply`, aufgerufen über
`execute_proposal()` in beiden Betriebsarten), nicht die Integration eines Agenten. Für den
Agenten selbst gilt das Gegenteil:

| | Zeitraffer | Live |
|---|---|---|
| Aufrufmuster | In-Prozess-Funktion, synchron | Eigener Prozess, HTTP, asynchron |
| Bekommt Fill-Ergebnis zurück? | nein (aber `execute_proposal` intern sofort) | nein — muss selbst pollen (`GET /api/decisions`) |
| Muss Admin-Token kennen? | nein | ja |
| Muss Pending/Expiry selbst behandeln? | nein (`run_replay` reicht `next_candle` sofort mit) | ja |
| Symbolumfang | **ein** Symbol pro Lauf (siehe 1.3) | beliebig viele (`cfg.market_symbols`, Vorgabe 2) |

Ein Agent, der als reine Python-Funktion `f(history) -> Proposal` geschrieben wird, kann diese
Funktion zwar in beiden Welten **aufrufen** — aber die Aufrufumgebung drumherum (Zustand,
Timing, Authentifizierung, Antwortverarbeitung) muss für live komplett neu gebaut werden. Das ist
kein Bug, sondern eine **fehlende Schicht**: ein „Live-Runner", der `decide_fn` periodisch mit
frisch aus `GET /api/market/candles` gezogener Historie aufruft und das Ergebnis über
`POST /api/risk/check` einreicht. Diese Schicht existiert heute **nicht** (**belegt**: kein
Modul unter `app/aitra/*.py` importiert `decide_fn`-artige Aufrufe außerhalb von `replay.py`,
gemessen per `grep -rn "decide_fn" app/aitra app/tests` → drei Treffer, alle in
`replay.py`/`test_replay.py`/ein Docstring-Verweis in `benchmark.py`).

### 1.3 Ein Symbol, nicht mehrere — eine zweite, wichtigere Lücke

`run_replay()` ist **strukturell einsymbolig**: „`run_replay()` ist einsymbolig: candles,
decide_fn und der Buy-&-Hold-Vergleich laufen alle auf `candles[0].symbol`" (**belegt**,
`app/aitra/replay.py:73-79`), erzwungen durch eine `ValueError`, wenn `benchmark_symbol` vom
Kerzensymbol abweicht (Zeile 85-90). Getestet und als Vertrag festgeschrieben: „`run_replay()`
ist einsymbolig (Docstring)" (**belegt**, `app/tests/test_replay.py:736`).

Live dagegen handelt der Poller serienmäßig **zwei** Symbole (`BTCUSDC`, `BNBUSDC`,
`config.py:44`, K-1 der Spec). Ein Agent, der zwischen zwei Assets **allozieren** soll (z. B.
„40 % BTC, 20 % BNB, Rest Kasse"), lässt sich mit dem heutigen Zeitraffer **gar nicht trainieren
oder backtesten** — er müsste entweder zwei unabhängige Einzelläufe fahren (verliert jede
Information über Korrelation zwischen den beiden Symbolen) oder `run_replay()` müsste um
Mehrsymbol-Fähigkeit erweitert werden. Das ist eine der teureren Erweiterungen, die für „lernender
Agent" auf mehr als einem Markt nötig würde, und sie steht in keiner bisherigen Aufgabenliste.

### 1.4 Was an Daten überhaupt vorhanden ist

- **Vorhanden, aus dem Bestand nutzbar:** OHLCV-Kerzen (15m, zwei Symbole), Equity-Kurve
  (`equity_curve`-Tabelle, `store_run.py`), Fills mit Preis/Gebühr/Slippage, Ablehnungscodes,
  Benchmark-Equity, Kill-Switch-Ereignisse, `pending_ref_price`/`pending_base_qty` an
  schwebenden Entscheidungen. Alles über `store_run.py`/`store.py` abrufbar, alles als `Decimal`
  bzw. TEXT (E-007).
- **Nicht vorhanden:** Orderbuchtiefe, Trade-Tape, Open-Interest/Funding (Spot hat das ohnehin
  nicht), Nachrichten/Sentiment (Out-of-Scope, Teilprojekt B), irgendein Merkmal, das nicht aus
  OHLCV ableitbar ist. **Einschätzung:** Für 15m-Kerzen ist das eine ziemlich dünne
  Informationsgrundlage; die meisten klassischen technischen Indikatoren (SMA, RSI, ATR, Bollinger)
  lassen sich daraus aber ohne neue Datenquelle berechnen.

---

## 2. Die Entscheidungen, die der Nutzer treffen muss

### Entscheidung 1 — Welcher Lernansatz?

**Worum es geht:** „Lernender Agent" ist ein Bereich, kein Punkt. Die Randbedingung
`app/requirements.txt` hat heute **zwei** Zeilen (**gemessen**: `cat app/requirements.txt` →
`flask==3.1.3`, `gunicorn==26.2.0`), begründet in E-004 mit Angriffsfläche, Speicherbudget
(2 GiB RAM, F-1, **gemessen** im Proxmox-Webinterface am 2026-09-22) und Kontrolle über das
Füllmodell.

**Optionen:**

1. **Parameteroptimierung einer festen Regelstrategie** (z. B. „kaufe, wenn SMA-schnell über
   SMA-langsam kreuzt, bei Schwellen `a,b,c`") über Gittersuche oder Zufallssuche im Zeitraffer.
   - *Kosten:* keine neue Abhängigkeit — Gittersuche ist eine Schleife um `run_replay()`, in
     Standard-`decimal`/`itertools`. Läuft mit A-9s Tempo (876–2.920 Entscheidungen/s,
     **belegt**, Spec A-9) problemlos für Zehntausende Parameterkombinationen über
     Quartalsepisoden.
   - *Grenze:* Der „Lernraum" ist der Strategieraum, den ein Mensch vorher entworfen hat. Der
     Agent lernt nicht neue Zusammenhänge, nur die besten Zahlen für eine feste Formel.
2. **Bandit-/einfaches Policy-Gradient-Verfahren auf handgefertigten Merkmalen**, reine
   Python/`decimal`- oder notfalls `array`-Implementierung ohne `numpy`.
   - *Kosten:* mehr eigener Code (Gradientenrechnung, Exploration), aber weiterhin **keine**
     neue Laufzeitabhängigkeit. Tempo ist die Sorge: A-9 fordert ≥ 876 Entscheidungen/s im
     Container; reine Listen-Arithmetik in Python für z. B. 20 Merkmale × ein kleines lineares
     Modell ist dafür realistisch machbar (**Einschätzung**, nicht gemessen).
3. **Reinforcement Learning mit neuronalem Netz** (z. B. `stable-baselines3`/PPO/DQN).
   - *Kosten, gemessen/recherchiert:* `stable-baselines3` verlangt `numpy>=1.20,<3.0`,
     `torch>=2.3` (aktuell `>=2.8` in neueren Releases), `gymnasium>=0.29.1` — drei neue
     Laufzeitabhängigkeiten, davon `torch` allein ist ein Vielfaches der bisherigen
     Container-Software (**belegt/recherchiert** über `WebSearch`, PyPI/GitHub-Releaseseiten,
     2026-09-22; das exakte `torch`-Wheel-Gewicht wurde nicht selbst nachgemessen — offen).
     E-004 hat für `pandas`+`numpy` bereits „rund 120 MB installiert" **gemessen/genannt**
     (`docs/entscheidungen/E-004`); `torch` liegt laut allgemeiner Erfahrung eine Größenordnung
     darüber (**Einschätzung**, nicht selbst gemessen — vor einer Entscheidung nachzumessen:
     `pip download torch --no-deps -d /tmp` im Zielcontainer).
   - Das bricht E-004 direkt: neue Angriffsfläche (jedes dieser Pakete zieht weitere nach),
     neuer Speicherbedarf in einem Container mit 2 GiB RAM und 15,58 GiB Bootdisk (**gemessen**,
     F-1), und ein fremdes numerisches Ökosystem, dessen Korrektheit man nicht mehr komplett
     selbst prüft.

**Empfehlung:** **Option 1 zuerst, Option 2 als Ausbaustufe, Option 3 nur mit einer expliziten,
gesonderten Entscheidung, die E-004 bewusst aufhebt.** Begründung: Aitras eigener Maßstab
(E-001, E-004) ist „kein RL-Framework in A/A2", und das war eine bewusste, begründete
Entscheidung des Auftraggebers, keine vorläufige. Eine Gittersuche über eine Handvoll Parameter
ist mit dem heutigen Zeitraffer **sofort** baubar, ohne eine einzige neue Zeile in
`requirements.txt`, und liefert schon einen „lernenden" (sich selbst verbessernden) Agenten im
Sinne von „er stellt sich anhand eigener historischer Ergebnisse neu ein" — was der Nutzer laut
Auftrag zuerst will. RL mit neuronalem Netz ist die teuerste Antwort auf eine Frage, die noch
nicht gestellt wurde: Ob eine einfache Regel + Parametersuche für Daytrading auf 15m-Kerzen mit
2 Symbolen überhaupt Kapazität für mehr braucht, ist unbewiesen. Sylvron-Prinzip YAGNI trifft
hier wörtlich zu.

### Entscheidung 2 — Merkmalsraum: aus welchen Daten entscheidet der Agent?

**Worum es geht:** `decide_fn` bekommt heute nur `Sequence[Candle]` für **ein** Symbol
(1.1/1.3). Jedes Merkmal muss daraus abgeleitet werden.

**Optionen:**

1. **Reine Preis-/Volumenmerkmale aus OHLCV eines Symbols** (Returns, gleitende Durchschnitte,
   Volatilität, RSI o. Ä.), berechnet innerhalb von `decide_fn`, ohne Änderung an der
   Engine-Schnittstelle.
   - *Kosten:* null an der Schnittstelle. Grenze: `decide_fn` kennt nur die Vergangenheit des
     einen Symbols, nicht das andere gehandelte Symbol, nicht das eigene Portfolio (1.1).
2. **Zusätzlich Portfolio-Zustand** (Kasse, Position, letzte Entscheidung) als weiterer Parameter
   an `decide_fn` — würde die Signatur `Callable[[Sequence[Candle]], Proposal]` ändern zu etwas
   wie `Callable[[Sequence[Candle], PortfolioState], Proposal]`.
   - *Kosten:* Eingriff in `replay.py` (`run_replay`) **und** in einen noch zu bauenden
     Live-Runner — an zwei Stellen konsistent zu halten, aber technisch klein.
3. **Mehrsymbol-Merkmale** (z. B. Verhältnis BTC/BNB, Korrelation) — braucht die unter 1.3
   beschriebene Erweiterung von `run_replay()` auf mehrere Symbole gleichzeitig.
   - *Kosten:* die teuerste Option, weil sie den Kern von `run_replay()` anfasst (heute
     strukturell einsymbolig, mit hartem `ValueError`-Schutz dagegen).

**Empfehlung:** **Option 1 zum Start, Option 2 als nächster, klar geschnittener Schritt.**
Ohne Portfolio-Zustand kann ein Agent nicht lernen, seine Positionsgröße von seiner aktuellen
Exposition abhängig zu machen — ein sinnvoller Daytrading-Agent braucht das fast zwangsläufig.
Aber das ist ein sauber abgrenzbares, einzeln testbares Vorhaben (Signaturänderung plus
Rot-Nachweis, dass die Engine dem Agenten nie einen falschen/veralteten Portfolio-Stand zeigt).
Mehrsymbol-Handel (Option 3) würde ich zurückstellen, bis der Ein-Symbol-Fall sauber läuft —
genau die inkrementelle Reihenfolge, die A1→A2 schon vorgemacht hat.

### Entscheidung 3 — Wie wird gelernt, ohne sich selbst zu betrügen?

**Worum es geht:** Der Zeitraffer ist der Trainingsplatz. Ohne Trennung von Trainings- und
Bewertungszeitraum lernt der Agent die Vergangenheit auswendig (Overfitting) und die gemessene
Rendite im Backtest sagt nichts über künftige Performance.

**Optionen:**

1. **Fester Train/Test-Split über Kalenderzeit** (z. B. 2024 zum Optimieren, 2025 nur zum
   einmaligen Messen) — die einfachste, in der Finanzwelt übliche Antwort.
   - *Kosten:* gering, nur Disziplin bei der Datumsauswahl.
   - *Grenze:* ein einziger Testzeitraum ist selbst eine Zufallsstichprobe; ein Agent, der auf
     genau diesem Testjahr zufällig gut abschneidet, sieht besser aus, als er ist.
2. **Walk-Forward-Validierung** (mehrere aufeinanderfolgende Trainings-/Testfenster, die mit der
   Zeit vorrücken) — Standard in der quantitativen Finanzforschung.
   - *Kosten:* mehrere Replay-Läufe statt einem, mehr Rechenzeit (aber bei 876–2.920
     Entscheidungen/s, A-9, unproblematisch), mehr Code zur Aggregation der Ergebnisse.
3. **Kreuzvalidierung über zufällige Zeitfenster** — in Finanzzeitreihen mit Autokorrelation
   fachlich fragwürdig (Zukunftsdaten könnten ins Training sickern, wenn Fenster sich
   überlappen) — **nicht empfohlen**, hier nur der Vollständigkeit halber genannt.

**Empfehlung:** **Walk-Forward (Option 2), mit einem festen letzten Out-of-Sample-Zeitraum, der
während der gesamten Entwicklung nie zur Parameterwahl angefasst wird.** Der Zeitraffer kann das
technisch schon heute (`replay.py --from --to`), es fehlt nur die Orchestrierung mehrerer Läufe
und die Regel, sie einzuhalten. Zusätzlich, unabhängig vom Validierungsschema: **E-008 mahnt
bereits an, dass ein Replay „optimistischer" ist als Live**, weil sich der Tagesverlust-Kill-
Switch im Replay an der UTC-Tagesgrenze selbst löst (**belegt**, `docs/entscheidungen/E-008`).
Jede Bewertung eines gelernten Agenten muss die Zahl „Kill-Switch-Auslösungen im Lauf"
(`ReplayResult.kill_switch_engagements`, **belegt**, `replay.py:37`) mit ausweisen — ein Agent,
der im Backtest gut aussieht, aber 40-mal angehalten worden wäre, ist kein guter Agent.

### Entscheidung 4 — Wann gilt ein Agent als „besser"?

**Worum es geht:** Ohne einen festen Maßstab optimiert jede Suche gegen Rauschen.

**Optionen:**

1. **Alpha gegen BTC-Buy-&-Hold** (`alpha_pct`, bereits im Bestand als Kennzahl vorhanden,
   **belegt**, Spec Abschnitt 7.1, `benchmark.py`) über den festen Out-of-Sample-Zeitraum.
2. **Risikobereinigt** (z. B. Rendite je Einheit `max_drawdown_pct`, ebenfalls schon eine
   bestehende Kennzahl, Spec 7.1) statt reiner Rendite — verhindert, dass ein Agent gewinnt, der
   nur mehr Risiko eingeht.
3. **Kombiniert mit einer Mindestzahl an Entscheidungen/Trades**, damit ein Agent, der zufällig
   einmal richtig kauft und sonst nichts tut, nicht als „bester Agent" durchgeht.

**Empfehlung:** **Alpha gegen Buy & Hold (1) als Hauptzahl, Max Drawdown (2) als Nebenbedingung
(„nicht schlechter als X %"), gemessen ausschließlich auf dem Out-of-Sample-Zeitraum.** Eine
konkrete Schwellenzahl (wie viele Prozentpunkte Alpha, welcher Zeitraum in Handelstagen) ist
heute **nicht** seriös festlegbar — dafür fehlt jede empirische Grundlage aus diesem Projekt
(**offen**, keine Messung vorhanden). Das gehört in die SPEC des Teilprojekts, nachdem eine
erste Baseline (Entscheidung 1, Option 1) gemessen ist, nicht hierher geraten.

### Entscheidung 5 — Reicht die heutige Schnittstelle, oder wird sie zuerst erweitert?

**Worum es geht:** Abschnitt 1 zeigt drei konkrete Lücken: kein Portfolio-Zustand in `decide_fn`,
kein Live-Runner, kein Mehrsymbol-Replay. Ein „Teilprojekt lernender Agent" könnte diese Lücken
mitlösen oder sie bewusst als Nicht-Ziel benennen und zunächst nur mit dem arbeiten, was da ist.

**Optionen:**

1. **Erst der Agent auf der heutigen Schnittstelle** (ein Symbol, keine Portfolio-Sicht), Lücken
   als benannte Nicht-Ziele — schnellster Einstieg, aber ein Agent, der strukturell nicht lernen
   kann, wann er schon investiert ist.
2. **Erst die Schnittstelle erweitern** (Portfolio-Zustand an `decide_fn`, siehe Entscheidung 2,
   Option 2), dann der Agent — sauberer, kostet aber einen eigenen kleinen Vorlauf-Schritt.

**Empfehlung:** **Option 2, aber als eigener, kleiner erster Schritt** (siehe Zuschnitt, Punkt 4
unten) — nicht vermischt mit der Lernlogik selbst. Genau die Größenordnung, in der A1/A2 schon
gearbeitet haben: eine Schnittstellenänderung mit eigenem Rot-Nachweis, bevor die komplexere
Logik obendrauf kommt.

---

## 3. Der Weg zu echtem Geld

Aitra liest heute **ausschließlich**. Kein API-Schlüssel existiert im Projekt (**belegt**, E-005:
„es existiert kein Schlüssel im Projekt, also kann auch keiner abfließen"). Was für eine
Order-Ausführung mit echtem Geld fehlt:

| Baustein | Fehlt heute? | Eigenes Teilprojekt? |
|---|---|---|
| **Signierte Aufrufe** (HMAC-SHA256 mit API-Secret, Binance-Order-Endpunkte) | vollständig fehlend; `binance.py` kennt nur öffentliche, unauthentifizierte GETs (**belegt**, E-005) | **ja** — komplett neue Angriffsfläche: ein Fehler hier bewegt Geld, nicht nur Anzeige |
| **Schlüsselverwahrung** (API-Key/Secret, nie im Code, nie geloggt) | fehlt vollständig; heute gibt es nur `ADMIN_TOKEN`, ein reines Dashboard-Passwort, kein Börsenschlüssel (**belegt**, `config.py:170-184`) | **ja**, gehört zwingend zusammen mit dem Punkt oben |
| **Teilausführungen** (Market-Order füllt nicht komplett) | Das Modell nimmt „vollständig gefüllt oder abgelehnt" an, nie beides teilweise (Spec Nicht-Ziele: „Limit-, Stop-, OCO-Orders; Teilfüllungen; Orderbuchtiefe. A kennt nur Market-Orders, die vollständig füllen") (**belegt**) | **ja** — ändert das Ledger-Datenmodell (`Fill` müsste eine Menge < angeforderter Menge tragen können) |
| **Abgelehnte Orders der Börse selbst** (nicht die eigene `Rejection`, sondern ein Fehler von Binance nach dem Absenden) | fehlt vollständig; heute lehnt nur die eigene `RiskEngine`/`Ledger` ab, nie die Gegenseite | kann **mitlaufen** mit dem Order-Ausführungs-Teilprojekt — dieselbe Codeebene |
| **Abgleich Journal ↔️ echter Kontostand** | fehlt vollständig; `Ledger.restore()` rekonstruiert heute aus dem **eigenen** Journal (`fills`/`positions`), nie gegen eine externe Quelle (**belegt**, `ledger.py:220-227`, `poller.py:200-205`) | **ja** — braucht einen periodischen `GET /api/v3/account`-Abgleich und eine Entscheidung, was bei Abweichung passiert (Kill Switch? Warnung? Anhalten?) |
| **Verbindungsabbruch mitten in einer Order** | fehlt vollständig; heute gibt es keine Order, die abbrechen könnte. `binance.py` hat zwar schon Timeouts/Backoff für **Lese**zugriffe (E-005), aber kein Konzept für „wurde die Order angenommen, bevor die Antwort verloren ging?" | **ja** — braucht `clientOrderId` als Idempotenzschlüssel und eine „bei Zweifel nachfragen, nie blind erneut senden"-Regel |
| **Marktordermodell zu optimistisch** | Das heutige Füllmodell nimmt an, dass jede Order zu `open`±Slippage vollständig füllt, mit fixen 5 bp Slippage (K-4: real gemessen ~0,06 bp Halbspanne, 5 bp ist ein **gewählter**, nicht gemessener Sicherheitsaufschlag, **belegt**, Spec K-4). Bei echten Market-Orders mit echtem Geld können Ausführungspreise **schlechter** von den Modellannahmen abweichen, besonders bei dünnerem Orderbuch oder in Stressphasen | Erkenntnis, kein eigenes Teilprojekt — gehört als Kalibrierungsaufgabe in den Order-Baustein |

**Was konkret schiefgeht, wenn es niemand baut — nicht allgemein:**

- **Ohne Idempotenz bei Verbindungsabbruch:** Ein Netz-Timeout nach dem Absenden einer Order
  sagt nichts darüber, ob die Order bei Binance angekommen ist. Ein naiver „bei Fehler erneut
  senden"-Reflex (der im Bestand für **Lese**zugriffe bereits Standard ist, Backoff in
  `poller.py:39`) würde bei einer **Schreib**operation zu einer **doppelt ausgeführten Order**
  führen — konkret: eine 1.000-USDC-BUY-Order wird zweimal gebucht, weil die erste Antwort nur
  verloren ging, die Order selbst aber durchging. Das ist kein Rand-, sondern ein
  Alltagsfall bei jeder Netzunterbrechung in einem Proxmox-Container.
- **Ohne Kontostand-Abgleich:** Driftet das eigene Journal vom echten Binance-Kontostand ab
  (z. B. durch eine manuell am Handy platzierte Order, einen nicht erfassten Airdrop, oder
  genau den obigen Doppelfehler), rechnet die Risk Engine auf einem **falschen** `PortfolioState`
  weiter. `MAX_EXPOSURE` und `MAX_POSITION` schützen dann vor nichts mehr, weil die Zahl, gegen
  die sie prüfen, nicht mehr stimmt. Das ist der Fall, in dem der Kill Switch am dringendsten
  gebraucht würde und am wenigsten greift, weil er auf denselben falschen Zahlen basiert.
- **Ohne Teilfüllungs-Handling:** Eine Order über 1.000 USDC, die nur zu 600 USDC gefüllt wird
  (dünnes Buch, Stressmoment), würde vom heutigen Modell entweder als „ganz gefüllt" (falsch,
  400 USDC Differenz verschwinden aus der Buchhaltung) oder als „ganz abgelehnt" (falsch, 600
  USDC wurden tatsächlich gehandelt, aber nirgends gebucht) fehlinterpretiert — in beiden Fällen
  bricht die Identität `equity == cash + Σ qty·mark`, die A-1 heute exakt fordert.

**Einschätzung, nicht gemessen:** Die genannten Bausteine (signierte Aufrufe + Schlüsselverwahrung
+ Kontostand-Abgleich) gehören fachlich zusammen und rechtfertigen ein **eigenes** Teilprojekt,
weil sie zusammen die gesamte neue Angriffsfläche „Geld bewegender Code" bilden — das ist genau
die Art Änderung, für die die Sylvron-Pipeline (Security-Gate, Rot-Nachweis, Review) am
wichtigsten ist. Teilausführungen und Verbindungsabbruch sind technisch enger mit diesem
Baustein verzahnt (dieselbe Order-Anfrage, dieselbe Antwortverarbeitung) und sollten **darin**
mitlaufen, nicht als separates Teilprojekt.

---

## 4. Vorschlag zum Zuschnitt

Aitra hat bislang zwei Teilprojekte geschafft: **A1** (offline: Marktdaten, Ledger, Benchmark,
Zeitraffer, 157 Tests) und **A2** (live: Poller, Dashboard, Auslieferung, +128 Tests, 285
gesamt). Dieselbe Größenordnung — ein für sich lauffähiges, geprüftes Stück — ist der Maßstab.

| # | Teilprojekt | Inhalt | Warum als eigener Schnitt |
|---|---|---|---|
| **D1** | **Portfolio-Sicht für `decide_fn`** | `decide_fn`-Signatur um einen Portfolio-Parameter erweitern (Entscheidung 2/5), in `replay.py` verdrahtet, mit Rot-Nachweis, dass der übergebene Zustand nie der aus der Fill-Kerze ist (E-006-Parität) | klein, isoliert, unmittelbar testbar, Voraussetzung für alles Folgende |
| **D2** | **Baseline-Agent + Gittersuche (Entscheidung 1, Option 1)** | Eine oder zwei feste Regelstrategien (z. B. gleitender Durchschnitt, Schwellenwert-Momentum) plus eine Parametersuche über den Zeitraffer, mit Walk-Forward-Auswertung (Entscheidung 3) und Alpha/Drawdown-Messung (Entscheidung 4) | keine neue Abhängigkeit, nutzt A1/A2 vollständig, liefert die erste ehrliche Zahl, gegen die alles Weitere sich misst |
| **D3** | **Live-Runner** | Der fehlende Prozess aus Abschnitt 1.2: zieht periodisch `GET /api/market/candles`, ruft denselben `decide_fn` wie im Zeitraffer, sendet `POST /api/risk/check`, behandelt `pending_fill`/Verfall, hält eigenen Zustand über Neustarts (analog `Ledger.restore()`) | eigene Fehlerklasse (Netz, Authentifizierung, asynchrones Timing) getrennt von der Lernlogik testbar — genau der Schnitt, der A1/A2 (offline/live) schon einmal getragen hat |
| **D4** | **Mehrsymbol-Zeitraffer** (falls Entscheidung 2/Option 3 gewünscht wird) | `run_replay()` von strukturell einsymbolig auf mehrere Symbole gleichzeitig erweitern, inklusive eines neuen A-8-artigen Paritätswächters | größte Änderung am Kern von A1; verdient eine eigene Spec und eigene Abnahmekriterien, nicht „nebenbei" in D2 oder D3 |
| **D5** | **Echtgeld-Ausführung** (Abschnitt 3) | Signierte Order-Aufrufe, Schlüsselverwahrung, Kontostand-Abgleich, Teilausführungen, Verbindungsabbruch-Idempotenz | größte Sicherheitsrelevanz im gesamten Projekt; braucht volle Security-Pipeline (SAST, Secrets-Scan, Threat-Modeling) als eigenes Gate, unabhängig vom Lernfortschritt |

**Reihenfolge-Empfehlung:** D1 → D2 → (D3 und D4 unabhängig voneinander, je nach dem, was der
Nutzer zuerst braucht: Live-Betrieb oder Mehrsymbol-Lernen) → D5 zuletzt, und D5 nur, wenn D2
über einen validierten Out-of-Sample-Zeitraum tatsächlich einen positiven, risikobereinigten
Alpha zeigt — alles andere wäre echtes Geld auf eine Strategie zu setzen, die noch niemand
gemessen hat.

---

## Offene Punkte, ausdrücklich nicht geraten

- **Welche Schwellenzahl für „besser als Buy & Hold" genügt**, um D5 zu rechtfertigen — keine
  Messung im Projekt liefert dafür heute eine Grundlage.
- **Ob `torch`/`numpy` je nötig werden**, hängt davon ab, ob D2 (Baseline) an eine Kapazitätsgrenze
  stößt, die eine feste Regel nicht mehr auflösen kann. Das ist heute unbekannt und nicht
  vorwegzunehmen.
- **Das tatsächliche Installationsgewicht von `torch` im Zielcontainer** wurde nicht gemessen
  (nur recherchiert); vor einer RL-Entscheidung wäre `pip download` im Container die richtige
  Messung, kein Schätzwert aus dem Internet.
- **A-10b (RSS im 24-h-Dauerbetrieb) ist laut Abnahme A2 noch offen** (**belegt**,
  `docs/abnahme/2026-09-22-teilprojekt-a2-live-und-dashboard.md`) — jede Aussage über
  Speicherbudget für einen zusätzlichen Live-Runner-Prozess (D3) baut auf einer noch nicht
  gemessenen Zahl auf.

Sources:
- [numpy · PyPI](https://pypi.org/project/numpy/)
- [Installation — Stable Baselines3 documentation](https://stable-baselines3.readthedocs.io/en/master/guide/install.html)
- [Stable-Baselines3 v2.4.0 Release Notes](https://github.com/DLR-RM/stable-baselines3/releases/tag/v2.4.0)
