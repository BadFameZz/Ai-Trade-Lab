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
    assert engine.check(Proposal("ETHUSDC", "BUY", 8), pf, False).code == "MAX_EXPOSURE"


@pytest.mark.parametrize("action", ["SHORT", "LONG_10X", "MARGIN_BUY", "WITHDRAW"])
def test_non_spot_rejected(engine, action):
    assert engine.check(Proposal("BTCUSDC", action, 1), FLAT, False).code == "NOT_SPOT"


def test_no_short(engine):
    assert engine.check(Proposal("BTCUSDC", "SELL", 5), FLAT, False).code == "NO_POSITION"


def test_bad_symbol(engine):
    assert engine.check(Proposal("btc/usdc; drop", "BUY", 5), FLAT, False).code == "BAD_SYMBOL"
