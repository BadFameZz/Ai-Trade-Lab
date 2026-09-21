# Teilprojekt A1 — Offline-Engine: Geld, Ledger, Replay

> **Für agentische Bearbeiter:** ERFORDERLICHE SUB-SKILL: `superpowers:subagent-driven-development`
> (empfohlen) oder `superpowers:executing-plans`. Die Schritte nutzen Checkbox-Syntax (`- [ ]`).

**Ziel:** Eine vollständige, netzfreie Paper-Trading-Engine, die aus Kerzen Fills erzeugt,
ein Portfolio führt und im Zeitraffer gegen BTC Buy & Hold gemessen werden kann.

**Architektur:** Das Ledger ist rein — kein Netz, keine DB, keine Uhr, kein Zufall. Es bekommt
Kerzen und erzeugt Fills. Ob die Kerzen aus einer Liste, aus SQLite oder später vom Netz
kommen, weiß es nicht. Jeder Zeitstempel ist ein Parameter. Genau das macht live, Backtest und
Training zu demselben Code (E-001).

**Tech-Stack:** Python 3.12, Standardbibliothek. `decimal` fürs Geld, `sqlite3` für Persistenz.
**Keine neue Abhängigkeit** — das ist Abnahmekriterium A-18.

**Spec:** `docs/specs/2026-09-20-teilprojekt-a-marktdaten-ledger-benchmark.md`
**Entscheidungen:** `docs/entscheidungen/E-001` … `E-008`

**Nicht in diesem Plan:** `binance.py`, `poller.py`, `web.py`-Endpunkte, `static/index.html`,
`backfill.py`, README/CHANGELOG. Das ist Plan A2 und hängt an den Bauschritten 7–10 der Spec.

---

## Globale Randbedingungen

Diese gelten für **jede** Aufgabe, ohne dass sie dort wiederholt werden:

- **Keine neue Laufzeitabhängigkeit.** `app/requirements.txt` bleibt bei `flask==3.1.3` und
  `gunicorn==26.2.0`. `pytest` bleibt reine Entwicklungsabhängigkeit. (A-18)
- **Python 3.12** — `app/Dockerfile:1` ist `python:3.12-slim`. Keine 3.13-Syntax.
- **Bestandsstil:** `from __future__ import annotations` als erste Zeile, frozen dataclasses,
  deutschsprachige Docstrings, keine Klasse ohne Grund.
- **Kein Modul über 200 Zeilen.**
- **Geld ist niemals `float`.** Kein `float()` auf einem Geldwert, keine Geld-Literale ohne
  `Decimal`, keine Geldrechnung außerhalb des Kontexts aus `money.py`. Prozentsätze und
  Basispunkte dürfen `float` bleiben — sie sind keine Beträge.
- **`money.py`, `ledger.py`, `sizing.py`, `benchmark.py` rufen niemals** `time.time()`,
  `datetime.now()`, `datetime.utcnow()` oder `random.*` auf. Zeitstempel sind Parameter. (A-8b)
- **Bestehende Tests müssen grün bleiben.** `app/tests/test_risk.py` und `test_api.py`
  konstruieren `Config` und `PortfolioState` positional — neue Felder nur **hinten** und nur
  **mit Vorgabewert** (Befund B-5).
- **Tests laufen im Container:**
  `docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c 'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q'`
  Auf dem Mac wird nicht nativ gebaut.
- **Rot-Nachweis ist Pflicht.** Jeder Test wird einmal absichtlich gebrochen, die rote Ausgabe
  gezeigt, der Bruch zurückgenommen. Ein Test ohne gezeigte rote Ausgabe zählt nicht.

---

## Dateiübersicht

| Datei | Neu/Geändert | Verantwortung |
|---|---|---|
| `app/aitra/money.py` | neu | Decimal-Kontext, `SymbolSpec`, Quantisierung, Textform |
| `app/aitra/marketdata.py` | neu | `Candle`, `Clock`, `ListSource`, `SqliteSource` |
| `app/aitra/ledger.py` | neu | `Order`/`Fill`/`Rejection`/`Valuation`, `apply()`, `mark()` |
| `app/aitra/sizing.py` | neu | `size_order()` — Prozent → quantisierte Menge |
| `app/aitra/execute.py` | neu | `execute_proposal()` — Risk → Sizing → Ledger → Journal |
| `app/aitra/benchmark.py` | neu | `BuyAndHold` |
| `app/aitra/replay.py` | neu | `run_replay()` + CLI |
| `app/aitra/store.py` | neu | Lesen/Schreiben der neuen Tabellen |
| `app/aitra/config.py` | geändert | `_dec()`, `starting_balance` als Decimal, Startwarnung |
| `app/aitra/risk.py` | geändert | `position_pct_by_symbol`, SELL-Korrektur (B-1) |
| `app/aitra/db.py` | geändert | Migration 2 |
| `app/tests/test_money.py` … `test_replay.py` | neu | je Modul eine Testdatei |

---

### Aufgabe 1: `money.py` — Geld, das nicht driftet

**Dateien:**
- Neu: `app/aitra/money.py`
- Neu: `app/tests/test_money.py`

**Schnittstellen:**
- Nutzt: nichts (nur stdlib)
- Stellt bereit: `CTX`, `dec(v) -> Decimal`, `step_down(v, step) -> Decimal`,
  `tick_down(v, tick) -> Decimal`, `tick_up(v, tick) -> Decimal`,
  `to_text(d, dp=8) -> str`, `from_text(s) -> Decimal`,
  `SymbolSpec(symbol, base, quote, tick_size, step_size, min_qty, min_notional, base_precision, quote_precision)`
  mit `effective_min_notional(price) -> Decimal`, sowie `BUILTIN_SPECS: dict[str, SymbolSpec]`
  mit Einträgen für `BTCUSDC` und `ETHUSDC`.

- [ ] **Schritt 1: Den scheiternden Test schreiben**

```python
# app/tests/test_money.py
from __future__ import annotations

from decimal import Decimal

import pytest

from aitra import money


def test_dec_lehnt_float_ab():
    """Geld darf nie aus einem float entstehen - der Fehler soll laut sein."""
    with pytest.raises(TypeError):
        money.dec(0.1)


def test_dec_aus_text_und_int():
    assert money.dec("0.1") == Decimal("0.1")
    assert money.dec(5) == Decimal("5")


def test_step_down_rundet_immer_ab():
    step = Decimal("0.00001")
    assert money.step_down(Decimal("0.123456789"), step) == Decimal("0.12345")
    assert money.step_down(Decimal("0.12345"), step) == Decimal("0.12345")
    assert money.step_down(Decimal("0.000009"), step) == Decimal("0")


def test_tick_down_und_tick_up():
    tick = Decimal("0.01")
    assert money.tick_down(Decimal("81287.039"), tick) == Decimal("81287.03")
    assert money.tick_up(Decimal("81287.031"), tick) == Decimal("81287.04")
    assert money.tick_up(Decimal("81287.03"), tick) == Decimal("81287.03")


def test_textform_ist_kanonisch_und_rundreisefest():
    # Feste Nachkommastellen, damit Gleichheitsvergleiche auf TEXT verlaesslich sind (E-007)
    assert money.to_text(Decimal("100")) == "100.00000000"
    assert money.to_text(Decimal("100.0")) == "100.00000000"
    assert money.to_text(Decimal("0.1")) == "0.10000000"
    assert money.from_text(money.to_text(Decimal("1234.5678"))) == Decimal("1234.5678")


def test_effektive_mindestorder_liegt_ueber_min_notional():
    """K-3: Weil immer abgerundet wird, rutscht eine auf 5,00 gezielte Order
    nach dem Runden darunter. Die garantierte Grenze ist min_notional + step*preis."""
    spec = money.BUILTIN_SPECS["BTCUSDC"]
    eff = spec.effective_min_notional(Decimal("81287.04"))
    assert eff > spec.min_notional
    assert eff == Decimal("5") + Decimal("0.00001") * Decimal("81287.04")
    assert Decimal("5.81") < eff < Decimal("5.82")


def test_builtin_specs_kennen_beide_symbole():
    for sym in ("BTCUSDC", "ETHUSDC"):
        spec = money.BUILTIN_SPECS[sym]
        assert spec.quote == "USDC"
        assert spec.min_notional == Decimal("5")
        assert spec.tick_size > 0 and spec.step_size > 0
```

- [ ] **Schritt 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q pytest && cd app && python -m pytest tests/test_money.py -q'
```

Erwartet: `ModuleNotFoundError: No module named 'aitra.money'`

- [ ] **Schritt 3: `money.py` schreiben**

```python
"""Geldarithmetik fuer Aitra - Decimal statt float, ueberall.

Gruende in docs/entscheidungen/E-002. Kurz: 0.1 + 0.2 ist in Binaerarithmetik
nicht 0.3, und ueber tausende Fills driftet der Saldo. prec=34 entspricht
IEEE decimal128 und macht jede Operation des Fuellmodells exakt (Spec 6.4).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_DOWN, ROUND_UP

CTX = Context(prec=34)
DP = 8  # kanonische Nachkommastellen fuer die Textform


def dec(value: str | int | Decimal) -> Decimal:
    """Wandelt zu Decimal. float ist verboten und wirft."""
    if isinstance(value, float):
        raise TypeError(
            "Geld nie aus float erzeugen - Zeichenkette, int oder Decimal verwenden"
        )
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _quant(value: Decimal, unit: Decimal, rounding: str) -> Decimal:
    if unit <= 0:
        raise ValueError(f"Schrittweite muss positiv sein, war {unit}")
    faktor = CTX.divide(value, unit).to_integral_value(rounding=rounding)
    return CTX.multiply(faktor, unit)


def step_down(value: Decimal, step: Decimal) -> Decimal:
    """Menge auf ein Vielfaches der Schrittweite abrunden. Immer abwaerts."""
    return _quant(value, step, ROUND_DOWN)


def tick_down(value: Decimal, tick: Decimal) -> Decimal:
    """Preis auf die Tickgroesse abrunden."""
    return _quant(value, tick, ROUND_DOWN)


def tick_up(value: Decimal, tick: Decimal) -> Decimal:
    """Preis auf die Tickgroesse aufrunden."""
    return _quant(value, tick, ROUND_UP)


def to_text(d: Decimal, dp: int = DP) -> str:
    """Kanonische Textform fuer Datenbank und JSON: feste Nachkommastellen."""
    return f"{d:.{dp}f}"


def from_text(s: str) -> Decimal:
    return Decimal(s)


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    base: str
    quote: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    base_precision: int
    quote_precision: int

    def effective_min_notional(self, price: Decimal) -> Decimal:
        """Die Ordergroesse, die nach dem Abrunden garantiert noch durchgeht (K-3).

        min_notional allein genuegt nicht: eine exakt auf min_notional gezielte
        Order faellt nach dem Abrunden auf step_size darunter und wird abgelehnt.
        Fuer BTCUSDC bei 81287,04 sind das 5,8128704 statt 5,00 USDC.
        """
        return CTX.add(self.min_notional, CTX.multiply(self.step_size, price))


# Am 2026-09-20 von api.binance.com/api/v3/exchangeInfo abgelesen.
# binance.py (Plan A2) ueberschreibt sie zur Laufzeit; hier stehen sie, damit
# A1 vollstaendig ohne Netz testbar ist.
BUILTIN_SPECS: dict[str, SymbolSpec] = {
    "BTCUSDC": SymbolSpec(
        symbol="BTCUSDC", base="BTC", quote="USDC",
        tick_size=Decimal("0.01"), step_size=Decimal("0.00001"),
        min_qty=Decimal("0.00001"), min_notional=Decimal("5"),
        base_precision=8, quote_precision=8,
    ),
    "ETHUSDC": SymbolSpec(
        symbol="ETHUSDC", base="ETH", quote="USDC",
        tick_size=Decimal("0.01"), step_size=Decimal("0.0001"),
        min_qty=Decimal("0.0001"), min_notional=Decimal("5"),
        base_precision=8, quote_precision=8,
    ),
}
```

- [ ] **Schritt 4: Test laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q pytest && cd app && python -m pytest tests/test_money.py -q'
```

Erwartet: `7 passed`

- [ ] **Schritt 5: Rot-Nachweis führen**

`step_down` auf `ROUND_UP` ändern, Test laufen lassen. Erwartet: `test_step_down_rundet_immer_ab`
schlägt fehl mit `Decimal('0.12346') != Decimal('0.12345')`. Ausgabe zeigen, Änderung zurücknehmen,
Test erneut grün.

- [ ] **Schritt 6: Commit**

```bash
git add app/aitra/money.py app/tests/test_money.py
git commit -m "feat(money): Decimal-Arithmetik mit Quantisierung und SymbolSpec"
```

---

### Aufgabe 2: `config.py` — Decimal an der Quelle, Allowlist, Startwarnung

**Macht grün:** A-3 (teilweise, über die Decimal-Herkunft von `starting_balance`), A-17
(alle fünf Messpunkte), A-22 (die ersten beiden Messungen; die dritte — `GET /api/events` —
hängt an `web.py` und ist Plan A2).

**Dateien:**
- Geändert: `app/aitra/config.py`
- Neu: `app/tests/test_config.py`

**Schnittstellen:**
- Nutzt: `money.dec(v) -> Decimal`, `money.BUILTIN_SPECS`, `money.SymbolSpec.effective_min_notional`
  (alle aus Aufgabe 1)
- Stellt bereit:
  - `_dec(name: str, default: str, lo: Decimal, hi: Decimal) -> Decimal`
  - `_binance_base_url(name: str = "BINANCE_BASE_URL", default: str = "https://api.binance.com") -> str`
  - `ALLOWED_BINANCE_HOSTS: frozenset[str]`
  - `Config` mit **einer geänderten Annotation** (`starting_balance: Decimal`, war `float`)
    und **einem neuen Feld hinten mit Vorgabewert**: `binance_base_url: str = "https://api.binance.com"`
  - `load() -> Config` unverändert in der Signatur

**Wichtig für Folgeaufgaben:** `Config` ist damit `Config(starting_balance: Decimal,
max_position_pct: float, max_daily_loss_pct: float, max_total_exposure_pct: float,
data_dir: Path, admin_token: str, trading_mode: str = "PAPER", live_locked: bool = True,
binance_base_url: str = "https://api.binance.com")`. Nur `starting_balance` wechselt den Typ
(die Annotation, nicht die Konstruktor-Reihenfolge) — bestehende positional-Aufrufe
(`Config(100, 10, 2, 50, tmp_path, TOKEN)`) bleiben gültig, weil Python Dataclass-Annotationen
zur Laufzeit nicht erzwingt.

- [ ] **Schritt 1: Den scheiternden Test schreiben**

```python
# app/tests/test_config.py
from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import pytest

from aitra import config
from aitra.config import Config, ConfigError, load


def _base_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_TOKEN", "x" * 32)


def test_dec_liefert_decimal_und_keinen_float(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    val = config._dec("STARTING_BALANCE", "10000", Decimal(0), Decimal(1_000_000))
    assert val == Decimal("10000")
    assert isinstance(val, Decimal)


def test_dec_lehnt_nicht_numerischen_text_ab():
    with pytest.raises(ConfigError):
        config._dec("STARTING_BALANCE", "zehntausend", Decimal(0), Decimal(1_000_000))


def test_dec_ist_praezise_ohne_float_umweg(monkeypatch):
    # B-4: 0.1 darf niemals ueber float((str)) laufen und driften
    monkeypatch.setenv("X_TEST", "0.1")
    val = config._dec("X_TEST", "1", Decimal(0), Decimal(10))
    assert val == Decimal("0.1")


def test_dec_prueft_grenzen():
    with pytest.raises(ConfigError):
        config._dec("X", "-1", Decimal(0), Decimal(10))


def test_starting_balance_vorgabe_ist_10000(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.delenv("STARTING_BALANCE", raising=False)
    cfg = load()
    assert cfg.starting_balance == Decimal("10000")
    assert isinstance(cfg.starting_balance, Decimal)


def test_binance_base_url_akzeptiert_den_offiziellen_host(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("BINANCE_BASE_URL", "https://api.binance.com")
    cfg = load()
    assert cfg.binance_base_url == "https://api.binance.com"


@pytest.mark.parametrize("bad_url", [
    "http://169.254.169.254",              # Metadaten-Adresse, zudem kein TLS
    "https://evil.example.com",            # falscher Host
    "http://api.binance.com",              # richtiger Host, aber kein TLS
    "https://api.binance.com.evil.example",  # Praefix-Falle (A-17)
])
def test_binance_base_url_lehnt_alles_ausserhalb_der_allowlist_ab(monkeypatch, tmp_path, bad_url):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("BINANCE_BASE_URL", bad_url)
    with pytest.raises(ConfigError):
        load()


def test_narrow_trading_window_warnt_unter_der_schwelle(monkeypatch, tmp_path, caplog):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("STARTING_BALANCE", "100")
    with caplog.at_level(logging.WARNING, logger="aitra.config"):
        load()
    assert "NARROW_TRADING_WINDOW" in caplog.text


def test_narrow_trading_window_schweigt_ueber_der_schwelle(monkeypatch, tmp_path, caplog):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("STARTING_BALANCE", "10000")
    with caplog.at_level(logging.WARNING, logger="aitra.config"):
        load()
    assert "NARROW_TRADING_WINDOW" not in caplog.text


def test_bestandskonstruktion_bleibt_positional_gueltig(tmp_path):
    # Befund B-5: test_risk.py und test_api.py duerfen durch Aufgabe 2 nicht brechen
    cfg = Config(100, 10, 2, 50, tmp_path, "x" * 32)
    assert cfg.starting_balance == 100
    assert cfg.binance_base_url == "https://api.binance.com"
```

- [ ] **Schritt 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_config.py -q'
```

Erwartet: `AttributeError: module 'aitra.config' has no attribute '_dec'` (erster Test),
danach in Kaskade weitere Fehlschläge (`starting_balance == 100` statt `10000`,
`AttributeError: ... 'binance_base_url'`).

- [ ] **Schritt 3: `config.py` ändern**

```python
"""Konfiguration aus Umgebungsvariablen – mit harten Grenzen.

