# app/tests/test_poller.py
from __future__ import annotations

import json
import threading
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


@pytest.mark.slow
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
            # Serverzeit MUSS zum Fenster u passen (synchron zur jeweils
            # verarbeiteten Kerze, siehe Docstring) - nicht auf das Ende des
            # gesamten Universums fixiert sein. Sonst waechst der Uhrversatz
            # ueber das Jahr auf ~365 Tage und loest den Kill Switch ueber
            # market_clock_skew_kill_s (30s) aus, unabhaengig davon, ob
            # poll_once() korrekt ist - der Rot-Nachweis waere fuer den
            # falschen Grund rot (gemessen, siehe task-5-report.md).
            return FakeAntwort(_body({"serverTime": u[-1][6] + 1}))
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


def _kerze_mit_preisen(open_time: int, interval_s: int, open_: str, close: str) -> list:
    close_time = open_time + interval_s * 1000 - 1
    return [open_time, open_, open_, open_, close, "1.0", close_time, "1", 1, "0", "0", "0"]


def test_bench_bekommt_nur_die_kerze_des_eigenen_symbols(tmp_path):
    """Regression: BuyAndHold ist auf GENAU EIN Symbol fest verdrahtet
    (self._symbol). Werden in derselben Poll-Runde mehrere Symbole neu
    beliefert, darf poll_once() dem Benchmark nur die Kerze SEINES eigenen
    Symbols (cfg.benchmark_symbol) geben - sonst kauft er (mit dem falschen
    Preis) unter dem Namen des Benchmark-Symbols, sobald ein anderes Symbol
    zuerst in der Iteration drankommt.

    specs wird absichtlich mit BNBUSDC ZUERST gebaut (Iterationsreihenfolge
    von pc.ctx.specs folgt der Einfuegereihenfolge), damit ein Fehler, der
    einfach ueber alle neuen Kerzen iteriert, hier auch wirklich sichtbar
    wird: BTCUSDC liegt bei rund 80000, BNBUSDC bei rund 500 - eine
    Verwechslung faellt am Fuellpreis sofort auf.
    """
    specs = {"BNBUSDC": money.BUILTIN_SPECS["BNBUSDC"], "BTCUSDC": money.BUILTIN_SPECS["BTCUSDC"]}
    interval_s = 900
    t0 = 10_000_000_000
    btc1 = _kerze_mit_preisen(t0, interval_s, "80000.00", "80100.00")
    btc2 = _kerze_mit_preisen(t0 + interval_s * 1000, interval_s, "80200.00", "80300.00")
    # BNBUSDC exakt um den Faktor 10 verschoben (gleiche relative Kerzenluecke
    # wie BTCUSDC) - eine Verwechslung waere sonst zufaellig durch die
    # INSUFFICIENT_CASH-Pruefung in Ledger.apply() abgefangen, statt sichtbar
    # zu fuellen (gemessen: mit ungleicher relativer Luecke schlug der erste,
    # falsche Fuellversuch schlicht fehl und der zweite, richtige Versuch
    # rettete den Test - kein wirklicher Rot-Nachweis).
    bnb1 = _kerze_mit_preisen(t0, interval_s, "800000.00", "801000.00")
    bnb2 = _kerze_mit_preisen(t0 + interval_s * 1000, interval_s, "802000.00", "803000.00")
    server_time_ms = bnb2[6] + 1

    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    cfg = _cfg(tmp_path)  # benchmark_symbol Vorgabe ist BTCUSDC
    clock = SimClock(btc1[6] + 1)
    client = _client_fuer({"BTCUSDC": [btc1], "BNBUSDC": [bnb1]}, server_time_ms)
    pc = poller.build_context(conn, cfg, specs, clock=clock, client=client)
    assert list(pc.ctx.specs)[0] == "BNBUSDC", "Pruefflaeche setzt eine bestimmte Reihenfolge voraus"

    poller.poll_once(pc)  # erste Kerze: noch keine Vorgaengerkerze, kein Kauf
    assert pc.bench.bought is False

    clock.set(btc2[6] + 1)
    pc.client = _client_fuer({"BTCUSDC": [btc2], "BNBUSDC": [bnb2]}, server_time_ms)
    poller.poll_once(pc)

    assert pc.bench.bought is True, "Benchmark haette auf der zweiten Kerze kaufen muessen"
    fills = store_run.get_fills(conn, "bench-live")
    assert len(fills) == 1, f"genau ein Fill erwartet, gemessen: {len(fills)}"
    assert fills[0]["symbol"] == "BTCUSDC"
    assert Decimal("70000") < fills[0]["price"] < Decimal("90000"), (
        f"Fuellpreis {fills[0]['price']} liegt nicht im BTCUSDC-Bereich (~80200) - "
        "der Benchmark wurde vermutlich mit der BNBUSDC-Kerze (~800000er Bereich) gefuellt"
    )


