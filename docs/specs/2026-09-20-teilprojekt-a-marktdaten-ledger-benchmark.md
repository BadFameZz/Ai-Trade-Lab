# SPEC — Teilprojekt A: Marktdaten, Paper-Ledger, Benchmark

- **Projekt:** AI Trade Lab (Aitra), Repo `BadFameZz/Ai-Trade-Lab`
- **Ausgangsversion:** v0.2.1 (gemessen: 485 Zeilen in `app/aitra/`)
- **Zielversion:** v0.3.0
- **Fassung:** 3 vom 2026-09-21 (Änderungen gegenüber Fassung 2 in Abschnitt 19)
- **Status:** freigabereif — offen sind nur noch F-1 und F-3, beide reine Zahlenwerte
- **Zugehörige Entscheidungen:** `docs/entscheidungen/E-001` … `E-008`

---

## 1. Ziel

Aitra kann heute Vorschläge *prüfen*, aber nichts *bewerten* und nichts *ausführen*. Teilprojekt A
schließt genau diese Lücke — ohne eine Zeile KI:

1. **Marktdaten** von Binance Spot (öffentlich, nur lesend), persistiert, mit Veraltet-Erkennung,
   die den Kill Switch auslöst.
2. **Paper-Ledger**, das aus Kerzen Fills erzeugt, Kasse und Positionen exakt führt und
   `risk.PortfolioState` mit echten Zahlen füllt — statt der heute fest verdrahteten
   `positions=[]` und `trades_total=0` in `web.py:122-123`.
3. **Benchmark BTC Buy & Hold**, gerechnet durch dasselbe Ledger, damit die Zahl vergleichbar ist.
4. **Drei Betriebsarten, eine Implementierung:** live, aus der Datenbank, im Zeitraffer.

Der Zeitraffer ist kein Bonus, sondern der Beweis für E-001 und die Voraussetzung für Teilprojekt C.

### Nicht-Ziele (Out of Scope, ausdrücklich)

| Nicht in A | Warum / wohin |
|---|---|
| Jede Form von KI, LLM, Sentiment, News | Teilprojekt B |
| RL-Agent, Gym-Environment, Reward-Design, Training | Teilprojekt C |
| Handelsstrategien jenseits von Buy & Hold und Test-Attrappen | Roadmap 4, nach A |
| Echtes Geld, API-Schlüssel, private Binance-Endpunkte, Orderplatzierung | dauerhaft gesperrt (`config.live_locked`) |
| WebSocket-Streams | A pollt REST; Streams erst, wenn die Latenz nachweislich stört |
| Limit-, Stop-, OCO-Orders; Teilfüllungen; Orderbuchtiefe | A kennt nur Market-Orders, die vollständig füllen |
| Short, Margin, Hebel, Futures | Risk Engine verbietet es bereits (`risk.py:83`) |
| Mehrere Kontowährungen | Quote ist immer USDC |
| Steuern, Funding, Ein-/Auszahlungen, BNB-Rabatt, VIP-Stufen | nicht modelliert — ein Rabatt, den wir nicht haben, schönt jedes Ergebnis |
| Login/Auth fürs Dashboard | unverändert: nur im Heimnetz betreiben |
| **HTTP-Endpunkt zum Starten eines Replays** | nur CLI. Ein Endpunkt, der auf einem Dashboard ohne Login rechenintensive Läufe startet, ist eine offene Tür |
| Chart-Bibliothek, Build-Schritt fürs Frontend | Inline-SVG, zwei Linien, keine neue Abhängigkeit |
| Achsenbeschriftung, Zoom, Tooltip im Chart | genau das, was der Platzhalter heute verspricht — nicht mehr |
| Automatisierter Browsertest des Dashboards | siehe Lücke L-1 in Abschnitt 12 |
| Aufrüstung auf Python 3.13 | A bleibt auf `python:3.12-slim` (Abschnitt 2.1) |
| Automatisches Überschreiben der `.env` beim Update | dort steht der Admin-Token; stattdessen Befund B-6 und Kriterium A-22 |

---

## 2. Gemessene Ausgangslage

Alles hier Genannte ist aus dem Code oder live von der Binance-API gelesen, nichts geschätzt.

### 2.1 Bestandscode

| Datei | Zeilen | Was davon A betrifft |
|---|---|---|
| `app/aitra/config.py` | 77 | `Config` ist ein frozen dataclass mit 6 Pflichtfeldern in fester Reihenfolge; Tests konstruieren **positional** (`Config(100, 10, 2, 50, tmp_path, TOKEN)`). Neue Felder müssen hinten mit Vorgabewert angehängt werden. `_num()` liefert `float` (Befund B-4). |
| `app/aitra/db.py` | 103 | `MIGRATIONS` ist eine Liste; `migrate()` wendet ab `schema_version` an. Neue Migration = Index 1 der Liste → Version 2. `PRAGMA foreign_keys=ON` ist aktiv, Fremdschlüssel werden also erzwungen. |
| `app/aitra/risk.py` | 86 | `Proposal`, `PortfolioState`, `RiskDecision`, `RiskEngine.check()`. Geld als `float`. SELL-Prüfung fehlerhaft (Befund B-1). |
| `app/aitra/web.py` | 219 | `portfolio()` (52-54) liefert einen Standzustand; `positions=[]`, `trades_total=0` (122-123); `cash` wird aus `equity × (1 − exposure/100)` *abgeleitet* (114) statt geführt (Befund B-3). |
| `app/requirements.txt` | 2 | `flask==3.1.3`, `gunicorn==26.2.0` — beide auf PyPI nachgesehen, beide die aktuelle Fassung (Flask 3.1.3 vom 2026-02-19). |
| `app/Dockerfile` | 23 | Basis **`python:3.12-slim`**. A bleibt dabei; die Bauumgebung auf dem Mac ist 3.13, das Laufzeitziel ist 3.12. Ein Versionssprung gehört in einen eigenen Vorgang, nicht in A. |
| `app/docker-compose.yml` | 26 | `read_only: true`, `tmpfs: /tmp`, `cap_drop: ALL`, `USER` uid 10001 aus dem Dockerfile, Volume `./data:/app/data`. |
| `proxmox/installer.sh` | — | Vorgabe 4 Kerne / 8192 MB / 32 GB (32-35) — das ist die *Vorgabe*, nicht die Ausstattung von CT 107 (→ F-1). Zeile 533: `if [ ! -f .env ]` — ein Update fasst die Datei **nicht** an (Befund B-6, bestätigt). |

### 2.2 Binance Spot, live abgefragt am 2026-09-20

| Größe | Wert | Quelle |
|---|---|---|
| BTCUSDC Status | `TRADING` | `GET /api/v3/exchangeInfo?symbol=BTCUSDC` |
| BTCUSDC `tickSize` / `stepSize` / `minQty` / `minNotional` | `0.01` / `0.00001` / `0.00001` / `5.00` (`applyMinToMarket: true`) | ebd. |
| ETHUSDC `tickSize` / `stepSize` / `minQty` / `minNotional` | `0.01` / `0.0001` / `0.0001` / `5.00` | `GET /api/v3/exchangeInfo?symbol=ETHUSDC` |
| Präzision Basis/Quote (beide Paare) | 8 / 8 | ebd. |
| BTCUSDC Bid/Ask | `81287.03` / `81287.04` → Spanne 0,01 USDC = **0,123 bp**, Halb-Spanne 0,062 bp | `GET /api/v3/ticker/bookTicker` |
| BTCUSDC Tiefe an der Spitze | `askQty 0.23863 BTC ≈ 19.400 USDC` | ebd. |
| ETHUSDC Bid/Ask | `2631.77` / `2631.78` → Spanne 0,01 USDC = **0,038 bp**, Halb-Spanne 0,019 bp | `GET /api/v3/ticker/bookTicker` |
| ETHUSDC Tiefe an der Spitze | `askQty 15.5725 ETH ≈ 40.977 USDC` | ebd. |
| Gewicht `GET /api/v3/klines` | **2**, `limit` max 1000, Vorgabe 500 | Binance Spot REST-Doku |
| Kerzen-Feldreihenfolge | 12 Felder: openTime, open, high, low, close, volume, closeTime, quoteVolume, trades, takerBuyBase, takerBuyQuote, ignore | ebd. |
| IP-Limit | `REQUEST_WEIGHT 6000 / 1 min`, `RAW_REQUESTS 300000 / 5 min` | `exchangeInfo.rateLimits` |
| Überschreitung | HTTP 429 mit `Retry-After`, danach 418 (Bann 2 min … 3 Tage) | Binance Limits-Doku |
| Spot-Gebühr VIP 0 | **0,10 % Maker und Taker** | Binance Gebührenübersicht |
| Zweit-Host für Marktdaten | `data-api.binance.vision` antwortet (`{"serverTime":1789933036347}`) | live geprüft |

### 2.3 Die Betriebskonfiguration und was aus ihr folgt

**Entschieden:** `STARTING_BALANCE = 10000` USDC · Symbole **BTCUSDC, ETHUSDC** ·
Intervall **15m** · `MARKET_POLL_S = 60` · `MAX_POSITION_PCT = 10` (also höchstens 1.000 USDC
je Order).

Alle Zahlen im Folgenden sind gegen diese Konfiguration gerechnet.

**K-1 — Das handelbare Fenster ist weit: 5 bis 1.000 USDC.**
`minNotional` ist 5,00 USDC, die größte erlaubte Order 1.000 USDC. Das sind **0,05 % bis 10 %**
des Kapitals, ein Faktor 200. Mit dem alten Startkapital von 100 USDC wären es 5 % bis 10 %
gewesen, ein Faktor 2 — B und C hätten praktisch zwei Positionsgrößen zur Wahl gehabt.
**Achtung:** Auf einem *bestehenden* Container greift das nicht automatisch → Befund B-6,
Kriterium A-22.

**K-2 — Der Losgrößenverlust, drei Bezugsgrößen.**
Der maximale Rundungsrest ist `step_size × preis`:

| Paar | `step × preis` | bezogen auf die größte Order (1.000 USDC) | bezogen auf das Portfolio (10.000 USDC) | bezogen auf die kleinste Order (5 USDC) |
|---|---|---|---|---|
| BTCUSDC | 0,8129 USDC | **0,0813 %** | 0,0081 % | 16,3 % |
| ETHUSDC | 0,2632 USDC | **0,0263 %** | 0,0026 % | 5,3 % |

> Die Zahl, die eine Strategie spürt, ist die **ordbezogene**: 0,0813 % bei BTCUSDC.
> Der portfoliobezogene Wert (0,0081 %) beschreibt nichts, was jemand erlebt.

Beides liegt deutlich unter der Gebühr von 10 bp. Kritisch wird es nur am unteren Rand — K-3.

**K-3 — Die effektive Mindestordergröße liegt über `minNotional`.**
Weil die Menge immer abgerundet wird (E-002), kann eine Order, die auf genau 5,00 USDC zielt,
nach dem Runden unter 5,00 fallen und wird dann abgelehnt. Die kleinste Zielgröße, die
*garantiert* durchgeht, ist `min_notional + step × preis`:

- **BTCUSDC: 5,82 USDC** · **ETHUSDC: 5,27 USDC**

Eine abgeleitete, keine von Binance genannte Zahl. Sie gehört in die Fehlermeldung von
`Rejection(MIN_NOTIONAL)`, damit B und C nicht im Dunkeln tappen. Kriterium A-4b misst sie.

**K-4 — Reibung: Gebühren dominieren, Slippage ist Beiwerk.**
Roundtrip-Gebühr = 20 bp. Die gemessenen Halb-Spannen sind 0,062 bp (BTC) und 0,019 bp (ETH),
die Tiefe an der Spitze (19.400 bzw. 40.977 USDC) trägt jede Order dieses Labors — auch die
größte mit 1.000 USDC — ohne das Buch zu bewegen. Realistische Slippage ist also **rund
0,06 bp, nicht 5 bp**. Die Vorgabe von 5 bp (Abschnitt 6.3) ist ein bewusster
Sicherheitsaufschlag von 50 % auf die Gesamtreibung — begründet, nicht gemessen, und als
solcher gekennzeichnet.

**K-5 — Was 15m an Mengengerüst bedeutet.**
Das Intervall bestimmt fast jede Zahl in den Abnahmekriterien:

| Größe | Wert bei 15m (`interval_s = 900`) |
|---|---|
| Kerzen je Tag und Symbol | 96 |
| Kerzen je Jahr und Symbol | **35.040** |
| Kerzen bei 400 Tagen Aufbewahrung, 2 Symbole | **76.800** |
| Veraltet-Schwelle `warn_s` = `max(150, 1,5·900)` | **1.350 s** (22,5 min) |
| Veraltet-Schwelle `kill_s` = `max(300, 3·900)` | **2.700 s** (45 min) |
| Verzögerung Klick → Fill (E-006), inkl. 60 s Poll | **bis ~16 min** |
| Verfall eines schwebenden Vorschlags (`2 · interval_s`) | **30 min** |

---

## 3. Module und Dateien

Alle neuen Module liegen in `app/aitra/`, folgen dem Bestandsstil (`from __future__ import annotations`,
frozen dataclasses, deutschsprachige Docstrings, keine Klassen ohne Grund) und hängen **nur** von
der Standardbibliothek und voneinander ab.

### 3.1 Neu