Live-Trading ist in dieser Version nicht implementiert und bleibt gesperrt,
egal was in der .env steht.
"""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from . import money

log = logging.getLogger("aitra.config")

ALLOWED_BINANCE_HOSTS = frozenset({"api.binance.com", "data-api.binance.vision"})

# Referenzpreis fuer die Schwelle in _narrow_trading_window: BTCUSDC am 2026-09-20
# (Spec Abschnitt 2.2 / K-3). Ein Marktpreis wuerde die Schwelle staendig verschieben;
# die Spec verwendet bewusst denselben festen Wert wie K-3.
_NARROW_WINDOW_REF_PRICE = Decimal("81287.04")
_NARROW_WINDOW_FACTOR = Decimal(20)


class ConfigError(ValueError):
    pass


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


def _num(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        val = float(raw)
    except ValueError as e:
        raise ConfigError(f"{name}={raw!r} ist keine Zahl") from e
    if not (lo < val <= hi):
        raise ConfigError(f"{name}={val} liegt außerhalb der erlaubten Grenzen ({lo} < x ≤ {hi})")
    return val


def _dec(name: str, default: str, lo: Decimal, hi: Decimal) -> Decimal:
    """Wie _num, aber ohne den Umweg über float (Befund B-4).

    STARTING_BALANCE=0.1 wird heute (_num) zu 0.1000000000000000055511151231257827,
    weil float(raw) zuerst bindaer rundet. _dec liest denselben String direkt in
    Decimal ein und bleibt exakt.
    """
    raw = os.getenv(name, default).strip()
    try:
        val = money.dec(raw)
    except (InvalidOperation, ValueError) as e:
        raise ConfigError(f"{name}={raw!r} ist keine Zahl") from e
    if not (lo < val <= hi):
        raise ConfigError(f"{name}={val} liegt außerhalb der erlaubten Grenzen ({lo} < x ≤ {hi})")
    return val


def _binance_base_url(name: str = "BINANCE_BASE_URL", default: str = "https://api.binance.com") -> str:
    """Nur HTTPS und nur die beiden bekannten Binance-Hosts, exakt verglichen (A-17).

    Ein Praefixvergleich (startswith) wuerde https://api.binance.com.evil.example
    durchlassen — genau das ist der Rot-Nachweis unten.
    """
    raw = os.getenv(name, default).strip()
    parts = urlsplit(raw)
    if parts.scheme != "https":
        raise ConfigError(f"{name}={raw!r} muss https verwenden")
    if parts.hostname not in ALLOWED_BINANCE_HOSTS:
        raise ConfigError(f"{name}={raw!r} ist kein erlaubter Binance-Host")
    return raw


def _narrow_trading_window(balance: Decimal, max_position_pct: float) -> bool:
    """K-1/A-22: Das Fenster ist zu eng, wenn die groesste erlaubte Order nicht
    mindestens das Zwanzigfache der effektiven Mindestordergroesse (K-3) erreicht.

    balance * max_position_pct/100 >= 20 * effective_min_notional(ref_price)
    Bei 5,82 USDC (BTCUSDC, 81.287) und 10 % Positionsgroesse liegt die Grenze bei
    rund 1.164 USDC; in der Doku wird grosszuegig auf 1.200 USDC aufgerundet.
    """
    spec = money.BUILTIN_SPECS["BTCUSDC"]
    eff_min = spec.effective_min_notional(_NARROW_WINDOW_REF_PRICE)
    largest_order = balance * Decimal(str(max_position_pct)) / Decimal(100)
    return largest_order < _NARROW_WINDOW_FACTOR * eff_min


def _admin_token(data_dir: Path) -> str:
    env = os.getenv("ADMIN_TOKEN", "").strip()
    if env:
        if len(env) < 24:
            raise ConfigError("ADMIN_TOKEN muss mindestens 24 Zeichen lang sein")
        return env
    # Kein Token gesetzt → einmalig erzeugen und nur lokal ablegen (nie loggen)
    path = data_dir / "admin_token"
    if path.exists():
        return path.read_text().strip()
    token = secrets.token_hex(24)
    path.write_text(token)
    path.chmod(0o600)
    log.warning("ADMIN_TOKEN nicht gesetzt – neuer Token in %s abgelegt", path)
    return token


def load() -> Config:
    data_dir = Path(os.getenv("DATA_DIR", "/app/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    mode = os.getenv("TRADING_MODE", "paper").strip().lower()
    if mode != "paper":
        log.warning("TRADING_MODE=%r ignoriert – Live-Trading ist gesperrt, erzwinge PAPER", mode)

    starting_balance = _dec("STARTING_BALANCE", "10000", Decimal(0), Decimal(1_000_000))
    max_position_pct = _num("MAX_POSITION_PCT", 10, 0, 25)
    binance_base_url = _binance_base_url()

    if _narrow_trading_window(starting_balance, max_position_pct):
        log.warning(
            "NARROW_TRADING_WINDOW: STARTING_BALANCE=%s mit MAX_POSITION_PCT=%s%% "
            "ergibt ein zu enges Handelsfenster (< 20x der effektiven Mindestordergroesse, K-1)",
            money.to_text(starting_balance), max_position_pct,
        )

    return Config(
        starting_balance=starting_balance,
        max_position_pct=max_position_pct,
        max_daily_loss_pct=_num("MAX_DAILY_LOSS_PCT", 2, 0, 10),
        max_total_exposure_pct=_num("MAX_TOTAL_EXPOSURE_PCT", 50, 0, 100),
        data_dir=data_dir,
        admin_token=_admin_token(data_dir),
        binance_base_url=binance_base_url,
    )
```

- [ ] **Schritt 4: Test laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_config.py -q'
```

Erwartet: `11 passed`

- [ ] **Schritt 5: Bestandstests laufen lassen (Befund B-5 darf nicht neu auftreten)**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_risk.py tests/test_api.py -q'
```

Erwartet: unverändert `18 passed` (die heutige Zahl aus Spec Abschnitt 2.1/A-20).

- [ ] **Schritt 6: Rot-Nachweis führen**

*Fehler 1 (A-17):* In `_binance_base_url` den Hostvergleich durch einen Präfixvergleich ersetzen:
`if not raw.startswith("https://api.binance.com"):`. Test laufen lassen. Erwartet:
`test_binance_base_url_lehnt_alles_ausserhalb_der_allowlist_ab[https://api.binance.com.evil.example]`
schlägt fehl — `ConfigError` wird **nicht** geworfen, `Failed: DID NOT RAISE`. Ausgabe zeigen,
Änderung zurücknehmen.

*Fehler 2 (Startkapital):* In `load()` den Vorgabewert zurück auf `"100"` setzen
(`_dec("STARTING_BALANCE", "100", ...)`). Test laufen lassen. Erwartet:
`test_starting_balance_vorgabe_ist_10000` schlägt fehl mit
`AssertionError: assert Decimal('100') == Decimal('10000')`. Ausgabe zeigen, Änderung zurücknehmen,
beide Tests erneut grün.

- [ ] **Schritt 7: Commit**

```bash
git add app/aitra/config.py app/tests/test_config.py
git commit -m "feat(config): Decimal-Startkapital, Binance-Allowlist, Startwarnung NARROW_TRADING_WINDOW"
```

---

### Aufgabe 3: `db.py` Migration 2 + `store.py`

**Macht grün:** A-13, A-15, A-16, A-16b (Erstmessung, Zahl aus F-1 folgt in A2).

**Dateien:**
- Geändert: `app/aitra/db.py`
- Neu: `app/aitra/store.py`
- Neu: `app/tests/test_db.py`
- Neu: `app/tests/test_store.py`

**Schnittstellen:**
- Nutzt: `db.connect`, `db.migrate`, `db.now`, `db.add_decision` (Bestand, unverändert),
  `money.to_text(d, dp=8) -> str`, `money.from_text(s) -> Decimal`, `money.SymbolSpec` (Aufgabe 1)
- Stellt bereit (`store.py`):
  - `CandleRow(symbol, interval, open_time, close_time, open, high, low, close, volume, source, fetched_at)`
    (frozen dataclass, Geld als `Decimal` im Python-Objekt, als TEXT in der DB)
  - `upsert_candles(conn, rows: Sequence[CandleRow]) -> None`
  - `get_candles(conn, symbol, interval, start_ms=None, end_ms=None, limit=500) -> list[CandleRow]`
  - `prune_candles(conn, symbol, interval, retention_days) -> int` (Rückgabe: Anzahl gelöschter Zeilen)
  - `create_run(conn, run_id, kind, started_at, code_version, params_json="{}") -> None`
  - `finish_run(conn, run_id, finished_at) -> None`
  - `upsert_symbol_spec(conn, spec: money.SymbolSpec, source, fetched_at) -> None`
  - `get_symbol_spec(conn, symbol) -> money.SymbolSpec | None`
  - `insert_fill(conn, *, run_id, decision_id, symbol, side, candle_open_time, price, qty,
    gross_quote, fee, net_quote, cash_after, fee_bps, slippage_bps, ts) -> int` (Rückgabe: `fills.id`)
  - `get_fills(conn, run_id) -> list[dict]`
  - `upsert_position(conn, *, run_id, symbol, qty, avg_price, realized_pnl, updated_at) -> None`
  - `get_positions(conn, run_id) -> dict[str, dict]`
  - `append_equity_point(conn, *, run_id, ts_ms, equity, cash, benchmark_equity, exposure_pct) -> None`
  - `get_equity_curve(conn, run_id, limit=500) -> list[dict]`
  - `mark_decision_pending(conn, decision_id, pending_since_ms) -> None`
  - `resolve_decision(conn, decision_id, fill_id) -> None`
  - `expire_decision(conn, decision_id) -> None`
  - `get_pending_decisions(conn, run_id=None) -> list[dict]`

**Wichtig für Folgeaufgaben:** `store.py` besitzt **keine eigene Uhr** — `ts`, `fetched_at`,
`started_at`, `finished_at`, `updated_at` und `ts_ms` kommen immer als Parameter vom Aufrufer
(Aufgabe 4 nutzt `get_candles`/`upsert_candles`, Aufgabe 8 nutzt die `decision_*`-Funktionen,
Aufgabe 9/10 nutzen `create_run`/`insert_fill`/`upsert_position`/`append_equity_point`).
Die Durchschnittspreis-Fortschreibung (A-14) passiert **nicht** hier — `store.py` speichert nur,
was ihm gegeben wird. Die Rechnung dafür liegt im `Ledger` (Aufgabe 5).

- [ ] **Schritt 1: Den scheiternden Test schreiben**

```python
# app/tests/test_db.py
from __future__ import annotations

from pathlib import Path

from aitra import db


def test_migration_2_erzeugt_alle_neuen_tabellen(tmp_path: Path):
    conn = db.connect(tmp_path / "a.db")
    version = db.migrate(conn)
    assert version == 2
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert {"candles", "runs", "symbol_specs", "fills", "positions", "equity_curve"} <= tables
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(decisions)").fetchall()}
    assert {"run_id", "fill_id", "pending_since_ms"} <= cols


def test_migration_bewahrt_bestehende_decisions_a15(tmp_path: Path):
    conn = db.connect(tmp_path / "a.db")
    conn.executescript(db.MIGRATIONS[0])
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.commit()
    for i in range(3):
        db.add_decision(conn, symbol="BTCUSDC", action="WAIT", reason=f"r{i}", approved=1)
    before = sorted(
        (r["symbol"], r["action"], r["reason"])
        for r in conn.execute("SELECT symbol, action, reason FROM decisions").fetchall()
    )
    version = db.migrate(conn)
    assert version == 2
    assert conn.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"] == 3
    after = sorted(
        (r["symbol"], r["action"], r["reason"])
        for r in conn.execute("SELECT symbol, action, reason FROM decisions").fetchall()
    )
    assert before == after
```

```python
# app/tests/test_store.py
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from aitra import db, money, store


def _conn(tmp_path: Path):
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    return conn


def _row(i: int) -> store.CandleRow:
    day_ms = 86_400_000
    return store.CandleRow(
        symbol="BTCUSDC", interval="1d", open_time=i * day_ms, close_time=i * day_ms + day_ms - 1,
        open=Decimal("100"), high=Decimal("110"), low=Decimal("90"), close=Decimal("105"),
        volume=Decimal("1.5"), source="fixture", fetched_at="2026-01-01T00:00:00Z",
    )


def test_candles_geld_steht_als_text_a13(tmp_path):
    conn = _conn(tmp_path)
    store.upsert_candles(conn, [_row(0)])
    types = conn.execute(
        "SELECT typeof(open), typeof(high), typeof(low), typeof(close) FROM candles"
    ).fetchall()
    assert len(types) == 1
    assert tuple(types[0]) == ("text", "text", "text", "text")


def test_candles_upsert_ist_idempotent_und_liest_decimal_zurueck(tmp_path):
    conn = _conn(tmp_path)
    store.upsert_candles(conn, [_row(0), _row(1)])
    store.upsert_candles(conn, [_row(0)])  # gleicher Primaerschluessel, kein Duplikat
    rows = store.get_candles(conn, "BTCUSDC", "1d")
    assert len(rows) == 2
    assert rows[0].open_time == 0
    assert rows[0].open == Decimal("100")
    assert isinstance(rows[0].open, Decimal)


def test_prune_candles_a16(tmp_path):
    conn = _conn(tmp_path)
    store.upsert_candles(conn, [_row(i) for i in range(500)])  # Tag 0..499
    deleted = store.prune_candles(conn, "BTCUSDC", "1d", retention_days=400)
    remaining = store.get_candles(conn, "BTCUSDC", "1d", limit=1000)
    assert deleted == 100
    assert len(remaining) == 400
    assert remaining[0].open_time == 100 * 86_400_000


def test_fills_geld_steht_als_text_a13(tmp_path):
    conn = _conn(tmp_path)
    store.create_run(conn, "run-1", "replay", "2026-01-01T00:00:00Z", "0.3.0")
    fid = store.insert_fill(
        conn, run_id="run-1", decision_id=None, symbol="BTCUSDC", side="BUY",
        candle_open_time=900_000, price=Decimal("81287.04"), qty=Decimal("0.01"),
        gross_quote=Decimal("812.8704"), fee=Decimal("0.81287040"),
        net_quote=Decimal("813.68327040"), cash_after=Decimal("9186.31672960"),
        fee_bps=10.0, slippage_bps=5.0, ts="2026-01-01T00:15:00Z",
    )
    assert fid > 0
    types = conn.execute(
        "SELECT typeof(price), typeof(qty), typeof(fee), typeof(cash_after) FROM fills"
    ).fetchall()
    assert tuple(types[0]) == ("text", "text", "text", "text")


def test_positions_und_equity_curve_runtrip(tmp_path):
    conn = _conn(tmp_path)
    store.create_run(conn, "run-1", "replay", "2026-01-01T00:00:00Z", "0.3.0")
    store.upsert_position(
        conn, run_id="run-1", symbol="BTCUSDC", qty=Decimal("0.01"),
        avg_price=Decimal("81287.04"), realized_pnl=Decimal("0"),
        updated_at="2026-01-01T00:15:00Z",
    )
    positions = store.get_positions(conn, "run-1")
    assert positions["BTCUSDC"]["qty"] == Decimal("0.01")
    store.append_equity_point(
        conn, run_id="run-1", ts_ms=900_000, equity=Decimal("10000"), cash=Decimal("9186.32"),
        benchmark_equity=Decimal("10000"), exposure_pct=8.13,
    )
    curve = store.get_equity_curve(conn, "run-1")
    assert len(curve) == 1
    assert curve[0]["equity"] == Decimal("10000")


def test_pending_decision_lebenszyklus(tmp_path):
    conn = _conn(tmp_path)
    did = db.add_decision(conn, symbol="BTCUSDC", action="BUY", reason="test", approved=1,
                           requested_position_pct=8)
    store.mark_decision_pending(conn, did, pending_since_ms=1_000)
    pending = store.get_pending_decisions(conn)
    assert len(pending) == 1
    assert pending[0]["id"] == did
    store.resolve_decision(conn, did, fill_id=42)
    assert store.get_pending_decisions(conn) == []
    row = conn.execute("SELECT fill_id FROM decisions WHERE id=?", (did,)).fetchone()
    assert row["fill_id"] == 42


def test_symbol_spec_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    spec = money.BUILTIN_SPECS["BTCUSDC"]
    store.upsert_symbol_spec(conn, spec, source="builtin", fetched_at="2026-01-01T00:00:00Z")
    back = store.get_symbol_spec(conn, "BTCUSDC")
    assert back == spec
```

- [ ] **Schritt 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_db.py tests/test_store.py -q'
```

Erwartet: `test_db.py` schlägt fehl mit `assert 1 == 2` (Migration steht noch bei Version 1);
`test_store.py` schlägt fehl mit `ModuleNotFoundError: No module named 'aitra.store'`.

- [ ] **Schritt 3: `db.py` — Migration 2 ergänzen**

Die zweite Zeichenkette in `MIGRATIONS` einfügen (Index 1 → Schema-Version 2), wörtlich aus
Spec Abschnitt 5:

```python
MIGRATIONS: list[str] = [
    # 1 – Grundschema
    """
    CREATE TABLE decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        strategy_version TEXT NOT NULL DEFAULT 'manual',
        symbol TEXT NOT NULL,
        action TEXT NOT NULL,
        confidence REAL,
        reason TEXT NOT NULL DEFAULT '',
        requested_position_pct REAL,
        approved INTEGER,
        risk_code TEXT,
        risk_reason TEXT
    );
    CREATE INDEX idx_decisions_ts ON decisions(ts);
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        component TEXT NOT NULL,
        severity TEXT NOT NULL,
        event TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_events_ts ON events(ts);
    CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
    # 2 – Marktdaten, Ledger, Benchmark
    """
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
    """,
]
```

Sonst bleibt `db.py` unverändert.

- [ ] **Schritt 4: `store.py` schreiben**

```python
"""Lese-/Schreibzugriff auf die in Migration 2 angelegten Tabellen.

Geld wandert ausschliesslich als TEXT durch SQLite (E-007). Diese Datei ist die
einzige Stelle, die zwischen Decimal und TEXT wandelt (money.to_text/from_text).
Keine Uhr: jeder Zeitstempel (ts, fetched_at, started_at, finished_at, updated_at,
ts_ms) kommt als Parameter vom Aufrufer.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from . import money

DP = 8


@dataclass(frozen=True)
class CandleRow:
    """Eine gespeicherte, abgeschlossene Kerze (offene Kerzen werden nie gespeichert)."""
    symbol: str
    interval: str
    open_time: int
    close_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str
    fetched_at: str


def upsert_candles(conn: sqlite3.Connection, rows: Sequence[CandleRow]) -> None:
    conn.executemany(
        """INSERT INTO candles (symbol, interval, open_time, close_time, open, high, low,
                                 close, volume, source, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(symbol, interval, open_time) DO UPDATE SET
               close_time=excluded.close_time, open=excluded.open, high=excluded.high,
               low=excluded.low, close=excluded.close, volume=excluded.volume,
               source=excluded.source, fetched_at=excluded.fetched_at""",
        [
            (r.symbol, r.interval, r.open_time, r.close_time,
             money.to_text(r.open, DP), money.to_text(r.high, DP),
             money.to_text(r.low, DP), money.to_text(r.close, DP),
             money.to_text(r.volume, DP), r.source, r.fetched_at)
            for r in rows
        ],
    )
    conn.commit()


def get_candles(
    conn: sqlite3.Connection, symbol: str, interval: str,
    start_ms: int | None = None, end_ms: int | None = None, limit: int = 500,
) -> list[CandleRow]:
    sql = "SELECT * FROM candles WHERE symbol = ? AND interval = ?"
    args: list = [symbol, interval]
    if start_ms is not None:
        sql += " AND open_time >= ?"
        args.append(start_ms)
    if end_ms is not None:
        sql += " AND open_time <= ?"
        args.append(end_ms)
    sql += " ORDER BY open_time ASC LIMIT ?"
    args.append(limit)
    out = []
    for r in conn.execute(sql, args).fetchall():
        out.append(CandleRow(
            symbol=r["symbol"], interval=r["interval"],
            open_time=r["open_time"], close_time=r["close_time"],
            open=money.from_text(r["open"]), high=money.from_text(r["high"]),
            low=money.from_text(r["low"]), close=money.from_text(r["close"]),
            volume=money.from_text(r["volume"]), source=r["source"], fetched_at=r["fetched_at"],
        ))
    return out


def prune_candles(conn: sqlite3.Connection, symbol: str, interval: str, retention_days: int) -> int:
    """Loescht Kerzen, deren close_time <= (neueste close_time - retention_days) ist.

    A-16: Die Grenze ist inklusiv (<=). Mit < wuerde die aelteste noch zu loeschende
    Zeile ueberleben und die Zaehlung um 1 verschieben.
    """
    row = conn.execute(
        "SELECT MAX(close_time) m FROM candles WHERE symbol = ? AND interval = ?",
        (symbol, interval),
    ).fetchone()
    if row is None or row["m"] is None:
        return 0
    threshold = row["m"] - retention_days * 86_400_000
    cur = conn.execute(
        "DELETE FROM candles WHERE symbol = ? AND interval = ? AND close_time <= ?",
        (symbol, interval, threshold),
    )
    conn.commit()
    return cur.rowcount


def create_run(conn: sqlite3.Connection, run_id: str, kind: str, started_at: str,
               code_version: str, params_json: str = "{}") -> None:
    conn.execute(
        "INSERT INTO runs (run_id, kind, started_at, code_version, params_json) VALUES (?, ?, ?, ?, ?)",
        (run_id, kind, started_at, code_version, params_json),
    )
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: str, finished_at: str) -> None:
    conn.execute("UPDATE runs SET finished_at = ? WHERE run_id = ?", (finished_at, run_id))
    conn.commit()


def upsert_symbol_spec(conn: sqlite3.Connection, spec: money.SymbolSpec, source: str, fetched_at: str) -> None:
    conn.execute(
        """INSERT INTO symbol_specs (symbol, base, quote, tick_size, step_size, min_qty,
                                      min_notional, base_precision, quote_precision, source, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(symbol) DO UPDATE SET
               base=excluded.base, quote=excluded.quote, tick_size=excluded.tick_size,
               step_size=excluded.step_size, min_qty=excluded.min_qty,
               min_notional=excluded.min_notional, base_precision=excluded.base_precision,
               quote_precision=excluded.quote_precision, source=excluded.source,
               fetched_at=excluded.fetched_at""",
        (spec.symbol, spec.base, spec.quote, money.to_text(spec.tick_size, DP),
         money.to_text(spec.step_size, DP), money.to_text(spec.min_qty, DP),
         money.to_text(spec.min_notional, DP), spec.base_precision, spec.quote_precision,
         source, fetched_at),
    )
    conn.commit()


def get_symbol_spec(conn: sqlite3.Connection, symbol: str) -> money.SymbolSpec | None:
    r = conn.execute("SELECT * FROM symbol_specs WHERE symbol = ?", (symbol,)).fetchone()
    if r is None:
        return None
    return money.SymbolSpec(
        symbol=r["symbol"], base=r["base"], quote=r["quote"],
        tick_size=money.from_text(r["tick_size"]), step_size=money.from_text(r["step_size"]),
        min_qty=money.from_text(r["min_qty"]), min_notional=money.from_text(r["min_notional"]),
        base_precision=r["base_precision"], quote_precision=r["quote_precision"],
    )


def insert_fill(conn: sqlite3.Connection, *, run_id: str, decision_id: int | None, symbol: str,
                 side: str, candle_open_time: int, price: Decimal, qty: Decimal,
                 gross_quote: Decimal, fee: Decimal, net_quote: Decimal, cash_after: Decimal,
                 fee_bps: float, slippage_bps: float, ts: str) -> int:
    cur = conn.execute(
        """INSERT INTO fills (ts, run_id, decision_id, symbol, side, candle_open_time, price, qty,
                               gross_quote, fee, net_quote, cash_after, fee_bps, slippage_bps)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ts, run_id, decision_id, symbol, side, candle_open_time,
         money.to_text(price, DP), money.to_text(qty, DP), money.to_text(gross_quote, DP),
         money.to_text(fee, DP), money.to_text(net_quote, DP), money.to_text(cash_after, DP),
         fee_bps, slippage_bps),
    )
    conn.commit()
    return cur.lastrowid