def test_run_forever_ueberlebt_einen_fehler_im_poll_zyklus_und_geht_in_backoff(tmp_path, monkeypatch):
    """Falle 2 des Auftrags: eine Ausnahme WAEHREND eines Poll-Zyklus darf den
    Thread nicht ersatzlos beenden. BACKOFF_STEPS_S wird auf 0 gepatcht und
    market_poll_s auf 0 gesetzt, damit kein einziger Wartezyklus in diesem
    Test echte Wanduhrzeit kostet (Falle 1) - threading.Event.wait(0) kehrt
    sofort zurueck, unabhaengig vom Backoff-Wert."""
    monkeypatch.setattr(poller, "BACKOFF_STEPS_S", (0, 0, 0, 0))
    clock = SimClock(10_000_000)
    kerzen = {"BTCUSDC": [_kerze(clock.now_ms() - 100 - 900_000, 900)]}
    client = _client_fuer(kerzen, clock.now_ms())
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    cfg = _cfg(tmp_path, market_poll_s=0)
    pc = poller.build_context(conn, cfg, SPECS, clock=clock, client=client)

    calls = {"n": 0}
    stop_event = threading.Event()
    orig_poll_once = poller.poll_once

    def flackernder_poll_once(pc_arg):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("absichtlicher Fehler im Poll-Zyklus (Testdouble)")
        if calls["n"] >= 3:
            stop_event.set()
        return orig_poll_once(pc_arg)

    monkeypatch.setattr(poller, "poll_once", flackernder_poll_once)

    thread = threading.Thread(target=poller.run_forever, args=(pc, stop_event), daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive(), "Thread lief nach stop_event.set() nicht aus (Beweis: joint nicht)"
    assert calls["n"] >= 3, f"Thread ist wohl an der Ausnahme gestorben, nur {calls['n']} Aufrufe"
    # Pruefflaeche gegen den stillen Totalausfall (Fixrunde 1, gemessen):
    # conn wurde im Haupt-Thread erzeugt, poll_once() laeuft im Worker-Thread.
    # Ohne check_same_thread=False in db.connect() wirft JEDER DB-Zugriff dort
    # sqlite3.ProgrammingError, run_forever() faengt das ab und geht in
    # Backoff - der Thread ueberlebt und terminiert wie erwartet, aber es
    # wird nie ein einziger Poll tatsaechlich wirksam. calls["n"] allein haette
    # das NICHT aufgedeckt, weil es nur zaehlt, ob poll_once() aufgerufen
    # wurde, nicht ob es etwas bewirkt hat.
    rows = store.get_candles(conn, "BTCUSDC", "15m", limit=10)
    assert len(rows) >= 1, (
        "Pruefflaeche zu schwach: kein Poll hat im Worker-Thread wirklich Kerzen "
        "gespeichert - vermutlich sqlite3.ProgrammingError (cross-thread), von "
        "run_forever() stillschweigend in Backoff verwandelt"
    )
    assert db.get_state(conn, "kill_switch", "0") == "0"


def test_start_erzeugt_thread_der_nach_stop_event_deterministisch_auslaeuft(tmp_path, monkeypatch):
    """Falle 2, oeffentliche Schnittstelle: start() liefert echten Thread +
    Event; nach stop_event.set() MUSS der Thread auslaufen (join, nicht
    hoffen). BinanceClient/WallClock werden im poller-Modul auf ein
    Testdouble umgebogen (Netz- und Uhr-Grenze), ohne dass ein echtes
    Netzwerk oder eine echte Wartezeit noetig ist."""
    specs = {"BTCUSDC": money.BUILTIN_SPECS["BTCUSDC"], "BNBUSDC": money.BUILTIN_SPECS["BNBUSDC"]}
    clock = SimClock(10_000_000)
    kerzen = {sym: [_kerze(clock.now_ms() - 100 - 900_000, 900)] for sym in specs}
    fake_client = _client_fuer(kerzen, clock.now_ms())
    monkeypatch.setattr(poller, "BinanceClient", lambda *a, **kw: fake_client)
    monkeypatch.setattr(poller, "WallClock", lambda: clock)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    cfg = _cfg(tmp_path, market_symbols=("BTCUSDC", "BNBUSDC"))

    thread, stop_event = poller.start(conn, cfg, specs)
    try:
        assert thread.is_alive() or thread.join(timeout=0) is None
    finally:
        stop_event.set()
        thread.join(timeout=5)

    assert not thread.is_alive(), "Thread lief nach stop_event.set() nicht aus (Beweis: joint nicht)"
    # Wie oben: conn wurde im Haupt-Thread erzeugt und start() haendigt sie
    # dem Worker-Thread aus. Ohne check_same_thread=False waere der Thread
    # zwar sauber ausgelaufen, haette aber nie tatsaechlich gepollt.
    rows = store.get_candles(conn, "BTCUSDC", "15m", limit=10)
    assert len(rows) >= 1, (
        "Pruefflaeche zu schwach: start() hat im Worker-Thread keine Kerze gespeichert"
    )


def test_poll_once_meldet_warn_ohne_kill_switch_und_schreibt_genau_ein_lagging_ereignis(tmp_path):
    """Ergaenzung 1, Fixrunde 1 (Luecke im Brief, vom Reviewer gefunden): der
    warn-Zwischenzustand (MARKET_DATA_LAGGING) hatte bisher keine Pruefflaeche
    - nur 'ok' und 'stale' waren getestet. Datenalter 2000s liegt zwischen
    warn_eff=1350s und kill_eff=2700s (15m-Intervall, Vorgabeschwellen)."""
    clock = SimClock(10_000_000)
    kerzen = {"BTCUSDC": [_kerze(clock.now_ms() - 2_000_000 - 900_000, 900)]}
    client = _client_fuer(kerzen, clock.now_ms())
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    pc = poller.build_context(conn, _cfg(tmp_path), SPECS, clock=clock, client=client)

    outcome = poller.poll_once(pc)
    assert outcome.staleness.status == "warn"
    assert db.get_state(conn, "kill_switch", "0") == "0"
    ereignisse = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE event='MARKET_DATA_LAGGING'"
    ).fetchone()["c"]
    assert ereignisse == 1, f"genau ein Ereignis erwartet, gemessen: {ereignisse}"


