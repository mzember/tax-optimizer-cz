"""Tests for validation checks."""

from decimal import Decimal
from danove.validace import (
    check_balance,
    check_sale_before_acq,
    check_duplicates,
    check_stablecoin,
)


def _row(typ, coin, mnozstvi, proto="CZK", proto_mnozstvi="0", datum="2024-01-01T10:00:00Z",
         rid="id1", cena_czk="100000"):
    return {
        "id": rid, "burza": "test", "datum_utc": datum, "typ": typ,
        "coin": coin, "mnozstvi": str(mnozstvi),
        "protistrana_coin": proto, "protistrana_mnozstvi": str(proto_mnozstvi),
        "cena_za_kus_czk": cena_czk, "fee_mnozstvi": "0", "fee_coin": "",
        "fee_czk": "0", "celkem_czk": str(Decimal(cena_czk) * Decimal(mnozstvi)),
    }


def test_negative_balance_detected():
    """Oversell musí byť hlášen (WARN — phantom lot to v optimalizaci ošetří)."""
    rows = [
        _row("NAKUP", "BTC", "1.0", rid="r1", datum="2020-01-01T00:00:00Z"),
        _row("PRODEJ", "BTC", "2.0", rid="r2", datum="2024-01-01T00:00:00Z"),  # oversell
    ]
    issues, balance = check_balance(rows)
    assert any("WARN" in i and "BTC" in i for i in issues)
    assert balance["BTC"] < 0


def test_valid_trades_no_error():
    rows = [
        _row("NAKUP", "BTC", "2.0", rid="r1", datum="2020-01-01T00:00:00Z"),
        _row("PRODEJ", "BTC", "1.0", rid="r2", datum="2024-01-01T00:00:00Z"),
    ]
    issues, balance = check_balance(rows)
    assert all("ERROR" not in i for i in issues)
    assert balance["BTC"] == Decimal("1.0")


def test_sale_before_acquisition():
    """PRODEJ bez předchozího NAKUP je WARN, ne ERROR — phantom lot v
    optimalizaci to zdaní v plné výši, takže pipeline nesmí padnout."""
    rows = [
        _row("PRODEJ", "ETH", "1.0", rid="r1", datum="2020-01-01T00:00:00Z"),
    ]
    issues = check_sale_before_acq(rows)
    assert any("WARN" in i and "ETH" in i for i in issues)
    assert all("ERROR" not in i for i in issues)


def test_duplicate_id():
    rows = [
        _row("NAKUP", "BTC", "1.0", rid="dup-id"),
        _row("PRODEJ", "BTC", "1.0", rid="dup-id"),
    ]
    issues = check_duplicates(rows)
    assert any("dup-id" in i for i in issues)


def test_stablecoin_warning():
    rows = [_row("NAKUP", "USDT", "100", proto="USD", proto_mnozstvi="100")]
    issues = check_stablecoin(rows)
    assert any("WARN" in i and "USDT" in i for i in issues)