def get_fills(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM fills WHERE run_id = ? ORDER BY id ASC", (run_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("price", "qty", "gross_quote", "fee", "net_quote", "cash_after"):
            d[k] = money.from_text(d[k])
        out.append(d)
    return out


def upsert_position(conn: sqlite3.Connection, *, run_id: str, symbol: str, qty: Decimal,
                     avg_price: Decimal, realized_pnl: Decimal, updated_at: str) -> None:
    conn.execute(
        """INSERT INTO positions (run_id, symbol, qty, avg_price, realized_pnl, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(run_id, symbol) DO UPDATE SET
               qty=excluded.qty, avg_price=excluded.avg_price,
               realized_pnl=excluded.realized_pnl, updated_at=excluded.updated_at""",
        (run_id, symbol, money.to_text(qty, DP), money.to_text(avg_price, DP),
         money.to_text(realized_pnl, DP), updated_at),
    )
    conn.commit()


def get_positions(conn: sqlite3.Connection, run_id: str) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM positions WHERE run_id = ?", (run_id,)).fetchall()
    out = {}
    for r in rows:
        out[r["symbol"]] = {
            "qty": money.from_text(r["qty"]), "avg_price": money.from_text(r["avg_price"]),
            "realized_pnl": money.from_text(r["realized_pnl"]), "updated_at": r["updated_at"],
        }
    return out


def append_equity_point(conn: sqlite3.Connection, *, run_id: str, ts_ms: int, equity: Decimal,
                         cash: Decimal, benchmark_equity: Decimal | None, exposure_pct: float) -> None:
    conn.execute(
        """INSERT INTO equity_curve (run_id, ts_ms, equity, cash, benchmark_equity, exposure_pct)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(run_id, ts_ms) DO UPDATE SET
               equity=excluded.equity, cash=excluded.cash,
               benchmark_equity=excluded.benchmark_equity, exposure_pct=excluded.exposure_pct""",
        (run_id, ts_ms, money.to_text(equity, DP), money.to_text(cash, DP),
         None if benchmark_equity is None else money.to_text(benchmark_equity, DP), exposure_pct),
    )
    conn.commit()


def get_equity_curve(conn: sqlite3.Connection, run_id: str, limit: int = 500) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM equity_curve WHERE run_id = ? ORDER BY ts_ms ASC LIMIT ?", (run_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "ts_ms": r["ts_ms"], "equity": money.from_text(r["equity"]),
            "cash": money.from_text(r["cash"]),
            "benchmark_equity": None if r["benchmark_equity"] is None else money.from_text(r["benchmark_equity"]),
            "exposure_pct": r["exposure_pct"],
        })
    return out


def mark_decision_pending(conn: sqlite3.Connection, decision_id: int, pending_since_ms: int) -> None:
    conn.execute("UPDATE decisions SET pending_since_ms = ? WHERE id = ?", (pending_since_ms, decision_id))
    conn.commit()


def resolve_decision(conn: sqlite3.Connection, decision_id: int, fill_id: int) -> None:
    conn.execute(
        "UPDATE decisions SET fill_id = ?, pending_since_ms = NULL WHERE id = ?",
        (fill_id, decision_id),
    )
    conn.commit()


def expire_decision(conn: sqlite3.Connection, decision_id: int) -> None:
    conn.execute(
        "UPDATE decisions SET pending_since_ms = NULL, risk_code = 'PENDING_EXPIRED' WHERE id = ?",
        (decision_id,),
    )
    conn.commit()


def get_pending_decisions(conn: sqlite3.Connection, run_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM decisions WHERE pending_since_ms IS NOT NULL"
    args: list = []
    if run_id is not None:
        sql += " AND run_id = ?"
        args.append(run_id)
    sql += " ORDER BY id ASC"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]
```

- [ ] **Schritt 5: Test laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_db.py tests/test_store.py -q'
```

Erwartet: `2 passed` (`test_db.py`) und `7 passed` (`test_store.py`).

- [ ] **Schritt 6: Rot-Nachweis führen**

*Fehler 1 (A-16):* In `prune_candles` `<=` durch `<` ersetzen. Test laufen lassen. Erwartet:
`test_prune_candles_a16` schlägt fehl mit `AssertionError: assert 99 == 100` (bzw.
`assert 401 == 400` für `remaining`). Ausgabe zeigen, Änderung zurücknehmen.

*Fehler 2 (A-15):* In der Migration 2 vor die erste `CREATE TABLE` die Zeile `DROP TABLE decisions;`
einfügen. Test laufen lassen. Erwartet: `test_migration_bewahrt_bestehende_decisions_a15`
schlägt fehl mit `sqlite3.OperationalError: no such table: decisions` (die nachfolgenden
`ALTER TABLE decisions ADD COLUMN`-Zeilen finden die Tabelle nicht mehr). Ausgabe zeigen,
Änderung zurücknehmen, beide Tests erneut grün.

- [ ] **Schritt 7: Commit**

```bash
git add app/aitra/db.py app/aitra/store.py app/tests/test_db.py app/tests/test_store.py
git commit -m "feat(store): Migration 2 (Kerzen, Runs, Fills, Positionen, Equity-Kurve) + store.py"
```

---

### Aufgabe 4: `marketdata.py` — Kerzen, Uhr, Quellen, Veraltet-Erkennung

**Macht grün:** A-8b (vollständig), A-11 und A-11b (auf Funktionsebene — `staleness()` selbst;
die Anbindung an `state.kill_switch`/HTTP 503 folgt mit `web.py` in Plan A2).

**Dateien:**
- Neu: `app/aitra/marketdata.py`
- Neu: `app/tests/test_marketdata.py`

**Schnittstellen:**
- Nutzt: `store.CandleRow`, `store.get_candles(conn, symbol, interval, start_ms=None, end_ms=None, limit=500) -> list[store.CandleRow]` (Aufgabe 3)
- Stellt bereit:
  - `Candle(symbol, interval, open_time, close_time, open, high, low, close, volume, closed)`
    (frozen dataclass, Felder wie Spec 4.1)
  - `class CandleSource(Protocol): def candles(self, symbol, interval, start_ms=None, limit=500) -> list[Candle]`
  - `class Clock(Protocol): def now_ms(self) -> int`
  - `class WallClock: def now_ms(self) -> int` — der **einzige** erlaubte `time.time()`-Aufruf
    im gesamten `aitra`-Paket außerhalb der Tests (A-8b)
  - `class SimClock: def __init__(self, ms: int); def now_ms(self) -> int; def set(self, ms: int) -> None`
  - `class ListSource: def __init__(self, candles: Sequence[Candle]); def candles(...) -> list[Candle]`
  - `class SqliteSource: def __init__(self, conn: sqlite3.Connection); def candles(...) -> list[Candle]`
  - `Staleness(status: str, data_age_s: float, clock_skew_s: float)` (frozen dataclass;
    `status` ∈ `{"ok", "warn", "stale"}`)
  - `staleness(clock: Clock, latest_close_time_ms: int | None, server_time_ms: int | None,
    interval_s: int, warn_s: int = 150, kill_s: int = 300,
    clock_skew_warn_s: int = 5, clock_skew_kill_s: int = 30) -> Staleness`

- [ ] **Schritt 1: Den scheiternden Test schreiben**

```python
# app/tests/test_marketdata.py
from __future__ import annotations

import re
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from aitra import db, store
from aitra.marketdata import Candle, ListSource, SimClock, SqliteSource, WallClock, staleness


def _candle(open_time: int, closed: bool = True) -> Candle:
    step = 900_000
    return Candle(
        symbol="BTCUSDC", interval="15m", open_time=open_time, close_time=open_time + step - 1,
        open=Decimal("81000"), high=Decimal("81100"), low=Decimal("80900"), close=Decimal("81050"),
        volume=Decimal("1.2"), closed=closed,
    )


def test_wallclock_liefert_ms_seit_epoche():
    ms = WallClock().now_ms()
    assert ms > 1_700_000_000_000  # nach 2023, grobe Plausibilitaet


def test_simclock_liefert_gesetzten_wert():
    clock = SimClock(1_000)
    assert clock.now_ms() == 1_000
    clock.set(2_000)
    assert clock.now_ms() == 2_000


def test_list_source_liefert_aufsteigend_und_gedeckelt():
    candles = [_candle(i * 900_000) for i in range(10)]
    src = ListSource(candles)
    out = src.candles("BTCUSDC", "15m", limit=3)
    assert [c.open_time for c in out] == [0, 900_000, 1_800_000]


def test_sqlite_source_liest_nur_gespeicherte_geschlossene_kerzen(tmp_path: Path):
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    store.upsert_candles(conn, [
        store.CandleRow(symbol="BTCUSDC", interval="15m", open_time=0, close_time=899_999,
                         open=Decimal("81000"), high=Decimal("81100"), low=Decimal("80900"),
                         close=Decimal("81050"), volume=Decimal("1.2"), source="fixture",
                         fetched_at="2026-01-01T00:00:00Z"),
    ])
    src = SqliteSource(conn)
    out = src.candles("BTCUSDC", "15m")
    assert len(out) == 1
    assert out[0].closed is True
    assert out[0].open == Decimal("81000")
    assert isinstance(out[0].open, Decimal)


def test_list_und_sqlite_source_liefern_identische_kerzen(tmp_path: Path):
    candles = [_candle(i * 900_000) for i in range(5)]
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    store.upsert_candles(conn, [
        store.CandleRow(symbol=c.symbol, interval=c.interval, open_time=c.open_time,
                         close_time=c.close_time, open=c.open, high=c.high, low=c.low,
                         close=c.close, volume=c.volume, source="fixture",
                         fetched_at="2026-01-01T00:00:00Z")
        for c in candles
    ])
    a = ListSource(candles).candles("BTCUSDC", "15m", limit=100)
    b = SqliteSource(conn).candles("BTCUSDC", "15m", limit=100)
    assert [(c.open_time, c.open, c.close) for c in a] == [(c.open_time, c.open, c.close) for c in b]


@pytest.mark.parametrize("interval_s,age_s,expected", [
    (60, 149, "ok"), (60, 299, "warn"), (60, 301, "stale"),
    (900, 1349, "ok"), (900, 2699, "warn"), (900, 2701, "stale"),
])
def test_staleness_intervallrelative_schwellen_a11(interval_s, age_s, expected):
    clock = SimClock(age_s * 1000)
    result = staleness(clock, latest_close_time_ms=0, server_time_ms=clock.now_ms(),
                        interval_s=interval_s)
    assert result.status == expected


@pytest.mark.parametrize("skew_s,expected", [(31, "stale"), (29, "warn"), (4, "ok"), (-31, "stale")])
def test_staleness_uhrversatz_a11b(skew_s, expected):
    clock = SimClock(1_000_000)
    result = staleness(clock, latest_close_time_ms=clock.now_ms(), server_time_ms=clock.now_ms() - skew_s * 1000,
                        interval_s=900)
    assert result.status == expected


def test_a8b_keine_versteckte_uhr_kein_versteckter_zufall():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "marketdata.py").read_text()
    matches = list(re.finditer(r"time\.time|datetime\.(now|utcnow)|random\.", text))
    assert len(matches) == 1
    wallclock_start = text.index("class WallClock")
    tail = text[wallclock_start + len("class WallClock"):]
    next_top_level = re.search(r"\nclass |\ndef ", tail)
    wallclock_end = len(text) if next_top_level is None else wallclock_start + len("class WallClock") + next_top_level.start()
    assert wallclock_start < matches[0].start() < wallclock_end
```

- [ ] **Schritt 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_marketdata.py -q'
```

Erwartet: `ModuleNotFoundError: No module named 'aitra.marketdata'`

- [ ] **Schritt 3: `marketdata.py` schreiben**

```python
"""Kerzenquellen, Uhr und Veraltet-Erkennung.

Reine Datenhaltung und -abfrage: kein Netz. Nur WallClock.now_ms() ruft time.time()
auf (E-001, A-8b) — alle anderen Uhren sind Werte, die der Aufrufer setzt.
"""
from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Sequence

from . import store


@dataclass(frozen=True)
class Candle:
    symbol: str
    interval: str
    open_time: int
    close_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    closed: bool


class CandleSource(Protocol):
    def candles(self, symbol: str, interval: str, start_ms: int | None = None, limit: int = 500) -> list[Candle]: ...


class Clock(Protocol):
    def now_ms(self) -> int: ...


class WallClock:
    """Die Uhr fuer den Live-Betrieb. Der einzige time.time()-Aufruf in diesem Paket."""
    def now_ms(self) -> int:
        return int(time.time() * 1000)


class SimClock:
    """Die Uhr fuer Replay und Zeitraffer: wird explizit auf die aktuelle Kerze gesetzt."""
    def __init__(self, ms: int) -> None:
        self._ms = ms

    def now_ms(self) -> int:
        return self._ms

    def set(self, ms: int) -> None:
        self._ms = ms


class ListSource:
    """Kerzenquelle aus dem Speicher, fuer den Zeitraffer (ausschliesslich geschlossene Kerzen)."""
    def __init__(self, candles: Sequence[Candle]) -> None:
        self._candles = sorted(
            (c for c in candles if c.closed), key=lambda c: (c.symbol, c.interval, c.open_time)
        )

    def candles(self, symbol: str, interval: str, start_ms: int | None = None, limit: int = 500) -> list[Candle]:
        out = [c for c in self._candles if c.symbol == symbol and c.interval == interval]
        if start_ms is not None:
            out = [c for c in out if c.open_time >= start_ms]
        return out[:limit]


class SqliteSource:
    """Kerzenquelle aus der Datenbank, fuer den db-Replay."""
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def candles(self, symbol: str, interval: str, start_ms: int | None = None, limit: int = 500) -> list[Candle]:
        rows = store.get_candles(self._conn, symbol, interval, start_ms=start_ms, limit=limit)
        return [
            Candle(symbol=r.symbol, interval=r.interval, open_time=r.open_time, close_time=r.close_time,
                   open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume, closed=True)
            for r in rows
        ]


@dataclass(frozen=True)
class Staleness:
    status: str  # "ok" | "warn" | "stale"
    data_age_s: float
    clock_skew_s: float


def staleness(
    clock: Clock,
    latest_close_time_ms: int | None,
    server_time_ms: int | None,
    interval_s: int,
    warn_s: int = 150,
    kill_s: int = 300,
    clock_skew_warn_s: int = 5,
    clock_skew_kill_s: int = 30,
) -> Staleness:
    """Veraltet-Erkennung mit intervallrelativen Schwellen (Spec 8.2).

    warn_s/kill_s sind Untergrenzen; wirksam ist max(wert, 1,5*interval_s) bzw.
    max(wert, 3*interval_s). Ohne diese Anhebung wuerde jedes Intervall oberhalb
    von 1m im Normalbetrieb dauernd ausloesen.
    """
    warn_eff = max(warn_s, math.ceil(1.5 * interval_s))
    kill_eff = max(kill_s, 3 * interval_s)

    now = clock.now_ms()
    data_age_s = math.inf if latest_close_time_ms is None else (now - latest_close_time_ms) / 1000
    clock_skew_s = 0.0 if server_time_ms is None else abs(now - server_time_ms) / 1000

    if data_age_s > kill_eff or clock_skew_s > clock_skew_kill_s:
        status = "stale"
    elif data_age_s > warn_eff or clock_skew_s > clock_skew_warn_s:
        status = "warn"
    else:
        status = "ok"
    return Staleness(status=status, data_age_s=data_age_s, clock_skew_s=clock_skew_s)
```

*Hinweis zu Schritt 1:* Der letzte Test (`test_a8b_keine_versteckte_uhr...`) prüft nur grob,
dass der einzige Treffer nach `class WallClock` liegt; das eigentliche, scharfe Kriterium A-11
läuft später als eigenständiger `grep`-Befehl (siehe A-8b-Kommando in den globalen Randbedingungen)
und wird in Schritt 5 zusätzlich so ausgeführt.

- [ ] **Schritt 4: Test laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_marketdata.py -q'
```

Erwartet: `13 passed`

- [ ] **Schritt 5: A-8b als exakten Befehl bestätigen**

```bash
grep -rnE "time\.time|datetime\.(now|utcnow)|random\." app/aitra/marketdata.py
```

Erwartet: genau **eine** Zeile, `return int(time.time() * 1000)` innerhalb `class WallClock`.

- [ ] **Schritt 6: Rot-Nachweis führen**

*Fehler 1 (A-11):* In `staleness()` `max(warn_s, math.ceil(1.5 * interval_s))` durch `warn_s`
ersetzen (die intervallrelative Anhebung entfernen). Test laufen lassen. Erwartet:
`test_staleness_intervallrelative_schwellen_a11[900-1349-ok]` schlägt fehl mit
`AssertionError: assert 'warn' == 'ok'` (1.349 s liegt dann über der starren 150-s-Schwelle).
Ausgabe zeigen, Änderung zurücknehmen.

*Fehler 2 (A-8b):* Ein `import datetime` und `datetime.datetime.now()` irgendwo in `ListSource`
einbauen (unbenutzt, nur um den Treffer zu erzeugen). `grep`-Befehl aus Schritt 5 erneut laufen
lassen. Erwartet: **zwei** Treffer statt einem. Änderung zurücknehmen, `grep` liefert wieder
genau eine Zeile.

- [ ] **Schritt 7: Commit**

```bash
git add app/aitra/marketdata.py app/tests/test_marketdata.py
git commit -m "feat(marketdata): Candle, Clock/WallClock/SimClock, ListSource/SqliteSource, staleness()"
```

---

### Aufgabe 5: `ledger.py` — Fills, Kasse, Positionen, Bewertung

**Macht grün:** A-1, A-2, A-4 (die zwei Messpunkte an der Grenze), A-8b (Beitrag: 0 Treffer in
`ledger.py`). *A-3 (1.000 Stichproben), A-4b (500 Preise) und A-5 (bezifferter Losgrößenverlust)
brauchen zusätzlich `sizing.size_order()` und werden vollständig erst in Aufgabe 6 grün.*

**Dateien:**
- Neu: `app/aitra/ledger.py`
- Neu: `app/tests/test_ledger.py`

**Schnittstellen:**
- Nutzt: `money.dec/step_down/tick_down/tick_up/to_text/CTX`, `money.SymbolSpec` (Aufgabe 1),
  `marketdata.Candle` (Aufgabe 4), `risk.PortfolioState(equity, start_of_day_equity, exposure_pct)`
  (Bestand, siehe `app/aitra/risk.py` — wird erst in Aufgabe 7 um `position_pct_by_symbol` erweitert)
- Stellt bereit:
  - `Order(symbol: str, side: str, base_qty: Decimal)` (frozen dataclass)
  - `Fill(symbol, side, price, qty, gross_quote, fee, net_quote, cash_after,
    candle_open_time, fee_bps, slippage_bps)` (frozen dataclass, Felder wie Spec 4.1)
  - `Rejection(code: str, reason: str)` (frozen dataclass;
    `code` ∈ `{MIN_NOTIONAL, MIN_QTY, INSUFFICIENT_CASH, NO_POSITION, NO_SPEC, ZERO_QTY}`)
  - `Position(symbol: str, qty: Decimal, avg_price: Decimal, realized_pnl: Decimal)` (frozen dataclass)
  - `Valuation(ts_ms, cash, position_value, equity, exposure_pct, position_pct_by_symbol)`
    (frozen dataclass, `position_pct_by_symbol: dict[str, float]`)
  - `class Ledger:`
    - `__init__(self, starting_cash: Decimal, specs: Mapping[str, money.SymbolSpec], fee_bps: float, slippage_bps: float)`
    - `apply(self, order: Order, candle_next: Candle) -> Fill | Rejection`
    - `mark(self, marks: Mapping[str, Decimal], ts_ms: int) -> Valuation`
    - `to_portfolio_state(self, v: Valuation, start_of_day_equity: Decimal) -> risk.PortfolioState`
      **(Zwischenstand: liefert in dieser Aufgabe nur `equity`, `start_of_day_equity`,
      `exposure_pct` — das Feld `position_pct_by_symbol` existiert in `risk.PortfolioState`
      erst nach Aufgabe 7. Aufgabe 7 erweitert genau diese Methode um den Parameter.)**
    - `cash: Decimal` (Property, aktueller Kassenstand)
    - `position(self, symbol: str) -> Position` (Nullposition, falls keine gehalten wird)

