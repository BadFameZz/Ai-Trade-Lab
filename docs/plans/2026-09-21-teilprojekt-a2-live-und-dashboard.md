# Teilprojekt A2 — Marktdaten live, Poller, Dashboard, Auslieferung

> **Für agentische Bearbeiter:** ERFORDERLICHE SUB-SKILL: `superpowers:subagent-driven-development`.
> Die Schritte nutzen Checkbox-Syntax (`- [ ]`).

**Ziel:** Aitra holt echte Kerzen von Binance, führt sie live nach, zeigt Portfolio und
Benchmark im Dashboard — und liefert sich als Release aus.

**Architektur:** `binance.py` ist die einzige Stelle mit Netzzugriff und liefert dieselben
`Candle`-Objekte wie `ListSource` und `SqliteSource`. Der Poller speist sie in die Datenbank
und löst schwebende Vorschläge auf. Das Ledger, die Risk Engine und `execute_proposal()`
bleiben unverändert — sie wissen nicht, woher die Kerzen kommen. Genau das ist E-001.

**Tech-Stack:** Python 3.12, Standardbibliothek. `urllib.request` fürs Netz — dieselbe
Bibliothek, die der Healthcheck im Dockerfile schon benutzt. **Keine neue Abhängigkeit.**

**Spec:** `docs/specs/2026-09-20-teilprojekt-a-marktdaten-ledger-benchmark.md`, Bauschritte 7–10
**Entscheidungen:** `docs/entscheidungen/E-001` … `E-010`
**Vorgänger:** `docs/plans/2026-09-21-teilprojekt-a1-offline-engine.md` (abgeschlossen, 157 Tests)

---

## Globale Randbedingungen

Diese gelten für **jede** Aufgabe:

- **Keine neue Laufzeitabhängigkeit.** `app/requirements.txt` bleibt bei `flask==3.1.3`
  und `gunicorn==26.2.0`. (A-18)
- **Python 3.12** — `app/Dockerfile` ist `python:3.12-slim`.
- `from __future__ import annotations` nach dem Modul-Docstring, frozen dataclasses,
  deutschsprachige Docstrings.
- **Richtwert 200 Zeilen je Modul, Marke bei 300** (Spec 3.1b). `store.py` liegt mit 318
  darüber und wird in Aufgabe 1 geteilt.
- **Geld ist niemals `float`.** Prozentsätze und Basispunkte dürfen `float` bleiben.
- **Nur `binance.py` greift aufs Netz.** Kein anderes Modul öffnet eine Verbindung.
- **Kein Test geht ins Netz.** `MARKET_DATA_ENABLED` ist per Vorgabe `false`; Netztests
  laufen gegen eine Attrappe, nicht gegen Binance.
- **Bestandstests bleiben grün.** Aktuell **157** (153 ohne `slow`). Die Zahl muss steigen.
- Tests im Container:
  `docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c 'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q'`
- **Rot-Nachweis ist Pflicht**, ausgeführt und wörtlich, nicht hergeleitet.

---

## Fünf Lehren aus A1, die hier wieder greifen

Sie stehen hier, weil jede von ihnen in A1 einen echten Fehler aufgedeckt hat:

1. **Schlägt ein Rot-Nachweis nicht an, taugt der Nachweis nichts, nicht der Test.** Melden
   statt still ersetzen.
2. **Prüffläche zählen.** Ein Test über viele Stichproben, der Fälle überspringt, muss zählen
   wie viele wirklich geprüft wurden, und scheitern wenn es zu wenige sind.
3. **Ein Test muss den Code berühren, den er absichert.** Eine Parallelrechnung beweist nichts
   über das ausgelieferte Modul.
4. **Erwartungswerte herleiten, nicht aus dem Lauf übernehmen.**
5. **Bei Widersprüchen fragen, nicht raten.** In A1 hat das viermal einen Planfehler aufgedeckt.

---

## Zwei Entscheidungen, die vor dem Bauen gefallen sind

### E-010 ist aufgelöst — der Livebetrieb rechnet bei `t`, nicht bei `t+1`

`run_replay` bewertet, prüft und bemisst alles bei `t` und bucht auf `t+1` — in einem Aufruf.
Der Livepfad muss dasselbe tun, sonst weichen die Mengen ab und A-8 ist unerreichbar.

**Entschieden:** Beim Einstellen eines schwebenden Vorschlags wird die **fertig bemessene
Order** gespeichert, nicht nur der Referenzpreis. Beim Auflösen wird **nicht neu bewertet und
nicht neu bemessen** — nur der Kill Switch wird erneut geprüft, dann gebucht.

Die Kill-Switch-Prüfung ist die einzige Ausnahme von der Parität, und sie ist eine
Sicherheitsfunktion: ein Vorschlag, der zwischen Entscheidung und Ausführung vom Kill Switch
eingeholt wird, darf nicht mehr füllen. Im Zeitraffer gibt es diese Lücke nicht, weil
Entscheidung und Ausführung in derselben Iteration liegen.

*Kosten bei Irrtum:* Ändert sich das Portfolio zwischen `t` und `t+1` durch einen Fill in einem
anderen Symbol, kann die gespeicherte Menge die Kasse übersteigen. Das Ledger lehnt dann mit
`INSUFFICIENT_CASH` ab — richtig, aber der Vorschlag ist verloren statt verkleinert zu werden.
Bei zwei Symbolen und 15-Minuten-Kerzen ein seltener Fall; tritt er gehäuft auf, wird beim
Auflösen auf die verfügbare Kasse gedeckelt.

### Die Symbole sind BTCUSDC und BNBUSDC, nicht ETHUSDC

Der Nutzer handelt BTC/USDC und BNB/USDC. Am 2026-09-21 von Binance abgefragt:

| Symbol | tickSize | stepSize | minQty | minNotional | Preis | wirksame Mindestorder |
|---|---|---|---|---|---|---|
| BTCUSDC | 0,01 | 0,00001 | 0,00001 | 5 | 85.237,71 | **5,85 USDC** |
| BNBUSDC | 0,01 | **0,001** | 0,001 | 5 | 789,71 | **5,79 USDC** |

Losgrößenverlust je 1.000-USDC-Order: BTC 0,85 USDC (0,085 %), BNB 0,79 USDC (0,079 %).

**Gebühr bleibt bei 0,10 %, ohne BNB-Rabatt** — ausdrücklich entschieden. Ein Backtest, der
einen Rabatt unterstellt, den der Agent sich durch den Verkauf seiner BNB selbst nehmen kann,
schönt das Ergebnis. Was mit voller Gebühr trägt, trägt mit Rabatt erst recht.

---

## Dateiübersicht

| Datei | Neu/Geändert | Verantwortung |
|---|---|---|
| `app/aitra/store.py` | geteilt | Marktdaten: Kerzen, Symbol-Specs |
| `app/aitra/store_run.py` | neu | Lauf, Fills, Positionen, Equity-Kurve, Entscheidungen |
| `app/aitra/money.py` | geändert | `BUILTIN_SPECS`: ETHUSDC → BNBUSDC |
| `app/aitra/execute.py` | geändert | E-010: Order speichern statt neu bemessen |
| `app/aitra/binance.py` | neu | **Die einzige Stelle mit Netzzugriff** |
| `app/aitra/poller.py` | neu | Livebetrieb: Thread, Backoff, Veraltet-Erkennung |
| `app/aitra/backfill.py` | neu | Historische Kerzen holen, CLI |
| `app/aitra/web.py` | geändert | Neue Endpunkte, geführte Kasse, echte Positionen |
| `app/static/index.html` | geändert | Chart, offene Positionen, schwebende Vorschläge |
| `app/.env.example`, `README.md`, `app/CHANGELOG.md`, `app/VERSION` | geändert | v0.3.0 |

---

## Aufgabenübersicht

| # | Aufgabe | Macht grün |
|---|---|---|
| 1 | `store.py` teilen, Symbole auf BNBUSDC | — (Altlast aus A1) |
| 2 | E-010 auflösen: Order speichern, beim Auflösen nur Kill Switch prüfen | A-8 wird erreichbar |
| 3 | `binance.py` — Netz, Härtung, Allowlist, Größenlimit | A-17, A-17b |
| 4 | `backfill.py` — historische Kerzen, CLI | A-16b |
| 5 | `poller.py` — Thread, Backoff, Veraltet → Kill Switch | A-11, A-11b, A-12, A-17c |
| 6 | `web.py` — Endpunkte, geführte Kasse, echte Positionen | A-19a |
| 7 | `static/index.html` — Chart, Positionen, schwebende Vorschläge | A-19b |
| 8 | README, CHANGELOG, `.env.example`, v0.3.0, Release | A-20, A-21, A-22 |

**Aufgabe 2 vor Aufgabe 5.** Entsteht der Poller vor der E-010-Auflösung, baut er auf der
Asymmetrie auf und A-8 ist danach nicht mehr erreichbar, ohne ihn umzubauen.

---

### Aufgabe 1: `store.py` teilen und die Symbole auf BNBUSDC umstellen

**Macht grün:** kein neues Kriterium — Altlast aus A1 (Spec 3.1b: `store.py` liegt mit 318
Zeilen über der harten Marke von 300) plus die Symbolentscheidung dieses Plans. Hält
A-13, A-14, A-16 grün, die über die verschobenen Funktionen laufen.

**Dateien:**
- Geändert: `app/aitra/store.py` (bleibt: Marktdaten — `CandleRow`, `upsert_candles`,
  `get_candles`, `prune_candles`, `upsert_symbol_spec`, `get_symbol_spec`)
- Neu: `app/aitra/store_run.py` (wandert dorthin: `create_run`, `finish_run`, `insert_fill`,
  `get_fills`, `upsert_position`, `get_positions`, `EquityPoint`, `append_equity_points`,
  `get_equity_curve`, `mark_decision_pending`, `resolve_decision`, `reject_decision`,
  `expire_decision`, `get_pending_decisions`)
- Geändert: `app/aitra/execute.py` (13 `store.`-Aufrufe → `store_run.`)
- Geändert: `app/aitra/replay.py` (`store.create_run`, `store.finish_run`, `store.EquityPoint`,
  `store.append_equity_points` → `store_run.`; `store.get_candles` bleibt)
- Geändert: `app/aitra/money.py` (`BUILTIN_SPECS`: ETHUSDC raus, BNBUSDC rein)
- Geändert: `app/aitra/config.py` (neues Feld `market_symbols`, Vorgabe `BTCUSDC,BNBUSDC`)
- Test: `app/tests/test_store.py` (behält die Kerzen-/Spec-Tests)
- Test neu: `app/tests/test_store_run.py`
- Test neu: `app/tests/test_modulgroesse.py`
- Test geändert: `app/tests/test_money.py`, `app/tests/test_config.py`,
  `app/tests/test_execute.py`, `app/tests/test_ledger.py`, `app/tests/test_risk.py`,
  `app/tests/test_replay.py` (jeweils `ETHUSDC` → `BNBUSDC`)
- Geändert: `docs/specs/2026-09-20-teilprojekt-a-marktdaten-ledger-benchmark.md`
  (Abschnitte 2.2, 2.3/K-2, 3.1b, 4.1, 6.3, 18 und Kriterium A-19b)

**Schnittstellen:**
- Nutzt: `aitra.money` (`to_text`, `from_text`, `DP`, `SymbolSpec`), `aitra.db`
- Stellt bereit:
  - `store.CandleRow(symbol, interval, open_time, close_time, open, high, low, close, volume, source, fetched_at)`
  - `store.upsert_candles(conn, rows: Sequence[CandleRow]) -> None`
  - `store.get_candles(conn, symbol, interval, start_ms=None, end_ms=None, limit=500) -> list[CandleRow]`
  - `store.prune_candles(conn, symbol, interval, retention_days) -> int`
  - `store.upsert_symbol_spec(conn, spec: money.SymbolSpec, source: str, fetched_at: str) -> None`
  - `store.get_symbol_spec(conn, symbol) -> money.SymbolSpec | None`
  - `store_run.create_run(conn, run_id, kind, started_at, code_version, params_json="{}") -> None`
  - `store_run.finish_run(conn, run_id, finished_at) -> None`
  - `store_run.insert_fill(conn, *, run_id, decision_id, symbol, side, candle_open_time, price, qty, gross_quote, fee, net_quote, cash_after, fee_bps, slippage_bps, ts, commit=True) -> int`
  - `store_run.get_fills(conn, run_id) -> list[dict]`
  - `store_run.upsert_position(conn, *, run_id, symbol, qty, avg_price, realized_pnl, updated_at, commit=True) -> None`
  - `store_run.get_positions(conn, run_id) -> dict[str, dict]`
  - `store_run.EquityPoint(run_id, ts_ms, equity, cash, benchmark_equity, exposure_pct)`
  - `store_run.append_equity_points(conn, punkte: Sequence[EquityPoint]) -> None`
  - `store_run.get_equity_curve(conn, run_id, limit=500) -> list[dict]`
  - `store_run.mark_decision_pending(conn, decision_id, pending_since_ms, ref_price) -> None`
    *(Aufgabe 2 erweitert die Signatur um `base_qty` — hier noch unverändert)*
  - `store_run.resolve_decision(conn, decision_id, fill_id, commit=True) -> None`
  - `store_run.reject_decision(conn, decision_id, code, reason) -> None`
  - `store_run.expire_decision(conn, decision_id) -> None`
  - `store_run.get_pending_decisions(conn, run_id=None) -> list[dict]`
  - `money.BUILTIN_SPECS: dict[str, SymbolSpec]` mit **`BTCUSDC`** und **`BNBUSDC`**
  - `config.Config.market_symbols: tuple[str, ...]` (Vorgabe `("BTCUSDC", "BNBUSDC")`)

---

- [ ] **Schritt 1: Den scheiternden Test für die Modulgröße schreiben**

```python
# app/tests/test_modulgroesse.py
from __future__ import annotations

from pathlib import Path

AITRA = Path(__file__).resolve().parent.parent / "aitra"
MARKE = 300  # harte Marke aus Spec 3.1b; 200 Zeilen sind nur der Richtwert


def test_kein_modul_ueber_der_300_zeilen_marke():
    """Spec 3.1b: 200 Zeilen sind Richtwert, ab 300 wird geteilt.

    Prueffläche: Die Schleife laeuft ueber ein Verzeichnis. Waere das Muster
    falsch oder das Verzeichnis leer, liefe sie leer durch und waere immer
    gruen - deshalb wird die Zahl der geprueften Dateien selbst zugesichert.
    """
    module = sorted(p for p in AITRA.glob("*.py") if p.name != "__init__.py")
    assert len(module) >= 11, f"Pruefflaeche zu klein: nur {len(module)} Module gefunden"

    zu_gross = {
        p.name: len(p.read_text().splitlines())
        for p in module
        if len(p.read_text().splitlines()) > MARKE
    }
    assert zu_gross == {}, f"ueber der 300-Zeilen-Marke: {zu_gross}"


def test_store_und_store_run_sind_wirklich_getrennt():
    """Die Naht aus Spec 3.1b: Marktdaten hier, Lauf und Ledger dort.

    Nicht nur 'store_run existiert', sondern auch: die Laufzugriffe sind aus
    store.py *verschwunden*. Ohne die zweite Zusicherung waere ein Copy-Paste,
    das beide Module mit allem fuellt, ebenfalls gruen.
    """
    from aitra import store, store_run

    marktdaten = ["CandleRow", "upsert_candles", "get_candles", "prune_candles",
                  "upsert_symbol_spec", "get_symbol_spec"]
    lauf = ["create_run", "finish_run", "insert_fill", "get_fills", "upsert_position",
            "get_positions", "EquityPoint", "append_equity_points", "get_equity_curve",
            "mark_decision_pending", "resolve_decision", "reject_decision",
            "expire_decision", "get_pending_decisions"]

    fehlend_markt = [n for n in marktdaten if not hasattr(store, n)]
    fehlend_lauf = [n for n in lauf if not hasattr(store_run, n)]
    assert fehlend_markt == [], f"fehlt in store.py: {fehlend_markt}"
    assert fehlend_lauf == [], f"fehlt in store_run.py: {fehlend_lauf}"

    uebriggeblieben = [n for n in lauf if hasattr(store, n)]
    verirrt = [n for n in marktdaten if hasattr(store_run, n)]
    assert uebriggeblieben == [], f"noch in store.py statt store_run.py: {uebriggeblieben}"
    assert verirrt == [], f"in store_run.py statt store.py: {verirrt}"
```

- [ ] **Schritt 2: Den scheiternden Test für BNBUSDC schreiben**

`app/tests/test_money.py`: die Funktion `test_builtin_specs_kennen_beide_symbole`
(Zeile 53-58) **ersetzen** durch:

```python
def test_builtin_specs_kennen_btcusdc_und_bnbusdc():
    """Der Nutzer handelt BTC/USDC und BNB/USDC. ETHUSDC ist raus - und muss
    raus sein, sonst bemisst ein Lauf gegen eine Losgroesse, die niemand handelt."""
    assert set(money.BUILTIN_SPECS) == {"BTCUSDC", "BNBUSDC"}
    for sym in ("BTCUSDC", "BNBUSDC"):
        spec = money.BUILTIN_SPECS[sym]
        assert spec.quote == "USDC"
        assert spec.min_notional == Decimal("5")
        assert spec.tick_size > 0 and spec.step_size > 0
        assert spec.base_precision == 8 and spec.quote_precision == 8


def test_bnbusdc_losgroesse_am_2026_09_21_abgefragt():
    """Die vier Filterwerte, am 2026-09-21 von Binance abgefragt (Planungskopf).

    stepSize ist 0,001 - nicht 0,0001 wie bei ETHUSDC. Genau diese Zahl bestimmt
    ueber effective_min_notional die kleinste garantiert durchgehende Order.
    """
    spec = money.BUILTIN_SPECS["BNBUSDC"]
    assert spec.base == "BNB" and spec.quote == "USDC"
    assert spec.tick_size == Decimal("0.01")
    assert spec.step_size == Decimal("0.001")
    assert spec.min_qty == Decimal("0.001")
    assert spec.min_notional == Decimal("5")

    # Preis am 2026-09-21: 789,71 USDC
    eff = spec.effective_min_notional(Decimal("789.71"))
    assert eff == Decimal("5") + Decimal("0.001") * Decimal("789.71")
    assert eff == Decimal("5.78971")
    assert Decimal("5.78") < eff < Decimal("5.79")
```

Und in `app/tests/test_config.py` anhängen:

```python
def test_market_symbols_vorgabe_ist_btc_und_bnb(monkeypatch, tmp_path):
    """Die Vorgabe muss zu BUILTIN_SPECS passen - ein Symbol ohne Spec fuehrt
    zur Laufzeit zu Rejection(NO_SPEC) statt zu einem Fehler beim Start."""
    from aitra import money
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_TOKEN", "x" * 32)
    monkeypatch.delenv("MARKET_SYMBOLS", raising=False)
    cfg = load()
    assert cfg.market_symbols == ("BTCUSDC", "BNBUSDC")
    assert all(s in money.BUILTIN_SPECS for s in cfg.market_symbols)


def test_market_symbols_aus_der_umgebung_und_grenzen(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_TOKEN", "x" * 32)
    monkeypatch.setenv("MARKET_SYMBOLS", " btcusdc , bnbusdc ")
    assert load().market_symbols == ("BTCUSDC", "BNBUSDC")

    monkeypatch.setenv("MARKET_SYMBOLS", "BTC/USDC")
    with pytest.raises(ConfigError):
        load()

    monkeypatch.setenv("MARKET_SYMBOLS", "")
    with pytest.raises(ConfigError):
        load()

    monkeypatch.setenv("MARKET_SYMBOLS", "A1USDC,A2USDC,A3USDC,A4USDC,A5USDC,A6USDC")
    with pytest.raises(ConfigError):
        load()
```

- [ ] **Schritt 3: Tests laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q \
   tests/test_modulgroesse.py tests/test_money.py tests/test_config.py'
```

Erwartet, wörtlich zu protokollieren:
- `test_kein_modul_ueber_der_300_zeilen_marke` → `AssertionError: ueber der 300-Zeilen-Marke: {'store.py': 318}`
- `test_store_und_store_run_sind_wirklich_getrennt` → `ModuleNotFoundError: No module named 'aitra.store_run'`
- `test_builtin_specs_kennen_btcusdc_und_bnbusdc` → `AssertionError: assert {'BTCUSDC', 'ETHUSDC'} == {'BTCUSDC', 'BNBUSDC'}`
- `test_bnbusdc_losgroesse_am_2026_09_21_abgefragt` → `KeyError: 'BNBUSDC'`
- `test_market_symbols_vorgabe_ist_btc_und_bnb` → `AttributeError: 'Config' object has no attribute 'market_symbols'`

- [ ] **Schritt 4: `money.py` — BUILTIN_SPECS umstellen**

In `app/aitra/money.py` den Kommentarblock und den ETHUSDC-Eintrag ersetzen:

```python
# BTCUSDC am 2026-09-20, BNBUSDC am 2026-09-21 von
# api.binance.com/api/v3/exchangeInfo abgelesen.
# binance.py (Aufgabe 3) ueberschreibt sie zur Laufzeit ueber store.upsert_symbol_spec;
# hier stehen sie, damit die Engine vollstaendig ohne Netz testbar bleibt.
BUILTIN_SPECS: dict[str, SymbolSpec] = {
    "BTCUSDC": SymbolSpec(
        symbol="BTCUSDC", base="BTC", quote="USDC",
        tick_size=Decimal("0.01"), step_size=Decimal("0.00001"),
        min_qty=Decimal("0.00001"), min_notional=Decimal("5"),
        base_precision=8, quote_precision=8,
    ),
    "BNBUSDC": SymbolSpec(
        symbol="BNBUSDC", base="BNB", quote="USDC",
        tick_size=Decimal("0.01"), step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"), min_notional=Decimal("5"),
        base_precision=8, quote_precision=8,
    ),
}
```

- [ ] **Schritt 5: `config.py` — `market_symbols` ergänzen**

`Config` bekommt **hinten** ein Feld mit Vorgabewert (Befund B-5 — `test_risk.py:11`,
`test_api.py:11`, `test_api.py:48` konstruieren positional):

```python
@dataclass(frozen=True)
class Config:
    starting_balance: Decimal
    max_position_pct: float
    max_daily_loss_pct: float
    max_total_exposure_pct: float
    data_dir: Path
    admin_token: str
    trading_mode: str = "PAPER"
    live_locked: bool = True
    binance_base_url: str = "https://api.binance.com"
    market_symbols: tuple[str, ...] = ("BTCUSDC", "BNBUSDC")
```

Dazu der Leser, oberhalb von `load()`:

```python
def _symbols(name: str = "MARKET_SYMBOLS", default: str = "BTCUSDC,BNBUSDC") -> tuple[str, ...]:
    """1 bis 5 Symbole, je gegen SYMBOL_RE geprueft (Spec 18, Spec 11.2).

    Die Pruefung passiert hier und nicht erst in der SQL-Schicht: ein Symbol,
    das erst zur Laufzeit auffaellt, faellt im Poller-Thread auf - also dort,
    wo niemand hinsieht.
    """
    from .risk import SYMBOL_RE  # lokal: risk.py importiert config.py (Zyklus)

    roh = os.getenv(name, default).strip()
    teile = tuple(s.strip().upper() for s in roh.split(",") if s.strip())
    if not 1 <= len(teile) <= 5:
        raise ConfigError(f"{name}={roh!r} muss 1 bis 5 Symbole nennen, hat {len(teile)}")
    for s in teile:
        if not SYMBOL_RE.match(s):
            raise ConfigError(f"{name}: Symbol {s!r} ungültig")
    return teile
```

und in `load()` vor dem `return`:

```python
    market_symbols = _symbols()
```

sowie im `Config(...)`-Aufruf `market_symbols=market_symbols,` als letztes Argument.

- [ ] **Schritt 6: `store.py` teilen**

`app/aitra/store.py` behält Kopf, Importe und **nur** die Marktdaten-Funktionen. Neuer
Docstring:

```python
"""Lese-/Schreibzugriff auf die Marktdaten-Tabellen (candles, symbol_specs).

Die Naht zu store_run.py stammt aus Spec 3.1b: Marktdaten hier, alles
Laufbezogene (runs, fills, positions, equity_curve, decisions) dort. Vor der
Teilung lag store.py mit 318 Zeilen ueber der harten 300er-Marke.

Geld wandert ausschliesslich als TEXT durch SQLite (E-007). store.py und
store_run.py sind die einzigen Stellen, die zwischen Decimal und TEXT wandeln
(money.to_text/from_text). Keine Uhr: jeder Zeitstempel kommt als Parameter.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from . import money

DP = money.DP
```

Danach unverändert: `CandleRow`, `upsert_candles`, `get_candles`, `prune_candles`,
`upsert_symbol_spec`, `get_symbol_spec`. Alles ab `create_run` wird **gelöscht**.

- [ ] **Schritt 7: `store_run.py` anlegen**

```python
"""Lese-/Schreibzugriff auf die lauf- und ledgerbezogenen Tabellen.

Gegenstueck zu store.py (Marktdaten). Hier liegen runs, fills, positions,
equity_curve und die Lauf-Spalten von decisions. Die Naht ist in Spec 3.1b
beschrieben; sie wurde gezogen, weil store.py mit 318 Zeilen ueber der harten
300er-Marke lag.

Geld wandert ausschliesslich als TEXT durch SQLite (E-007), gewandelt nur ueber
money.to_text/from_text. Keine Uhr: ts, started_at, finished_at und updated_at
kommen als Parameter vom Aufrufer.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from . import money

