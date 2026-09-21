"""Live-Betrieb: ein Poll-Zyklus pro Aufruf, Backoff bei Fehlern, Kerzen
persistieren, Veraltet-Erkennung -> Kill Switch, schwebende Vorschlaege
ausfuehren, Equity-Schnappschuesse (Spec 9.1, 13 Ansatz 1, E-005, E-006).

poll_once() bekommt die Uhr ueber PollerContext.clock (Clock-Protokoll) und
ruft niemals time.time() selbst auf: Tests starten und stoppen den Poller
deterministisch, ohne auf Wanduhrzeit zu warten (A2-Randbedingung). Nur
start()/run_forever() erzeugen einen echten Thread und eine echte Wartezeit;
sie laufen in keinem Test mit echtem Netz oder echter Uhr.

server_time() wird hoechstens alle 15 Minuten erneut abgefragt (Spec 11.3) -
sonst waere der Uhrversatz zwar aktueller, aber das Ratenbudget (A-17c)
verdoppelt sich naeherungsweise ohne Nutzen.

run_forever() faengt JEDE Ausnahme aus poll_once() ab (nicht nur BinanceError):
ein Fehler ausserhalb des Netzcodes (z. B. ein Bug in der Bewertung) darf den
Thread nicht ersatzlos beenden, sonst liefe die Veraltet-Erkennung selbst still
aus, ohne dass sie je den Kill Switch ausloesen konnte.
"""
from __future__ import annotations

import logging
import threading
from typing import Mapping

from . import db, execute, money, store, store_run
from .benchmark import BuyAndHold
from .binance import BinanceClient, BinanceError, BinanceRateLimited
from .config import Config
from .execute import ExecutionContext
from .ledger import Ledger, Position
from .marketdata import Candle, Clock, WallClock, projizierte_serverzeit
from .pollstate import (PollerContext, PollOutcome, apply_staleness,  # noqa: F401
                         check_staleness, tageswechsel)
from .risk import RiskEngine

log = logging.getLogger("aitra.poller")

BACKOFF_STEPS_S = (60, 120, 240, 600)  # Spec 8.3, Deckel 600 s
_TIME_CHECK_INTERVAL_MS = 900_000       # alle 15 min server_time() (Spec 11.3)


def _to_row(k: Candle, source: str = "binance") -> store.CandleRow:
    return store.CandleRow(symbol=k.symbol, interval=k.interval, open_time=k.open_time,
                            close_time=k.close_time, open=k.open, high=k.high, low=k.low,
                            close=k.close, volume=k.volume, source=source, fetched_at=db.now())


def _backoff(consecutive_failures: int) -> float:
    if consecutive_failures <= 0:
        return 0.0
    idx = min(consecutive_failures - 1, len(BACKOFF_STEPS_S) - 1)
    return float(BACKOFF_STEPS_S[idx])