**Wichtig für Folgeaufgaben:** `Ledger.apply()` entnimmt `candle_next` **ausschließlich**
`open` und `open_time` (E-006, Spec 4.2). `Order.base_qty` ist bereits auf `step_size`
abgerundet — `apply()` quantisiert defensiv erneut (idempotent), damit ein Programmierfehler
in `sizing.py` (Aufgabe 6) hier nicht zu falschen Fills führt, sondern zu einer Ablehnung.
Diese Datei ruft **nie** `time.time()`, `datetime.now()/utcnow()` oder `random.*` auf (A-8b) —
jeder Zeitstempel ist ein Parameter.

- [ ] **Schritt 1: Den scheiternden Test schreiben**

```python
# app/tests/test_ledger.py
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from aitra import money
from aitra.ledger import Fill, Ledger, Order, Rejection, Valuation
from aitra.marketdata import Candle

BTC = money.BUILTIN_SPECS["BTCUSDC"]
ETH = money.BUILTIN_SPECS["ETHUSDC"]
SPECS = {"BTCUSDC": BTC, "ETHUSDC": ETH}


def _candle(open_price: str, open_time: int = 900_000, high="999999", low="1", close="0") -> Candle:
    return Candle(
        symbol="BTCUSDC", interval="15m", open_time=open_time, close_time=open_time + 899_999,
        open=Decimal(open_price), high=Decimal(high), low=Decimal(low), close=Decimal(close),
        volume=Decimal("1"), closed=True,
    )


def _ledger(cash: str = "10000", fee_bps: float = 10.0, slippage_bps: float = 5.0) -> Ledger:
    return Ledger(starting_cash=Decimal(cash), specs=SPECS, fee_bps=fee_bps, slippage_bps=slippage_bps)


def test_buy_fill_rechnet_slippage_und_gebuehr_wie_in_spec_6_1():
    ledger = _ledger()
    f = ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.01")), _candle("81287.03"))
    assert isinstance(f, Fill)
    # s=0.0005, f=0.0010: exec = tick_up(81287.03*1.0005) = tick_up(81327.6736...) = 81327.68
    assert f.price == Decimal("81327.68")
    assert f.gross_quote == Decimal("813.2768")
    assert f.fee == Decimal("0.81327680")
    assert f.net_quote == Decimal("814.09007680")
    assert f.cash_after == Decimal("9185.90992320")
    assert f.cash_after == Decimal("10000") - f.net_quote
    assert f.candle_open_time == 900_000


def test_sell_fill_rundet_gegen_den_haendler():
    ledger = _ledger()
    ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.02")), _candle("81287.03"))
    f = ledger.apply(Order("BTCUSDC", "SELL", Decimal("0.01")), _candle("81400.00", open_time=1_800_000))
    assert isinstance(f, Fill)
    # s=0.0005: exec = tick_down(81400*0.9995) = tick_down(81359.30) = 81359.30
    assert f.price == Decimal("81359.30")
    assert f.side == "SELL"


def test_ledger_nutzt_nur_open_und_open_time_der_folgekerze():
    ledger = _ledger()
    hoch = _candle("81287.03", high="999999999", low="1", close="1")
    niedrig = _candle("81287.03", high="1", low="1", close="1")
    f1 = ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.01")), hoch)
    ledger2 = _ledger()
    f2 = ledger2.apply(Order("BTCUSDC", "BUY", Decimal("0.01")), niedrig)
    assert isinstance(f1, Fill) and isinstance(f2, Fill)
    assert f1.price == f2.price == Decimal("81327.68")


def test_no_spec_ohne_symbolspec():
    ledger = _ledger()
    r = ledger.apply(Order("DOGEUSDC", "BUY", Decimal("100")), _candle("0.1", ))
    assert isinstance(r, Rejection) and r.code == "NO_SPEC"


def test_zero_qty_nach_quantisierung():
    ledger = _ledger()
    r = ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.000001")), _candle("81287.03"))
    assert isinstance(r, Rejection) and r.code == "ZERO_QTY"


def test_min_notional_an_der_grenze_a4():
    ledger = _ledger()
    # 4,99 USDC-Ziel -> Rejection
    r = ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.00006")), _candle("81287.04"))
    assert isinstance(r, Rejection) and r.code == "MIN_NOTIONAL"
    assert "garantiert ab" in r.reason
    # 5,82 USDC (K-3, effektive Grenze) -> Fill
    eff = BTC.effective_min_notional(Decimal("81287.04"))
    qty = money.step_down(eff / Decimal("81287.04") * Decimal("1.02"), BTC.step_size)
    f = ledger.apply(Order("BTCUSDC", "BUY", qty), _candle("81287.04"))
    assert isinstance(f, Fill)


def test_insufficient_cash():
    ledger = _ledger(cash="1")
    r = ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.01")), _candle("81287.03"))
    assert isinstance(r, Rejection) and r.code == "INSUFFICIENT_CASH"
    assert ledger.cash == Decimal("1")  # Kasse bleibt unveraendert und nichtnegativ


def test_no_position_beim_verkauf_ohne_bestand():
    ledger = _ledger()
    r = ledger.apply(Order("BTCUSDC", "SELL", Decimal("0.01")), _candle("81287.03"))
    assert isinstance(r, Rejection) and r.code == "NO_POSITION"


def test_no_position_beim_verkauf_ueber_bestand_hinaus():
    ledger = _ledger()
    ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.01")), _candle("81287.03"))
    r = ledger.apply(Order("BTCUSDC", "SELL", Decimal("0.02")), _candle("81287.03", open_time=1_800_000))
    assert isinstance(r, Rejection) and r.code == "NO_POSITION"


def test_mark_liefert_exakte_identitaet():
    ledger = _ledger()
    ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.05")), _candle("81287.03"))
    v = ledger.mark({"BTCUSDC": Decimal("82000")}, ts_ms=1_800_000)
    assert isinstance(v, Valuation)
    berechnet = v.cash + ledger.position("BTCUSDC").qty * Decimal("82000")
    assert v.equity - berechnet == Decimal("0")


def test_buchhaltung_identitaet_a1():
    """A-1: feste, deterministische Sequenz (kein Zufall), 10.000 Fillversuche."""
    ledger = _ledger()
    ts = 900_000
    treffer = 0
    for i in range(10_000):
        symbol = "BTCUSDC" if i % 2 == 0 else "ETHUSDC"
        spec = SPECS[symbol]
        price = Decimal("81287.03") if symbol == "BTCUSDC" else Decimal("2631.77")
        price = price + Decimal(i % 50) * spec.tick_size
        side = "BUY" if (i % 3) != 2 else "SELL"
        pos = ledger.position(symbol)
        if side == "SELL" and pos.qty == 0:
            side = "BUY"
        equity_now = ledger.mark({"BTCUSDC": price, "ETHUSDC": price}, ts_ms=ts).equity
        target_quote = equity_now * Decimal("2") / Decimal(100)
        raw_qty = target_quote / price if side == "BUY" else min(target_quote / price, pos.qty)
        qty = money.step_down(raw_qty, spec.step_size)
        candle = Candle(symbol=symbol, interval="15m", open_time=ts, close_time=ts + 899_999,
                         open=price, high=price, low=price, close=price, volume=Decimal("1"), closed=True)
        result = ledger.apply(Order(symbol, side, qty), candle)
        ts += 900_000
        if isinstance(result, Rejection):
            continue
        treffer += 1
        v = ledger.mark({"BTCUSDC": price, "ETHUSDC": price}, ts_ms=ts)
        held_value = sum(ledger.position(s).qty * price for s in ("BTCUSDC", "ETHUSDC"))
        assert v.equity - (v.cash + held_value) == Decimal("0")
    assert treffer > 5000  # die Pruefflaeche darf nicht leer sein
    assert ledger.cash >= Decimal("0")
    assert ledger.position("BTCUSDC").qty >= Decimal("0")
    assert ledger.position("ETHUSDC").qty >= Decimal("0")


def test_a8b_keine_versteckte_uhr_kein_versteckter_zufall():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "ledger.py").read_text()
    assert re.search(r"time\.time|datetime\.(now|utcnow)|random\.", text) is None
```

- [ ] **Schritt 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_ledger.py -q'
```

Erwartet: `ModuleNotFoundError: No module named 'aitra.ledger'`

- [ ] **Schritt 3: `ledger.py` schreiben**

```python
"""Das Paper-Ledger: Fills, Kasse, Positionen, Bewertung.

Rein (E-001): kein Netz, keine DB, keine Uhr, kein Zufall. Jeder Zeitstempel ist
ein Parameter. apply() entnimmt der Folgekerze ausschliesslich open und open_time
(E-006) — high/low/close bleiben ungenutzt.
"""
from __future__ import annotations

import decimal
from dataclasses import dataclass, field
from decimal import ROUND_UP, Decimal
from typing import Mapping

from . import money, risk
from .marketdata import Candle

_ONE = Decimal(1)
_BPS = Decimal(10_000)


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str  # "BUY" | "SELL"
    base_qty: Decimal


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: str
    price: Decimal
    qty: Decimal
    gross_quote: Decimal
    fee: Decimal
    net_quote: Decimal
    cash_after: Decimal
    candle_open_time: int
    fee_bps: float
    slippage_bps: float


@dataclass(frozen=True)
class Rejection:
    code: str
    reason: str


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: Decimal
    avg_price: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True)
class Valuation:
    ts_ms: int
    cash: Decimal
    position_value: Decimal
    equity: Decimal
    exposure_pct: float
    position_pct_by_symbol: dict[str, float]


def _fee_round_up(amount: Decimal, quote_precision: int) -> Decimal:
    unit = Decimal(1).scaleb(-quote_precision)
    return amount.quantize(unit, rounding=ROUND_UP)


class Ledger:
    def __init__(
        self,
        starting_cash: Decimal,
        specs: Mapping[str, money.SymbolSpec],
        fee_bps: float,
        slippage_bps: float,
    ) -> None:
        self._cash = starting_cash
        self._specs = specs
        self._fee_bps = fee_bps
        self._slippage_bps = slippage_bps
        self._positions: dict[str, Position] = {}

    @property
    def cash(self) -> Decimal:
        return self._cash

    def position(self, symbol: str) -> Position:
        return self._positions.get(symbol, Position(symbol, Decimal(0), Decimal(0), Decimal(0)))

    def apply(self, order: Order, candle_next: Candle) -> Fill | Rejection:
        with decimal.localcontext(money.CTX):
            spec = self._specs.get(order.symbol)
            if spec is None:
                return Rejection("NO_SPEC", f"Keine SymbolSpec für {order.symbol}")

            qty = money.step_down(order.base_qty, spec.step_size)
            if qty <= 0:
                return Rejection("ZERO_QTY", "Menge nach Quantisierung 0")

            s = Decimal(str(self._slippage_bps)) / _BPS
            f = Decimal(str(self._fee_bps)) / _BPS
            p = candle_next.open  # E-006: ausschliesslich open + open_time der Folgekerze
            if order.side == "BUY":
                exec_price = money.tick_up(p * (_ONE + s), spec.tick_size)
            elif order.side == "SELL":
                exec_price = money.tick_down(p * (_ONE - s), spec.tick_size)
            else:
                raise ValueError(f"Unbekannte Seite {order.side!r}")

            if qty < spec.min_qty:
                return Rejection("MIN_QTY", f"Menge {qty} unter min_qty {spec.min_qty}")

            gross = exec_price * qty
            if gross < spec.min_notional:
                eff = spec.effective_min_notional(exec_price)
                return Rejection(
                    "MIN_NOTIONAL",
                    f"{gross} USDC unter min_notional {spec.min_notional} USDC; "
                    f"garantiert ab {eff} USDC",
                )

            fee = _fee_round_up(gross * f, spec.quote_precision)

            if order.side == "BUY":
                net = gross + fee
                if net > self._cash:
                    return Rejection(
                        "INSUFFICIENT_CASH", f"{net} USDC benötigt, {self._cash} USDC verfügbar"
                    )
                self._cash = self._cash - net
                self._book_buy(order.symbol, qty, exec_price)
            else:
                pos = self.position(order.symbol)
                if pos.qty <= 0:
                    return Rejection("NO_POSITION", f"Keine Position in {order.symbol}")
                if qty > pos.qty:
                    return Rejection("NO_POSITION", f"Verkauf {qty} > Bestand {pos.qty}")
                net = gross - fee
                self._cash = self._cash + net
                self._book_sell(order.symbol, qty, exec_price)

            return Fill(
                symbol=order.symbol, side=order.side, price=exec_price, qty=qty,
                gross_quote=gross, fee=fee, net_quote=net, cash_after=self._cash,
                candle_open_time=candle_next.open_time,
                fee_bps=self._fee_bps, slippage_bps=self._slippage_bps,
            )

    def _book_buy(self, symbol: str, qty: Decimal, price: Decimal) -> None:
        pos = self.position(symbol)
        new_qty = pos.qty + qty
        # mengengewichteter Durchschnittspreis (A-14)
        new_avg = (pos.qty * pos.avg_price + qty * price) / new_qty if new_qty > 0 else Decimal(0)
        self._positions[symbol] = Position(symbol, new_qty, new_avg, pos.realized_pnl)

    def _book_sell(self, symbol: str, qty: Decimal, price: Decimal) -> None:
        pos = self.position(symbol)
        realized = (price - pos.avg_price) * qty
        new_qty = pos.qty - qty
        new_avg = pos.avg_price if new_qty > 0 else Decimal(0)
        self._positions[symbol] = Position(symbol, new_qty, new_avg, pos.realized_pnl + realized)

    def mark(self, marks: Mapping[str, Decimal], ts_ms: int) -> Valuation:
        with decimal.localcontext(money.CTX):
            position_value = Decimal(0)
            values: dict[str, Decimal] = {}
            for symbol, pos in self._positions.items():
                if pos.qty == 0:
                    continue
                price = marks.get(symbol)
                if price is None:
                    continue
                value = pos.qty * price
                values[symbol] = value
                position_value += value
            equity = self._cash + position_value
            pct_by_symbol = {
                sym: float(value / equity * 100) if equity > 0 else 0.0
                for sym, value in values.items()
            }
            exposure_pct = float(position_value / equity * 100) if equity > 0 else 0.0
            return Valuation(
                ts_ms=ts_ms, cash=self._cash, position_value=position_value, equity=equity,
                exposure_pct=exposure_pct, position_pct_by_symbol=pct_by_symbol,
            )

    def to_portfolio_state(self, v: Valuation, start_of_day_equity: Decimal) -> risk.PortfolioState:
        # Zwischenstand vor Aufgabe 7: risk.PortfolioState kennt position_pct_by_symbol
        # noch nicht. Aufgabe 7 erweitert diesen Aufruf um genau dieses Feld.
        return risk.PortfolioState(
            equity=v.equity,
            start_of_day_equity=start_of_day_equity,
            exposure_pct=v.exposure_pct,
        )
```

- [ ] **Schritt 4: Test laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_ledger.py -q'
```

Erwartet: `12 passed`

- [ ] **Schritt 5: Rot-Nachweis führen**

*Fehler 1 (A-1):* In `apply()` beim BUY-Zweig `self._cash = self._cash - net` durch
`self._cash = self._cash - gross` ersetzen (die Gebühr fehlt beim Kassenabzug, steckt aber
weiterhin im zurückgegebenen `Fill.fee`). Test laufen lassen. Erwartet:
`test_buchhaltung_identitaet_a1` schlägt fehl mit
`AssertionError: assert Decimal('...') == Decimal('0')` (Differenz ungleich 0, in der
Größenordnung der aufsummierten Gebühren über mehrere Tausend Fills). Ausgabe zeigen,
Änderung zurücknehmen.

*Fehler 2 (A-3/A-4):* `money.step_down(order.base_qty, spec.step_size)` durch
`order.base_qty.quantize(spec.step_size, rounding=decimal.ROUND_HALF_UP)` ersetzen. Test laufen
lassen. Erwartet: `test_min_notional_an_der_grenze_a4` schlägt fehl, weil die Zielmenge jetzt
aufgerundet statt abgerundet wird und der erste (4,99-USDC-)Fall zu einem `Fill` statt einer
`Rejection` wird — `AssertionError: assert False` bei `isinstance(r, Rejection)`. Ausgabe zeigen,
Änderung zurücknehmen, beide Tests erneut grün.

- [ ] **Schritt 6: Commit**

```bash
git add app/aitra/ledger.py app/tests/test_ledger.py
git commit -m "feat(ledger): Order/Fill/Rejection/Position/Valuation, Ledger.apply()/mark()"
```

---

### Aufgabe 6: `sizing.py` — Prozent zu quantisierter Menge

**Macht grün:** A-3 (vollständig, 1.000 Stichproben), A-4b (vollständig, 500 Preise),
A-5 (vollständig, bezifferter Losgrößenverlust), A-8b (Beitrag: 0 Treffer in `sizing.py`).

**Dateien:**
- Neu: `app/aitra/sizing.py`
- Neu: `app/tests/test_sizing.py`

**Schnittstellen:**
- Nutzt: `money.SymbolSpec/tick_up/tick_down/step_down/CTX` (Aufgabe 1),
  `ledger.Order`, `ledger.Rejection`, `ledger.Valuation` (Aufgabe 5),
  `risk.Proposal(symbol, action, position_pct, confidence=None)` (Bestand)
- Stellt bereit:
  - `size_order(proposal: risk.Proposal, valuation: ledger.Valuation, spec: money.SymbolSpec,
    ref_price: Decimal, fee_bps: float, slippage_bps: float, held_qty: Decimal) -> ledger.Order | ledger.Rejection`

**Wichtig für Folgeaufgaben:** Die Funktion heißt **`size_order`**, nicht `size` — dieser Name
zieht sich unverändert durch `execute.py` (Aufgabe 8) und `replay.py` (Aufgabe 10). Wie
`ledger.py` ruft auch diese Datei **nie** `time.time()`, `datetime.now()/utcnow()` oder
`random.*` auf (A-8b) — `ref_price` kommt als Parameter (aus `candle.open` der aktuellen,
nicht der Folgekerze — siehe E-006/Aufgabe 8).

- [ ] **Schritt 1: Den scheiternden Test schreiben**