DP = money.DP
```

Darunter **wortgleich** aus der alten `store.py` übernehmen (nur der Modulkopf ist neu):
`create_run`, `finish_run`, `insert_fill`, `get_fills`, `upsert_position`, `get_positions`,
`EquityPoint`, `_EQUITY_SQL`, `_equity_row`, `append_equity_points`, `get_equity_curve`,
`mark_decision_pending`, `resolve_decision`, `reject_decision`, `expire_decision`,
`get_pending_decisions`. Kein Verhalten ändern — das ist eine reine Verschiebung.

- [ ] **Schritt 8: Aufrufstellen nachziehen**

`app/aitra/execute.py`:
- Importzeile `from . import db, money, store` → `from . import db, money, store_run`
  (`store` wird in execute.py nicht mehr gebraucht — nachprüfen mit
  `grep -n "store\." app/aitra/execute.py`)
- alle 13 Vorkommen `store.` → `store_run.`

`app/aitra/replay.py`:
- Importzeile `from . import config, db, money, store` → `from . import config, db, money, store, store_run`
  (`store.get_candles` bleibt in `_load_candles_from_db`)
- `store.create_run` → `store_run.create_run` (2×)
- `store.finish_run` → `store_run.finish_run` (2×)
- `store.EquityPoint` → `store_run.EquityPoint` (2×)
- `store.append_equity_points` → `store_run.append_equity_points` (2×)
- Der Kommentar in Zeile 148-151, der `store.finish_run()` nennt, wird zu `store_run.finish_run()`

`app/aitra/marketdata.py`: unverändert (nutzt nur `store.get_candles`).

Gegenprobe, muss **0 Zeilen** liefern:

```bash
grep -rn "store\.\(create_run\|finish_run\|insert_fill\|get_fills\|upsert_position\|get_positions\|EquityPoint\|append_equity_points\|get_equity_curve\|mark_decision_pending\|resolve_decision\|reject_decision\|expire_decision\|get_pending_decisions\)" app/aitra
```

- [ ] **Schritt 9: Testdateien nachziehen**

`app/tests/test_store.py` behält: `_conn`, `_row`, `test_candles_geld_steht_als_text_a13`,
`test_candles_upsert_ist_idempotent_und_liest_decimal_zurueck`, `test_prune_candles_a16`,
`test_symbol_spec_roundtrip`. Import bleibt `from aitra import db, money, store`.

`app/tests/test_store_run.py` ist neu und übernimmt **wortgleich** (nur `store.` → `store_run.`
und `"ETHUSDC"` → `"BNBUSDC"` in Zeile 139 der alten Datei):
`test_fills_geld_steht_als_text_a13`, `test_positions_und_equity_curve_runtrip`,
`test_pending_decision_lebenszyklus`, `test_get_fills_reihenfolge_geld_exakt_und_run_isolation`,
`test_finish_run_setzt_finished_at`, `test_expire_decision_entfernt_aus_pending`.
Kopf der neuen Datei:

```python
# app/tests/test_store_run.py
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from aitra import db, money, store_run


def _conn(tmp_path: Path):
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    return conn
```

Symbolumstellung in den Bestandstests, rein mechanisch:

| Datei | Änderung |
|---|---|
| `app/tests/test_execute.py:19` | `SPECS = {"BTCUSDC": BTC, "BNBUSDC": money.BUILTIN_SPECS["BNBUSDC"]}` |
| `app/tests/test_execute.py:119-136` | `eth_candle` → `bnb_candle`, `"ETHUSDC"` → `"BNBUSDC"`, Preis `2631.77` → `789.71` |
| `app/tests/test_execute.py:397,401` | `basispreise = {"BTCUSDC": Decimal("81287.03"), "BNBUSDC": Decimal("789.71")}` |
| `app/tests/test_ledger.py:15-16` | `BNB = money.BUILTIN_SPECS["BNBUSDC"]`, `SPECS = {"BTCUSDC": BTC, "BNBUSDC": BNB}` |
| `app/tests/test_ledger.py:173-265` | alle `"ETHUSDC"` → `"BNBUSDC"` |
| `app/tests/test_risk.py:43,64,71` | `"ETHUSDC"` → `"BNBUSDC"` |
| `app/tests/test_replay.py:743,748` | `"ETHUSDC"` → `"BNBUSDC"` |

`app/tests/test_execute.py:427-473` (`test_resolve_pending_bewertet_auch_das_andere_symbol`)
wird in **Aufgabe 2** ersetzt — hier nur die Symbolnamen umstellen, damit der Lauf grün bleibt.

> **Wenn ein Bestandstest nach der reinen Umbenennung numerisch scheitert** (BNBUSDC hat mit
> 0,001 eine 10× gröbere Losgröße als ETHUSDC), ist das **zu melden, nicht zu glätten**. Die
> Schwelle im Test nachzuziehen, damit sie passt, ist genau der Griff, den Prime Directive 4
> verbietet.

- [ ] **Schritt 10: Tests laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q'
```

Erwartet: **≥ 160 passed**, 0 failed (157 Bestand + 2 Modulgröße + 1 BNB-Losgröße
+ 2 `market_symbols`, minus/plus die Umbenennungen). Zusätzlich messen und notieren:

```bash
wc -l app/aitra/store.py app/aitra/store_run.py
```
Erwartet: beide **< 200**, Summe etwa 335 (der neue Modulkopf kostet ~10 Zeilen).

- [ ] **Schritt 11: Rot-Nachweis führen (zwei Nachweise)**

*Nachweis 1 — die 300er-Marke greift wirklich.* An `app/aitra/store.py` unten anhängen:
```python
# --- Rot-Nachweis, danach loeschen ---
```
gefolgt von 200 Zeilen `#`. Test laufen lassen.
Erwartet: `test_kein_modul_ueber_der_300_zeilen_marke` schlägt fehl mit
`AssertionError: ueber der 300-Zeilen-Marke: {'store.py': 3xx}`. Ausgabe zeigen, Zeilen
löschen, Test erneut grün.

*Wäre der Test ohne den Fehler grün gewesen?* Ja — Schritt 10 hat ihn grün gemessen, und der
Fehler wirkt genau auf die gemessene Größe (Zeilenzahl von `store.py`), nicht auf eine
Nebenbedingung.

*Nachweis 2 — die BNB-Losgröße ist wirklich 0,001.* In `money.py` bei `BNBUSDC`
`step_size=Decimal("0.001")` auf `Decimal("0.0001")` ändern (der alte ETHUSDC-Wert).
Test laufen lassen. Erwartet: `test_bnbusdc_losgroesse_am_2026_09_21_abgefragt` schlägt
zweimal fehl —
`AssertionError: assert Decimal('0.0001') == Decimal('0.001')` in der Filterzusicherung und,
wenn man diese Zeile überspringt, `assert Decimal('5.078971') == Decimal('5.78971')`.
Ausgabe zeigen, zurücknehmen, Test erneut grün.

- [ ] **Schritt 12: Spec nachziehen**

In `docs/specs/2026-09-20-teilprojekt-a-marktdaten-ledger-benchmark.md`:

| Stelle | alt | neu |
|---|---|---|
| 2.2, Zeile „ETHUSDC `tickSize` …" | `0.01` / `0.0001` / `0.0001` / `5.00` | **BNBUSDC** `0.01` / **`0.001`** / **`0.001`** / `5.00`, Quelle `GET /api/v3/exchangeInfo?symbol=BNBUSDC`, abgefragt **2026-09-21** |
| 2.2, Zeile „ETHUSDC Bid/Ask" | `2631.77` / `2631.78` | **BNBUSDC** `789.70` / `789.71` → Spanne 0,01 USDC = **0,127 bp** |
| 2.2, Zeile „ETHUSDC Tiefe" | `askQty 15.5725 ETH` | als **nicht neu erhoben** kennzeichnen: „BNBUSDC-Tiefe am 2026-09-21 nicht abgefragt — K-4 stützt sich auf BTCUSDC" |
| 2.3, Kopfzeile | `Symbole **BTCUSDC, ETHUSDC**` | `Symbole **BTCUSDC, BNBUSDC**` |
| 2.3, K-2-Tabelle, ETH-Zeile | `0,2632 USDC` / `0,0263 %` / `0,0026 %` / `5,3 %` | **BNBUSDC** `step × preis = 0,001 × 789,71 =` **`0,7897 USDC`** / **`0,0790 %`** / **`0,0079 %`** / **`15,8 %`** |
| 2.3, K-3 | `BTCUSDC: 5,82 USDC · ETHUSDC: 5,27 USDC` | `BTCUSDC: 5,82 USDC · **BNBUSDC: 5,79 USDC**` |
| 2.3, K-4 | Halb-Spannen `0,062 bp (BTC) und 0,019 bp (ETH)` | `0,062 bp (BTC) und **0,063 bp (BNB)**` |
| 3.1b, Tabelle | `store.py 324` mit dem Konflikt-Absatz | `store.py` und `store_run.py` je unter 200, Konflikt-Absatz durch „**aufgelöst in A2, Aufgabe 1**" ersetzen |
| 4.1, Kommentar `step_size` | `0.00001 (BTC) | 0.0001 (ETH)` | `0.00001 (BTC) | 0.001 (BNB)` |
| 6.3, `SLIPPAGE_BPS` | `0,019 bp (ETH)` | `0,063 bp (BNB)` |
| 12, **A-19b** | `MARKET_SYMBOLS=BTCUSDC,ETHUSDC` | `MARKET_SYMBOLS=BTCUSDC,BNBUSDC` |
| 18, `MARKET_SYMBOLS` | Vorgabe `BTCUSDC,ETHUSDC` | Vorgabe **`BTCUSDC,BNBUSDC`** |
| 12, A-2-Beispielausgabe | `(BUY ETHUSDC, OK)` | `(BUY BNBUSDC, OK)` |

Gegenprobe, muss **0** liefern:
```bash
grep -c "ETHUSDC" docs/specs/2026-09-20-teilprojekt-a-marktdaten-ledger-benchmark.md
```

- [ ] **Schritt 13: Commit**

```bash
git add app/aitra/store.py app/aitra/store_run.py app/aitra/execute.py app/aitra/replay.py \
        app/aitra/money.py app/aitra/config.py app/tests/ \
        docs/specs/2026-09-20-teilprojekt-a-marktdaten-ledger-benchmark.md
git commit -m "refactor(store): Naht Marktdaten/Lauf gezogen; Symbole auf BTCUSDC+BNBUSDC

store.py lag mit 318 Zeilen ueber der harten 300er-Marke (Spec 3.1b). Neue
Datei store_run.py nimmt runs, fills, positions, equity_curve und die
Lauf-Spalten von decisions auf; store.py behaelt candles und symbol_specs.
Reine Verschiebung, kein Verhalten geaendert.

BUILTIN_SPECS: ETHUSDC raus, BNBUSDC rein (stepSize 0,001, am 2026-09-21
abgefragt). MARKET_SYMBOLS-Vorgabe BTCUSDC,BNBUSDC. Spec-Zahlen nachgezogen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Aufgabe 2: E-010 auflösen — die Order wird gespeichert, nicht neu bemessen

**Macht grün:** A-7b bleibt grün (vier Messpunkte), A-8 wird **erreichbar** (gemessen wird
A-8 selbst in Aufgabe 5). Behebt den Blocker aus `docs/entscheidungen/E-010`, beide
Ausprägungen: die Bewertung gegen die Füllkerze **und** das leere `_last_marks` nach einem
Neustart.

**Dateien:**
- Geändert: `app/aitra/db.py` (Migration 4 als Index 3 → `schema_version` **4**)
- Geändert: `app/aitra/store_run.py` (`mark_decision_pending` bekommt `base_qty`;
  `resolve_decision`, `reject_decision`, `expire_decision` räumen die neue Spalte mit auf)
- Geändert: `app/aitra/execute.py` (`execute_proposal` speichert die Order,
  `resolve_pending` bemisst nicht mehr)
- Test geändert: `app/tests/test_db.py` (Schemaversion 3 → 4, neuer Migrationstest)
- Test geändert: `app/tests/test_execute.py` (drei Tests ersetzt, einer neu, einer erweitert)
- Geändert: `docs/entscheidungen/E-010-resolve-pending-bewertet-mit-der-fuellkerze.md`
  (Status auf „aufgelöst", Weg A, mit Datum und Commit)

**Schnittstellen:**
- Nutzt: `store_run.get_pending_decisions/reject_decision/resolve_decision` (Aufgabe 1),
  `ledger.Ledger.apply(order, candle_next) -> Fill | Rejection`,
  `ledger.Order(symbol, side, base_qty)`, `money.from_text(s) -> Decimal`
- Stellt bereit:
  - `store_run.mark_decision_pending(conn, decision_id, pending_since_ms, ref_price: Decimal, base_qty: Decimal) -> None`
  - `execute.resolve_pending(ctx, candle) -> list[Fill]` — **ruft `size_order()` nicht mehr auf
    und ruft `Ledger.mark()` nicht mehr auf**
  - `db.MIGRATIONS` mit 4 Einträgen, `db.migrate(conn) -> 4`
  - neue Ablehnungscodes an schwebenden Zeilen: `"KILL_SWITCH"`, `"NO_BASE_QTY"`

---

- [ ] **Schritt 1: Die scheiternden Tests schreiben**

In `app/tests/test_db.py` die drei Zusicherungen `assert version == 3` bzw.
`assert db.migrate(conn) == 3` auf `4` ändern und anhängen:

```python
def test_migration_4_ist_nachtraeglich_und_nullbar(tmp_path: Path):
    """Migration 4 (pending_base_qty) laeuft auf einer DB, die auf Version 3 steht
    und eine schwebende Zeile enthaelt. Die neue Spalte ist nullbar; die
    Bestandszeile behaelt ihren pending_ref_price und bekommt NULL als Menge -
    execute.resolve_pending() lehnt sie spaeter mit NO_BASE_QTY ab, statt sie
    still falsch zu bemessen."""
    conn = db.connect(tmp_path / "a.db")
    for sql in db.MIGRATIONS[:3]:
        conn.executescript(sql)
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (3)")
    conn.commit()
    did = db.add_decision(conn, symbol="BTCUSDC", action="BUY", reason="alt", approved=1)
    conn.execute(
        "UPDATE decisions SET pending_since_ms = 900000, pending_ref_price = '81287.03000000' "
        "WHERE id = ?", (did,),
    )
    conn.commit()

    assert db.migrate(conn) == 4
    row = conn.execute(
        "SELECT reason, pending_since_ms, pending_ref_price, pending_base_qty "
        "FROM decisions WHERE id = ?", (did,),
    ).fetchone()
    assert row["reason"] == "alt"
    assert row["pending_since_ms"] == 900000
    assert row["pending_ref_price"] == "81287.03000000"
    assert row["pending_base_qty"] is None
```

In `app/tests/test_execute.py`: die Funktion
`test_resolve_pending_bemisst_mit_dem_ref_price_des_vorschlags` (Zeile 308-351)
**vollständig ersetzen** durch:

```python
def test_resolve_pending_ruft_size_order_nicht_mehr_auf_e010(tmp_path):
    """E-010, Weg A: Beim Aufloesen wird weder neu bewertet noch neu bemessen.

    Vorgeschichte: resolve_pending() rief size_order() mit dem gespeicherten
    pending_ref_price auf, bewertete dabei aber ueber Ledger.mark() gegen die
    FUELLKERZE. equity (ueber target_quote) und cash (ueber INSUFFICIENT_CASH)
    hingen damit an einem Preis, den die Entscheidung nicht kennen konnte -
    dieselbe Entscheidung ergab live eine andere Menge als im Replay, und A-8
    ("drei Quellen, ein Hash") war konstruktionsbedingt unerreichbar.

    Der Spion zaehlt Aufrufe. Er darf bei 0 bleiben - und die Pruefflaeche ist
    der echte Fill daneben: ohne ihn zaehlte ein Spion, der nie etwas zu sehen
    bekam, dasselbe wie ein korrekter Livepfad.

    Die Fuellkerze traegt bewusst einen ganz anderen Preis (99.999) als der
    Vorschlag (81.287,03), damit eine Verwechslung sofort auffaellt.
    """
    ctx = _ctx(tmp_path)
    ref = Decimal("81287.03")
    pending = execute_proposal(
        Proposal("BTCUSDC", "BUY", 8), ctx, marks={}, ts_ms=900_000, ref_price=ref,
        start_of_day_equity=Decimal("10000"), next_candle=None,
    )
    assert pending.status == "pending_fill"

    # Migration 4: die fertig bemessene Menge steht kanonisch als TEXT in der Zeile (E-007)
    row = ctx.conn.execute(
        "SELECT pending_ref_price, pending_base_qty FROM decisions WHERE id=?",
        (pending.decision_id,),
    ).fetchone()
    assert row["pending_ref_price"] == "81287.03000000"
    gespeicherte_menge = money.from_text(row["pending_base_qty"])
    assert gespeicherte_menge > Decimal("0")

    fuellkerze = _candle(1_800_000, "99999.00")
    spion = {"aufrufe": 0}
    from aitra.execute import size_order as _echtes_size_order

    def spy(*args, **kwargs):
        spion["aufrufe"] += 1
        return _echtes_size_order(*args, **kwargs)

    with patch("aitra.execute.size_order", side_effect=spy):
        fills = resolve_pending(ctx, fuellkerze)

    # Pruefflaeche zuerst: ohne echten Fill misst der Spion nichts.
    assert len(fills) == 1, "kein Fill - der Spion haette auch bei kaputtem Code 0 gezaehlt"
    assert spion["aufrufe"] == 0, (
        f"size_order() wurde beim Aufloesen {spion['aufrufe']}x aufgerufen - "
        f"E-010 Weg A verlangt 0"
    )
    # Gebucht wurde exakt die gespeicherte Menge, nicht eine neu berechnete.
    assert fills[0].qty == gespeicherte_menge
    # Gefuellt wird trotzdem zum Preis der Folgekerze (E-006).
    assert fills[0].candle_open_time == 1_800_000
    assert fills[0].price > fuellkerze.open  # 99.999 + Slippage
```

Die Funktion `test_resolve_pending_verwirft_zeilen_ohne_gespeicherten_ref_price`
(Zeile 353-374) **ersetzen** durch:

```python
def test_resolve_pending_verwirft_zeilen_ohne_gespeicherte_menge(tmp_path):
    """Bestandszeilen aus einer DB vor Migration 4 haben pending_base_qty NULL.

    Die Menge nachtraeglich zu berechnen waere genau die Neubemessung, die
    E-010 beseitigt - also wird die Zeile abgelehnt, mit eigenem Code. Nicht
    PENDING_EXPIRED: der Vorschlag ist nicht verfallen, sondern nicht buchbar.
    """
    ctx = _ctx(tmp_path)
    pending = execute_proposal(
        Proposal("BTCUSDC", "BUY", 8), ctx, marks={}, ts_ms=900_000,
        ref_price=Decimal("81287.03"), start_of_day_equity=Decimal("10000"), next_candle=None,
    )
    ctx.conn.execute("UPDATE decisions SET pending_base_qty = NULL WHERE id = ?",
                      (pending.decision_id,))
    ctx.conn.commit()

    assert resolve_pending(ctx, _candle(1_800_000)) == []
    row = ctx.conn.execute(
        "SELECT approved, risk_code, pending_since_ms FROM decisions WHERE id=?",
        (pending.decision_id,),
    ).fetchone()
    assert row["risk_code"] == "NO_BASE_QTY"
    assert row["approved"] == 0
    assert row["pending_since_ms"] is None
    assert ctx.conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 0
```

Die Funktion `test_resolve_pending_bewertet_auch_das_andere_symbol` (Zeile 427-473)
**ersetzen** durch:

```python
def test_resolve_pending_braucht_keine_marktpreise_nach_neustart_e010(tmp_path):
    """Der zweite Befund aus E-010: _last_marks ist nach einem Neustart leer.

    Ein neu gestarteter Live-Prozess laedt Positionen aus dem Journal, aber
    Ledger._last_marks ist reiner In-Prozess-Zustand und beginnt leer. Bewertete
    resolve_pending() noch ueber Ledger.mark(), wuerde der allererste Fillversuch
    nach jedem Neustart mit "Kein Marktpreis fuer gehaltene Position ..." werfen,
    sobald mehr als ein Symbol gehalten wird. Weg A loest das mit: es wird gar
    nicht mehr bewertet.
    """
    ctx = _ctx(tmp_path)
    ctx.engine = RiskEngine(Config(Decimal("10000"), 100, 100, 100, tmp_path, "x" * 32))
    bnb_preis = Decimal("789.71")
    bnb_kerze = Candle(symbol="BNBUSDC", interval="15m", open_time=900_000, close_time=1_799_999,
                        open=bnb_preis, high=bnb_preis, low=bnb_preis, close=bnb_preis,
                        volume=Decimal("1"), closed=True)
    gekauft = execute_proposal(
        Proposal("BNBUSDC", "BUY", 40), ctx, marks={}, ts_ms=900_000, ref_price=bnb_preis,
        start_of_day_equity=Decimal("10000"), next_candle=bnb_kerze,
    )
    assert gekauft.status == "filled"
    assert ctx.ledger.position("BNBUSDC").qty > Decimal("0")

    schwebend = execute_proposal(
        Proposal("BTCUSDC", "BUY", 5), ctx,
        marks={"BNBUSDC": bnb_preis}, ts_ms=1_800_000, ref_price=Decimal("81287.03"),
        start_of_day_equity=Decimal("10000"), next_candle=None,
    )
    assert schwebend.status == "pending_fill"

    # Neustart nachstellen: das Ledger haelt die Position weiter (sie kaeme aus
    # dem Journal), aber die zuletzt gesehenen Marktpreise sind weg. Das ist
    # genau der Zustand eines frisch gestarteten Prozesses.
    ctx.ledger._last_marks.clear()
    assert ctx.ledger.last_marks == {}

    fills = resolve_pending(ctx, _candle(2_700_000))
    assert len(fills) == 1
    assert fills[0].symbol == "BTCUSDC"
    assert ctx.ledger.position("BNBUSDC").qty > Decimal("0")  # unangetastet


def test_resolve_pending_prueft_den_kill_switch_erneut(tmp_path):
    """Die einzige Ausnahme von der Paritaet, und sie ist eine Sicherheitsfunktion.

    Zwei Messpunkte an derselben Konstruktion: mit ausgeschaltetem Kill Switch
    fuellt der Vorschlag, mit eingeschaltetem nicht. Ohne den zweiten Punkt
    waere ein resolve_pending(), das grundsaetzlich nichts mehr bucht, ebenfalls
    gruen.
    """
    for kill, erwartete_fills, erwarteter_code in ((False, 1, None), (True, 0, "KILL_SWITCH")):
        ctx = _ctx(tmp_path / f"ks-{kill}")
        pending = execute_proposal(
            Proposal("BTCUSDC", "BUY", 8), ctx, marks={}, ts_ms=900_000,
            ref_price=Decimal("81287.03"), start_of_day_equity=Decimal("10000"),
            next_candle=None,
        )
        assert pending.status == "pending_fill"

        ctx.kill_switch = kill  # zwischen Entscheidung und Ausfuehrung ausgeloest
        fills = resolve_pending(ctx, _candle(1_800_000))
        assert len(fills) == erwartete_fills, f"kill_switch={kill}"
        anzahl = ctx.conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"]
        assert anzahl == erwartete_fills
        if erwarteter_code is not None:
            row = ctx.conn.execute(
                "SELECT approved, risk_code, pending_since_ms FROM decisions WHERE id=?",
                (pending.decision_id,),
            ).fetchone()
            assert row["risk_code"] == erwarteter_code
            assert row["approved"] == 0
            assert row["pending_since_ms"] is None
```

`_ctx()` muss dafür ein Verzeichnis anlegen können — in `app/tests/test_execute.py` die
Hilfsfunktion in Zeile 58 um eine Zeile ergänzen:

```python
def _ctx(tmp_path: Path, clock_ms: int = 900_000, kill_switch: bool = False) -> ExecutionContext:
    tmp_path.mkdir(parents=True, exist_ok=True)   # erlaubt _ctx(tmp_path / "unterordner")
    conn = db.connect(tmp_path / "a.db")
    ...
```

Und `test_a7b_resolve_pending_fuellt_bei_ankunft_der_folgekerze` (Zeile 204-219) um zwei
Zusicherungen erweitern, direkt nach `assert pending.status == "pending_fill"`:

```python
    vorher = ctx.conn.execute(
        "SELECT pending_base_qty FROM decisions WHERE id=?", (pending.decision_id,)
    ).fetchone()["pending_base_qty"]
    assert vorher is not None, "Migration 4: die bemessene Menge muss beim Einstellen stehen"
```

und ganz am Ende der Funktion:

```python
    assert fills[0].qty == money.from_text(vorher)  # nicht neu bemessen (E-010)
```

- [ ] **Schritt 2: Tests laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q \
   tests/test_db.py tests/test_execute.py'
```

Erwartet, wörtlich zu protokollieren:
- `test_migration_4_ist_nachtraeglich_und_nullbar` →
  `sqlite3.OperationalError: no such column: pending_base_qty`
- `test_migration_2_erzeugt_alle_neuen_tabellen` → `assert 3 == 4`
- `test_resolve_pending_ruft_size_order_nicht_mehr_auf_e010` →
  `sqlite3.OperationalError: no such column: pending_base_qty`
- `test_resolve_pending_prueft_den_kill_switch_erneut` → `AssertionError: kill_switch=True`
  (heute füllt der Vorschlag trotz Kill Switch)

- [ ] **Schritt 3: Migration 4 in `db.py` ergänzen**

An `MIGRATIONS` als vierten Eintrag anhängen:

```python
    # 4 – die fertig bemessene Menge an der schwebenden Entscheidung (E-010, Weg A)
    #     Ohne diese Spalte muesste resolve_pending() beim Aufloesen neu bemessen und
    #     dafuer neu bewerten. equity und cash haengen dann an der Fuellkerze — an einem
    #     Preis, den die Entscheidung nicht kennen konnte. run_replay() bemisst dagegen
    #     bei t. Zwei verschiedene Mengen fuer dieselbe Entscheidung, entgegen E-001,
    #     und A-8 ("drei Quellen, ein Hash") waere unerreichbar.
    #     Geld steht als TEXT (E-007). Die Spalte ist nullbar; Zeilen aus einer aelteren
    #     DB behalten NULL und werden von resolve_pending() mit NO_BASE_QTY abgelehnt.
    #     Ruecknahme: ALTER TABLE decisions DROP COLUMN pending_base_qty; (SQLite >= 3.35)
    """
    ALTER TABLE decisions ADD COLUMN pending_base_qty TEXT;
    """,
```

- [ ] **Schritt 4: `store_run.py` — Menge mitschreiben und mit aufräumen**

```python
def mark_decision_pending(conn: sqlite3.Connection, decision_id: int, pending_since_ms: int,
                           ref_price: Decimal, base_qty: Decimal) -> None:
    """Legt einen genehmigten, bereits bemessenen Vorschlag schwebend ab (E-006/E-010).

    base_qty ist die zum *Vorschlagszeitpunkt* berechnete Ordermenge. Sie ist der
    eigentliche Inhalt dieser Zeile: resolve_pending() bucht sie unveraendert und
    bemisst nicht neu (E-010, Weg A).

    ref_price ist der zugehoerige Referenzpreis. Er wird weiter mitgefuehrt,
    aber nur noch als Journal- und Anzeigewert ("Vorschlag bei 81.287,03,
    gefuellt bei 81.310,00"). Die Bemessung haengt nicht mehr an ihm.
    """
    conn.execute(
        "UPDATE decisions SET pending_since_ms = ?, pending_ref_price = ?, "
        "pending_base_qty = ? WHERE id = ?",
        (pending_since_ms, money.to_text(ref_price, DP), money.to_text(base_qty, DP), decision_id),
    )
    conn.commit()
```

In `resolve_decision`, `reject_decision` und `expire_decision` jeweils
`pending_base_qty = NULL` in das `SET` aufnehmen — sonst bliebe an einer gebuchten oder
verfallenen Zeile eine Menge stehen, die nie wieder gemeint ist:

```python
# resolve_decision
"UPDATE decisions SET fill_id = ?, pending_since_ms = NULL, pending_ref_price = NULL, "
"pending_base_qty = NULL WHERE id = ?"

# reject_decision
"UPDATE decisions SET approved = 0, risk_code = ?, risk_reason = ?, "
"pending_since_ms = NULL, pending_ref_price = NULL, pending_base_qty = NULL WHERE id = ?"

# expire_decision
"UPDATE decisions SET pending_since_ms = NULL, pending_ref_price = NULL, "
"pending_base_qty = NULL, risk_code = 'PENDING_EXPIRED' WHERE id = ?"
```

