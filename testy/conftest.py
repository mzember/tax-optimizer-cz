"""Shared pytest fixtures."""

import csv
import io
import tempfile
from pathlib import Path

import pytest
import duckdb

from danove.util import coin as coin_util
from danove.util import http as http_util


FIXTURES = Path(__file__).parent / "fixtures"
CONFIG_DIR = Path(__file__).parent.parent / "config"


@pytest.fixture(autouse=True)
def init_coins():
    coin_util.init(CONFIG_DIR)


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def tmp_cache(tmp_path):
    """Return path to a temporary DuckDB cache file."""
    return tmp_path / "cache.duckdb"


@pytest.fixture
def mock_http():
    """Context manager that injects mock HTTP responses."""
    responses: dict[str, bytes] = {}

    def handler(url: str) -> bytes:
        for pattern, data in responses.items():
            if pattern in url:
                return data
        raise RuntimeError(f"Žádná mock odpověď pro URL: {url}")

    http_util.inject_mock(handler)
    yield responses
    http_util.inject_mock(None)


def make_cnb_response(rates: dict[str, float]) -> bytes:
    """Build a minimal ČNB daily rate response."""
    lines = ["01.01.2020 #1", "Country|Currency|Quantity|Code|Rate"]
    country_map = {"EUR": "EMU|euro", "USD": "USA|dollar", "GBP": "Velká Británie|libra",
                   "CHF": "Švýcarsko|frank"}
    for code, rate in rates.items():
        country = country_map.get(code, f"X|{code.lower()}")
        lines.append(f"{country}|1|{code}|{rate:.3f}")
    return "\n".join(lines).encode("utf-8")


def make_cryptocompare_response(ticker: str, usd_price: float) -> bytes:
    import json
    return json.dumps({ticker.upper(): {"USD": usd_price}}).encode("utf-8")