```python
# app/tests/test_sizing.py
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from aitra import money
from aitra.ledger import Ledger, Order, Rejection, Valuation
from aitra.marketdata import Candle
from aitra.risk import Proposal
from aitra.sizing import size_order

BTC = money.BUILTIN_SPECS["BTCUSDC"]
FEE_BPS = 10.0
SLIP_BPS = 5.0


def _valuation(equity: str = "10000", cash: str | None = None) -> Valuation:
    cash_dec = Decimal(cash) if cash is not None else Decimal(equity)
    return Valuation(ts_ms=0, cash=cash_dec, position_value=Decimal(0), equity=Decimal(equity),
                      exposure_pct=0.0, position_pct_by_symbol={})


def test_buy_liefert_quantisierte_order():
    p = Proposal("BTCUSDC", "BUY", position_pct=10)
    o = size_order(p, _valuation(), BTC, Decimal("81287.03"), FEE_BPS, SLIP_BPS, held_qty=Decimal(0))
    assert isinstance(o, Order)
    assert o.base_qty % BTC.step_size == 0


def test_sell_ohne_bestand_wird_abgelehnt():
    p = Proposal("BTCUSDC", "SELL", position_pct=5)
    r = size_order(p, _valuation(), BTC, Decimal("81287.03"), FEE_BPS, SLIP_BPS, held_qty=Decimal(0))
    assert isinstance(r, Rejection) and r.code == "NO_POSITION"


def test_sell_deckelt_auf_bestand():
    p = Proposal("BTCUSDC", "SELL", position_pct=100)  # will mehr, als gehalten wird
    o = size_order(p, _valuation(), BTC, Decimal("81287.03"), FEE_BPS, SLIP_BPS, held_qty=Decimal("0.01"))
    assert isinstance(o, Order)
    assert o.base_qty <= Decimal("0.01")


def test_insufficient_cash_bei_knapper_kasse():
    p = Proposal("BTCUSDC", "BUY", position_pct=10)
    r = size_order(p, _valuation(equity="10000", cash="1"), BTC, Decimal("81287.03"),
                    FEE_BPS, SLIP_BPS, held_qty=Decimal(0))
    assert isinstance(r, Rejection) and r.code == "INSUFFICIENT_CASH"


def test_a3_quantisierung_1000_stichproben():
    """A-3: 1.000 Ordergroessen von 6 bis 1.000 USDC ueber drei Groessenordnungen."""
    ledger = Ledger(starting_cash=Decimal("1000000"), specs={"BTCUSDC": BTC},
                     fee_bps=FEE_BPS, slippage_bps=SLIP_BPS)
    price = Decimal("81287.03")
    verletzungen_qty = verletzungen_tick = verletzungen_notional = 0
    for i in range(1000):
        target_usdc = Decimal(6) * (Decimal(1000) / Decimal(6)) ** (Decimal(i) / Decimal(999))
        pct = target_usdc / Decimal("1000000") * Decimal(100)
        p = Proposal("BTCUSDC", "BUY", position_pct=float(pct))
        v = _valuation(equity="1000000")
        result = size_order(p, v, BTC, price, FEE_BPS, SLIP_BPS, held_qty=Decimal(0))
        if isinstance(result, Rejection):
            continue
        if result.base_qty % BTC.step_size != 0:
            verletzungen_qty += 1
        candle_ts = 900_000 + i * 900_000
        candle = Candle(symbol="BTCUSDC", interval="15m", open_time=candle_ts,
                         close_time=candle_ts + 899_999, open=price, high=price, low=price,
                         close=price, volume=Decimal("1"), closed=True)
        fill = ledger.apply(result, candle)
        if isinstance(fill, Rejection):
            continue
        if fill.price % BTC.tick_size != 0:
            verletzungen_tick += 1
        if fill.gross_quote > target_usdc:
            verletzungen_notional += 1
    assert verletzungen_qty == 0
    assert verletzungen_tick == 0
    assert verletzungen_notional == 0


def test_a4b_effektive_mindestordergroesse_ist_real():
    """A-4b, gegen sizing.size_order() selbst (nicht gegen ledger.apply()):
    Ziel-Notional = min_notional + step*preis (K-3) wird in 500/500 Faellen zu einer
    Order, Ziel-Notional = min_notional + 0,5*step*preis in mindestens 200/500 Faellen
    zu einer Rejection mit der effektiven Grenze in der Begruendung.
    fee_bps=slippage_bps=0, damit exec_price exakt price ist und target_quote exakt
    dem gewuenschten Ziel-Notional entspricht (equity so gewaehlt, dass position_pct=1
    genau das Ziel ergibt)."""
    gefuellt_bei_eff = 0
    abgelehnt_bei_halb = 0
    ablehnungstexte = []
    for i in range(500):
        price = Decimal("60000") + Decimal(i) * (Decimal("60000") / Decimal(499))
        price = money.tick_down(price, BTC.tick_size)  # exec_price = tick_up(price) bleibt price
        eff = BTC.effective_min_notional(price)

        v_eff = _valuation(equity=str(eff * Decimal(100)))
        o_eff = size_order(Proposal("BTCUSDC", "BUY", position_pct=1), v_eff, BTC, price,
                            fee_bps=0.0, slippage_bps=0.0, held_qty=Decimal(0))
        if isinstance(o_eff, Order):
            gefuellt_bei_eff += 1

        halb_target = BTC.min_notional + Decimal("0.5") * BTC.step_size * price
        v_halb = _valuation(equity=str(halb_target * Decimal(100)))
        r_halb = size_order(Proposal("BTCUSDC", "BUY", position_pct=1), v_halb, BTC, price,
                             fee_bps=0.0, slippage_bps=0.0, held_qty=Decimal(0))
        if isinstance(r_halb, Rejection):
            abgelehnt_bei_halb += 1
            assert "garantiert ab" in r_halb.reason
            ablehnungstexte.append(r_halb.reason)

    assert gefuellt_bei_eff == 500
    assert abgelehnt_bei_halb >= 200
    assert len(ablehnungstexte) == abgelehnt_bei_halb


def test_a5_losgroessenverlust_ist_beziffert():
    """A-5: bei step=0.00001 und 1.000 Preisstufen um 81.287 USDC liegt der
    Rundungsrest einer 1.000-USDC-Order zwischen 0 und step*preis."""
    reste = []
    for i in range(1000):
        price = Decimal("80787") + Decimal(i) * Decimal("1")  # 1.000 Stufen um 81.287
        raw_qty = Decimal("1000") / price
        qty = money.step_down(raw_qty, BTC.step_size)
        rest = Decimal("1000") - qty * price
        reste.append(rest)
    assert max(reste) < Decimal("0.8129")
    assert max(reste) > Decimal("0.70")
    assert max(reste) / Decimal(1000) < Decimal("0.001")
    assert min(reste) >= Decimal("0")


def test_a8b_keine_versteckte_uhr_kein_versteckter_zufall():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "sizing.py").read_text()
    assert re.search(r"time\.time|datetime\.(now|utcnow)|random\.", text) is None
```

- [ ] **Schritt 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_sizing.py -q'
```

Erwartet: `ModuleNotFoundError: No module named 'aitra.sizing'`

- [ ] **Schritt 3: `sizing.py` schreiben**

```python
"""Positionsgroessenbestimmung: Prozent -> quantisierte Menge (Spec 6.2).

Rein (E-001/A-8b): kein Netz, keine DB, keine Uhr, kein Zufall. ref_price kommt
immer als Parameter vom Aufrufer (execute.py nimmt dafuer candle.open der aktuellen,
nicht der Folgekerze — siehe E-006).
"""
from __future__ import annotations

import decimal
from decimal import Decimal

from . import money
from .ledger import Order, Rejection, Valuation
from .risk import Proposal

_BPS = Decimal(10_000)
_ONE = Decimal(1)


def size_order(
    proposal: Proposal,
    valuation: Valuation,
    spec: money.SymbolSpec,
    ref_price: Decimal,
    fee_bps: float,
    slippage_bps: float,
    held_qty: Decimal,
) -> Order | Rejection:
    with decimal.localcontext(money.CTX):
        s = Decimal(str(slippage_bps)) / _BPS
        f = Decimal(str(fee_bps)) / _BPS
        target_quote = valuation.equity * Decimal(str(proposal.position_pct)) / Decimal(100)

        if proposal.action == "BUY":
            exec_price = money.tick_up(ref_price * (_ONE + s), spec.tick_size)
            raw_qty = target_quote / (exec_price * (_ONE + f))
        elif proposal.action == "SELL":
            if held_qty <= 0:
                return Rejection("NO_POSITION", f"Keine Position in {proposal.symbol}")
            exec_price = money.tick_down(ref_price * (_ONE - s), spec.tick_size)
            raw_qty = min(target_quote / exec_price, held_qty)
        else:
            raise ValueError(f"size_order erwartet BUY oder SELL, nicht {proposal.action!r}")

        qty = money.step_down(raw_qty, spec.step_size)
        if qty < spec.min_qty:
            return Rejection("MIN_QTY", f"Menge {qty} unter min_qty {spec.min_qty}")

        notional = qty * exec_price
        if notional < spec.min_notional:
            eff = spec.effective_min_notional(exec_price)
            return Rejection(
                "MIN_NOTIONAL",
                f"{notional} USDC unter min_notional {spec.min_notional} USDC; "
                f"garantiert ab {eff} USDC",
            )

        if proposal.action == "BUY":
            cost = notional * (_ONE + f)
            if cost > valuation.cash:
                return Rejection(
                    "INSUFFICIENT_CASH", f"{cost} USDC benötigt, {valuation.cash} USDC verfügbar"
                )

        return Order(symbol=proposal.symbol, side=proposal.action, base_qty=qty)
```

- [ ] **Schritt 4: Test laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_sizing.py -q'
```

Erwartet: `9 passed`

- [ ] **Schritt 5: Rot-Nachweis führen**

*Fehler 1 (A-3):* In `size_order` `money.step_down(raw_qty, spec.step_size)` durch
`raw_qty.quantize(spec.step_size, rounding=decimal.ROUND_HALF_UP)` ersetzen. Test laufen lassen.
Erwartet: `test_a3_quantisierung_1000_stichproben` schlägt fehl mit
`AssertionError: assert 0 == 0` wird zu `assert 512 == 0` (ungefähr die Hälfte der
Stichproben rundet jetzt auf und überschreitet die angeforderte Notional) — konkret:
`assert <verletzungen_notional> == 0` mit einem Wert deutlich über 0 (rund 500, wie im
Abnahmekriterium beschrieben). Ausgabe zeigen, Änderung zurücknehmen.

*Fehler 2 (A-4b):* In `size_order` die Bedingung `if notional < spec.min_notional:` durch
`if notional < spec.min_notional / Decimal(2):` ersetzen (die Grenze wird zu lasch). Test
laufen lassen. Erwartet: `test_a4b_effektive_mindestordergroesse_ist_real` schlägt fehl mit
`AssertionError: assert 0 >= 200` (`abgelehnt_bei_halb` fällt auf 0, weil jetzt auch die
absichtlich zu kleinen Zielgrößen durchgehen). Ausgabe zeigen, Änderung zurücknehmen.
Das zeigt zugleich, warum dieselbe Absicherung **zusätzlich** in `ledger.py` steht (Aufgabe 5,
`test_min_notional_an_der_grenze_a4`): Zwei unabhängige Schichten, die beide dieselbe Grenze
prüfen, sind beabsichtigte Redundanz, keine Doppelarbeit — sizing.py schützt vor unnötigen
Rejections zur Entscheidungszeit, ledger.py ist die letzte, unumgehbare Instanz beim Fill.

- [ ] **Schritt 6: Commit**

```bash
git add app/aitra/sizing.py app/tests/test_sizing.py
git commit -m "feat(sizing): size_order() – Prozent zu quantisierter Menge inkl. K-3-Absicherung"
```

---

### Aufgabe 7: `risk.py`-Korrektur — Befund B-1, `position_pct_by_symbol`

**Macht grün:** A-6c (vollständig, drei Messpunkte + Gegenprobe `test_no_short`).

**Dateien:**
- Geändert: `app/aitra/risk.py`
- Geändert: `app/aitra/ledger.py` (nur `Ledger.to_portfolio_state`, siehe unten)
- Geändert: `app/tests/test_risk.py` (Tests angehängt, Bestand bleibt unverändert erhalten)
- Geändert: `app/tests/test_ledger.py` (ein Test angehängt)

**Schnittstellen:**
- Nutzt: nichts Neues aus vorherigen Aufgaben (nutzt weiterhin nur `config.Config`)
- Stellt bereit:
  - `PortfolioState` mit **einem neuen Feld hinten mit Vorgabewert**:
    `position_pct_by_symbol: Mapping[str, float] = field(default_factory=dict)`
  - `RiskEngine.check()` — Signatur unverändert, SELL-Prüfung intern korrigiert
  - `RiskEngine.daily_loss_pct(pf) -> float` — Signatur unverändert, verträgt jetzt sowohl
    `Decimal`- als auch `float`/`int`-wertige `equity`/`start_of_day_equity` (Bestandstests
    übergeben `float`, `Ledger.to_portfolio_state` übergibt `Decimal`)

**Der Grund für den Eingriff in `ledger.py`:** In Aufgabe 5 lieferte
`Ledger.to_portfolio_state()` noch kein `position_pct_by_symbol`, weil das Feld in
`risk.PortfolioState` nicht existierte. Jetzt, wo es existiert, wird genau diese eine
Rückgabe ergänzt — sonst bliebe die Brücke aus Spec 4.2 unvollständig und Aufgabe 8
(`execute.py`) bekäme über `to_portfolio_state()` immer ein leeres `{}`, egal was das Ledger
tatsächlich hält, und A-6c würde im echten Betrieb nie greifen, obwohl der Test dafür grün ist.

**Warum `equity`/`start_of_day_equity` beides vertragen müssen:** `test_risk.py` konstruiert
`PortfolioState` bis heute mit `float`-Werten (`equity=97.9`); `Ledger.to_portfolio_state()`
liefert ab jetzt `Decimal`. `Decimal(...) >= float(...)` wirft `TypeError` — deshalb normalisiert
`daily_loss_pct()` beide Operanden über `Decimal(str(x))` und gibt am Ende `float` zurück
(der Vergleich mit `cfg.max_daily_loss_pct` bleibt `float` gegen `float`, wie im Bestand).

- [ ] **Schritt 1: Die scheiternden Tests anhängen**

An `app/tests/test_risk.py` anhängen (der bestehende Inhalt bleibt unverändert stehen):

```python
# an app/tests/test_risk.py anhängen


def test_a6c_sell_prueft_gegen_die_position_nicht_gegen_die_gesamtexposition(engine):
    """B-1: SELL BTCUSDC ohne BTC-Position muss scheitern, auch wenn ETH genug
    Gesamtexposition traegt. Vor der Korrektur ist dieser Test absichtlich rot."""
    pf_ohne_btc = PortfolioState(
        equity=10000, start_of_day_equity=10000, exposure_pct=20,
        position_pct_by_symbol={"ETHUSDC": 20.0},
    )
    r1 = engine.check(Proposal("BTCUSDC", "SELL", 5), pf_ohne_btc, False)
    assert not r1.approved and r1.code == "NO_POSITION"

    pf_mit_btc = PortfolioState(
        equity=10000, start_of_day_equity=10000, exposure_pct=28,
        position_pct_by_symbol={"BTCUSDC": 8.0, "ETHUSDC": 20.0},
    )
    r2 = engine.check(Proposal("BTCUSDC", "SELL", 5), pf_mit_btc, False)
    assert r2.approved

    r3 = engine.check(Proposal("BTCUSDC", "SELL", 12), pf_mit_btc, False)
    assert not r3.approved and r3.code == "NO_POSITION"


def test_no_short_bleibt_gruen_nach_der_korrektur(engine):
    # Gegenprobe aus A-6c: flaches Portfolio, SELL 5 % -> weiterhin NO_POSITION
    assert engine.check(Proposal("BTCUSDC", "SELL", 5), FLAT, False).code == "NO_POSITION"


def test_daily_loss_vertraegt_decimal_equity(engine):
    from decimal import Decimal
    pf = PortfolioState(equity=Decimal("9790"), start_of_day_equity=Decimal("10000"), exposure_pct=0)
    r = engine.check(Proposal("BTCUSDC", "BUY", 5), pf, False)
    assert not r.approved and r.code == "DAILY_LOSS"
```

An `app/tests/test_ledger.py` anhängen:

```python
# an app/tests/test_ledger.py anhängen


def test_to_portfolio_state_gibt_position_pct_by_symbol_weiter():
    ledger = _ledger()
    ledger.apply(Order("BTCUSDC", "BUY", Decimal("0.05")), _candle("81287.03"))
    v = ledger.mark({"BTCUSDC": Decimal("82000")}, ts_ms=1_800_000)
    pf = ledger.to_portfolio_state(v, start_of_day_equity=Decimal("10000"))
    assert pf.position_pct_by_symbol == v.position_pct_by_symbol
    assert pf.position_pct_by_symbol["BTCUSDC"] > 0
```

