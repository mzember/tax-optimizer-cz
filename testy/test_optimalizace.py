"""Tests for LP optimization — key golden test: LP beats HIFO across years.

Scenario S2 (revidovaný):
  Nakup 1 BTC @ 1 000 000 CZK dne 2022-01-01
  Nakup 1 BTC @ 500 000 CZK  dne 2022-01-02
  Prodej 1 BTC @ 700 000 CZK dne 2024-06-01  (3y test: 2022-01-01+3y=2025-01-01 > 2024-06-01 → NE)
  Prodej 1 BTC @ 1 200 000 CZK dne 2024-12-31 (3y test: 2022-01-01+3y=2025-01-01 > 2024-12-31 → NE)

HIFO pro 2024:
  Prodej1 @ 700k použije lot 1M → ztráta 300k (zahozená)
  Prodej2 @ 1,2M použije lot 500k → zisk 700k zdanitelný
  Celkem zdanitelný zisk 2024: 700 000 CZK

LP (globální):
  Prodej1 @ 700k použije lot 500k → zisk 200k zdanitelný
  Prodej2 @ 1,2M použije lot 1M  → zisk 200k zdanitelný
  Celkem zdanitelný zisk 2024: 400 000 CZK ← 300 000 CZK úspora

Scenario S3 — exempt:
  Nakup 1 BTC @ 100 000 CZK dne 2020-01-01
  Prodej 1 BTC @ 300 000 CZK dne 2023-06-01 (3y test: 2020-01-01+3y=2023-01-01 < 2023-06-01 → ANO)
  Zdanitelný zisk: 0 CZK, osvobozeno: 200 000 CZK
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from danove.optimalizace import Lot, Sale, _solve
from danove.util import coin as coin_util

CONFIG_DIR = Path(__file__).parent.parent / "config"


@pytest.fixture(autouse=True)
def init_coins():
    coin_util.init(CONFIG_DIR)


def _make_lot(lid, coin, nakup_date, mnozstvi, cena, fee=Decimal("0")):
    return Lot(
        lot_id=lid,
        coin=coin,
        datum_nakupu=date.fromisoformat(nakup_date),
        mnozstvi=Decimal(str(mnozstvi)),
        cena_za_kus_czk=Decimal(str(cena)),
        fee_czk_total=Decimal(str(fee)),
    )


def _make_sale(sid, coin, prodej_date, mnozstvi, prijem):
    d = date.fromisoformat(prodej_date)
    return Sale(
        sale_id=sid,
        coin=coin,
        datum_prodeje=d,
        mnozstvi=Decimal(str(mnozstvi)),
        prijem_za_kus_czk=Decimal(str(prijem)),
        rok=d.year,
    )


def _total_zdanitelny(parovani):
    total = Decimal("0")
    for p in parovani:
        if p["osvobozeno"] == "ne":
            zisk = Decimal(p["zisk_czk"])
            if zisk > 0:
                total += zisk
    return total


def test_lp_beats_hifo_s2():
    """LP must find 400 000 CZK zdanitelný zisk, not HIFO's 700 000."""
    loty = [
        _make_lot("lot1", "BTC", "2022-01-01", "1.0", "1000000"),
        _make_lot("lot2", "BTC", "2022-01-02", "1.0", "500000"),
    ]
    prodeje = [
        _make_sale("sale1", "BTC", "2024-06-01", "1.0", "700000"),
        _make_sale("sale2", "BTC", "2024-12-31", "1.0", "1200000"),
    ]

    parovani = _solve(loty, prodeje, {}, [])
    zdanitelny = _total_zdanitelny(parovani)

    # LP optimal: 200k + 200k = 400k
    assert zdanitelny == Decimal("400000"), (
        f"Očekáváno 400 000 CZK zdanitelný zisk (LP), dostáno {zdanitelny}. "
        "HIFO by dalo 700 000 CZK."
    )


def test_exempt_after_3_years_s3():
    """Sale more than 3 years after acquisition must be fully exempt."""
    loty = [_make_lot("lot1", "BTC", "2020-01-01", "1.0", "100000")]
    prodeje = [_make_sale("sale1", "BTC", "2023-06-01", "1.0", "300000")]

    parovani = _solve(loty, prodeje, {}, [])
    assert len(parovani) == 1
    assert parovani[0]["osvobozeno"] == "ano"
    zdanitelny = _total_zdanitelny(parovani)
    assert zdanitelny == Decimal("0")