| Modul | ~Zeilen | Verantwortung (eine pro Modul) | Hängt ab von |
|---|---|---|---|
| `money.py` | 90 | Decimal-Kontext, Parsen, Quantisieren (`step_down`, `tick_up`, `tick_down`), `SymbolSpec`, kanonische Text-Form für DB und JSON | — (stdlib) |
| `marketdata.py` | 150 | `Candle`, Protokoll `CandleSource`, `Clock`/`WallClock`/`SimClock`, `ListSource`, `SqliteSource`, `staleness()` inkl. intervallrelativer Schwellen | `money`, `store` |
| `binance.py` | 130 | **Die einzige Stelle mit Netzzugriff.** URL-Allowlist, Timeouts, Größenlimit, kein Redirect; `klines()`, `exchange_info()`, `server_time()` | `money`, `marketdata` |
| `ledger.py` | 170 | `Order`, `Fill`, `Position`, `Rejection`, `Ledger.apply()`, `Ledger.mark()`. **Rein:** kein Netz, keine DB, keine Uhr, kein Zufall | `money`, `marketdata` |
| `sizing.py` | 75 | `size_order()` — Prozent → Menge, unter Berücksichtigung von Spec, Gebühr und Slippage | `money`, `risk`, `ledger` |
| `execute.py` | 120 | `execute_proposal()` — **der einzige Aufrufer von `Ledger.apply()`.** Risk Engine → Sizing → Ledger → Journal; verwaltet auch schwebende Vorschläge (E-006) | `risk`, `sizing`, `ledger`, `store` |
| `benchmark.py` | 70 | `BuyAndHold` — eine zweite `Ledger`-Instanz, genau ein BUY, danach nur `mark()` | `ledger`, `money` |
| `replay.py` | 120 | `run_replay(candles, decide_fn, ctx)` mit `SimClock` und lauf-lokalem Kill Switch (E-008); `__main__` als CLI | `marketdata`, `execute`, `benchmark` |
| `poller.py` | 160 | Live-Betriebsart: Thread, Backoff, Kerzen persistieren, Veraltet-Erkennung → Kill Switch, schwebende Fills ausführen, Equity-Schnappschüsse | `binance`, `marketdata`, `execute`, `store` |
| `store.py` | **324** *(geschätzt waren 190)* | Lese-/Schreibzugriff auf die neuen Tabellen. Geld nur über `money` in TEXT und zurück | `db`, `money` |
| `backfill.py` | 70 | `python -m aitra.backfill --symbol … --interval … --days …`, im Container aufgerufen | `binance`, `store` |

**Summe geschätzt: ~1.345 neue Zeilen** (Bestand 485 → ~1.850).

### 3.1b Die 200-Zeilen-Regel ist ein Richtwert, keine Grenze *(Ruling Fix-Welle A1)*

Die ursprüngliche Randbedingung lautete „Kein Modul über 200 Zeilen". Sie ist **gescheitert**:
nach der Fix-Welle überschreiten sie **sechs von zwölf** Modulen, jedes aus nachvollziehbarem
Grund. Das wird hier festgehalten statt stillschweigend übergangen — am Ende umzubauen wäre
schlechter als die Regel ehrlich zu korrigieren.

**Neue Regel:**
- **200 Zeilen sind ein Richtwert.** Wer ihn überschreitet, begründet es hier in der Tabelle.
- **Ab 300 Zeilen wird geteilt.** Das ist die harte Marke.

Gemessen am 2026-09-21 (`wc -l app/aitra/*.py`):

