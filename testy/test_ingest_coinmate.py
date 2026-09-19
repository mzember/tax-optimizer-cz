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


def _v2(rows: str) -> str:
    return (
        "ID;Datum;Účet;Typ;Částka;Částka měny;Cena;Cena měny;Poplatek;Poplatek měny;"
        "Celkem;Celkem měny;Popisek;Status;První zůstatek po;První zůstatek po měně;"
        "Druhý zůstatek po;Druhý zůstatek po měně\n" + rows
    )


def test_market_orders_are_trades(tmp_path):
    """MARKET_BUY/MARKET_SELL jsou plnohodnotné obchody — dřív se tiše zahazovaly."""
    f = tmp_path / "tx.csv"
    f.write_text(_v2(
        "9101;2024-05-01 10:00:00;M;MARKET_BUY;0.002;BTC;1500000;CZK;7.5;CZK;-3007.5;CZK;;OK;0.1;BTC;0;CZK\n"
        "9102;2024-06-01 10:00:00;M;MARKET_SELL;-0.001;BTC;1600000;CZK;4;CZK;1596;CZK;;OK;0.099;BTC;1596;CZK\n"
    ), encoding="utf-8")
    rows = parse_file(f)
    assert [r["typ"] for r in rows] == ["NAKUP", "PRODEJ"]
    assert float(rows[0]["mnozstvi"]) == 0.002
    assert float(rows[0]["protistrana_mnozstvi"]) == 3000.0
    assert float(rows[1]["protistrana_mnozstvi"]) == 1600.0


def test_debit_credit_become_transfers_with_warning(tmp_path, capsys):
    """Interní korekce DEBIT/CREDIT → WITHDRAWAL/DEPOSIT (bez lotu) + WARN, ne tiché zahození."""
    f = tmp_path / "tx.csv"
    f.write_text(_v2(
        "9201;2024-02-10 08:00:00;M;DEBIT;-0.3;BTC;;;0;BTC;-0.3;BTC;;OK;0.2;BTC;0;CZK\n"
        "9202;2024-02-11 08:00:00;M;CREDIT;0.5;BTC;;;0;BTC;0.5;BTC;;OK;0.7;BTC;0;CZK\n"
        "9203;2024-02-12 08:00:00;M;AIRDROP;0.5;BTC;;;0;BTC;0.5;BTC;;OK;1.2;BTC;0;CZK\n"
    ), encoding="utf-8")
    rows = parse_file(f)
    assert [(r["id"], r["typ"]) for r in rows] == [
        ("coinmate:9201", "WITHDRAWAL"), ("coinmate:9202", "DEPOSIT"),
    ]
    err = capsys.readouterr().err
    assert "DEBIT" in err and "CREDIT" in err
    assert "neznámý typ 'AIRDROP'" in err
