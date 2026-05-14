"""Tests for Binance ingest."""

import csv
from pathlib import Path
import pytest
from danove.ingest.binance import parse_file, run

FIXTURES = Path(__file__).parent / "fixtures"


def test_deposit_row():
    rows = parse_file(FIXTURES / "ukazka_binance.csv")
    deposits = [r for r in rows if r["typ"] == "DEPOSIT"]
    assert len(deposits) == 1
    assert deposits[0]["coin"] == "BTC"
    assert float(deposits[0]["mnozstvi"]) == 2.0


def test_trade_cluster():
    """Three rows with same timestamp should produce one NAKUP trade."""
    rows = parse_file(FIXTURES / "ukazka_binance.csv")
    nakupy = [r for r in rows if r["typ"] == "NAKUP"]
    assert len(nakupy) == 1
    trade = nakupy[0]
    assert trade["coin"] == "BTC"
    assert trade["protistrana_coin"] == "LTC"
    assert float(trade["protistrana_mnozstvi"]) == 5.0
    assert float(trade["fee_mnozstvi"]) > 0
    assert trade["fee_coin"] == "BTC"


def test_run_writes_csv(tmp_path):
    src = tmp_path / "binance"
    src.mkdir()
    (src / "test.csv").write_text(
        "User ID,Time,Account,Operation,Coin,Change,Remark\n"
        "1,20-01-01 10:00:00,Spot,Deposit,BTC,1.0,\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.csv"
    run(src, out)
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 1
    assert rows[0]["typ"] == "DEPOSIT"