| Modul | Zeilen | nicht leer | Warum über 200 |
|---|---:|---:|---|
| `store.py` | **324** | 270 | 19 flache CRUD-Funktionen über 6 Tabellen, genau eine Verantwortung. Gewachsen um `reject_decision()` (Ablehnungen im Journal) und `EquityPoint`/`append_equity_points()` (Sammelschreiben). **Über der 300er-Marke — siehe Konflikt unten.** |
| `replay.py` | **283** | 233 | Engine *und* CLI in einem Modul — so von Abschnitt 3.1 vorgegeben („`__main__` als CLI"). Gewachsen um `--strategie` (A-8c), Equity-Kurve, `finish_run`, `_iso_ms` (UTC). |
| `ledger.py` | **227** | 192 | Gewachsen um die strenge `mark()` (wirft bei Position ohne Marktpreis) und `last_marks`. Fast nur Docstring, der das *Warum* trägt. |
| `web.py` | **218** | 188 | Bestand, von der Fix-Welle nicht berührt. |
| `execute.py` | **204** | 178 | Gewachsen um `reject_decision()` an sieben Stellen und `pending_ref_price`. |
| `db.py` | **201** | 175 | Gewachsen um Migration 3 samt Begründung im SQL-Kommentar. |

Zwei Beobachtungen, die zur Regel gehören: Ein erheblicher Teil des Wachstums sind **Docstrings
und Kommentare**, die das *Warum* einer Korrektur tragen. Diese zu kürzen, um unter eine Zahl zu
kommen, wäre das Spiel mit der Kennzahl statt Entwurfsarbeit — deshalb zählt die Regel Zeilen,
verlangt aber eine Begründung, keine Kürzung.

> **Konflikt, offen für den Orchestrator:** `store.py` liegt mit **324 Zeilen** bereits über der
> soeben gesetzten 300er-Marke. Damit stehen sich zwei Rulings gegenüber: das aus Aufgabe 3
> („`store.py` bleibt ungeteilt — Zweck der Regel ist Fokus, nicht die Zahl") und die neue Marke.
> In dieser Runde wurde **nicht geteilt**: unmittelbar vor Nachprüfung und Push eine
> Modulaufteilung vorzunehmen, von der kein A1-Kriterium profitiert, legt das ganze Risiko auf
> die Änderung. Die Naht ist bekannt und im Aufgabe-3-Ruling bereits benannt — **Marktdaten**
> (`candles`, `symbol_specs`) gegen **Lauf und Ledger** (`runs`, `fills`, `positions`,
> `equity_curve`, `decisions`). *Kosten bei Irrtum:* `store.py` wächst in A2 um die
> Poller-Zugriffe weiter und wird unhandlich; die Trennung an dieser Naht ist ein Commit.

### 3.2 Geändert

| Datei | Änderung |
|---|---|
| `config.py` | `_dec()` neben `_num()` (Befund **B-4**: `Decimal(raw)` direkt aus dem Umgebungsstring, nie über `float`); `starting_balance` wird `Decimal`, Vorgabe **10.000**; neue Felder **hinten** mit Vorgabewert; Allowlist-Prüfung für `BINANCE_BASE_URL`; Startwarnung bei zu engem Handelsfenster (A-22) |
| `risk.py` | `PortfolioState.equity`/`start_of_day_equity` werden `Decimal`; neues Feld `position_pct_by_symbol: Mapping[str,float] = {}`; SELL-Prüfung nutzt den **symbolbezogenen** Wert (Befund **B-1**, Kriterium A-6c) |
| `db.py` | Migration Nr. 2 (Abschnitt 5) |
| `web.py` | `portfolio()` liest aus dem Ledger; **geführte** statt abgeleiteter Kasse (Befund **B-3**); `positions`/`trades_total` werden echt; neue Endpunkte; `/api/health` kennt `market_data` und `paper_engine` wirklich; `POST /api/risk/check` ruft `execute_proposal()` |
| `static/index.html` | Der Platzhalter „Noch keine Daten" (129) wird ein Inline-SVG mit zwei `<polyline>`; Karte „Offene Positionen"; KPIs Trefferquote und Max Drawdown; Anzeige schwebender Vorschläge (E-006) |
| `app/.env.example` | `STARTING_BALANCE=10000`, `MARKET_INTERVAL=15m`, übrige Marktdaten-Variablen |
| `README.md` | Befund **B-2** (Egress, Kriterium A-21) und Befund **B-6** (Handgriff beim Update) |
| `requirements.txt` | **unverändert** — Kriterium A-18 |
| `app/CHANGELOG.md`, `app/VERSION` | v0.3.0, inklusive des `.env`-Handgriffs aus B-6 |

### 3.3 Abhängigkeiten: begründet null

E-004 verbietet schwere Fremdbibliotheken. A kommt ohne *jede* neue Abhängigkeit aus:
`urllib.request` fürs Netz (dasselbe, was der Healthcheck im Dockerfile Zeile 21 schon nutzt),
`decimal` fürs Geld, eigener Code für Zeitraffer und Kennzahlen, Inline-SVG für den Chart.
`pytest` bleibt reine Entwicklungsabhängigkeit.

---

## 4. Das Herzstück: Kerzenquelle → Ledger → PortfolioState

### 4.1 Die Datentypen, Feld für Feld

```python
# marketdata.py
@dataclass(frozen=True)
class Candle:
    symbol: str          # "BTCUSDC" — muss risk.SYMBOL_RE erfüllen
    interval: str        # "1m" | "5m" | "15m" | "1h" | "4h" | "1d"
    open_time: int       # ms seit Epoche, UTC, inklusive (Binance-Semantik)
    close_time: int      # ms, inklusive: open_time + interval_ms - 1
    open:  Decimal
    high:  Decimal
    low:   Decimal
    close: Decimal
    volume: Decimal      # in Basis-Asset
    closed: bool         # True nur, wenn die Kerze abgeschlossen ist
```

Offene Kerzen (`closed=False`) werden **nie** an das Ledger gegeben und **nie** gespeichert.
Binance liefert die laufende Kerze als letztes Element; `binance.klines()` verwirft sie anhand
`close_time < server_time`.

```python
# money.py
@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    base: str; quote: str            # "BTC" / "USDC"
    tick_size: Decimal               # 0.01
    step_size: Decimal               # 0.00001 (BTC) | 0.0001 (ETH)
    min_qty: Decimal
    min_notional: Decimal            # 5
    base_precision: int              # 8
    quote_precision: int             # 8

    def effective_min_notional(self, price: Decimal) -> Decimal:
        """min_notional + step_size*price — die Größe, die nach dem Abrunden
        garantiert noch durchgeht (K-3). Für BTCUSDC bei 81287,04: 5,82 USDC."""
```

```python
# ledger.py
@dataclass(frozen=True)
class Order:
    symbol: str
    side: str            # "BUY" | "SELL"
    base_qty: Decimal    # bereits auf step_size abgerundet — sizing.py garantiert das

@dataclass(frozen=True)
class Fill:
    symbol: str; side: str
    price: Decimal       # auf tick_size quantisiert, inkl. Slippage
    qty: Decimal         # auf step_size quantisiert
    gross_quote: Decimal # price * qty, exakt
    fee: Decimal         # in Quote (USDC), auf quote_precision aufgerundet
    net_quote: Decimal   # BUY: gross+fee (Abfluss) | SELL: gross-fee (Zufluss)
    cash_after: Decimal
    candle_open_time: int
    fee_bps: float; slippage_bps: float

@dataclass(frozen=True)
class Rejection:
    code: str            # MIN_NOTIONAL | MIN_QTY | INSUFFICIENT_CASH | NO_POSITION | NO_SPEC | ZERO_QTY
    reason: str          # bei MIN_NOTIONAL mit der effektiven Grenze aus K-3

@dataclass(frozen=True)
class Valuation:
    ts_ms: int
    cash: Decimal
    position_value: Decimal
    equity: Decimal                      # cash + position_value, exakt
    exposure_pct: float
    position_pct_by_symbol: dict[str, float]
```

### 4.2 Die Schnittstelle, Aufruf für Aufruf

```
Quelle          → CandleSource.candles(symbol, interval, start_ms=None, limit=500) -> list[Candle]
                  (aufsteigend nach open_time, nur closed=True, Lücken werden gemeldet)

Entscheidung    → decide_fn(history: Sequence[Candle]) -> Proposal
                  history endet bei Kerze t. decide_fn sieht nie t+1. (→ E-006)

Prüfung         → RiskEngine.check(proposal, portfolio_state, kill_switch) -> RiskDecision
                  portfolio_state kommt aus Ledger.mark() der Kerze t.

Größe           → sizing.size_order(proposal, valuation, spec, ref_price, fee_bps, slippage_bps,
                                    held_qty) -> Order | Rejection

Ausführung      → Ledger.apply(order, candle_next) -> Fill | Rejection
                  Ledger nimmt die **Folgekerze** t+1 und füllt zu deren open. (→ E-006)

Bewertung       → Ledger.mark(marks: Mapping[str, Decimal], ts_ms: int) -> Valuation

Brücke          → Ledger.to_portfolio_state(valuation, start_of_day_equity) -> risk.PortfolioState
```

| Aufruf | Eingabe | Rückgabe | Nebenwirkung |
|---|---|---|---|
| `Ledger.apply(order, candle)` | `Order` (Menge, schon quantisiert) + `Candle` (die Folgekerze) | `Fill` **oder** `Rejection`; niemals beides, niemals None | ändert `self._cash` und `self._positions`; **keine** DB, **kein** Log, **keine** Uhr |
| `Ledger.mark(marks, ts_ms)` | Abbildung Symbol → Decimal-Preis; Zeitstempel als **Parameter** | `Valuation` | keine |
| `Ledger.to_portfolio_state(v, sod)` | `Valuation` + Tagesanfangs-Equity | `risk.PortfolioState` inkl. `position_pct_by_symbol` | keine |

`Ledger.apply()` entnimmt der Kerze **nur `candle.open`** als Referenzpreis und `candle.open_time`
für die Protokollierung. High/Low/Close bleiben ungenutzt — bewusst: jede Nutzung von High oder
Low würde eine Annahme über den intra-Kerzen-Verlauf einführen, die es nicht gibt.

### 4.3 Warum das Ledger keine Uhr kennt

`money.py`, `ledger.py`, `sizing.py`, `benchmark.py` und `marketdata.py` (außer `WallClock`)
rufen **niemals** `time.time()`, `datetime.now()`, `datetime.utcnow()` oder `random.*` auf.
Jeder Zeitstempel ist ein Parameter. Das ist die technische Grundlage dafür, dass derselbe Code
live, aus der DB und im Zeitraffer identische Ergebnisse liefert (E-001) — und es ist
statisch prüfbar (Kriterium A-8b).

### 4.4 Die drei Betriebsarten

| Art | Quelle | Uhr | Kill Switch | Persistenz |
|---|---|---|---|---|
| **live** | `BinanceSource` (Netz) → speichert jede Kerze | `WallClock` | global, in `state`; Freigabe **nur mit Admin-Token** | Kerzen, Fills, Equity-Kurve |
| **db** | `SqliteSource` über einen Zeitraum | `SimClock(candle.close_time)` | lauf-lokal, löst sich an der UTC-Tagesgrenze (E-008) | Fills und Equity-Kurve unter eigener `run_id` |
| **zeitraffer** | `ListSource` (In-Memory) | `SimClock` | wie db | optional, für Teilprojekt C abschaltbar |

Der einzige bewusste Unterschied ist das Verhalten des Kill Switch nach dem Tagesverlustlimit:
live bleibt er, bis ein Mensch ihn mit Token freigibt; im Replay löst er sich an der
UTC-Tagesgrenze, weil ein Backtest sonst nach dem ersten schlechten Tag stillsteht.
Das ist in **E-008** festgehalten und wird von Kriterium A-12b gemessen.

---

## 5. Datenmodell

Neue Migration als **Index 1** der Liste `MIGRATIONS` in `db.py` → Schema-Version **2**.
*Nachtrag Fix-Welle A1:* Dazu kommt **Migration 3** (Index 2) → Schema-Version **3**:
`ALTER TABLE decisions ADD COLUMN pending_ref_price TEXT`. Sie speichert den zum
Vorschlagszeitpunkt gültigen Referenzpreis an der schwebenden Entscheidung, damit
`resolve_pending()` die Order nicht gegen die Füllkerze bemisst (E-001, siehe 9.1).
Bestehende Tabellen bleiben unverändert, `decisions` bekommt drei nullbare Spalten per
`ALTER TABLE ADD COLUMN` (SQLite-tauglich, Bestandszeilen erhalten NULL).

```sql
-- 2 – Marktdaten, Ledger, Benchmark
CREATE TABLE candles (
    symbol     TEXT    NOT NULL,
    interval   TEXT    NOT NULL,
    open_time  INTEGER NOT NULL,          -- ms UTC
    close_time INTEGER NOT NULL,
    open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL,
    volume TEXT NOT NULL,
    source     TEXT    NOT NULL,          -- 'binance' | 'backfill' | 'fixture'
    fetched_at TEXT    NOT NULL,
    PRIMARY KEY (symbol, interval, open_time)
) WITHOUT ROWID;
CREATE INDEX idx_candles_time ON candles(symbol, interval, close_time);

CREATE TABLE runs (
    run_id      TEXT PRIMARY KEY,         -- 'live' | 'replay-<utc>' | 'bench-<run_id>'
    kind        TEXT NOT NULL,            -- live | replay | benchmark
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    code_version TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}' -- Symbole, Intervall, Gebühr, Slippage,
                                           -- Startkapital, Herkunft der SymbolSpecs
);

CREATE TABLE symbol_specs (
    symbol TEXT PRIMARY KEY,
    base TEXT NOT NULL, quote TEXT NOT NULL,
    tick_size TEXT NOT NULL, step_size TEXT NOT NULL,
    min_qty TEXT NOT NULL, min_notional TEXT NOT NULL,
    base_precision INTEGER NOT NULL, quote_precision INTEGER NOT NULL,
    source TEXT NOT NULL,                 -- 'builtin' | 'binance'
    fetched_at TEXT NOT NULL
);

CREATE TABLE fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    decision_id INTEGER REFERENCES decisions(id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,                   -- BUY | SELL
    candle_open_time INTEGER NOT NULL,
    price TEXT NOT NULL, qty TEXT NOT NULL,
    gross_quote TEXT NOT NULL, fee TEXT NOT NULL, net_quote TEXT NOT NULL,
    cash_after TEXT NOT NULL,
    fee_bps REAL NOT NULL, slippage_bps REAL NOT NULL
);
CREATE INDEX idx_fills_run ON fills(run_id, id);

CREATE TABLE positions (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    symbol TEXT NOT NULL,
    qty TEXT NOT NULL,
    avg_price TEXT NOT NULL,
    realized_pnl TEXT NOT NULL DEFAULT '0',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, symbol)
);

CREATE TABLE equity_curve (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    ts_ms INTEGER NOT NULL,
    equity TEXT NOT NULL,
    cash TEXT NOT NULL,
    benchmark_equity TEXT,
    exposure_pct REAL NOT NULL,
    PRIMARY KEY (run_id, ts_ms)
) WITHOUT ROWID;

ALTER TABLE decisions ADD COLUMN run_id TEXT;
ALTER TABLE decisions ADD COLUMN fill_id INTEGER;
ALTER TABLE decisions ADD COLUMN pending_since_ms INTEGER;  -- schwebende Vorschläge, E-006
```

**Geld steht als TEXT in der Datenbank, nie als REAL** (E-007). `store.py` ist die einzige Stelle,
die `Decimal` ↔ TEXT wandelt, über `money.to_text(d, dp)` / `money.from_text(s)`. Kanonische Form:
feste Nachkommastellenzahl (8), damit Gleichheitsvergleiche auf TEXT verlässlich sind.
Auf Geldspalten wird nie sortiert und nie mit SQL gerechnet.

`positions` ist ein materialisierter Schnappschuss; `fills` ist die Wahrheit. Kriterium A-14
misst, dass beide übereinstimmen.

**Aufbewahrung:** `store.prune_candles(symbol, interval, retention_days)` löscht Kerzen, deren
`close_time` älter ist als `neueste_close_time − retention_days`. Vorgabe
`CANDLE_RETENTION_DAYS=400` → bei 15m und 2 Symbolen **76.800 Zeilen** (K-5).

---

## 6. Gebühren, Slippage, Mindestgrößen

### 6.1 Das Füllmodell

Für eine Order auf der Folgekerze mit `p = candle.open`:

```
s = slippage_bps / 10_000            # Vorgabe 5 bp → 0.0005
f = fee_bps      / 10_000            # Vorgabe 10 bp → 0.0010

BUY :  exec_price = tick_up(   p * (1 + s), tick_size )
SELL:  exec_price = tick_down( p * (1 - s), tick_size )

gross = exec_price * qty                       # exakt
fee   = round_up( gross * f, quote_precision ) # immer in USDC, immer gegen den Händler
BUY :  net = gross + fee   → cash -= net
SELL:  net = gross - fee   → cash += net
```

Alle vier Rundungen gehen gegen den Händler: Kaufpreis nach oben, Verkaufspreis nach unten,
Gebühr nach oben, Menge nach unten. Ein Paper-Ledger, das sich verrechnet, soll sich zu seinen
Ungunsten verrechnen.

**Die Gebühr wird immer in der Quote-Währung (USDC) abgerechnet**, nicht wie bei Binance
standardmäßig beim Kauf im Basis-Asset. Das hält die Buchhaltung einwährungsfrei und ist
leicht konservativ. Kein BNB-Rabatt.

### 6.2 Größenbestimmung (`sizing.size_order`)

```
BUY :  target_quote = valuation.equity * position_pct / 100
       exec_price   = tick_up(ref * (1+s))
       raw_qty      = target_quote / (exec_price * (1+f))   # Gebühr aus demselben Topf
       qty          = step_down(raw_qty)
       if qty < min_qty                     -> Rejection(MIN_QTY)
       if qty * exec_price < min_notional   -> Rejection(MIN_NOTIONAL,
                                                "… garantiert ab {effective_min_notional} USDC")
       if qty * exec_price * (1+f) > cash   -> Rejection(INSUFFICIENT_CASH)

SELL:  target_quote = valuation.equity * position_pct / 100
       exec_price   = tick_down(ref * (1-s))
       raw_qty      = min(target_quote / exec_price, held_qty)
       qty          = step_down(raw_qty)
       if held_qty == 0                     -> Rejection(NO_POSITION)
       ... gleiche Grenzen wie oben
```

Dass die Gebühr beim BUY aus demselben Topf kommt, ist der Grund, warum die Kapitalbindung nie
über das von der Risk Engine genehmigte Limit hinausgeht. Ohne diesen Term wäre sie um `f` höher
als genehmigt.

### 6.3 Voreinstellungen und ihre Begründung

| Parameter | Vorgabe | Grenzen | Begründung |
|---|---|---|---|
| `STARTING_BALANCE` | **10000** | 0 < x ≤ 1.000.000 | Öffnet das handelbare Fenster von Faktor 2 auf Faktor 200 (K-1) und drückt den Losgrößenverlust der größten Order auf 0,08 % (K-2) |
| `MARKET_INTERVAL` | **15m** | `1m 5m 15m 1h 4h 1d` | Kompromiss zwischen Wartezeit und Datenmenge: bei 1h wären es bis zu 61 min vom Klick bis zum Fill (E-006), bei 15m höchstens ~16 min. Preis: 35.040 statt 8.760 Kerzen im Jahr (K-5) |
| `FEE_BPS` | **10** (= 0,10 %) | 0 … 100 | Gemessene Binance-Spot-Gebühr VIP 0, Maker wie Taker, ohne BNB-Rabatt |
| `SLIPPAGE_BPS` | **5** (= 0,05 %) | 0 … 200 | **Nicht gemessen, sondern gewählt.** Die gemessenen Halb-Spannen sind 0,062 bp (BTC) und 0,019 bp (ETH); die Tiefe an der Spitze trägt auch die größte Order. 5 bp sind rund 80× die reale Reibung und ein Aufschlag von 50 % auf die dominierende Gebühr. Zweck: verhindern, dass B und C Strategien lernen, die nur in einer reibungsfreien Welt funktionieren |
| `min_notional`, `step_size`, `tick_size` | aus `symbol_specs` | — | Erstbestückung aus einer eingebauten Tabelle mit den in 2.2 gemessenen Werten; `--refresh` holt sie aus `exchangeInfo`. Eingebaut statt immer online, damit Replays reproduzierbar und offline lauffähig bleiben. Die verwendete Quelle steht in `runs.params_json` |
| `FILL_PRICE_RULE` | `next_open` | fest in A | siehe E-006 |

### 6.4 Warum die Buchhaltung *exakt* aufgeht

Der Decimal-Kontext wird auf `prec=34` gesetzt (`money.CONTEXT`). Damit gilt:

- `exec_price` hat höchstens 2 Nachkommastellen und Beträge bis 10⁶ → ≤ 8 signifikante Stellen.
- `qty` hat höchstens 8 Nachkommastellen und Beträge bis 9·10³ → ≤ 12 signifikante Stellen.
- `gross = price * qty` → ≤ 20 signifikante Stellen, **exakt** bei prec 34.
- `fee` wird auf 8 Nachkommastellen aufgerundet, ist also ebenfalls exakt darstellbar.
- `cash ± net` bleibt bei ≤ 8 Nachkommastellen und moderaten Beträgen exakt.
- Die einzige unexakte Operation ist die Division in `size_order` — deren Ergebnis wird
  unmittelbar danach auf `step_size` abgerundet, die Unschärfe wird also absorbiert.

**Folge:** `equity = cash + Σ qty·mark` ist keine Näherung, sondern eine Identität.
Deshalb fordert Kriterium A-1 eine Differenz von **exakt 0** und nicht „unter 0,01 USDC".
Eine Toleranz würde einen echten Buchungsfehler verstecken.

---

## 7. Benchmark BTC Buy & Hold

### 7.1 Berechnung

Der Benchmark ist **kein Sonderfall, sondern eine zweite `Ledger`-Instanz** mit demselben
Startkapital, demselben Gebühren- und Slippage-Modell und derselben Symbol-Spec:

1. Auf der **zweiten** Kerze des Laufs (Fill-Regel `next_open`, also derselbe Zeitpunkt, zu dem
   eine Strategie frühestens hätte kaufen können) wird ein BUY mit `position_pct = 100`
   ausgeführt. Die Losgröße rundet ab, die Restkasse bleibt liegen (K-2).
2. Danach kein weiterer Handel. Bei jeder Bewertung nur `mark()`.
3. `benchmark_equity(t) = qty_btc · close_t + restkasse`.

Damit zahlt der Benchmark **eine** Einstiegsgebühr und **eine** Slippage, die Strategie zahlt sie
bei jedem Roundtrip zweimal. Das ist die ehrliche Messlatte: „Hättest du einfach gekauft und
gehalten."

| Kennzahl | Formel |
|---|---|
| `return_pct` | `(equity_T / equity_0 − 1) · 100` |
| `max_drawdown_pct` | `max über t von (peak_bis_t − equity_t) / peak_bis_t · 100` |
| `alpha_pct` | `portfolio.return_pct − benchmark.return_pct` (Prozentpunkte, nicht Prozent) |
| `hit_rate_pct` | Anteil der geschlossenen Roundtrips mit `realized_pnl > 0` |

### 7.2 Darstellung

- `GET /api/status` bekommt
  `benchmark: {symbol, equity, return_pct, max_drawdown_pct, started_at}` und `alpha_pct`.
- `GET /api/equity-curve?run_id=live&limit=500` liefert
  `{run_id, points: [{ts_ms, equity, benchmark}]}`. Geld als **String** (E-007); das Frontend
  wandelt mit `Number()` nur zur Anzeige.
- Im Dashboard ersetzt ein Inline-SVG mit zwei `<polyline>` den Platzhalter in
  `static/index.html:129` — Portfolio in `--blue`, Benchmark gedämpft und gestrichelt.
  **Zwei Linien, sonst nichts:** keine Achsen, kein Zoom, kein Tooltip. Genau das, was der
  Platzhalter heute verspricht. Keine Bibliothek, keine CDN-Anfrage; die bestehende CSP
  (`default-src 'self'`) bleibt unangetastet.
- Die KPI-Kacheln „Trefferquote" und „Max Drawdown" (heute `—`) werden gefüllt.

---

## 8. Veraltet-Erkennung

### 8.1 Zwei Alterungsbegriffe, ein Auslöser

| Begriff | Definition | Kann den Kill Switch auslösen? |
|---|---|---|
| **Datenalter** | `clock.now_ms() − neueste_gespeicherte_candle.close_time` | **ja** |
| **Uhrversatz** | `lokale_zeit_ms − binance_server_time_ms`, alle 15 min über `GET /api/v3/time` (Gewicht 1) | **ja** |
| **Abrufalter** | `clock.now_ms() − letzter_erfolgreicher_abruf_ms` | nein, nur `warn` |

Das Abrufalter löst bewusst **nicht** aus: Wenn Abrufe scheitern, altern die Daten ohnehin —
ein zweiter Kill-Pfad mit eigener Uhr würde denselben Sachverhalt doppelt messen und mit dem
Backoff aus 8.3 kollidieren (der legitim 600 s erreicht). Es bleibt als Frühwarnung sichtbar.

### 8.2 Die Schwellen sind intervallrelativ

Eine feste 300-s-Schwelle würde bei jedem Intervall oberhalb von 1m im Normalbetrieb
dauernd auslösen, weil die neueste geschlossene Kerze regelmäßig fast ein ganzes Intervall
alt ist.

```
warn_s = max(MARKET_STALE_WARN_S, ceil(1.5 * interval_s))
kill_s = max(MARKET_STALE_KILL_S, 3   * interval_s)
```

| Intervall | `warn_s` | `kill_s` | entspricht |
|---|---|---|---|
| `1m` (60 s) | 150 (Untergrenze greift) | 300 (Untergrenze greift) | 2,5 / 5 Kerzen |
| **`15m` (900 s)** | **1.350** (22,5 min) | **2.700** (45 min) | 1,5 / 3 Kerzen |
| `1h` (3.600 s) | 5.400 (90 min) | 10.800 (3 h) | 1,5 / 3 Kerzen |

| Zustand | Bedingung | Reaktion |
|---|---|---|
| `ok` | Datenalter ≤ `warn_s` und \|Uhrversatz\| ≤ 5 s und Abrufalter ≤ 5·`MARKET_POLL_S` | nichts |
| `warn` | einer darüber, keiner über der Kill-Schwelle | `/api/health` → `market_data: "warn"`, HTTP bleibt 200; Event `MARKET_DATA_LAGGING` (höchstens einmal pro 15 min) |
| `stale` | Datenalter > `kill_s` **oder** \|Uhrversatz\| > 30 s | **Kill Switch an**, Event `MARKET_DATA_STALE` (WARN), `/api/health` → `market_data: "stale"` und HTTP **503**, `/api/status` → `risk: "BLOCKED"` |

Der Uhrversatz bleibt absolut (5 s / 30 s) — er hat nichts mit dem Kerzenintervall zu tun.
Er gehört dazu, weil das Datenalter sonst nur so gut ist wie die Containeruhr: Ein LXC mit zehn
Minuten Versatz meldet frische Daten als uralt oder uralte als frisch.

**Freigabe: nur mit Admin-Token.** Der Kill Switch löst sich **nicht** von selbst, wenn die Daten
zurückkehren. Automatisch wieder anzulaufen, weil sich die Datenlage scheinbar erholt hat, ist
genau der Fall, für den es den Kill Switch gibt.

**Im Replay:** Die Uhr ist `SimClock`, deren `now_ms()` die `close_time` der gerade verarbeiteten
Kerze ist. Das Datenalter ist damit immer 0, und die Veraltet-Erkennung ist im Zeitraffer
strukturell inert — nicht abgeschaltet, sondern bedeutungslos. Kriterium A-12 misst das.

### 8.3 Netzausfall und Rücknahme

`poller.py` fängt jede Ausnahme aus `binance.py`, protokolliert ein Event
`MARKET_DATA_FETCH_FAILED` mit dem Ausnahmetyp (nie mit URL oder Antwortkörper) und wartet nach
Fehlern mit exponentiellem Backoff: 60 s → 120 s → 240 s → 600 s (Deckel). Bei HTTP 429/418 wird
`Retry-After` respektiert, mindestens aber 60 s gewartet. Der Thread stirbt nie an einer
Ausnahme — ein toter Poller wäre für die Veraltet-Erkennung unsichtbar, ein schlafender nicht.
`/api/health` prüft zusätzlich `thread.is_alive()` und meldet sonst `paper_engine: "error"`.

---

## 9. Datenfluss

### 9.1 Live

```
poller-Thread (alle MARKET_POLL_S = 60 s)
  └─ für jedes Symbol: binance.klines(symbol, "15m", limit=2)     Gewicht 2
     └─ verwirft die offene Kerze
     └─ store.upsert_candles(...)                     PK (symbol,interval,open_time), idempotent
  └─ marketdata.staleness(clock, letzte_kerze, letzter_abruf, versatz, interval_s)
     └─ stale?  -> state.kill_switch='1' + Event MARKET_DATA_STALE
  └─ execute.resolve_pending(neue_kerzen)             ← schwebende Vorschläge füllen (E-006)
  └─ ledger.mark({sym: candle.close}, ts_ms=candle.close_time)
     └─ Tageswechsel (UTC)?  -> state.sod_equity := equity, state.sod_date := heute
  └─ benchmark.mark(...)
  └─ store.append_equity_point(run_id='live', ...)    ← genau ein Punkt je neuer Kerze

HTTP POST /api/risk/check   (Admin-Token, wie bisher)
  └─ execute.execute_proposal(proposal, ctx)
     ├─ RiskEngine.check(proposal, portfolio_state, kill_switch)   ← Nadelöhr (E-003)
     │    abgelehnt -> decisions-Zeile, 0 Zeilen in fills, fertig
     └─ genehmigt  -> decisions-Zeile mit pending_since_ms, Antwort status="pending_fill"
                      (der Fill folgt im nächsten Poll-Zyklus mit der dann vorliegenden Kerze)
```

**Die Live-Verzögerung mit 15m-Kerzen: bis zu ~16 Minuten** (bis zu 15 min bis zur nächsten
Kerze plus bis zu 60 s Poll-Abstand). Das ist der Preis für E-006 und E-001, und er wird
nicht versteckt:

- Die Antwort von `/api/risk/check` enthält `status: "pending_fill"` und
  `expected_fill_after_ms` (der `open_time` der erwarteten Kerze).
- Das Dashboard zeigt schwebende Vorschläge als eigene Zeile im Decision Journal mit
  „schwebend seit …, Fill erwartet ab …".
- Ein schwebender Vorschlag **verfällt nach 30 Minuten** (`2 × interval_s = 1.800 s`) ohne
  passende Kerze, mit `risk_code = "PENDING_EXPIRED"`, damit nichts unbemerkt liegen bleibt.
- Wer beim Ausprobieren nicht warten will, setzt `MARKET_INTERVAL=1m` — dann sind es ≤ 2 min.

### 9.2 Replay

```
replay.run_replay(candles, decide_fn, ctx)
  für jede Kerze t (ab index 1):
    clock.set(candles[t-1].close_time)
    if utc_date(clock) != kill_switch_tag:  kill_switch_lokal = False   # E-008
    v   = ledger.mark({sym: candles[t-1].close}, ts_ms=clock.now_ms())
    pf  = ledger.to_portfolio_state(v, sod_equity)
    p   = decide_fn(candles[:t])                   # sieht nie candles[t]
    d   = risk.check(p, pf, kill_switch_lokal)
    if d.approved and p.action != "WAIT":
        o = sizing.size_order(p, v, spec, candles[t].open, ...)
        if isinstance(o, Order): f = ledger.apply(o, candles[t])
    bench.mark(...)
    equity_punkt anhängen
```

Identische Reihenfolge wie live, identische Funktionen. Der einzige Unterschied ist die Uhr,
die Quelle und der lauf-lokale Kill Switch (E-008).

**Auslösung nur per CLI**, im Container:
`docker compose exec ai-trade-lab python -m aitra.replay --symbol BTCUSDC --interval 15m --from 2025-01-01 --to 2026-01-01`

---

## 10. Fehlerbehandlung

| Fall | Verhalten |
|---|---|
| Binance antwortet nicht / Timeout | Event `MARKET_DATA_FETCH_FAILED`, Backoff, nach `kill_s` (bei 15m: 2.700 s) Datenalter → Kill Switch |
| HTTP 429 / 418 | `Retry-After` beachten, Event `MARKET_DATA_RATE_LIMITED`, Backoff |
| Antwort ist kein JSON, falsche Struktur, nichtnumerische Felder | Antwort verwerfen, Event `MARKET_DATA_MALFORMED` mit Ausnahmetyp; **nie** der Rohtext ins Log |
| Antwort größer als `MAX_RESPONSE_BYTES` (2 MiB) | Verbindung abbrechen, Event, verwerfen |
| Kerzenlücke (`open_time` springt um mehr als ein Intervall) | Kerzen trotzdem speichern, Event `CANDLE_GAP` mit Anzahl fehlender Intervalle; Ledger und Replay arbeiten weiter |
| Kerze mit `high < low` oder `close ≤ 0` | verwerfen, Event `MARKET_DATA_MALFORMED` |
| Order unter `min_notional`/`min_qty` | `Rejection` mit der effektiven Grenze aus K-3 in der Begründung; Zeile in `decisions`, **keine** Zeile in `fills` |
| Kasse reicht nicht | `Rejection(INSUFFICIENT_CASH)`; die Kasse kann niemals negativ werden (A-2) |
| Kein `SymbolSpec` für das Symbol | `Rejection(NO_SPEC)` — lieber keine Order als eine mit geratener Losgröße |
| Schwebender Vorschlag ohne passende Kerze | nach 30 min verfallen, `risk_code = "PENDING_EXPIRED"` |
| Startkapital zu klein für ein sinnvolles Handelsfenster | Startereignis `NARROW_TRADING_WINDOW` (WARN) mit den konkreten Grenzen (A-22) |
| SQLite `database is locked` | `timeout=10` ist gesetzt; darüber hinaus ein Wiederholungsversuch, dann Event `DB_BUSY` |
| Poller-Thread wirft | gefangen, protokolliert, Schleife läuft weiter; stirbt er doch, meldet `/api/health` `paper_engine: "error"` |

---

## 11. Sicherheit

### 11.1 Neue Angriffsfläche: ausgehendes Netz

Bis v0.2.1 hat der Container **keine** ausgehenden Verbindungen aufgebaut. Mit A gibt es HTTPS
nach `api.binance.com`. → Befund **B-2**, das README muss das sagen (Kriterium A-21).

| Maßnahme | Konkret |
|---|---|
| Host-Allowlist | nur `api.binance.com` und `data-api.binance.vision`, geprüft auf **exakte Gleichheit des Hostnamens**, nicht per Präfix; alles andere → `ConfigError` **beim Start** |
| Nur HTTPS | `http://` wird abgelehnt; Zertifikatsprüfung über den System-Truststore des Slim-Images, nie abgeschaltet |
| Keine Weiterleitungen | eigener `HTTPRedirectHandler`, der jede 3xx zum Fehler macht — verhindert Umleitung auf `169.254.169.254` oder `127.0.0.1` |
| Timeouts | 10 s Verbindung, 10 s Lesen |
| Größenlimit | höchstens 2 MiB gelesen, dann Abbruch |
| Keine Ausgangsdaten | gesendet werden ausschließlich `symbol`, `interval`, `limit`, `startTime`, `endTime`. Kein Token, kein Hostname, keine Kennung im User-Agent. Es gibt keine API-Schlüssel im Projekt, also kann auch keiner abfließen |
| Kein Log von Antwortkörpern | Events enthalten nur Ausnahmetyp und HTTP-Status |

### 11.2 Eingaben

- Symbolparameter aller neuen Endpunkte werden gegen `risk.SYMBOL_RE` geprüft, bevor sie in eine
  SQL-Abfrage gehen; alle Abfragen sind parametrisiert (wie im Bestand).
- `interval` gegen eine feste Menge geprüft (`1m 5m 15m 1h 4h 1d`).
- `limit` wie im Bestand über `min(max(...), 500)` gedeckelt.
- JSON von Binance: jedes Feld typgeprüft, Zahlen nur über `Decimal(str)` — nie über `float`.
- **Kein HTTP-Endpunkt startet einen Replay.** Das Dashboard hat keinen Login; ein Endpunkt, der
  einen Jahreslauf über 35.040 Kerzen anstößt, wäre dort ein DoS-Hebel für jeden im Heimnetz.

### 11.3 Ressourcen und Rechte

- Schreibzugriff nur nach `/app/data` (Volume). `PRAGMA temp_store=MEMORY` wird gesetzt, damit
  SQLite bei größeren Sortierungen keine Temp-Datei außerhalb von `data/` anlegt — das
  Dateisystem ist `read_only: true`.
- Der Poller-Thread läuft im gunicorn-Worker (uid 10001), ohne neue Capabilities.
- Der Thread startet **nur** bei `MARKET_DATA_ENABLED=true`. Vorgabe `false`, damit
  `create_app()` in den Tests (die es mehrfach aufrufen, `test_api.py:47-50`) niemals
  Netzverkehr auslöst.
- **Ratenbudget:** 2 Symbole, Poll 60 s → 2 `klines`-Aufrufe/min × Gewicht 2 = 4/min, plus
  `time` alle 15 min = 0,07/min. Summe **4,07 Gewicht/min von 6.000 = 0,068 %.**
  Vom Kerzenintervall unabhängig — es bestimmt nur, wie oft ein Abruf etwas Neues bringt.
  Kriterium A-17c deckelt bei 10/min.
- Plattenwachstum: Kriterium A-16b misst Bytes pro Kerzenzeile; Aufbewahrung nach
  `CANDLE_RETENTION_DAYS` begrenzt (A-16).

### 11.4 Was sich *nicht* ändert

Kill Switch, Admin-Token, CSP, Security-Header, Nicht-root, read-only, `cap_drop: ALL`,
Live-Sperre (`config.live_locked`). A fügt keine Ausnahme hinzu.

---

## 12. Abnahmekriterien

Jedes Kriterium nennt eine Messung, eine Schwelle, den Befehl und den eingebauten Fehler,
der es rot machen muss. Alle Befehle laufen aus `app/`.

> **Zwei Schwellen sind gegenüber dem ursprünglichen Auftrag verschärft** und übernommen:
> A-1 (exakt 0 statt < 0,01 USDC) und A-9 (Schwelle aus der Entscheidungsrate abgeleitet
> statt aus einer Gesamtzeit).

### Buchhaltung

**A-1 · Die Identität geht exakt auf.**
10.000 Fills aus einer festen, im Test hinterlegten Sequenz (kein Zufall) auf BTCUSDC und ETHUSDC.
Nach jedem Fill gilt `ledger.mark(marks).equity − (cash + Σ qty·mark) == Decimal("0")`;
am Ende zusätzlich `cash_end − (cash_start − Σ net(BUY) + Σ net(SELL)) == Decimal("0")`.
**Schwelle: Differenz exakt 0 in 10.000 von 10.000 Fällen.**
*Warum nicht 0,01 USDC:* Abschnitt 6.4 zeigt, dass jede Operation bei `prec=34` exakt ist.
Eine Toleranz von 0,01 würde bei 10.000 Fills einen systematischen Gebührenfehler von bis zu
100 USDC durchlassen.
`python -m pytest -q tests/test_ledger.py::test_buchhaltung_identitaet_a1`
*Rot:* Die Gebühr beim Kassenabzug weglassen (nur im `Fill`, nicht in `cash`) → Differenz ≈ 2.000 USDC.

**A-2 · Kasse und Mengen bleiben nichtnegativ.**
In denselben 10.000 Fills: `min(cash) ≥ 0` und `min(qty je Position) ≥ 0`, 0 Verletzungen.
*Rot:* ~~Die `INSUFFICIENT_CASH`-Prüfung in `sizing.py` entfernen → `cash < 0` tritt auf.~~

> **Korrektur Fix-Welle A1 — der hier genannte Rot-Nachweis ist falsch.** Es gibt **zwei**
> Kassenwächter, nicht einen: `sizing.py` deckelt die Ordergröße, und `Ledger.apply()` prüft
> vor der Buchung ein zweites Mal (`if net > self._cash`). Nachgemessen im Container:
> nur den Wächter in `sizing.py` entfernt → **beide** A-2-Tests bleiben grün (der Ledger fängt
> es ab). Erst wenn **beide** entfernt sind, entsteht `cash < 0`:
> `A-2 verletzt: Kasse -482.74576514 < 0 bei Schritt 55 (BUY ETHUSDC, OK)`.
> Das ist kein Mangel, sondern die beabsichtigte zweite Schicht — der Spec-Text beschrieb nur
> eine davon.
>
> **Zweite Korrektur: wo A-2 tatsächlich gemessen wird.** Der 10.000-Fill-Treiber in
> `test_ledger.py` rechnet die Menge selbst aus und ruft `Ledger.apply()` direkt; `sizing.py`
> liegt dort gar nicht im Pfad. Er deckelt zudem den Kauf selbst auf 95 % der Kasse und den
> Verkauf auf `pos.qty` — eine negative Kasse oder Menge ist dort **strukturell unmöglich**,
> unabhängig vom Produktivcode. Nachgemessen: beide Kassenwächter entfernt → `1 passed`;
> Bestandswächter (`qty > pos.qty`) entfernt → `1 passed`. Die Zeilen bleiben dort stehen
> (die Spec verlangt die Prüfung in denselben 10.000 Fills), die **belastbare** A-2-Messung ist
> aber `tests/test_execute.py::test_a2_kasse_und_mengen_nichtnegativ_ueber_execute_proposal`:
> 400 Vorschläge über `RiskEngine.check()` → `size_order()` → `Ledger.apply()`, geprüft nach
> jedem Aufruf. Dort müssen außerdem die übrigen Risikoschranken aufgezogen sein
> (`max_total_exposure_pct = 10_000`), sonst hält schon `MAX_EXPOSURE` die Kasse über null und
> der Test misst den Kassenwächter gar nicht.
>
> Abnahmebefehle:
> `python -m pytest -q tests/test_execute.py::test_a2_kasse_und_mengen_nichtnegativ_ueber_execute_proposal`
> `python -m pytest -q tests/test_ledger.py::test_buchhaltung_identitaet_a1`

**A-3 · Quantisierung, 1.000 Stichproben.**
Für 1.000 Ordergrößen (fest hinterlegt, von 6 bis 1.000 USDC über drei Größenordnungen) gilt:
`qty % step_size == 0` in 1000/1000, `price % tick_size == 0` in 1000/1000,
`gefüllte Notional ≤ angeforderte Notional` in 1000/1000.
*Rot:* `ROUND_HALF_UP` statt `ROUND_FLOOR` bei der Menge → rund 500 Verletzungen der dritten Bedingung.

**A-4 · Mindestordergröße, an der Grenze gemessen.**
Order mit Notional **4,99 USDC** → `Rejection(MIN_NOTIONAL)`; Order mit **5,00 USDC** und einem
Preis, bei dem die Abrundung exakt aufgeht → `Fill`. Zwei Messpunkte.
*Rot:* `min_notional` auf 0 setzen → der 4,99-Fall wird gefüllt.

**A-4b · Die effektive Mindestgröße aus K-3 ist real und steht in der Meldung.**
Für 500 Preise im Bereich 60.000 … 120.000 USDC gilt: eine Zielgröße von
`min_notional + step·preis` (BTCUSDC: 5,82 USDC bei 81.287) wird in **500/500** Fällen gefüllt,
eine Zielgröße von `min_notional + 0,5·step·preis` wird in **mindestens 200/500** Fällen
abgelehnt (das Kriterium greift also wirklich und ist nicht stumpf).
Die Begründung jeder `MIN_NOTIONAL`-Ablehnung enthält den effektiven Wert:
`grep -c "garantiert ab" <ablehnungstexte>` == Anzahl der Ablehnungen.
*Rot:* `effective_min_notional()` als `min_notional` zurückgeben → die erste Zahl fällt unter 500.

**A-5 · Der Losgrößenverlust ist beziffert, nicht behauptet.**
Bei `step = 0.00001` und 1.000 Preisstufen um 81.287 USDC liegt der Rundungsrest einer
1.000-USDC-Order zwischen 0 und `step·preis = 0,8129 USDC`. Der Test prüft
`max(rest) < 0.8129` **und** `max(rest) > 0.70` — die Grenze wird also ausgereizt —
sowie `max(rest)/1000 < 0.001` (= 0,1 %, der Wert aus K-2).
*Rot:* Den Rest der Kasse gutschreiben *und* die Menge aufrunden → `rest < 0` tritt auf.

### Das Nadelöhr

**A-6 · Abgelehnte Vorschläge erzeugen null Fills.**
`execute_proposal()` mit einem Vorschlag, den die Risk Engine ablehnt (`position_pct=15` bei
Limit 10), erzeugt **1** Zeile in `decisions` und **0** Zeilen in `fills`. Für alle 9
Ablehnungscodes aus `risk.py` je ein Fall: 9/9 mit 0 Fills.
*Rot:* In `execute.py` `Ledger.apply()` vor die Risk-Prüfung ziehen → 9 Fills.

**A-6b · Es gibt genau einen Aufrufer.**
`grep -rn "\.apply(" app/aitra --include=*.py | grep -v "^app/aitra/execute.py"` liefert
**0 Zeilen**.
*Rot:* Einen zweiten Aufruf in `poller.py` einbauen → 1 Zeile.

**A-6c · Befund B-1 ist behoben und bleibt behoben.**
Portfolio mit **0 BTC** und **20 % Exposure in ETH**. Ein `SELL BTCUSDC` über 5 % muss
`NO_POSITION` ergeben.
Gegenprobe, damit das Kriterium nicht durch pauschales Ablehnen erfüllt wird:
Portfolio mit **8 % BTC** und 20 % ETH, `SELL BTCUSDC` über 5 % → **genehmigt**;
`SELL BTCUSDC` über 12 % → `NO_POSITION`. **Drei Messpunkte.**
Und: `test_no_short` aus dem Bestand bleibt grün (flaches Portfolio, SELL 5 % → `NO_POSITION`).
*Rot:* Die alte Zeile `p.position_pct > pf.exposure_pct` wiederherstellen → der erste
Messpunkt wird genehmigt, obwohl keine BTC-Position existiert.
*Anmerkung:* Dieses Kriterium ist heute, vor der Korrektur, **rot** — und soll es sein.
Es benennt echte Schuld im Bestand.

### Kein Blick in die Zukunft

**A-7 · Die Entscheidung sieht die Fill-Kerze nicht.**
Replay über **8.640 Kerzen** (90 Tage 15m). Für jeden Aufruf von `decide_fn` gilt:
`history[-1].open_time == aktuelle_kerze.open_time − 900_000 ms`, in **8640/8640** Aufrufen.
Für jeden Fill gilt `fill.candle_open_time == entscheidungskerze.open_time + 900_000`,
in 100 % der Fälle.
*Rot:* `decide_fn(candles[:t+1])` statt `[:t]` → 0/8640.

**A-7b · Auch live wird auf der Folgekerze gefüllt, und Schwebendes verfällt.**
Ein `execute_proposal()` im Live-Pfad ohne vorliegende Folgekerze liefert
`status == "pending_fill"` und erzeugt **0** Zeilen in `fills`. Nach Einspeisen der Folgekerze
über `resolve_pending()` entsteht **genau 1** Fill mit `candle_open_time == folgekerze.open_time`.
Mit einer `SimClock`, die **1.801 s** (30 min + 1 s) vorspringt, ohne dass eine Kerze kommt:
**0** Fills und `risk_code == "PENDING_EXPIRED"`; bei **1.799 s**: der Vorschlag schwebt noch.
**Vier Messpunkte.**
*Rot:* Im Live-Pfad zum letzten bekannten Close füllen → sofort 1 Fill, `status` ist nicht
`pending_fill`. Zweiter Rot-Nachweis: Verfall weglassen → der 1.801-s-Fall schwebt weiter.

### Die drei Betriebsarten sind eine (der Beweis für E-001)

**A-8 · Identische Fill-Listen aus drei Quellen.**
Dieselben 500 Kerzen, dieselbe Test-Strategie, dreimal: aus `ListSource` (Speicher),
aus `SqliteSource` (DB), aus einem `FakeBinanceSource` über den Live-Pfad (Poller-Schritt ohne
Thread, inklusive `resolve_pending`). Die drei Fill-Listen sind feldweise identisch:
**0 Abweichungen bei 500 Kerzen × 12 Feldern**, geprüft über SHA-256 der serialisierten Listen —
**drei identische Hashes**.
*Rot:* Im Live-Pfad das Vorzeichen der Slippage drehen → 2 von 3 Hashes weichen ab.

**A-8b · Keine versteckte Uhr, kein versteckter Zufall.**
`grep -rnE "time\.time|datetime\.(now|utcnow)|random\." app/aitra/money.py app/aitra/ledger.py app/aitra/sizing.py app/aitra/benchmark.py`
liefert **0 Treffer**. In `marketdata.py` ist höchstens **1** Treffer erlaubt, und er muss
zwischen `class WallClock` und der nächsten Top-Level-Definition stehen.
*Rot:* Ein `datetime.now()` in `ledger.py` einbauen → 1 Treffer.

**A-8c · Zweimal derselbe Lauf, derselbe Hash.**
Zwei Läufe desselben Replays in einem Prozess und zwei in getrennten Prozessen
(`PYTHONHASHSEED` variiert) ergeben **4 identische SHA-256** über die Fill-Liste.
*Rot:* `run_id` mit Zeitstempel in den Hash aufnehmen → 4 verschiedene.

### Tempo und Speicher

**A-9 · Ein Jahr 15m-Kerzen im Zeitraffer.**
Replay über **35.040 Kerzen** (1 Jahr 15m, K-5) mit Entscheidung, Risk-Prüfung, Sizing,
Fill-Versuch und Bewertung auf jeder Kerze, In-Memory-Ledger, ohne DB-Schreibzugriff.

Die Schwelle wird aus der **Entscheidungsrate** abgeleitet, nicht aus einer Gesamtzeit:

| Umgebung | geforderte Rate | daraus die Schwelle |
|---|---|---|
| Entwicklungsrechner | ≥ **2.920** Entscheidungen/s | 35.040 / 2.920 = **< 12,0 s** |
| Container (`docker compose exec`) | ≥ **876** Entscheidungen/s | 35.040 / 876 = **< 40,0 s** |

*Warum diese Raten:* Teilprojekt C braucht 10⁴–10⁵ Entscheidungen je Trainingsepisode.
876/s ist die Untergrenze, unterhalb derer Training unbrauchbar wird.
*Ehrliche Folge der 15m-Entscheidung:* Eine Episode über ein **volles Jahr** kostet bei
876/s jetzt **40 s** statt 10 s. C wird deshalb entweder mit Quartalsepisoden arbeiten
(8.640 Kerzen ≈ 10 s bei der Untergrenze) oder mehr Tempo brauchen als das Minimum. Das ist
kein Mangel dieser Spec, sondern der Preis des kürzeren Intervalls — und er gehört in die
Planung von C, nicht in eine Fußnote.
*Kalibrierung:* Erwartet werden 0,8–4,0 s auf dem Entwicklungsrechner (hochgerechnet aus
~0,2–1,0 s für 8.760 Kerzen). Die Erstmessung kommt nach `docs/abnahme/`; liegt sie unter
4,0 s, wird die Schwelle in der Folgefassung auf das Dreifache der Messung gesenkt — ein
Wächter mit zehnfacher Luft ist keiner.
*Betriebshinweis:* Der 40-s-Containerlauf wird als `@pytest.mark.slow` geführt, damit die
Suite in `build.sh` (die auf dem Mac läuft, Budget 12 s) nicht ausgebremst wird.
*Rot:* `time.sleep(0.002)` je Kerze → 70 s.

**A-10 · Speicher im Zeitraffer.**
Derselbe Lauf unter `tracemalloc`: Spitzenverbrauch **< 120 MB**.
Erwartet werden 32–48 MB (35.040 Kerzen × ~900 B plus Fills und Bewertungen), die Schwelle
lässt Faktor 2,5–3,75.
*Hinweis zur Fassung 2:* Dort stand < 40 MB, gerechnet gegen 8.760 Kerzen. Mit 35.040 Kerzen
wäre diese Schwelle **unerfüllbar** gewesen — ein Kriterium, das man später hätte aufweichen
müssen, und damit eines, dem man danach nicht mehr glaubt.
Die Schwelle ist maschinenunabhängig und hängt nicht an F-1.
*Rot:* Alle Zwischen-`Valuation`-Objekte in einer Liste halten → > 200 MB.

**A-10b · Speicher im Dauerbetrieb** *(Zahl aus F-1 einsetzen)*.
RSS des gunicorn-Workers nach 24 h Live-Betrieb mit 2 Symbolen:
**< 25 % des Container-RAM**. Einzusetzen, sobald F-1 beantwortet ist:
bei 2.048 MB → **< 512 MB**, bei 8.192 MB → **< 2.048 MB**.
Der Poller hält nur die jeweils neuesten Kerzen, nicht die Historie — die Zahl ist vom
Intervall unabhängig.
Messung: `docker stats --no-stream --format '{{.MemUsage}}' ai-trade-lab`.
*Rot:* Alle geholten Kerzen zusätzlich in einer Modulvariablen sammeln → Wachstum über 24 h sichtbar.

### Veraltet-Erkennung

**A-11 · Die Schwelle wird an beiden Seiten gemessen, für beide betriebsrelevanten Intervalle.**
Mit `SimClock` und einer neuesten Kerze definierten Alters:

| Intervall | Alter | erwartet |
|---|---|---|
| `1m` | 149 s | `ok`, HTTP 200, `kill_switch == "0"` |
| `1m` | 299 s | `warn`, HTTP 200, `kill_switch == "0"` |
| `1m` | 301 s | `stale`, HTTP **503**, `kill_switch == "1"`, genau 1 Event `MARKET_DATA_STALE` |
| **`15m`** | **1.349 s** | `ok` |
| **`15m`** | **2.699 s** | `warn` |
| **`15m`** | **2.701 s** | `stale`, `kill_switch == "1"` |

**Sechs Messpunkte.** Der 15m-Teil ist der eigentliche Wächter: Ohne die intervallrelative
Schwelle aus 8.2 würde der Kill Switch im Normalbetrieb dauernd auslösen, weil die neueste
geschlossene Kerze regelmäßig bis zu 900 s alt ist.
*Rot:* Das `max(..., 3·interval_s)` entfernen → die drei 15m-Messpunkte kippen alle auf `stale`.

**A-11b · Uhrversatz wird erkannt.**
Fake-`server_time` mit 31 s Abweichung → `stale`; mit 29 s → `warn`; mit 4 s → `ok`;
mit **−31 s** → `stale` (Betrag, nicht Vorzeichen). **Vier Messpunkte.**
*Rot:* Betragsfunktion weglassen → der negative Fall bleibt `ok`.

**A-12 · Der Zeitraffer löst nie Veraltet aus.**
Replay über 35.040 Kerzen aus dem Kalenderjahr 2024 (über ein Jahr alt):
**0** Events `MARKET_DATA_STALE`, `kill_switch` bleibt `"0"`.
*Rot:* In `replay.py` die `SimClock` durch `WallClock` ersetzen → Kill beim ersten Schritt.
Dieses Kriterium ist der Beweis, dass die Uhr wirklich injiziert ist und nicht nur „meistens"
gesetzt wird.

**A-12b · Tagesverlustlimit im Replay blockiert einen Tag, nicht den Lauf (E-008).**
Replay über 5 Tage (480 Kerzen bei 15m), konstruiert so, dass die Equity an Tag 1 um 3 % fällt
(Limit 2 %): genau **1** Event `KILL_SWITCH_ENGAGED`, **0** Fills nach dem Auslösen an Tag 1,
**≥ 1** Fill an Tag 2.
Gegenprobe live: derselbe Verlauf über den Live-Pfad → der Kill Switch ist auch an Tag 2 noch
aktiv, `0` Fills an den Tagen 2–5, bis ein Aufruf mit Admin-Token ihn löst.
*Rot:* Das Zurücksetzen an der Tagesgrenze auch im Live-Pfad aktivieren → die Gegenprobe
erzeugt Fills an Tag 2.

### Persistenz

**A-13 · Geld steht als Text in der Datenbank.**
Nach einem Replay mit 1.000 Fills liefert
`SELECT DISTINCT typeof(price), typeof(qty), typeof(fee), typeof(cash_after) FROM fills`
genau **eine** Zeile mit viermal `'text'`; `SELECT count(*) FROM fills WHERE typeof(price)!='text'`
ist **0**.
*Rot:* Spalte als `REAL` deklarieren (SQLite wandelt den Text dann still um) → `'real'`.

**A-14 · Rekonstruktion aus dem Journal.**
Aus den 1.000 `fills`-Zeilen allein (ohne Kerzen) werden Kasse und Positionsbestand nachgerechnet.
Differenz zum gespeicherten `positions`-Schnappschuss und zur `cash_after` der letzten Zeile:
**exakt `Decimal("0")` bei 1.000/1.000 Zeilen**.
*Rot:* Beim `positions`-Upsert den Durchschnittspreis nicht mengengewichtet fortschreiben →
Differenz beim ersten Nachkauf.

**A-15 · Migration verliert nichts.**
Eine DB auf `schema_version = 1` mit 3 Zeilen in `decisions` wird auf die aktuelle Version gehoben.
Danach: `schema_version == 3` *(Fix-Welle A1: war 2, bis Migration 3 dazukam)*,
`count(decisions) == 3`, und ein SHA-256 über die sortierten
Zeileninhalte (ohne die drei neuen Spalten) ist **identisch** zu vorher.
*Rot:* `DROP TABLE decisions` in der Migration → 0 Zeilen.

**A-16 · Aufbewahrung, exakt abgezählt.**
500 Tageskerzen (Tag 0 … 499) einfügen, `prune_candles(retention_days=400)`:
danach **genau 400** Zeilen, die älteste bei Tag **100**, **100** gelöscht.
*Rot:* `<` statt `<=` in der Löschbedingung → 401 oder 399 Zeilen.
*Dies ist der Wächter, der im Alltag arbeitet* — anders als A-16b.

**A-16b · Platzbedarf pro Kerze** *(zweite Zahl aus F-1 einsetzen)*.
10.000 15m-Kerzen in eine frische DB schreiben, `VACUUM`, Dateigröße messen:
**≤ 250 Bytes pro Zeile**. Diese Schwelle ist maschinenunabhängig.
Hochrechnung auf die Betriebskonfiguration (2 Symbole × 15m × 400 Tage = **76.800 Zeilen**):
**≤ 19,2 MB**, zu notieren in `docs/abnahme/` und zu prüfen gegen **10 % der Container-Disk** —
einzusetzen, sobald F-1 beantwortet ist: bei 16 GB → 1,6 GB, bei 32 GB → 3,2 GB.
*Ehrlich dazugesagt:* Mit 15m wird diese Schwelle um **Faktor 83** (bei 16 GB) bzw. **167**
(bei 32 GB) unterschritten. In Fassung 2 stand hier Faktor 300 — das galt für 1h und ist mit
dem Wechsel auf 15m falsch geworden. Der Wächter greift erst bei einer Umstellung auf 1m
(2 × 1m × 400 Tage = 1.152.000 Zeilen ≈ 288 MB, dann nur noch Faktor 5,6 bei 16 GB) oder bei
deutlich längerer Aufbewahrung. Genau dafür ist er da.
*Rot:* Kerzen als JSON-Blob in einer Spalte speichern → > 400 B/Zeile.

### Netz und Härtung

**A-17 · Die Allowlist greift.**
`config.load()` mit `BINANCE_BASE_URL=http://169.254.169.254` → `ConfigError`;
`https://evil.example.com` → `ConfigError`;
`http://api.binance.com` (kein TLS) → `ConfigError`;
`https://api.binance.com.evil.example` → `ConfigError` (Präfix-Falle);
`https://api.binance.com` → akzeptiert.
**Fünf Messpunkte.**
*Rot:* Allowlist per `startswith` statt Hostvergleich → die Präfix-Falle wird akzeptiert.

**A-17b · Keine Weiterleitung wird verfolgt.**
Ein lokaler Test-Server antwortet mit `302` auf `http://127.0.0.1:1/`. Der Client wirft eine
Ausnahme; der Zähler des Umleitungsziels bleibt bei **0 Anfragen**.
*Rot:* Den Standard-`HTTPRedirectHandler` aktiv lassen → 1 Anfrage auf das Ziel.

**A-17c · Ratenbudget.**
Trockenlauf des Pollers über simulierte 5 Minuten mit `MARKET_POLL_S=60` und 2 Symbolen:
verbrauchtes Gewicht **≤ 10 pro Minute** (erwartet 4,07; Limit 6.000 → ≤ 0,17 %).
Zähler im Client.
*Rot:* `MARKET_POLL_S=1` → 240/min.

**A-18 · Keine neue Abhängigkeit.**
`sha256sum app/requirements.txt` ist identisch zu v0.2.1, und
`docker compose exec ai-trade-lab pip list --format=freeze | wc -l` wächst gegenüber v0.2.1
um **0**.
*Rot:* `requests` in `requirements.txt` eintragen → Hash weicht ab.

### Der Weg zum Nutzer

**A-19a · End-to-End, Rauchlauf mit 1m-Kerzen.**
Die Produktionskonfiguration nutzt 15m-Kerzen; ein Fill käme dort erst nach bis zu ~16 Minuten
(Abschnitt 9.1) — länger als jedes vertretbare Zeitfenster für einen Rauchlauf. Der Rauchlauf
setzt deshalb bewusst `MARKET_INTERVAL=1m` und `MARKET_POLL_S=15`. Nach `docker compose up -d`
und höchstens **150 s**:

| Messung | Befehl | Schwelle |
|---|---|---|
| Kerzen kommen an | `curl -s localhost:8787/api/status \| jq '.market_data.age_s'` | ≥ 0 und ≤ 150 |
| Benchmark läuft | `… \| jq -r '.benchmark.equity'` | Zahl > 0, nicht `null` |
| Equity-Kurve wächst | `curl -s localhost:8787/api/equity-curve \| jq '.points \| length'` | **≥ 2** |
| Vorschlag schwebt zuerst | `POST /api/risk/check` BUY 10 % → `jq -r '.status'` | `pending_fill` |
| … und wird dann gefüllt | nach ≤ 150 s: `jq '.trades_total'` und `jq '.positions \| length'` | genau **1** und genau **1** |
| Kasse ist geführt, nicht abgeleitet (B-3) | `jq -r '.cash'` nach dem Kauf | `< 10000` und `> 8900` (1.000 USDC Order + Gebühr) |
| Health bleibt grün | `curl -s -o /dev/null -w '%{http_code}' …/api/health` | `200` |
| Das Dashboard ruft die Kurve ab | `grep -c "api/equity-curve" app/static/index.html` | **≥ 1** |

*Rot:* `MARKET_DATA_ENABLED=false` → `benchmark.equity` ist `null`, `points` leer,
`trades_total` bleibt 0; fünf der acht Messungen fallen.

**A-19b · End-to-End, Produktionskonfiguration mit 15m-Kerzen.**
`MARKET_SYMBOLS=BTCUSDC,ETHUSDC`, `MARKET_INTERVAL=15m`, `MARKET_POLL_S=60`.
Nach höchstens **150 s**: `market_data == "ok"`, `age_s ≤ 900`, `benchmark.equity` ist eine
Zahl > 0, `points | length >= 1`, HTTP 200. Ein Fill wird hier **nicht** erwartet.
*Rot:* Die intervallrelative Schwelle aus 8.2 entfernen → `market_data == "stale"` und HTTP 503
statt 200 (zugleich der Nachweis, dass A-11 einen echten Betriebsfall absichert).

**A-20 · Die Testsuite als Ganzes.**
`cd app && python -m pytest -q`: **0 failures, 0 errors**, Anzahl Tests **≥ 66** (heute 18).
`./build.sh` läuft bis zum Ende durch und erzeugt `dist/ai-trade-lab-install.sh` mit passender
`.sha256`.
*Rot:* Einen der obigen Tests scheitern lassen → `build.sh` bricht wegen `set -e` in Zeile 6 ab.

**A-21 · Das README sagt die Wahrheit über den Netzverkehr (B-2).**
`grep -c "api.binance.com" README.md` ist **≥ 1**, und der Abschnitt „Sicherheit" nennt, dass
keine Kontodaten oder Kennungen übertragen werden:
`grep -c "keine Kontodaten\|keinerlei Kontodaten" README.md` ist **≥ 1**.
Die unqualifizierte Aussage darf nicht stehen bleiben:
`grep -c "^Privates Paper-Trading-Labor.*ohne Cloud-Dienste" README.md` ist **0**.
*Rot:* Das README unverändert lassen → erste und zweite Messung 0, dritte 1.
*Warum ein Kriterium und keine Aufgabe:* Das ist eine Sicherheitsaussage gegenüber dem Nutzer,
kein Kommentar.

**A-22 · Die Abnahme misst nicht versehentlich auf 100 USDC (B-6).**
`proxmox/installer.sh:533` (`if [ ! -f .env ]`) lässt die `.env` beim Update unangetastet.
Ein bestehender Container läuft nach dem Update also weiter mit `STARTING_BALANCE=100` — und
damit im engen Fenster [5 %, 10 %] aus K-1, das die Entscheidung für 10.000 USDC gerade
beseitigen sollte. **Wer A auf einem solchen Container abnimmt, misst das Falsche und merkt es
nicht.** Zwei Sicherungen:

| Messung | Befehl | Schwelle |
|---|---|---|
| Der Container läuft mit dem entschiedenen Kapital | `pct exec 107 -- grep '^STARTING_BALANCE=' /opt/ai-trade-lab/.env` | `STARTING_BALANCE=10000` |
| Die App bestätigt es | `curl -s …/api/status \| jq -r '.starting_balance'` | `10000` |
| Die App warnt von selbst, wenn nicht | Start mit `STARTING_BALANCE=100` → `GET /api/events` enthält genau **1** Ereignis `NARROW_TRADING_WINDOW` (severity WARN) mit den konkreten Grenzen; Start mit `10000` → **0** Ereignisse | 1 bzw. 0 |

*Schwelle der Warnung, abgeleitet statt geraten:* Das Fenster ist brauchbar, wenn die größte
Order mindestens 20× der kleinsten entspricht:
`balance × MAX_POSITION_PCT/100 ≥ 20 × effective_min_notional` → bei 5,82 USDC und 10 %
ergibt das **balance ≥ 1.164 USDC**. Gewarnt wird unterhalb von 1.200 USDC.
Bei 10.000 USDC ist das Verhältnis 172, bei 100 USDC nur 1,7.
*Rot:* Den Startcheck entfernen → der 100-USDC-Start erzeugt 0 Ereignisse und die Abnahme
liefe unbemerkt im engen Fenster.
*Warum in den Kriterien und nicht in der Release-Notiz:* Eine Notiz liest, wer sie sucht.

### Bewusste Lücke

**L-1 · Kein automatisierter Browsertest.**
Dass das Dashboard die beiden Polylinien wirklich zeichnet, wird in A **nicht** automatisch
geprüft — Playwright oder ein Headless-Browser sprengen das Abhängigkeitsbudget (E-004) und das
Laufzeit-Image. **A-19a prüft ausschließlich die Datenlage am Endpunkt** plus einen `grep`, der
belegt, dass der Frontend-Code den Endpunkt überhaupt anspricht. Ob ein Pixel erscheint, prüft
A-19a nicht.
Bis zur Schließung gilt: der Chart wird **einmal von Hand** in Safari (WebKit — die Engine, in
der auf macOS alles läuft) und in Firefox angesehen und das Ergebnis mit Screenshot in
`docs/abnahme/` abgelegt. **Ein Blick ist kein Test.** Er erzeugt keinen Rot-Nachweis, er
wiederholt sich nicht bei jeder Änderung, und er darf in keiner Abnahme als grüner Haken
auftauchen — er ist ein datierter Beleg für einen Zeitpunkt, mehr nicht.
*Die Aufgabe, die die Lücke einlöst:* „Dashboard-Rauchtest mit Playwright in einem separaten
Entwicklungs-Image, nicht im Laufzeit-Image" — Teilprojekt A+, nach A.

---

## 13. Die Live-Schleife: drei Ansätze, eine Empfehlung

Die offene Architekturfrage in A ist nicht das Ledger (durch E-001 entschieden), sondern
**wo der Poller läuft**.

### Ansatz 1 — Hintergrund-Thread im gunicorn-Worker *(Empfehlung)*

`create_app()` startet bei `MARKET_DATA_ENABLED=true` genau einen Daemon-Thread.

| Dafür | Dagegen |
|---|---|
| Keine neue Prozess-, Container- oder Compose-Struktur | Bei `--workers > 1` liefe der Poller mehrfach; heute ist `--workers 1` gesetzt (Dockerfile 23), aber das ist eine ungeschriebene Kopplung |
| Ledger-Zustand liegt im selben Prozess wie die API → kein Zustand über die DB zu synchronisieren | Ein Worker-Neustart setzt den In-Memory-Ledger zurück (muss aus `fills` rekonstruiert werden — A-14 stellt das ohnehin sicher) |
| Kein zweiter DB-Schreiber, WAL-Konflikte bleiben aus | Ein blockierender Netzaufruf kann bei `--threads 4` HTTP-Kapazität kosten |
| Tests können den Thread schlicht nicht starten (Vorgabe `false`) | Im Container schwerer zu beobachten als ein eigener Prozess |

**Absicherung:** `--workers 1` wird im Dockerfile mit einem Kommentar als Voraussetzung
markiert, und `create_app()` legt `data/poller.lock` mit `O_EXCL` an; existiert sie und lebt der
darin genannte PID, startet der Thread nicht und protokolliert ein Event.

### Ansatz 2 — Eigener Dienst `aitra-poller` im Compose

| Dafür | Dagegen |
|---|---|
| Getrennte Verantwortung, eigene Logs, eigener Neustart | Zwei SQLite-Schreiber auf einem Volume — WAL trägt das, aber `database is locked` wird zur echten Betriebsart |
| Poller-Absturz nimmt das Dashboard nicht mit | Ledger-Zustand muss über die DB geteilt werden → neue Kohärenzfragen |
| Skaliert später auf viele Symbole | `proxmox/installer.sh` müsste Installation, Update und Rollback für einen zweiten Container kennen — spürbarer Aufwand außerhalb von A |

### Ansatz 3 — Träges Nachladen bei HTTP-Anfragen

| Dafür | Dagegen |
|---|---|
| Kein Thread, kein Prozess, einfachster Code | **Die Veraltet-Erkennung wird wirkungslos:** ohne Besucher keine Prüfung, also kein Kill Switch — genau das, was E-005 verlangt, fällt aus |
| Kein Netzverkehr ohne Nutzung | Das Dashboard pollt alle 10 s (`index.html:267`) → das Ratenbudget hinge an der Zahl offener Tabs |
| — | Die Equity-Kurve hätte Löcher, sobald niemand hinsieht |

### Empfehlung: **Ansatz 1**

Ansatz 3 scheidet aus, weil er die Sicherheitsfunktion aus E-005 von der Anwesenheit eines
Browsers abhängig macht — das ist kein Kompromiss, sondern ein Defekt. Zwischen 1 und 2
entscheidet der Umfang: Ansatz 2 ist die bessere Endarchitektur, verlangt aber Änderungen am
Installer, Update- und Rollback-Pfad — Arbeit außerhalb von A, deren Nutzen erst bei vielen
Symbolen eintritt. Ansatz 1 liefert dieselbe Funktion mit rund 40 Zeilen, und der spätere
Wechsel ist billig, weil `poller.py` bereits ein aufrufbares Modul mit `__main__` ist und der
Ledger-Zustand dank A-14 ohnehin aus der DB rekonstruierbar sein muss.

**Kosten bei Irrtum:** Wird `--workers` je erhöht, ohne die Kopplung zu bemerken, laufen zwei
Poller und verdoppeln Ratenbudget und Kill-Switch-Events. Die Lockdatei fängt das ab; ihr
Fehlen wäre der eigentliche Fehler.

---

## 14. Offene Fragen

Nur noch zwei, beide reine Zahlenwerte. Die Kriterien sind so formuliert, dass die Zahl
eingesetzt werden kann, ohne den Text umzuschreiben.

| Nr. | Frage | Wo die Zahl hingehört |
|---|---|---|
| **F-1** | Wie viel RAM und Disk hat CT 107 tatsächlich? (`pct config 107 \| grep -E 'memory\|rootfs'`) | **A-10b** (RSS < 25 % des RAM: 2.048 MB → 512 MB, 8.192 MB → 2.048 MB) und **A-16b** (Kerzen-DB < 10 % der Disk: 16 GB → 1,6 GB, 32 GB → 3,2 GB) |
| **F-3** | Kommt ausgehendes HTTPS nach `api.binance.com` aus dem LXC durch? (`pct exec 107 -- curl -s -o /dev/null -w '%{http_code}' https://api.binance.com/api/v3/time` → erwartet `200`) | Keine Schwelle, aber eine Voraussetzung: Ohne Egress ist A nur im Replay lauffähig, und die Bauschritte 7–9 müssten gegen einen lokalen Fake laufen |

### Beantwortet und eingearbeitet

| Nr. | Antwort |
|---|---|
| F-2 | Python bleibt **3.12** (`python:3.12-slim`); 3.13 war die Bauumgebung |
| F-4 | **BTCUSDC und ETHUSDC**, Intervall **15m** (Fassung 2 hatte 1h); Rückfüllung 400 Tage |
| F-5 | **`STARTING_BALANCE = 10000`** — K-1 … K-3 gerechnet; Umsetzung auf Bestandscontainern über A-22 |
| F-6 | Gebühr in **Quote-Währung (USDC), 0,10 %, kein BNB-Rabatt** |
| F-7 | Fill auf der **Folgekerze, auch live** — bei 15m bis zu ~16 min, Verfall nach 30 min |
| F-8 | Kill Switch nach Tagesverlust löst sich **nur im Replay** an der UTC-Tagesgrenze → **E-008** |
| F-9 | Kill Switch nach Datenausfall: **Freigabe nur mit Admin-Token** |
| F-10 | Befund **B-1 ist Teil von A** → Kriterium A-6c |
| F-11 | Replay **nur per CLI**, kein HTTP-Endpunkt |
| F-12 | Chart: **zwei Linien**, Portfolio gegen BTC Buy & Hold, sonst nichts |

---

## 15. Befunde aus dem Bestand

**B-1 · `risk.py:82-83` prüft SELL gegen das Gesamtrisiko statt gegen die Position im Symbol.**
**→ in A behoben (F-10), Kriterium A-6c.**

```python
if action == "SELL" and p.position_pct > pf.exposure_pct:
    return RiskDecision(False, "NO_POSITION", "Verkauf größer als vorhandene Position (kein Short)")
```

Heute harmlos, weil `pf.exposure_pct` immer 0 ist. Sobald ein Ledger existiert, lässt diese
Prüfung einen BTC-Verkauf durch, solange ETH genug Gesamtrisiko trägt — also genau den Short,
den sie verhindern soll. Korrektur: `PortfolioState` bekommt `position_pct_by_symbol` (mit
Vorgabewert, damit `test_risk.py` unverändert konstruiert), die Prüfung nutzt
`pf.position_pct_by_symbol.get(p.symbol, 0.0)`. `test_no_short` bleibt grün.

**B-2 · Das README behauptet etwas, das mit A nicht mehr stimmt.** **→ in A korrigiert, A-21.**
`README.md:3-5`: „komplett lokal – ohne Cloud-Dienste". Vorschlag für den neuen Text:
„… komplett lokal, ohne Cloud-Dienste — mit einer Ausnahme: Für Kursdaten ruft die App die
öffentlichen, lesenden Endpunkte von `api.binance.com` ab. Dabei werden ausschließlich Symbol,
Intervall und Zeitraum übertragen, **keine Kontodaten, keine Kennungen, keine API-Schlüssel**;
solche existieren im Projekt nicht."

**B-3 · `web.py:114` leitet die Kasse ab, statt sie zu führen.** **→ in A behoben, A-19a.**
`cash = equity × (1 − exposure/100)` stimmt nur, solange nichts gehandelt wird.

**B-4 · `float` in `config._num()` ist die Quelle, nicht nur die Stelle.** **→ in A behoben.**
`STARTING_BALANCE=0.1` wird heute zu `0.1000000000000000055511151231257827`. E-002 korrigiert
das an der Quelle (`Decimal(raw)` direkt aus dem Umgebungsstring), nicht erst im Ledger.

**B-5 · Umsetzungshinweis: Die Testsuite konstruiert `Config` und `PortfolioState` positional.**
`test_risk.py:11`, `test_api.py:11`, `test_api.py:48`. Jedes neue Pflichtfeld ohne Vorgabewert
bricht alle drei Stellen. **Regel für A: neue Felder nur hinten und nur mit Vorgabewert.**
Die übergebenen Python-`int`s (`100`, `10`, …) sind mit `Decimal` verträglich; gemischte
`float`/`Decimal`-Arithmetik wirft dagegen erst zur Laufzeit `TypeError` — deshalb wandelt
ausschließlich `money.py` an den Systemgrenzen.

**B-6 · Ein bestehender Container bekommt das neue Startkapital nicht.**
**→ in A abgesichert, Kriterium A-22 — kein Nebensatz, sondern ein Abnahmepunkt.**
`proxmox/installer.sh:533` (`if [ ! -f .env ]`) kopiert `.env.example` **nur bei der
Erstinstallation** und ersetzt danach ausschließlich `ADMIN_TOKEN`. CT 107 läuft nach dem
Update weiter mit `STARTING_BALANCE=100` — im engen Fenster [5 %, 10 %] aus K-1.
**Wer A auf einem solchen Container abnimmt, misst das Falsche und merkt es nicht.**

Drei Maßnahmen, keine davon ein automatisches Überschreiben der `.env` (dort steht der
Admin-Token):

1. Die v0.3.0-Release-Notiz und das README nennen den Handgriff:
   `pct exec 107 -- sed -i 's/^STARTING_BALANCE=.*/STARTING_BALANCE=10000/' /opt/ai-trade-lab/.env`
   samt `docker compose restart`.
2. Die App warnt beim Start selbst (`NARROW_TRADING_WINDOW`), wenn das Fenster zu eng ist —
   damit es auch auffällt, wenn niemand die Notiz gelesen hat.
3. **A-22** prüft beides bei der Abnahme.

---

## 16. Wie B und C andocken, ohne A umzubauen

### Teilprojekt B — LLM-Analyst

B erzeugt `risk.Proposal`-Objekte. Mehr nicht.

```python
execute.execute_proposal(
    Proposal(symbol="BTCUSDC", action="BUY", position_pct=8, confidence=71),
    ctx, source="llm-analyst-v1", reason="…",
)
```

Was A dafür bereitstellt:

- `execute_proposal()` als **die** Funktion, die Risk, Sizing, Ledger und Journal verbindet —
  benutzt vom bestehenden `POST /api/risk/check` genauso wie später von B (A-6b).
- `decisions.strategy_version` existiert bereits und nimmt die Herkunft auf.
- Marktdaten als Lesequelle: `GET /api/market/candles` und `SqliteSource`.
- **Keine** Änderung am Ledger, an `sizing.py` oder an der Risk Engine.

B muss mitbringen: Prompting, Anbindung, Kosten, Rate Limits, Halluzinationsschutz — und die
Entscheidung, ob Marktdaten an einen fremden Dienst gehen dürfen (Prime Directive 1). A trifft
diese Entscheidung nicht vor.

**Für B relevant:** Ein Vorschlag wird nicht sofort gefüllt, sondern schwebt bis zur nächsten
Kerze (bis ~16 min) und verfällt nach 30 min. B muss damit umgehen, dass zwischen Vorschlag und
Fill ein Kursänderung liegt — und darf nicht im Sekundentakt nachlegen.

### Teilprojekt C — RL-Agent

| C braucht | A liefert | Kriterium |
|---|---|---|
| Sehr viele Schritte in kurzer Zeit | `replay.run_replay()` mit In-Memory-Ledger, ohne DB | A-9 (≥ 876 Entscheidungen/s im Container) |
| Reproduzierbarkeit über Episoden | kein Zufall, keine Uhr unterhalb des Runners | A-8b, A-8c |
| Beobachtung und Belohnung | `Valuation` je Schritt und die Equity-Kurve | A-1, A-14 |
| Episoden, die nach einem schlechten Tag weiterlaufen | lauf-lokaler Kill Switch mit Tagesreset (E-008) | A-12b |

```python
def step(self, action):
    proposal = self._to_proposal(action)
    result   = execute.execute_proposal(proposal, self.ctx)   # A
    self.i  += 1
    v        = self.ledger.mark({sym: self.candles[self.i].close}, self.candles[self.i].close_time)
    return self._obs(v), self._reward(v), self.i >= len(self.candles) - 1, {}
```

C darf `Ledger.apply()` **nicht** direkt aufrufen — das ist E-003, und A-6b misst es.

**Für C relevant, und ausdrücklich zu planen:** Mit 15m umfasst ein Jahr 35.040 Schritte.
An der Tempo-Untergrenze aus A-9 (876/s im Container) kostet eine Jahresepisode **40 s**.
Quartalsepisoden (8.640 Schritte) kosten rund 10 s. Welche Episodenlänge sinnvoll ist, entscheidet
C — aber die Zahl gehört auf den Tisch, bevor jemand ein Trainingsbudget schätzt.

### Was A bewusst *nicht* vorbaut

Kein abstraktes Strategie-Plugin-System, kein Ereignisbus, keine generische Feature-Pipeline.
YAGNI: B und C brauchen genau zwei Berührungspunkte (`execute_proposal` und `run_replay`),
und beide entstehen in A ohnehin.

---

## 17. Vorgeschlagene Bauschritte (Skizze für PLAN)

| # | Schritt | Macht grün |
|---|---|---|
| 1 | `money.py` + `Decimal` an der Quelle in `config.py` (B-4), `STARTING_BALANCE=10000`, Startwarnung `NARROW_TRADING_WINDOW` | A-3 (teilweise), A-22 (dritte Messung) |
| 2 | Migration 2 + `store.py` | A-13, A-15, A-16, A-16b |
| 3 | `marketdata.py` (Candle, Clock, ListSource, SqliteSource) | A-8b |
| 4 | `ledger.py` + `sizing.py` inkl. `effective_min_notional` | A-1 … A-5, A-4b |
| 5 | `risk.py`-Korrektur B-1 + `execute.py` inkl. schwebender Vorschläge und Verfall | A-6, A-6b, **A-6c**, A-7b |
| 6 | `replay.py` + `benchmark.py` + E-008 | A-7, A-8, A-8c, **A-9**, A-10, A-12b |
| 7 | `binance.py` (Netz, Härtung) | A-17, A-17b |
| 8 | `poller.py` + intervallrelative Veraltet-Erkennung | A-11, A-11b, A-12, A-17c |
| 9 | `web.py`-Endpunkte + `static/index.html` (Chart, schwebende Vorschläge, B-3) | A-19a, A-19b |
| 10 | `backfill.py`, README (B-2, B-6), CHANGELOG, `.env.example`, Release-Notiz | A-18, A-20, A-21, A-22 |

**A-9 gehört in Schritt 6, nicht ans Ende.** Sollte Decimal an der Tempo-Schwelle scheitern
(E-002, Kosten bei Irrtum), ist eine Umkehr dort noch billig. Mit 15m ist das Datenvolumen
viermal so groß wie in Fassung 2 — die Messung ist dadurch aussagekräftiger, aber auch
näher an der Grenze.

---

## 18. Neue Konfigurationsvariablen

| Variable | Vorgabe | Grenzen | Zweck |
|---|---|---|---|
| `STARTING_BALANCE` | **10000** *(geändert von 100)* | 0 < x ≤ 1.000.000 | Startkapital, jetzt `Decimal`. Unterhalb von 1.200 → Startwarnung (A-22) |
| `MARKET_DATA_ENABLED` | `false` | bool | Startet den Poller. Vorgabe aus, damit Tests nie ins Netz gehen |
| `MARKET_SYMBOLS` | `BTCUSDC,ETHUSDC` | 1 … 5 Symbole, je gegen `SYMBOL_RE` | zu verfolgende Symbole |
| `MARKET_INTERVAL` | **`15m`** | `1m 5m 15m 1h 4h 1d` | Kerzenintervall. Bestimmt Veraltet-Schwellen, Fill-Verzögerung und Datenmenge (K-5) |
| `MARKET_POLL_S` | `60` | 10 … 300 | Abrufabstand; unabhängig vom Intervall |
| `MARKET_STALE_WARN_S` | `150` | 30 … 3600 | **Untergrenze** für `warn`; wirksam ist `max(wert, 1,5·interval_s)` → bei 15m **1.350 s** |
| `MARKET_STALE_KILL_S` | `300` | 60 … 86400, muss > warn sein | **Untergrenze** für `stale`; wirksam ist `max(wert, 3·interval_s)` → bei 15m **2.700 s** |
| `MARKET_CLOCK_SKEW_WARN_S` | `5` | 1 … 60 | Uhrversatz `warn` (absolut) |
| `MARKET_CLOCK_SKEW_KILL_S` | `30` | 5 … 600, muss > warn sein | Uhrversatz `stale` (absolut) |
| `BINANCE_BASE_URL` | `https://api.binance.com` | Host-Allowlist, nur HTTPS | Datenquelle |
| `FEE_BPS` | `10` | 0 … 100 | Handelsgebühr, in USDC abgerechnet |
| `SLIPPAGE_BPS` | `5` | 0 … 200 | Slippage |
| `BENCHMARK_SYMBOL` | `BTCUSDC` | `SYMBOL_RE` | Vergleichsmaßstab |
| `CANDLE_RETENTION_DAYS` | `400` | 7 … 3650 | Aufbewahrung → bei 15m und 2 Symbolen 76.800 Zeilen |

---

## 19. Änderungen gegenüber Fassung 2

Auslöser: **Intervall 15m statt 1h.** Fast jede Zahl in den Kriterien hängt daran.

| # | Was | Neu | Alt (Fassung 2) |
|---|---|---|---|
| 1 | Kerzen je Jahr und Symbol (K-5) | **35.040** | 8.760 |
| 2 | **A-9**, Schwelle aus der Entscheidungsrate abgeleitet | **< 12,0 s** (Dev, ≥ 2.920/s) · **< 40,0 s** (Container, ≥ 876/s) | < 3,0 s · < 10,0 s |
| 3 | **A-10 — die Zahl, die gekippt wäre.** 35.040 Kerzen brauchen 32–48 MB; die alte Schwelle war **unerfüllbar** und hätte nachträglich aufgeweicht werden müssen | **< 120 MB** | < 40 MB |
| 4 | Veraltet-Schwellen bei 15m (8.2) | `warn_s` **1.350 s**, `kill_s` **2.700 s** | 5.400 / 10.800 (1h) |
| 5 | **A-11**: die drei 1h-Messpunkte wurden zu 15m-Punkten | 1.349 / 2.699 / 2.701 s | 5.399 / 10.799 / 10.801 s |
| 6 | **A-19b** Produktionslauf: erwartetes `age_s` | **≤ 900** | ≤ 3600 |
| 7 | **E-006 / A-7b**: Live-Verzögerung und Verfall als konkrete Minutenzahlen | **~16 min** bis Fill, **30 min** Verfall; A-7b misst den Verfall an 1.799 s / 1.801 s | ~61 min / 2 h, Verfall nur als Formel |
| 8 | **A-7**: Umfang des Look-ahead-Replays (90 Tage) | **8.640** Kerzen | 2.160 |
| 9 | **A-12**: Umfang des Jahresreplays | **35.040** Kerzen | 8.760 |
| 10 | **A-12b**: 5 Tage | **480** Kerzen | 120 |
| 11 | **A-16b**: Zeilen bei 400 Tagen Aufbewahrung und der Unterschreitungsfaktor **korrigiert** | **76.800 Zeilen ≈ 19,2 MB**, Faktor **83** (16 GB) bzw. **167** (32 GB) | 19.200 Zeilen ≈ 4,8 MB, Faktor 300 |
| 12 | **A-22 neu** für B-6: Abnahme darf nicht versehentlich auf 100 USDC messen — inklusive Startwarnung `NARROW_TRADING_WINDOW` mit abgeleiteter Schwelle (**1.200 USDC**, aus `20 × 5,82 / 0,10`) | — | B-6 war nur ein Befund |
| 13 | Abschnitt 16: Für C ausdrücklich beziffert, dass eine **Jahresepisode jetzt 40 s** statt 10 s kostet, Quartalsepisode ~10 s | — | — |
| 14 | **A-17** Reihenfolge und Rot-Nachweis geschärft: der eingebaute Fehler ist jetzt `startswith` statt Hostvergleich, und die Präfix-Falle ist der Messpunkt, der ihn trifft | — | Rot-Nachweis traf die Präfix-Falle nicht gezielt |
| 15 | Erwartete Testanzahl | **≥ 66** | ≥ 65 |
| 16 | Neues K-5 als Übersicht: welche Zahl am Intervall hängt | — | — |
| 17 | Betriebshinweis zu A-9: der 40-s-Containerlauf läuft als `@pytest.mark.slow`, damit `build.sh` nicht ausgebremst wird | — | — |
| 18 | Ratenbudget **unverändert** bei 4,07 Gewicht/min — es hängt am Poll-Abstand, nicht am Intervall | 0,068 % | 0,068 % |

**Unverändert geblieben:** K-1 bis K-4 (vom Intervall unabhängig), das Füllmodell, das
Datenmodell, die Sicherheitsabschnitte, die Empfehlung für Ansatz 1, alle Kriterien zur
Buchhaltung (A-1 … A-5), zum Nadelöhr (A-6 … A-6c) und zur Persistenz (A-13 … A-16).

---

*Ende der Spec. Offen sind nur F-1 und F-3; beide blockieren den Baubeginn nicht — F-1 blockiert
die Abnahme von A-10b und A-16b, F-3 die Bauschritte 7–9 im echten Container.*