def test_partial_lot_split():
    """Selling 0.5 BTC from a 1 BTC lot — only half should be consumed."""
    loty = [_make_lot("lot1", "BTC", "2024-01-01", "1.0", "500000")]
    prodeje = [_make_sale("sale1", "BTC", "2024-06-01", "0.5", "600000")]

    parovani = _solve(loty, prodeje, {}, [])
    assert len(parovani) == 1
    qty = Decimal(parovani[0]["mnozstvi_pouzite"])
    assert abs(qty - Decimal("0.5")) < Decimal("0.00001")


def test_exempt_before_taxable():
    """If one lot is exempt and another is taxable, LP must prefer the exempt one."""
    loty = [
        _make_lot("old_lot", "BTC", "2019-01-01", "1.0", "100000"),  # > 3y by 2024
        _make_lot("new_lot", "BTC", "2023-01-01", "1.0", "200000"),  # taxable
    ]
    prodeje = [_make_sale("sale1", "BTC", "2024-06-01", "1.0", "500000")]

    parovani = _solve(loty, prodeje, {}, [])
    # Only one pair should be non-trivial
    used = [p for p in parovani if Decimal(p["mnozstvi_pouzite"]) > Decimal("0.0001")]
    assert len(used) == 1
    assert used[0]["lot_id"] == "old_lot"
    assert used[0]["osvobozeno"] == "ano"


def test_multiple_coins_independent():
    """BTC and LTC lots must not be mixed."""
    loty = [
        _make_lot("btc1", "BTC", "2024-01-01", "1.0", "500000"),
        _make_lot("ltc1", "LTC", "2024-01-01", "10.0", "3000"),
    ]
    prodeje = [
        _make_sale("btc_sale", "BTC", "2024-06-01", "1.0", "600000"),
        _make_sale("ltc_sale", "LTC", "2024-06-01", "10.0", "4000"),
    ]

    parovani = _solve(loty, prodeje, {}, [])
    btc_rows = [p for p in parovani if p["coin"] == "BTC"]
    ltc_rows = [p for p in parovani if p["coin"] == "LTC"]
    assert btc_rows[0]["lot_id"] == "btc1"
    assert ltc_rows[0]["lot_id"] == "ltc1"


# ── Tests A–I: phantom lot correctness and LP invariants ─────────────────────

def test_lot_split_exempt_and_taxable():
    """A — one lot split between an exempt and a taxable sale."""
    # Lot 2021-06-15, 3y boundary = 2024-06-15.
    loty = [_make_lot("lot1", "BTC", "2021-06-15", "1.0", "200000")]
    prodeje = [
        _make_sale("sA", "BTC", "2024-05-01", "0.4", "500000"),  # gap < 3y → taxable
        _make_sale("sB", "BTC", "2024-07-01", "0.6", "600000"),  # gap > 3y → exempt
    ]
    parovani = _solve(loty, prodeje, {}, [])
    osv = [p for p in parovani if p["osvobozeno"] == "ano"]
    neosv = [p for p in parovani if p["osvobozeno"] == "ne"]
    assert len(osv) == 1 and osv[0]["prodej_id"] == "sB"
    assert len(neosv) == 1 and neosv[0]["prodej_id"] == "sA"
    assert abs(Decimal(neosv[0]["mnozstvi_pouzite"]) - Decimal("0.4")) < Decimal("0.00001")
    assert abs(Decimal(osv[0]["mnozstvi_pouzite"]) - Decimal("0.6")) < Decimal("0.00001")


def test_phantom_never_exempt():
    """B — phantom lot covering a shortage is always taxable (osvobozeno=ne)."""
    loty = [_make_lot("real1", "BTC", "2017-01-01", "0.5", "10000")]
    prodeje = [_make_sale("s1", "BTC", "2025-06-01", "1.0", "1000000")]
    parovani = _solve(loty, prodeje, {}, [])
    real_rows = [p for p in parovani if p["lot_id"] == "real1"]
    phantom_rows = [p for p in parovani if p["lot_id"].startswith("phantom:")]
    assert real_rows[0]["osvobozeno"] == "ano"   # 8+ year gap
    assert phantom_rows[0]["osvobozeno"] == "ne"  # gap = 0


