from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import tracemalloc
from decimal import Decimal
from pathlib import Path

import pytest

from aitra import db, money, store, store_run
from aitra.config import Config
from aitra.execute import ExecutionContext, execute_proposal
from aitra.ledger import Ledger
from aitra.marketdata import Candle, ListSource, SimClock, SqliteSource
from aitra.replay import ReplayResult, _iso_ms, run_replay
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
    """A-8c ueber Prozessgrenzen, mit echten Fills (Fix-Welle, Review-Befund 2).

    Vorher fuhr dieser Test die CLI mit der fest verdrahteten Vorgabestrategie
    _wait_fn. Jeder Unterprozess erzeugte damit null Fills, und verglichen wurden
    zweimal sha256("[]") == "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    (nachgemessen). Dieser Vergleich bliebe auch dann gruen, wenn die Engine
    ueber Prozessgrenzen hinweg beliebig nichtdeterministisch fuellte.
    Jetzt faehrt die CLI mit --strategie takt (eingebaut, deterministisch,
    7 Fills bei 300 Kerzen), und der Leerlauf-Hash ist ausdruecklich
    ausgeschlossen."""
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
             "--from", from_iso, "--to", to_iso, "--db", str(tmp_path / "seed.db"),
             "--strategie", "takt"],
            cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True,
            env={**os.environ, "PYTHONHASHSEED": seed, "DATA_DIR": str(tmp_path), "ADMIN_TOKEN": "x" * 32},
        )
        assert out.returncode == 0, out.stderr
        # Mindestsicherung: der Lauf darf nicht heimlich leer sein.
        assert out.stdout.strip() != _hash_fills([]), (
            f"CLI-Lauf erzeugte null Fills -- verglichen wuerde nur sha256('[]'); stderr={out.stderr}"
        )
        assert "Fills=7 " in out.stderr, out.stderr  # 299 Entscheidungen, jede 40. -> 299 // 40
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


@pytest.mark.slow
def test_a9_tempo_35040_kerzen_dateibasierte_db(tmp_path):
    """A-9 gegen eine DATEI, nicht gegen :memory: (Koordinatoren-Entscheidung).

    Die beiden anderen A-9-Messungen laufen gegen :memory:, wo ein Commit
    praktisch nichts kostet. Ein Trainingslauf in Teilprojekt C benutzt aber
    eine dateibasierte Datenbank, und die Zahl aus A-9 soll dessen
    Rechenbudget tragen -- eine Messung gegen :memory: beantwortet diese Frage
    nicht.

    Gemessen wurde vor dem Sammelschreiben 12,86 s bei 116.421 Commits
    (2.715 Entsch./s), danach 6,49 s bei 38.848 Commits (5.398 Entsch./s).
    Die Schwelle bleibt unveraendert bei 40,0 s bzw. 876 Entscheidungen/s.
    """
    import time
    candles = _candles(35_040)

    def decide_fn(history):
        t = len(history)
        if t % 7 == 0:
            if (t // 7) % 2 == 0:
                return Proposal("BTCUSDC", "BUY", position_pct=4)
            return Proposal("BTCUSDC", "SELL", position_pct=2)
        return Proposal("BTCUSDC", "WAIT")

    conn = db.connect(tmp_path / "tempo.db")
    db.migrate(conn)
    start = time.perf_counter()
    result = run_replay(candles, decide_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                         benchmark_symbol="BTCUSDC", run_id="test-a9-datei", conn=conn)
    elapsed = time.perf_counter() - start

    assert result.decisions == 35_039
    assert len(result.fills) > 500  # Pruefflaeche: es wird wirklich geschrieben
    # Die Kurve ist vollstaendig auf der Platte gelandet, nicht nur im Puffer.
    assert len(store_run.get_equity_curve(conn, "test-a9-datei", limit=100_000)) == 35_039
    assert elapsed < 40.0, f"{elapsed:.2f} s fuer 35.039 Entscheidungen = {35_039/elapsed:.0f}/s"
    assert (tmp_path / "tempo.db").stat().st_size > 0


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
    assert result.benchmark_bought is True  # sonst waere 12100 nur das Startkapital
    assert result.final_equity == Decimal("11050")
    assert result.benchmark_final_equity == Decimal("12100")


# 2024-01-01T00:00:00Z in ms -- A-12 verlangt Kerzen aus dem Kalenderjahr 2024,
# also Daten, die im Livebetrieb laengst als veraltet gelten wuerden.
_2024_START_MS = 1_704_067_200_000


def _candles_2024(n: int) -> list[Candle]:
    """Wie _candles(), aber mit Zeitstempeln aus dem Kalenderjahr 2024 (A-12)."""
    return [
        Candle(symbol=c.symbol, interval=c.interval,
               open_time=_2024_START_MS + c.open_time,
               close_time=_2024_START_MS + c.close_time,
               open=c.open, high=c.high, low=c.low, close=c.close,
               volume=c.volume, closed=True)
        for c in _candles(n)
    ]


def test_a12_zeitraffer_nutzt_nur_simclock():
    """A-12, Fix-Welle (Review-Befund 9).

    Vorher prueften hier nur zwei Abwesenheiten von Zeichenketten. Ein solcher
    Test bestuende auch bei leerer Datei -- er misst kein Verhalten. Ergaenzt
    sind deshalb eine positive Kontrolle (die Datei enthaelt wirklich SimClock,
    der Pfad stimmt also und die Datei ist nicht leer) und ein echter Lauf ueber
    Kerzen aus dem Kalenderjahr 2024.
    """
    text = Path(__file__).resolve().parent.parent.joinpath("aitra", "replay.py").read_text()
    assert "SimClock" in text  # positive Kontrolle: die Datei existiert und ist nicht leer
    assert "WallClock" not in text
    assert "staleness(" not in text

    candles = _candles_2024(800)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    def decide_fn(history):
        return (Proposal("BTCUSDC", "BUY", position_pct=1) if len(history) % 50 == 0
                else Proposal("BTCUSDC", "WAIT"))

    result = run_replay(candles, decide_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                         benchmark_symbol="BTCUSDC", run_id="test-a12-2024", conn=conn)

    # Ueber ein Jahr alte Kerzen loesen im Zeitraffer nichts aus, und der Lauf
    # handelt tatsaechlich -- ein stillstehender Lauf waere keine Messung.
    assert result.kill_switch_engagements == 0
    assert len(result.fills) > 0
    assert result.benchmark_bought is True
    stale = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE event = 'MARKET_DATA_STALE'"
    ).fetchone()["c"]
    assert stale == 0


