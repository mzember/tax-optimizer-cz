"""Tests for Coinmate ingest."""

from pathlib import Path
from danove.ingest.coinmate import parse_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_buy():
    rows = parse_file(FIXTURES / "ukazka_coinmate.csv")
    nakupy = [r for r in rows if r["typ"] == "NAKUP"]
    assert len(nakupy) == 1
    n = nakupy[0]
    assert n["coin"] == "BTC"
    assert float(n["mnozstvi"]) == 1.0
    assert n["protistrana_coin"] == "CZK"
    # gross = cena * castka = 100000 * 1.0 = 100000
    assert float(n["protistrana_mnozstvi"]) == 100000.0


def test_sell():
    rows = parse_file(FIXTURES / "ukazka_coinmate.csv")
    prodeje = [r for r in rows if r["typ"] == "PRODEJ"]
    assert len(prodeje) == 1
    p = prodeje[0]
    assert p["coin"] == "BTC"
    assert float(p["mnozstvi"]) == 1.0
    assert float(p["protistrana_mnozstvi"]) == 300000.0


def test_v2_with_ucet(tmp_path):
    """Test Coinmate v2 format with Účet column."""
    f = tmp_path / "tx.csv"
    f.write_text(
        "ID;Datum;Účet;Typ;Částka;Částka měny;Cena;Cena měny;Poplatek;Poplatek měny;"
        "Celkem;Celkem měny;Popisek;Status;První zůstatek po;První zůstatek po měně;"
        "Druhý zůstatek po;Druhý zůstatek po měně\n"
        "9001;2024-04-01 10:00:00;M;SELL;-0.001;BTC;1500000;CZK;2.25;CZK;"
        "1497.75;CZK;;OK;0.1;BTC;100000;CZK\n",
        encoding="utf-8",
    )
    rows = parse_file(f)
    assert len(rows) == 1
    assert rows[0]["typ"] == "PRODEJ"
    assert rows[0]["coin"] == "BTC"
