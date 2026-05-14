"""Tests for CZK price lookups (with HTTP mocking, no real network calls)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from danove import ceny
from danove.util import coin as coin_util
from testy.conftest import make_cnb_response, make_cryptocompare_response

CONFIG_DIR = Path(__file__).parent.parent / "config"


@pytest.fixture(autouse=True)
def fresh_db(tmp_cache, monkeypatch):
    ceny.init_db(tmp_cache)
    yield
    # reset global db
    ceny._db = None


def test_czk_returns_one():
    assert ceny.ziskej_cenu_czk("CZK", date(2024, 1, 1)) == Decimal("1")


def test_eur_from_cnb(mock_http):
    mock_http["denni_kurz"] = make_cnb_response({"EUR": 25.0, "USD": 23.0})
    rate = ceny.ziskej_cenu_czk("EUR", date(2024, 1, 1))
    assert rate == Decimal("25.000")


def test_btc_from_cryptocompare_via_usd(mock_http):
    mock_http["denni_kurz"] = make_cnb_response({"USD": 23.0})
    mock_http["cryptocompare"] = make_cryptocompare_response("BTC", 50000.0)
    price = ceny.ziskej_cenu_czk("BTC", date(2024, 1, 15))
    # 50000 USD * 23 CZK/USD = 1 150 000 CZK
    assert price is not None
    assert abs(price - Decimal("1150000")) < Decimal("1")


def test_cache_hit(mock_http, tmp_cache):
    ceny.init_db(tmp_cache)
    call_count = {"n": 0}
    original_responses = {"denni_kurz": make_cnb_response({"EUR": 25.0})}

    def counting_handler(url):
        call_count["n"] += 1
        for k, v in original_responses.items():
            if k in url:
                return v
        raise RuntimeError(f"Unknown URL: {url}")

    from danove.util import http as http_util
    http_util.inject_mock(counting_handler)
    try:
        ceny.ziskej_cenu_czk("EUR", date(2024, 1, 2))
        ceny.ziskej_cenu_czk("EUR", date(2024, 1, 2))  # should hit cache
        assert call_count["n"] == 1  # only one HTTP call
    finally:
        http_util.inject_mock(None)


def test_unknown_coin_returns_none():
    price = ceny.ziskej_cenu_czk("UNKNOWNCOIN999", date(2024, 1, 1))
    assert price is None