def test_poll_once_bleibt_ok_knapp_unter_der_warn_schwelle(tmp_path):
    """Grenzwert-Gegenprobe zum vorigen Test: Datenalter 1349s liegt knapp
    UNTER warn_eff=1350s (max(150, 1.5*900), 15m-Intervall) - status muss
    'ok' bleiben, kein MARKET_DATA_LAGGING-Ereignis. Ohne diese Grenze waere
    der warn-Pfad zwar aufgerufen, aber nicht wirklich an der richtigen
    Schwelle geprueft (Reviewer-Rot-Nachweis Fixrunde 1: der Faktor 1,5 liess
    sich auf 1,4 aendern, ohne dass ein einziger Poller-Test rot wurde -
    dieser Test haette es getan, weil 1349 > 1,4*900=1260, aber < 1,5*900=1350)."""
    clock = SimClock(10_000_000)
    kerzen = {"BTCUSDC": [_kerze(clock.now_ms() - 1_349_000 - 900_000, 900)]}
    client = _client_fuer(kerzen, clock.now_ms())
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    pc = poller.build_context(conn, _cfg(tmp_path), SPECS, clock=clock, client=client)

    outcome = poller.poll_once(pc)
    assert outcome.staleness.status == "ok", f"gemessen: {outcome.staleness}"
    ereignisse = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE event='MARKET_DATA_LAGGING'"
    ).fetchone()["c"]
    assert ereignisse == 0, f"kein Ereignis erwartet, gemessen: {ereignisse}"


