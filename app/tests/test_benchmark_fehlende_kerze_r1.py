# app/tests/test_benchmark_fehlende_kerze_r1.py
"""R1: Schutz ohne Nachweis -- zwei wirksame Zeilen in benchmark.py, die
keine Prueffläche hatten.

DER BEFUND (vom Reviewer gemessen, nicht vermutet)
==================================================

    benchmark.py:100   ledger.mark({s: p.avg_price ...}, ts_ms=...)   (Seed
                       von last_marks direkt nach ledger.restore())
    benchmark.py:156   ledger.mark({**ledger.last_marks, **marks}, ts_ms)
                       (equity() ergaenzt fehlende Preise)

BEIDE Zeilen liessen sich entfernen, ohne dass auch nur ein Test der Suite
anschlug (302 passed, unveraendert). Tot sind sie trotzdem nicht: ohne sie
wirft ein Zyklus nach einem Neustart mit gehaltener Benchmark-Position

    ValueError: Kein Marktpreis fuer gehaltene Position BTCUSDC
    (qty=0.09955000); mark() erhielt Preise fuer []

WARUM DER FALL REAL IST
=======================

Ledger.restore() laesst last_marks bewusst leer (E-010/Weg A). poll_once()
reicht dem Benchmark nur die Preise der Symbole durch, die in DIESER Runde
eine neue Kerze geliefert haben (poller.py:153/170). Es gibt mehr als ein
Symbol (cfg.market_symbols = BTCUSDC, BNBUSDC), und ein Abruf kann je Symbol
einzeln fehlschlagen (poller.py:109-118: `continue`, nicht `return`). Sobald
also nach einem Prozessneustart die BTC-Kerze einmal ausbleibt, waehrend BNB
liefert, steht in `marks` kein Preis fuer die gehaltene Benchmark-Position.

Die Folge trifft nicht nur den Benchmark: run_forever() faengt die Ausnahme
zwar ab (der Thread ueberlebt), aber der GANZE Zyklus ist verloren -- keine
Equity-Kurve, keine Fills, nur ein POLL_CYCLE_EXCEPTION im Ereignisprotokoll
und Backoff. Ein einzelner fehlgeschlagener Kerzenabruf legt damit die
gesamte Buchung lahm.

DIE PRUEFFLAECHE (B-D1-Lehre)
=============================

Jeder Test hier sichert VOR dem eigentlichen Nadeloehr ab, dass

 1. der Benchmark ueberhaupt eine Position haelt (sonst wirft mark() nie),
 2. ein echter Neustart stattgefunden hat (sonst ist last_marks schon gefuellt
    und der Fall tritt gar nicht ein),
 3. die BTC-Kerze in der Pruefrunde WIRKLICH ausgeblieben ist -- nachgewiesen
    an der Kerzentabelle, nicht am Testdouble,
 4. BNB in derselben Runde WIRKLICH geliefert hat (sonst bliebe neue_kerzen
    leer und der ganze Benchmark-Zweig wuerde uebersprungen).

Ohne (3) und (4) waere der Test gruen, ohne je den Pfad zu betreten.

ZWEI SCHICHTEN
==============

test_..._poll_once_... prueft den Zyklus. test_..._api_equity_curve_...
prueft den Weg bis zum Nutzer: dass der Punkt mit benchmark != None ueber
GET /api/equity-curve tatsaechlich beim Auftraggeber ankommt. Ein Waechter,
der nur poll_once() prueft, saehe nicht, ob der Punkt je gespeichert wird.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from aitra import db, money, poller, store, store_run
from aitra.binance import BinanceClient
from aitra.config import Config
from aitra.marketdata import SimClock
from aitra.web import create_app

BASIS = "https://api.binance.com"
BENCH = "BTCUSDC"        # cfg.benchmark_symbol
ANDERES = "BNBUSDC"      # liefert, waehrend BENCH ausbleibt
SPECS = {BENCH: money.BUILTIN_SPECS[BENCH], ANDERES: money.BUILTIN_SPECS[ANDERES]}
BENCH_RUN = "bench-live"
TOKEN = "t" * 32
INTERVALL_MS = 900_000
T0 = 900_000_000


# --------------------------------------------------------------------------
# Testdoubles: wie in test_benchmark_neustart_b_d5.py, aber PRO SYMBOL --
# genau das ist hier der Punkt. Eigenstaendig, damit diese Datei keine fremde
# Testdatei anfasst.
# --------------------------------------------------------------------------
class FakeAntwort:
    def __init__(self, body: bytes) -> None:
        self._b, self._p = body, 0

    def read(self, n: int = -1) -> bytes:
        d = self._b[self._p:] if n is None or n < 0 else self._b[self._p:self._p + n]
        self._p += len(d)
        return d

    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _body(obj) -> bytes:
    return json.dumps(obj).encode()


def _kerze(index: int, open_price: str, close_price: str) -> list:
    """Eine Roh-Kline (12 Felder, Spec 2.2). open und close bewusst getrennt:
    ref_price ist prev.close, gefuellt wird zu candle.open (E-006)."""
    open_time = T0 + index * INTERVALL_MS
    hoch = max(Decimal(open_price), Decimal(close_price))
    tief = min(Decimal(open_price), Decimal(close_price))
    return [open_time, open_price, str(hoch), str(tief), close_price, "12.345",
            open_time + INTERVALL_MS - 1, "1", 1, "0", "0", "0"]


class Markt:
    """Liefert je Symbol genau die Kerze, die gerade gesetzt ist -- oder gar
    keine (`None` = leere Antwort) oder einen Netzfehler (`fehler`). Die Uhr
    steht 30 s hinter der juengsten close_time; bei 15m-Kerzen liegt die
    Veraltet-Schwelle bei max(150, 1,5*900) = 1350 s, ein einzelner
    Kerzenausfall (930 s) bleibt also sicher unter 'warn'."""

    def __init__(self) -> None:
        self.clock = SimClock(T0)
        self.kerzen: dict[str, list | None] = {}
        self.fehler: set[str] = set()
        self.abgefragt: list[str] = []

    def client(self) -> BinanceClient:
        markt = self

        def klines(url):
            symbol = parse_qs(urlsplit(url).query)["symbol"][0]
            markt.abgefragt.append(symbol)
            if symbol in markt.fehler:
                raise OSError("Testdouble: Kerzenabruf fehlgeschlagen")
            k = markt.kerzen.get(symbol)
            return FakeAntwort(_body([] if k is None else [k]))

        def zeit(url):
            return FakeAntwort(_body({"serverTime": markt.clock.now_ms()}))

        class Opener:
            def open(self, req, timeout=None):
                pfad = urlsplit(req.full_url).path
                if pfad == "/api/v3/klines":
                    return klines(req.full_url)
                if pfad == "/api/v3/time":
                    return zeit(req.full_url)
                raise AssertionError(f"unerwarteter Pfad: {pfad}")

        return BinanceClient(BASIS, opener=Opener())


def _cfg(tmp_path: Path) -> Config:
    return Config(Decimal("10000"), 10, 2, 50, tmp_path, TOKEN)


def _neustart(conn, markt: Markt, cfg: Config) -> poller.PollerContext:
    """Ein Prozessneustart: neuer Kontext gegen DIESELBE Datenbank."""
    return poller.build_context(conn, cfg, SPECS, clock=markt.clock, client=markt.client())


def _fahre(pc, markt: Markt, kerzen: dict, fehler=frozenset()):
    """Eine Runde. `kerzen` bildet Symbol -> Roh-Kline ab; ein Symbol, das
    dort fehlt, liefert eine LEERE Antwort, eines in `fehler` einen Netzfehler."""
    markt.kerzen = dict(kerzen)
    markt.fehler = set(fehler)
    markt.clock.set(max(k[6] for k in kerzen.values()) + 30_000)
    return poller.poll_once(pc)


# --------------------------------------------------------------------------
# Kursverlauf. Die Luecke close(n) -> open(n+1) ist beim Kauf 0, damit die
# Kassenmarge von BuyAndHold (0,3 %) nicht zum Nebenschauplatz wird.
# --------------------------------------------------------------------------
BTC = {0: _kerze(0, "99900.00", "100000.00"),   # prev is None -> kein Kauf
       1: _kerze(1, "100000.00", "100200.00")}  # ref=100000 -> Kauf zu open
BNB = {0: _kerze(0, "600.00", "601.00"),
       1: _kerze(1, "601.00", "602.00"),
       2: _kerze(2, "602.00", "603.00")}        # die Runde OHNE BTC-Kerze


def _aufbau(tmp_path, dateiname="aitra.db"):
    """Gemeinsamer Vorlauf: Datenbank, zwei Runden mit beiden Symbolen, danach
    haelt der Benchmark eine BTC-Position -- und dann ein echter Neustart."""
    markt, cfg = Markt(), _cfg(tmp_path)
    conn = db.connect(tmp_path / dateiname)
    db.migrate(conn)

    pc1 = _neustart(conn, markt, cfg)
    _fahre(pc1, markt, {BENCH: BTC[0], ANDERES: BNB[0]})
    assert store_run.get_fills(conn, BENCH_RUN) == [], (
        "Prueffläche: auf der ersten Kerze darf nichts gekauft werden (prev is None)"
    )
    _fahre(pc1, markt, {BENCH: BTC[1], ANDERES: BNB[1]})

    fills = store_run.get_fills(conn, BENCH_RUN)
    assert len(fills) == 1, (
        "Prueffläche: der Benchmark MUSS vor dem Neustart genau einmal gekauft haben, "
        f"sonst haelt er keine Position und mark() koennte gar nicht werfen. Gemessen: "
        f"{[(f['side'], f['symbol'], str(f['qty'])) for f in fills]}"
    )
    pos = store_run.get_positions(conn, BENCH_RUN)[BENCH]
    assert pos["qty"] > 0, f"Prueffläche: gehaltene Menge {pos['qty']} ist nicht > 0"
    return conn, markt, cfg, fills, pos


def _erwartete_bench_equity(fills, pos) -> Decimal:
    """Ohne frischen BTC-Preis bewertet der Benchmark seine Position mit dem
    letzten bekannten Kurs -- das ist der Fuellpreis (= avg_price, Buy & Hold
    kauft genau einmal). Plus die Kasse aus dem Journal."""
    return pos["qty"] * pos["avg_price"] + fills[-1]["cash_after"]


def _pruefe_runde_war_wirklich_ohne_bench_kerze(conn, ergebnis):
    """Prueffläche (3) und (4): nachgewiesen an der Kerzentabelle, nicht am
    Testdouble. Ohne diese Zusicherungen koennte der Test gruen sein, ohne den
    fraglichen Pfad ueberhaupt betreten zu haben."""
    btc = store.get_candles(conn, BENCH, "15m", limit=100)
    bnb = store.get_candles(conn, ANDERES, "15m", limit=100)
    assert [k.open_time for k in btc] == [BTC[0][0], BTC[1][0]], (
        "Prueffläche: in der Pruefrunde darf KEINE neue BTC-Kerze angekommen sein. "
        f"Gespeichert: {[k.open_time for k in btc]}, erwartet {[BTC[0][0], BTC[1][0]]}"
    )
    assert [k.open_time for k in bnb] == [BNB[0][0], BNB[1][0], BNB[2][0]], (
        "Prueffläche: das ANDERE Symbol muss in der Pruefrunde geliefert haben, sonst "
        f"bleibt neue_kerzen leer. Gespeichert: {[k.open_time for k in bnb]}"
    )
    assert ergebnis.staleness.status == "ok", (
        f"Prueffläche: der Kerzenausfall darf die Veraltet-Erkennung nicht ausloesen, "
        f"sonst prueft der Test einen anderen Fall. Gemessen: {ergebnis.staleness}"
    )


@pytest.mark.parametrize("ausfallart,fehler", [
    ("leere Antwort", frozenset()),
    ("Netzfehler", frozenset({BENCH})),
])
def test_r1_zyklus_ueberlebt_ausbleibende_benchmark_kerze(tmp_path, ausfallart, fehler):
    """Der Kern von R1: nach einem Neustart mit gehaltener Benchmark-Position
    bleibt die BTC-Kerze aus, waehrend BNB liefert. Der Zyklus darf NICHT
    werfen, und die Benchmark-Equity muss einen plausiblen Wert liefern.

    Beide Ausfallarten laufen durch denselben Zweig in poll_once()
    (`continue`, nicht `return`) und landen in derselben Luecke -- geprueft
    werden beide, weil die eine eine leere Antwort ist und die andere eine
    gefangene BinanceError."""
    conn, markt, cfg, fills, pos = _aufbau(tmp_path)

    # --- Neustart: ledger.restore() laesst last_marks leer (E-010/Weg A) ----
    pc2 = _neustart(conn, markt, cfg)
    assert pc2.bench is not None and pc2.bench.bought is True, (
        "Prueffläche: der Benchmark muss seinen Kauf aus dem Journal kennen (B-D5)"
    )
    assert pc2.bench._ctx.ledger.position(BENCH).qty == pos["qty"], (
        "Prueffläche: die Position muss den Neustart ueberlebt haben, sonst gibt es "
        "nichts zu bewerten und mark() koennte nicht werfen"
    )
    assert pc2.last_candle == {}, "Prueffläche: der Neustart muss das Gedaechtnis leeren"

    # --- Die Pruefrunde: BTC bleibt aus, BNB liefert ------------------------
    try:
        ergebnis = _fahre(pc2, markt, {ANDERES: BNB[2]}, fehler=fehler)
    except Exception as e:  # noqa: BLE001 -- genau das ist der Befund
        raise AssertionError(
            f"R1: der Poll-Zyklus ist gescheitert ({ausfallart}), weil die Kerze des "
            f"Benchmark-Symbols {BENCH} ausblieb, waehrend {ANDERES} lieferte:\n"
            f"    {type(e).__name__}: {e}\n\n"
            "Nach einem Neustart ist last_marks des Benchmark-Ledgers leer "
            "(Ledger.restore(), E-010/Weg A), und poll_once() reicht nur die Preise "
            "der Symbole durch, die in dieser Runde geliefert haben. Fehlen beide "
            "Ergaenzungen (benchmark.py:100 Seed nach restore(), benchmark.py:156 "
            "equity() ergaenzt aus last_marks), hat mark() keinen Preis fuer die "
            "gehaltene Position.\n"
            "run_forever() faengt das zwar ab, aber der GANZE Zyklus ist verloren: "
            "kein Equity-Punkt, keine Fills, nur POLL_CYCLE_EXCEPTION und Backoff. "
            "Ein einzelner fehlgeschlagener Kerzenabruf legt damit die Buchung lahm."
        ) from e

    _pruefe_runde_war_wirklich_ohne_bench_kerze(conn, ergebnis)

    kurve = store_run.get_equity_curve(conn, "live")
    assert kurve, "Prueffläche: die Equity-Kurve darf nicht leer sein"
    letzter = kurve[-1]
    assert letzter["ts_ms"] == BNB[2][6], (
        f"Prueffläche: der letzte Punkt muss aus der Pruefrunde stammen, gemessen "
        f"ts_ms={letzter['ts_ms']}, erwartet {BNB[2][6]} (close_time der BNB-Kerze)"
    )

    gemessen = letzter["benchmark_equity"]
    erwartet = _erwartete_bench_equity(fills, pos)
    assert gemessen is not None, (
        "benchmark_equity ist None, obwohl der Benchmark eine Position haelt -- der "
        "Vergleichsgegner faellt in genau der Runde aus der Kurve, in der seine Kerze "
        "fehlte. Alpha waere an dieser Stelle nicht berechenbar."
    )
    assert abs(gemessen - erwartet) < Decimal("0.01"), (
        f"Benchmark-Equity ohne frische Kerze: gemessen {gemessen} USDC, erwartet "
        f"{erwartet} USDC (= {pos['qty']} BTC zum letzten bekannten Kurs "
        f"{pos['avg_price']} + Kasse {fills[-1]['cash_after']}). Ohne frischen Preis "
        "MUSS der letzte bekannte gelten -- weder 0 noch die blosse Kasse."
    )
    # Plausibilitaet: die Position darf nicht stillschweigend aus der Equity
    # fallen (dann blieben nur ~45 USDC Kasse stehen -- ein Scheinverlust).
    assert gemessen > cfg.starting_balance * Decimal("0.9"), (
        f"Benchmark-Equity {gemessen} USDC liegt unter 90 % des Startkapitals "
        f"({cfg.starting_balance}), obwohl der Kurs seit dem Kauf nicht gefallen ist. "
        f"Die gehaltene Position ({pos['qty']} BTC) ist offenbar unbewertet aus der "
        f"Equity gefallen; uebrig bliebe nur die Kasse ({fills[-1]['cash_after']})."
    )


def test_r1_api_equity_curve_zeigt_den_punkt_der_kerzenlosen_runde(tmp_path):
    """Zweite Schicht: der Weg bis zum Nutzer.

    Ein Waechter, der nur poll_once() prueft, sieht nicht, ob der Punkt je
    beim Auftraggeber ankommt. Hier laeuft derselbe Ausfall, und geprueft wird
    GET /api/equity-curve -- derselbe Endpunkt, den das Dashboard liest."""
    conn, markt, cfg, fills, pos = _aufbau(tmp_path)
    pc2 = _neustart(conn, markt, cfg)
    ergebnis = _fahre(pc2, markt, {ANDERES: BNB[2]})
    _pruefe_runde_war_wirklich_ohne_bench_kerze(conn, ergebnis)
    conn.commit()

    client = create_app(cfg).test_client()
    r = client.get("/api/equity-curve?run_id=live")
    assert r.status_code == 200, f"HTTP {r.status_code}"
    punkte = r.get_json()["points"]
    assert len(punkte) >= 1, (
        "Prueffläche leer: /api/equity-curve liefert keinen einzigen Punkt -- eine "
        "Schleife darueber waere immer gruen."
    )
    letzter = punkte[-1]
    assert letzter["ts_ms"] == BNB[2][6], (
        f"Prueffläche: der juengste Punkt muss aus der kerzenlosen Runde stammen, "
        f"gemessen {letzter['ts_ms']}, erwartet {BNB[2][6]}"
    )
    assert letzter["benchmark"] is not None, (
        "GET /api/equity-curve liefert benchmark=None fuer die Runde, in der die "
        "Benchmark-Kerze ausblieb -- im Dashboard reisst die Vergleichslinie ab."
    )
    erwartet = _erwartete_bench_equity(fills, pos)
    assert abs(money.from_text(letzter["benchmark"]) - erwartet) < Decimal("0.01"), (
        f"benchmark={letzter['benchmark']} USDC aus der API, erwartet {erwartet} USDC"
    )
    assert isinstance(letzter["benchmark"], str), "Geld als String (E-007)"
