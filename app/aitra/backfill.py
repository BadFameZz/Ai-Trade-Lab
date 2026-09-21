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