def test_more_real_lots_never_increases_tax():
    """C — adding more real acquisition data never raises the tax."""
    prodeje = [_make_sale("s1", "BTC", "2025-06-01", "1.0", "1000000")]
    par_before = _solve(
        [_make_lot("L1", "BTC", "2020-01-01", "0.3", "100000")],
        prodeje, {}, [],
    )
    par_after = _solve(
        [
            _make_lot("L1", "BTC", "2020-01-01", "0.3", "100000"),
            _make_lot("L2", "BTC", "2019-05-01", "0.4", "80000"),
        ],
        prodeje, {}, [],
    )
    assert _total_zdanitelny(par_after) <= _total_zdanitelny(par_before)


def test_phantom_mixes_with_real_for_same_sale():
    """D — real exempt lot + phantom taxable lot cover one sale with partial shortage."""
    loty = [_make_lot("real1", "BTC", "2017-01-01", "0.3", "10000")]
    prodeje = [_make_sale("s1", "BTC", "2025-06-01", "1.0", "1000000")]
    parovani = _solve(loty, prodeje, {}, [])
    real_use = sum(Decimal(p["mnozstvi_pouzite"]) for p in parovani if p["lot_id"] == "real1")
    phantom_use = sum(Decimal(p["mnozstvi_pouzite"]) for p in parovani
                      if p["lot_id"].startswith("phantom:"))
    assert abs(real_use - Decimal("0.3")) < Decimal("0.00001")
    assert abs(phantom_use - Decimal("0.7")) < Decimal("0.00001")
    assert abs(_total_zdanitelny(parovani) - Decimal("700000")) < Decimal("1")  # 0.7*1M, LP precision


def test_no_phantom_when_sufficient_real_lots():
    """E — no phantom lots created when real supply meets demand."""
    loty = [_make_lot("L1", "BTC", "2020-01-01", "2.0", "100000")]
    prodeje = [_make_sale("s1", "BTC", "2024-06-01", "1.0", "200000")]
    parovani = _solve(loty, prodeje, {}, [])
    assert all("phantom" not in p["lot_id"] for p in parovani)


def test_phantom_does_not_leak_between_sales():
    """F — phantom for sale_A cannot pair with sale_B (phantom_for_sale_id isolation)."""
    prodeje = [
        _make_sale("s_2025", "BTC", "2025-06-01", "1.0", "500000"),
        _make_sale("s_2026", "BTC", "2026-06-01", "1.0", "800000"),
    ]
    parovani = _solve([], prodeje, {}, [])
    for p in parovani:
        assert p["datum_nakupu"] == p["datum_prodeje"], "phantom must have gap=0"
        assert p["osvobozeno"] == "ne"
    assert _total_zdanitelny(parovani) == Decimal("1300000")  # 500k + 800k, cost=0


def test_lot_fee_proportional_on_split():
    """G — lot fee is allocated proportionally when only part of the lot is used."""
    # 1 BTC lot, fee 1000. Using 0.4 BTC → naklad = 0.4*500000 + 0.4*1000 = 200400
    loty = [_make_lot("L1", "BTC", "2024-01-01", "1.0", "500000", fee="1000")]
    prodeje = [_make_sale("s1", "BTC", "2024-06-01", "0.4", "600000")]
    parovani = _solve(loty, prodeje, {}, [])
    naklad = Decimal(parovani[0]["naklad_czk"])
    assert naklad == Decimal("200400.00")


def test_year_loss_zero_tax_not_negative():
    """H — a loss-making sale produces tax_base = 0, never negative (§10 ZDP)."""
    loty = [_make_lot("L1", "BTC", "2024-01-01", "1.0", "1000000")]
    prodeje = [_make_sale("s1", "BTC", "2024-06-01", "1.0", "500000")]  # loss 500k
    parovani = _solve(loty, prodeje, {}, [])
    assert _total_zdanitelny(parovani) == Decimal("0")


def test_hifo_when_all_taxable():
    """I — LP picks highest cost basis (HIFO-like) when all lots are taxable."""
    loty = [
        _make_lot("cheap",     "BTC", "2023-01-01", "1.0", "300000"),
        _make_lot("expensive", "BTC", "2023-01-02", "1.0", "800000"),
    ]
    prodeje = [_make_sale("s1", "BTC", "2024-06-01", "1.0", "1000000")]
    parovani = _solve(loty, prodeje, {}, [])
    used = [p for p in parovani if Decimal(p["mnozstvi_pouzite"]) > Decimal("0.0001")]
    assert len(used) == 1
    assert used[0]["lot_id"] == "expensive"
    assert abs(_total_zdanitelny(parovani) - Decimal("200000")) < Decimal("2")  # 1M − 800k, LP precision