- [ ] **Schritt 5: `execute.py` — Order speichern, beim Auflösen nur noch buchen**

Importzeile erweitern:

```python
from .ledger import Fill, Ledger, Order, Rejection
```

In `execute_proposal()` den Pending-Zweig ersetzen:

```python
    if next_candle is None:
        # E-006/E-010: Folgekerze liegt noch nicht vor -> die FERTIG BEMESSENE
        # Order wird abgelegt, nicht nur der Referenzpreis. resolve_pending()
        # bucht sie spaeter unveraendert.
        store_run.mark_decision_pending(
            ctx.conn, decision_id, pending_since_ms=ts_ms,
            ref_price=ref_price, base_qty=order.base_qty,
        )
        return ExecutionResult(decision_id, True, "OK",
                                "Order schwebt bis zur Folgekerze", status="pending_fill")
```

`resolve_pending()` vollständig ersetzen:

```python
def resolve_pending(ctx: ExecutionContext, candle: Candle) -> list[Fill]:
    """Bucht schwebende Vorschlaege fuer candle.symbol mit der nun vorliegenden Kerze.

    E-010 ist am 2026-09-21 ueber Weg A aufgeloest: Beim Aufloesen wird **nicht
    neu bewertet und nicht neu bemessen**. Die Menge wurde zum
    Vorschlagszeitpunkt berechnet und steht als decisions.pending_base_qty in
    der Zeile; hier wird nur noch der Kill Switch geprueft und dann gebucht.

    Warum: run_replay() bewertet, prueft und bemisst alles bei t und bucht auf
    t+1 — in einem Aufruf. Bemaesse der Livepfad beim Aufloesen neu, haengen
    equity (ueber target_quote) und cash (ueber INSUFFICIENT_CASH) an der
    Fuellkerze, also an einem Preis, den die Entscheidung nicht kennen konnte.
    Dieselbe Entscheidung ergaebe live eine andere Menge als im Replay, und A-8
    ("drei Quellen, ein Hash") waere konstruktionsbedingt unerreichbar.

    Zweite Wirkung, ausdruecklich gewollt: Ledger.mark() wird hier gar nicht
    mehr aufgerufen. Damit verschwindet der zweite Befund aus E-010 — ein frisch
    gestarteter Prozess hat ein leeres _last_marks, und mark() haette fuer jede
    aus dem Journal rekonstruierte Position sofort geworfen.

    Die Kill-Switch-Pruefung ist die einzige Ausnahme von der Paritaet, und sie
    ist eine Sicherheitsfunktion: ein Vorschlag, den der Kill Switch zwischen
    Entscheidung und Ausfuehrung einholt, darf nicht mehr fuellen. Im Zeitraffer
    gibt es diese Luecke nicht, weil Entscheidung und Ausfuehrung in derselben
    Iteration liegen.

    Kosten bei Irrtum (aus E-010 uebernommen): Schrumpft die Kasse zwischen t
    und t+1 durch einen Fill in einem anderen Symbol, lehnt Ledger.apply() mit
    INSUFFICIENT_CASH ab, statt die Order zu verkleinern. Richtig, aber der
    Vorschlag ist dann verloren. Tritt das gehaeuft auf, wird beim Aufloesen auf
    die verfuegbare Kasse gedeckelt.
    """
    expire_stale_pending(ctx)
    filled: list[Fill] = []
    for row in store_run.get_pending_decisions(ctx.conn, ctx.run_id):
        if row["symbol"] != candle.symbol:
            continue
        if ctx.kill_switch:
            store_run.reject_decision(
                ctx.conn, row["id"], "KILL_SWITCH",
                "Kill Switch aktiv – schwebender Vorschlag nicht gebucht",
            )
            continue
        if row["symbol"] not in ctx.specs:
            store_run.reject_decision(ctx.conn, row["id"], "NO_SPEC",
                                       f"Keine SymbolSpec für {row['symbol']}")
            continue
        roh_menge = row["pending_base_qty"]
        if roh_menge is None:
            # Zeile aus einer DB vor Migration 4. Die Menge nachtraeglich zu
            # berechnen waere genau die Neubemessung, die E-010 beseitigt.
            store_run.reject_decision(
                ctx.conn, row["id"], "NO_BASE_QTY",
                "Keine gespeicherte Ordermenge (DB vor Migration 4)",
            )
            continue
        order = Order(symbol=row["symbol"], side=str(row["action"]).upper(),
                       base_qty=money.from_text(roh_menge))
        fill = ctx.ledger.apply(order, candle)
        if isinstance(fill, Rejection):
            store_run.reject_decision(ctx.conn, row["id"], fill.code, fill.reason)
            continue
        _journal_fill(ctx, row["id"], fill)
        filled.append(fill)
    return filled
```

Den Modul-Docstring von `execute.py` in Zeile 5-7 anpassen: „… bis `resolve_pending()` eine
Folgekerze liefert" → „… bis `resolve_pending()` die gespeicherte Order mit einer Folgekerze
bucht (E-010, Weg A)".

- [ ] **Schritt 6: Tests laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q'
```

Erwartet: **0 failed**. `test_resolve_pending_ablehnung_ist_kein_verfall` bleibt grün und
misst danach den *Ledger*-Kassenwächter statt den aus `sizing.py` — das ist genau die in
E-010 angekündigte Verhaltensänderung und gehört in den Commit-Text.

- [ ] **Schritt 7: Rot-Nachweis führen (drei Nachweise)**

*Nachweis 1 — der Spion zählt wirklich.* In `resolve_pending()` vor `ctx.ledger.apply(...)`
den alten Pfad wieder einbauen:

```python
        marks = {**ctx.ledger.last_marks, candle.symbol: candle.open}
        valuation = ctx.ledger.mark(marks, ts_ms=ctx.clock.now_ms())
        proposal = Proposal(symbol=row["symbol"], action=row["action"],
                             position_pct=row["requested_position_pct"] or 0.0)
        order = size_order(proposal, valuation, ctx.specs[row["symbol"]],
                            money.from_text(row["pending_ref_price"]),
                            ctx.fee_bps, ctx.slippage_bps,
                            ctx.ledger.position(row["symbol"]).qty)
```

Erwartet: `test_resolve_pending_ruft_size_order_nicht_mehr_auf_e010` schlägt fehl mit
`AssertionError: size_order() wurde beim Aufloesen 1x aufgerufen - E-010 Weg A verlangt 0`.
*Wäre der Test ohne den Fehler grün gewesen?* Ja — Schritt 6 hat ihn grün gemessen, und die
Prüfflächen-Zusicherung `len(fills) == 1` steht **vor** der Spionzusicherung, kann sie also
nicht verdecken.

*Nachweis 2 — die Kill-Switch-Prüfung greift wirklich.* In `resolve_pending()` den Block
`if ctx.kill_switch: … continue` auskommentieren. Erwartet:
`test_resolve_pending_prueft_den_kill_switch_erneut` schlägt fehl mit
`AssertionError: kill_switch=True` und, eine Zeile davor, `assert 1 == 0`.
*Wäre der Test ohne den Fehler grün gewesen?* Ja — und der erste Schleifendurchlauf
(`kill=False`) beweist, dass der Test nicht durch pauschales Nichtbuchen grün wird.

*Nachweis 3 — der Neustart-Fall wird wirklich getroffen.* Dieselbe Änderung wie in
Nachweis 1, aber nur die beiden `marks`/`valuation`-Zeilen. Erwartet:
`test_resolve_pending_braucht_keine_marktpreise_nach_neustart_e010` schlägt fehl mit
`ValueError: Kein Marktpreis für gehaltene Position BNBUSDC (qty=...); mark() erhielt Preise für ['BTCUSDC']`.
Ausgabe zeigen, alle drei Änderungen zurücknehmen, volle Suite erneut grün.

- [ ] **Schritt 8: E-010 auf „aufgelöst" setzen**

`docs/entscheidungen/E-010-resolve-pending-bewertet-mit-der-fuellkerze.md`:

- Titel: `# E-010 — resolve_pending() bewertet mit der Füllkerze (aufgelöst)`
- Kopfblock: `**Status:** **aufgelöst am 2026-09-21 über Weg A** (Teilprojekt A2, Aufgabe 2)`
- Neuer Abschnitt direkt unter „## Die zwei Wege":

```markdown
## Auflösung (2026-09-21, Teilprojekt A2, Aufgabe 2)

**Gewählt: Weg A.** Migration 4 fügt `decisions.pending_base_qty` hinzu.
`execute_proposal()` legt die zum Vorschlagszeitpunkt bemessene Order dort ab;
`resolve_pending()` ruft weder `size_order()` noch `Ledger.mark()` auf, sondern
prüft den Kill Switch und reicht die gespeicherte Order an `Ledger.apply()`.

Damit sind **beide** Ausprägungen erledigt: die Bewertung gegen die Füllkerze und
das leere `_last_marks` nach einem Neustart — letzteres, weil beim Auflösen gar
keine vollständige `Valuation` mehr gebraucht wird.

**Die angekündigte Verhaltensänderung ist eingetreten:** Die Kassenprüfung liegt
jetzt vollständig in `Ledger.apply()`. Eine zwischen Vorschlag und Füllung
geschrumpfte Kasse führt zu `INSUFFICIENT_CASH` statt zu einer kleineren Order.
Gemessen an `test_resolve_pending_ablehnung_ist_kein_verfall`, der unverändert
grün bleibt und danach den Ledger-Wächter misst statt den aus `sizing.py`.

**Neue Ablehnungscodes an schwebenden Zeilen:** `KILL_SWITCH` (Kill Switch
zwischen Entscheidung und Ausführung ausgelöst) und `NO_BASE_QTY` (Zeile aus
einer DB vor Migration 4).
```

- [ ] **Schritt 9: Commit**

```bash
git add app/aitra/db.py app/aitra/store_run.py app/aitra/execute.py \
        app/tests/test_db.py app/tests/test_execute.py \
        docs/entscheidungen/E-010-resolve-pending-bewertet-mit-der-fuellkerze.md
git commit -m "fix(execute): E-010 ueber Weg A aufgeloest - Order speichern statt neu bemessen

Migration 4 legt decisions.pending_base_qty an. execute_proposal() speichert die
zum Vorschlagszeitpunkt bemessene Order; resolve_pending() ruft weder size_order()
noch Ledger.mark() auf, sondern prueft den Kill Switch und bucht.

Damit haengt die Ordergroesse im Livepfad nicht mehr an der Fuellkerze — A-8
(drei Quellen, ein Hash) wird erreichbar. Zugleich erledigt: das leere
_last_marks nach einem Neustart, das beim ersten Fill geworfen haette.

Verhaltensaenderung wie in E-010 angekuendigt: die Kassenpruefung liegt jetzt
vollstaendig in Ledger.apply(). Neue Codes: KILL_SWITCH, NO_BASE_QTY.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Aufgabe 3: `binance.py` — der einzige Netzzugang, gehärtet

**Macht grün:** **A-17** (zweite Schicht: die Allowlist im Client, fünf Messpunkte plus
`data-api.binance.vision`), **A-17b** (keine Weiterleitung, mit Zähler am Umleitungsziel).
Liefert den Gewichtszähler, mit dem Aufgabe 5 **A-17c** misst.

> **Widerspruch in der Spec, hier aufgelöst und gemeldet:** A-17b misst mit
> `http://127.0.0.1:1/` — eine URL, die Abschnitt 11.1 (nur HTTPS, Host-Allowlist) schon
> beim Bau des Clients ablehnt. Über die öffentliche Client-Schnittstelle ist der Fall
> **nicht erreichbar**. Deshalb stehen hier **zwei** Tests statt einem: die Allowlist wird
> am Client gemessen (und zählt dabei, dass gar kein Verbindungsversuch stattfand), die
> Weiterleitung am Opener, den `binance.build_opener()` liefert. Ein Wächter, der nur den
> Client prüft, sähe die Umleitungsschicht nie.

**Dateien:**
- Neu: `app/aitra/binance.py`
- Geändert: `app/aitra/marketdata.py` (`INTERVALS`, `interval_seconds()`)
- Test neu: `app/tests/test_binance.py`
- Test geändert: `app/tests/test_marketdata.py` (Intervalltabelle, Schwellenherleitung)

**Schnittstellen:**
- Nutzt: `config.ALLOWED_BINANCE_HOSTS` (Bestand, A1), `money.SymbolSpec`,
  `marketdata.Candle(symbol, interval, open_time, close_time, open, high, low, close, volume, closed)`
- Stellt bereit:
  - `marketdata.INTERVALS: dict[str, int]` — `{"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400,"1d":86400}`
  - `marketdata.interval_seconds(interval: str) -> int` (wirft `ValueError`)
  - `binance.build_opener() -> urllib.request.OpenerDirector`
  - `binance.BinanceError`, `BinanceMalformed`, `BinanceTooLarge`,
    `BinanceHTTPError(status)`, `BinanceRateLimited(status, retry_after_s)`
  - `binance.MAX_RESPONSE_BYTES = 2_097_152`, `TIMEOUT_S = 10.0`,
    `WEIGHT_KLINES = 2`, `WEIGHT_TIME = 1`, `WEIGHT_EXCHANGE_INFO = 20`
  - `binance.BinanceClient(base_url: str, *, timeout_s=TIMEOUT_S, max_bytes=MAX_RESPONSE_BYTES, opener=None)` mit
    - `weight_used -> int` (Eigenschaft), `reset_weight() -> None`
    - `server_time() -> int`
    - `klines(symbol: str, interval: str, *, server_time_ms: int, limit: int = 500, start_ms: int | None = None, end_ms: int | None = None) -> list[Candle]`
    - `exchange_info(symbol: str) -> money.SymbolSpec`

---

- [ ] **Schritt 1: Den scheiternden Test schreiben**

```python
# app/tests/test_binance.py
from __future__ import annotations

import json
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from aitra import binance, money
from aitra.binance import (BinanceClient, BinanceError, BinanceMalformed,
                            BinanceRateLimited, BinanceTooLarge)

BASIS = "https://api.binance.com"


class FakeAntwort:
    """Antwortobjekt der Attrappe - genau so viel, wie binance.py benutzt."""

    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None) -> None:
        self._body = body
        self._pos = 0
        self.status = status
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            d = self._body[self._pos:]
            self._pos = len(self._body)
            return d
        d = self._body[self._pos:self._pos + n]
        self._pos += len(d)
        return d

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


class FakeOpener:
    """Ersetzt urllib vollstaendig. KEIN Test in dieser Datei geht ins Netz.

    antworten: Pfad -> FakeAntwort, Exception oder Callable(url) -> FakeAntwort.
    """

    def __init__(self, antworten: dict) -> None:
        self.antworten = antworten
        self.aufrufe: list[str] = []
        self.requests: list[urllib.request.Request] = []
        self.timeout = None

    def open(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.aufrufe.append(url)
        self.requests.append(req)
        self.timeout = timeout
        a = self.antworten[urlsplit(url).path]
        if isinstance(a, BaseException):
            raise a
        if callable(a):
            a = a(url)
        return a


def _kerze(open_time: int, close_time: int, close: str = "81287.03") -> list:
    """Eine Binance-Kerzenzeile: 12 Felder in der Reihenfolge aus Spec 2.2."""
    return [open_time, "81287.00", "81300.00", "81200.00", close, "12.345",
            close_time, "1003000.0", 2500, "6.0", "500000.0", "0"]


def _body(obj) -> bytes:
    return json.dumps(obj).encode()


def _client(antworten: dict, **kw) -> tuple[BinanceClient, FakeOpener]:
    opener = FakeOpener(antworten)
    return BinanceClient(BASIS, opener=opener, **kw), opener


# ---------------------------------------------------------------- Allowlist

@pytest.mark.parametrize("url,erlaubt", [
    ("https://api.binance.com", True),
    ("https://data-api.binance.vision", True),
    ("http://api.binance.com", False),              # kein TLS
    ("https://api.binance.com.evil.example", False),  # Praefix-Falle
    ("https://evil.example.com", False),
    ("http://169.254.169.254", False),              # Cloud-Metadaten
])
def test_a17_allowlist_wird_exakt_verglichen(url, erlaubt):
    """A-17, zweite Schicht: dieselbe Pruefung wie in config.load(), aber im Client.

    Zweite Zusicherung: die Pruefung greift VOR jedem Verbindungsversuch. Ohne
    sie waere eine Umsetzung gruen, die erst nach dem Verbindungsaufbau prueft -
    der Port waere dann bereits angefasst.
    """
    opener = FakeOpener({})
    if erlaubt:
        BinanceClient(url, opener=opener)
    else:
        with pytest.raises(BinanceError):
            BinanceClient(url, opener=opener)
    assert opener.aufrufe == []


def test_allowlist_greift_auch_pro_anfrage_nicht_nur_beim_bau():
    """Eine Allowlist, die nur im Konstruktor prueft, waere zu umgehen, indem
    jemand _base nachtraeglich setzt. Die Pruefung liegt deshalb auch im
    Anfragepfad."""
    c, opener = _client({"/api/v3/time": FakeAntwort(_body({"serverTime": 1}))})
    c._base = "https://evil.example.com"
    with pytest.raises(BinanceError):
        c.server_time()
    assert opener.aufrufe == []


# ------------------------------------------------------- A-17b Weiterleitung

class _Umleiter(BaseHTTPRequestHandler):
    treffer = {"ziel": 0}

    def do_GET(self):
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/ziel")
            self.end_headers()
        else:
            _Umleiter.treffer["ziel"] += 1
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


@pytest.fixture
def umleitungsserver():
    _Umleiter.treffer["ziel"] = 0
    srv = HTTPServer(("127.0.0.1", 0), _Umleiter)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_a17b_keine_weiterleitung_wird_verfolgt(umleitungsserver):
    """A-17b: der Opener aus binance.py folgt keiner 3xx.

    Gemessen wird der ZAEHLER des Umleitungsziels, nicht nur die Ausnahme. Eine
    Ausnahme allein bewiese nicht, dass die zweite Anfrage unterblieben ist.

    Der Testserver spricht http auf 127.0.0.1 - das ist Loopback, kein Netz, und
    KEIN Widerspruch zur Allowlist: geprueft wird hier die Umleitungsschicht.
    Ueber die oeffentliche Client-Schnittstelle ist dieser Fall gar nicht
    erreichbar, weil die Allowlist vorher greift (siehe Test darueber).
    """
    start = f"http://127.0.0.1:{umleitungsserver.server_port}/start"
    opener = binance.build_opener()
    with pytest.raises(BinanceError):
        opener.open(start, timeout=5)
    assert _Umleiter.treffer["ziel"] == 0

    # Gegenprobe: mit dem Standardhandler WIRD die Umleitung verfolgt. Ohne sie
    # waere der Test auch dann gruen, wenn der Server gar nicht umleitete.
    urllib.request.build_opener().open(start, timeout=5).read()
    assert _Umleiter.treffer["ziel"] == 1


# ------------------------------------------------------------ Groessenlimit

def test_groessenlimit_greift_beidseitig():
    """Exakt an der Grenze noch gueltig, ein Byte darueber verworfen.

    Die Fuellung ist Leerraum am Ende des JSON - gueltiges JSON, aber sie
    verschiebt die Laenge um genau ein Byte.
    """
    kern = _body([_kerze(0, 899_999)])
    grenze = len(kern) + 50
    genau = kern + b" " * (grenze - len(kern))
    zuviel = kern + b" " * (grenze - len(kern) + 1)
    assert len(genau) == grenze and len(zuviel) == grenze + 1

    c, _ = _client({"/api/v3/klines": FakeAntwort(genau)}, max_bytes=grenze)
    assert len(c.klines("BTCUSDC", "15m", server_time_ms=900_000)) == 1

    c, _ = _client({"/api/v3/klines": FakeAntwort(zuviel)}, max_bytes=grenze)
    with pytest.raises(BinanceTooLarge):
        c.klines("BTCUSDC", "15m", server_time_ms=900_000)


def test_zwei_mebibyte_ist_die_vorgabe():
    assert binance.MAX_RESPONSE_BYTES == 2 * 1024 * 1024


# --------------------------------------------------- laufende Kerze verwerfen

def test_offene_kerze_wird_verworfen_und_nie_weitergegeben():
    """Beidseitig an der Grenze: close_time == server_time gilt als offen,
    close_time == server_time - 1 als geschlossen.

    Die laufende Kerze traegt einen vorlaeufigen close. Ein Fill auf ihr waere
    ein Fill auf einem Preis, den es so nie gab.
    """
    st = 1_800_000
    zeilen = [_kerze(0, st - 1, "81287.03"), _kerze(900_000, st, "99999.00")]
    c, _ = _client({"/api/v3/klines": FakeAntwort(_body(zeilen))})
    kerzen = c.klines("BTCUSDC", "15m", server_time_ms=st)

    assert len(kerzen) == 1
    assert kerzen[0].close_time == st - 1
    assert kerzen[0].close == Decimal("81287.03")
    assert all(k.closed for k in kerzen)
    assert Decimal("99999.00") not in [k.close for k in kerzen]


# -------------------------------------------------------- kaputte Antworten

@pytest.mark.parametrize("roh,grund", [
    (b"kein json", "kein JSON"),
    (_body({"code": -1121, "msg": "Invalid symbol."}), "Objekt statt Array"),
    (_body([[0, "1", "2"]]), "zu wenige Felder"),
    (_body([[0, "81287.00", "81200.00", "81300.00", "81250.00", "1", 899_999,
             "0", 1, "0", "0", "0"]]), "high < low"),
    (_body([[0, "81287.00", "81300.00", "81200.00", "0", "1", 899_999,
             "0", 1, "0", "0", "0"]]), "close <= 0"),
    (_body([[0, 81287.0, "81300.00", "81200.00", "81250.00", "1", 899_999,
             "0", 1, "0", "0", "0"]]), "float statt Zeichenkette"),
    (_body([[0, "abc", "81300.00", "81200.00", "81250.00", "1", 899_999,
             "0", 1, "0", "0", "0"]]), "nicht numerisch"),
    (_body([[0, "81287.00", "81300.00", "81200.00", "81250.00", "1", "899999",
             "0", 1, "0", "0", "0"]]), "closeTime als Zeichenkette"),
])
def test_kaputte_antworten_werfen_malformed(roh, grund):
    c, _ = _client({"/api/v3/klines": FakeAntwort(roh)})
    with pytest.raises(BinanceMalformed):
        c.klines("BTCUSDC", "15m", server_time_ms=900_000)


def test_eine_gueltige_antwort_wirft_nicht():
    """Gegenprobe zur Parametrisierung darueber: ein Waechter, der ALLES ablehnt,
    waere dort ebenfalls gruen."""
    c, _ = _client({"/api/v3/klines": FakeAntwort(_body([_kerze(0, 899_999)]))})
    assert len(c.klines("BTCUSDC", "15m", server_time_ms=900_000)) == 1


def test_fehlermeldungen_tragen_nie_den_antwortkoerper():
    """Spec 11.1: Antwortkoerper landen nie im Log. Der Ausnahmetext ist die
    Stelle, an der ein Koerper am leichtesten dorthin durchrutscht."""
    geheim = b'{"leak":"ADMIN-TOKEN-abcdef0123456789"}'
    c, _ = _client({"/api/v3/klines": FakeAntwort(geheim)})
    with pytest.raises(BinanceMalformed) as e:
        c.klines("BTCUSDC", "15m", server_time_ms=900_000)
    assert "ADMIN-TOKEN" not in str(e.value)
    assert "leak" not in str(e.value)


# -------------------------------------------------------------- Ratenlimits

@pytest.mark.parametrize("status", [429, 418])
def test_429_und_418_werden_zu_rate_limited(status):
    fehler = urllib.error.HTTPError(
        f"{BASIS}/api/v3/klines", status, "Too Many Requests", {"Retry-After": "7"}, None)
    c, _ = _client({"/api/v3/klines": fehler})
    with pytest.raises(BinanceRateLimited) as e:
        c.klines("BTCUSDC", "15m", server_time_ms=900_000)
    assert e.value.status == status
    assert e.value.retry_after_s == 7


@pytest.mark.parametrize("kopf", [{}, {"Retry-After": "bald"}, {"Retry-After": "-5"}])
def test_retry_after_fehlt_oder_ist_unsinn(kopf):
    """Kein Retry-After heisst 0 - der Poller macht daraus seine Mindestwartezeit
    von 60 s (Spec 8.3). Ein negativer Wert darf nicht zu 'sofort nochmal' werden."""
    fehler = urllib.error.HTTPError(f"{BASIS}/api/v3/klines", 429, "x", kopf, None)
    c, _ = _client({"/api/v3/klines": fehler})
    with pytest.raises(BinanceRateLimited) as e:
        c.klines("BTCUSDC", "15m", server_time_ms=900_000)
    assert e.value.retry_after_s == 0


def test_andere_http_fehler_bleiben_unterscheidbar():
    fehler = urllib.error.HTTPError(f"{BASIS}/api/v3/klines", 503, "x", {}, None)
    c, _ = _client({"/api/v3/klines": fehler})
    with pytest.raises(binance.BinanceHTTPError) as e:
        c.klines("BTCUSDC", "15m", server_time_ms=900_000)
    assert e.value.status == 503
    assert not isinstance(e.value, BinanceRateLimited)


# ------------------------------------------------- Gewicht, Parameter, Kopf

def test_gewichtszaehler_zaehlt_je_aufruf():
    """Grundlage fuer A-17c. Der Zaehler sitzt im Client, weil nur er weiss,
    welcher Endpunkt welches Gewicht kostet."""
    c, _ = _client({
        "/api/v3/klines": lambda url: FakeAntwort(_body([_kerze(0, 899_999)])),
        "/api/v3/time": lambda url: FakeAntwort(_body({"serverTime": 1_789_933_036_347})),
    })
    assert c.weight_used == 0
    c.server_time()
    assert c.weight_used == binance.WEIGHT_TIME == 1
    c.klines("BTCUSDC", "15m", server_time_ms=900_000)
    assert c.weight_used == 1 + binance.WEIGHT_KLINES == 3
    c.reset_weight()
    assert c.weight_used == 0


def test_gesendet_werden_nur_die_fuenf_erlaubten_parameter():
    """Spec 11.1: symbol, interval, limit, startTime, endTime - mehr nicht."""
    c, opener = _client({"/api/v3/klines": FakeAntwort(_body([_kerze(0, 899_999)]))})
    c.klines("BTCUSDC", "15m", limit=2, start_ms=0, end_ms=899_999, server_time_ms=900_000)
    assert len(opener.aufrufe) == 1
    schluessel = set(parse_qs(urlsplit(opener.aufrufe[0]).query))
    assert schluessel == {"symbol", "interval", "limit", "startTime", "endTime"}


def test_keine_kennung_in_den_kopfzeilen():
    """Spec 11.1: kein Token, kein Hostname, keine Kennung. Es gibt keine
    API-Schluessel im Projekt - aber der Hostname reicht schon, um einen
    Heimanschluss wiederzuerkennen."""
    c, opener = _client({"/api/v3/time": FakeAntwort(_body({"serverTime": 1}))})
    c.server_time()
    werte = " ".join(f"{k}: {v}" for k, v in opener.requests[0].header_items()).lower()
    assert socket.gethostname().lower() not in werte
    assert "aitra" not in werte
    assert "token" not in werte
    assert "authorization" not in werte
    assert "apikey" not in werte


def test_timeout_wird_an_urllib_durchgereicht():
    c, opener = _client({"/api/v3/time": FakeAntwort(_body({"serverTime": 1}))})
    c.server_time()
    assert opener.timeout == binance.TIMEOUT_S == 10.0


def test_unbekanntes_intervall_wird_abgelehnt_ohne_anfrage():
    c, opener = _client({"/api/v3/klines": FakeAntwort(b"[]")})
    with pytest.raises(BinanceError):
        c.klines("BTCUSDC", "7m", server_time_ms=900_000)
    assert opener.aufrufe == []


# ------------------------------------------------------------ exchangeInfo

def _exchange_info_body(symbol="BNBUSDC", status="TRADING", filter_weglassen=None) -> bytes:
    filters = [
        {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
        {"filterType": "LOT_SIZE", "stepSize": "0.00100000", "minQty": "0.00100000"},
        {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
    ]
    if filter_weglassen:
        filters = [f for f in filters if f["filterType"] != filter_weglassen]
    return _body({"symbols": [{
        "symbol": symbol, "status": status, "baseAsset": "BNB", "quoteAsset": "USDC",
        "baseAssetPrecision": 8, "quoteAssetPrecision": 8, "filters": filters,
    }]})


def test_exchange_info_liefert_eine_symbolspec_die_zur_eingebauten_passt():
    """Die eingebaute Tabelle und die abgefragte Wahrheit muessen uebereinstimmen.

    Weichen sie ab, bemisst der Replay (builtin) anders als der Livebetrieb
    (binance) - und A-8 faellt, ohne dass irgendwo ein Fehler steht.
    """
    c, _ = _client({"/api/v3/exchangeInfo": FakeAntwort(_exchange_info_body())})
    spec = c.exchange_info("BNBUSDC")
    assert isinstance(spec, money.SymbolSpec)
    assert spec.tick_size == Decimal("0.01")
    assert spec.step_size == Decimal("0.001")
    assert spec.min_qty == Decimal("0.001")
    assert spec.min_notional == Decimal("5")
    assert spec == money.BUILTIN_SPECS["BNBUSDC"]


@pytest.mark.parametrize("kwargs", [
    {"status": "BREAK"},
    {"filter_weglassen": "LOT_SIZE"},
    {"filter_weglassen": "PRICE_FILTER"},
    {"filter_weglassen": "NOTIONAL"},
])
def test_exchange_info_lehnt_unbrauchbare_antworten_ab(kwargs):
    c, _ = _client({"/api/v3/exchangeInfo": FakeAntwort(_exchange_info_body(**kwargs))})
    with pytest.raises(BinanceMalformed):
        c.exchange_info("BNBUSDC")


# ------------------------------------------- zweite Schicht: wer darf ins Netz

def test_nur_binance_py_benutzt_urllib_request():
    """Zweite Schicht. Ein Waechter, der nur den Client prueft, saehe einen
    zweiten Netzzugang in poller.py oder web.py ueberhaupt nicht.

    urllib.parse ist erlaubt (config.py benutzt urlsplit) - gesucht wird nur,
    was tatsaechlich eine Verbindung aufbauen kann.
    """
    aitra = Path(__file__).resolve().parent.parent / "aitra"
    treffer = subprocess.run(
        ["grep", "-rlnE", "--include=*.py",
         r"urllib\.request|urllib\.error|http\.client|socket\.socket|import requests",
         str(aitra)],
        capture_output=True, text=True).stdout.splitlines()
    assert any(Path(z).name == "binance.py" for z in treffer), (
        "Pruefflaeche leer: der grep findet nicht einmal binance.py - Muster pruefen"
    )
    andere = [z for z in treffer if Path(z).name != "binance.py"]
    assert andere == [], f"Netzzugriff ausserhalb von binance.py: {andere}"
```