@pytest.mark.slow
def test_a12_voller_jahreslauf_2024_loest_nie_veraltet_aus():
    """A-12 in der von der Spec geforderten Groesse: 35.040 Kerzen aus dem
    Kalenderjahr 2024. Als `slow` gefuehrt, weil build.sh ein Budget von 12 s
    hat (dieselbe Begruendung wie bei A-9)."""
    candles = _candles_2024(35_040)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    def decide_fn(history):
        return (Proposal("BTCUSDC", "BUY", position_pct=1) if len(history) % 1000 == 0
                else Proposal("BTCUSDC", "WAIT"))

    result = run_replay(candles, decide_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                         benchmark_symbol="BTCUSDC", run_id="test-a12-jahr", conn=conn)
    assert result.decisions == 35_039
    assert result.kill_switch_engagements == 0
    assert len(result.fills) > 0
    assert conn.execute(
        "SELECT COUNT(*) c FROM events WHERE event = 'MARKET_DATA_STALE'"
    ).fetchone()["c"] == 0


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
    store_run.create_run(conn, "live-sim", "live", db.now(), "0.3.0")
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


def test_replay_schreibt_equity_kurve_und_schliesst_beide_laeufe_ab():
    """Fix-Welle, Review-Befund 7: store_run.append_equity_point() und
    store_run.finish_run() hatten im gesamten Produktivcode keinen Aufrufer,
    obwohl Spec 4.4 ("Fills und Equity-Kurve unter eigener run_id") und 9.2
    ("equity_punkt anhaengen") beides verlangen. Jeder Lauf blieb in
    runs.finished_at fuer immer offen."""
    candles = _candles(50)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    def decide_fn(history):
        return (Proposal("BTCUSDC", "BUY", position_pct=1) if len(history) % 10 == 0
                else Proposal("BTCUSDC", "WAIT"))

    result = run_replay(candles, decide_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                         benchmark_symbol="BTCUSDC", run_id="test-kurve", conn=conn)
    assert len(result.fills) > 0  # Pruefflaeche: die Kurve muss sich bewegen koennen

    kurve = store_run.get_equity_curve(conn, "test-kurve", limit=10_000)
    # Ein Punkt je Schleifendurchlauf, also je Entscheidungskerze candles[0..n-2].
    assert len(kurve) == len(candles) - 1 == result.decisions
    assert [punkt["ts_ms"] for punkt in kurve] == [c.close_time for c in candles[:-1]]
    assert all(punkt["benchmark_equity"] is not None for punkt in kurve)

    # Die Kurve steht nicht still: nach dem ersten Kauf sinkt die Kasse und die
    # Position traegt Risiko.
    assert kurve[0]["cash"] == Decimal("10000")
    assert kurve[0]["exposure_pct"] == 0.0
    assert kurve[-1]["cash"] < Decimal("10000")
    assert kurve[-1]["exposure_pct"] > 0.0
    # Geld steht auch hier als TEXT in der Datenbank (E-007/A-13).
    typen = conn.execute(
        "SELECT DISTINCT typeof(equity), typeof(cash), typeof(benchmark_equity) FROM equity_curve"
    ).fetchall()
    assert [tuple(r) for r in typen] == [("text", "text", "text")]

    # Beide Laeufe sind abgeschlossen (Spec 9.2).
    laeufe = {r["run_id"]: r["finished_at"]
              for r in conn.execute("SELECT run_id, finished_at FROM runs").fetchall()}
    assert set(laeufe) == {"test-kurve", "bench-test-kurve"}
    assert all(wert is not None for wert in laeufe.values()), laeufe


