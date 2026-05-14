"""CZK price lookup: ČNB daily rates + CoinGecko historical prices.

All rates cached in DuckDB (build/cache.duckdb).
Entry point: ziskej_cenu_czk(coin, datum) -> Decimal
"""

import json
import sys
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb

from danove.util import coin as coin_util
from danove.util import datum as datum_util
from danove.util import http as http_util

_db: duckdb.DuckDBPyConnection | None = None


def init_db(cache_path: Path) -> None:
    global _db
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    _db = duckdb.connect(str(cache_path))
    _db.execute("""
        CREATE TABLE IF NOT EXISTS kurzy_cnb (
            mena VARCHAR,
            datum DATE,
            mnozstvi INTEGER,
            kurz_czk DOUBLE,
            retrieved_at TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY (mena, datum)
        )
    """)
    _db.execute("""
        CREATE TABLE IF NOT EXISTS kurzy_coingecko (
            coingecko_id VARCHAR,
            datum DATE,
            cena_usd DOUBLE,
            retrieved_at TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY (coingecko_id, datum)
        )
    """)


def _db_conn() -> duckdb.DuckDBPyConnection:
    if _db is None:
        raise RuntimeError("ceny.init_db() must be called before price lookups")
    return _db


# ── ČNB ──────────────────────────────────────────────────────────────────────

def _fetch_cnb(d: date) -> dict[str, Decimal]:
    """Fetch ČNB daily rates for date d. Returns {mena: czk_per_1_unit}."""
    url = (
        "https://www.cnb.cz/cs/financni-trhy/devizovy-trh/"
        "kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/"
        f"denni_kurz.txt?date={datum_util.format_date_cnb(d)}"
    )
    text = http_util.get(url).decode("utf-8")
    rates: dict[str, Decimal] = {"CZK": Decimal("1")}
    lines = text.splitlines()
    for line in lines[2:]:  # skip header lines
        parts = line.split("|")
        if len(parts) < 5:
            continue
        try:
            mnozstvi = int(parts[2])
            kod = parts[3].strip().upper()
            kurz = Decimal(parts[4].strip().replace(",", "."))
            rates[kod] = kurz / Decimal(mnozstvi)
        except Exception:
            continue
    return rates


def _cache_cnb(d: date) -> None:
    rates = _fetch_cnb(d)
    db = _db_conn()
    for mena, kurz in rates.items():
        db.execute(
            "INSERT OR REPLACE INTO kurzy_cnb (mena, datum, mnozstvi, kurz_czk) VALUES (?, ?, ?, ?)",
            [mena, d, 1, float(kurz)],
        )


def _get_cnb_rate(mena: str, d: date) -> Decimal | None:
    """CZK per 1 unit of mena on date d. Tries cache first, then fetches."""
    mena = mena.upper()
    if mena == "CZK":
        return Decimal("1")

    # ČNB doesn't publish on weekends/holidays; roll back to last business day
    d_lookup = datum_util.predchozi_pracovni_den(d)

    db = _db_conn()
    row = db.execute(
        "SELECT kurz_czk FROM kurzy_cnb WHERE mena = ? AND datum = ?",
        [mena, d_lookup],
    ).fetchone()
    if row:
        return Decimal(str(row[0]))

    # Fetch and cache all currencies for that day
    _cache_cnb(d_lookup)
    time.sleep(0.2)  # gentle rate limit

    row = db.execute(
        "SELECT kurz_czk FROM kurzy_cnb WHERE mena = ? AND datum = ?",
        [mena, d_lookup],
    ).fetchone()
    return Decimal(str(row[0])) if row else None


def ziskej_kurz_cnb(mena: str, d: date) -> Decimal:
    """Return ČNB daily CZK rate for fiat currency. Raises if unavailable."""
    rate = _get_cnb_rate(mena, d)
    if rate is None:
        raise ValueError(f"ČNB kurz pro {mena} na {d} není k dispozici")
    return rate


# ── CryptoCompare ─────────────────────────────────────────────────────────────
# Free public API, no key required, rate limit ~100 req/min.
# Endpoint: /data/pricehistorical?fsym=BTC&tsyms=USD&ts=<unix>

def _fetch_cryptocompare(ticker: str, d: date) -> float | None:
    import calendar
    ts = int(calendar.timegm(d.timetuple()))
    url = (
        f"https://min-api.cryptocompare.com/data/pricehistorical"
        f"?fsym={ticker.upper()}&tsyms=USD&ts={ts}"
    )
    try:
        data = json.loads(http_util.get(url).decode("utf-8"))
        return float(data[ticker.upper()]["USD"])
    except (KeyError, json.JSONDecodeError, TypeError):
        return None


def _get_crypto_usd(ticker: str, d: date) -> Decimal | None:
    db = _db_conn()
    # Reuse the same cache table, keyed by ticker instead of coingecko_id
    row = db.execute(
        "SELECT cena_usd FROM kurzy_coingecko WHERE coingecko_id = ? AND datum = ?",
        [ticker.upper(), d],
    ).fetchone()
    if row:
        return Decimal(str(row[0]))

    time.sleep(0.7)  # CryptoCompare free tier: ~100 req/min → 0.6s+ between calls
    price_usd = _fetch_cryptocompare(ticker, d)
    if price_usd is not None:
        db.execute(
            "INSERT OR REPLACE INTO kurzy_coingecko (coingecko_id, datum, cena_usd) VALUES (?, ?, ?)",
            [ticker.upper(), d, price_usd],
        )
        return Decimal(str(price_usd))
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def ziskej_cenu_czk(ticker: str, d: date) -> Decimal | None:
    """Return CZK price of 1 unit of ticker on date d. Returns None if unavailable."""
    ticker = coin_util.normalizuj(ticker)

    if coin_util.je_fiat(ticker):
        try:
            return ziskej_kurz_cnb(ticker, d)
        except ValueError:
            return None

    if coin_util.klasifikace(ticker) == "ignore":
        return None

    usd_price = _get_crypto_usd(ticker, d)
    if usd_price is None:
        return None

    usd_czk = _get_cnb_rate("USD", d)
    if usd_czk is None:
        return None

    return usd_price * usd_czk


def ziskej_cenu_czk_or_raise(ticker: str, d: date) -> Decimal:
    price = ziskej_cenu_czk(ticker, d)
    if price is None:
        raise ValueError(f"CZK cena pro {ticker} na {d} není k dispozici")
    return price


if __name__ == "__main__":
    import argparse
    from datetime import date as date_cls

    parser = argparse.ArgumentParser()
    parser.add_argument("--coin", required=True)
    parser.add_argument("--datum", required=True, help="YYYY-MM-DD")
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()

    coin_util.init(args.config.parent if args.config.is_file() else args.config)
    init_db(args.cache)
    d = date_cls.fromisoformat(args.datum)
    price = ziskej_cenu_czk(args.coin, d)
    print(f"{args.coin} na {d}: {price} CZK")
