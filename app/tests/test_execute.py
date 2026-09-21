from __future__ import annotations

import inspect
import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from aitra import db, money, store
from aitra.config import Config
from aitra.execute import ExecutionContext, execute_proposal, expire_stale_pending, resolve_pending
from aitra.ledger import Ledger
from aitra.marketdata import Candle, SimClock
from aitra.risk import Proposal, RiskEngine

BTC = money.BUILTIN_SPECS["BTCUSDC"]
SPECS = {"BTCUSDC": BTC, "ETHUSDC": money.BUILTIN_SPECS["ETHUSDC"]}


def _install_apply_guard():
    """Laufzeit-Waechter fuer A-6b (Fix-Runde 1).

    Der textbasierte Waechter (test_a6b_ledger_apply_hat_genau_einen_aufrufer)
    erkennt nur den Substring '.apply(' und wird durch eine Zuweisung
    (`bypass = Ledger.apply; bypass(...)`) oder `getattr(ledger, "apply")(...)`
    umgangen, ohne dass dieser Substring je entsteht. Dieser Waechter patcht
    Ledger.apply so, dass jeder tatsaechliche Aufruf ueber inspect.stack()
    geprueft wird: die unmittelbar aufrufende Datei muss execute.py sein.

    autospec=True ist noetig, damit der Patch weiterhin wie eine gebundene
    Methode aufgerufen werden kann (self wird automatisch mitgereicht) -
    das erzeugt aber zwei Arten von Rauschen im Frame-Stack, die uebersprungen
    werden muessen: mehrere unittest/mock.py-interne Frames und einen von
    autospec per exec() erzeugten Signatur-Proxy mit dem Dateinamen '<string>'.
    """
    original_apply = Ledger.apply

    def guarded_apply(self, order, candle_next):
        caller_file = None
        for frame_info in inspect.stack()[1:]:
            fn = frame_info.filename
            if fn == "<string>" or os.path.basename(fn) == "mock.py":
                continue
            caller_file = fn
            break
        assert caller_file is not None, "Kein Aufrufer außerhalb von unittest.mock gefunden"
        # Basisname, nicht endswith(): 'test_execute.py' endet zwar ebenfalls
        # auf 'execute.py', ist aber nicht das Modul (derselbe Fallstrick, den
        # der grep-Waechter mit line.endswith('execute.py') schon hat).
        assert os.path.basename(caller_file) == "execute.py", (
            f"Ledger.apply() wurde aus {caller_file} aufgerufen, nicht aus execute.py (A-6b)"
        )
        return original_apply(self, order, candle_next)

    return patch.object(Ledger, "apply", autospec=True, side_effect=guarded_apply)


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
    """Nur execute.py darf .apply( aufrufen (E-003/A-6b).

    --include=*.py ist noetig: `grep -l` meldet sonst auch Bytecode-Cache-Treffer
    aus app/aitra/__pycache__/*.pyc (Binaerdateien, die denselben Bytestring
    zufaellig enthalten) als vermeintlich zweiten Aufrufer.
    """
    import subprocess
    aitra_dir = Path(__file__).resolve().parent.parent / "aitra"
    out = subprocess.run(
        ["grep", "-rln", "--include=*.py", r"\.apply(", str(aitra_dir)], capture_output=True, text=True
    ).stdout.splitlines()
    andere = [line for line in out if not line.endswith("execute.py")]
    assert andere == []


def test_a6b_laufzeit_waechter_laesst_echte_buchungen_durch(tmp_path):
    """Laufzeit-Gegenstueck zum grep-Waechter (Fix-Runde 1): muss echte
    Buchungswege unveraendert durchlassen - sowohl den Sofort-Fill in
    execute_proposal() als auch den Fill in resolve_pending()."""
    with _install_apply_guard():
        ctx = _ctx(tmp_path)
        r = execute_proposal(
            Proposal("BTCUSDC", "BUY", 8), ctx, marks={}, ts_ms=900_000, ref_price=Decimal("81287.03"),
            start_of_day_equity=Decimal("10000"), next_candle=_candle(1_800_000),
        )
        assert r.status == "filled"

        # marks muss die gehaltene BTC-Position bewerten, sonst zaehlt equity()
        # nur die Kasse und meldet einen Scheinverlust (DAILY_LOSS) nach dem
        # ersten Kauf - kein Guard-Fehler, sondern ein Bewertungsartefakt.
        pending = execute_proposal(
            Proposal("BTCUSDC", "BUY", 5), ctx, marks={"BTCUSDC": Decimal("81287.03")}, ts_ms=1_800_000,
            ref_price=Decimal("81287.03"), start_of_day_equity=Decimal("10000"), next_candle=None,
        )
        assert pending.status == "pending_fill"
        fills = resolve_pending(ctx, _candle(2_700_000))
        assert len(fills) == 1


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

    # Bisher nur implizit bewiesen (fills==0 direkt nach dem Verfall, ohne dass
    # danach je eine Folgekerze angeboten wurde): auch eine anschliessend
    # eintreffende, an sich passende Folgekerze darf den verfallenen Vorschlag
    # nicht mehr fuellen, weil er in get_pending_decisions() nicht mehr auftaucht.
    fills_nach_verfall = resolve_pending(ctx, _candle(1_800_000))
    assert fills_nach_verfall == []
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

    # avg_price ist ein Quotient (gewichteter Durchschnitt) und kann mehr als
    # DP=8 Nachkommastellen brauchen, um exakt zu sein; qty und cash sind reine
    # Summen/Differenzen von bereits auf DP=8 begrenzten Werten und bleiben es.
    # store.py persistiert JEDEN Geldwert kanonisch mit DP=8 (money.to_text) -
    # dieselbe Rundung muss die unabhaengige Rekonstruktion anwenden, sonst
    # vergleicht sie eine unendlich genaue Zahl mit einer bewusst gerundeten.
    avg_price = money.from_text(money.to_text(avg_price, money.DP))

    positions = store.get_positions(ctx.conn, "run-1")
    assert qty - positions["BTCUSDC"]["qty"] == Decimal("0")
    assert avg_price - positions["BTCUSDC"]["avg_price"] == Decimal("0")
    assert cash - fills[-1]["cash_after"] == Decimal("0")