- [ ] **Schritt 2: Tests laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_risk.py tests/test_ledger.py -q'
```

Erwartet: `test_a6c_...` schlägt fehl bei `r1` — `AssertionError: assert not True` (heute wird
der SELL genehmigt, weil `pf.exposure_pct=20 >= 5` gilt, obwohl keine BTC-Position existiert;
genau das ist Befund B-1). `test_daily_loss_vertraegt_decimal_equity` schlägt fehl mit
`TypeError: unsupported operand type(s) for -: 'decimal.Decimal' and ...` bzw. beim Vergleich
mit `cfg.max_daily_loss_pct`. `test_to_portfolio_state_gibt_position_pct_by_symbol_weiter`
schlägt fehl mit `TypeError: PortfolioState.__init__() got an unexpected keyword argument
'position_pct_by_symbol'`.

- [ ] **Schritt 3: `risk.py` ändern**

```python
"""Harte Risiko-Engine.

Deterministisch und bewusst getrennt von jeder Strategie- oder KI-Logik:
Eine Strategie darf nur *vorschlagen*, diese Engine entscheidet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping

from .config import Config

SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,12}(USDC|USDT|EUR|BTC)$")
SPOT_ACTIONS = {"BUY", "SELL", "WAIT"}


@dataclass(frozen=True)
class Proposal:
    symbol: str
    action: str               # BUY | SELL | WAIT
    position_pct: float = 0   # Anteil am Portfolio in %
    confidence: float | None = None


@dataclass(frozen=True)
class PortfolioState:
    equity: float
    start_of_day_equity: float
    exposure_pct: float       # aktuell investierter Anteil in %
    position_pct_by_symbol: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    code: str
    reason: str


class RiskEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def daily_loss_pct(self, pf: PortfolioState) -> float:
        # equity/start_of_day_equity koennen float (Bestandstests) oder Decimal
        # (Ledger.to_portfolio_state) sein. Decimal(str(x)) normalisiert beides,
        # ohne ueber float neu zu erzeugen, was aus einem echten Decimal-Geldwert
        # Praezision herausschneiden wuerde.
        sod = Decimal(str(pf.start_of_day_equity))
        eq = Decimal(str(pf.equity))
        if sod <= 0:
            return 0.0
        return float(max(Decimal(0), (sod - eq) / sod * Decimal(100)))

    def check(self, p: Proposal, pf: PortfolioState, kill_switch: bool) -> RiskDecision:
        action = p.action.upper()

        if action not in SPOT_ACTIONS:
            return RiskDecision(False, "NOT_SPOT", f"Aktion {p.action!r} nicht erlaubt – nur Spot BUY/SELL/WAIT")
        if not SYMBOL_RE.match(p.symbol):
            return RiskDecision(False, "BAD_SYMBOL", f"Symbol {p.symbol!r} ungültig")
        if action == "WAIT":
            return RiskDecision(True, "NO_ORDER", "Keine Order – Entscheidung wird nur protokolliert")

        if kill_switch:
            return RiskDecision(False, "KILL_SWITCH", "Kill Switch aktiv – neue Orders blockiert")
        if self.cfg.trading_mode != "PAPER" or not self.cfg.live_locked:
            return RiskDecision(False, "MODE", "Nur Paper-Trading erlaubt")

        loss = self.daily_loss_pct(pf)
        if loss >= self.cfg.max_daily_loss_pct:
            return RiskDecision(
                False, "DAILY_LOSS",
                f"Tagesverlust {loss:.2f} % ≥ Limit {self.cfg.max_daily_loss_pct:g} %",
            )

        if not (0 < p.position_pct <= 100):
            return RiskDecision(False, "BAD_SIZE", "Positionsgröße muss zwischen 0 und 100 % liegen")
        if p.position_pct > self.cfg.max_position_pct:
            return RiskDecision(
                False, "MAX_POSITION",
                f"Position {p.position_pct:g} % > Limit {self.cfg.max_position_pct:g} %",
            )
        if action == "BUY" and pf.exposure_pct + p.position_pct > self.cfg.max_total_exposure_pct:
            return RiskDecision(
                False, "MAX_EXPOSURE",
                f"Gesamtrisiko {pf.exposure_pct + p.position_pct:g} % > Limit {self.cfg.max_total_exposure_pct:g} %",
            )
        if action == "SELL":
            # Befund B-1: gegen die Position IM SYMBOL pruefen, nicht gegen die
            # Gesamtexposition. pf.exposure_pct waere hier falsch: ein Verkauf in
            # BTCUSDC darf nicht durchgehen, nur weil ETHUSDC genug Gesamtrisiko traegt.
            held_pct = pf.position_pct_by_symbol.get(p.symbol, 0.0)
            if p.position_pct > held_pct:
                return RiskDecision(False, "NO_POSITION", "Verkauf größer als vorhandene Position (kein Short)")

        return RiskDecision(True, "OK", "Alle Risikoregeln erfüllt")
```

- [ ] **Schritt 4: `ledger.py` — `to_portfolio_state` erweitern**

In `app/aitra/ledger.py` (aus Aufgabe 5) den Rückgabewert von `to_portfolio_state` ändern:

```python
    def to_portfolio_state(self, v: Valuation, start_of_day_equity: Decimal) -> risk.PortfolioState:
        return risk.PortfolioState(
            equity=v.equity,
            start_of_day_equity=start_of_day_equity,
            exposure_pct=v.exposure_pct,
            position_pct_by_symbol=v.position_pct_by_symbol,
        )
```

(Nur der Funktionskörper ändert sich; der Kommentar aus Aufgabe 5 über den „Zwischenstand"
entfällt.)

- [ ] **Schritt 5: Tests laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_risk.py tests/test_ledger.py -q'
```

Erwartet: `test_risk.py` **14 passed** (11 Bestand + 3 neu), `test_ledger.py` **13 passed**
(12 aus Aufgabe 5 + 1 neu).

- [ ] **Schritt 6: Bestandstests laufen lassen (Befund B-5 darf nicht neu auftreten)**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_api.py -q'
```

Erwartet: unverändert **5 passed** (`test_api.py` konstruiert `Config` positional und
`PortfolioState` nicht direkt — `web.py` baut es intern, unverändert in Plan A1).

- [ ] **Schritt 7: Rot-Nachweis führen**

Die alte, fehlerhafte Zeile wiederherstellen:

```python
        if action == "SELL" and p.position_pct > pf.exposure_pct:
            return RiskDecision(False, "NO_POSITION", "Verkauf größer als vorhandene Position (kein Short)")
```

(anstelle des `held_pct`-Blocks). Test laufen lassen. Erwartet:
`test_a6c_sell_prueft_gegen_die_position_nicht_gegen_die_gesamtexposition` schlägt fehl bei der
ersten Zusicherung mit `AssertionError: assert not True` — der Verkauf ohne jede BTC-Position
wird genehmigt, weil `pf.exposure_pct=20` über `p.position_pct=5` liegt. Ausgabe zeigen,
Korrektur zurücknehmen, Test erneut grün.

*Anmerkung wie in Spec Abschnitt 12 vermerkt:* Dieser Test ist vor der Korrektur bewusst rot —
er benennt echte Schuld im Bestand (Befund B-1), nicht einen künstlich eingebauten Fehler.

- [ ] **Schritt 8: Commit**

```bash
git add app/aitra/risk.py app/aitra/ledger.py app/tests/test_risk.py app/tests/test_ledger.py
git commit -m "fix(risk): B-1 – SELL prüft gegen die Position im Symbol, nicht gegen die Gesamtexposition"
```

---

### Aufgabe 8: `execute.py` — das Nadelöhr, schwebende Vorschläge, Verfall

**Macht grün:** A-6, A-6b, A-7b, A-14.

**Dateien:**
- Neu: `app/aitra/execute.py`
- Neu: `app/tests/test_execute.py`

**Schnittstellen:**
- Nutzt: `risk.Proposal/PortfolioState/RiskEngine/RiskDecision` (Bestand + Aufgabe 7),
  `ledger.Ledger/Order/Fill/Rejection` (Aufgabe 5), `sizing.size_order` (Aufgabe 6),
  `marketdata.Candle/Clock` (Aufgabe 4), `store.mark_decision_pending/resolve_decision/
  expire_decision/get_pending_decisions/insert_fill/upsert_position` (Aufgabe 3),
  `db.add_decision/now` (Bestand), `money.SymbolSpec` (Aufgabe 1)
- Stellt bereit:
  - `ExecutionContext(conn, run_id, ledger, engine, specs, fee_bps, slippage_bps, clock,
    kill_switch=False, pending_expiry_ms=1_800_000)` (Dataclass, **nicht** frozen —
    `kill_switch` wird vom Aufrufer je nach Betriebsart aktualisiert)
  - `ExecutionResult(decision_id, approved, code, reason, status, fill=None)` (frozen dataclass;
    `status` ∈ `{"filled", "rejected", "pending_fill", "no_order"}`)
  - `execute_proposal(proposal: Proposal, ctx: ExecutionContext, *, marks: Mapping[str, Decimal],
    ts_ms: int, ref_price: Decimal, start_of_day_equity: Decimal, strategy_version: str = "manual",
    reason: str = "", next_candle: Candle | None = None) -> ExecutionResult`
    **die einzige Funktion im gesamten Paket, die `Ledger.apply()` aufruft (E-003, A-6b)**
  - `resolve_pending(ctx: ExecutionContext, candle: Candle) -> list[Fill]`
  - `expire_stale_pending(ctx: ExecutionContext) -> list[int]` (Rückgabe: verfallene `decision_id`s)

**Wichtig für Folgeaufgaben:** `replay.py` (Aufgabe 10) ruft **ausschließlich**
`execute.execute_proposal(..., next_candle=candles[t])` auf, **nie** `ledger.apply()` direkt —
sonst verletzt Aufgabe 10 A-6b. Da im Replay die Folgekerze bereits bekannt ist, füllt
`execute_proposal()` dort sofort (kein `pending_fill`); live (`next_candle=None`) legt sie den
Vorschlag schwebend ab. `ref_price` ist der Preis, zu dem `sizing.size_order()` die Zielmenge
berechnet — das ist `candle.open` der **aktuellen** (Entscheidungs-)Kerze, nicht der Folgekerze
(E-006); die tatsächliche Ausführung passiert unabhängig davon in `Ledger.apply()` zum `open`
der Folgekerze.

- [ ] **Schritt 1: Den scheiternden Test schreiben**

```python
# app/tests/test_execute.py
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from aitra import db, money, store
from aitra.config import Config
from aitra.execute import ExecutionContext, execute_proposal, expire_stale_pending, resolve_pending
from aitra.ledger import Ledger
from aitra.marketdata import Candle, SimClock
from aitra.risk import Proposal, RiskEngine

BTC = money.BUILTIN_SPECS["BTCUSDC"]
SPECS = {"BTCUSDC": BTC, "ETHUSDC": money.BUILTIN_SPECS["ETHUSDC"]}


def _ctx(tmp_path: Path, clock_ms: int = 900_000, kill_switch: bool = False) -> ExecutionContext:
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    store.create_run(conn, "run-1", "replay", "2026-01-01T00:00:00Z", "0.3.0")
    ledger = Ledger(starting_cash=Decimal("10000"), specs=SPECS, fee_bps=10.0, slippage_bps=5.0)
    cfg = Config(Decimal("10000"), 10, 2, 50, tmp_path, "x" * 32)
    engine = RiskEngine(cfg)
    return ExecutionContext(conn=conn, run_id="run-1", ledger=ledger, engine=engine, specs=SPECS,
                             fee_bps=10.0, slippage_bps=5.0, clock=SimClock(clock_ms),
                             kill_switch=kill_switch)


def _candle(open_time: int, price: str = "81287.03") -> Candle:
    return Candle(symbol="BTCUSDC", interval="15m", open_time=open_time, close_time=open_time + 899_999,
                  open=Decimal(price), high=Decimal(price), low=Decimal(price), close=Decimal(price),
                  volume=Decimal("1"), closed=True)


@pytest.mark.parametrize("proposal,kill_switch,expected_code", [
    (Proposal("BTCUSDC", "SHORT", 5), False, "NOT_SPOT"),
    (Proposal("btc/usdc", "BUY", 5), False, "BAD_SYMBOL"),
    (Proposal("BTCUSDC", "BUY", 5), True, "KILL_SWITCH"),
    (Proposal("BTCUSDC", "BUY", 200), False, "BAD_SIZE"),
    (Proposal("BTCUSDC", "BUY", 15), False, "MAX_POSITION"),
    (Proposal("BTCUSDC", "SELL", 5), False, "NO_POSITION"),
])
def test_a6_abgelehnte_vorschlaege_erzeugen_null_fills(tmp_path, proposal, kill_switch, expected_code):
    ctx = _ctx(tmp_path, kill_switch=kill_switch)
    result = execute_proposal(proposal, ctx, marks={}, ts_ms=900_000, ref_price=Decimal("81287.03"),
                               start_of_day_equity=Decimal("10000"))
    assert result.code == expected_code
    assert result.status == "rejected"
    assert ctx.conn.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"] == 1
    assert ctx.conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 0


def test_a6_daily_loss_und_mode_abdeckung(tmp_path):
    ctx = _ctx(tmp_path)
    # DAILY_LOSS: start_of_day_equity hoch, equity (aus marks) tief -> Verlust > 2%
    result = execute_proposal(
        Proposal("BTCUSDC", "BUY", 5), ctx, marks={}, ts_ms=900_000, ref_price=Decimal("81287.03"),
        start_of_day_equity=Decimal("10500"),
    )
    assert result.code == "DAILY_LOSS"
    assert ctx.conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 0

    cfg_live = Config(Decimal("10000"), 10, 2, 50, tmp_path, "x" * 32, trading_mode="LIVE")
    ctx.engine = RiskEngine(cfg_live)
    result2 = execute_proposal(
        Proposal("BTCUSDC", "BUY", 5), ctx, marks={}, ts_ms=900_000, ref_price=Decimal("81287.03"),
        start_of_day_equity=Decimal("10000"),
    )
    assert result2.code == "MODE"
    assert ctx.conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 0


def test_a6_max_exposure_neunter_ablehnungscode(tmp_path):
    """Deckt den neunten und letzten Ablehnungscode aus risk.py ab (9/9, A-6)."""
    ctx = _ctx(tmp_path)
    # Aufbau einer grossen ETH-Position braucht vorübergehend ein grosszuegiges Limit,
    # sonst greift schon MAX_POSITION statt MAX_EXPOSURE.
    ctx.engine = RiskEngine(Config(Decimal("10000"), 90, 2, 90, tmp_path, "x" * 32))
    eth_candle = Candle(symbol="ETHUSDC", interval="15m", open_time=1_800_000, close_time=2_699_999,
                         open=Decimal("2631.77"), high=Decimal("2631.77"), low=Decimal("2631.77"),
                         close=Decimal("2631.77"), volume=Decimal("1"), closed=True)
    r0 = execute_proposal(Proposal("ETHUSDC", "BUY", 45), ctx, marks={}, ts_ms=900_000,
                           ref_price=Decimal("2631.77"), start_of_day_equity=Decimal("10000"),
                           next_candle=eth_candle)
    assert r0.status == "filled"

    # Zurueck zur Standardkonfiguration (max_total_exposure_pct=50) fuer die eigentliche Messung
    ctx.engine = RiskEngine(Config(Decimal("10000"), 10, 2, 50, tmp_path, "x" * 32))
    r1 = execute_proposal(Proposal("BTCUSDC", "BUY", 8), ctx, marks={"ETHUSDC": Decimal("2631.77")},
                           ts_ms=2_700_000, ref_price=Decimal("81287.03"),
                           start_of_day_equity=Decimal("10000"))
    assert r1.code == "MAX_EXPOSURE"
    assert ctx.conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 1  # nur der ETH-Fill


def test_a6b_ledger_apply_hat_genau_einen_aufrufer():
    import subprocess
    aitra_dir = Path(__file__).resolve().parent.parent / "aitra"
    out = subprocess.run(
        ["grep", "-rln", r"\.apply(", str(aitra_dir)], capture_output=True, text=True
    ).stdout.splitlines()
    andere = [line for line in out if not line.endswith("execute.py")]
    assert andere == []


def test_execute_proposal_ohne_folgekerze_ist_pending(tmp_path):
    ctx = _ctx(tmp_path)
    result = execute_proposal(
        Proposal("BTCUSDC", "BUY", 8), ctx, marks={}, ts_ms=900_000, ref_price=Decimal("81287.03"),
        start_of_day_equity=Decimal("10000"), next_candle=None,
    )
    assert result.status == "pending_fill"
    assert result.approved
    assert ctx.conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 0
    row = ctx.conn.execute("SELECT pending_since_ms FROM decisions WHERE id=?", (result.decision_id,)).fetchone()
    assert row["pending_since_ms"] == 900_000


def test_execute_proposal_mit_folgekerze_fuellt_sofort(tmp_path):
    ctx = _ctx(tmp_path)
    candle = _candle(1_800_000)
    result = execute_proposal(
        Proposal("BTCUSDC", "BUY", 8), ctx, marks={}, ts_ms=900_000, ref_price=Decimal("81287.03"),
        start_of_day_equity=Decimal("10000"), next_candle=candle,
    )
    assert result.status == "filled"
    assert result.fill.candle_open_time == 1_800_000
    assert ctx.conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 1


def test_a7b_resolve_pending_fuellt_bei_ankunft_der_folgekerze(tmp_path):
    ctx = _ctx(tmp_path)
    pending = execute_proposal(
        Proposal("BTCUSDC", "BUY", 8), ctx, marks={}, ts_ms=900_000, ref_price=Decimal("81287.03"),
        start_of_day_equity=Decimal("10000"), next_candle=None,
    )
    assert pending.status == "pending_fill"
    fills = resolve_pending(ctx, _candle(1_800_000))
    assert len(fills) == 1
    assert fills[0].candle_open_time == 1_800_000
    assert ctx.conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 1
    row = ctx.conn.execute("SELECT risk_code, fill_id, pending_since_ms FROM decisions WHERE id=?",
                            (pending.decision_id,)).fetchone()
    assert row["fill_id"] is not None
    assert row["pending_since_ms"] is None


def test_a7b_verfall_an_der_grenze_1799_vs_1801_sekunden(tmp_path):
    ctx = _ctx(tmp_path, clock_ms=0)
    pending = execute_proposal(
        Proposal("BTCUSDC", "BUY", 8), ctx, marks={}, ts_ms=0, ref_price=Decimal("81287.03"),
        start_of_day_equity=Decimal("10000"), next_candle=None,
    )
    ctx.clock.set(1_799_000)
    expired = expire_stale_pending(ctx)
    assert expired == []
    row = ctx.conn.execute("SELECT pending_since_ms FROM decisions WHERE id=?", (pending.decision_id,)).fetchone()
    assert row["pending_since_ms"] is not None  # schwebt noch

    ctx.clock.set(1_801_000)
    expired = expire_stale_pending(ctx)
    assert expired == [pending.decision_id]
    row = ctx.conn.execute("SELECT risk_code, pending_since_ms FROM decisions WHERE id=?",
                            (pending.decision_id,)).fetchone()
    assert row["risk_code"] == "PENDING_EXPIRED"
    assert row["pending_since_ms"] is None
    assert ctx.conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 0


def test_a14_rekonstruktion_aus_dem_journal_1000_fills(tmp_path):
    """A-14: Kasse und Position lassen sich allein aus den fills-Zeilen rekonstruieren,
    ohne jede Kerze — die Differenz zum positions-Schnappschuss und zur cash_after der
    letzten Zeile muss exakt 0 sein, bei 1.000 von 1.000 Fills."""
    ctx = _ctx(tmp_path)
    ctx.engine = RiskEngine(Config(Decimal("10000"), 100, 100, 100, tmp_path, "x" * 32))
    price = Decimal("81287.03")
    erfolgreiche_fills = 0
    i = 0
    while erfolgreiche_fills < 1000:
        held = ctx.ledger.position("BTCUSDC").qty
        side = "BUY" if (i % 3 != 2 or held == 0) else "SELL"
        fill_price = price + Decimal(i % 40) * Decimal("0.01")
        candle = Candle(symbol="BTCUSDC", interval="15m", open_time=(i + 1) * 900_000,
                         close_time=(i + 1) * 900_000 + 899_999, open=fill_price, high=fill_price,
                         low=fill_price, close=fill_price, volume=Decimal("1"), closed=True)
        result = execute_proposal(
            Proposal("BTCUSDC", side, position_pct=2), ctx, marks={"BTCUSDC": price},
            ts_ms=i * 900_000, ref_price=price, start_of_day_equity=Decimal("10000"),
            next_candle=candle,
        )
        if result.status == "filled":
            erfolgreiche_fills += 1
        i += 1
        assert i <= 20_000, "zu viele Versuche ohne 1.000 Fills - Testaufbau pruefen"

    fills = store.get_fills(ctx.conn, "run-1")
    assert len(fills) == 1000

    cash = Decimal("10000")
    qty = Decimal("0")
    avg_price = Decimal("0")
    for f in fills:
        if f["side"] == "BUY":
            cash -= f["net_quote"]
            new_qty = qty + f["qty"]
            # mengengewichtete Fortschreibung (A-14) — dieselbe Formel wie Ledger._book_buy
            avg_price = (qty * avg_price + f["qty"] * f["price"]) / new_qty if new_qty > 0 else Decimal(0)
            qty = new_qty
        else:
            cash += f["net_quote"]
            qty -= f["qty"]

    positions = store.get_positions(ctx.conn, "run-1")
    assert qty - positions["BTCUSDC"]["qty"] == Decimal("0")
    assert avg_price - positions["BTCUSDC"]["avg_price"] == Decimal("0")
    assert cash - fills[-1]["cash_after"] == Decimal("0")
```

- [ ] **Schritt 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_execute.py -q'
```

Erwartet: `ModuleNotFoundError: No module named 'aitra.execute'`

- [ ] **Schritt 3: `execute.py` schreiben**

```python
"""execute_proposal(): das Nadeloehr (E-003).

Die einzige Funktion im Paket, die Ledger.apply() aufruft (A-6b) — verbindet
RiskEngine.check() -> sizing.size_order() -> Ledger.apply() -> Journal (decisions/fills).
Verwaltet zusaetzlich schwebende Vorschlaege (E-006): Ohne next_candle wird der
Vorschlag als pending_fill abgelegt, bis resolve_pending() eine Folgekerze liefert
oder expire_stale_pending() ihn nach pending_expiry_ms verwerfen laesst.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from . import db, money, store
from .ledger import Fill, Ledger, Rejection
from .marketdata import Candle, Clock
from .risk import Proposal, RiskEngine
from .sizing import size_order

PENDING_EXPIRY_MS_DEFAULT = 1_800_000  # 2 * interval_s bei 15m (E-006)


@dataclass
class ExecutionContext:
    conn: sqlite3.Connection
    run_id: str
    ledger: Ledger
    engine: RiskEngine
    specs: Mapping[str, money.SymbolSpec]
    fee_bps: float
    slippage_bps: float
    clock: Clock
    kill_switch: bool = False
    pending_expiry_ms: int = PENDING_EXPIRY_MS_DEFAULT


@dataclass(frozen=True)
class ExecutionResult:
    decision_id: int
    approved: bool
    code: str
    reason: str
    status: str  # "filled" | "rejected" | "pending_fill" | "no_order"
    fill: Fill | None = None


def _journal_fill(ctx: ExecutionContext, decision_id: int, fill: Fill) -> None:
    fill_id = store.insert_fill(
        ctx.conn, run_id=ctx.run_id, decision_id=decision_id, symbol=fill.symbol, side=fill.side,
        candle_open_time=fill.candle_open_time, price=fill.price, qty=fill.qty,
        gross_quote=fill.gross_quote, fee=fill.fee, net_quote=fill.net_quote,
        cash_after=fill.cash_after, fee_bps=fill.fee_bps, slippage_bps=fill.slippage_bps,
        ts=db.now(),
    )
    store.resolve_decision(ctx.conn, decision_id, fill_id)
    pos = ctx.ledger.position(fill.symbol)
    store.upsert_position(ctx.conn, run_id=ctx.run_id, symbol=fill.symbol, qty=pos.qty,
                           avg_price=pos.avg_price, realized_pnl=pos.realized_pnl, updated_at=db.now())


def execute_proposal(
    proposal: Proposal,
    ctx: ExecutionContext,
    *,
    marks: Mapping[str, Decimal],
    ts_ms: int,
    ref_price: Decimal,
    start_of_day_equity: Decimal,
    strategy_version: str = "manual",
    reason: str = "",
    next_candle: Candle | None = None,
) -> ExecutionResult:
    valuation = ctx.ledger.mark(marks, ts_ms)
    pf = ctx.ledger.to_portfolio_state(valuation, start_of_day_equity)
    decision = ctx.engine.check(proposal, pf, ctx.kill_switch)

    decision_id = db.add_decision(
        ctx.conn, strategy_version=strategy_version, symbol=proposal.symbol,
        action=proposal.action, confidence=proposal.confidence, reason=reason,
        requested_position_pct=proposal.position_pct, approved=int(decision.approved),
        risk_code=decision.code, risk_reason=decision.reason,
    )
    ctx.conn.execute("UPDATE decisions SET run_id = ? WHERE id = ?", (ctx.run_id, decision_id))
    ctx.conn.commit()

    if proposal.action.upper() == "WAIT":
        return ExecutionResult(decision_id, decision.approved, decision.code, decision.reason, status="no_order")
    if not decision.approved:
        return ExecutionResult(decision_id, False, decision.code, decision.reason, status="rejected")

    spec = ctx.specs.get(proposal.symbol)
    if spec is None:
        return ExecutionResult(decision_id, False, "NO_SPEC", f"Keine SymbolSpec für {proposal.symbol}", status="rejected")

    held = ctx.ledger.position(proposal.symbol).qty
    order = size_order(proposal, valuation, spec, ref_price, ctx.fee_bps, ctx.slippage_bps, held)
    if isinstance(order, Rejection):
        return ExecutionResult(decision_id, False, order.code, order.reason, status="rejected")

    if next_candle is None:
        # E-006, live: Folgekerze liegt noch nicht vor -> schwebend
        store.mark_decision_pending(ctx.conn, decision_id, pending_since_ms=ts_ms)
        return ExecutionResult(decision_id, True, "OK", "Order schwebt bis zur Folgekerze", status="pending_fill")

    fill = ctx.ledger.apply(order, next_candle)
    if isinstance(fill, Rejection):
        return ExecutionResult(decision_id, False, fill.code, fill.reason, status="rejected")

    _journal_fill(ctx, decision_id, fill)
    return ExecutionResult(decision_id, True, "OK", "Gefüllt", status="filled", fill=fill)


def resolve_pending(ctx: ExecutionContext, candle: Candle) -> list[Fill]:
    """Fuellt schwebende Vorschlaege fuer candle.symbol mit der nun vorliegenden Kerze."""
    expire_stale_pending(ctx)
    filled: list[Fill] = []
    for row in store.get_pending_decisions(ctx.conn, ctx.run_id):
        if row["symbol"] != candle.symbol:
            continue
        spec = ctx.specs.get(row["symbol"])
        if spec is None:
            store.expire_decision(ctx.conn, row["id"])
            continue
        held = ctx.ledger.position(row["symbol"]).qty
        proposal = Proposal(symbol=row["symbol"], action=row["action"],
                             position_pct=row["requested_position_pct"] or 0.0)
        valuation = ctx.ledger.mark({row["symbol"]: candle.open}, ts_ms=ctx.clock.now_ms())
        order = size_order(proposal, valuation, spec, candle.open, ctx.fee_bps, ctx.slippage_bps, held)
        if isinstance(order, Rejection):
            store.expire_decision(ctx.conn, row["id"])
            continue
        fill = ctx.ledger.apply(order, candle)
        if isinstance(fill, Rejection):
            store.expire_decision(ctx.conn, row["id"])
            continue
        _journal_fill(ctx, row["id"], fill)
        filled.append(fill)
    return filled


