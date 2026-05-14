"""Tests for Bittrex ingest including UTF-16 detection."""

import csv
from pathlib import Path
import tempfile
import pytest
from danove.ingest.bittrex import parse_file

FIXTURES = Path(__file__).parent / "fixtures"


def _write_utf16_csv(path: Path, content: str) -> None:
    path.write_bytes(b"\xff\xfe" + content.encode("utf-16-le"))


def test_utf16_detection(tmp_path):
    f = tmp_path / "orders.csv"
    _write_utf16_csv(
        f,
        "OrderUuid,Exchange,Type,Quantity,Limit,CommissionPaid,Price,Opened,Closed\n"
        "aaaa-1111,BTC-LTC,LIMIT_BUY,10.0,0.005,0.0001,0.05,"
        "1/1/2020 10:00:00 AM,1/1/2020 10:01:00 AM\n",
    )
    rows = parse_file(f)
    assert len(rows) == 1
    r = rows[0]
    assert r["typ"] == "NAKUP"
    assert r["coin"] == "LTC"
    assert r["protistrana_coin"] == "BTC"
    assert float(r["mnozstvi"]) == 10.0


def test_limit_sell_utf16(tmp_path):
    f = tmp_path / "orders.csv"
    _write_utf16_csv(
        f,
        "OrderUuid,Exchange,Type,Quantity,Limit,CommissionPaid,Price,Opened,Closed\n"
        "bbbb-2222,USDT-BTC,LIMIT_SELL,0.5,9000,4.5,4495.5,"
        "6/1/2023 09:00:00 AM,6/1/2023 09:01:00 AM\n",
    )
    rows = parse_file(f)
    assert len(rows) == 1
    r = rows[0]
    assert r["typ"] == "PRODEJ"
    assert r["coin"] == "BTC"
    assert r["protistrana_coin"] == "USDT"


def test_tx_history_deposit(tmp_path):
    f = tmp_path / "tx.csv"
    f.write_text(
        "Date,Currency,Type,Address,Memo/Tag,TxId,Amount\n"
        "2024-01-01 10:00:00.000,BTC,DEPOSIT,,,,+0.5\n",
        encoding="utf-8",
    )
    rows = parse_file(f)
    deps = [r for r in rows if r["typ"] == "DEPOSIT"]
    assert len(deps) == 1
    assert deps[0]["coin"] == "BTC"
    assert float(deps[0]["mnozstvi"]) == 0.5