def test_resolve_pending_bemisst_mit_dem_ref_price_des_vorschlags(tmp_path):
    """Fix-Welle, Review-Befund 3 — Spion analog test_benchmark.py.

    resolve_pending() bemass die Order mit ref_price=candle.open, also mit dem
    Fuellpreis selbst. Dasselbe Muster wurde in benchmark.py bereits als
    Critical zurueckgenommen; im Livepfad stand es unveraendert. Ergebnis waere
    gewesen: Replay bemisst gegen current.close, live gegen candle.open — zwei
    verschiedene Mengen fuer dieselbe Entscheidung, entgegen E-001, und A-8
    (eine Quelle, ein Hash) in Teilprojekt A2 unerreichbar.

    Die Fuellkerze traegt hier bewusst einen ganz anderen Preis (99.999) als
    der Vorschlag (81.287,03), damit eine Verwechslung sofort auffaellt.
    """
    ctx = _ctx(tmp_path)
    ref = Decimal("81287.03")
    pending = execute_proposal(
        Proposal("BTCUSDC", "BUY", 8), ctx, marks={}, ts_ms=900_000, ref_price=ref,
        start_of_day_equity=Decimal("10000"), next_candle=None,
    )
    assert pending.status == "pending_fill"
    # Migration 3: der Vorschlagspreis steht kanonisch als TEXT in der Zeile (E-007)
    row = ctx.conn.execute("SELECT pending_ref_price FROM decisions WHERE id=?",
                            (pending.decision_id,)).fetchone()
    assert row["pending_ref_price"] == "81287.03000000"

    fuellkerze = _candle(1_800_000, "99999.00")
    captured: dict = {}
    from aitra.execute import size_order as _real_size_order

    def spy(*args, **kwargs):
        captured["ref_price"] = args[3]  # 4. Positionsargument von size_order()
        return _real_size_order(*args, **kwargs)

    with patch("aitra.execute.size_order", side_effect=spy):
        fills = resolve_pending(ctx, fuellkerze)

    assert len(fills) == 1  # Pruefflaeche: der Spion muss einen echten Fill gesehen haben
    assert captured["ref_price"] == ref
    assert captured["ref_price"] != fuellkerze.open
    # Gefuellt wird trotzdem zum Preis der Folgekerze (E-006) — nur bemessen
    # wurde mit dem Vorschlagspreis.
    assert fills[0].candle_open_time == 1_800_000
    assert fills[0].price > fuellkerze.open  # 99.999 + Slippage


def test_resolve_pending_verwirft_zeilen_ohne_gespeicherten_ref_price(tmp_path):
    """Bestandszeilen aus einer DB vor Migration 3 haben pending_ref_price NULL.
    Mit candle.open weiterzurechnen waere genau der Look-ahead, den Migration 3
    beseitigt — also verfallen sie, statt still falsch bemessen zu werden."""
    ctx = _ctx(tmp_path)
    pending = execute_proposal(
        Proposal("BTCUSDC", "BUY", 8), ctx, marks={}, ts_ms=900_000,
        ref_price=Decimal("81287.03"), start_of_day_equity=Decimal("10000"), next_candle=None,
    )
    ctx.conn.execute("UPDATE decisions SET pending_ref_price = NULL WHERE id = ?",
                      (pending.decision_id,))
    ctx.conn.commit()

    assert resolve_pending(ctx, _candle(1_800_000)) == []
    assert ctx.conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 0
    row = ctx.conn.execute("SELECT risk_code, pending_since_ms FROM decisions WHERE id=?",
                            (pending.decision_id,)).fetchone()
    assert row["risk_code"] == "PENDING_EXPIRED"
    assert row["pending_since_ms"] is None
