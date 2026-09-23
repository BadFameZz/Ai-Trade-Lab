# app/tests/test_benchmark_neustart_b_d5.py
"""B-D5: BuyAndHold fuehrt seinen Zustand nur im Arbeitsspeicher.

Der Befund (gemessen auf CT 107 am 2026-09-23, zwei Neustarts, danach zwei
BUY-Fills unter run_id='bench-live' aus je vollen 10.000 USDC):

  benchmark.py:73   self._bought = False   -- nie persistiert, nie wiederhergestellt
  benchmark.py:64   Ledger(starting_cash=cfg.starting_balance, ...)  -- nie restore()
  poller.py:215     build_context() baut bei JEDEM Prozessstart ein neues BuyAndHold

Das widerspricht dem Docstring von build_context() selbst (poller.py:192-194,
A-14): "ein neu gestarteter Prozess kennt seine Positionen nur aus
fills/positions, nie aus dem Speicher." Der Live-Ledger direkt darueber haelt
sich daran (poller.py:200-204, ledger.restore(positions, cash)), der Benchmark
eine Zeile darunter nicht.

Warum das schwer wiegt: Der Maßstab fuer Teilprojekt D ist "Alpha gegen
BTC-Buy-&-Hold" (Entscheidung 4, docs/recherche/2026-09-22-grundlagen-lernender
-agent.md). Setzt der Vergleichsgegner bei jedem Neustart auf 10.000 zurueck,
ist Alpha nach dem ersten Neustart bedeutungslos.

PRUEFFLAECHE (bewusst, B-D1-Lehre): jeder Test hier faehrt MEHRERE Kerzen mit
UNTERSCHIEDLICHEN Preisen und MINDESTENS EINEN Neustart, und jeder Test sichert
vorher ab, dass der erste Kauf ueberhaupt stattgefunden hat. Ein Test mit einer
Kerze oder ohne Neustart ist fuer genau diesen Fehler blind.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from aitra import db, money, poller, store_run
from aitra.binance import BinanceClient
from aitra.config import Config
from aitra.marketdata import SimClock

BASIS = "https://api.binance.com"
SYMBOL = "BTCUSDC"
SPECS = {SYMBOL: money.BUILTIN_SPECS[SYMBOL]}
BENCH_RUN = "bench-live"
INTERVAL_MS = 900_000
T0 = 900_000_000  # beliebiger, auf 15m ausgerichteter Startzeitpunkt


# --------------------------------------------------------------------------
# Testdoubles (gleiche Bauart wie in test_poller.py, hier eigenstaendig, damit
# diese Datei keine fremde Datei anfasst)
# --------------------------------------------------------------------------
class FakeAntwort:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            d = self._body[self._pos:]
            self._pos = len(self._body)
            return d
        d = self._body[self._pos:self._pos + n]
        self._pos += len(d)
        return d

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


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


def _kerze(index: int, open_price: str, close_price: str) -> list:
    """Eine Roh-Kline (12 Felder, Spec 2.2). open/close werden ausdruecklich
    getrennt gesetzt: ref_price ist prev.close, gefuellt wird zu candle.open."""
    open_time = T0 + index * INTERVAL_MS
    close_time = open_time + INTERVAL_MS - 1
    hoch = max(Decimal(open_price), Decimal(close_price))
    tief = min(Decimal(open_price), Decimal(close_price))
    return [open_time, open_price, str(hoch), str(tief), close_price, "12.345",
            close_time, "1", 1, "0", "0", "0"]


class Markt:
    """Liefert genau die Kerze, die gerade an der Reihe ist, und haelt die Uhr
    30 s hinter deren close_time -- damit ist nichts veraltet (Datenalter 30 s,
    Uhrversatz 0) und der Kill Switch bleibt aus."""

    def __init__(self) -> None:
        self.clock = SimClock(T0)
        self.kerze: list | None = None

    def client(self) -> BinanceClient:
        def klines(url):
            return FakeAntwort(_body([] if self.kerze is None else [self.kerze]))

        def zeit(url):
            return FakeAntwort(_body({"serverTime": self.clock.now_ms()}))

        return BinanceClient(BASIS, opener=FakeOpener(
            {"/api/v3/klines": klines, "/api/v3/time": zeit}))


def _cfg(tmp_path: Path) -> Config:
    return Config(Decimal("10000"), 10, 2, 50, tmp_path, "x" * 32)


def _neustart(conn, markt: Markt, cfg: Config) -> poller.PollerContext:
    """Ein Prozessneustart: neuer Kontext gegen DIESELBE Datenbank."""
    return poller.build_context(conn, cfg, SPECS, clock=markt.clock, client=markt.client())


def _fahre(pc: poller.PollerContext, markt: Markt, kerze: list) -> None:
    """Eine Kerze durch den Poller schicken."""
    markt.kerze = kerze
    markt.clock.set(kerze[6] + 30_000)
    poller.poll_once(pc)


def _fills(conn) -> list[dict]:
    return store_run.get_fills(conn, BENCH_RUN)


def _zeige(fills) -> str:
    return "; ".join(
        f"#{i + 1} {f['side']} {f['symbol']} Kerze={f['candle_open_time']} "
        f"Preis={f['price']} qty={f['qty']} cash_after={f['cash_after']}"
        for i, f in enumerate(fills)
    ) or "(keine)"


# --------------------------------------------------------------------------
# Der Kursverlauf. Zwischen Kerze 2 und Kerze 3 halbiert sich der Kurs -- genau
# dort liegt der Neustart. Die Luecke close(n) -> open(n+1) ist an jeder
# Kaufstelle 0, damit die Kassenmarge von BuyAndHold (0,3 %) nicht zum
# Nebenschauplatz wird.
# --------------------------------------------------------------------------
K1 = _kerze(0, "99900.00", "100000.00")   # erste Kerze: prev is None, kein Kauf
K2 = _kerze(1, "100000.00", "100200.00")  # ref=100000 -> Kauf zu open=100000
K3 = _kerze(2, "50000.00", "50000.00")    # nach dem Neustart: prev is None
K4 = _kerze(3, "50000.00", "50100.00")    # ref=50000  -> hier schlaegt B-D5 zu
K5 = _kerze(4, "50100.00", "50200.00")
K6 = _kerze(5, "50200.00", "50300.00")


def _aufbau(tmp_path):
    """Gemeinsamer Vorlauf: Datenbank, erster Kontext, erster (echter) Kauf."""
    markt = Markt()
    cfg = _cfg(tmp_path)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)

    pc1 = _neustart(conn, markt, cfg)
    _fahre(pc1, markt, K1)
    assert _fills(conn) == [], "Prueffläche: auf der ersten Kerze darf nichts gekauft werden"
    _fahre(pc1, markt, K2)

    fills = _fills(conn)
    assert len(fills) == 1, (
        "Prueffläche: der eine ECHTE Kauf muss vor dem Neustart stattgefunden haben, "
        f"sonst prueft dieser Test nichts. Gemessen: {_zeige(fills)}"
    )
    assert pc1.bench is not None and pc1.bench.bought is True
    return conn, markt, cfg, fills


def test_bd5_benchmark_kauft_nach_jedem_neustart_erneut(tmp_path):
    """Kern von B-D5: ueber den GESAMTEN Lauf darf es genau EINEN bench-live-Fill
    geben, egal wie oft build_context() den Kontext neu baut.

    Zwei Neustarts, je zwei Kerzen danach (die erste Kerze nach einem Neustart
    hat prev is None -- ohne die zweite waere der Test blind)."""
    conn, markt, cfg, _ = _aufbau(tmp_path)

    # --- Neustart 1 -------------------------------------------------------
    pc2 = _neustart(conn, markt, cfg)
    assert pc2.last_candle == {}, "Prueffläche: der Neustart muss das Gedaechtnis leeren"
    _fahre(pc2, markt, K3)
    _fahre(pc2, markt, K4)

    # --- Neustart 2 -------------------------------------------------------
    pc3 = _neustart(conn, markt, cfg)
    _fahre(pc3, markt, K5)
    _fahre(pc3, markt, K6)

    fills = _fills(conn)
    assert len(fills) == 1, (
        f"BuyAndHold hat nach den Neustarts erneut gekauft: {len(fills)} Fills unter "
        f"run_id='{BENCH_RUN}' statt 1. {_zeige(fills)}"
    )


def test_bd5_benchmark_ledger_wird_nach_neustart_aus_dem_journal_rekonstruiert(tmp_path):
    """Analog zu poller.py:200-204 (Live-Ledger): Kasse nach dem Neustart MUSS
    fills[-1]['cash_after'] sein, nicht cfg.starting_balance, und die Position
    muss aus positions kommen."""
    conn, markt, cfg, fills = _aufbau(tmp_path)

    kasse_journal = fills[-1]["cash_after"]
    pos_journal = store_run.get_positions(conn, BENCH_RUN)[SYMBOL]
    assert pos_journal["qty"] > 0, "Prueffläche: das Journal muss eine Position tragen"
    assert kasse_journal < cfg.starting_balance - Decimal("9000"), (
        "Prueffläche: die Journalkasse muss sich deutlich vom Startkapital unterscheiden, "
        f"sonst unterscheidet der Test die beiden Deutungen nicht. Gemessen: "
        f"{kasse_journal} vs. {cfg.starting_balance}"
    )

    # --- Neustart ---------------------------------------------------------
    pc2 = _neustart(conn, markt, cfg)
    ledger = pc2.bench._ctx.ledger

    assert ledger.cash == kasse_journal, (
        "Das Benchmark-Ledger faengt nach dem Neustart beim Startkapital an, statt die "
        f"Kasse aus dem Journal zu uebernehmen: gemessen {ledger.cash}, erwartet "
        f"{kasse_journal} (Startkapital waere {cfg.starting_balance})"
    )
    assert ledger.position(SYMBOL).qty == pos_journal["qty"], (
        f"Position nach dem Neustart: gemessen {ledger.position(SYMBOL).qty}, "
        f"erwartet {pos_journal['qty']} aus positions"
    )
    assert pc2.bench.bought is True, (
        "BuyAndHold.bought ist nach dem Neustart False, obwohl ein Fill im Journal steht "
        f"({_zeige(fills)}) -- der naechste Kerzenpaar-Durchlauf kauft erneut."
    )


def test_bd5_benchmark_equity_folgt_dem_kurs_seit_dem_urspruenglichen_kauf(tmp_path):
    """Die Folge, die den Auftraggeber trifft (Alpha gegen BTC-Buy-&-Hold).

    Nach dem Neustart halbiert sich der Kurs (100.000 -> 50.000). Die beiden
    Deutungen liegen dadurch rund 4.987 USDC auseinander:

      richtig (Position aus dem Journal, gekauft bei ~100.050):  ~5.017 USDC
      falsch  (frische 10.000, neu gekauft bei 50.000):         ~10.005 USDC

    Geprueft wird nicht die Funktion, sondern der Weg zum Nutzer: der Wert, der
    als equity_curve.benchmark_equity fuer run_id='live' gespeichert wird und
    ueber GET /api/equity-curve bzw. dashboard.build_status() beim Auftraggeber
    ankommt."""
    conn, markt, cfg, fills = _aufbau(tmp_path)

    kasse_journal = fills[-1]["cash_after"]
    qty_journal = store_run.get_positions(conn, BENCH_RUN)[SYMBOL]["qty"]
    mark_preis = Decimal(K4[4])  # 50.100,00 -- Schlusskurs der letzten Kerze

    erwartet = qty_journal * mark_preis + kasse_journal
    abstand = cfg.starting_balance - erwartet
    assert abstand > Decimal("4000"), (
        "Prueffläche: der Kursverlauf muss die beiden Deutungen deutlich trennen. "
        f"Gemessen: aus dem Journal {erwartet} USDC gegen ~{cfg.starting_balance} USDC "
        f"bei einem Neustart-Kauf, Abstand nur {abstand} USDC"
    )

    # --- Neustart ---------------------------------------------------------
    pc2 = _neustart(conn, markt, cfg)
    _fahre(pc2, markt, K3)
    _fahre(pc2, markt, K4)

    kurve = store_run.get_equity_curve(conn, "live")
    assert kurve, "Prueffläche: die Equity-Kurve darf nicht leer sein"
    letzter = kurve[-1]
    assert letzter["ts_ms"] == K4[6], (
        f"Prueffläche: der letzte Punkt muss von der letzten Kerze stammen, "
        f"gemessen ts_ms={letzter['ts_ms']}, erwartet {K4[6]}"
    )
    gemessen = letzter["benchmark_equity"]
    assert gemessen is not None, "Prueffläche: benchmark_equity darf nicht None sein"

    assert abs(gemessen - erwartet) < Decimal("1"), (
        "Die Benchmark-Equity nach dem Neustart spiegelt nicht die Kursbewegung seit dem "
        f"urspruenglichen Kauf wider: gemessen {gemessen} USDC, erwartet {erwartet} USDC "
        f"(= {qty_journal} BTC aus dem Journal * {mark_preis} + {kasse_journal} Kasse). "
        f"Differenz {gemessen - erwartet} USDC -- der Benchmark hat bei rund "
        f"{cfg.starting_balance} USDC neu angefangen, obwohl sich der Kurs seit seinem "
        "Kauf halbiert hat. Alpha gegen diesen Vergleichsgegner ist damit bedeutungslos."
    )


def test_bd5_kein_geistervorschlag_je_neustart_im_journal(tmp_path):
    """Zweite Schicht: jeder Kaufversuch schreibt vorher eine decisions-Zeile
    (execute.execute_proposal() ruft db.add_decision() VOR jeder Pruefung).

    Kennt BuyAndHold seinen Kauf aus dem Journal, schlaegt es gar keinen
    zweiten Kauf mehr vor -- und das Journal bleibt bei genau einer
    bench-live-Entscheidung. Bewusst streng: 'bought' ist aus fills exakt
    ableitbar (A-14), ein erneuter Vorschlag ist deshalb kein Grenzfall,
    sondern Zustand aus dem Speicher."""
    conn, markt, cfg, _ = _aufbau(tmp_path)

    vorher = conn.execute("SELECT COUNT(*) c FROM decisions WHERE run_id = ?",
                          (BENCH_RUN,)).fetchone()["c"]
    assert vorher == 1, f"Prueffläche: genau ein Vorschlag vor dem Neustart, gemessen {vorher}"

    pc2 = _neustart(conn, markt, cfg)
    _fahre(pc2, markt, K3)
    _fahre(pc2, markt, K4)
    pc3 = _neustart(conn, markt, cfg)
    _fahre(pc3, markt, K5)
    _fahre(pc3, markt, K6)

    nachher = conn.execute("SELECT COUNT(*) c FROM decisions WHERE run_id = ?",
                           (BENCH_RUN,)).fetchone()["c"]
    zeilen = conn.execute(
        "SELECT id, action, approved, risk_code FROM decisions WHERE run_id = ? ORDER BY id",
        (BENCH_RUN,)).fetchall()
    assert nachher == 1, (
        f"Das Journal waechst um eine Geister-Entscheidung je Neustart: {nachher} Zeilen "
        f"unter run_id='{BENCH_RUN}' statt 1. "
        + "; ".join(f"id={r['id']} {r['action']} approved={r['approved']} {r['risk_code']}"
                    for r in zeilen)
    )
