"""Der Zustand eines Live-Poller-Laufs und was ein Zyklus darueber festschreibt.

Herausgeloest aus poller.py im Gesamtreview A2: mit den Behebungen B-1, B-3
und V-1/V-3/V-4/V-6/V-7 lag poller.py bei 349 Zeilen, ueber der harten
300er-Marke aus Spec 3.1b. Die Naht verlaeuft dort, wo poller.py schon immer
zwei Dinge tat: den Zyklus FAHREN (poll_once, build_context, run_forever,
start - dort geblieben) und den Betriebszustand FESTSTELLEN und
FESTSCHREIBEN (dieses Modul).

Hier steht nichts Netzbezogenes und nichts Buchendes: check_staleness()
bewertet, apply_staleness() und tageswechsel() schreiben in `state` bzw.
`events`. Beides ist ohne Thread, ohne Netz und ohne Uhr direkt pruefbar.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import db, money
from .benchmark import BuyAndHold
from .binance import BinanceClient
from .config import Config
from .execute import ExecutionContext
from .marketdata import Clock, Staleness, interval_seconds, staleness
from .replay import _utc_date as utc_tag


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


def apply_staleness(pc: PollerContext, st: Staleness) -> None:
    """Setzt den Kill Switch bei 'stale' und loggt NUR beim Uebergang - sonst
    spammt jeder weitere Poll dasselbe Ereignis, solange der Zustand anhaelt
    (Spec 8.2: MARKET_DATA_LAGGING hoechstens einmal pro 15 min)."""
    db.set_state(pc.conn, "market_data_status", st.status)
    db.set_state(pc.conn, "market_data_age_s", str(st.data_age_s))
    if st.status == "stale":
        # V-3 (Gesamtreview A2): die Sicherung faellt bei JEDEM Zyklus, solange
        # der Zustand anhaelt - frueher stand set_state() innerhalb des
        # `if not pc.was_stale`, und ein manuelles Release liess den Poller den
        # Kill Switch nie wieder setzen, obwohl die Stoerung weiterlief. Eine
        # Sicherung, die sich durch einen Klick dauerhaft abschalten laesst,
        # waehrend die Gefahr anhaelt, ist keine. Gedrosselt wird nur das
        # EREIGNIS (Spec 8.2), nicht die Sicherung.
        db.set_state(pc.conn, "kill_switch", "1")
        if not pc.was_stale:
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


def tageswechsel(pc: PollerContext, ts_ms: int, equity) -> None:
    """Setzt sod_equity/sod_date an der UTC-Tagesgrenze (Spec 9.1, E-008).

    V-1 (Gesamtreview A2): sod_equity wurde nirgends im Paket geschrieben, nur
    mit Vorgabe starting_balance gelesen - die Tagesverlustgrenze war damit
    eine Grenze SEIT INBETRIEBNAHME. Fiel die Equity einmal 2 % unter das
    Startkapital, lehnte die Engine dauerhaft jeden Kauf ab, und
    /api/risk/check setzte zusaetzlich den Kill Switch. replay.py machte es
    richtig (E-008) - live und Zeitraffer liefen auseinander, obwohl Spec 9.2
    identisches Verhalten zusagt.

    Der Tag kommt aus replay._utc_date (hier als utc_tag importiert), nicht aus
    einer eigenen Zeile: dieselbe Regel, nicht eine zweite Auslegung davon.
    Bezugszeit ist - wie im Zeitraffer - die close_time der Kerze, nicht die
    Wanduhr."""
    tag = utc_tag(ts_ms).isoformat()
    if db.get_state(pc.conn, "sod_date") == tag:
        return
    db.set_state(pc.conn, "sod_date", tag)
    db.set_state(pc.conn, "sod_equity", money.to_text(equity))
    db.log_event(pc.conn, "POLLER", "INFO", "START_OF_DAY",
                 f"{tag}, sod_equity={money.to_text(equity)}")


