"""Tests for Bitstamp ingest."""

import csv
from pathlib import Path
from danove.ingest.bitstamp import parse_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_market_buy():
    rows = parse_file(FIXTURES / "ukazka_bitstamp.csv")
    nakupy = [r for r in rows if r["typ"] == "NAKUP"]
    assert len(nakupy) == 1
    n = nakupy[0]
    assert n["coin"] == "BTC"
    assert float(n["mnozstvi"]) == 2.0
    assert n["protistrana_coin"] == "CZK"
    assert float(n["protistrana_mnozstvi"]) == 200000.0


def test_market_sell():
    rows = parse_file(FIXTURES / "ukazka_bitstamp.csv")
    prodeje = [r for r in rows if r["typ"] == "PRODEJ"]
    assert len(prodeje) == 1
    p = prodeje[0]
    assert p["coin"] == "BTC"
    assert float(p["mnozstvi"]) == 1.0
    assert float(p["protistrana_mnozstvi"]) == 300000.0