def test_benchmark_bought_meldet_den_nie_ausgefuehrten_kauf():
    """Fix-Welle, Review-Befund 8: Schlaegt der Kauf an der Kassenmarge fehl,
    bleibt `bought` False und equity() liefert unveraendert das Startkapital.
    Jede Alpha-Zahl saehe dann glaenzend aus -- die Strategie "schlaegt" einen
    Vergleich, der nie stattgefunden hat. benchmark_bought macht das sichtbar.

    Aufbau: jede Kerze eroeffnet 5 % ueber dem Schluss der Vorgaengerkerze.
    Die Kassenmarge von BuyAndHold ist 2*(fee_bps+slippage_bps)/100 = 0,3 %
    und reicht dafuer an keiner einzigen Kerze."""
    preise = [Decimal("100"), Decimal("105"), Decimal("110.25"), Decimal("115.7625")]
    candles = [
        Candle(symbol="BTCUSDC", interval="15m", open_time=i * 900_000,
               close_time=i * 900_000 + 899_999, open=p, high=p, low=p, close=p,
               volume=Decimal("1"), closed=True)
        for i, p in enumerate(preise)
    ]
    result = run_replay(candles, _wait_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                         benchmark_symbol="BTCUSDC", run_id="test-bench-nie-gekauft")

    assert result.benchmark_bought is False
    # Genau die Falle: der Benchmark steht unveraendert auf dem Startkapital.
    assert result.benchmark_final_equity == Decimal("10000")

    # Gegenprobe: ohne Luecke zwischen Schluss- und Eroeffnungskurs kauft er.
    flach = [
        Candle(symbol="BTCUSDC", interval="15m", open_time=i * 900_000,
               close_time=i * 900_000 + 899_999, open=Decimal("100"), high=Decimal("100"),
               low=Decimal("100"), close=Decimal("100"), volume=Decimal("1"), closed=True)
        for i in range(4)
    ]
    ok = run_replay(flach, _wait_fn, CFG, SPECS, fee_bps=10.0, slippage_bps=5.0,
                     benchmark_symbol="BTCUSDC", run_id="test-bench-gekauft")
    assert ok.benchmark_bought is True
    assert ok.benchmark_final_equity < Decimal("10000")  # Gebuehr und Slippage kosten


def test_cli_bericht_nennt_benchmark_gekauft(tmp_path):
    """Der CLI-Bericht muss die Zahl mitliefern, sonst bliebe der nie
    ausgefuehrte Benchmark-Kauf im Betrieb unsichtbar (Review-Befund 8)."""
    candles = _candles(60)
    conn = db.connect(tmp_path / "cli2.db")
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
         "--from", "1970-01-01T00:00:00+00:00", "--to", "1970-01-02T00:00:00+00:00",
         "--db", str(tmp_path / "cli2.db"), "--strategie", "takt"],
        cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True,
        env={**os.environ, "DATA_DIR": str(tmp_path), "ADMIN_TOKEN": "x" * 32},
    )
    assert out.returncode == 0, out.stderr
    assert "Benchmark-gekauft=ja" in out.stderr, out.stderr


