from __future__ import annotations

import hashlib
import itertools
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

from test_execute import _install_apply_guard

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
    buy_positionen: list[int] = []
    # Unabhaengiger Aufrufzaehler: er zaehlt, zum wievielten Mal decide_fn
    # aufgerufen wurde, und wird NICHT aus len(history) oder aus einem Feld der
    # Kerzen abgeleitet. Nur so ist die Fill-Pruefung unten eine echte Messung.
    zaehler = itertools.count(1)

    def decide_fn(history):
        pos = next(zaehler)
        aufrufe.append((history[-1].open_time, len(history)))
        if pos % 500 == 0:
            buy_positionen.append(pos)
            return Proposal("BTCUSDC", "BUY", position_pct=1)
        return Proposal("BTCUSDC", "WAIT")

    result = run_replay(candles, decide_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                         benchmark_symbol="BTCUSDC", run_id="test-a7")

    assert len(aufrufe) == 8639
    # Wichtig: die Erwartung kommt aus der Aufrufreihenfolge (pos), nicht aus
    # len(history) selbst -- sonst waere die Pruefung tautologisch und wuerde
    # jeden Blick in die Zukunft durchwinken (die urspruengliche Fassung aus dem
    # Plan-Brief hat genau das getan: t = len(history) neu bestimmt und damit
    # gegen sich selbst statt gegen eine unabhaengige Referenz geprueft; siehe
    # Rot-Nachweis im Bericht).
    for pos, (open_time, hist_len) in enumerate(aufrufe, start=1):
        assert hist_len == pos, f"history hat {hist_len} Kerzen bei Aufruf {pos}, erwartet genau {pos}"
        assert open_time == candles[pos - 1].open_time
        assert open_time == (pos - 1) * 900_000

    # Fix-Welle (Review-Befund 1): die frueher hier stehende Pruefung leitete den
    # Index der Entscheidungskerze aus f.candle_open_time ab
    # (f.candle_open_time // 900_000 - 1) und verglich ihn danach gegen genau diese
    # Zahl -- eine Tautologie, die fuer jeden Wert wahr ist. Ein Look-ahead
    # (next_candle=candles[t-1] statt candles[t]) waere gruen durchgelaufen.
    # Jetzt kommt die Erwartung aus den unabhaengig mitgeschriebenen
    # Aufrufpositionen der BUYs: ein BUY, der beim p-ten Aufruf von decide_fn
    # beschlossen wurde, sieht candles[:p] und MUSS auf candles[p] fuellen --
    # also open_time == p * 900_000 (E-006).
    assert len(buy_positionen) == 17  # hergeleitet: 8.639 Aufrufe, jeder 500. -> 8639 // 500
    assert len(result.fills) == len(buy_positionen)  # Pruefflaeche: jeder BUY muss fuellen
    assert [f.candle_open_time for f in result.fills] == [p * 900_000 for p in buy_positionen]


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