def _client_mit_dynamischer_serverzeit(kerzen_je_symbol: dict[str, list], clock: SimClock) -> BinanceClient:
    """Wie _client_fuer(), aber serverTime folgt der Uhr statt an ihrem
    Konstruktionszeitpunkt zu haengen - so verhaelt sich ein echter, mit dem
    Container synchroner Binance-Server ueber mehrere Zyklen hinweg.

    Richtigstellung (Gesamtreview A2, B-1): hier stand frueher, der Uhrversatz
    wachse, weil "die SimClock springt statt zu ticken". Das war eine falsche
    Diagnose. Der Versatz wuchs, weil poll_once() den GECACHTEN server_time-Wert
    an staleness() gab, die ihn gegen die aktuelle Uhr rechnet - mit einer
    echten Wanduhr genauso wie mit der SimClock. Der Fehler lag im
    Produktivcode, nicht in der Testuhr; behoben ueber
    marketdata.projizierte_serverzeit()."""
    def klines(url):
        qs = parse_qs(urlsplit(url).query)
        sym = qs["symbol"][0]
        return FakeAntwort(_body(kerzen_je_symbol[sym]))

    def zeit(url):
        return FakeAntwort(_body({"serverTime": clock.now_ms()}))

    opener = FakeOpener({"/api/v3/klines": klines, "/api/v3/time": zeit})
    return BinanceClient(BASIS, opener=opener)