Und an `app/tests/test_marketdata.py` anhängen:

```python
def test_intervalltabelle_und_abgeleitete_schwellen():
    """Die sechs erlaubten Intervalle (Spec 11.2) und die Schwellen, die sich
    daraus ergeben (Spec 8.2). Die Tabelle ist die einzige Stelle, an der ein
    Intervall in Sekunden uebersetzt wird - ein Parser waere hier die Quelle
    stiller Fehler ('1M' ist ein Monat, '1m' eine Minute)."""
    from aitra.marketdata import INTERVALS, interval_seconds

    assert INTERVALS == {"1m": 60, "5m": 300, "15m": 900, "1h": 3600,
                          "4h": 14400, "1d": 86400}
    assert interval_seconds("15m") == 900
    with pytest.raises(ValueError):
        interval_seconds("7m")
    with pytest.raises(ValueError):
        interval_seconds("1M")

    # Spec 8.2, Tabelle: die Untergrenze greift nur bei 1m
    erwartet = {"1m": (150, 300), "15m": (1350, 2700), "1h": (5400, 10800)}
    geprueft = 0
    for intervall, (warn, kill) in erwartet.items():
        s = interval_seconds(intervall)
        assert max(150, math.ceil(1.5 * s)) == warn, intervall
        assert max(300, 3 * s) == kill, intervall
        geprueft += 1
    assert geprueft == 3
```

(`import math` oben in der Datei ergänzen, falls nicht vorhanden.)

- [ ] **Schritt 2: Tests laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q \
   tests/test_binance.py tests/test_marketdata.py'
```

Erwartet: `ModuleNotFoundError: No module named 'aitra.binance'` (Sammelfehler beim
Import der Testdatei) und in `test_marketdata.py`
`ImportError: cannot import name 'INTERVALS' from 'aitra.marketdata'`.

- [ ] **Schritt 3: `marketdata.py` — Intervalltabelle ergänzen**

Direkt unter den Importen, vor `class Candle`:

```python
# Die sechs erlaubten Kerzenintervalle und ihre Laenge in Sekunden (Spec 11.2).
# Feste Menge statt Parsen: '1M' waere ein Monat, '1m' eine Minute — ein Parser
# ueber Ziffer+Buchstabe verwechselt beides still.
INTERVALS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400,
}


def interval_seconds(interval: str) -> int:
    """Laenge einer Kerze in Sekunden. Grundlage der intervallrelativen
    Veraltet-Schwellen aus Spec 8.2."""
    try:
        return INTERVALS[interval]
    except KeyError as e:
        raise ValueError(
            f"Unbekanntes Intervall {interval!r}; erlaubt: {sorted(INTERVALS)}"
        ) from e
```

- [ ] **Schritt 4: `binance.py` schreiben**

```python
"""Der einzige Netzzugang des Projekts: Binance Spot, oeffentlich und nur lesend.

E-005 und Spec 11.1. Jede Haertung steht hier, weil es keine zweite Stelle gibt,
an der sie stehen koennte:

- Host-Allowlist, verglichen auf EXAKTE Gleichheit des Hostnamens. Ein
  Praefixvergleich liesse https://api.binance.com.evil.example durch (A-17).
- Nur HTTPS; Zertifikatspruefung ueber den System-Truststore, nie abgeschaltet.
- Keine Weiterleitungen: ein eigener Handler macht jede 3xx zum Fehler. Ohne ihn
  waeren 169.254.169.254 und 127.0.0.1 gueltige Umleitungsziele, obwohl die
  Allowlist nur die ERSTE URL prueft (A-17b).
- 10 s Timeout, hoechstens 2 MiB gelesen, dann Abbruch.
- Gesendet werden ausschliesslich symbol, interval, limit, startTime, endTime.
  Kein Token, kein Hostname, keine Kennung. API-Schluessel gibt es im Projekt
  nicht, also kann auch keiner abfliessen.
- Antwortkoerper landen nie in einer Fehlermeldung und nie im Log.

Zahlen aus JSON entstehen ausschliesslich ueber Decimal(str) — nie ueber float
(E-002). Ein float-Feld in der Antwort ist ein Fehler, kein Wert.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode, urlsplit

from . import money
from .config import ALLOWED_BINANCE_HOSTS
from .marketdata import INTERVALS, Candle

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 10.0
WEIGHT_KLINES = 2          # Spec 2.2, aus der Binance-Doku
WEIGHT_TIME = 1
WEIGHT_EXCHANGE_INFO = 20

_ERLAUBTE_PARAMETER = frozenset({"symbol", "interval", "limit", "startTime", "endTime"})


class BinanceError(RuntimeError):
    """Oberklasse. Traegt nie einen Antwortkoerper und nie eine volle URL."""


class BinanceMalformed(BinanceError):
    """Antwort ist kein JSON, hat die falsche Struktur oder unplausible Werte."""


class BinanceTooLarge(BinanceError):
    """Antwort ueber MAX_RESPONSE_BYTES — Verbindung abgebrochen, Inhalt verworfen."""


class BinanceHTTPError(BinanceError):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


class BinanceRateLimited(BinanceHTTPError):
    """429 oder 418. retry_after_s ist 0, wenn Binance keinen Wert schickt —
    die Mindestwartezeit setzt der Poller (Spec 8.3: mindestens 60 s)."""

    def __init__(self, status: int, retry_after_s: int) -> None:
        super().__init__(status)
        self.retry_after_s = retry_after_s


class _KeineWeiterleitung(urllib.request.HTTPRedirectHandler):
    """Macht jede 3xx zum Fehler (A-17b)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BinanceError(f"Weiterleitung {code} wird nicht verfolgt")


def build_opener() -> urllib.request.OpenerDirector:
    """Der Opener des Projekts. build_opener() ersetzt den Standard-Handler,
    weil _KeineWeiterleitung von ihm erbt."""
    return urllib.request.build_opener(_KeineWeiterleitung())


def _pruefe_ziel(url: str) -> None:
    """Nur HTTPS, nur die beiden bekannten Hosts, Hostname exakt verglichen."""
    teile = urlsplit(url)
    if teile.scheme != "https":
        raise BinanceError("Nur HTTPS erlaubt")
    if teile.hostname not in ALLOWED_BINANCE_HOSTS:
        raise BinanceError(f"Host nicht in der Allowlist: {teile.hostname!r}")


def _dec(wert: Any, feld: str) -> Decimal:
    """JSON-Wert zu Decimal. float und bool sind Fehler, keine Werte (E-002)."""
    if isinstance(wert, bool) or isinstance(wert, float):
        raise BinanceMalformed(f"Feld {feld}: float/bool statt Zeichenkette")
    if not isinstance(wert, (str, int)):
        raise BinanceMalformed(f"Feld {feld}: unerwarteter Typ {type(wert).__name__}")
    try:
        return Decimal(str(wert))
    except InvalidOperation as e:
        raise BinanceMalformed(f"Feld {feld}: nicht numerisch") from e


def _int(wert: Any, feld: str) -> int:
    if isinstance(wert, bool) or not isinstance(wert, int):
        raise BinanceMalformed(f"Feld {feld}: kein ganzzahliger Wert")
    return wert


def _retry_after(e: urllib.error.HTTPError) -> int:
    roh = (e.headers or {}).get("Retry-After", "")
    try:
        return max(0, int(str(roh).strip()))
    except (TypeError, ValueError):
        return 0


class BinanceClient:
    """Duenner, gehaerteter Lesezugriff. opener ist einspeisbar, damit kein Test
    ins Netz geht (Spec 11.3)."""

    def __init__(self, base_url: str, *, timeout_s: float = TIMEOUT_S,
                 max_bytes: int = MAX_RESPONSE_BYTES, opener=None) -> None:
        _pruefe_ziel(base_url)
        self._base = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes
        self._opener = opener if opener is not None else build_opener()
        self._weight = 0

    @property
    def weight_used(self) -> int:
        """Verbrauchtes Ratengewicht seit dem letzten reset_weight() (A-17c)."""
        return self._weight

    def reset_weight(self) -> None:
        self._weight = 0

    def _hole(self, pfad: str, params: dict, gewicht: int) -> Any:
        unerlaubt = set(params) - _ERLAUBTE_PARAMETER
        if unerlaubt:
            raise BinanceError(f"Unerlaubte Parameter: {sorted(unerlaubt)}")
        url = f"{self._base}{pfad}?{urlencode(params)}" if params else f"{self._base}{pfad}"
        _pruefe_ziel(url)
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        self._weight += gewicht
        try:
            with self._opener.open(req, timeout=self._timeout_s) as resp:
                roh = resp.read(self._max_bytes + 1)
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                raise BinanceRateLimited(e.code, _retry_after(e)) from None
            raise BinanceHTTPError(e.code) from None
        except urllib.error.URLError as e:
            raise BinanceError(f"Verbindungsfehler: {type(e.reason).__name__}") from None
        if len(roh) > self._max_bytes:
            raise BinanceTooLarge(f"Antwort über {self._max_bytes} Bytes – verworfen")
        try:
            return json.loads(roh)
        except (ValueError, UnicodeDecodeError) as e:
            # Bewusst ohne roh: der Koerper darf nie in eine Meldung geraten.
            raise BinanceMalformed(f"Antwort ist kein JSON ({type(e).__name__})") from None

    def server_time(self) -> int:
        """Binance-Serverzeit in ms. Grundlage des Uhrversatzes (Spec 8.1)."""
        daten = self._hole("/api/v3/time", {}, WEIGHT_TIME)
        if not isinstance(daten, dict):
            raise BinanceMalformed("time: Antwort ist kein Objekt")
        return _int(daten.get("serverTime"), "serverTime")

    def klines(self, symbol: str, interval: str, *, server_time_ms: int,
                limit: int = 500, start_ms: int | None = None,
                end_ms: int | None = None) -> list[Candle]:
        """Ausschliesslich ABGESCHLOSSENE Kerzen.

        Binance liefert die laufende Kerze mit. Sie traegt einen vorlaeufigen
        close, der beim naechsten Abruf ein anderer ist — ein Fill auf ihr waere
        ein Fill auf einem Preis, den es so nie gab. Verworfen wird anhand
        close_time < server_time_ms, also gegen die SERVERZEIT und nicht gegen
        die Containeruhr: ein LXC mit Versatz reichte sonst offene Kerzen als
        geschlossen durch (Spec 8.1).
        """
        if interval not in INTERVALS:
            raise BinanceError(f"Unbekanntes Intervall {interval!r}")
        params: dict = {"symbol": symbol, "interval": interval,
                         "limit": min(max(int(limit), 1), 1000)}
        if start_ms is not None:
            params["startTime"] = int(start_ms)
        if end_ms is not None:
            params["endTime"] = int(end_ms)

        daten = self._hole("/api/v3/klines", params, WEIGHT_KLINES)
        if not isinstance(daten, list):
            raise BinanceMalformed("klines: Antwort ist kein Array")

        kerzen: list[Candle] = []
        for zeile in daten:
            if not isinstance(zeile, list) or len(zeile) < 12:
                raise BinanceMalformed("klines: Zeile hat nicht die 12 Felder aus Spec 2.2")
            open_time = _int(zeile[0], "openTime")
            close_time = _int(zeile[6], "closeTime")
            o, h, t, c = (_dec(zeile[1], "open"), _dec(zeile[2], "high"),
                           _dec(zeile[3], "low"), _dec(zeile[4], "close"))
            v = _dec(zeile[5], "volume")
            if h < t or c <= 0 or o <= 0:
                raise BinanceMalformed("klines: unplausible Kerze (high<low oder Preis<=0)")
            if close_time >= server_time_ms:
                continue  # laufende Kerze — nie weitergeben
            kerzen.append(Candle(symbol=symbol, interval=interval, open_time=open_time,
                                  close_time=close_time, open=o, high=h, low=t, close=c,
                                  volume=v, closed=True))
        return kerzen

    def exchange_info(self, symbol: str) -> money.SymbolSpec:
        """Losgroessen eines Symbols, frisch von Binance.

        Ein Symbol je Aufruf: Spec 11.1 erlaubt als Parameter ausschliesslich
        symbol, interval, limit, startTime, endTime. Der Sammelparameter
        'symbols' steht nicht darauf, und eine Ausnahme fuer Bequemlichkeit
        waere die erste Bresche in einer Liste, die genau deshalb kurz ist.
        """
        daten = self._hole("/api/v3/exchangeInfo", {"symbol": symbol}, WEIGHT_EXCHANGE_INFO)
        if not isinstance(daten, dict) or not isinstance(daten.get("symbols"), list) \
                or not daten["symbols"]:
            raise BinanceMalformed("exchangeInfo: kein symbols-Array")
        s = daten["symbols"][0]
        if s.get("status") != "TRADING":
            raise BinanceMalformed(f"exchangeInfo: {symbol} steht nicht auf TRADING")
        filt = {f.get("filterType"): f for f in s.get("filters", []) if isinstance(f, dict)}
        try:
            preis, lot = filt["PRICE_FILTER"], filt["LOT_SIZE"]
            notional = filt.get("NOTIONAL") or filt["MIN_NOTIONAL"]
        except KeyError as e:
            raise BinanceMalformed(f"exchangeInfo: Filter {e} fehlt") from e
        return money.SymbolSpec(
            symbol=str(s["symbol"]), base=str(s["baseAsset"]), quote=str(s["quoteAsset"]),
            tick_size=_dec(preis["tickSize"], "tickSize"),
            step_size=_dec(lot["stepSize"], "stepSize"),
            min_qty=_dec(lot["minQty"], "minQty"),
            min_notional=_dec(notional["minNotional"], "minNotional"),
            base_precision=_int(s["baseAssetPrecision"], "baseAssetPrecision"),
            quote_precision=_int(s["quoteAssetPrecision"], "quoteAssetPrecision"),
        )
```

- [ ] **Schritt 5: Tests laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q'
```

Erwartet: 0 failed; `tests/test_binance.py` allein **≥ 33 passed** (6 Allowlist-Punkte,
1 Anfragepfad, 1 Weiterleitung, 2 Größenlimit, 1 offene Kerze, 8 kaputte Antworten,
1 Gegenprobe, 1 Körperleck, 2 Ratenlimit, 3 Retry-After, 1 anderer HTTP-Fehler,
1 Gewicht, 1 Parameter, 1 Kopfzeilen, 1 Timeout, 1 Intervall, 1 exchangeInfo,
4 exchangeInfo-Ablehnungen, 1 grep-Wächter). Dazu messen:

```bash
wc -l app/aitra/binance.py     # erwartet < 230, Marke ist 300
```

- [ ] **Schritt 6: Rot-Nachweis führen (fünf Nachweise)**

| # | Eingebauter Fehler | Erwartete rote Ausgabe |
|---|---|---|
| 1 | In `_pruefe_ziel` den Hostvergleich durch einen Präfixvergleich ersetzen: `if not any(url.startswith("https://" + h) for h in ALLOWED_BINANCE_HOSTS): raise ...` | `test_a17_allowlist_wird_exakt_verglichen[https://api.binance.com.evil.example-False]` → `Failed: DID NOT RAISE <class 'aitra.binance.BinanceError'>` |
| 2 | In `build_opener()` `_KeineWeiterleitung()` weglassen (`return urllib.request.build_opener()`) | `test_a17b_keine_weiterleitung_wird_verfolgt` → `Failed: DID NOT RAISE` und, mit `pytest.raises` entfernt, `assert 1 == 0` am Zähler des Umleitungsziels |
| 3 | In `klines()` `close_time >= server_time_ms` zu `close_time > server_time_ms` ändern | `test_offene_kerze_wird_verworfen_und_nie_weitergegeben` → `assert 2 == 1` |
| 4 | In `_hole()` `resp.read(self._max_bytes + 1)` zu `resp.read()` ändern und die Längenprüfung löschen | `test_groessenlimit_greift_beidseitig` → `Failed: DID NOT RAISE <class 'aitra.binance.BinanceTooLarge'>` |
| 5 | In `_dec()` `isinstance(wert, float)` aus der Bedingung entfernen | `test_kaputte_antworten_werfen_malformed[...-float statt Zeichenkette]` → `Failed: DID NOT RAISE <class 'aitra.binance.BinanceMalformed'>` |

Jeden Fehler einzeln einbauen, die Ausgabe wörtlich protokollieren, zurücknehmen,
Suite erneut grün.

*Wären die Tests ohne die eingebauten Fehler grün gewesen?* Ja für alle fünf — Schritt 5
hat sie grün gemessen. Nachweis 2 ist zusätzlich abgesichert: die Gegenprobe im Test
(`urllib.request.build_opener()` folgt der Umleitung, Zähler 1) beweist, dass der Server
tatsächlich umleitet und der Zähler tatsächlich zählt. Ohne sie könnte ein Test grün sein,
der nur deshalb 0 misst, weil nichts passiert ist.

- [ ] **Schritt 7: Commit**

```bash
git add app/aitra/binance.py app/aitra/marketdata.py \
        app/tests/test_binance.py app/tests/test_marketdata.py
git commit -m "feat(binance): einziger Netzzugang, gehaertet (A-17, A-17b)

Host-Allowlist mit exaktem Hostvergleich (Praefix-Falle faellt), nur HTTPS,
eigener Handler macht jede 3xx zum Fehler, 10 s Timeout, 2 MiB Deckel,
Gewichtszaehler fuer A-17c. Die laufende Kerze wird anhand
close_time < server_time verworfen und nie weitergegeben.

Kein Test geht ins Netz: urllib ist durch eine Attrappe ersetzt. A-17b laeuft
gegen einen lokalen Loopback-Server und misst den Zaehler des Umleitungsziels,
plus Gegenprobe mit dem Standardhandler.

marketdata: INTERVALS/interval_seconds als einzige Intervall-Uebersetzung.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Aufgabe 4: `backfill.py` — historische Kerzen holen, blockweise, mit Ratenbudget und Lückenmeldung

**Macht grün:** **A-16b** (Platzbedarf je Kerze). Die Messung selbst berührt
`store.upsert_candles` (Aufgabe 1) — dieselbe Funktion, die `backfill_symbol()` für
jeden Block aufruft. Der Umweg über eine simulierte Binance-Antwort mit 10.000 Zeilen
kostet nur Testaufwand, ohne eine andere Codezeile zu berühren als die, die den
Plattenverbrauch tatsächlich bestimmt — deshalb steht die Messung hier, im echten
Aufrufpfad von `backfill.py`, nicht als isolierter Store-Test.

**Dateien:**
- Neu: `app/aitra/backfill.py`
- Test neu: `app/tests/test_backfill.py`

**Schnittstellen:**
- Nutzt (aus Aufgabe 1 und 3): `store.CandleRow`, `store.upsert_candles`,
  `store.get_candles`, `db.connect`, `db.migrate`, `db.now`, `db.log_event`,
  `config.load() -> Config` (für `binance_base_url`, `data_dir`),
  `binance.BinanceClient(base_url, *, opener=None).server_time() -> int`,
  `binance.BinanceClient.klines(symbol, interval, *, server_time_ms, limit=500, start_ms=None, end_ms=None) -> list[Candle]`,
  `binance.BinanceRateLimited(status, retry_after_s)`, `binance.BinanceError`,
  `marketdata.interval_seconds(interval) -> int`, `marketdata.Candle`
- Stellt bereit:
  - `backfill.BackfillResult(symbol, interval, fetched: int, requests: int, gaps: int, weight_used: int)`
  - `backfill.backfill_symbol(client: BinanceClient, conn: sqlite3.Connection, symbol: str, interval: str, days: int, *, source: str = "backfill", block_limit: int = 1000, sleep_fn: Callable[[float], None] = time.sleep) -> BackfillResult`
  - `backfill.main(argv: list[str] | None = None) -> int` — CLI:
    `python -m aitra.backfill --symbol BTCUSDC --interval 15m --days 400 [--db PFAD]`

---

- [ ] **Schritt 1: Die scheiternden Tests schreiben**

```python
# app/tests/test_backfill.py
from __future__ import annotations

import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from aitra import backfill, db, store
from aitra.backfill import backfill_symbol
from aitra.binance import BinanceClient

BASIS = "https://api.binance.com"


class FakeAntwort:
    """Wortgleich zum Muster aus test_binance.py (Aufgabe 3) — bewusst hier
    erneut ausgeschrieben, kein Test geht ins Netz und keiner verweist auf
    eine andere Testdatei."""

    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None) -> None:
        self._body = body
        self._pos = 0
        self.status = status
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            d = self._body[self._pos:]
            self._pos = len(self._body)
            return d
        d = self._body[self._pos:self._pos + n]
        self._pos += len(d)
        return d

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


class FakeOpener:
    """Ersetzt urllib vollstaendig. antworten: Pfad -> FakeAntwort, Exception
    oder Callable(url) -> FakeAntwort. Ein Callable darf selbst eine Exception
    werfen (fuer zustandsbehaftete Sequenzen, siehe _AntwortSequenz unten)."""

    def __init__(self, antworten: dict) -> None:
        self.antworten = antworten
        self.aufrufe: list[str] = []

    def open(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.aufrufe.append(url)
        a = self.antworten[urlsplit(url).path]
        if isinstance(a, BaseException):
            raise a
        if callable(a):
            a = a(url)
        return a


class _AntwortSequenz:
    """Liefert bei jedem Aufruf den naechsten Eintrag, haengt am letzten fest."""

    def __init__(self, eintraege: list) -> None:
        self._eintraege = eintraege
        self._i = 0

    def __call__(self, url):
        e = self._eintraege[min(self._i, len(self._eintraege) - 1)]
        self._i += 1
        if isinstance(e, BaseException):
            raise e
        return e


def _kerze(open_time: int, interval_s: int, close: str = "81287.03") -> list:
    close_time = open_time + interval_s * 1000 - 1
    return [open_time, "81287.00", "81300.00", "81200.00", close, "12.345",
            close_time, "1003000.0", 2500, "6.0", "500000.0", "0"]


def _body(obj) -> bytes:
    import json
    return json.dumps(obj).encode()


def _universum(n: int, interval_s: int, start: int = 0) -> list:
    return [_kerze(start + i * interval_s * 1000, interval_s) for i in range(n)]


def _opener_fuer(universum: list, server_time_ms: int) -> FakeOpener:
    def klines(url):
        qs = parse_qs(urlsplit(url).query)
        start = int(qs.get("startTime", ["0"])[0])
        limit = int(qs.get("limit", ["500"])[0])
        slice_ = [z for z in universum if z[0] >= start][:limit]
        return FakeAntwort(_body(slice_))

    def zeit(url):
        return FakeAntwort(_body({"serverTime": server_time_ms}))

    return FakeOpener({"/api/v3/klines": klines, "/api/v3/time": zeit})


def test_backfill_holt_mehrere_bloecke_und_speichert_sie(tmp_path):
    """1.440 Kerzen (1 Tag bei 1m), block_limit=500 -> 3 Bloecke (500+500+440).

    Pruefflaeche: fetched UND die tatsaechlich in der DB stehenden Zeilen
    werden geprueft, nicht nur der Rueckgabewert - store.upsert_candles ist
    idempotent, ein Bug in der Bloecke-Weiterschaltung wuerde denselben Block
    mehrfach schreiben, ohne dass 'fetched' allein das zeigen wuerde.
    """
    universum = _universum(1440, 60)
    server_time_ms = universum[-1][6] + 1
    opener = _opener_fuer(universum, server_time_ms)
    client = BinanceClient(BASIS, opener=opener)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)

    result = backfill_symbol(client, conn, "BTCUSDC", "1m", days=1,
                              block_limit=500, sleep_fn=lambda s: None)

    assert result.requests == 3, "3 Bloecke erwartet: 500+500+440"
    assert result.fetched == 1440
    assert result.gaps == 0
    rows = store.get_candles(conn, "BTCUSDC", "1m", limit=2000)
    assert len(rows) == 1440, f"Pruefflaeche: {len(rows)} von 1440 Kerzen in der DB"
    assert rows[0].open_time == 0 and rows[-1].open_time == 1439 * 60_000


