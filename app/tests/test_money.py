from __future__ import annotations

from decimal import Decimal

import pytest

from aitra import money


def test_dec_lehnt_float_ab():
    """Geld darf nie aus einem float entstehen - der Fehler soll laut sein."""
    with pytest.raises(TypeError):
        money.dec(0.1)


def test_dec_aus_text_und_int():
    assert money.dec("0.1") == Decimal("0.1")
    assert money.dec(5) == Decimal("5")


def test_step_down_rundet_immer_ab():
    step = Decimal("0.00001")
    assert money.step_down(Decimal("0.123456789"), step) == Decimal("0.12345")
    assert money.step_down(Decimal("0.12345"), step) == Decimal("0.12345")
    assert money.step_down(Decimal("0.000009"), step) == Decimal("0")


def test_tick_down_und_tick_up():
    tick = Decimal("0.01")
    assert money.tick_down(Decimal("81287.039"), tick) == Decimal("81287.03")
    assert money.tick_up(Decimal("81287.031"), tick) == Decimal("81287.04")
    assert money.tick_up(Decimal("81287.03"), tick) == Decimal("81287.03")


def test_textform_ist_kanonisch_und_rundreisefest():
    # Feste Nachkommastellen, damit Gleichheitsvergleiche auf TEXT verlaesslich sind (E-007)
    assert money.to_text(Decimal("100")) == "100.00000000"
    assert money.to_text(Decimal("100.0")) == "100.00000000"
    assert money.to_text(Decimal("0.1")) == "0.10000000"
    assert money.from_text(money.to_text(Decimal("1234.5678"))) == Decimal("1234.5678")


def test_effektive_mindestorder_liegt_ueber_min_notional():
    """K-3: Weil immer abgerundet wird, rutscht eine auf 5,00 gezielte Order
    nach dem Runden darunter. Die garantierte Grenze ist min_notional + step*preis."""
    spec = money.BUILTIN_SPECS["BTCUSDC"]
    eff = spec.effective_min_notional(Decimal("81287.04"))
    assert eff > spec.min_notional
    assert eff == Decimal("5") + Decimal("0.00001") * Decimal("81287.04")
    assert Decimal("5.81") < eff < Decimal("5.82")


def test_builtin_specs_kennen_btcusdc_und_bnbusdc():
    """Der Nutzer handelt BTC/USDC und BNB/USDC. ETHUSDC ist raus - und muss
    raus sein, sonst bemisst ein Lauf gegen eine Losgroesse, die niemand handelt."""
    assert set(money.BUILTIN_SPECS) == {"BTCUSDC", "BNBUSDC"}
    for sym in ("BTCUSDC", "BNBUSDC"):
        spec = money.BUILTIN_SPECS[sym]
        assert spec.quote == "USDC"
        assert spec.min_notional == Decimal("5")
        assert spec.tick_size > 0 and spec.step_size > 0
        assert spec.base_precision == 8 and spec.quote_precision == 8


def test_bnbusdc_losgroesse_am_2026_09_21_abgefragt():
    """Die vier Filterwerte, am 2026-09-21 von Binance abgefragt (Planungskopf).

    stepSize ist 0,001 - nicht 0,0001 wie bei ETHUSDC. Genau diese Zahl bestimmt
    ueber effective_min_notional die kleinste garantiert durchgehende Order.
    """
    spec = money.BUILTIN_SPECS["BNBUSDC"]
    assert spec.base == "BNB" and spec.quote == "USDC"
    assert spec.tick_size == Decimal("0.01")
    assert spec.step_size == Decimal("0.001")
    assert spec.min_qty == Decimal("0.001")
    assert spec.min_notional == Decimal("5")

    # Preis am 2026-09-21: 789,71 USDC
    eff = spec.effective_min_notional(Decimal("789.71"))
    assert eff == Decimal("5") + Decimal("0.001") * Decimal("789.71")
    assert eff == Decimal("5.78971")
    assert Decimal("5.78") < eff < Decimal("5.79")