def test_poll_once_drosselt_lagging_ereignisse_auf_hoechstens_eins_pro_15min(tmp_path):
    """Ergaenzung 1, Fixrunde 1: mehrere Polls hintereinander im warn-Fenster
    duerfen wegen der Drosselung (last_lagging_event_ms, 900_000 ms) nicht je
    ein Ereignis erzeugen. Start bei Datenalter 1400s (knapp ueber
    warn_eff=1350s), drei Vorstellungen der Uhr um je 300s (900s kumuliert
    genau an der Drosselschwelle, Datenalter am Ende 2300s - weiterhin sicher
    unter kill_eff=2700s, also weiterhin 'warn', nie 'stale').

    Aufgeraeumt im Gesamtreview A2 (B-1): dieser Test lief zuvor mit
    market_clock_skew_kill_s=100_000, begruendet mit einer SimClock, "die
    springt statt zu ticken". Die Begruendung war falsch und die Umgehung hat
    einen Blocker zugedeckt - der gemeldete Uhrversatz wuchs, weil der
    GECACHTE server_time-Wert gegen die aktuelle Uhr gerechnet wurde (B-1).
    Seit marketdata.projizierte_serverzeit() ist der gemeldete Versatz hier 0,
    und der Test laeuft mit der ausgelieferten Schwelle
    (MARKET_CLOCK_SKEW_KILL_S=30) - so, wie der Nutzer ihn betreibt."""
    clock = SimClock(10_000_000)
    kerzen = {"BTCUSDC": [_kerze(clock.now_ms() - 1_400_000 - 900_000, 900)]}
    client = _client_mit_dynamischer_serverzeit(kerzen, clock)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    cfg = _cfg(tmp_path)  # ausgelieferte Schwellen, keine Umgehung mehr
    pc = poller.build_context(conn, cfg, SPECS, clock=clock, client=client)

    def anzahl_lagging() -> int:
        return conn.execute(
            "SELECT COUNT(*) c FROM events WHERE event='MARKET_DATA_LAGGING'"
        ).fetchone()["c"]

    outcome = poller.poll_once(pc)
    assert outcome.staleness.status == "warn"
    assert anzahl_lagging() == 1, f"erstes Ereignis erwartet, gemessen: {anzahl_lagging()}"

    for _ in range(2):  # kumuliert 600s - weiterhin innerhalb der 900s-Drossel
        clock.set(clock.now_ms() + 300_000)
        outcome = poller.poll_once(pc)
        assert outcome.staleness.status == "warn"
    gemessen_gedrosselt = anzahl_lagging()
    assert gemessen_gedrosselt == 1, (
        f"waehrend der Drosselung erwartet: 1 Ereignis, gemessen: {gemessen_gedrosselt}"
    )

    clock.set(clock.now_ms() + 300_000)  # kumuliert 900s - Drosselschwelle erreicht
    outcome = poller.poll_once(pc)
    assert outcome.staleness.status == "warn"
    gemessen_nach_drossel = anzahl_lagging()
    assert gemessen_nach_drossel == 2, (
        f"nach Ablauf der Drosselschwelle erwartet: 2 Ereignisse, gemessen: {gemessen_nach_drossel}"
    )


def test_run_forever_loggt_poll_cycle_exception_bei_unerwartetem_fehler(tmp_path, monkeypatch):
    """Blocker aus Fixrunde 1: ein Fehler VOR der Veraltet-Pruefung (z. B. ein
    Tippfehler wie pc.ctx.ledgar statt pc.ctx.ledger) darf nicht spurlos nach
    stderr verschwinden - sonst friert market_data_status/kill_switch auf dem
    letzten Wert ein, waehrend nirgends in der DB sichtbar wird, dass der
    Poller seitdem in Backoff haengt."""
    monkeypatch.setattr(poller, "BACKOFF_STEPS_S", (0, 0, 0, 0))
    clock = SimClock(10_000_000)
    kerzen = {"BTCUSDC": [_kerze(clock.now_ms() - 100 - 900_000, 900)]}
    client = _client_fuer(kerzen, clock.now_ms())
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    cfg = _cfg(tmp_path, market_poll_s=0)
    pc = poller.build_context(conn, cfg, SPECS, clock=clock, client=client)

    calls = {"n": 0}
    stop_event = threading.Event()

    def kaputter_poll_once(pc_arg):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AttributeError("Testdouble: pc.ctx.ledgar statt pc.ctx.ledger")
        stop_event.set()
        return poller.PollOutcome(ok=True, staleness=None, fills=[], backoff_s=0.0)

    monkeypatch.setattr(poller, "poll_once", kaputter_poll_once)

    thread = threading.Thread(target=poller.run_forever, args=(pc, stop_event), daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive(), "Thread lief nach stop_event.set() nicht aus (Beweis: joint nicht)"
    ereignisse = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE event='POLL_CYCLE_EXCEPTION'"
    ).fetchone()["c"]
    assert ereignisse == 1, f"genau ein POLL_CYCLE_EXCEPTION erwartet, gemessen: {ereignisse}"
    detail = conn.execute(
        "SELECT detail FROM events WHERE event='POLL_CYCLE_EXCEPTION'"
    ).fetchone()["detail"]
    assert detail == "AttributeError", f"Ausnahmetyp im Detail erwartet, gemessen: {detail!r}"