def test_backfill_meldet_luecken_in_der_reihe(tmp_path):
    """Eine kuenstliche Luecke von genau einem fehlenden Intervall bei Index 100."""
    universum = _universum(200, 900)
    del universum[100]  # Luecke: Index 99 und (ehem.) 101 werden Nachbarn
    server_time_ms = universum[-1][6] + 1
    opener = _opener_fuer(universum, server_time_ms)
    client = BinanceClient(BASIS, opener=opener)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)

    result = backfill_symbol(client, conn, "BTCUSDC", "15m", days=3, sleep_fn=lambda s: None)

    assert result.fetched == 199
    assert result.gaps == 1, f"genau eine Luecke erwartet, gemessen: {result.gaps}"
    treffer = conn.execute(
        "SELECT detail FROM events WHERE event = 'CANDLE_GAP'"
    ).fetchall()
    assert len(treffer) == 1, "genau ein CANDLE_GAP-Ereignis erwartet"
    assert "BTCUSDC" in treffer[0]["detail"]


def test_backfill_wiederholt_bei_429_mit_retry_after(tmp_path):
    """Ein 429 mit Retry-After=3 wird einmal wiederholt, nicht durchgereicht."""
    universum = _universum(4, 900)
    server_time_ms = universum[-1][6] + 1
    fehler = urllib.error.HTTPError(f"{BASIS}/api/v3/klines", 429, "Too Many Requests",
                                     {"Retry-After": "3"}, None)
    sequenz = _AntwortSequenz([fehler, FakeAntwort(_body(universum))])
    opener = FakeOpener({
        "/api/v3/klines": sequenz,
        "/api/v3/time": FakeAntwort(_body({"serverTime": server_time_ms})),
    })
    client = BinanceClient(BASIS, opener=opener)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    geschlafen: list[float] = []

    result = backfill_symbol(client, conn, "BTCUSDC", "15m", days=1, sleep_fn=geschlafen.append)

    assert result.fetched == 4
    assert geschlafen == [3], f"erwartet genau ein Schlaf von 3s, gemessen: {geschlafen}"


def test_a16b_platzbedarf_pro_kerze(tmp_path):
    """A-16b: 10.000 15m-Kerzen ueber genau den Pfad schreiben, den backfill.py
    operativ benutzt (Bloecke von 1.000 ueber store.upsert_candles), dann
    VACUUM und Dateigroesse messen. Schwelle maschinenunabhaengig: <= 250 B/Zeile.
    Hochrechnung Betriebskonfiguration (76.800 Zeilen, K-5): siehe docs/abnahme/.
    """
    n = 10_000
    universum = _universum(n, 900)
    server_time_ms = universum[-1][6] + 1
    opener = _opener_fuer(universum, server_time_ms)
    client = BinanceClient(BASIS, opener=opener)
    db_path = tmp_path / "a.db"
    conn = db.connect(db_path)
    db.migrate(conn)
    days = (n * 900 * 1000) // 86_400_000 + 1

    result = backfill_symbol(client, conn, "BTCUSDC", "15m", days=days,
                              block_limit=1000, sleep_fn=lambda s: None)
    assert result.fetched == n, f"Pruefflaeche: erwartet {n}, geschrieben {result.fetched}"

    conn.execute("VACUUM")
    conn.close()
    bytes_je_zeile = db_path.stat().st_size / n
    assert bytes_je_zeile <= 250, f"{bytes_je_zeile:.1f} B/Zeile > 250 B/Zeile (A-16b)"
```

- [ ] **Schritt 2: Tests laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q tests/test_backfill.py'
```

Erwartet: `ModuleNotFoundError: No module named 'aitra.backfill'` (Sammelfehler
beim Import der Testdatei).

- [ ] **Schritt 3: `backfill.py` schreiben**

```python
"""python -m aitra.backfill: historische Kerzen ueber binance.klines() holen
und speichern (Spec Bauschritt 10). Nur CLI, kein HTTP-Endpunkt — Spec 11.2
verbietet einen Endpunkt, der einen Ruecklauf ueber Zehntausende Kerzen anstoesst.

Blockweise ueber BinanceClient.klines() (limit hoechstens 1000, Spec 2.2),
Fortschritt ueber startTime. Ein 429/418 wird mit Retry-After wiederholt,
nicht durchgereicht — ein Backfill, der beim ersten Ratenlimit abbricht, waere
fuer 400 Tage Rueckfuellung unbrauchbar. Luecken in der Reihe (open_time springt
um mehr als ein Intervall) werden gezaehlt und als Event geloggt (Spec 10,
CANDLE_GAP) — Kerzen werden trotzdem gespeichert, nie verworfen.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config, db, store
from .binance import BinanceClient, BinanceError, BinanceRateLimited
from .marketdata import interval_seconds

BLOCK_LIMIT_DEFAULT = 1000  # Spec 2.2: klines-limit maximal 1000
_RATE_LIMIT_RETRIES = 5


@dataclass(frozen=True)
class BackfillResult:
    symbol: str
    interval: str
    fetched: int
    requests: int
    gaps: int
    weight_used: int


def backfill_symbol(
    client: BinanceClient,
    conn,
    symbol: str,
    interval: str,
    days: int,
    *,
    source: str = "backfill",
    block_limit: int = BLOCK_LIMIT_DEFAULT,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> BackfillResult:
    """Holt alle geschlossenen Kerzen der letzten `days` Tage in Bloecken und
    speichert sie ueber store.upsert_candles (idempotent, A-13/A-15).
    """
    interval_s = interval_seconds(interval)
    interval_ms = interval_s * 1000
    server_time_ms = client.server_time()
    start_ms = server_time_ms - days * 86_400_000

    fetched_at = db.now()
    open_times: list[int] = []
    requests = 0
    cursor = start_ms
    while cursor < server_time_ms:
        for versuch in range(_RATE_LIMIT_RETRIES + 1):
            try:
                kerzen = client.klines(symbol, interval, server_time_ms=server_time_ms,
                                        limit=block_limit, start_ms=cursor, end_ms=server_time_ms)
                break
            except BinanceRateLimited as e:
                if versuch == _RATE_LIMIT_RETRIES:
                    raise
                sleep_fn(max(1, e.retry_after_s))
        requests += 1
        if not kerzen:
            break
        rows = [
            store.CandleRow(symbol=k.symbol, interval=k.interval, open_time=k.open_time,
                             close_time=k.close_time, open=k.open, high=k.high, low=k.low,
                             close=k.close, volume=k.volume, source=source, fetched_at=fetched_at)
            for k in kerzen
        ]
        store.upsert_candles(conn, rows)
        open_times.extend(k.open_time for k in kerzen)
        letzte = kerzen[-1]
        if letzte.open_time <= cursor:
            break  # keine neuen Daten -> Endlosschleife vermeiden
        cursor = letzte.open_time + interval_ms

    gaps = 0
    for a, b in zip(open_times, open_times[1:]):
        diff = b - a
        if diff > interval_ms:
            gaps += int(diff // interval_ms - 1)
    if gaps:
        db.log_event(conn, "BACKFILL", "WARN", "CANDLE_GAP",
                     f"{symbol} {interval}: {gaps} fehlende Intervalle in {len(open_times)} Kerzen")

    return BackfillResult(symbol=symbol, interval=interval, fetched=len(open_times),
                           requests=requests, gaps=gaps, weight_used=client.weight_used)


def _parse_args(argv):
    p = argparse.ArgumentParser(description="Aitra Backfill (historische Kerzen)")
    p.add_argument("--symbol", required=True)
    p.add_argument("--interval", default="15m")
    p.add_argument("--days", type=int, required=True)
    p.add_argument("--db", default=None, help="Pfad zur aitra.db; ohne Angabe DATA_DIR/aitra.db")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg = config.load()
    db_path = Path(args.db) if args.db else cfg.data_dir / "aitra.db"
    conn = db.connect(db_path)
    db.migrate(conn)
    client = BinanceClient(cfg.binance_base_url)
    result = backfill_symbol(client, conn, args.symbol.upper(), args.interval, args.days)
    print(f"{result.symbol} {result.interval}: {result.fetched} Kerzen in {result.requests} "
          f"Anfragen, {result.gaps} Luecken, Gewicht {result.weight_used}", file=sys.stderr)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Schritt 4: Tests laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q'
```

Erwartet: **0 failed**, `tests/test_backfill.py` allein **5 passed**.

- [ ] **Schritt 5: Rot-Nachweis führen (vier Nachweise)**

| # | Eingebauter Fehler | Erwartete rote Ausgabe |
|---|---|---|
| 1 | In `backfill_symbol()` beim `client.klines(...)`-Aufruf `start_ms=cursor` entfernen (immer ab dem Anfang lesen) | `test_backfill_holt_mehrere_bloecke_und_speichert_sie` → `AssertionError: Pruefflaeche: 500 von 1440 Kerzen in der DB` — nur der erste Block landet in der DB, jede Folgeanfrage liefert ihn erneut (idempotent überschrieben) |
| 2 | Die Lückenschleife (`for a, b in zip(open_times, open_times[1:]): ...`) entfernen, `gaps = 0` fest lassen | `test_backfill_meldet_luecken_in_der_reihe` → `AssertionError: genau eine Luecke erwartet, gemessen: 0` |
| 3 | Den `try/except BinanceRateLimited`-Block um `client.klines(...)` entfernen | `test_backfill_wiederholt_bei_429_mit_retry_after` → `aitra.binance.BinanceRateLimited: HTTP 429` statt eines Ergebnisses |
| 4 | In `store.upsert_candles()` (Aufgabe 1) die Kerze testweise zusätzlich als JSON-Text in eine zweite Spalte schreiben (Blob-Verdopplung) | `test_a16b_platzbedarf_pro_kerze` → `AssertionError: 4xx.x B/Zeile > 250 B/Zeile (A-16b)` |

Jeden Fehler einzeln einbauen, Ausgabe wörtlich protokollieren, zurücknehmen, Suite
erneut grün. *Wären die Tests ohne die Fehler grün gewesen?* Ja für alle vier — Schritt 4
hat sie grün gemessen, und jeder Fehler wirkt direkt auf die gemessene Größe (Zeilenzahl
in der DB, Lückenzähler, Ausnahmepfad, Bytes/Zeile), nicht auf eine Nebenbedingung.

- [ ] **Schritt 6: Commit**

