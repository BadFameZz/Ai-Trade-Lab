from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import pytest

from aitra import config
from aitra.config import Config, ConfigError, load


def _base_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_TOKEN", "x" * 32)


def test_dec_liefert_decimal_und_keinen_float(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    val = config._dec("STARTING_BALANCE", "10000", Decimal(0), Decimal(1_000_000))
    assert val == Decimal("10000")
    assert isinstance(val, Decimal)


def test_dec_lehnt_nicht_numerischen_text_ab():
    with pytest.raises(ConfigError):
        config._dec("STARTING_BALANCE", "zehntausend", Decimal(0), Decimal(1_000_000))


def test_dec_ist_praezise_ohne_float_umweg(monkeypatch):
    # B-4: 0.1 darf niemals ueber float((str)) laufen und driften
    monkeypatch.setenv("X_TEST", "0.1")
    val = config._dec("X_TEST", "1", Decimal(0), Decimal(10))
    assert val == Decimal("0.1")


def test_dec_prueft_grenzen():
    with pytest.raises(ConfigError):
        config._dec("X", "-1", Decimal(0), Decimal(10))


def test_starting_balance_vorgabe_ist_10000(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.delenv("STARTING_BALANCE", raising=False)
    cfg = load()
    assert cfg.starting_balance == Decimal("10000")
    assert isinstance(cfg.starting_balance, Decimal)


def test_binance_base_url_akzeptiert_den_offiziellen_host(monkeypatch, tmp_path):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("BINANCE_BASE_URL", "https://api.binance.com")
    cfg = load()
    assert cfg.binance_base_url == "https://api.binance.com"


@pytest.mark.parametrize("bad_url", [
    "http://169.254.169.254",              # Metadaten-Adresse, zudem kein TLS
    "https://evil.example.com",            # falscher Host
    "http://api.binance.com",              # richtiger Host, aber kein TLS
    "https://api.binance.com.evil.example",  # Praefix-Falle (A-17)
])
def test_binance_base_url_lehnt_alles_ausserhalb_der_allowlist_ab(monkeypatch, tmp_path, bad_url):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("BINANCE_BASE_URL", bad_url)
    with pytest.raises(ConfigError):
        load()


def test_narrow_trading_window_warnt_unter_der_schwelle(monkeypatch, tmp_path, caplog):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("STARTING_BALANCE", "100")
    with caplog.at_level(logging.WARNING, logger="aitra.config"):
        load()
    assert "NARROW_TRADING_WINDOW" in caplog.text


def test_narrow_trading_window_schweigt_ueber_der_schwelle(monkeypatch, tmp_path, caplog):
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("STARTING_BALANCE", "10000")
    with caplog.at_level(logging.WARNING, logger="aitra.config"):
        load()
    assert "NARROW_TRADING_WINDOW" not in caplog.text


def test_bestandskonstruktion_bleibt_positional_gueltig(tmp_path):
    # Befund B-5: test_risk.py und test_api.py duerfen durch Aufgabe 2 nicht brechen
    cfg = Config(100, 10, 2, 50, tmp_path, "x" * 32)
    assert cfg.starting_balance == 100
    assert cfg.binance_base_url == "https://api.binance.com"


def test_market_symbols_vorgabe_ist_btc_und_bnb(monkeypatch, tmp_path):
    """Die Vorgabe muss zu BUILTIN_SPECS passen - ein Symbol ohne Spec fuehrt
    zur Laufzeit zu Rejection(NO_SPEC) statt zu einem Fehler beim Start."""
    from aitra import money
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_TOKEN", "x" * 32)
    monkeypatch.delenv("MARKET_SYMBOLS", raising=False)
    cfg = load()
    assert cfg.market_symbols == ("BTCUSDC", "BNBUSDC")
    assert all(s in money.BUILTIN_SPECS for s in cfg.market_symbols)


def test_market_symbols_aus_der_umgebung_und_grenzen(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_TOKEN", "x" * 32)
    monkeypatch.setenv("MARKET_SYMBOLS", " btcusdc , bnbusdc ")
    assert load().market_symbols == ("BTCUSDC", "BNBUSDC")

    monkeypatch.setenv("MARKET_SYMBOLS", "BTC/USDC")
    with pytest.raises(ConfigError):
        load()

    monkeypatch.setenv("MARKET_SYMBOLS", "")
    with pytest.raises(ConfigError):
        load()

    monkeypatch.setenv("MARKET_SYMBOLS", "A1USDC,A2USDC,A3USDC,A4USDC,A5USDC,A6USDC")
    with pytest.raises(ConfigError):
        load()