def test_iso_ms_nimmt_ohne_zeitzone_utc_an(monkeypatch):
    """Fix-Welle, Review-Befund 10: nackte Datumsangaben wurden als Lokalzeit
    gelesen. Diese Pruefung ist von der Zeitzone des Rechners unabhaengig --
    sie vergleicht die nackte Form direkt mit der ausdruecklichen UTC-Form.

    Der Container laeuft in UTC: dort haelt jede der fuenf Zusicherungen
    auch mit der alten, fehlerhaften Fassung (datetime.fromisoformat() ohne
    tzinfo, .timestamp() in Lokalzeit == UTC == keine Verschiebung). Die
    Zeitzone wird deshalb hier ausdruecklich auf Europe/Berlin gesetzt, statt
    sich auf die Umgebung des CI-Laufs zu verlassen -- sonst ist dieser Test
    nur unter einer zufaellig passenden Rechnerzeitzone ein Rot-Nachweis.
    """
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    try:
        assert _iso_ms("1970-01-01") == 0
        assert _iso_ms("1970-01-01") == _iso_ms("1970-01-01T00:00:00+00:00")
        assert _iso_ms("2025-01-01") == _iso_ms("2025-01-01T00:00:00+00:00")
        assert _iso_ms("2025-07-01") == _iso_ms("2025-07-01T00:00:00+00:00")  # Sommerzeit
        # Eine ausdrueckliche Zeitzone wird weiterhin respektiert.
        assert _iso_ms("2025-01-01T00:00:00+01:00") == _iso_ms("2025-01-01") - 3_600_000
    finally:
        # monkeypatch.undo() sofort statt am Fixture-Teardown: tzset() muss
        # danach laufen, damit der Prozess fuer nachfolgende Tests wieder in
        # der urspruenglichen Zeitzone steht.
        monkeypatch.undo()
        time.tzset()


def test_cli_nackte_datumsangaben_sind_utc_unabhaengig_von_der_rechnerzeitzone(tmp_path):
    """Derselbe Befehl muss auf zwei Rechnern dieselbe Kerzenmenge und
    denselben Hash liefern (A-8c). Gemessen unter Europe/Berlin ergibt
    '1970-01-01' als Lokalzeit -3.600.000 ms statt 0 -- vier Kerzen Unterschied
    im hier gewaehlten Fenster."""
    candles = _candles(300)
    conn = db.connect(tmp_path / "tz.db")
    db.migrate(conn)
    store.upsert_candles(conn, [
        store.CandleRow(symbol=c.symbol, interval=c.interval, open_time=c.open_time,
                         close_time=c.close_time, open=c.open, high=c.high, low=c.low,
                         close=c.close, volume=c.volume, source="fixture",
                         fetched_at="2026-01-01T00:00:00Z")
        for c in candles
    ])
    conn.close()

    def lauf(tz: str, von: str, bis: str):
        out = subprocess.run(
            [sys.executable, "-m", "aitra.replay", "--symbol", "BTCUSDC", "--interval", "15m",
             "--from", von, "--to", bis, "--db", str(tmp_path / "tz.db"), "--strategie", "takt"],
            cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True,
            env={**os.environ, "TZ": tz, "DATA_DIR": str(tmp_path), "ADMIN_TOKEN": "x" * 32},
        )
        assert out.returncode == 0, out.stderr
        kerzen = re.search(r"Kerzen=(\d+)", out.stderr)
        assert kerzen is not None, out.stderr
        return out.stdout.strip(), int(kerzen.group(1))

    utc = lauf("UTC", "1970-01-01", "1970-01-02")
    berlin = lauf("Europe/Berlin", "1970-01-01", "1970-01-02")
    ausdruecklich = lauf("UTC", "1970-01-01T00:00:00+00:00", "1970-01-02T00:00:00+00:00")

    assert utc == berlin, f"Zeitzone des Rechners aendert das Ergebnis: UTC={utc}, Berlin={berlin}"
    assert utc == ausdruecklich  # nackt und ausdruecklich UTC sind dasselbe
    assert utc[1] == 97  # hergeleitet: open_time 0 .. 86_400_000 bei 900_000 ms Schritt
    assert utc[0] != _hash_fills([])  # Mindestsicherung: der Lauf war nicht leer


def test_run_replay_lehnt_abweichenden_benchmark_symbol_frueh_ab():
    """run_replay() ist einsymbolig (Docstring): decide_fn, execute_proposal und
    der Benchmark laufen alle auf candles[0].symbol. Weicht benchmark_symbol
    davon ab, kauft BuyAndHold eine Position unter dem falschen Schluessel,
    und der erste run_replay-interne mark()-Aufruf, dem fuer diesen Schluessel
    kein Preis mitgegeben wird, wirft tief aus ledger.py -- weit weg von der
    eigentlichen Aufrufstelle, die den Denkfehler gemacht hat. run_replay()
    muss den Widerspruch selbst und sofort melden."""
    specs = {"BTCUSDC": money.BUILTIN_SPECS["BTCUSDC"], "BNBUSDC": money.BUILTIN_SPECS["BNBUSDC"]}
    candles = _candles(10)  # alle mit symbol="BTCUSDC"

    with pytest.raises(ValueError, match="benchmark_symbol"):
        run_replay(candles, _wait_fn, CFG, specs, fee_bps=10.0, slippage_bps=5.0,
                   benchmark_symbol="BNBUSDC", run_id="test-benchmark-mismatch")
