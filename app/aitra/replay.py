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
import uuid
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
    """Ergebnis eines vollstaendigen Zeitraffer-Laufs (A-9/A-12)."""
    run_id: str
    final_equity: Decimal
    benchmark_final_equity: Decimal
    fills: list[Fill]
    kill_switch_engagements: int
    decisions: int


def _utc_date(ms: int):
    """Der UTC-Kalendertag einer Kerze; Grundlage fuer den Tagesreset (E-008)."""
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
    """Spult candles vor: decide_fn sieht nie die Fuellkerze (A-7), gebucht wird
    ausschliesslich ueber execute_proposal() (A-6b). Kill Switch loest sich an der
    UTC-Tagesgrenze (E-008, einzige Abweichung vom Livebetrieb). Ohne conn: :memory:."""
    if len(candles) < 2:
        raise ValueError("run_replay() braucht mindestens zwei Kerzen (Entscheidung + Fuellung)")

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
        if tag != kill_switch_tag:  # E-008: Kill Switch loest sich an der UTC-Tagesgrenze
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
    """Laedt geschlossene Kerzen im Zeitraum [from_ms, to_ms] aus der DB (A-8, SqliteSource-Pfad)."""
    rows = store.get_candles(conn, symbol, interval, start_ms=from_ms, end_ms=to_ms, limit=1_000_000)
    return [
        Candle(symbol=r.symbol, interval=r.interval, open_time=r.open_time, close_time=r.close_time,
               open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume, closed=True)
        for r in rows
    ]


def _wait_fn(history: Sequence[Candle]) -> Proposal:
    """CLI-Vorgabestrategie: immer WAIT. Echte decide_fn kommen von B/C."""
    return Proposal(symbol=history[-1].symbol, action="WAIT")


def _takt_fn(history: Sequence[Candle]) -> Proposal:
    """Eingebaute, deterministisch handelnde CLI-Strategie (A-8c).

    Kauft bei jeder 40. Entscheidung 1 % des Kapitals, sonst WAIT. Haengt
    ausschliesslich an der Laenge der Historie -- nicht an Preisen, nicht an
    einer Uhr, nicht an Zufall. Zweck: Der Prozessgrenzen-Vergleich in A-8c
    braucht Laeufe mit echten Fills. Mit _wait_fn erzeugt jeder CLI-Lauf null
    Fills, und verglichen wuerden zweimal sha256("[]") -- ein Hash, der auch
    dann gleich bliebe, wenn die Engine ueber Prozessgrenzen hinweg beliebig
    nichtdeterministisch fuellte.
    """
    if len(history) % 40 == 0:
        return Proposal(symbol=history[-1].symbol, action="BUY", position_pct=1)
    return Proposal(symbol=history[-1].symbol, action="WAIT")


STRATEGIEN: dict[str, Callable[[Sequence[Candle]], Proposal]] = {
    "wait": _wait_fn,
    "takt": _takt_fn,
}


def _hash_fills(fills: list[Fill]) -> str:
    """Deterministischer Fingerabdruck aller Fills, prozess- und seedunabhaengig (A-8c)."""
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
    p.add_argument("--strategie", choices=sorted(STRATEGIEN), default="wait",
                   help="eingebaute Strategie: 'wait' (nie handeln) oder 'takt' "
                        "(jede 40. Entscheidung 1 %% BUY, deterministisch, A-8c)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI-Einstiegspunkt: einziger Weg, einen Replay-Lauf zu starten (kein HTTP-Endpunkt)."""
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

    # uuid4-Suffix: runs.run_id ist PRIMARY KEY; _hash_fills haengt nur von den Fills ab (A-8c).
    run_id = f"replay-{args.from_}-{args.to}-{uuid.uuid4().hex[:12]}"
    result = run_replay(
        candles, STRATEGIEN[args.strategie], cfg, {args.symbol: spec}, fee_bps=10.0, slippage_bps=5.0,
        benchmark_symbol=args.symbol, run_id=run_id, conn=conn,
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
