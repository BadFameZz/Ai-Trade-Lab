"""Konfiguration aus Umgebungsvariablen – mit harten Grenzen.

Live-Trading ist in dieser Version nicht implementiert und bleibt gesperrt,
egal was in der .env steht.
"""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from . import money

log = logging.getLogger("aitra.config")

ALLOWED_BINANCE_HOSTS = frozenset({"api.binance.com", "data-api.binance.vision"})

# Referenzpreis fuer die Schwelle in _narrow_trading_window: BTCUSDC am 2026-09-20
# (Spec Abschnitt 2.2 / K-3). Ein Marktpreis wuerde die Schwelle staendig verschieben;
# die Spec verwendet bewusst denselben festen Wert wie K-3.
_NARROW_WINDOW_REF_PRICE = Decimal("81287.04")
_NARROW_WINDOW_FACTOR = Decimal(20)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    starting_balance: Decimal
    max_position_pct: float
    max_daily_loss_pct: float
    max_total_exposure_pct: float
    data_dir: Path
    admin_token: str
    trading_mode: str = "PAPER"
    live_locked: bool = True
    binance_base_url: str = "https://api.binance.com"
    market_symbols: tuple[str, ...] = ("BTCUSDC", "BNBUSDC")


def _num(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        val = float(raw)
    except ValueError as e:
        raise ConfigError(f"{name}={raw!r} ist keine Zahl") from e
    if not (lo < val <= hi):
        raise ConfigError(f"{name}={val} liegt außerhalb der erlaubten Grenzen ({lo} < x ≤ {hi})")
    return val


def _dec(name: str, default: str, lo: Decimal, hi: Decimal) -> Decimal:
    """Wie _num, aber ohne den Umweg über float (Befund B-4).

    STARTING_BALANCE=0.1 wird heute (_num) zu 0.1000000000000000055511151231257827,
    weil float(raw) zuerst bindaer rundet. _dec liest denselben String direkt in
    Decimal ein und bleibt exakt.
    """
    raw = os.getenv(name, default).strip()
    try:
        val = money.dec(raw)
    except (InvalidOperation, ValueError) as e:
        raise ConfigError(f"{name}={raw!r} ist keine Zahl") from e
    if not (lo < val <= hi):
        raise ConfigError(f"{name}={val} liegt außerhalb der erlaubten Grenzen ({lo} < x ≤ {hi})")
    return val


def _binance_base_url(name: str = "BINANCE_BASE_URL", default: str = "https://api.binance.com") -> str:
    """Nur HTTPS und nur die beiden bekannten Binance-Hosts, exakt verglichen (A-17).

    Ein Praefixvergleich (startswith) wuerde https://api.binance.com.evil.example
    durchlassen — genau das ist der Rot-Nachweis unten.
    """
    raw = os.getenv(name, default).strip()
    parts = urlsplit(raw)
    if parts.scheme != "https":
        raise ConfigError(f"{name}={raw!r} muss https verwenden")
    if parts.hostname not in ALLOWED_BINANCE_HOSTS:
        raise ConfigError(f"{name}={raw!r} ist kein erlaubter Binance-Host")
    return raw


def _narrow_trading_window(balance: Decimal, max_position_pct: float) -> bool:
    """K-1/A-22: Das Fenster ist zu eng, wenn die groesste erlaubte Order nicht
    mindestens das Zwanzigfache der effektiven Mindestordergroesse (K-3) erreicht.

    balance * max_position_pct/100 >= 20 * effective_min_notional(ref_price)
    Bei 5,82 USDC (BTCUSDC, 81.287) und 10 % Positionsgroesse liegt die Grenze bei
    rund 1.164 USDC; in der Doku wird grosszuegig auf 1.200 USDC aufgerundet.
    """
    spec = money.BUILTIN_SPECS["BTCUSDC"]
    eff_min = spec.effective_min_notional(_NARROW_WINDOW_REF_PRICE)
    largest_order = balance * Decimal(str(max_position_pct)) / Decimal(100)
    return largest_order < _NARROW_WINDOW_FACTOR * eff_min


def _symbols(name: str = "MARKET_SYMBOLS", default: str = "BTCUSDC,BNBUSDC") -> tuple[str, ...]:
    """1 bis 5 Symbole, je gegen SYMBOL_RE geprueft (Spec 18, Spec 11.2).

    Die Pruefung passiert hier und nicht erst in der SQL-Schicht: ein Symbol,
    das erst zur Laufzeit auffaellt, faellt im Poller-Thread auf - also dort,
    wo niemand hinsieht.
    """
    from .risk import SYMBOL_RE  # lokal: risk.py importiert config.py (Zyklus)

    roh = os.getenv(name, default).strip()
    teile = tuple(s.strip().upper() for s in roh.split(",") if s.strip())
    if not 1 <= len(teile) <= 5:
        raise ConfigError(f"{name}={roh!r} muss 1 bis 5 Symbole nennen, hat {len(teile)}")
    for s in teile:
        if not SYMBOL_RE.match(s):
            raise ConfigError(f"{name}: Symbol {s!r} ungültig")
    return teile


def _admin_token(data_dir: Path) -> str:
    env = os.getenv("ADMIN_TOKEN", "").strip()
    if env:
        if len(env) < 24:
            raise ConfigError("ADMIN_TOKEN muss mindestens 24 Zeichen lang sein")
        return env
    # Kein Token gesetzt → einmalig erzeugen und nur lokal ablegen (nie loggen)
    path = data_dir / "admin_token"
    if path.exists():
        return path.read_text().strip()
    token = secrets.token_hex(24)
    path.write_text(token)
    path.chmod(0o600)
    log.warning("ADMIN_TOKEN nicht gesetzt – neuer Token in %s abgelegt", path)
    return token


def load() -> Config:
    data_dir = Path(os.getenv("DATA_DIR", "/app/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    mode = os.getenv("TRADING_MODE", "paper").strip().lower()
    if mode != "paper":
        log.warning("TRADING_MODE=%r ignoriert – Live-Trading ist gesperrt, erzwinge PAPER", mode)

    starting_balance = _dec("STARTING_BALANCE", "10000", Decimal(0), Decimal(1_000_000))
    max_position_pct = _num("MAX_POSITION_PCT", 10, 0, 25)
    binance_base_url = _binance_base_url()

    if _narrow_trading_window(starting_balance, max_position_pct):
        log.warning(
            "NARROW_TRADING_WINDOW: STARTING_BALANCE=%s mit MAX_POSITION_PCT=%s%% "
            "ergibt ein zu enges Handelsfenster (< 20x der effektiven Mindestordergroesse, K-1)",
            money.to_text(starting_balance), max_position_pct,
        )

    market_symbols = _symbols()

    return Config(
        starting_balance=starting_balance,
        max_position_pct=max_position_pct,
        max_daily_loss_pct=_num("MAX_DAILY_LOSS_PCT", 2, 0, 10),
        max_total_exposure_pct=_num("MAX_TOTAL_EXPOSURE_PCT", 50, 0, 100),
        data_dir=data_dir,
        admin_token=_admin_token(data_dir),
        binance_base_url=binance_base_url,
        market_symbols=market_symbols,
    )