def expire_stale_pending(ctx: ExecutionContext) -> list[int]:
    """Laesst schwebende Vorschlaege verfallen, die laenger als pending_expiry_ms
    schweben — unabhaengig davon, ob je eine passende Kerze eintrifft (E-006)."""
    now = ctx.clock.now_ms()
    expired: list[int] = []
    for row in store.get_pending_decisions(ctx.conn, ctx.run_id):
        if now - row["pending_since_ms"] > ctx.pending_expiry_ms:
            store.expire_decision(ctx.conn, row["id"])
            expired.append(row["id"])
    return expired
```

- [ ] **Schritt 4: Test laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_execute.py -q'
```

Erwartet: `14 passed` (6 parametrisierte Fälle aus `test_a6_abgelehnte_vorschlaege_erzeugen_null_fills`
+ 8 weitere Testfunktionen, darunter `test_a14_rekonstruktion_aus_dem_journal_1000_fills`).

- [ ] **Schritt 5: Rot-Nachweis führen**

*Fehler 1 (A-6):* In `execute_proposal` unmittelbar nach der Journal-Zeile testweise
`if next_candle is not None: ctx.ledger.apply(size_order(proposal, valuation, ctx.specs.get(proposal.symbol), ref_price, ctx.fee_bps, ctx.slippage_bps, ctx.ledger.position(proposal.symbol).qty), next_candle)`
einfügen — **vor** der `if not decision.approved:`-Prüfung, unabhängig vom Ergebnis der
Risk-Engine. Test laufen lassen. Erwartet: Bei den Fällen mit gültigem Symbol und gültiger
Aktion (`KILL_SWITCH`, `BAD_SIZE`, `MAX_POSITION`, `NO_POSITION`) schlägt
`test_a6_abgelehnte_vorschlaege_erzeugen_null_fills` fehl mit `AssertionError: assert 1 == 0`
(`fills`-Zeilen entstehen trotz Ablehnung); bei `NOT_SPOT`/`BAD_SYMBOL` wirft `size_order`
zusätzlich eine `ValueError`, weil die Aktion oder das Symbol dort nicht mehr geprüft ist.
Ausgabe zeigen, Änderung zurücknehmen.

*Fehler 2 (A-6b):* In `resolve_pending` probehalber einen zweiten, redundanten Aufruf
`ctx.ledger.apply(order, candle)` direkt neben den bestehenden schreiben (simuliert einen
zweiten Aufrufer außerhalb der ursprünglichen Codepfad-Disziplin — ersatzweise: eine Zeile
`self.ledger.apply(...)` in eine Kopie von `poller.py`-artigem Code einfügen ist hier nicht
anwendbar, da `poller.py` nicht Teil von Plan A1 ist; stattdessen wird probeweise ein zweiter
Treffer direkt in `marketdata.py` eingefügt: `# ledger.apply(order, candle)  # Testzeile`).
Da Kommentare vom `grep`-Muster `\.apply(` **nicht** ausgeschlossen werden, liefert
`test_a6b_ledger_apply_hat_genau_einen_aufrufer` jetzt `andere == ['app/aitra/marketdata.py']`
statt `[]` — `AssertionError: assert ['.../marketdata.py'] == []`. Ausgabe zeigen, Zeile wieder
entfernen, Test erneut grün.

*Fehler 3 (A-7b, Verfall):* In `expire_stale_pending` die Bedingung `now - row["pending_since_ms"]
> ctx.pending_expiry_ms` durch `>=` ersetzen **und** `ctx.pending_expiry_ms` versehentlich mit
`1_800_001` initialisieren — einfacher und zielgenauer: den Verfall-Aufruf ganz auskommentieren
(`# store.expire_decision(...); continue` → nur `continue`, ohne Verfall). Test laufen lassen.
Erwartet: `test_a7b_verfall_an_der_grenze_1799_vs_1801_sekunden` schlägt fehl bei
`assert expired == [pending.decision_id]` mit `AssertionError: assert [] == [12]` (der
Vorschlag verfällt bei 1.801 s nicht mehr). Ausgabe zeigen, Änderung zurücknehmen, komplette
Testdatei erneut grün.

*Fehler 4 (A-14):* In `ledger.py` (Aufgabe 5), Methode `_book_buy`, die mengengewichtete
Fortschreibung durch den simplen neuen Preis ersetzen: `new_avg = price` statt
`new_avg = (pos.qty * pos.avg_price + qty * price) / new_qty if new_qty > 0 else Decimal(0)`.
Test laufen lassen. Erwartet: `test_a14_rekonstruktion_aus_dem_journal_1000_fills` schlägt fehl
bei `assert avg_price - positions["BTCUSDC"]["avg_price"] == Decimal("0")` — die persistierte
Position spiegelt nach mehreren Nachkäufen nur noch den letzten Kaufpreis, nicht den
mengengewichteten Einstand, den die Rekonstruktion aus den `fills`-Zeilen liefert. `qty` und
`cash` bleiben davon unberührt und weiterhin grün — das zeigt, warum alle drei Prüfungen nötig
sind. Ausgabe zeigen, Änderung zurücknehmen, komplette Testdatei erneut grün.

- [ ] **Schritt 6: Commit**

```bash
git add app/aitra/execute.py app/tests/test_execute.py
git commit -m "feat(execute): execute_proposal() als einziges Nadelöhr, schwebende Vorschläge, Verfall"
```

---

### Aufgabe 9: `benchmark.py` — BTC Buy & Hold

**Macht grün:** Grundlage für A-8 (dritter Baustein des Hash-Vergleichs in Aufgabe 10),
kein eigenständiges Kriterium — `benchmark.equity`/`return_pct`/`max_drawdown_pct` werden erst
über `web.py` sichtbar (Plan A2). Aufgabe 9 liefert die geprüfte Berechnung dafür.

**Dateien:**
- Neu: `app/aitra/benchmark.py`
- Neu: `app/tests/test_benchmark.py`

**Schnittstellen:**
- Nutzt: `config.Config` (Aufgabe 2), `ledger.Ledger` (Aufgabe 5), `marketdata.Candle/Clock`
  (Aufgabe 4), `risk.Proposal/RiskEngine` (Bestand + Aufgabe 7),
  `execute.ExecutionContext/execute_proposal` (Aufgabe 8), `money.SymbolSpec`
- Stellt bereit:
  - `class BuyAndHold:`
    - `__init__(self, cfg: Config, conn: sqlite3.Connection, run_id: str, symbol: str,
      spec: money.SymbolSpec, fee_bps: float, slippage_bps: float, clock: Clock)`
    - `on_candle(self, candle: Candle, prev_candle: Candle | None) -> None`
    - `equity(self, marks: Mapping[str, Decimal], ts_ms: int) -> Decimal`
    - `bought: bool` (Property)