@pytest.mark.slow
def test_a9_tempo_35040_kerzen_realistische_handelsfrequenz():
    """A-9-Ergaenzung (Fix-Runde 1, Coordinator-Befund): der obige Tempotest handelt
    nur bei 0,1 % der Kerzen (t % 1000 == 0) und misst damit ueberwiegend das reine
    Protokollieren von WAIT-Entscheidungen, nicht die volle Kette aus RiskEngine.check(),
    sizing.size_order() und Ledger.apply(). Hier wird bei jeder siebten Kerze (~14,3 %,
    im geforderten Fuenf-bis-Zehn-Kerzen-Rhythmus) ein echter Orderversuch ausgeloest,
    abwechselnd BUY und SELL, damit auch der Verkaufspfad und die Positionsfuehrung unter
    Last stehen. CFG (max_position_pct=10, max_total_exposure_pct=50) bleibt real -- mit
    wachsender Position entstehen dabei auch echte MAX_EXPOSURE-Ablehnungen, keine
    Kunstwelt ohne Risikoschranken.

    BUY 4 % / SELL 2 % (statt z. B. SELL 50 %) ist bewusst so gewaehlt, dass die
    RiskEngine-SELL-Pruefung (position_pct darf hoechstens die tatsaechlich gehaltene
    Positionsgroesse in Prozent sein, B-1) nach einem erfolgreichen Kauf fast immer
    durchgeht -- eine erste Messung mit SELL 50 % scheiterte fast vollstaendig an
    NO_POSITION (gemessen: nur 10 von 5005 Versuchen kamen durch), weil 4-%-Kaeufe
    niemals 50 % Bestand aufbauen. Das war kein tauglicher Lasttest, siehe Bericht."""
    import time
    candles = _candles(35_040)

    def decide_fn(history):
        t = len(history)
        if t % 7 == 0:
            if (t // 7) % 2 == 0:
                return Proposal("BTCUSDC", "BUY", position_pct=4)
            return Proposal("BTCUSDC", "SELL", position_pct=2)
        return Proposal("BTCUSDC", "WAIT")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    start = time.perf_counter()
    result = run_replay(candles, decide_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                         benchmark_symbol="BTCUSDC", run_id="test-a9-realistisch", conn=conn)
    elapsed = time.perf_counter() - start

    buys = [f for f in result.fills if f.side == "BUY"]
    sells = [f for f in result.fills if f.side == "SELL"]
    assert result.decisions == 35_039
    assert len(buys) > 500  # Pruefflaeche: der Kaufpfad muss echt unter Last stehen
    assert len(sells) > 500  # Pruefflaeche: der Verkaufspfad muss echt unter Last stehen
    assert elapsed < 40.0  # dieselbe Container-Schwelle wie A-9, jetzt unter realistischer Last


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


def test_a6b_replay_laufzeit_waechter_laesst_run_replay_durch():
    """A-6b auch im Zeitraffer: run_replay() darf Ledger.apply() nur ueber
    execute_proposal() ausloesen (execute.py), niemals direkt. Der Laufzeit-
    Waechter aus test_execute.py wird hier zusaetzlich gegen run_replay()
    gehalten -- ein direkter replay.py-Aufruf von Ledger.apply() wuerde den
    Assert im Waechter zum Platzen bringen, bevor der erste Fill entsteht."""
    candles = _candles(300)

    def decide_fn(history):
        t = len(history)
        return Proposal("BTCUSDC", "BUY", position_pct=1) if t % 40 == 0 else Proposal("BTCUSDC", "WAIT")

    with _install_apply_guard():
        result = run_replay(candles, decide_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                             benchmark_symbol="BTCUSDC", run_id="test-a6b-replay")
    assert len(result.fills) > 0  # Pruefflaeche: der Waechter muss echte Buchungen sehen


def test_final_equity_und_benchmark_von_hand_nachgerechnet():
    """Coordinator-Befund (Fix-Runde 1): final_equity/benchmark_final_equity wurden von
    keinem Test angefasst -- das ist die Zahl, um die es im ganzen Teilprojekt geht
    (Strategie gegen Buy & Hold). Drei Kerzen, fee_bps=slippage_bps=0 (damit die Fill-
    Preise exakt den ref_price-Vorgaben entsprechen und keine Rundungskette entsteht),
    permissive Risikogrenzen (100/100/100), damit nichts abgelehnt wird.

    Kerzen (BTCUSDC, alle OHLC gleich, flach): K0=100.00, K1=100.00, K2=121.00.

    Strategie: BUY 50 % bei t=1 (history=[K0]), danach WAIT.
    - Vor dem Kauf: cash=10000, keine Position -> equity=10000.
    - target_quote = 10000 * 50/100 = 5000.
    - ref_price (Entscheidungskerze K0.close) = 100.00, s=f=0 -> exec_price(sizing)=100.00.
    - raw_qty = 5000 / 100.00 = 50; step_size=0.00001 -> qty=50 (schon glatt).
    - Fuellung auf K1.open (E-006) = 100.00 (keine Luecke zu K0.close): gross=50*100=5000,
      fee=0, cash_after = 10000 - 5000 = 5000, Position 50 BTC @ avg_price 100.00.
    - t=2: WAIT, keine Aenderung.
    - Endbewertung auf K2.close=121.00: final_equity = cash(5000) + 50*121 = 5000+6050
      = 11050.

    Benchmark (BuyAndHold, eigenes Ledger, dieselbe Startkasse 10000): margin_pct =
    2*(fee_bps+slippage_bps)/100 = 0 -> position_pct=100 %.
    - Erster Versuch bei on_candle(K1, K0): equity=10000 (keine Position), ref_price=
      K0.close=100.00, target_quote=10000*100/100=10000, exec_price(sizing)=100.00,
      raw_qty=10000/100=100 (schon glatt).
    - Fuellung auf K1.open=100.00 (keine Luecke): gross=100*100=10000, fee=0,
      cost=10000 <= cash(10000) (nicht groesser, geht durch) -> cash_after=0,
      Position 100 BTC @ avg_price 100.00. bought=True, kein zweiter Versuch bei K2.
    - Endbewertung auf K2.close=121.00: benchmark_final_equity = cash(0) + 100*121
      = 12100.

    Erwartungsgemaess schlaegt Buy & Hold (voll investiert) die 50-%-Strategie bei einem
    reinen Aufwaertstrend -- genau die Vergleichsgroesse, um die es in der Spec geht."""
    candles = [
        Candle(symbol="BTCUSDC", interval="15m", open_time=0, close_time=899_999,
               open=Decimal("100.00"), high=Decimal("100.00"), low=Decimal("100.00"),
               close=Decimal("100.00"), volume=Decimal("1"), closed=True),
        Candle(symbol="BTCUSDC", interval="15m", open_time=900_000, close_time=1_799_999,
               open=Decimal("100.00"), high=Decimal("100.00"), low=Decimal("100.00"),
               close=Decimal("100.00"), volume=Decimal("1"), closed=True),
        Candle(symbol="BTCUSDC", interval="15m", open_time=1_800_000, close_time=2_699_999,
               open=Decimal("121.00"), high=Decimal("121.00"), low=Decimal("121.00"),
               close=Decimal("121.00"), volume=Decimal("1"), closed=True),
    ]

    def decide_fn(history):
        if len(history) == 1:
            return Proposal("BTCUSDC", "BUY", position_pct=50)
        return Proposal("BTCUSDC", "WAIT")

    permissive_cfg = Config(Decimal("10000"), 100, 100, 100, Path("/tmp"), "x" * 32)
    result = run_replay(candles, decide_fn, permissive_cfg, SPECS, fee_bps=0.0, slippage_bps=0.0,
                         benchmark_symbol="BTCUSDC", run_id="test-equity-handrechnung")

    assert len(result.fills) == 1  # nur der eine BUY der Strategie
    assert result.final_equity == Decimal("11050")
    assert result.benchmark_final_equity == Decimal("12100")


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