```bash
git add app/aitra/backfill.py app/tests/test_backfill.py
git commit -m "feat(backfill): historische Kerzen blockweise holen (A-16b)

python -m aitra.backfill holt Kerzen ueber binance.klines() in Bloecken von
hoechstens 1000, wiederholt 429/418 mit Retry-After statt abzubrechen, und
meldet Luecken in der Reihe als CANDLE_GAP-Ereignis statt sie zu verschweigen.

A-16b (Platzbedarf je Kerze, <= 250 B/Zeile) wird hier gemessen, ueber genau
den Pfad, den ein echter Rueckfuellungslauf benutzt.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Aufgabe 5: `poller.py` — Thread, Backoff, Veraltet-Erkennung → Kill Switch

**Macht grün:** A-11 (Poller-Anteil), A-11b (Poller-Anteil), A-12 (hier aufgelöst,
siehe Kasten unten), A-17c.

> **Zwei Befunde, hier gemeldet statt stillschweigend übernommen.**
>
> **Erstens — A-11/A-11b sind an ihrer Arithmetik bereits aus A1 grün.**
> `test_staleness_intervallrelative_schwellen_a11` und `test_staleness_uhrversatz_a11b`
> in `app/tests/test_marketdata.py` decken die reine Schwellen-Arithmetik von
> `marketdata.staleness()` bereits **vollständig** ab — sogar mit mehr Messpunkten als
> die Spec verlangt (zusätzlich die exakten Grenzen 150/151 und 1350/1351, acht statt
> vier Uhrversatz-Punkte). Das ist keine Lücke, sondern eine Vorarbeit aus Teilprojekt
> A1, weil `marketdata.py` dort entstand. Diese Aufgabe fügt der Arithmetik nichts hinzu;
> sie macht **die Wirkung** grün — dass ein echter Poll-Zyklus `staleness()` mit den
> konfigurierten Schwellen aufruft, den Kill Switch tatsächlich setzt und genau ein
> Ereignis pro Übergang loggt. Die Tests unten prüfen deshalb `poll_once()`, nicht noch
> einmal `staleness()` isoliert — eine zweite Kopie derselben Parametrisierung würde nur
> bereits bewiesenen Code ein zweites Mal beweisen.
>
> **Zweitens — A-11's HTTP-503-Anteil und A-12 wie wörtlich formuliert haben aktuell
> keine Prüffläche.** A-11 nennt „HTTP 200"/„HTTP 503" — das ist `/api/health`, das erst
> in Aufgabe 6 verdrahtet wird; der HTTP-Anteil wird dort mitgemessen, nicht hier, und ist
> bis dahin **nicht** als grüner Haken zu werten. A-12 nennt „Replay über 35.040 Kerzen …
> 0 Events" — aber `run_replay()` (Teilprojekt A1, unverändert) ruft `staleness()`
> **nirgends** auf. Ein Test gegen den echten Replay-Pfad wäre dort immer grün, egal ob
> die Schwellenformel stimmt — die leere Prüffläche aus Falle 1 des Auftrags. Aufgelöst:
> `poll_once()` nimmt die Uhr als `Clock`-Parameter (Protokoll, nicht `WallClock` fest
> verdrahtet), obwohl `poller.start()` in der Produktion ausschließlich `WallClock`
> verwendet. A-12 wird deshalb gegen `poll_once()` selbst gemessen — mit einer `SimClock`
> synchron zu 35.040 Kerzen aus 2024 (Datenalter dadurch strukturell 0) plus einer
> Gegenprobe mit `WallClock` auf denselben Daten, die sofort `stale` auslöst. Das prüft
> exakt die Eigenschaft, die A-12 meint („die Uhr ist wirklich injiziert, nicht nur
> meistens gesetzt"), an der Stelle, an der sie im Produktivcode tatsächlich existiert.

**Dateien:**
- Neu: `app/aitra/poller.py`
- Geändert: `app/aitra/config.py` (11 neue Felder, Spec Abschnitt 18: `market_data_enabled`,
  `market_interval`, `market_poll_s`, `market_stale_warn_s`, `market_stale_kill_s`,
  `market_clock_skew_warn_s`, `market_clock_skew_kill_s`, `fee_bps`, `slippage_bps`,
  `benchmark_symbol`, `candle_retention_days` — alle **hinten**, alle mit Vorgabewert, B-5)
- Geändert: `app/aitra/ledger.py` (`Ledger.restore()` — Journal-Rekonstruktion, A-14)
- Geändert: `app/aitra/store_run.py` (`ensure_run()` — idempotentes `create_run` für den
  dauerhaften Lauf `"live"`, der jeden Prozess-Neustart überlebt)
- Test neu: `app/tests/test_poller.py`
- Test geändert: `app/tests/test_config.py` (11 neue Felder, Defaults + Validierung)

**Schnittstellen:**
- Nutzt: `binance.BinanceClient`, `binance.BinanceError`, `binance.BinanceRateLimited`,
  `marketdata.staleness`, `marketdata.interval_seconds`, `marketdata.Clock`,
  `marketdata.WallClock`, `marketdata.SimClock`, `store.upsert_candles`, `store.CandleRow`,
  `store_run.get_positions`, `store_run.get_fills`, `store_run.append_equity_points`,
  `store_run.EquityPoint`, `execute.resolve_pending`, `execute.ExecutionContext`,
  `benchmark.BuyAndHold`, `ledger.Ledger`, `ledger.Position`, `risk.RiskEngine`,
  `db.get_state`, `db.set_state`, `db.log_event`, `db.now`
- Stellt bereit:
  - `config.Config` elf neue Felder (siehe Dateien), alle hinten mit Vorgabewert
  - `ledger.Ledger.restore(self, positions: Mapping[str, Position], cash: Decimal) -> None`
  - `store_run.ensure_run(conn, run_id: str, kind: str, started_at: str, code_version: str) -> None`
  - `poller.check_staleness(cfg: Config, clock: Clock, latest_close_time_ms: int | None, server_time_ms: int | None) -> marketdata.Staleness`
  - `poller.PollerContext` (`conn`, `cfg`, `client`, `clock`, `ctx: ExecutionContext`,
    `bench: BuyAndHold | None`, `last_close_time`, `last_candle`, `last_server_time_ms`,
    `last_time_check_ms`, `consecutive_failures`, `was_stale`, `last_lagging_event_ms`)
  - `poller.PollOutcome(ok: bool, staleness: Staleness | None, fills: list[Fill], backoff_s: float)`
  - `poller.poll_once(pc: PollerContext) -> PollOutcome`
  - `poller.build_context(conn, cfg: Config, specs: Mapping[str, money.SymbolSpec], clock: Clock | None = None, client: BinanceClient | None = None) -> PollerContext`
  - `poller.run_forever(pc: PollerContext, stop_event: threading.Event) -> None`
  - `poller.start(conn, cfg: Config, specs: Mapping[str, money.SymbolSpec]) -> tuple[threading.Thread, threading.Event]`

---

- [ ] **Schritt 1: Die scheiternden Tests für `config.py` schreiben**

An `app/tests/test_config.py` anhängen:

```python
def test_market_poller_config_defaults(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    for var in ("MARKET_DATA_ENABLED", "MARKET_INTERVAL", "MARKET_POLL_S",
                "MARKET_STALE_WARN_S", "MARKET_STALE_KILL_S", "MARKET_CLOCK_SKEW_WARN_S",
                "MARKET_CLOCK_SKEW_KILL_S", "FEE_BPS", "SLIPPAGE_BPS", "BENCHMARK_SYMBOL",
                "CANDLE_RETENTION_DAYS"):
        monkeypatch.delenv(var, raising=False)
    cfg = load()
    assert cfg.market_data_enabled is False
    assert cfg.market_interval == "15m"
    assert cfg.market_poll_s == 60
    assert cfg.market_stale_warn_s == 150
    assert cfg.market_stale_kill_s == 300
    assert cfg.market_clock_skew_warn_s == 5
    assert cfg.market_clock_skew_kill_s == 30
    assert cfg.fee_bps == 10.0
    assert cfg.slippage_bps == 5.0
    assert cfg.benchmark_symbol == "BTCUSDC"
    assert cfg.candle_retention_days == 400


def test_market_interval_wird_gegen_die_erlaubte_menge_geprueft(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MARKET_INTERVAL", "7m")
    with pytest.raises(ConfigError):
        load()


def test_market_data_enabled_akzeptiert_nur_bool_text(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MARKET_DATA_ENABLED", "yes")
    with pytest.raises(ConfigError):
        load()
    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    assert load().market_data_enabled is True


def test_benchmark_symbol_wird_gegen_symbol_re_geprueft(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("BENCHMARK_SYMBOL", "btc-usdc")
    with pytest.raises(ConfigError):
        load()
```

- [ ] **Schritt 2: Tests laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q tests/test_config.py'
```

Erwartet: `test_market_poller_config_defaults` → `AttributeError: 'Config' object has no
attribute 'market_data_enabled'`.

- [ ] **Schritt 3: `config.py` — elf neue Felder ergänzen**

`Config` bekommt hinten:

```python
    market_data_enabled: bool = False
    market_interval: str = "15m"
    market_poll_s: int = 60
    market_stale_warn_s: int = 150
    market_stale_kill_s: int = 300
    market_clock_skew_warn_s: int = 5
    market_clock_skew_kill_s: int = 30
    fee_bps: float = 10.0
    slippage_bps: float = 5.0
    benchmark_symbol: str = "BTCUSDC"
    candle_retention_days: int = 400
```

Leser, oberhalb von `load()`:

```python
def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    if raw not in {"true", "false", "1", "0"}:
        raise ConfigError(f"{name}={raw!r} muss true/false sein")
    return raw in {"true", "1"}


def _interval(name: str = "MARKET_INTERVAL", default: str = "15m") -> str:
    from .marketdata import INTERVALS  # lokal: marketdata importiert nichts aus config, aber
                                        # Konsistenz mit dem lokalen Import-Muster von _symbols()
    raw = os.getenv(name, default).strip()
    if raw not in INTERVALS:
        raise ConfigError(f"{name}={raw!r} nicht in {sorted(INTERVALS)}")
    return raw


def _int_range(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        val = int(raw)
    except ValueError as e:
        raise ConfigError(f"{name}={raw!r} ist keine Ganzzahl") from e
    if not (lo <= val <= hi):
        raise ConfigError(f"{name}={val} liegt außerhalb ({lo} ≤ x ≤ {hi})")
    return val


def _benchmark_symbol(name: str = "BENCHMARK_SYMBOL", default: str = "BTCUSDC") -> str:
    from .risk import SYMBOL_RE
    raw = os.getenv(name, default).strip().upper()
    if not SYMBOL_RE.match(raw):
        raise ConfigError(f"{name}={raw!r} ungültig")
    return raw
```

In `load()` vor dem `return` und im `Config(...)`-Aufruf **hinten** ergänzen:

```python
    market_data_enabled = _bool("MARKET_DATA_ENABLED", False)
    market_interval = _interval()
    market_poll_s = _int_range("MARKET_POLL_S", 60, 10, 300)
    market_stale_warn_s = _int_range("MARKET_STALE_WARN_S", 150, 30, 3600)
    market_stale_kill_s = _int_range("MARKET_STALE_KILL_S", 300, 60, 86400)
    if market_stale_kill_s <= market_stale_warn_s:
        raise ConfigError("MARKET_STALE_KILL_S muss größer als MARKET_STALE_WARN_S sein")
    market_clock_skew_warn_s = _int_range("MARKET_CLOCK_SKEW_WARN_S", 5, 1, 60)
    market_clock_skew_kill_s = _int_range("MARKET_CLOCK_SKEW_KILL_S", 30, 5, 600)
    if market_clock_skew_kill_s <= market_clock_skew_warn_s:
        raise ConfigError("MARKET_CLOCK_SKEW_KILL_S muss größer als MARKET_CLOCK_SKEW_WARN_S sein")
    fee_bps = _num("FEE_BPS", 10, 0, 100)
    slippage_bps = _num("SLIPPAGE_BPS", 5, 0, 200)
    benchmark_symbol = _benchmark_symbol()
    candle_retention_days = _int_range("CANDLE_RETENTION_DAYS", 400, 7, 3650)
```

und im `Config(...)`-Aufruf nach `market_symbols=market_symbols,` (Aufgabe 1) anhängen:
`market_data_enabled=market_data_enabled, market_interval=market_interval,
market_poll_s=market_poll_s, market_stale_warn_s=market_stale_warn_s,
market_stale_kill_s=market_stale_kill_s, market_clock_skew_warn_s=market_clock_skew_warn_s,
market_clock_skew_kill_s=market_clock_skew_kill_s, fee_bps=fee_bps, slippage_bps=slippage_bps,
benchmark_symbol=benchmark_symbol, candle_retention_days=candle_retention_days,`

- [ ] **Schritt 4: Tests laufen lassen, Erfolg bestätigen (nur config)**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q tests/test_config.py'
```

Erwartet: **0 failed**.

- [ ] **Schritt 5: Die scheiternden Tests für `poller.py` schreiben**

```python
# app/tests/test_poller.py
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from aitra import db, money, poller, store, store_run
from aitra.binance import BinanceClient
from aitra.config import Config
from aitra.marketdata import SimClock, WallClock

BASIS = "https://api.binance.com"
SPECS = {"BTCUSDC": money.BUILTIN_SPECS["BTCUSDC"]}


class FakeAntwort:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            d = self._body[self._pos:]; self._pos = len(self._body); return d
        d = self._body[self._pos:self._pos + n]; self._pos += len(d); return d

    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeOpener:
    def __init__(self, antworten: dict) -> None:
        self.antworten = antworten

    def open(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        a = self.antworten[urlsplit(url).path]
        if callable(a):
            a = a(url)
        return a


def _body(obj) -> bytes:
    return json.dumps(obj).encode()


def _kerze(open_time: int, interval_s: int, close: str = "81287.03") -> list:
    close_time = open_time + interval_s * 1000 - 1
    return [open_time, "81287.00", "81300.00", "81200.00", close, "12.345",
            close_time, "1", 1, "0", "0", "0"]


def _client_fuer(kerzen_je_symbol: dict[str, list], server_time_ms: int) -> BinanceClient:
    def klines(url):
        qs = parse_qs(urlsplit(url).query)
        sym = qs["symbol"][0]
        return FakeAntwort(_body(kerzen_je_symbol[sym]))

    def zeit(url):
        return FakeAntwort(_body({"serverTime": server_time_ms}))

    opener = FakeOpener({"/api/v3/klines": klines, "/api/v3/time": zeit})
    return BinanceClient(BASIS, opener=opener)


def _cfg(tmp_path: Path, **kw) -> Config:
    return Config(Decimal("10000"), 10, 2, 50, tmp_path, "x" * 32, **kw)


def _pc(tmp_path, client, clock, *, alt_ms: int = 0, cfg=None) -> poller.PollerContext:
    """alt_ms: wie alt die einzige gelieferte Kerze relativ zu clock.now_ms() sein soll."""
    now = clock.now_ms()
    kerzen = {"BTCUSDC": [_kerze(now - alt_ms - 900_000, 900)]}
    server_time_ms = now
    client = client or _client_fuer(kerzen, server_time_ms)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    cfg = cfg or _cfg(tmp_path)
    return poller.build_context(conn, cfg, SPECS, clock=clock, client=client)


def test_poll_once_setzt_kill_switch_bei_stale_und_loggt_genau_ein_ereignis(tmp_path):
    """A-11-Wirkung: bei einem Datenalter über der 15m-Kill-Schwelle (2.700 s)
    wird der Kill Switch gesetzt UND bleibt es bei einem zweiten, weiterhin
    stale-en Poll — aber es entsteht kein zweites Ereignis (Spec 8.2)."""
    clock = SimClock(10_000_000)
    kerzen = {"BTCUSDC": [_kerze(clock.now_ms() - 2_701_000 - 900_000, 900)]}
    client = _client_fuer(kerzen, clock.now_ms())
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    pc = poller.build_context(conn, _cfg(tmp_path), SPECS, clock=clock, client=client)

    outcome1 = poller.poll_once(pc)
    assert outcome1.staleness.status == "stale"
    assert db.get_state(conn, "kill_switch", "0") == "1"
    ereignisse = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE event='MARKET_DATA_STALE'"
    ).fetchone()["c"]
    assert ereignisse == 1, f"genau ein Ereignis erwartet, gemessen: {ereignisse}"

    clock.set(clock.now_ms() + 60_000)  # ein weiterer Poll, weiterhin stale
    poller.poll_once(pc)
    ereignisse2 = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE event='MARKET_DATA_STALE'"
    ).fetchone()["c"]
    assert ereignisse2 == 1, f"kein zweites Ereignis erwartet, gemessen: {ereignisse2}"


def test_poll_once_bleibt_normal_bei_frischen_daten(tmp_path):
    clock = SimClock(10_000_000)
    kerzen = {"BTCUSDC": [_kerze(clock.now_ms() - 100 - 900_000, 900)]}
    client = _client_fuer(kerzen, clock.now_ms())
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    pc = poller.build_context(conn, _cfg(tmp_path), SPECS, clock=clock, client=client)

    outcome = poller.poll_once(pc)
    assert outcome.staleness.status == "ok"
    assert db.get_state(conn, "kill_switch", "0") == "0"
    rows = store.get_candles(conn, "BTCUSDC", "15m", limit=10)
    assert len(rows) == 1, "Pruefflaeche: die gepollte Kerze muss gespeichert sein"


def test_a12_simclock_bleibt_inert_wallclock_gegenprobe(tmp_path):
    """A-12, wie im Kopf dieser Aufgabe aufgelöst: gemessen gegen poll_once()
    selbst (nicht gegen replay.py, das staleness() nirgends aufruft).

    35.040 Kerzen aus 2024 (1 Jahr, 15m). Mit SimClock, synchron zur jeweils
    verarbeiteten Kerze, bleibt das Datenalter strukturell 0: 0 Events, Kill
    Switch bleibt '0'. Die Gegenprobe mit WallClock auf denselben (uralten)
    Daten beweist, dass der Test nicht unabhängig von der Uhr immer grün wäre.
    """
    interval_s = 900
    n = 35_040
    start = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    universum = [_kerze(start + i * interval_s * 1000, interval_s) for i in range(n)]
    server_time_ms = universum[-1][6] + 1

    def opener_liefert_letzte_zwei(u):
        def klines(url):
            qs = parse_qs(urlsplit(url).query)
            return FakeAntwort(_body(u[-2:]))
        def zeit(url):
            return FakeAntwort(_body({"serverTime": server_time_ms}))
        return FakeOpener({"/api/v3/klines": klines, "/api/v3/time": zeit})

    conn = db.connect(tmp_path / "sim.db")
    db.migrate(conn)
    sim_clock = SimClock(start)
    client_sim = BinanceClient(BASIS, opener=opener_liefert_letzte_zwei(universum[:2]))
    pc_sim = poller.build_context(conn, _cfg(tmp_path), SPECS, clock=sim_clock, client=client_sim)

    geprueft = 0
    for i in range(n):
        fenster = universum[max(0, i - 1):i + 1] or [universum[0]]
        client_sim._opener = opener_liefert_letzte_zwei(fenster).antworten and \
            BinanceClient(BASIS, opener=opener_liefert_letzte_zwei(fenster))._opener
        sim_clock.set(universum[i][6] + 1)
        pc_sim.client = BinanceClient(BASIS, opener=opener_liefert_letzte_zwei(fenster))
        poller.poll_once(pc_sim)
        geprueft += 1
    assert geprueft == n, f"Pruefflaeche zu klein: nur {geprueft} von {n} Iterationen"
    assert db.get_state(conn, "kill_switch", "0") == "0"
    assert conn.execute(
        "SELECT COUNT(*) c FROM events WHERE event='MARKET_DATA_STALE'"
    ).fetchone()["c"] == 0

    conn2 = db.connect(tmp_path / "wall.db")
    db.migrate(conn2)
    client_wall = BinanceClient(BASIS, opener=opener_liefert_letzte_zwei(universum[-2:]))
    pc_wall = poller.build_context(conn2, _cfg(tmp_path), SPECS, clock=WallClock(), client=client_wall)
    poller.poll_once(pc_wall)
    assert db.get_state(conn2, "kill_switch", "0") == "1", (
        "Gegenprobe: dieselben (uralten) 2024er-Daten muessen mit WallClock sofort stale ausloesen"
    )


@pytest.mark.parametrize("market_poll_s,erwartet_ueber_10", [(60, False), (1, True)])
def test_a17c_ratenbudget(tmp_path, market_poll_s, erwartet_ueber_10):
    """A-17c: Trockenlauf über 5 simulierte Minuten, 2 Symbole.

    MARKET_POLL_S=60 -> ~4,07 Gewicht/min, klar unter 10 (gruener Fall).
    MARKET_POLL_S=1 (Spec 12, woertlicher Rot-Nachweis) -> 240/min, ueber 10 -
    dieser Parameterwert IST der geforderte Nachweis, dass die Schwelle wirklich
    greift und nicht stumpf ist (vgl. A-4b).
    """
    specs = {"BTCUSDC": money.BUILTIN_SPECS["BTCUSDC"], "BNBUSDC": money.BUILTIN_SPECS["BNBUSDC"]}
    cfg = _cfg(tmp_path, market_symbols=("BTCUSDC", "BNBUSDC"), market_poll_s=market_poll_s)
    clock = SimClock(10_000_000)
    kerzen = {sym: [_kerze(clock.now_ms() - 900_000, 900)] for sym in specs}
    client = _client_fuer(kerzen, clock.now_ms())
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    pc = poller.build_context(conn, cfg, specs, clock=clock, client=client)

    n_polls = max(1, (5 * 60) // market_poll_s)
    for _ in range(n_polls):
        poller.poll_once(pc)
        clock.set(clock.now_ms() + market_poll_s * 1000)
    minuten = (n_polls * market_poll_s) / 60
    gewicht_pro_minute = client.weight_used / minuten

    if erwartet_ueber_10:
        assert gewicht_pro_minute > 10, f"Rot-Nachweis griff nicht: {gewicht_pro_minute:.2f}/min"
    else:
        assert gewicht_pro_minute <= 10, f"{gewicht_pro_minute:.2f}/min > 10/min (A-17c)"
```

- [ ] **Schritt 6: Tests laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q tests/test_poller.py'
```

Erwartet: `ModuleNotFoundError: No module named 'aitra.poller'` (Sammelfehler beim
Import der Testdatei).

- [ ] **Schritt 7: `ledger.py` — `restore()` ergänzen**

Nach `to_portfolio_state()`:

```python
    def restore(self, positions: Mapping[str, Position], cash: Decimal) -> None:
        """Setzt Kasse und Positionen aus einer externen Quelle (dem Journal,
        A-14) statt sie ueber apply() zu erarbeiten — fuer den Start eines
        neuen Prozesses. last_marks bleibt bewusst leer: E-010/Weg A macht
        das fuer resolve_pending() ueberfluessig; ein POST /api/risk/check
        liefert vor dem naechsten mark()-Aufruf ohnehin frische Kurse."""
        self._positions = dict(positions)
        self._cash = cash
```

- [ ] **Schritt 8: `store_run.py` — `ensure_run()` ergänzen**

```python
def ensure_run(conn: sqlite3.Connection, run_id: str, kind: str, started_at: str,
               code_version: str) -> None:
    """Wie create_run(), aber idempotent: der Live-Lauf 'live' entsteht beim
    ersten Prozessstart und ueberlebt jeden weiteren Neustart unveraendert -
    ein zweites create_run() wuerde an runs.run_id (PRIMARY KEY) scheitern."""
    conn.execute(
        "INSERT OR IGNORE INTO runs (run_id, kind, started_at, code_version, params_json) "
        "VALUES (?, ?, ?, ?, '{}')",
        (run_id, kind, started_at, code_version),
    )
    conn.commit()
```

- [ ] **Schritt 9: `poller.py` schreiben**

```python
"""Live-Betrieb: ein Poll-Zyklus pro Aufruf, Backoff bei Fehlern, Kerzen
persistieren, Veraltet-Erkennung -> Kill Switch, schwebende Vorschlaege
ausfuehren, Equity-Schnappschuesse (Spec 9.1, 13 Ansatz 1, E-005, E-006).

poll_once() bekommt die Uhr ueber PollerContext.clock (Clock-Protokoll) und
ruft niemals time.time() selbst auf: Tests starten und stoppen den Poller
deterministisch, ohne auf Wanduhrzeit zu warten (A2-Randbedingung). Nur
start()/run_forever() erzeugen einen echten Thread und eine echte Wartezeit;
sie laufen in keinem Test.

server_time() wird hoechstens alle 15 Minuten erneut abgefragt (Spec 11.3) -
sonst waere der Uhrversatz zwar aktueller, aber das Ratenbudget (A-17c)
verdoppelt sich naeherungsweise ohne Nutzen.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Mapping

from . import db, execute, money, store, store_run
from .benchmark import BuyAndHold
from .binance import BinanceClient, BinanceError, BinanceRateLimited
from .config import Config
from .execute import ExecutionContext
from .ledger import Ledger, Position
from .marketdata import Candle, Clock, Staleness, WallClock, interval_seconds, staleness
from .risk import RiskEngine

log = logging.getLogger("aitra.poller")

BACKOFF_STEPS_S = (60, 120, 240, 600)  # Spec 8.3, Deckel 600 s
_TIME_CHECK_INTERVAL_MS = 900_000       # alle 15 min server_time() (Spec 11.3)


def check_staleness(cfg: Config, clock: Clock, latest_close_time_ms: int | None,
                     server_time_ms: int | None) -> Staleness:
    """Duenner Wrapper um marketdata.staleness() mit den konfigurierten
    Schwellen - eigene Funktion, damit A-11/A-11b/A-12 sie ohne Netz, ohne DB
    und ohne Thread direkt aufrufen koennen (siehe test_marketdata.py, A1)."""
    return staleness(
        clock, latest_close_time_ms, server_time_ms, interval_seconds(cfg.market_interval),
        warn_s=cfg.market_stale_warn_s, kill_s=cfg.market_stale_kill_s,
        clock_skew_warn_s=cfg.market_clock_skew_warn_s,
        clock_skew_kill_s=cfg.market_clock_skew_kill_s,
    )


@dataclass
class PollerContext:
    """Aller veraenderliche Zustand eines Live-Poller-Laufs, testbar ohne Thread."""
    conn: object
    cfg: Config
    client: BinanceClient
    clock: Clock
    ctx: ExecutionContext          # run_id == "live"
    bench: BuyAndHold | None
    last_close_time: dict = field(default_factory=dict)
    last_candle: dict = field(default_factory=dict)
    last_server_time_ms: int | None = None
    last_time_check_ms: int | None = None
    consecutive_failures: int = 0
    was_stale: bool = False
    last_lagging_event_ms: int | None = None


@dataclass(frozen=True)
class PollOutcome:
    ok: bool
    staleness: Staleness | None
    fills: list
    backoff_s: float


def _to_row(k: Candle, source: str = "binance") -> store.CandleRow:
    return store.CandleRow(symbol=k.symbol, interval=k.interval, open_time=k.open_time,
                            close_time=k.close_time, open=k.open, high=k.high, low=k.low,
                            close=k.close, volume=k.volume, source=source, fetched_at=db.now())


def _backoff(consecutive_failures: int) -> float:
    if consecutive_failures <= 0:
        return 0.0
    idx = min(consecutive_failures - 1, len(BACKOFF_STEPS_S) - 1)
    return float(BACKOFF_STEPS_S[idx])


def _apply_staleness(pc: PollerContext, st: Staleness) -> None:
    """Setzt den Kill Switch bei 'stale' und loggt NUR beim Uebergang - sonst
    spammt jeder weitere Poll dasselbe Ereignis, solange der Zustand anhaelt
    (Spec 8.2: MARKET_DATA_LAGGING hoechstens einmal pro 15 min)."""
    db.set_state(pc.conn, "market_data_status", st.status)
    db.set_state(pc.conn, "market_data_age_s", str(st.data_age_s))
    if st.status == "stale":
        if not pc.was_stale:
            db.set_state(pc.conn, "kill_switch", "1")
            db.log_event(pc.conn, "POLLER", "WARN", "MARKET_DATA_STALE",
                         f"Datenalter {st.data_age_s:.0f}s, Uhrversatz {st.clock_skew_s:.0f}s")
        pc.was_stale = True
    else:
        pc.was_stale = False
        if st.status == "warn":
            now = pc.clock.now_ms()
            if pc.last_lagging_event_ms is None or now - pc.last_lagging_event_ms >= 900_000:
                db.log_event(pc.conn, "POLLER", "WARN", "MARKET_DATA_LAGGING",
                             f"Datenalter {st.data_age_s:.0f}s")
                pc.last_lagging_event_ms = now


def poll_once(pc: PollerContext) -> PollOutcome:
    """Ein Poll-Zyklus. Wirft nie: jede binance.py-Ausnahme wird gefangen,
    protokolliert, und fuehrt zu Backoff (Spec 8.3)."""
    pc.ctx.kill_switch = db.get_state(pc.conn, "kill_switch", "0") == "1"

    need_time = (pc.last_time_check_ms is None
                 or pc.clock.now_ms() - pc.last_time_check_ms >= _TIME_CHECK_INTERVAL_MS)
    if need_time:
        try:
            pc.last_server_time_ms = pc.client.server_time()
            pc.last_time_check_ms = pc.clock.now_ms()
        except BinanceError as e:
            db.log_event(pc.conn, "POLLER", "WARN", "MARKET_DATA_FETCH_FAILED", type(e).__name__)
            pc.consecutive_failures += 1
            return PollOutcome(ok=False, staleness=None, fills=[],
                                backoff_s=_backoff(pc.consecutive_failures))
    server_time_ms = pc.last_server_time_ms

    neue_kerzen: dict[str, Candle] = {}
    fehlgeschlagen = False
    for symbol in pc.cfg.market_symbols:
        try:
            kerzen = pc.client.klines(symbol, pc.cfg.market_interval,
                                       server_time_ms=server_time_ms, limit=2)
        except BinanceRateLimited as e:
            db.log_event(pc.conn, "POLLER", "WARN", "MARKET_DATA_RATE_LIMITED",
                         f"{symbol}: retry_after={e.retry_after_s}")
            fehlgeschlagen = True
            continue
        except BinanceError as e:
            db.log_event(pc.conn, "POLLER", "WARN", "MARKET_DATA_FETCH_FAILED",
                         f"{symbol}: {type(e).__name__}")
            fehlgeschlagen = True
            continue
        if kerzen:
            store.upsert_candles(pc.conn, [_to_row(k) for k in kerzen])
            neueste = kerzen[-1]
            pc.last_close_time[symbol] = neueste.close_time
            neue_kerzen[symbol] = neueste
    pc.consecutive_failures = pc.consecutive_failures + 1 if fehlgeschlagen else 0

    latest_close = min(pc.last_close_time.values()) if pc.last_close_time else None
    st = check_staleness(pc.cfg, pc.clock, latest_close, server_time_ms)
    _apply_staleness(pc, st)

    fills = []
    for symbol, candle in neue_kerzen.items():
        fills.extend(execute.resolve_pending(pc.ctx, candle))

    if neue_kerzen:
        marks = {**pc.ctx.ledger.last_marks, **{s: c.close for s, c in neue_kerzen.items()}}
        ts_ms = pc.clock.now_ms()
        v = pc.ctx.ledger.mark(marks, ts_ms=ts_ms)
        bench_equity = None
        if pc.bench is not None:
            for symbol, candle in neue_kerzen.items():
                pc.bench.on_candle(candle, pc.last_candle.get(symbol))
                pc.last_candle[symbol] = candle
            bench_equity = pc.bench.equity(marks, ts_ms=ts_ms)
        store_run.append_equity_points(pc.conn, [store_run.EquityPoint(
            run_id="live", ts_ms=ts_ms, equity=v.equity, cash=v.cash,
            benchmark_equity=bench_equity, exposure_pct=v.exposure_pct,
        )])
    else:
        for symbol, candle in neue_kerzen.items():
            pc.last_candle[symbol] = candle

    return PollOutcome(ok=not fehlgeschlagen, staleness=st, fills=fills,
                        backoff_s=_backoff(pc.consecutive_failures) if fehlgeschlagen else 0.0)


def build_context(conn, cfg: Config, specs: Mapping[str, money.SymbolSpec],
                   clock: Clock | None = None, client: BinanceClient | None = None) -> PollerContext:
    """Baut den kompletten Live-Kontext, inklusive Rekonstruktion aus dem
    Journal (A-14): ein neu gestarteter Prozess kennt seine Positionen nur
    aus fills/positions, nie aus dem Speicher."""
    clock = clock or WallClock()
    client = client or BinanceClient(cfg.binance_base_url)
    store_run.ensure_run(conn, "live", "live", db.now(), "0.3.0")

    ledger = Ledger(starting_cash=cfg.starting_balance, specs=specs,
                     fee_bps=cfg.fee_bps, slippage_bps=cfg.slippage_bps)
    positions = {s: Position(s, p["qty"], p["avg_price"], p["realized_pnl"])
                 for s, p in store_run.get_positions(conn, "live").items()}
    fills = store_run.get_fills(conn, "live")
    cash = fills[-1]["cash_after"] if fills else cfg.starting_balance
    ledger.restore(positions, cash)

    ex_ctx = ExecutionContext(conn=conn, run_id="live", ledger=ledger, engine=RiskEngine(cfg),
                               specs=specs, fee_bps=cfg.fee_bps, slippage_bps=cfg.slippage_bps,
                               clock=clock, kill_switch=db.get_state(conn, "kill_switch", "0") == "1")

    bench = None
    bench_spec = specs.get(cfg.benchmark_symbol)
    if bench_spec is not None:
        store_run.ensure_run(conn, "bench-live", "benchmark", db.now(), "0.3.0")
        bench = BuyAndHold(cfg, conn, "bench-live", cfg.benchmark_symbol, bench_spec,
                            cfg.fee_bps, cfg.slippage_bps, clock)

    return PollerContext(conn=conn, cfg=cfg, client=client, clock=clock, ctx=ex_ctx, bench=bench)


def run_forever(pc: PollerContext, stop_event: threading.Event) -> None:
    """Thread-Ziel: poll_once() alle market_poll_s Sekunden, mit Backoff bei
    Fehlern. Endet, sobald stop_event gesetzt ist."""
    while not stop_event.is_set():
        outcome = poll_once(pc)
        wait_s = outcome.backoff_s if outcome.backoff_s > 0 else pc.cfg.market_poll_s
        stop_event.wait(wait_s)


def start(conn, cfg: Config, specs: Mapping[str, money.SymbolSpec]) -> tuple[threading.Thread, threading.Event]:
    """Startet den Daemon-Thread (Spec 13, Ansatz 1). Aufrufer: create_app()
    in web.py, nur wenn cfg.market_data_enabled (Aufgabe 6)."""
    pc = build_context(conn, cfg, specs)
    stop_event = threading.Event()
    thread = threading.Thread(target=run_forever, args=(pc, stop_event),
                               name="aitra-poller", daemon=True)
    thread.start()
    return thread, stop_event
```

- [ ] **Schritt 10: Tests laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q -m "not slow"'
```

Erwartet: **0 failed**. `test_a12_simclock_bleibt_inert_wallclock_gegenprobe` läuft über
35.040 Iterationen und wird als `@pytest.mark.slow` markiert (Dekorator ergänzen), damit
`build.sh` (Budget 12 s) nicht ausgebremst wird — Präzedenzfall A-9 aus A1.

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q tests/test_poller.py'
```

Erwartet: **7 passed** (inklusive der beiden `test_a17c_ratenbudget`-Parametrisierungen).

- [ ] **Schritt 11: Rot-Nachweis führen (vier Nachweise)**

| # | Eingebauter Fehler | Erwartete rote Ausgabe |
|---|---|---|
| 1 | In `_apply_staleness()` das `if not pc.was_stale:` weglassen (immer loggen) | `test_poll_once_setzt_kill_switch_bei_stale_und_loggt_genau_ein_ereignis` → `AssertionError: kein zweites Ereignis erwartet, gemessen: 2` |
| 2 | In `_apply_staleness()` die Zeile `db.set_state(pc.conn, "kill_switch", "1")` auskommentieren | `test_poll_once_setzt_kill_switch_bei_stale_und_loggt_genau_ein_ereignis` → `AssertionError: assert '0' == '1'` |
| 3 | In `poll_once()` `pc.clock.now_ms()` durch `WallClock().now_ms()` ersetzen (Uhr nicht mehr injiziert) | `test_a12_simclock_bleibt_inert_wallclock_gegenprobe` → `AssertionError: assert '1' == '0'` (der SimClock-Teil des Tests schlägt fehl, weil intern doch die Wanduhr greift) |
| 4 | `MARKET_POLL_S=1` statt `60` betreiben (Spec 12, wörtlicher Rot-Nachweis für A-17c) | `test_a17c_ratenbudget[1-True]` bliebe grün — das ist beabsichtigt: würde man denselben Grenzwert `<= 10` fälschlich auch auf diesen Fall anwenden, entstünde `AssertionError: 240.00/min > 10/min (A-17c)`, was zeigt, dass die Schwelle wirklich diskriminiert (Muster wie A-4b) |

Jeden Fehler einzeln einbauen, Ausgabe wörtlich protokollieren, zurücknehmen, Suite
erneut grün. *Wären die Tests ohne die Fehler grün gewesen?* Ja für alle vier — Schritt 10
hat sie grün gemessen.

- [ ] **Schritt 12: Commit**

```bash
git add app/aitra/poller.py app/aitra/config.py app/aitra/ledger.py app/aitra/store_run.py \
        app/tests/test_poller.py app/tests/test_config.py
git commit -m "feat(poller): Live-Thread mit Backoff und Veraltet-Erkennung (A-11/A-11b/A-12/A-17c)

poll_once() ist der testbare Kern: Kerzen holen, speichern, Veraltet pruefen
(intervallrelative Schwellen aus A1, hier erstmals verdrahtet), schwebende
Vorschlaege aufloesen (E-010, Weg A), Equity-Schnappschuss anhaengen. Kill
Switch wird bei 'stale' gesetzt, ein Ereignis nur beim Uebergang, nicht bei
jedem weiteren Poll.

server_time() wird hoechstens alle 15 Minuten neu abgefragt (A-17c: ~4,07
Gewicht/min bei 2 Symbolen und 60s-Poll).

Elf neue Config-Felder (Spec 18), alle hinten mit Vorgabewert (B-5).
Ledger.restore() und store_run.ensure_run() tragen die A-14-Rekonstruktion
eines neu gestarteten Prozesses.

A-12 gegen poll_once() selbst gemessen statt gegen run_replay() (das
staleness() nirgends aufruft) - Begruendung im Aufgabenkopf des Plans.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Aufgabe 6: `web.py` — neue Endpunkte, geführte Kasse, echte Positionen

**Macht grün:** A-19a (nur die Endpunkt-Anteile: geführte Kasse, echte `positions` und
`trades_total`, `/api/health` mit echtem `market_data`/`paper_engine`, `POST /api/risk/check`
über `execute_proposal()`. Der volle Rauchlauf mit `docker compose up -d` gehört zur
Abnahme, nicht zu dieser Aufgabe — hier wird die Datenlage am Endpunkt geprüft, nicht der
Container).

**Dateien:**
- Geändert: `app/aitra/web.py`
- Geändert: `app/aitra/ledger.py` (`realized_pnl_per_sell()` — reine Funktion für die
  Trefferquote, Spec 7.1)
- Test geändert: `app/tests/test_api.py`
- Test geändert: `app/tests/test_ledger.py` (neuer Test für `realized_pnl_per_sell`)

**Schnittstellen:**
- Nutzt: `poller.build_context`, `poller.start`, `store.get_candles`, `store.get_symbol_spec`,
  `store_run.get_positions`, `store_run.get_fills`, `store_run.get_equity_curve`,
  `execute.execute_proposal`, `execute.ExecutionContext`, `ledger.Ledger`, `ledger.Position`,
  `marketdata.interval_seconds`, `money.BUILTIN_SPECS`, `money.from_text`, `money.to_text`
- Stellt bereit:
  - `ledger.realized_pnl_per_sell(fills: Sequence[Mapping]) -> list[Decimal]` — repliziert
    die mengengewichtete Durchschnittspreis-Buchhaltung aus `_book_buy`/`_book_sell` über
    eine bereits gespeicherte Fill-Liste, je Symbol chronologisch, und liefert je SELL-Fill
    den realisierten Gewinn/Verlust
  - `GET /api/market/candles?symbol=…&interval=…&limit=500` → `list[dict]` (Geld als String, E-007)
  - `GET /api/equity-curve?run_id=live&limit=500` → `{run_id, points: [{ts_ms, equity, cash, benchmark, exposure_pct}]}`
  - `GET /api/status` erweitert: `positions: list[dict]`, `trades_total: int`,
    `cash` **geführt** (aus dem letzten Fill oder `starting_balance`, nicht abgeleitet — B-3),
    `benchmark: {symbol, equity, return_pct, max_drawdown_pct, started_at}`, `alpha_pct`,
    `hit_rate_pct`, `max_drawdown_pct`
  - `GET /api/health` erweitert: `market_data` aus `db.get_state(conn,"market_data_status",…)`,
    `paper_engine` aus `thread.is_alive()` (`"idle"` wenn `MARKET_DATA_ENABLED=false`,
    `"ok"`/`"error"` sonst); **HTTP 503, wenn `market_data == "stale"`**
  - `POST /api/risk/check` ruft `execute.execute_proposal()` mit `next_candle=None` (live
    kennt die Folgekerze nie sofort, E-006) und liefert zusätzlich `status` und
    `expected_fill_after_ms`; ohne gespeicherte Kerze für das Symbol: `503`

---

- [ ] **Schritt 1: Den scheiternden Test für `realized_pnl_per_sell` schreiben**

An `app/tests/test_ledger.py` anhängen:

```python
def test_realized_pnl_per_sell_repliziert_die_buchhaltung():
    """Zwei Kaeufe zu unterschiedlichen Preisen (mengengewichteter Schnitt),
    dann ein Teilverkauf mit Gewinn und einer mit Verlust — von Hand
    nachgerechnet, nicht aus dem Lauf uebernommen.

    Kauf 1: 10 @ 100 -> avg=100. Kauf 2: 10 @ 120 -> avg=(10*100+10*120)/20=110.
    Verkauf 1: 5 @ 130 -> realized=(130-110)*5=100 (Gewinn).
    Verkauf 2: 5 @ 90  -> realized=(90-110)*5=-100 (Verlust).
    """
    fills = [
        {"symbol": "BTCUSDC", "side": "BUY", "qty": Decimal("10"), "price": Decimal("100"), "id": 1},
        {"symbol": "BTCUSDC", "side": "BUY", "qty": Decimal("10"), "price": Decimal("120"), "id": 2},
        {"symbol": "BTCUSDC", "side": "SELL", "qty": Decimal("5"), "price": Decimal("130"), "id": 3},
        {"symbol": "BTCUSDC", "side": "SELL", "qty": Decimal("5"), "price": Decimal("90"), "id": 4},
    ]
    ergebnis = ledger.realized_pnl_per_sell(fills)
    assert ergebnis == [Decimal("100"), Decimal("-100")]


def test_realized_pnl_per_sell_haelt_symbole_getrennt():
    """Pruefflaeche: zwei Symbole duerfen sich nicht gegenseitig beeinflussen -
    ein Bug, der alle Fills in EINEN Topf wirft, waere sonst unbemerkt gruen,
    solange nur ein Symbol im ersten Test vorkommt."""
    fills = [
        {"symbol": "BTCUSDC", "side": "BUY", "qty": Decimal("1"), "price": Decimal("100"), "id": 1},
        {"symbol": "BNBUSDC", "side": "BUY", "qty": Decimal("1"), "price": Decimal("500"), "id": 2},
        {"symbol": "BTCUSDC", "side": "SELL", "qty": Decimal("1"), "price": Decimal("110"), "id": 3},
        {"symbol": "BNBUSDC", "side": "SELL", "qty": Decimal("1"), "price": Decimal("490"), "id": 4},
    ]
    ergebnis = ledger.realized_pnl_per_sell(fills)
    assert len(ergebnis) == 2, f"Pruefflaeche: 2 SELL-Fills erwartet, gemessen {len(ergebnis)}"
    assert Decimal("10") in ergebnis and Decimal("-10") in ergebnis
```

- [ ] **Schritt 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q tests/test_ledger.py -k realized_pnl_per_sell'
```

Erwartet: `AttributeError: module 'aitra.ledger' has no attribute 'realized_pnl_per_sell'`.

- [ ] **Schritt 3: `ledger.py` — `realized_pnl_per_sell()` ergänzen**

Als freistehende Funktion, nach der Klasse `Ledger`:

```python
def realized_pnl_per_sell(fills: Sequence[Mapping]) -> list[Decimal]:
    """Repliziert die mengengewichtete Durchschnittspreis-Buchhaltung aus
    Ledger._book_buy()/_book_sell() ueber eine bereits gespeicherte Fill-Liste,
    chronologisch je Symbol, und liefert je SELL-Fill den realisierten Gewinn
    oder Verlust. Fuer web.py's Trefferquote (Spec 7.1) — kein neuer
    Datenbank-Zustand, da fills.realized_pnl je Fill nicht gespeichert wird
    (nur kumulativ in positions.realized_pnl).
    """
    avg_price: dict[str, Decimal] = {}
    qty: dict[str, Decimal] = {}
    ergebnis: list[Decimal] = []
    for f in sorted(fills, key=lambda f: f["id"]):
        sym = f["symbol"]
        if f["side"] == "BUY":
            alte_qty = qty.get(sym, Decimal(0))
            neue_qty = alte_qty + f["qty"]
            alter_avg = avg_price.get(sym, Decimal(0))
            avg_price[sym] = ((alte_qty * alter_avg + f["qty"] * f["price"]) / neue_qty
                               if neue_qty > 0 else Decimal(0))
            qty[sym] = neue_qty
        else:
            realized = (f["price"] - avg_price.get(sym, Decimal(0))) * f["qty"]
            ergebnis.append(realized)
            qty[sym] = qty.get(sym, Decimal(0)) - f["qty"]
    return ergebnis
```

Importe in `ledger.py` oben ergänzen: `from typing import Mapping, Sequence` (Mapping ist
bereits importiert; `Sequence` ergänzen).

- [ ] **Schritt 4: Test laufen lassen, Erfolg bestätigen (nur ledger)**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q tests/test_ledger.py'
```

Erwartet: **0 failed**.

- [ ] **Schritt 5: Die scheiternden Tests für `web.py` schreiben**

An `app/tests/test_api.py` anhängen (Kopf um `from decimal import Decimal`, `from aitra
import db, money, store, store_run` und `from aitra.marketdata import Candle` ergänzen,
falls nicht vorhanden):

```python
def _mit_marktdaten(app, symbol="BTCUSDC", interval="15m", preis="81287.03"):
    """Legt eine einzelne, bereits geschlossene Kerze fuer symbol an - Grundlage
    fuer jeden Test, der /api/risk/check oder /api/status mit echten Zahlen
    braucht."""
    with app.app_context():
        from flask import g
        conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
        conn.row_factory__ = None
    conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
    store.upsert_candles(conn, [
        store.CandleRow(symbol=symbol, interval=interval, open_time=0, close_time=899_999,
                         open=Decimal(preis), high=Decimal(preis), low=Decimal(preis),
                         close=Decimal(preis), volume=Decimal("1"), source="fixture",
                         fetched_at="2026-01-01T00:00:00Z"),
    ])
    conn.close()


def test_status_positionen_und_trades_total_sind_echt(client, app):
    _mit_marktdaten(app)
    conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
    store_run.ensure_run(conn, "live", "live", db.now(), "0.3.0")
    store_run.insert_fill(conn, run_id="live", decision_id=None, symbol="BTCUSDC", side="BUY",
                           candle_open_time=900_000, price=Decimal("81287.03"), qty=Decimal("0.01"),
                           gross_quote=Decimal("812.8703"), fee=Decimal("0.81"),
                           net_quote=Decimal("813.6803"), cash_after=Decimal("9186.3197"),
                           fee_bps=10.0, slippage_bps=5.0, ts=db.now())
    store_run.upsert_position(conn, run_id="live", symbol="BTCUSDC", qty=Decimal("0.01"),
                               avg_price=Decimal("81287.03"), realized_pnl=Decimal("0"),
                               updated_at=db.now())
    conn.close()

    s = client.get("/api/status").get_json()
    assert s["trades_total"] == 1, f"Pruefflaeche: {s['trades_total']} statt 1"
    assert len(s["positions"]) == 1
    assert s["positions"][0]["symbol"] == "BTCUSDC"
    assert s["positions"][0]["qty"] == "0.01000000"
    assert s["cash"] == "9186.31970000", "Kasse muss aus cash_after des letzten Fills stammen (B-3)"


def test_status_kasse_bleibt_startkapital_ohne_fills(client):
    s = client.get("/api/status").get_json()
    assert s["cash"] == "100.00000000"
    assert s["trades_total"] == 0
    assert s["positions"] == []


def test_health_meldet_market_data_wirklich(client, app):
    conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
    db.set_state(conn, "market_data_status", "warn")
    conn.close()
    h = client.get("/api/health").get_json()
    assert h["market_data"] == "warn", f"Pruefflaeche: {h['market_data']!r} statt 'warn'"


def test_health_gibt_503_bei_stale_market_data(client, app):
    conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
    db.set_state(conn, "market_data_status", "stale")
    conn.close()
    r = client.get("/api/health")
    assert r.status_code == 503


def test_health_paper_engine_idle_ohne_market_data_enabled(client):
    h = client.get("/api/health").get_json()
    assert h["paper_engine"] == "idle"


def test_risk_check_liefert_pending_fill_ueber_execute_proposal(client, app):
    _mit_marktdaten(app)
    h = {"X-Admin-Token": TOKEN}
    r = client.post("/api/risk/check", headers=h,
                     json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 5})
    body = r.get_json()
    assert body["status"] == "pending_fill", f"Pruefflaeche: {body.get('status')!r}"
    assert body["expected_fill_after_ms"] == 900_000
    # Nadeloehr: kein sofortiger Fill (E-006) - trades_total bleibt 0
    assert client.get("/api/status").get_json()["trades_total"] == 0


def test_risk_check_ohne_marktdaten_liefert_503(client):
    h = {"X-Admin-Token": TOKEN}
    r = client.post("/api/risk/check", headers=h,
                     json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 5})
    assert r.status_code == 503


def test_market_candles_endpoint(client, app):
    _mit_marktdaten(app)
    r = client.get("/api/market/candles?symbol=BTCUSDC&interval=15m&limit=10")
    assert r.status_code == 200
    body = r.get_json()
    assert len(body) == 1, f"Pruefflaeche: {len(body)} Kerzen statt 1"
    assert body[0]["close"] == "81287.03000000"
    assert isinstance(body[0]["close"], str), "Geld als String (E-007)"


def test_equity_curve_endpoint(client, app):
    conn = db.connect(app.config["AITRA"].data_dir / "aitra.db")
    store_run.ensure_run(conn, "live", "live", db.now(), "0.3.0")
    store_run.append_equity_points(conn, [store_run.EquityPoint(
        run_id="live", ts_ms=900_000, equity=Decimal("10000"), cash=Decimal("9186.32"),
        benchmark_equity=Decimal("10050"), exposure_pct=8.1,
    )])
    conn.close()
    body = client.get("/api/equity-curve?run_id=live&limit=500").get_json()
    assert body["run_id"] == "live"
    assert len(body["points"]) == 1, f"Pruefflaeche: {len(body['points'])} Punkte statt 1"
    assert isinstance(body["points"][0]["equity"], str)
    assert body["points"][0]["benchmark"] == "10050.00000000"
```

Ergänzend braucht `client`/`app` in `test_api.py` eine `app`-Fixture (bisher gibt nur
`client` den Flask-Client zurück, nicht die App selbst):

```python
@pytest.fixture
def app(tmp_path):
    return create_app(Config(100, 10, 2, 50, tmp_path, TOKEN))


@pytest.fixture
def client(app):
    return app.test_client()
```

(Diese ersetzt die bisherige `client`-Fixture in `test_api.py`, Zeile 8-10 — `app` wird
jetzt separat gebraucht, weil mehrere neue Tests eine zweite Verbindung zur selben
`aitra.db` öffnen müssen, um Fixture-Daten einzuspielen.)

- [ ] **Schritt 6: Tests laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q tests/test_api.py'
```

Erwartet, wörtlich zu protokollieren:
- `test_status_positionen_und_trades_total_sind_echt` → `AssertionError: Pruefflaeche: 0 statt 1`
- `test_health_meldet_market_data_wirklich` → `AssertionError: Pruefflaeche: 'not_configured' statt 'warn'`
- `test_health_gibt_503_bei_stale_market_data` → `assert 200 == 503`
- `test_risk_check_liefert_pending_fill_ueber_execute_proposal` → `KeyError: 'status'`
- `test_market_candles_endpoint` → `404 NOT FOUND`
- `test_equity_curve_endpoint` → `404 NOT FOUND`

- [ ] **Schritt 7: `web.py` — geführte Kasse, echte Positionen, neue Endpunkte**

`create_app()` importiert und verdrahtet den Poller-Startpunkt:

```python
from . import VERSION, db, marketdata, money, poller, store, store_run
from .config import Config, load
from .execute import ExecutionContext, execute_proposal
from .ledger import Ledger, Position, realized_pnl_per_sell
from .risk import PortfolioState, Proposal, RiskEngine
```

Nach dem bestehenden Migrations-/Initialisierungsblock in `create_app()`:

```python
    specs = {s: store.get_symbol_spec(db.connect(db_path), s) or money.BUILTIN_SPECS.get(s)
             for s in cfg.market_symbols}
    specs = {s: sp for s, sp in specs.items() if sp is not None}
    poller_thread: threading.Thread | None = None
    if cfg.market_data_enabled:
        poller_thread, _ = poller.start(db.connect(db_path), cfg, specs)
    app.config["AITRA_POLLER_THREAD"] = poller_thread
    app.config["AITRA_SPECS"] = specs
```

(`import threading` oben ergänzen.)

`portfolio()` entfällt ersatzlos — geführte Kasse statt abgeleiteter (B-3) heißt: es gibt
keinen Standzustand mehr, den man einmal berechnet und wiederverwendet. Stattdessen zwei
kleine, gemeinsam genutzte Primitive, aus denen `status()` und `risk_check()` sich direkt
bedienen (kein `portfolio()`, das selbst nie wieder aufgerufen würde):

```python
    def _live_ledger() -> Ledger:
        """Rekonstruiert den Live-Ledger aus dem Journal (A-14) - bei jedem
        Request neu, absichtlich: es gibt keinen mit dem Poller-Thread
        geteilten Ledger-Zustand, um Threading-Kollisionen zwischen dem
        Poller (poller.py) und gunicorn-Request-Threads zu vermeiden."""
        ledger = Ledger(starting_cash=cfg.starting_balance, specs=app.config["AITRA_SPECS"],
                         fee_bps=cfg.fee_bps, slippage_bps=cfg.slippage_bps)
        positions = {s: Position(s, p["qty"], p["avg_price"], p["realized_pnl"])
                     for s, p in store_run.get_positions(conn(), "live").items()}
        fills = store_run.get_fills(conn(), "live")
        cash = fills[-1]["cash_after"] if fills else cfg.starting_balance
        ledger.restore(positions, cash)
        return ledger

    def _last_prices() -> dict:
        """Letzter bekannter Schlusskurs je konfiguriertem Symbol - deckt jede
        gehaltene Position ab, weil Positionen nur in konfigurierten Symbolen
        entstehen koennen (execute_proposal lehnt alles andere mit NO_SPEC ab)."""
        preise = {}
        for sym in app.config["AITRA_SPECS"]:
            rows = store.get_candles(conn(), sym, cfg.market_interval, limit=1)
            if rows:
                preise[sym] = rows[-1].close
        return preise
```

(`from decimal import Decimal` oben ergänzen, falls fehlend.)

`/api/status` erweitert:

```python
    @app.get("/api/status")
    def status():
        ledger = _live_ledger()
        marks = _last_prices()
        v = ledger.mark(marks, ts_ms=int(time.time() * 1000))
        pf = ledger.to_portfolio_state(v, Decimal(db.get_state(conn(), "sod_equity", str(cfg.starting_balance))))
        ks = kill_switch()
        fills = store_run.get_fills(conn(), "live")
        positions = [
            {"symbol": s, "qty": money.to_text(p["qty"]), "avg_price": money.to_text(p["avg_price"]),
             "realized_pnl": money.to_text(p["realized_pnl"])}
            for s, p in store_run.get_positions(conn(), "live").items() if p["qty"] != 0
        ]
        curve = store_run.get_equity_curve(conn(), "live", limit=100_000)
        bench_equity = curve[-1]["benchmark_equity"] if curve else None
        max_dd = Decimal(0)
        peak = curve[0]["equity"] if curve else None
        for pt in curve:
            peak = max(peak, pt["equity"])
            if peak > 0:
                max_dd = max(max_dd, (peak - pt["equity"]) / peak * 100)
        sells = [f for f in fills if f["side"] == "SELL"]
        pnls = realized_pnl_per_sell(fills)
        hit_rate = round(100 * sum(1 for p in pnls if p > 0) / len(pnls), 2) if pnls else None
        return jsonify(
            version=VERSION, mode=cfg.trading_mode, live_locked=cfg.live_locked,
            equity=v.equity, cash=money.to_text(ledger.cash), starting_balance=cfg.starting_balance,
            pnl=round(v.equity - cfg.starting_balance, 8),
            daily_pnl=round(v.equity - pf.start_of_day_equity, 8),
            daily_loss_pct=round(engine.daily_loss_pct(pf), 4),
            exposure_pct=v.exposure_pct, risk="BLOCKED" if ks else "NORMAL", kill_switch=ks,
            positions=positions, trades_total=len(fills),
            benchmark=dict(symbol=cfg.benchmark_symbol,
                            equity=(money.to_text(bench_equity) if bench_equity is not None else None)),
            alpha_pct=None, hit_rate_pct=hit_rate, max_drawdown_pct=float(max_dd),
            limits=dict(max_position_pct=cfg.max_position_pct, max_daily_loss_pct=cfg.max_daily_loss_pct,
                        max_total_exposure_pct=cfg.max_total_exposure_pct),
        )
```

`/api/health` erweitert:

```python
    @app.get("/api/health")
    def health():
        checks: dict[str, str] = {}
        try:
            conn().execute("SELECT 1").fetchone()
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "error"
        free_gb = shutil.disk_usage(cfg.data_dir).free / 1e9
        checks["disk"] = "ok" if free_gb > 1 else "low"
        checks["risk_engine"] = "ok"
        thread = app.config.get("AITRA_POLLER_THREAD")
        if not cfg.market_data_enabled:
            checks["paper_engine"] = "idle"
        else:
            checks["paper_engine"] = "ok" if thread is not None and thread.is_alive() else "error"
        checks["market_data"] = db.get_state(conn(), "market_data_status", "not_configured")
        healthy = (checks["database"] == "ok" and checks["disk"] == "ok"
                   and checks["market_data"] != "stale" and checks["paper_engine"] != "error")
        body = dict(status="healthy" if healthy else "degraded", version=VERSION,
                    trading_mode=cfg.trading_mode,
                    kill_switch=kill_switch() if checks["database"] == "ok" else None,
                    uptime_s=int(time.time() - STARTED), disk_free_gb=round(free_gb, 1),
                    server_time=db.now(), **checks)
        return jsonify(body), 200 if healthy else 503
```

`/api/risk/check` ruft jetzt `execute_proposal()`:

```python
    @app.post("/api/risk/check")
    def risk_check():
        if not authorized():
            return jsonify(ok=False, error="Admin-Token erforderlich"), 401
        if (err := need_json()) is not None:
            return err
        data = request.get_json(silent=True) or {}
        try:
            p = Proposal(
                symbol=str(data.get("symbol", "")).upper().strip(),
                action=str(data.get("action", "")).upper().strip(),
                position_pct=float(data.get("position_pct", 0) or 0),
                confidence=None if data.get("confidence") is None else float(data["confidence"]),
            )
        except (TypeError, ValueError):
            return jsonify(ok=False, error="Ungültige Werte"), 422

        rows = store.get_candles(conn(), p.symbol, cfg.market_interval, limit=1) if p.symbol else []
        if not rows and p.action != "WAIT":
            return jsonify(ok=False, error=f"Keine Marktdaten für {p.symbol}"), 503
        ref_price = rows[-1].close if rows else Decimal(0)
        ts_ms = rows[-1].close_time if rows else int(time.time() * 1000)

        specs = app.config["AITRA_SPECS"]
        ledger = _live_ledger()
        ex_ctx = ExecutionContext(conn=conn(), run_id="live", ledger=ledger, engine=engine,
                                   specs=specs, fee_bps=cfg.fee_bps, slippage_bps=cfg.slippage_bps,
                                   clock=poller.WallClock(), kill_switch=kill_switch())
        sod = Decimal(db.get_state(conn(), "sod_equity", str(cfg.starting_balance)))
        marks = _last_prices()
        result = execute_proposal(
            p, ex_ctx, marks=marks, ts_ms=ts_ms, ref_price=ref_price, start_of_day_equity=sod,
            strategy_version=str(data.get("strategy_version", "manual"))[:40],
            reason=str(data.get("reason", ""))[:500], next_candle=None,
        )
        if result.code == "DAILY_LOSS":
            db.set_state(conn(), "kill_switch", "1")
            db.log_event(conn(), "RISK_ENGINE", "WARN", "KILL_SWITCH_ENGAGED", "Tagesverlustlimit erreicht")
        if result.status == "rejected":
            db.log_event(conn(), "RISK_ENGINE", "WARN", "ORDER_REJECTED", f"{p.symbol} {result.code}")
        return jsonify(
            ok=True, decision_id=result.decision_id, approved=result.approved,
            code=result.code, reason=result.reason, status=result.status,
            expected_fill_after_ms=(rows[-1].close_time + 1 if rows else None),
        )
```

Neue lesende Endpunkte:

```python
    @app.get("/api/market/candles")
    def market_candles():
        symbol = request.args.get("symbol", "").upper().strip()
        interval = request.args.get("interval", cfg.market_interval)
        limit = min(max(request.args.get("limit", 500, type=int), 1), 1000)
        rows = store.get_candles(conn(), symbol, interval, limit=limit)
        return jsonify([
            dict(open_time=r.open_time, close_time=r.close_time, open=money.to_text(r.open),
                 high=money.to_text(r.high), low=money.to_text(r.low), close=money.to_text(r.close),
                 volume=money.to_text(r.volume))
            for r in rows
        ])

    @app.get("/api/equity-curve")
    def equity_curve():
        run_id = request.args.get("run_id", "live")
        limit = min(max(request.args.get("limit", 500, type=int), 1), 5000)
        points = store_run.get_equity_curve(conn(), run_id, limit=limit)
        return jsonify(run_id=run_id, points=[
            dict(ts_ms=p["ts_ms"], equity=money.to_text(p["equity"]), cash=money.to_text(p["cash"]),
                 benchmark=(money.to_text(p["benchmark_equity"]) if p["benchmark_equity"] is not None else None),
                 exposure_pct=p["exposure_pct"])
            for p in points
        ])
```

- [ ] **Schritt 8: Tests laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q'
```

Erwartet: **0 failed**.

- [ ] **Schritt 9: Rot-Nachweis führen (fünf Nachweise)**

| # | Eingebauter Fehler | Erwartete rote Ausgabe |
|---|---|---|
| 1 | In `status()` `cash=money.to_text(ledger.cash)` durch die alte Formel `cash=v.equity*(1-v.exposure_pct/100)` (B-3-Regression) ersetzen | `test_status_positionen_und_trades_total_sind_echt` → `AssertionError: Kasse muss aus cash_after des letzten Fills stammen (B-3)` |
| 2 | `positions=positions` durch `positions=[]` ersetzen | `test_status_positionen_und_trades_total_sind_echt` → `AssertionError: assert 0 == 1` |
| 3 | `checks["market_data"] = db.get_state(...)` durch `checks["market_data"] = "not_configured"` (fest) ersetzen | `test_health_meldet_market_data_wirklich` → `AssertionError: Pruefflaeche: 'not_configured' statt 'warn'` |
| 4 | In `health()` die Bedingung `and checks["market_data"] != "stale"` aus `healthy` entfernen | `test_health_gibt_503_bei_stale_market_data` → `assert 200 == 503` |
| 5 | In `risk_check()` `next_candle=None` durch Aufruf von `engine.check()` direkt ersetzen (alter Pfad ohne `execute_proposal`) | `test_risk_check_liefert_pending_fill_ueber_execute_proposal` → `KeyError: 'status'` |

Jeden Fehler einzeln einbauen, Ausgabe wörtlich protokollieren, zurücknehmen, Suite
erneut grün. *Wären die Tests ohne die Fehler grün gewesen?* Ja für alle fünf — Schritt 8
hat sie grün gemessen.

- [ ] **Schritt 10: Commit**

```bash
git add app/aitra/web.py app/aitra/ledger.py app/tests/test_api.py app/tests/test_ledger.py
git commit -m "feat(web): geführte Kasse, echte Positionen, Poller-Wiring, neue Endpunkte (A-19a)

/api/status: cash kommt aus dem letzten Fill (B-3, nicht mehr abgeleitet),
positions und trades_total sind echt (aus store_run), benchmark/hit_rate_pct/
max_drawdown_pct neu. /api/health kennt market_data (aus dem Poller-Zustand)
und paper_engine (thread.is_alive()) wirklich, HTTP 503 bei stale.

POST /api/risk/check ruft execute_proposal() statt nur engine.check() - liefert
jetzt status und expected_fill_after_ms (E-006); ohne gespeicherte Kerze 503
statt eines geratenen Referenzpreises.

Neu: GET /api/market/candles, GET /api/equity-curve. create_app() startet den
Poller-Thread nur bei MARKET_DATA_ENABLED=true.

ledger.realized_pnl_per_sell() traegt die Trefferquote (Spec 7.1), ohne die
Fill-Tabelle um eine Spalte zu erweitern.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Aufgabe 7: `app/static/index.html` — Chart, offene Positionen, KPIs, schwebende Vorschläge

**Macht grün:** A-19b (nur die Verdrahtungs-Anteile am Endpunkt, siehe Lücke L-1 unten).

> **Lücke L-1 bleibt bestehen — ausdrücklich, nicht als Formsache.** Es gibt in diesem
> Teilprojekt **keinen automatisierten Browsertest**. Ein Blick in Safari (WebKit) oder
> Firefox nach dem Bauen ist **kein Test**: Er erzeugt keinen Rot-Nachweis, er wiederholt
> sich nicht bei der nächsten Änderung, und er ist streng genommen nicht einmal
> Bestandteil dieser Aufgabe, sondern eine separate, datierte Beobachtung, die höchstens
> als Screenshot in `docs/abnahme/` landet. **A-19b prüft in dieser Aufgabe ausschließlich
> zwei Dinge:** (1) die Datenlage an den Endpunkten (`/api/status`, `/api/equity-curve`,
> `/api/market/candles`) — das ist bereits durch Aufgabe 6 grün — und (2) dass der
> HTML/JS-Quelltext die richtigen Endpunkte anspricht und die richtige Anzahl an
> Zeichenelementen anlegt. Ob im Browser tatsächlich ein Pixel erscheint, prüft **nichts**
> in dieser Aufgabe. Diese Lücke darf in keiner Abnahme als grüner Haken auftauchen — sie
> gehört unter „bewusst offen" (Spec Abschnitt 12, L-1), nicht unter „erledigt".

**Dateien:**
- Geändert: `app/static/index.html`
- Test neu: `app/tests/test_dashboard.py`

**Schnittstellen:**
- Nutzt (clientseitig, per `fetch`): `GET /api/status` (jetzt mit `positions`,
  `hit_rate_pct`, `max_drawdown_pct`, `benchmark`), `GET /api/equity-curve?run_id=live&limit=500`,
  `GET /api/decisions` (unverändert, liefert bereits `pending_since_ms`/`pending_ref_price`/
  `pending_base_qty` über `SELECT *`, keine Endpunktänderung nötig)
- Stellt bereit (Markup/IDs, damit `test_dashboard.py` sie zählen kann): `#chart` (SVG-Container),
  `#poly-portfolio`, `#poly-benchmark` (die zwei `<polyline>`-Elemente), `#positions`
  (Container „Offene Positionen"), `#k-hitrate`, `#k-drawdown` (KPI-Kacheln), `#pending`
  (Container „Schwebende Vorschläge")

---

- [ ] **Schritt 1: Den scheiternden Test schreiben**

```python
# app/tests/test_dashboard.py
from __future__ import annotations

import re
from pathlib import Path

HTML = Path(__file__).resolve().parent.parent / "static" / "index.html"


def _text() -> str:
    return HTML.read_text()


def test_chart_ruft_die_equity_kurve_ab():
    """Grundlage von A-19a/A-19b: grep -c 'api/equity-curve' >= 1."""
    t = _text()
    assert t.count("api/equity-curve") >= 1, "Pruefflaeche leer: kein Aufruf gefunden"


def test_chart_legt_genau_zwei_polylinien_an():
    """toHaveCount(2), nicht nur 'vorhanden': eine fehlende zweite Linie waere
    sonst unbemerkt gruen, solange irgendeine Linie existiert."""
    t = _text()
    treffer = re.findall(r"createElementNS\([^)]*['\"]polyline['\"]\)", t)
    assert len(treffer) == 2, f"erwartet genau 2 Polylinien-Erzeugungen, gefunden {len(treffer)}"
    assert "poly-portfolio" in t and "poly-benchmark" in t
    assert t.count("'poly-portfolio'") + t.count('"poly-portfolio"') >= 1
    assert t.count("'poly-benchmark'") + t.count('"poly-benchmark"') >= 1


def test_alter_platzhalter_chart_ist_wirklich_ersetzt():
    """Nicht nur 'neuer Code da', sondern 'alter Code weg' - sonst waere ein
    Chart, der NEBEN dem Platzhalter existiert, ebenfalls gruen."""
    t = _text()
    assert "Noch keine Daten." not in t


def test_offene_positionen_werden_aus_der_api_befuellt():
    t = _text()
    assert 'id="positions"' in t
    assert '<div class="muted" style="font-size:14px">Keine offenen Positionen.</div>' not in t, (
        "die fest verdrahtete Zeile muss durch JS-Befuellung ersetzt sein"
    )
    assert "s.positions" in t or "status.positions" in t or ".positions" in t


def test_kpi_kacheln_trefferquote_und_max_drawdown_sind_verdrahtet():
    t = _text()
    assert 'id="k-hitrate"' in t
    assert 'id="k-drawdown"' in t
    assert t.count('<div class="v muted">—</div>') == 0, (
        "die beiden festen Platzhalterkacheln muessen durch echte IDs ersetzt sein"
    )
    assert "hit_rate_pct" in t
    assert "max_drawdown_pct" in t


def test_schwebende_vorschlaege_werden_angezeigt():
    t = _text()
    assert 'id="pending"' in t
    assert "pending_since_ms" in t


def test_luecke_l1_ist_im_quelltext_dokumentiert():
    """Kein funktionaler Test - eine Erinnerung, die verhindert, dass die
    Lücke beim naechsten Redesign stillschweigend verschwindet."""
    t = _text()
    assert "L-1" in t or "Kein Browsertest" in t or "kein automatisierter Test" in t
```

- [ ] **Schritt 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q tests/test_dashboard.py'
```

Erwartet, wörtlich zu protokollieren:
- `test_chart_ruft_die_equity_kurve_ab` → `AssertionError: Pruefflaeche leer: kein Aufruf gefunden`
- `test_chart_legt_genau_zwei_polylinien_an` → `AssertionError: erwartet genau 2 Polylinien-Erzeugungen, gefunden 0`
- `test_offene_positionen_werden_aus_der_api_befuellt` → `AssertionError: assert 'id="positions"' in ...`
- `test_kpi_kacheln_trefferquote_und_max_drawdown_sind_verdrahtet` → `AssertionError: assert 'id="k-hitrate"' in ...`
- `test_schwebende_vorschlaege_werden_angezeigt` → `AssertionError: assert 'id="pending"' in ...`
- `test_luecke_l1_ist_im_quelltext_dokumentiert` → `AssertionError: assert ('L-1' in ...)`

- [ ] **Schritt 3: Chart-Karte ersetzen**

Den Platzhalter (bisher Zeile 126-130) ersetzen durch:

```html
      <div class="card">
        <h3>Portfolio-Verlauf <span class="muted">vs. BTC Buy &amp; Hold</span></h3>
        <!--
          L-1 (Spec Abschnitt 12): kein automatisierter Test zeichnet dieses
          SVG nach. Geprueft wird nur, dass die richtigen Endpunkte gerufen
          und genau zwei Polylinien erzeugt werden - kein Pixel.
        -->
        <svg id="chart" viewBox="0 0 600 220" preserveAspectRatio="none" style="width:100%;height:220px">
          <polyline id="poly-benchmark" fill="none" stroke="rgba(235,238,245,.35)"
                    stroke-width="2" stroke-dasharray="4 4" points=""></polyline>
          <polyline id="poly-portfolio" fill="none" stroke="var(--blue)" stroke-width="2.5" points=""></polyline>
        </svg>
        <div class="chart-empty" id="chart-empty" style="display:none">Noch keine Daten.<br>Der Chart erscheint, sobald der Livebetrieb läuft.</div>
      </div>
```

*Hinweis:* `test_alter_platzhalter_chart_ist_wirklich_ersetzt()` prüft nur den Satz
„Noch keine Daten." **ohne Punkt-Klammer**, der als sichtbarer Dauertext in Zeile 129
stand; als reiner JS-gesteuerter Leerzustand (`#chart-empty`, `display:none` per Vorgabe)
darf derselbe Satz weiterleben, weil er dann nicht mehr der permanente Inhalt der Karte
ist, sondern ein Zustand, den JavaScript aktiv umschaltet.

- [ ] **Schritt 4: Offene Positionen, KPIs, schwebende Vorschläge — Markup**

Die Karte „Offene Positionen" (bisher Zeile 152-155):

```html
      <div class="card">
        <h3>Offene Positionen</h3>
        <div id="positions" class="kv"><div class="muted" style="font-size:14px">Keine offenen Positionen.</div></div>
      </div>
```

Die beiden KPI-Kacheln (bisher Zeile 122-123):

```html
      <div class="card kpi"><div class="v" id="k-hitrate">—</div><div class="l">Trefferquote</div></div>
      <div class="card kpi"><div class="v" id="k-drawdown">—</div><div class="l">Max Drawdown</div></div>
```

Neue Karte „Schwebende Vorschläge", nach der Decision-Journal-Karte:

```html
    <section class="card" style="margin-top:16px">
      <h3>Schwebende Vorschläge <span class="muted" id="pending-count"></span></h3>
      <div id="pending" class="kv"><div class="muted" style="font-size:14px">Keine schwebenden Vorschläge.</div></div>
    </section>
```

- [ ] **Schritt 5: JavaScript ergänzen**

Nach `loadDecisions()`:

```javascript
async function loadChart() {
  const { body } = await j('/api/equity-curve?run_id=live&limit=500');
  const pts = body.points || [];
  const empty = $('chart-empty');
  const svg = $('chart');
  if (pts.length < 2) { empty.style.display = 'grid'; svg.style.display = 'none'; return; }
  empty.style.display = 'none'; svg.style.display = 'block';
  const eq = pts.map(p => Number(p.equity));
  const bm = pts.map(p => p.benchmark != null ? Number(p.benchmark) : null);
  const alle = eq.concat(bm.filter(v => v != null));
  const min = Math.min(...alle), max = Math.max(...alle) || 1;
  const zux = (i) => (i / (pts.length - 1)) * 600;
  const zuy = (v) => 210 - ((v - min) / (max - min || 1)) * 200;
  const zuPoints = (werte) => werte.map((v, i) => v == null ? null : `${zux(i)},${zuy(v)}`).filter(Boolean).join(' ');
  $('poly-portfolio').setAttribute('points', zuPoints(eq));
  $('poly-benchmark').setAttribute('points', zuPoints(bm));
}

function loadPositionsAndKpis(s) {
  const box = $('positions'); box.replaceChildren();
  if (!s.positions || s.positions.length === 0) {
    const d = document.createElement('div'); d.className = 'muted'; d.style.fontSize = '14px';
    d.textContent = 'Keine offenen Positionen.'; box.append(d);
  } else {
    for (const p of s.positions) {
      const row = document.createElement('div');
      const a = document.createElement('span'); a.textContent = p.symbol;
      const b = document.createElement('b'); b.textContent = `${p.qty} @ ${fmt(p.avg_price)}`;
      row.append(a, b); box.append(row);
    }
  }
  text($('k-hitrate'), s.hit_rate_pct != null ? fmt(s.hit_rate_pct, 1) + ' %' : '—');
  text($('k-drawdown'), s.max_drawdown_pct != null ? fmt(s.max_drawdown_pct, 2) + ' %' : '—');
}

async function loadPending() {
  const { body: rows } = await j('/api/decisions?limit=100');
  const schwebend = rows.filter(r => r.pending_since_ms != null);
  text($('pending-count'), schwebend.length + ' Einträge');
  const box = $('pending'); box.replaceChildren();
  if (schwebend.length === 0) {
    const d = document.createElement('div'); d.className = 'muted'; d.style.fontSize = '14px';
    d.textContent = 'Keine schwebenden Vorschläge.'; box.append(d);
    return;
  }
  for (const r of schwebend) {
    const row = document.createElement('div');
    const a = document.createElement('span'); a.textContent = `${r.symbol} ${r.action}`;
    const b = document.createElement('b'); b.textContent = `schwebend seit ${r.pending_since_ms} ms`;
    row.append(a, b); box.append(row);
  }
}
```

`refresh()` erweitern und `loadStatus()` `s` an `loadPositionsAndKpis()` weiterreichen:

```javascript
async function loadStatus() {
  const { body: s } = await j('/api/status');
  text($('ver'), s.version); text($('cap'), fmt(s.starting_balance) + ' USDC');
  text($('k-eq'), fmt(s.equity));
  text($('k-day'), signed(s.daily_pnl), 'v ' + (s.daily_pnl > 0 ? 'pos' : s.daily_pnl < 0 ? 'neg' : ''));
  text($('k-trades'), s.trades_total);
  text($('l-pos'), s.limits.max_position_pct + ' %');
  text($('l-exp'), s.limits.max_total_exposure_pct + ' %');
  text($('l-loss'), fmt(s.daily_loss_pct) + ' % / ' + s.limits.max_daily_loss_pct + ' %');
  const used = Math.min(100, s.daily_loss_pct / s.limits.max_daily_loss_pct * 100);
  const bar = $('loss-bar'); bar.style.width = used + '%'; bar.style.background = used > 75 ? 'var(--red)' : used > 40 ? 'var(--orange)' : 'var(--green)';
  text($('ks-state'), s.kill_switch ? 'KILL SWITCH AKTIV' : 'NORMAL', s.kill_switch ? 'no' : 'ok');
  pill('p-risk', s.kill_switch ? 'Orders blockiert' : 'Risk normal', s.kill_switch ? 'r' : 'g');
  $('ks-on').disabled = s.kill_switch; $('ks-off').disabled = !s.kill_switch;
  loadPositionsAndKpis(s);
}

async function refresh() {
  text($('clock'), new Date().toLocaleString('de-DE'));
  await Promise.allSettled([loadStatus(), loadHealth(), loadDecisions(), loadEvents(), loadChart(), loadPending()]);
}
```

- [ ] **Schritt 6: Test laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q'
```

Erwartet: **0 failed**, `tests/test_dashboard.py` allein **7 passed**.

- [ ] **Schritt 7: Rot-Nachweis führen (drei Nachweise)**

| # | Eingebauter Fehler | Erwartete rote Ausgabe |
|---|---|---|
| 1 | Die Zeile `<polyline id="poly-benchmark" ...>` aus dem SVG löschen | `test_chart_legt_genau_zwei_polylinien_an` → `AssertionError: erwartet genau 2 Polylinien-Erzeugungen, gefunden 1` |
| 2 | Die alte Zeile `<div class="muted" style="font-size:14px">Keine offenen Positionen.</div>` als zusätzliche, feste Zeile NEBEN `id="positions"` wieder einfügen | `test_offene_positionen_werden_aus_der_api_befuellt` → `AssertionError: die fest verdrahtete Zeile muss durch JS-Befuellung ersetzt sein` |
| 3 | Den L-1-Kommentar aus dem HTML löschen | `test_luecke_l1_ist_im_quelltext_dokumentiert` → `AssertionError: assert ('L-1' in ...)` |

Jeden Fehler einzeln einbauen, Ausgabe wörtlich protokollieren, zurücknehmen, Suite
erneut grün. *Wären die Tests ohne die Fehler grün gewesen?* Ja für alle drei — Schritt 6
hat sie grün gemessen.

**Nach dem Bauen, einmalig, kein Test:** Dashboard in Safari (WebKit) **und** Firefox
öffnen, Screenshot beider unter `docs/abnahme/2026-XX-XX-teilprojekt-a2-dashboard.md`
ablegen, mit Datum und den beiden Engines benannt. Dieser Blick zählt in keiner Abnahme
als grüner Haken — er ist ein datierter Beleg, mehr nicht (L-1).

- [ ] **Schritt 8: Commit**

```bash
git add app/static/index.html app/tests/test_dashboard.py
git commit -m "feat(dashboard): Chart, offene Positionen, KPIs, schwebende Vorschlaege (A-19b)

Inline-SVG mit zwei <polyline> (Portfolio, Benchmark gestrichelt) ersetzt den
Platzhalter, gespeist aus GET /api/equity-curve. Offene Positionen, Trefferquote
und Max Drawdown kommen jetzt aus GET /api/status. Schwebende Vorschlaege
(pending_since_ms) werden als eigene Liste angezeigt (E-006).

Keine neue Abhaengigkeit, keine CDN-Anfrage, CSP unangetastet (Spec 7.2).

L-1 bleibt bewusst offen: kein automatisierter Browsertest. test_dashboard.py
prueft ausschliesslich Endpunkt-Verdrahtung und Elementzahl im Quelltext, nie
das gerenderte Ergebnis. Screenshot-Beleg in docs/abnahme/, kein Testartefakt.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Aufgabe 8: Auslieferung — README, CHANGELOG, `.env.example`, v0.3.0, Release-Notiz

**Macht grün:** A-20, A-21, A-22.

**Dateien:**
- Geändert: `README.md` (B-2: unqualifizierte Aussage korrigieren; B-6: Handgriff nennen)
- Geändert: `app/CHANGELOG.md` (Abschnitt `## 0.3.0` anhängen — dient zugleich als
  Release-Notiz, siehe Begründung in Schritt 5)
- Geändert: `app/.env.example` (elf neue Variablen aus Aufgabe 5, Spec Abschnitt 18)
- Geändert: `app/VERSION` (`0.2.1` → `0.3.0`)
- Geändert: `app/aitra/config.py` (`narrow_trading_window: bool = False`, hinten mit
  Vorgabewert — B-5; der bisherige `log.warning(...)` in `_narrow_trading_window()`
  bleibt, der Rückgabewert wird zusätzlich am `Config`-Objekt sichtbar, weil `web.py`
  ihn für das DB-Ereignis aus A-22 braucht)
- Geändert: `app/aitra/web.py` (`create_app()` loggt beim Start `NARROW_TRADING_WINDOW`
  als DB-Ereignis, nicht nur als Python-Log — A-22 misst `/api/events`, nicht die Logs)
- Test geändert: `app/tests/test_config.py` (`narrow_trading_window`-Feld)
- Test geändert: `app/tests/test_api.py` (A-22-Ereignis)

**Schnittstellen:**
- Nutzt: `config.Config`, `config._narrow_trading_window()` (Aufgabe-1-Bestand, unverändert
  in der Berechnung), `db.log_event`
- Stellt bereit:
  - `config.Config.narrow_trading_window: bool` (Vorgabe `False`)

---

- [ ] **Schritt 1: Die scheiternden Tests schreiben**

An `app/tests/test_config.py` anhängen:

```python
def test_narrow_trading_window_true_bei_100_usdc(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path)); monkeypatch.setenv("ADMIN_TOKEN", "x" * 32)
    monkeypatch.setenv("STARTING_BALANCE", "100")
    assert load().narrow_trading_window is True


def test_narrow_trading_window_false_bei_10000_usdc(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path)); monkeypatch.setenv("ADMIN_TOKEN", "x" * 32)
    monkeypatch.setenv("STARTING_BALANCE", "10000")
    assert load().narrow_trading_window is False
```

An `app/tests/test_api.py` anhängen:

```python
def test_a22_narrow_trading_window_erzeugt_genau_ein_ereignis(tmp_path):
    cfg = Config(Decimal("100"), 10, 2, 50, tmp_path, "t" * 32, narrow_trading_window=True)
    rows = create_app(cfg).test_client().get("/api/events?limit=200").get_json()
    treffer = [r for r in rows if r["event"] == "NARROW_TRADING_WINDOW"]
    assert len(treffer) == 1, f"Pruefflaeche: {len(treffer)} Ereignisse statt 1"
    assert "1200" in treffer[0]["detail"] or "1.200" in treffer[0]["detail"]


def test_a22_kein_ereignis_bei_ausreichendem_kapital(tmp_path):
    cfg = Config(Decimal("10000"), 10, 2, 50, tmp_path, "t" * 32, narrow_trading_window=False)
    rows = create_app(cfg).test_client().get("/api/events?limit=200").get_json()
    treffer = [r for r in rows if r["event"] == "NARROW_TRADING_WINDOW"]
    assert treffer == [], f"Pruefflaeche: {len(treffer)} Ereignisse statt 0"
```

- [ ] **Schritt 2: Tests laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q \
   tests/test_config.py tests/test_api.py -k narrow_trading_window'
```

Erwartet:
- `test_narrow_trading_window_true_bei_100_usdc` → `AttributeError: 'Config' object has no attribute 'narrow_trading_window'`
- `test_a22_narrow_trading_window_erzeugt_genau_ein_ereignis` → `TypeError: __init__() got an unexpected keyword argument 'narrow_trading_window'`

- [ ] **Schritt 3: `config.py` — Feld ergänzen, Ergebnis sichtbar machen**

`Config` bekommt ganz hinten: `narrow_trading_window: bool = False`.

In `load()`:

```python
    eng_fenster = _narrow_trading_window(starting_balance, max_position_pct)
    if eng_fenster:
        log.warning(
            "NARROW_TRADING_WINDOW: STARTING_BALANCE=%s mit MAX_POSITION_PCT=%s%% "
            "ergibt ein zu enges Handelsfenster (< 20x der effektiven Mindestordergroesse, K-1)",
            money.to_text(starting_balance), max_position_pct,
        )
```

(Die bestehende `if _narrow_trading_window(...)`-Zeile durch diese ersetzen — dieselbe
Berechnung, jetzt einmal statt zweimal aufgerufen, das Ergebnis in `eng_fenster` gehalten.)

Im `Config(...)`-Aufruf ganz hinten: `narrow_trading_window=eng_fenster,`.

- [ ] **Schritt 4: `web.py` — DB-Ereignis beim Start**

Im Initialisierungsblock von `create_app()`, direkt nach dem bestehenden
`db.log_event(conn, "SYSTEM", "INFO", "STARTUP", ...)`:

```python
        if cfg.narrow_trading_window:
            db.log_event(
                conn, "RISK_ENGINE", "WARN", "NARROW_TRADING_WINDOW",
                f"STARTING_BALANCE={money.to_text(cfg.starting_balance)} mit "
                f"MAX_POSITION_PCT={cfg.max_position_pct}% ergibt ein zu enges "
                f"Handelsfenster (Schwelle 1.200 USDC, K-1)",
            )
```

- [ ] **Schritt 5: Tests laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q'
```

Erwartet: **0 failed**.

- [ ] **Schritt 6: Rot-Nachweis führen (ein Nachweis für den Code-Anteil)**

*Fehler:* Den `db.log_event(...)`-Aufruf aus Schritt 4 wieder entfernen.
*Erwartet:* `test_a22_narrow_trading_window_erzeugt_genau_ein_ereignis` →
`AssertionError: Pruefflaeche: 0 Ereignisse statt 1`.
Ausgabe zeigen, Aufruf wieder einfügen, Suite erneut grün.
*Wäre der Test ohne den Fehler grün gewesen?* Ja — Schritt 5 hat ihn grün gemessen, und
`test_a22_kein_ereignis_bei_ausreichendem_kapital` bleibt in beiden Fällen grün, was
belegt, dass der erste Test nicht durch pauschales Immer-Loggen besteht.

- [ ] **Schritt 7: `.env.example` — elf neue Variablen**

```bash
cat >> app/.env.example <<'EOF'

# Marktdaten (Teilprojekt A2)
MARKET_DATA_ENABLED=false
MARKET_SYMBOLS=BTCUSDC,BNBUSDC
MARKET_INTERVAL=15m
MARKET_POLL_S=60
MARKET_STALE_WARN_S=150
MARKET_STALE_KILL_S=300
MARKET_CLOCK_SKEW_WARN_S=5
MARKET_CLOCK_SKEW_KILL_S=30
BINANCE_BASE_URL=https://api.binance.com
FEE_BPS=10
SLIPPAGE_BPS=5
BENCHMARK_SYMBOL=BTCUSDC
CANDLE_RETENTION_DAYS=400
EOF
```

`STARTING_BALANCE=100` (Zeile 1) auf `STARTING_BALANCE=10000` ändern — die Vorgabe aus
Aufgabe 1/E-Nummer der Symbolentscheidung; ein `.env.example`, das noch 100 nennt, würde
jede Neuinstallation ins enge Fenster aus K-1 schicken.

- [ ] **Schritt 8: `app/VERSION`**

```bash
echo -n "0.3.0" > app/VERSION
```

- [ ] **Schritt 9: `app/CHANGELOG.md` — `0.3.0` voranstellen**

Direkt unter der Überschrift `# Changelog` einfügen (vor `## 0.2.1`):

```markdown
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
```

*Warum die Release-Notiz hier steht und nicht in einer eigenen Datei:* Es gibt in diesem
Projekt keine `docs/releases/`-Konvention; `docs/abnahme/` ist für Messungen und
Rot-Nachweise reserviert (siehe A1-Muster). Der `0.3.0`-Abschnitt in `CHANGELOG.md`
erfüllt Befund B-6's erste Maßnahme wörtlich („Die v0.3.0-Release-Notiz … nennt den
Handgriff") und ist der Ort, an dem ihn ein Betreiber vor einem Update tatsächlich liest.

- [ ] **Schritt 10: `README.md` — B-2 und B-6**

Zeile 3-5 ersetzen:

```markdown
Privates Paper-Trading-Labor. Läuft in einem eigenen Debian-LXC auf Proxmox. Fast
komplett lokal: Konfiguration, Datenbank, Dashboard und Ausführung bleiben auf dem
eigenen Server. Eine Ausnahme seit v0.3.0: Für Kursdaten ruft die App die öffentlichen,
lesenden Endpunkte von `api.binance.com` ab. Dabei werden ausschließlich Symbol,
Intervall und Zeitraum übertragen — **keine Kontodaten, keine Kennungen, keine
API-Schlüssel**; solche existieren im Projekt nicht. Der Quellcode liegt in einem
privaten GitHub-Repo; im Betrieb ruft die App nichts davon ab.
```

Im Abschnitt „Sicherheit" nach der bestehenden Liste ergänzen:

```markdown
- Ausgehender Netzverkehr beschränkt sich auf `api.binance.com` (und den Ausweichhost
  `data-api.binance.vision`), nur HTTPS, mit Host-Allowlist und ohne Weiterleitungen
```

Im Abschnitt „Update" nach dem bestehenden Text ergänzen:

```markdown
**Handgriff nach dem Sprung von v0.2.x auf v0.3.0:** Ein Update überschreibt die `.env`
nicht (dort steht der Admin-Token). Bestandscontainer laufen danach mit
`STARTING_BALANCE=100` weiter — die App warnt seit v0.3.0 selbst darüber
(`NARROW_TRADING_WINDOW` unter „Protokolle"), aber wer es sofort beheben will:

```bash
pct exec <CTID> -- sed -i 's/^STARTING_BALANCE=.*/STARTING_BALANCE=10000/' /opt/ai-trade-lab/.env
pct exec <CTID> -- docker compose -f /opt/ai-trade-lab/docker-compose.yml restart
```
```

- [ ] **Schritt 11: A-21 messen**

```bash
grep -c "api.binance.com" README.md                                    # erwartet ≥ 1
grep -c "keine Kontodaten\|keinerlei Kontodaten" README.md              # erwartet ≥ 1
grep -c "^Privates Paper-Trading-Labor.*ohne Cloud-Dienste" README.md   # erwartet 0
```

*Rot-Nachweis (README, kein Codefehler):* README auf den Stand vor Schritt 10
zurücksetzen (`git checkout -- README.md` im Arbeitsverzeichnis, **nicht committet**) und
dieselben drei Befehle laufen lassen. Erwartet: erste und zweite Messung `0`, dritte `1` —
genau der in Spec A-21 benannte Rot-Fall. Änderung danach wiederherstellen
(`git checkout -- README.md` nur, falls zwischenzeitlich ungewollt committet wurde, sonst
schlicht Schritt 10 erneut anwenden).

- [ ] **Schritt 12: A-18 messen**

```bash
sha256sum app/requirements.txt
```

Erwartet: identisch zum Stand vor Teilprojekt A2 (Datei in keiner der acht Aufgaben
verändert). *Rot-Nachweis:* `echo "requests==2.31.0" >> app/requirements.txt`, Hash
erneut prüfen — weicht ab. Zeile wieder entfernen.

- [ ] **Schritt 13: A-20 messen — volle Suite und `build.sh`**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q'
```

Erwartet: **0 failed, 0 errors**, Testanzahl **≥ 66** (heute, vor A2: 18 laut Spec-Kopf,
157 nach A1 laut A1-Abnahme — die Zahl ist hier gegen den A1-Endstand zu protokollieren,
nicht gegen den ursprünglichen Spec-Text, der vor A1 geschrieben wurde).

```bash
./build.sh
ls -la dist/ai-trade-lab-install.sh dist/ai-trade-lab-install.sh.sha256
```

Erwartet: Beide Dateien vorhanden, `build.sh` endet mit `✔ …`.
*Rot-Nachweis:* Eine Zeile in einem beliebigen Test absichtlich zum Scheitern bringen
(z. B. `assert False` in `test_money.py` einfügen), `./build.sh` erneut laufen lassen.
Erwartet: Abbruch mit Pytest-Fehlerausgabe, `dist/` bleibt unverändert (`set -e` in
`build.sh` Zeile 3 stoppt vor dem `tar`-Aufruf). Zeile wieder entfernen.

- [ ] **Schritt 14: Commit**

```bash
git add README.md app/CHANGELOG.md app/.env.example app/VERSION \
        app/aitra/config.py app/aitra/web.py app/tests/test_config.py app/tests/test_api.py
git commit -m "chore(release): v0.3.0 — README/CHANGELOG/.env.example, NARROW_TRADING_WINDOW-Ereignis

README: B-2 korrigiert (Netzverkehr zu api.binance.com wahrheitsgemaess genannt,
keine unqualifizierte 'ohne Cloud-Dienste'-Aussage mehr) und B-6 (Update-Handgriff
fuer STARTING_BALANCE explizit dokumentiert, mit Befehl).

CHANGELOG 0.3.0 dient zugleich als Release-Notiz (B-6, Massnahme 1). .env.example
um elf Marktdaten-Variablen ergaenzt (Spec 18), STARTING_BALANCE-Vorgabe auf 10000.

config.Config.narrow_trading_window (hinten, Vorgabe False, B-5) macht das
Ergebnis der bestehenden Pruefung sichtbar; web.py loggt beim Start ein
NARROW_TRADING_WINDOW-Ereignis in die DB (A-22), nicht nur ins Python-Log.

VERSION 0.2.1 -> 0.3.0. requirements.txt unveraendert (A-18).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