def poll_once(pc: PollerContext) -> PollOutcome:
    """Ein Poll-Zyklus. Wirft nie wegen eines Netzfehlers: jede binance.py-
    Ausnahme wird gefangen, protokolliert, und fuehrt zu Backoff (Spec 8.3).
    Gepollt wird nur, wofuer eine SymbolSpec vorliegt (pc.ctx.specs) - nicht
    blind ueber cfg.market_symbols, das mehr Symbole nennen kann, als der
    Aufrufer mit Specs versorgt hat (siehe build_context())."""
    pc.ctx.kill_switch = db.get_state(pc.conn, "kill_switch", "0") == "1"

    need_time = (pc.last_time_check_ms is None
                 or pc.clock.now_ms() - pc.last_time_check_ms >= _TIME_CHECK_INTERVAL_MS)
    zeit_fehlgeschlagen = False
    if need_time:
        try:
            # N-2: Die Reihenfolge dieser ZWEI Zeilen ist tragend, nicht
            # kosmetisch. Die Antwort traegt die Serverzeit aus dem Moment
            # ihrer Erzeugung; die lokale Marke wird gesetzt, wenn sie
            # ANGEKOMMEN ist. projizierte_serverzeit() liegt damit immer genau
            # eine RUECKlaufzeit HINTER der wahren Serverzeit - unabhaengig
            # davon, wie weit die Containeruhr absolut danebenliegt (der echte
            # Uhrversatz kuerzt sich exakt heraus). Genau diese Marge macht es
            # unbedenklich, dieselbe Zahl als klines-Grenze zu benutzen, wo
            # close_time >= server_time_ms die laufende Kerze verwirft.
            # Vertauscht laeuft die Projektion der Serverzeit um die
            # HINlaufzeit VORAUS, und eine unfertige Kerze kann als
            # geschlossen durchgehen. Vertauscht gemessen: 279 passed, kein
            # Test schlug an - deshalb
            # test_n2_projizierte_serverzeit_ueberschreitet_die_wahre_serverzeit_nie.
            pc.last_server_time_ms = pc.client.server_time()
            pc.last_time_check_ms = pc.clock.now_ms()
        except BinanceError as e:
            db.log_event(pc.conn, "POLLER", "WARN", "MARKET_DATA_FETCH_FAILED", type(e).__name__)
            # V-4 (Gesamtreview A2): hier stand ein `return` VOR
            # apply_staleness(). Nach einem Neustart ist last_time_check_ms
            # None, also versuchte jeder Zyklus die Abfrage und kehrte frueh
            # zurueck - gemessen: nach 5 Stunden ohne eine einzige Kerze
            # meldete /api/health HTTP 200, "healthy", market_data: ok, Kill
            # Switch aus. Dieselbe Klasse, die Fixrunde 1 fuer
            # POLL_CYCLE_EXCEPTION geschlossen hat. Jetzt wird
            # weitergelaufen; staleness() behandelt server_time_ms=None
            # sauber (data_age_s = inf -> stale).
            zeit_fehlgeschlagen = True
    # B-1: NIE der rohe Cache-Wert (siehe marketdata.projizierte_serverzeit).
    server_time_ms = projizierte_serverzeit(pc.clock, pc.last_server_time_ms,
                                             pc.last_time_check_ms)

    neue_kerzen: dict[str, Candle] = {}
    fehlgeschlagen = zeit_fehlgeschlagen or server_time_ms is None
    # Ohne Serverzeit keine klines: BinanceClient verwirft die laufende Kerze
    # ueber close_time >= server_time_ms und braucht dafuer eine Zahl (Spec 8.1).
    for symbol in (pc.ctx.specs if server_time_ms is not None else ()):
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
            # V-6 (Gesamtreview A2): nur WIRKLICH neue Kerzen. klines(limit=2)
            # gibt jeden Zyklus dieselbe geschlossene Kerze zurueck; "neu" war
            # damit jede Antwort, und es entstand ein Equity-Punkt je Poll
            # statt je Kerze (gemessen: 14 Punkte bei 1 Kerze in 15 min,
            # hochgerechnet 525.600 statt 35.040 im Jahr).
            if pc.last_close_time.get(symbol) != neueste.close_time:
                neue_kerzen[symbol] = neueste
            pc.last_close_time[symbol] = neueste.close_time
    pc.consecutive_failures = pc.consecutive_failures + 1 if fehlgeschlagen else 0

    latest_close = min(pc.last_close_time.values()) if pc.last_close_time else None
    st = check_staleness(pc.cfg, pc.clock, latest_close, server_time_ms)
    apply_staleness(pc, st)
    # B-3 (Blocker, Gesamtreview A2): das Flag oben stammt vom ANFANG des
    # Zyklus. apply_staleness() hat den Kill Switch gerade eben in der
    # Datenbank setzen koennen; ohne dieses erneute Lesen buchte
    # resolve_pending() unmittelbar danach gegen das veraltete Flag im
    # Speicher - gemessen: 798,64 USDC auf einer Kerze, die der Poller in
    # derselben Zeile als veraltet erkannt hatte.
    pc.ctx.kill_switch = db.get_state(pc.conn, "kill_switch", "0") == "1"

    # E-006: der Verfall lief bisher nur als Nebenwirkung von
    # resolve_pending(), das wegen der Dauer-"neuen" Kerze jeden Zyklus
    # aufgerufen wurde. Seit V-6 kann neue_kerzen leer bleiben - der Verfall
    # braucht deshalb einen eigenen, unbedingten Aufruf je Zyklus.
    execute.expire_stale_pending(pc.ctx)
    fills = []
    for symbol, candle in neue_kerzen.items():
        fills.extend(execute.resolve_pending(pc.ctx, candle))

    if neue_kerzen:
        marks = {**pc.ctx.ledger.last_marks, **{s: c.close for s, c in neue_kerzen.items()}}
        # V-6: die close_time der Kerze, nicht die Wanduhr - damit traegt der
        # Punkt die Zeit, zu der er gilt, und der Primaerschluessel
        # (run_id, ts_ms) schuetzt gegen Doppelschreibung.
        ts_ms = max(c.close_time for c in neue_kerzen.values())
        v = pc.ctx.ledger.mark(marks, ts_ms=ts_ms)
        tageswechsel(pc, ts_ms, v.equity)  # V-1, Spec 9.1/E-008
        bench_equity = None
        if pc.bench is not None:
            # Nur die eigene Kerze des Benchmark-Symbols speisen (siehe
            # Docstring): BuyAndHold ist auf genau ein Symbol fest verdrahtet
            # (self._symbol) und wuerde sonst mit dem Preis eines anderen
            # Symbols gefuellt, wenn mehrere Symbole in derselben Runde neue
            # Kerzen liefern.
            bench_candle = neue_kerzen.get(pc.cfg.benchmark_symbol)
            if bench_candle is not None:
                pc.bench.on_candle(bench_candle, pc.last_candle.get(pc.cfg.benchmark_symbol))
            bench_equity = pc.bench.equity(marks, ts_ms=ts_ms)
        for symbol, candle in neue_kerzen.items():
            pc.last_candle[symbol] = candle
        store_run.append_equity_points(pc.conn, [store_run.EquityPoint(
            run_id="live", ts_ms=ts_ms, equity=v.equity, cash=v.cash,
            benchmark_equity=bench_equity, exposure_pct=v.exposure_pct,
        )])
        # V-7: A-16 ist laut Spec "der Waechter, der im Alltag arbeitet" -
        # prune_candles() hatte im Produktivcode bis hierher genau einen
        # Treffer: seine eigene Definition. Ohne diesen Aufruf ist
        # CANDLE_RETENTION_DAYS ein Bedienelement ohne Wirkung und der
        # Plattenverbrauch unbeschraenkt.
        for symbol in neue_kerzen:
            store.prune_candles(pc.conn, symbol, pc.cfg.market_interval,
                                 pc.cfg.candle_retention_days)

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
    Fehlern. Endet, sobald stop_event gesetzt ist.

    Faengt bewusst JEDE Ausnahme, nicht nur BinanceError: poll_once() ist der
    einzige Ort, an dem auch ein Programmierfehler auftreten koennte (z. B.
    ein Tippfehler wie pc.ctx.ledgar statt pc.ctx.ledger), und ein Thread, der
    dabei stirbt, nimmt die Veraltet-Erkennung mit sich - genau der Fehler,
    den binance.py in Fixrunde 1 fuer den Netzcode bereits behoben hat (siehe
    Modul-Docstring). Das ist eine BEWUSSTE Entscheidung, keine Nachlaessigkeit:
    ein toter Poller ist schlimmer als ein fehlerhafter, der wenigstens die
    Chance hat, sich beim naechsten Zyklus zu erholen. Diesen except-Zweig
    spaeter auf `except BinanceError` zu verengen, wuerde genau diesen Schutz
    wieder entfernen.

    Fixrunde 1 (Reviewer-Befund): dieser Zweig schrieb ursprünglich NUR nach
    stderr (log.exception) - anders als jeder Binance-Fehlerpfad in
    poll_once(), der ein db.log_event() hinterlaesst. Ein Fehler VOR der
    Veraltet-Pruefung (z. B. der oben genannte Tippfehler) fror
    market_data_status/kill_switch damit auf ihrem letzten Wert ein, OHNE dass
    irgendwo in der Datenbank sichtbar wurde, dass der Poller ueberhaupt in
    Backoff haengt: das Dashboard zeigt weiter 'ok', obwohl seit Stunden keine
    Kerze mehr ankommt - die Veraltet-Erkennung, die genau das bemerken soll,
    wird von diesem Fehlerpfad lahmgelegt. Jetzt wird ein
    POLL_CYCLE_EXCEPTION-Ereignis geschrieben, mit dem Ausnahmetyp (nie der
    vollen Meldung - die kann Antwortinhalte tragen, E-005)."""
    while not stop_event.is_set():
        try:
            outcome = poll_once(pc)
            wait_s = outcome.backoff_s if outcome.backoff_s > 0 else pc.cfg.market_poll_s
        except Exception as e:
            pc.consecutive_failures += 1
            log.exception("poll_once() unerwarteter Fehler - Thread bleibt am Leben (Backoff)")
            try:
                db.log_event(pc.conn, "POLLER", "ERROR", "POLL_CYCLE_EXCEPTION", type(e).__name__)
            except Exception:
                pass  # die DB-Verbindung selbst koennte der Grund fuer den Fehler sein
            wait_s = _backoff(pc.consecutive_failures)
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
