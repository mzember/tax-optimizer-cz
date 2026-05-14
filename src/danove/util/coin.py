"""Coin alias normalization and classification."""

import csv
from functools import lru_cache
from pathlib import Path

_ALIASES: dict[str, dict] = {}


def _load(config_dir: Path) -> None:
    global _ALIASES
    path = config_dir / "coin_aliases.csv"
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            _ALIASES[row["alias"].upper()] = {
                "kanonicky": row["kanonicky_ticker"].upper(),
                "coingecko_id": row["coingecko_id"],
                "klasifikace": row["klasifikace"],
            }


def init(config_dir: Path) -> None:
    _load(config_dir)


def normalizuj(ticker: str) -> str:
    """Return canonical ticker (e.g. XBT→BTC)."""
    t = ticker.strip().upper()
    return _ALIASES.get(t, {}).get("kanonicky", t)


def coingecko_id(ticker: str) -> str | None:
    t = normalizuj(ticker)
    return _ALIASES.get(t, {}).get("coingecko_id") or None


def klasifikace(ticker: str) -> str:
    """Return 'fiat', 'crypto', 'stablecoin', 'ignore', or 'unknown'."""
    t = normalizuj(ticker)
    return _ALIASES.get(t, {}).get("klasifikace", "unknown")


def je_fiat(ticker: str) -> bool:
    return klasifikace(ticker) == "fiat"


def je_stablecoin(ticker: str) -> bool:
    return klasifikace(ticker) == "stablecoin"


def je_crypto(ticker: str) -> bool:
    return klasifikace(ticker) in ("crypto", "stablecoin", "unknown")