# --------------------------------------------------------------------------
# B-1: Der Auslieferungszustand ueber viele Zyklen (Gesamtreview A2)
# --------------------------------------------------------------------------

_INTERVALL_MS = 900_000


def _kerzen_zum_uhrstand(clock: SimClock) -> list:
    """Die zwei zuletzt GESCHLOSSENEN 15m-Kerzen zum aktuellen Uhrstand.

    'Puenktliche Kerzen': Binance liefert genau das, was zu dieser Sekunde
    geschlossen ist - kein Rueckstand, keine Luecke."""
    grenze = (clock.now_ms() // _INTERVALL_MS) * _INTERVALL_MS
    return [_kerze(grenze - 2 * _INTERVALL_MS, 900), _kerze(grenze - _INTERVALL_MS, 900)]


def _client_synchrone_uhr(clock: SimClock) -> BinanceClient:
    """Binance-Attrappe ohne Netz: serverTime == Containeruhr (Uhrversatz 0),
    Kerzen puenktlich zum Uhrstand. Das ist der gutmuetigste denkbare
    Auslieferungszustand - hier darf nichts 'stale' werden."""
    def klines(url):
        return FakeAntwort(_body(_kerzen_zum_uhrstand(clock)))

    def zeit(url):
        return FakeAntwort(_body({"serverTime": clock.now_ms()}))

    return BinanceClient(BASIS, opener=FakeOpener({"/api/v3/klines": klines, "/api/v3/time": zeit}))


def test_poll_once_bleibt_15_zyklen_ok_bei_synchroner_uhr_und_puenktlichen_kerzen(tmp_path):
    """B-1 (Blocker, Gesamtreview A2): der Poller schaltete sich im
    Lieferzustand nach 60 s selbst ab.

    server_time() wird nach Spec 11.3 hoechstens alle 15 min geholt
    (_TIME_CHECK_INTERVAL_MS). Der GECACHTE Wert ging jeden Zyklus an
    staleness(), die clock_skew_s = abs(now - server_time_ms) gegen die
    AKTUELLE Uhr rechnet - der gemeldete Versatz wuchs also um eine Sekunde
    pro Sekunde. Mit MARKET_CLOCK_SKEW_KILL_S=30 und MARKET_POLL_S=60 war der
    zweite Zyklus 'stale', der Kill Switch gesetzt und (weil _apply_staleness
    ihn nur setzt) nie wieder geloest.

    Hier laufen 16 Zyklen a 60 s mit synchroner Uhr und puenktlichen Kerzen.
    Nichts an diesem Szenario rechtfertigt auch nur ein 'warn'."""
    clock = SimClock(100 * _INTERVALL_MS + 200_000)  # 200 s nach dem Kerzenschluss
    client = _client_synchrone_uhr(clock)
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    pc = poller.build_context(conn, _cfg(tmp_path), SPECS, clock=clock, client=client)

    start_ms = clock.now_ms()
    for zyklus in range(16):
        outcome = poller.poll_once(pc)
        t_s = (clock.now_ms() - start_ms) // 1000
        assert outcome.staleness is not None, f"Zyklus {zyklus}: keine Veraltet-Auswertung"
        assert outcome.staleness.status == "ok", (
            f"Zyklus {zyklus} (t={t_s}s): status={outcome.staleness.status!r}, "
            f"Datenalter {outcome.staleness.data_age_s:.0f}s, "
            f"Uhrversatz {outcome.staleness.clock_skew_s:.0f}s"
        )
        assert db.get_state(conn, "kill_switch", "0") == "0", (
            f"Zyklus {zyklus} (t={t_s}s): Kill Switch gesetzt, "
            f"Uhrversatz {outcome.staleness.clock_skew_s:.0f}s"
        )
        clock.set(clock.now_ms() + 60_000)
