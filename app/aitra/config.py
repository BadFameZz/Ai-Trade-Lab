"""Konfiguration aus Umgebungsvariablen – mit harten Grenzen.

Live-Trading ist in dieser Version nicht implementiert und bleibt gesperrt,
egal was in der .env steht.
"""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("aitra.config")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    starting_balance: float
    max_position_pct: float
    max_daily_loss_pct: float
    max_total_exposure_pct: float
    data_dir: Path
    admin_token: str
    trading_mode: str = "PAPER"
    live_locked: bool = True


def _num(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        val = float(raw)
    except ValueError as e:
        raise ConfigError(f"{name}={raw!r} ist keine Zahl") from e
    if not (lo < val <= hi):
        raise ConfigError(f"{name}={val} liegt außerhalb der erlaubten Grenzen ({lo} < x ≤ {hi})")
    return val


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

    return Config(
        starting_balance=_num("STARTING_BALANCE", 100, 0, 1_000_000),
        max_position_pct=_num("MAX_POSITION_PCT", 10, 0, 25),
        max_daily_loss_pct=_num("MAX_DAILY_LOSS_PCT", 2, 0, 10),
        max_total_exposure_pct=_num("MAX_TOTAL_EXPOSURE_PCT", 50, 0, 100),
        data_dir=data_dir,
        admin_token=_admin_token(data_dir),
    )
