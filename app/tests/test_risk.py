from pathlib import Path

import pytest

from aitra.config import Config
from aitra.risk import PortfolioState, Proposal, RiskEngine


@pytest.fixture
def engine(tmp_path: Path):
    return RiskEngine(Config(100, 10, 2, 50, tmp_path, "x" * 32))


FLAT = PortfolioState(equity=100, start_of_day_equity=100, exposure_pct=0)


def test_buy_within_limits(engine):
    assert engine.check(Proposal("BTCUSDC", "BUY", 8, 71), FLAT, False).approved


def test_position_limit(engine):
    r = engine.check(Proposal("BTCUSDC", "BUY", 15), FLAT, False)
    assert not r.approved and r.code == "MAX_POSITION"


def test_kill_switch_blocks(engine):
    r = engine.check(Proposal("BTCUSDC", "BUY", 5), FLAT, True)
    assert not r.approved and r.code == "KILL_SWITCH"


def test_wait_always_logged(engine):
    assert engine.check(Proposal("BTCUSDC", "WAIT"), FLAT, True).code == "NO_ORDER"


def test_daily_loss(engine):
    pf = PortfolioState(equity=97.9, start_of_day_equity=100, exposure_pct=0)
    r = engine.check(Proposal("BTCUSDC", "BUY", 5), pf, False)
    assert not r.approved and r.code == "DAILY_LOSS"


def test_exposure(engine):
    pf = PortfolioState(equity=100, start_of_day_equity=100, exposure_pct=45)
    assert engine.check(Proposal("BNBUSDC", "BUY", 8), pf, False).code == "MAX_EXPOSURE"


@pytest.mark.parametrize("action", ["SHORT", "LONG_10X", "MARGIN_BUY", "WITHDRAW"])
def test_non_spot_rejected(engine, action):
    assert engine.check(Proposal("BTCUSDC", action, 1), FLAT, False).code == "NOT_SPOT"


def test_no_short(engine):
    assert engine.check(Proposal("BTCUSDC", "SELL", 5), FLAT, False).code == "NO_POSITION"


def test_bad_symbol(engine):
    assert engine.check(Proposal("btc/usdc; drop", "BUY", 5), FLAT, False).code == "BAD_SYMBOL"


def test_a6c_sell_prueft_gegen_die_position_nicht_gegen_die_gesamtexposition(engine):
    """B-1: SELL BTCUSDC ohne BTC-Position muss scheitern, auch wenn ETH genug
    Gesamtexposition traegt. Vor der Korrektur ist dieser Test absichtlich rot."""
    pf_ohne_btc = PortfolioState(
        equity=10000, start_of_day_equity=10000, exposure_pct=20,
        position_pct_by_symbol={"BNBUSDC": 20.0},
    )
    r1 = engine.check(Proposal("BTCUSDC", "SELL", 5), pf_ohne_btc, False)
    assert not r1.approved and r1.code == "NO_POSITION"

    pf_mit_btc = PortfolioState(
        equity=10000, start_of_day_equity=10000, exposure_pct=28,
        position_pct_by_symbol={"BTCUSDC": 8.0, "BNBUSDC": 20.0},
    )
    r2 = engine.check(Proposal("BTCUSDC", "SELL", 5), pf_mit_btc, False)
    assert r2.approved

    r3 = engine.check(Proposal("BTCUSDC", "SELL", 12), pf_mit_btc, False)
    assert not r3.approved and r3.code == "NO_POSITION"


def test_no_short_bleibt_gruen_nach_der_korrektur(engine):
    # Gegenprobe aus A-6c: flaches Portfolio, SELL 5 % -> weiterhin NO_POSITION
    assert engine.check(Proposal("BTCUSDC", "SELL", 5), FLAT, False).code == "NO_POSITION"


def test_daily_loss_vertraegt_decimal_equity(engine):
    from decimal import Decimal
    pf = PortfolioState(equity=Decimal("9790"), start_of_day_equity=Decimal("10000"), exposure_pct=0)
    r = engine.check(Proposal("BTCUSDC", "BUY", 5), pf, False)
    assert not r.approved and r.code == "DAILY_LOSS"


def test_max_position_blockiert_verkauf_nicht_mehr(engine):
    # Orchestrator-Entscheidung zu A-6c: MAX_POSITION darf den Abbau einer
    # bereits gehaltenen, ueber das Limit gewachsenen Position nicht verhindern.
    # 15 % gehalten (z.B. durch Kursgewinne ueber max_position_pct=10 gewachsen),
    # Verkauf von 12 % ist innerhalb der Position -> muss durchgehen.
    pf = PortfolioState(
        equity=10000, start_of_day_equity=10000, exposure_pct=15,
        position_pct_by_symbol={"BTCUSDC": 15.0},
    )
    r = engine.check(Proposal("BTCUSDC", "SELL", 12), pf, False)
    assert r.approved


def test_max_position_blockiert_kauf_weiterhin(engine):
    # Gegenprobe: BUY ueber max_position_pct muss weiterhin MAX_POSITION liefern.
    r = engine.check(Proposal("BTCUSDC", "BUY", 12), FLAT, False)
    assert not r.approved and r.code == "MAX_POSITION"