**Wichtige, dokumentierte Abweichung von Spec Abschnitt 3.1:** Die Tabelle dort nennt für
`benchmark.py` nur die Abhängigkeit „ledger, money". Kriterium **A-6b** verlangt aber, dass
`Ledger.apply()` im gesamten Paket **ausschließlich** aus `execute.py` aufgerufen wird — ein
zweiter, direkter Aufruf in `benchmark.py` (wie es Spec Abschnitt 7.1 wörtlich nahelegt: „eine
zweite Ledger-Instanz … wird ein BUY … ausgeführt") würde diesen Wächter unmittelbar verletzen.
`BuyAndHold` ruft deshalb **`execute.execute_proposal()`** auf, nicht `Ledger.apply()` direkt.
Weil `position_pct=100` das normale `MAX_POSITION`-Limit (Vorgabe 10 %) sprengen würde, bekommt
`BuyAndHold` eine **eigene, durchlässige `RiskEngine`** über `dataclasses.replace(cfg,
max_position_pct=100, max_daily_loss_pct=100, max_total_exposure_pct=100)` — der Benchmark
trägt kein echtes Risiko, er berechnet nur eine Referenzgröße, und diese Prüfung soll bei ihm
strukturell nie greifen. Diese Abweichung gehört gemeldet, nicht stillschweigend gemacht:
Sie ist hier dokumentiert und wird in `docs/abnahme/` als Korrektur zu Spec Abschnitt 3.1
nachgetragen, sobald diese Aufgabe abgenommen ist.

**Wichtig für Folgeaufgaben:** `replay.py` (Aufgabe 10) erzeugt `BuyAndHold` mit einer eigenen,
zweiten `run_id` (z. B. `f"bench-{run_id}"`, wie in Spec 5 für `runs.run_id` vorgesehen) und
demselben `conn`. `on_candle()` kauft **genau einmal**, beim ersten Aufruf mit `prev_candle is
not None` — in `replay.py`s Schleife ist das der Schritt `t=1`, also „die zweite Kerze des
Laufs" (Spec 7.1).

- [ ] **Schritt 1: Den scheiternden Test schreiben**

```python
# app/tests/test_benchmark.py
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from aitra import db, money, store
from aitra.benchmark import BuyAndHold
from aitra.config import Config
from aitra.marketdata import Candle, SimClock

BTC = money.BUILTIN_SPECS["BTCUSDC"]


def _candle(open_time: int, price: str) -> Candle:
    return Candle(symbol="BTCUSDC", interval="15m", open_time=open_time, close_time=open_time + 899_999,
                  open=Decimal(price), high=Decimal(price), low=Decimal(price), close=Decimal(price),
                  volume=Decimal("1"), closed=True)


def _bench(tmp_path: Path) -> BuyAndHold:
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    store.create_run(conn, "bench-run-1", "benchmark", "2026-01-01T00:00:00Z", "0.3.0")
    cfg = Config(Decimal("10000"), 10, 2, 50, tmp_path, "x" * 32)
    return BuyAndHold(cfg, conn, "bench-run-1", "BTCUSDC", BTC, fee_bps=10.0, slippage_bps=5.0,
                       clock=SimClock(0))


def test_kauft_nicht_ohne_vorgaengerkerze(tmp_path):
    bench = _bench(tmp_path)
    bench.on_candle(_candle(0, "81287.03"), prev_candle=None)
    assert bench.bought is False
    assert bench.equity({"BTCUSDC": Decimal("81287.03")}, ts_ms=0) == Decimal("10000")


def test_kauft_genau_einmal_auf_der_zweiten_kerze(tmp_path):
    bench = _bench(tmp_path)
    k0 = _candle(0, "81287.03")
    k1 = _candle(900_000, "81300.00")
    k2 = _candle(1_800_000, "81400.00")
    bench.on_candle(k0, prev_candle=None)
    bench.on_candle(k1, prev_candle=k0)
    assert bench.bought is True
    equity_nach_kauf = bench.equity({"BTCUSDC": Decimal("81300.00")}, ts_ms=900_000)
    assert equity_nach_kauf < Decimal("10000")  # Gebuehr + Slippage kosten etwas
    assert equity_nach_kauf > Decimal("9950")

    # zweiter Aufruf darf keinen weiteren Kauf ausloesen
    bench.on_candle(k2, prev_candle=k1)
    assert store.get_fills(bench._ctx.conn, "bench-run-1").__len__() == 1


def test_equity_folgt_dem_kurs_nach_dem_kauf(tmp_path):
    bench = _bench(tmp_path)
    k0 = _candle(0, "81287.03")
    k1 = _candle(900_000, "81287.03")
    bench.on_candle(k0, prev_candle=None)
    bench.on_candle(k1, prev_candle=k0)
    e_tief = bench.equity({"BTCUSDC": Decimal("70000")}, ts_ms=1_800_000)
    e_hoch = bench.equity({"BTCUSDC": Decimal("90000")}, ts_ms=1_800_000)
    assert e_hoch > e_tief


def test_kein_zweiter_aufrufer_von_apply_in_benchmark():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "benchmark.py").read_text()
    assert ".apply(" not in text


def test_a8b_keine_versteckte_uhr_kein_versteckter_zufall():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "benchmark.py").read_text()
    assert re.search(r"time\.time|datetime\.(now|utcnow)|random\.", text) is None
```

- [ ] **Schritt 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_benchmark.py -q'
```

Erwartet: `ModuleNotFoundError: No module named 'aitra.benchmark'`

- [ ] **Schritt 3: `benchmark.py` schreiben**

```python
"""BTC Buy & Hold: die Referenzgroesse, gerechnet durch dasselbe Ledger (Spec 7.1).

Ruft Ledger.apply() NICHT direkt auf (A-6b) — siehe die Abweichung von Spec 3.1,
dokumentiert im Plan. Stattdessen wie jede Strategie ueber execute.execute_proposal(),
mit einer eigenen, durchlaessigen RiskEngine, weil der Benchmark kein echtes Risiko
traegt, sondern nur eine Vergleichsgroesse berechnet.
"""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from decimal import Decimal
from typing import Mapping

from . import money
from .config import Config
from .execute import ExecutionContext, execute_proposal
from .ledger import Ledger
from .marketdata import Candle, Clock
from .risk import Proposal, RiskEngine


class BuyAndHold:
    def __init__(
        self,
        cfg: Config,
        conn: sqlite3.Connection,
        run_id: str,
        symbol: str,
        spec: money.SymbolSpec,
        fee_bps: float,
        slippage_bps: float,
        clock: Clock,
    ) -> None:
        permissive_cfg = replace(cfg, max_position_pct=100, max_daily_loss_pct=100, max_total_exposure_pct=100)
        ledger = Ledger(starting_cash=cfg.starting_balance, specs={symbol: spec},
                         fee_bps=fee_bps, slippage_bps=slippage_bps)
        self._ctx = ExecutionContext(
            conn=conn, run_id=run_id, ledger=ledger, engine=RiskEngine(permissive_cfg),
            specs={symbol: spec}, fee_bps=fee_bps, slippage_bps=slippage_bps, clock=clock,
        )
        self._symbol = symbol
        self._bought = False

    @property
    def bought(self) -> bool:
        return self._bought

    def on_candle(self, candle: Candle, prev_candle: Candle | None) -> None:
        """Kauft genau einmal, auf der zweiten Kerze des Laufs (Spec 7.1)."""
        if self._bought or prev_candle is None:
            return
        equity_now = self._ctx.ledger.mark({}, ts_ms=prev_candle.close_time).equity
        execute_proposal(
            Proposal(symbol=self._symbol, action="BUY", position_pct=100),
            self._ctx,
            marks={}, ts_ms=prev_candle.close_time, ref_price=prev_candle.close,
            start_of_day_equity=equity_now, next_candle=candle,
            strategy_version="benchmark-buy-and-hold",
            reason="BTC Buy & Hold Referenz (Spec 7.1)",
        )
        self._bought = True

    def equity(self, marks: Mapping[str, Decimal], ts_ms: int) -> Decimal:
        return self._ctx.ledger.mark(marks, ts_ms).equity
```

- [ ] **Schritt 4: Test laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_benchmark.py -q'
```

Erwartet: `5 passed`

- [ ] **Schritt 5: Rot-Nachweis führen**

*Fehler 1 (Kauf-Disziplin):* Die Zeile `self._bought = True` aus `on_candle` entfernen. Test
laufen lassen. Erwartet: `test_kauft_genau_einmal_auf_der_zweiten_kerze` schlägt fehl mit
`AssertionError: assert 2 == 1` (`store.get_fills(...)` liefert nach dem dritten Aufruf zwei
Fills statt einem, weil `BuyAndHold` bei jeder weiteren Kerze erneut kauft). Ausgabe zeigen,
Änderung zurücknehmen.

*Fehler 2 (A-6b-Abweichung):* In `on_candle` versuchsweise
`self._ctx.ledger.apply(None, candle)` als zusätzliche (nie erreichte, aber im Text vorhandene)
Zeile einfügen. `grep`-Prüfung aus `test_kein_zweiter_aufrufer_von_apply_in_benchmark` bzw. der
globale A-6b-Befehl aus Aufgabe 8 laufen lassen. Erwartet:
`test_kein_zweiter_aufrufer_von_apply_in_benchmark` schlägt fehl mit
`AssertionError: assert '.apply(' not in text`. Zeile wieder entfernen, Test erneut grün.

- [ ] **Schritt 6: Commit**

```bash
git add app/aitra/benchmark.py app/tests/test_benchmark.py
git commit -m "feat(benchmark): BuyAndHold – BTC-Referenz über dieselbe Ledger-/Execute-Kette"
```

---

### Aufgabe 10: `replay.py` — `run_replay()` mit `SimClock`, lauf-lokalem Kill Switch, CLI

**Macht grün:** A-7 (vollständig, 8.640 Kerzen), A-8 (zwei der drei Quellen — `ListSource` und
`SqliteSource`; die dritte, der Live-Pfad über einen `FakeBinanceSource`, braucht `poller.py`
aus Plan A2 und wird dort nachgezogen), A-8c, A-9, A-10, A-12, A-12b.

**Dateien:**
- Neu: `app/aitra/replay.py`
- Neu: `app/tests/test_replay.py`

**Schnittstellen:**
- Nutzt: `marketdata.Candle/ListSource/SqliteSource/SimClock` (Aufgabe 4), `ledger.Ledger/Fill`
  (Aufgabe 5), `sizing` (indirekt über `execute`), `risk.Proposal/RiskEngine` (Bestand + Aufgabe 7),
  `execute.ExecutionContext/execute_proposal` (Aufgabe 8), `benchmark.BuyAndHold` (Aufgabe 9),
  `store.create_run` (Aufgabe 3), `db.connect/migrate/now` (Bestand), `config.Config/load`
  (Aufgabe 2), `money.BUILTIN_SPECS/SymbolSpec` (Aufgabe 1)
- Stellt bereit:
  - `ReplayResult(run_id, final_equity, benchmark_final_equity, fills, kill_switch_engagements, decisions)`
    (frozen dataclass)
  - `run_replay(candles: Sequence[Candle], decide_fn: Callable[[Sequence[Candle]], Proposal],
    cfg: Config, specs: Mapping[str, money.SymbolSpec], fee_bps: float, slippage_bps: float,
    benchmark_symbol: str, run_id: str, conn: sqlite3.Connection | None = None) -> ReplayResult`
  - `main(argv: list[str] | None = None) -> int` — CLI-Einstiegspunkt
  - `if __name__ == "__main__": raise SystemExit(main())`

**Wichtige, dokumentierte Einschränkung gegenüber Spec Abschnitt 9.2:** `run_replay()` verarbeitet
in Plan A1 **genau einen Symbolstrom pro Aufruf** (wie das Pseudocode in Spec 9.2 selbst zeigt:
`ledger.mark({sym: candles[t-1].close}, ...)` mit einem einzelnen `sym`). Kein A-Kriterium
verlangt einen interleaved Mehrsymbol-Replay in einem Lauf; `Ledger` und `execute_proposal()`
sind bereits symbolagnostisch, ein Mehrsymbol-Replay wäre eine spätere, nicht vorweggenommene
Erweiterung (YAGNI).

**Wichtige, dokumentierte Auslegung von A-9 „ohne DB-Schreibzugriff":** `execute_proposal()`
schreibt immer über `store`/`db` in eine SQLite-Verbindung (das ist gewollt — sonst gäbe es
zwei verschiedene Codepfade und E-001 wäre gebrochen). „Ohne DB-Schreibzugriff" wird deshalb als
„ohne **dauerhaften** Schreibzugriff auf Platte" gelesen: Wird `run_replay()` ohne `conn`
aufgerufen, öffnet es intern `sqlite3.connect(":memory:")` — dieselbe Journal-Logik, aber ohne
jede Diskbelastung. Das ist eine bewusste Auslegungsentscheidung; sie wird in `docs/abnahme/`
vermerkt, damit sie im Zweifel korrigiert werden kann.

- [ ] **Schritt 1: Den scheiternden Test schreiben**

```python
# app/tests/test_replay.py
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tracemalloc
from decimal import Decimal
from pathlib import Path

import pytest

from aitra import db, money, store
from aitra.config import Config
from aitra.execute import ExecutionContext, execute_proposal
from aitra.ledger import Ledger
from aitra.marketdata import Candle, ListSource, SimClock, SqliteSource
from aitra.replay import ReplayResult, run_replay
from aitra.risk import Proposal, RiskEngine

BTC = money.BUILTIN_SPECS["BTCUSDC"]
SPECS = {"BTCUSDC": BTC}
CFG = Config(Decimal("10000"), 10, 2, 50, Path("/tmp"), "x" * 32)


def _candles(n: int, start_price: str = "81287.03", step_ms: int = 900_000) -> list[Candle]:
    out = []
    price = Decimal(start_price)
    for i in range(n):
        p = price + Decimal(i % 97) * Decimal("0.01")
        out.append(Candle(symbol="BTCUSDC", interval="15m", open_time=i * step_ms,
                           close_time=i * step_ms + step_ms - 1, open=p, high=p, low=p, close=p,
                           volume=Decimal("1"), closed=True))
    return out


def _wait_fn(history):
    return Proposal(history[-1].symbol, "WAIT")


def _hash_fills(fills) -> str:
    payload = json.dumps(
        [[f.symbol, f.side, str(f.price), str(f.qty), str(f.gross_quote), str(f.fee),
          str(f.net_quote), str(f.cash_after), f.candle_open_time, f.fee_bps, f.slippage_bps]
         for f in fills],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def test_a7_decide_fn_sieht_nie_die_fuellkerze():
    """A-7: 90 Tage 15m = 8.640 Kerzen. history[-1].open_time ist immer genau eine
    Kerze vor der aktuellen; jeder Fill landet auf open_time + 900_000."""
    candles = _candles(8640)
    aufrufe = []

    def decide_fn(history):
        aufrufe.append((history[-1].open_time, len(history)))
        t = len(history)
        if t % 500 == 0:
            return Proposal("BTCUSDC", "BUY", position_pct=1)
        return Proposal("BTCUSDC", "WAIT")

    result = run_replay(candles, decide_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                         benchmark_symbol="BTCUSDC", run_id="test-a7")

    assert len(aufrufe) == 8639
    for open_time, t in aufrufe:
        erwartet = candles[t - 1].open_time
        assert open_time == erwartet
        assert open_time == (t - 1) * 900_000

    assert len(result.fills) > 0  # Pruefflaeche darf nicht leer sein
    for f in result.fills:
        entscheidungskerze_index = f.candle_open_time // 900_000 - 1
        assert f.candle_open_time == candles[entscheidungskerze_index].open_time + 900_000


def test_a8_list_und_sqlite_quelle_liefern_identische_fills(tmp_path):
    candles = _candles(500)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    store.upsert_candles(conn, [
        store.CandleRow(symbol=c.symbol, interval=c.interval, open_time=c.open_time,
                         close_time=c.close_time, open=c.open, high=c.high, low=c.low,
                         close=c.close, volume=c.volume, source="fixture",
                         fetched_at="2026-01-01T00:00:00Z")
        for c in candles
    ])
    aus_liste = ListSource(candles).candles("BTCUSDC", "15m", limit=1000)
    aus_db = SqliteSource(conn).candles("BTCUSDC", "15m", limit=1000)

    def decide_fn(history):
        t = len(history)
        if t % 50 == 0:
            return Proposal("BTCUSDC", "BUY", position_pct=2)
        if t % 77 == 0:
            return Proposal("BTCUSDC", "SELL", position_pct=1)
        return Proposal("BTCUSDC", "WAIT")

    r1 = run_replay(aus_liste, decide_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                     benchmark_symbol="BTCUSDC", run_id="test-a8-liste")
    r2 = run_replay(aus_db, decide_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                     benchmark_symbol="BTCUSDC", run_id="test-a8-db")

    assert len(r1.fills) > 0
    assert _hash_fills(r1.fills) == _hash_fills(r2.fills)


def test_a8c_gleicher_lauf_im_selben_prozess_gleicher_hash():
    candles = _candles(300)

    def decide_fn(history):
        t = len(history)
        return Proposal("BTCUSDC", "BUY", position_pct=1) if t % 40 == 0 else Proposal("BTCUSDC", "WAIT")

    r1 = run_replay(candles, decide_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                     benchmark_symbol="BTCUSDC", run_id="hash-1")
    r2 = run_replay(candles, decide_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                     benchmark_symbol="BTCUSDC", run_id="hash-2")
    assert len(r1.fills) > 0
    assert _hash_fills(r1.fills) == _hash_fills(r2.fills)


def test_a8c_gleicher_lauf_in_getrennten_prozessen_gleicher_hash(tmp_path):
    candles = _candles(300)
    conn = db.connect(tmp_path / "seed.db")
    db.migrate(conn)
    store.upsert_candles(conn, [
        store.CandleRow(symbol=c.symbol, interval=c.interval, open_time=c.open_time,
                         close_time=c.close_time, open=c.open, high=c.high, low=c.low,
                         close=c.close, volume=c.volume, source="fixture",
                         fetched_at="2026-01-01T00:00:00Z")
        for c in candles
    ])
    conn.close()
    from_iso = "1970-01-01T00:00:00+00:00"
    to_iso = "1970-01-05T00:00:00+00:00"
    env_hashes = set()
    for seed in ("1", "2"):
        out = subprocess.run(
            [sys.executable, "-m", "aitra.replay", "--symbol", "BTCUSDC", "--interval", "15m",
             "--from", from_iso, "--to", to_iso, "--db", str(tmp_path / "seed.db")],
            cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True,
            env={**os.environ, "PYTHONHASHSEED": seed, "DATA_DIR": str(tmp_path), "ADMIN_TOKEN": "x" * 32},
        )
        assert out.returncode == 0, out.stderr
        env_hashes.add(out.stdout.strip())
    assert len(env_hashes) == 1


@pytest.mark.slow
def test_a9_tempo_35040_kerzen_container_schwelle():
    import time
    candles = _candles(35_040)

    def decide_fn(history):
        t = len(history)
        return Proposal("BTCUSDC", "BUY", position_pct=1) if t % 1000 == 0 else Proposal("BTCUSDC", "WAIT")

    start = time.perf_counter()
    result = run_replay(candles, decide_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                         benchmark_symbol="BTCUSDC", run_id="test-a9")
    elapsed = time.perf_counter() - start
    assert result.decisions == 35_039
    assert elapsed < 40.0  # Container-Schwelle (876 Entscheidungen/s); Dev-Schwelle: 12,0 s


def test_a10_speicher_35040_kerzen():
    candles = _candles(35_040)

    def decide_fn(history):
        t = len(history)
        return Proposal("BTCUSDC", "BUY", position_pct=1) if t % 1000 == 0 else Proposal("BTCUSDC", "WAIT")

    tracemalloc.start()
    run_replay(candles, decide_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
               benchmark_symbol="BTCUSDC", run_id="test-a10")
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 120 * 1024 * 1024


def test_a12_zeitraffer_nutzt_nur_simclock():
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "replay.py").read_text()
    assert "WallClock" not in text
    assert "staleness(" not in text


def test_a12b_tagesverlustlimit_blockiert_nur_den_tag_nicht_den_lauf():
    """5 Tage (480 Kerzen bei 15m). Ein harter Kurssturz auf Tag 1 loest die
    Tagesverlustgrenze aus; Tag 2 handelt wieder normal (E-008)."""
    day_len = 96
    n = 5 * day_len
    prices = []
    for i in range(n):
        if i < 2:
            prices.append(Decimal("100"))
        else:
            prices.append(Decimal("50"))
    candles = [
        Candle(symbol="BTCUSDC", interval="15m", open_time=i * 900_000, close_time=i * 900_000 + 899_999,
               open=prices[i], high=prices[i], low=prices[i], close=prices[i], volume=Decimal("1"), closed=True)
        for i in range(n)
    ]

    def decide_fn(history):
        t = len(history)
        if t == 1:
            return Proposal("BTCUSDC", "BUY", position_pct=100)
        return Proposal("BTCUSDC", "SELL", position_pct=1)

    permissive_cfg = Config(Decimal("10000"), 100, 2, 100, Path("/tmp"), "x" * 32)
    result = run_replay(candles, decide_fn, permissive_cfg, SPECS, fee_bps=10.0, slippage_bps=5.0,
                         benchmark_symbol="BTCUSDC", run_id="test-a12b")

    assert result.kill_switch_engagements == 1
    tag1_fills = [f for f in result.fills if f.candle_open_time < day_len * 900_000]
    tag2_fills = [f for f in result.fills if day_len * 900_000 <= f.candle_open_time < 2 * day_len * 900_000]
    # Nach dem Ausloesen (Kerze 2, Preissturz) darf an Tag 1 kein Verkauf mehr durchgehen:
    # genau der anfaengliche BUY (Kerze 1) und der eine SELL vor dem Sturz (Kerze 2) zaehlen.
    assert len(result.fills) >= 3  # die Pruefflaeche darf nicht leer sein
    assert len(tag1_fills) == 2
    assert len(tag2_fills) >= 1


def test_a12b_gegenprobe_ohne_tagesreset_bleibt_kill_switch_aktiv():
    """Dieselbe Situation, aber ohne den Tagesgrenzen-Reset aus replay.py nachgebaut
    (wie im Live-Pfad, E-008): der Kill Switch bleibt auch an Tag 2 aktiv, 0 Fills."""
    day_len = 96
    n = 3 * day_len
    prices = [Decimal("100") if i < 2 else Decimal("50") for i in range(n)]
    candles = [
        Candle(symbol="BTCUSDC", interval="15m", open_time=i * 900_000, close_time=i * 900_000 + 899_999,
               open=prices[i], high=prices[i], low=prices[i], close=prices[i], volume=Decimal("1"), closed=True)
        for i in range(n)
    ]
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.migrate(conn)
    store.create_run(conn, "live-sim", "live", db.now(), "0.3.0")
    ledger = Ledger(starting_cash=Decimal("10000"), specs=SPECS, fee_bps=10.0, slippage_bps=5.0)
    cfg = Config(Decimal("10000"), 100, 2, 100, Path("/tmp"), "x" * 32)
    ctx = ExecutionContext(conn=conn, run_id="live-sim", ledger=ledger, engine=RiskEngine(cfg),
                            specs=SPECS, fee_bps=10.0, slippage_bps=5.0, clock=SimClock(0))
    sod_equity = Decimal("10000")
    fills_nach_ausloesung = 0
    ausgeloest = False
    for t in range(1, n):
        current = candles[t - 1]
        ctx.clock.set(current.close_time)
        # KEIN Tagesreset hier -- das ist der Unterschied zu run_replay()
        proposal = Proposal("BTCUSDC", "BUY", position_pct=100) if t == 1 else Proposal("BTCUSDC", "SELL", position_pct=1)
        result = execute_proposal(proposal, ctx, marks={"BTCUSDC": current.close}, ts_ms=current.close_time,
                                   ref_price=current.close, start_of_day_equity=sod_equity, next_candle=candles[t])
        if result.code == "DAILY_LOSS" and not ausgeloest:
            ausgeloest = True
            ctx.kill_switch = True
        if ausgeloest and result.fill is not None:
            fills_nach_ausloesung += 1
    assert ausgeloest is True
    assert fills_nach_ausloesung == 0


def test_cli_lauft_end_to_end(tmp_path):
    candles = _candles(20)
    conn = db.connect(tmp_path / "cli.db")
    db.migrate(conn)
    store.upsert_candles(conn, [
        store.CandleRow(symbol=c.symbol, interval=c.interval, open_time=c.open_time,
                         close_time=c.close_time, open=c.open, high=c.high, low=c.low,
                         close=c.close, volume=c.volume, source="fixture",
                         fetched_at="2026-01-01T00:00:00Z")
        for c in candles
    ])
    conn.close()
    out = subprocess.run(
        [sys.executable, "-m", "aitra.replay", "--symbol", "BTCUSDC", "--interval", "15m",
         "--from", "1970-01-01T00:00:00+00:00", "--to", "1970-01-01T06:00:00+00:00",
         "--db", str(tmp_path / "cli.db")],
        cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True,
        env={**os.environ, "DATA_DIR": str(tmp_path), "ADMIN_TOKEN": "x" * 32},
    )
    assert out.returncode == 0, out.stderr
    assert re.search(r"[0-9a-f]{64}", out.stdout)  # der Hash, den auch A-8c vergleicht
```

- [ ] **Schritt 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_replay.py -q -m "not slow"'
```

Erwartet: `ModuleNotFoundError: No module named 'aitra.replay'`

- [ ] **Schritt 3: `replay.py` schreiben**

```python
"""run_replay(): Zeitraffer/DB-Replay mit SimClock und lauf-lokalem Kill Switch (E-008).

Identische Reihenfolge wie live: mark -> Entscheidung -> execute_proposal -> Bewertung.
Einziger Unterschied: die Uhr, die Quelle und der lauf-lokale Kill Switch (E-008).
CLI: python -m aitra.replay --symbol BTCUSDC --interval 15m --from ... --to ...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import config, db, money, store
from .benchmark import BuyAndHold
from .config import Config
from .execute import ExecutionContext, execute_proposal
from .ledger import Fill, Ledger
from .marketdata import Candle, SimClock
from .risk import Proposal, RiskEngine


@dataclass(frozen=True)
class ReplayResult:
    run_id: str
    final_equity: Decimal
    benchmark_final_equity: Decimal
    fills: list[Fill]
    kill_switch_engagements: int
    decisions: int


def _utc_date(ms: int):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()


def run_replay(
    candles: Sequence[Candle],
    decide_fn: Callable[[Sequence[Candle]], Proposal],
    cfg: Config,
    specs: Mapping[str, money.SymbolSpec],
    fee_bps: float,
    slippage_bps: float,
    benchmark_symbol: str,
    run_id: str,
    conn: sqlite3.Connection | None = None,
) -> ReplayResult:
    symbol = candles[0].symbol
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
    db.migrate(conn)
    bench_run_id = f"bench-{run_id}"
    store.create_run(conn, run_id, "replay", db.now(), "0.3.0")
    store.create_run(conn, bench_run_id, "benchmark", db.now(), "0.3.0")

    ledger = Ledger(starting_cash=cfg.starting_balance, specs=specs, fee_bps=fee_bps, slippage_bps=slippage_bps)
    clock = SimClock(candles[0].close_time)
    ctx = ExecutionContext(conn=conn, run_id=run_id, ledger=ledger, engine=RiskEngine(cfg),
                            specs=specs, fee_bps=fee_bps, slippage_bps=slippage_bps, clock=clock)

    bench_spec = specs[benchmark_symbol]
    bench = BuyAndHold(cfg, conn, bench_run_id, benchmark_symbol, bench_spec, fee_bps, slippage_bps, clock)

    kill_switch_tag = None
    kill_switch_local = False
    kill_switch_engagements = 0
    sod_equity = ledger.mark({symbol: candles[0].close}, ts_ms=candles[0].close_time).equity
    fills: list[Fill] = []
    decisions = 0

    for t in range(1, len(candles)):
        current = candles[t - 1]
        clock.set(current.close_time)

        tag = _utc_date(current.close_time)
        if tag != kill_switch_tag:
            kill_switch_tag = tag
            kill_switch_local = False
            sod_equity = ledger.mark({symbol: current.close}, ts_ms=current.close_time).equity
        ctx.kill_switch = kill_switch_local

        proposal = decide_fn(candles[:t])
        decisions += 1
        result = execute_proposal(
            proposal, ctx, marks={symbol: current.close}, ts_ms=current.close_time,
            ref_price=current.close, start_of_day_equity=sod_equity, next_candle=candles[t],
            strategy_version="replay",
        )
        if result.fill is not None:
            fills.append(result.fill)
        if result.code == "DAILY_LOSS" and not kill_switch_local:
            kill_switch_local = True
            kill_switch_engagements += 1

        bench.on_candle(candles[t], current)

    final_marks = {symbol: candles[-1].close}
    final_equity = ledger.mark(final_marks, ts_ms=candles[-1].close_time).equity
    bench_equity = bench.equity(final_marks, ts_ms=candles[-1].close_time)

    if own_conn:
        conn.close()

    return ReplayResult(run_id=run_id, final_equity=final_equity, benchmark_final_equity=bench_equity,
                         fills=fills, kill_switch_engagements=kill_switch_engagements, decisions=decisions)


def _load_candles_from_db(conn: sqlite3.Connection, symbol: str, interval: str,
                           from_ms: int, to_ms: int) -> list[Candle]:
    rows = store.get_candles(conn, symbol, interval, start_ms=from_ms, end_ms=to_ms, limit=1_000_000)
    return [
        Candle(symbol=r.symbol, interval=r.interval, open_time=r.open_time, close_time=r.close_time,
               open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume, closed=True)
        for r in rows
    ]


def _wait_fn(history: Sequence[Candle]) -> Proposal:
    """CLI-Vorgabestrategie: immer WAIT. Echte decide_fn kommen von B/C."""
    return Proposal(symbol=history[-1].symbol, action="WAIT")


def _hash_fills(fills: list[Fill]) -> str:
    payload = json.dumps(
        [[f.symbol, f.side, str(f.price), str(f.qty), str(f.gross_quote), str(f.fee),
          str(f.net_quote), str(f.cash_after), f.candle_open_time, f.fee_bps, f.slippage_bps]
         for f in fills],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Aitra Replay (Zeitraffer/DB-Backtest, E-001)")
    p.add_argument("--symbol", required=True)
    p.add_argument("--interval", default="15m")
    p.add_argument("--from", dest="from_", required=True, help="ISO-8601, z. B. 2025-01-01T00:00:00+00:00")
    p.add_argument("--to", required=True, help="ISO-8601")
    p.add_argument("--db", default=None, help="Pfad zur aitra.db; ohne Angabe :memory:")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from_ms = int(datetime.fromisoformat(args.from_).timestamp() * 1000)
    to_ms = int(datetime.fromisoformat(args.to).timestamp() * 1000)

    conn = db.connect(Path(args.db)) if args.db else sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.migrate(conn)

    candles = _load_candles_from_db(conn, args.symbol, args.interval, from_ms, to_ms)
    if len(candles) < 2:
        print(f"Zu wenige Kerzen ({len(candles)}) für {args.symbol} {args.interval} im Zeitraum", file=sys.stderr)
        return 1

    cfg = config.load()
    spec = money.BUILTIN_SPECS.get(args.symbol)
    if spec is None:
        print(f"Keine eingebaute SymbolSpec für {args.symbol}", file=sys.stderr)
        return 1

    result = run_replay(
        candles, _wait_fn, cfg, {args.symbol: spec}, fee_bps=10.0, slippage_bps=5.0,
        benchmark_symbol=args.symbol, run_id=f"replay-{args.from_}-{args.to}", conn=conn,
    )
    print(_hash_fills(result.fills))
    print(
        f"Kerzen={len(candles)} Entscheidungen={result.decisions} Fills={len(result.fills)} "
        f"Endkapital={result.final_equity} Benchmark={result.benchmark_final_equity} "
        f"Kill-Switch-Auslösungen={result.kill_switch_engagements}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Schritt 4: Test laufen lassen, Erfolg bestätigen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_replay.py -q -m "not slow"'
```

Erwartet: `9 passed`

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest tests/test_replay.py -q -m slow'
```

Erwartet: `1 passed` (der A-9-Tempotest; separat, weil er als `@pytest.mark.slow` geführt wird —
siehe Betriebshinweis in Spec Abschnitt 12/19: `build.sh` läuft ohne `slow`-Marker).

*Voraussetzung:* `app/pytest.ini` (oder `pyproject.toml`) registriert den Marker, sonst meldet
pytest eine Warnung `PytestUnknownMarkWarning`. Falls die Datei noch nicht existiert, anlegen:

```ini
# app/pytest.ini
[pytest]
markers =
    slow: langsame Tests (Tempo-/Speichermessungen), nicht Teil von build.sh
```

- [ ] **Schritt 5: Volle bestehende Suite laufen lassen**

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c \
  'pip install -q -r app/requirements.txt pytest && cd app && python -m pytest -q -m "not slow"'
```

Erwartet: **0 failures, 0 errors**, alle Module aus Aufgabe 1–10 gemeinsam grün.

- [ ] **Schritt 6: Rot-Nachweis führen**

*Fehler 1 (A-7):* In `run_replay` `decide_fn(candles[:t])` durch `decide_fn(candles[:t + 1])`
ersetzen. Test laufen lassen. Erwartet: `test_a7_decide_fn_sieht_nie_die_fuellkerze` schlägt
fehl mit `AssertionError: assert 900000 == 0` (oder einem anderen Kerzenindex) — `history[-1]`
ist jetzt die Entscheidungskerze selbst statt ihrer Vorgängerin, der Blick in die Zukunft ist
da. Ausgabe zeigen, Änderung zurücknehmen.

*Fehler 2 (A-12):* `clock = SimClock(candles[0].close_time)` durch `clock = WallClock()` ersetzen
(dafür `WallClock` importieren). Test laufen lassen. Erwartet:
`test_a12_zeitraffer_nutzt_nur_simclock` schlägt fehl mit
`AssertionError: assert 'WallClock' not in text`. Ausgabe zeigen, Änderung zurücknehmen.

*Fehler 3 (E-008/A-12b):* Den Block
```python
        if tag != kill_switch_tag:
            kill_switch_tag = tag
            kill_switch_local = False
            sod_equity = ledger.mark({symbol: current.close}, ts_ms=current.close_time).equity
```
durch `tag = kill_switch_tag` ersetzen (der Tageswechsel wird nie erkannt, der Kill Switch löst
sich nie). Test laufen lassen. Erwartet: `test_a12b_tagesverlustlimit_blockiert_nur_den_tag_nicht_den_lauf`
schlägt fehl mit `AssertionError: assert 0 >= 1` (`tag2_fills` bleibt leer, weil der Kill Switch
über die Tagesgrenze hinaus aktiv bleibt). Ausgabe zeigen, Änderung zurücknehmen, komplette
Suite (Schritt 5) erneut grün.

- [ ] **Schritt 7: Commit**

```bash
git add app/aitra/replay.py app/tests/test_replay.py app/pytest.ini
git commit -m "feat(replay): run_replay() mit SimClock, lauf-lokalem Kill Switch (E-008) und CLI"
```

---
