"""Zákonné požadavky — co by si prošel inspektor finančního úřadu.

Tyto testy nejsou postaveny na implementaci; vycházejí přímo ze ZDP a
Pokynu GFŘ ke kryptu. Když je kód jednou napsán jinak, lámou se právě ony.

Pokrývané paragrafy
-------------------
§4 odst. 1 písm. zk) ZDP — časový test 3 roky pro osvobození disposalu.
§10 odst. 1 ZDP        — krypto = "ostatní příjem", druh příjmu.
§10 odst. 4 ZDP        — základ nemůže být záporný, ztráta se nepřenáší.
§3 odst. 2 ZDP         — nepeněžní příjem; crypto-crypto je v CZK ekvivalentu.
Pokyn GFŘ ke krypto     — crypto-crypto směna = 2 zdaňované události (disposal + acquisition).
Doklady (§ 92 DŘ)       — bez doložené pořizovací ceny se zdaní celý příjem.

Testy záměrně používají malé celočíselné CZK částky, aby byly aritmeticky
ověřitelné na okem.
"""

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from danove.optimalizace import Lot, Sale, _solve, _load_enriched
from danove.util import coin as coin_util
from danove.util.datum import je_osvobozeno

CONFIG_DIR = Path(__file__).parent.parent / "config"


@pytest.fixture(autouse=True)
def init_coins():
    coin_util.init(CONFIG_DIR)


# ── Pomocné konstruktory ────────────────────────────────────────────────────

def _lot(lid, coin, nakup, mnozstvi, cena, fee=Decimal("0")):
    return Lot(
        lot_id=lid,
        coin=coin,
        datum_nakupu=date.fromisoformat(nakup),
        mnozstvi=Decimal(str(mnozstvi)),
        cena_za_kus_czk=Decimal(str(cena)),
        fee_czk_total=Decimal(str(fee)),
    )


def _sale(sid, coin, prodej, mnozstvi, prijem_za_kus):
    d = date.fromisoformat(prodej)
    return Sale(
        sale_id=sid, coin=coin, datum_prodeje=d,
        mnozstvi=Decimal(str(mnozstvi)),
        prijem_za_kus_czk=Decimal(str(prijem_za_kus)),
        rok=d.year,
    )


def _base_per_year(parovani):
    """Základ daně podle §10 odst. 4: max(0, netto neosvobozených) per rok."""
    per_year: dict[int, Decimal] = {}
    for p in parovani:
        if p["osvobozeno"] != "ne":
            continue
        y = int(p["rok_prodeje"])
        per_year[y] = per_year.get(y, Decimal("0")) + Decimal(p["zisk_czk"])
    return {y: max(Decimal("0"), v) for y, v in per_year.items()}


# ── §4 odst. 1 písm. zk) — časový test 3 roky ───────────────────────────────

def test_3y_exact_boundary_NOT_exempt():
    """Prodej přesně 3 roky po nákupu STÁLE NEosvobozen.

    §4 vyžaduje "dobu mezi nabytím a převodem přesahující 3 roky" — striktně více.
    je_osvobozeno: datum_prodeje > nakup+3y, nikoli ≥.
    """
    nakup = date(2021, 6, 15)
    prodej = date(2024, 6, 15)  # přesně 3 roky
    assert je_osvobozeno(nakup, prodej) is False


def test_3y_one_day_after_exempt():
    """Prodej 3 roky a 1 den po nákupu už osvobozen."""
    nakup = date(2021, 6, 15)
    prodej = date(2024, 6, 16)
    assert je_osvobozeno(nakup, prodej) is True


def test_3y_leap_year_29feb_acquisition():
    """Nákup 29. 2. (přestupný rok): hranice se po 3 letech posouvá na 28. 2.

    2020-02-29 + 3y neexistuje → hranice = 2023-02-28.
    Prodej 2023-03-01 už ji přesahuje → osvobozeno.
    """
    nakup = date(2020, 2, 29)
    assert je_osvobozeno(nakup, date(2023, 2, 28)) is False  # přesně hranice
    assert je_osvobozeno(nakup, date(2023, 3, 1)) is True


def test_3y_same_day_sale_not_exempt():
    """Prodej v den nákupu jistě není osvobozen (gap = 0)."""
    d = date(2024, 5, 1)
    assert je_osvobozeno(d, d) is False


# ── §10 odst. 4 — netting v rámci roku + zákaz převodu ───────────────────────

def test_within_year_netting_gain_minus_loss():
    """V rámci jednoho roku ztráta SNIŽUJE zisk (current interpretation).

    Rok 2024: zisk 100 000 + ztráta 60 000 → základ daně = 40 000 (ne 100 000).
    Viz [[netting-vyklad-10]]: krypto je jeden druh příjmu, netting v rámci roku
    je dnešní převažující výklad. Pokud bys přepl na strict per-transaction,
    tento test selže — a to je správně, aby ses zastavil a uvědomil změnu.
    """
    loty = [
        _lot("Lcheap", "BTC", "2024-01-01", "1.0", "100000"),    # → drahší prodej = zisk
        _lot("Lexp",   "BTC", "2024-01-02", "1.0", "300000"),    # → levnější prodej = ztráta
    ]
    prodeje = [
        _sale("Sgain", "BTC", "2024-06-01", "1.0", "200000"),    # Lcheap → +100k; Lexp → -100k
        _sale("Sloss", "BTC", "2024-07-01", "1.0", "240000"),    # Lcheap → +140k; Lexp → -60k
    ]
    parovani = _solve(loty, prodeje, {}, [])
    base = _base_per_year(parovani)
    # Optimální párování (minimalizuje daň): Lcheap→Sgain (+100k), Lexp→Sloss (-60k) → netto 40k
    # Anti-optimální by bylo Lcheap→Sloss (+140k), Lexp→Sgain (-100k) → netto 40k
    # Obě varianty po nettingu dají 40 000. LP najde 40k základ.
    assert base.get(2024) == Decimal("40000"), (
        f"Netting v rámci roku selhal: očekáván základ 40 000, dostáno {base.get(2024)}. "
        "Pokud základ = 100 000, byl odstraněn netting (strict per-transaction)."
    )


def test_within_year_total_loss_base_zero():
    """Netto záporný rok → základ = 0, nikdy ne záporné číslo (§10 odst. 4)."""
    loty = [_lot("L1", "BTC", "2024-01-01", "1.0", "500000")]
    prodeje = [_sale("S1", "BTC", "2024-06-01", "1.0", "200000")]  # ztráta 300k
    parovani = _solve(loty, prodeje, {}, [])
    base = _base_per_year(parovani)
    # Žádný zisk → base[2024] může být 0 nebo chybět v dict (žádný non-exempt zisk).
    assert base.get(2024, Decimal("0")) == Decimal("0")


def test_loss_does_NOT_carry_forward():
    """§10 odst. 4: ztráta z roku N nesnižuje základ roku N+1.

    Rok 2024 ztráta 300k, rok 2025 zisk 200k → 2025 zdaní celých 200k.
    """
    loty = [
        _lot("L2024", "BTC", "2024-01-01", "1.0", "500000"),    # spotřebovaná v 2024 prodejem se ztrátou
        _lot("L2025", "ETH", "2024-01-01", "1.0", "100000"),    # bude prodán v 2025 se ziskem
    ]
    prodeje = [
        _sale("Sloss2024", "BTC", "2024-06-01", "1.0", "200000"),   # ztráta -300k v 2024
        _sale("Sgain2025", "ETH", "2025-06-01", "1.0", "300000"),   # zisk  +200k v 2025
    ]
    parovani = _solve(loty, prodeje, {}, [])
    base = _base_per_year(parovani)
    assert base.get(2024, Decimal("0")) == Decimal("0"),  "2024 musí být 0 (max(0, -300k))"
    assert base.get(2025) == Decimal("200000"), (
        f"2025 musí zdanit celých 200 000, dostáno {base.get(2025)}. "
        "Pokud {base.get(2025)} < 200 000, ztráta z 2024 se chybně přenáší."
    )


def test_years_independent_no_pooling():
    """Zisky se přes roky NESPOJUJÍ — každý rok zdaněn samostatně."""
    loty = [
        _lot("L24", "BTC", "2024-01-01", "1.0", "100000"),
        _lot("L25", "BTC", "2024-01-02", "1.0", "100000"),
    ]
    prodeje = [
        _sale("S24", "BTC", "2024-06-01", "1.0", "300000"),  # zisk +200k v 2024
        _sale("S25", "BTC", "2025-06-01", "1.0", "300000"),  # zisk +200k v 2025
    ]
    parovani = _solve(loty, prodeje, {}, [])
    base = _base_per_year(parovani)
    assert base.get(2024) == Decimal("200000")
    assert base.get(2025) == Decimal("200000")
    # Pokud by pooling existoval, celek 400k by se rozpočítal jinak nebo
    # se zaznamenal jako jeden rok — to by tady padlo.


# ── §3 odst. 2 + Pokyn GFŘ — crypto-crypto = dvě události ───────────────────

def test_crypto_to_crypto_creates_sale_and_lot(tmp_path: Path):
    """Pokyn GFŘ: krypto-krypto výměna = disposal (sale BTC) + acquisition (lot ETH).

    Zapsáno jako jeden řádek typu NAKUP ETH s protistrana=BTC, ale ve výstupu
    musí být LOT ETH i SALE BTC — jinak by se polovina obchodu ztratila.
    """
    csv_path = tmp_path / "obohaceno.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "id", "burza", "datum_utc", "typ", "coin", "mnozstvi",
            "protistrana_coin", "protistrana_mnozstvi", "fee_mnozstvi", "fee_coin",
            "cena_za_kus_czk", "celkem_czk", "fee_czk", "kurz_zdroj", "zdroj_radek",
        ])
        w.writeheader()
        w.writerow({
            "id": "tx1", "burza": "x", "datum_utc": "2024-06-01T12:00:00Z",
            "typ": "NAKUP", "coin": "ETH", "mnozstvi": "10",
            "protistrana_coin": "BTC", "protistrana_mnozstvi": "0.5",
            "fee_mnozstvi": "0", "fee_coin": "",
            "cena_za_kus_czk": "50000", "celkem_czk": "500000", "fee_czk": "0",
            "kurz_zdroj": "", "zdroj_radek": "",
        })

    loty, prodeje = _load_enriched(csv_path)
    eth_lots = [l for l in loty if l.coin == "ETH"]
    btc_sales = [s for s in prodeje if s.coin == "BTC"]
    assert len(eth_lots) == 1, f"Chybí acquisition ETH: {loty}"
    assert len(btc_sales) == 1, f"Chybí disposal BTC: {prodeje}"
    # CZK hodnoty obou nohou se rovnají (jedno tx, jedna CZK protihodnota)
    assert eth_lots[0].cena_za_kus_czk * eth_lots[0].mnozstvi == Decimal("500000")
    assert btc_sales[0].prijem_za_kus_czk * btc_sales[0].mnozstvi == Decimal("500000")


def test_sale_fee_reduces_proceeds(tmp_path: Path):
    """Prodejní poplatek snižuje příjem (ne přidává náklad).

    Příjem v daňovém přiznání = co reálně dostaneme po srážce poplatku burzy.
    PRODEJ 1 BTC za 100 000 s poplatkem 5 000 → čistý příjem 95 000.
    """
    csv_path = tmp_path / "obohaceno.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "id", "burza", "datum_utc", "typ", "coin", "mnozstvi",
            "protistrana_coin", "protistrana_mnozstvi", "fee_mnozstvi", "fee_coin",
            "cena_za_kus_czk", "celkem_czk", "fee_czk", "kurz_zdroj", "zdroj_radek",
        ])
        w.writeheader()
        w.writerow({
            "id": "txS", "burza": "x", "datum_utc": "2024-06-01T12:00:00Z",
            "typ": "PRODEJ", "coin": "BTC", "mnozstvi": "1",
            "protistrana_coin": "CZK", "protistrana_mnozstvi": "100000",
            "fee_mnozstvi": "0", "fee_coin": "",
            "cena_za_kus_czk": "100000", "celkem_czk": "100000", "fee_czk": "5000",
            "kurz_zdroj": "", "zdroj_radek": "",
        })
    _, prodeje = _load_enriched(csv_path)
    assert len(prodeje) == 1
    # příjem za kus = (celkem - fee) / mnozstvi = (100000 - 5000) / 1 = 95000
    assert prodeje[0].prijem_za_kus_czk == Decimal("95000")


def test_acquisition_fee_part_of_cost_basis():
    """Nákupní poplatek je součástí nákladové ceny — vstupuje do `naklad_czk`.

    Officer: zisk = příjem – (cena nákupu + pořizovací poplatek).
    """
    loty = [_lot("L", "BTC", "2024-01-01", "1.0", "100000", fee="2000")]
    prodeje = [_sale("S", "BTC", "2024-06-01", "1.0", "150000")]
    parovani = _solve(loty, prodeje, {}, [])
    naklad = Decimal(parovani[0]["naklad_czk"])
    zisk = Decimal(parovani[0]["zisk_czk"])
    assert naklad == Decimal("102000.00000000")
    assert zisk == Decimal("48000.00000000")


# ── Doklady (§92 DŘ) — phantom safety net ────────────────────────────────────

def test_phantom_full_proceeds_taxed():
    """Bez doloženého nákupu se zdaní celý příjem (cost basis = 0)."""
    parovani = _solve([], [_sale("S", "BTC", "2025-06-01", "1.0", "800000")], {}, [])
    assert len(parovani) == 1
    p = parovani[0]
    assert p["lot_id"].startswith("phantom:")
    assert Decimal(p["naklad_czk"]) == Decimal("0")
    assert Decimal(p["zisk_czk"]) == Decimal("800000")
    assert p["osvobozeno"] == "ne"


# ── Identifikovatelnost & integrita LP řešení ────────────────────────────────

def test_every_pairing_has_ids():
    """Každý řádek párování má prodej_id i lot_id (pro daňovou rekonstrukci)."""
    loty = [_lot("L1", "BTC", "2024-01-01", "2.0", "100000")]
    prodeje = [
        _sale("Sa", "BTC", "2024-06-01", "0.5", "150000"),
        _sale("Sb", "BTC", "2024-07-01", "1.0", "150000"),
    ]
    parovani = _solve(loty, prodeje, {}, [])
    for p in parovani:
        assert p["prodej_id"], f"chybí prodej_id: {p}"
        assert p["lot_id"], f"chybí lot_id: {p}"
        assert p["coin"]
        assert p["datum_prodeje"] and p["datum_nakupu"]
        assert p["rok_prodeje"]


def test_lot_capacity_never_exceeded():
    """Σ x[i,j] přes všechny prodeje ≤ lot.mnozstvi — lot nelze přeprodat."""
    loty = [_lot("L1", "BTC", "2024-01-01", "1.0", "100000")]
    prodeje = [
        _sale("S1", "BTC", "2024-06-01", "0.6", "200000"),
        _sale("S2", "BTC", "2024-07-01", "0.6", "200000"),  # dohromady 1.2 BTC > 1.0
    ]
    parovani = _solve(loty, prodeje, {}, [])
    used_L1 = sum(Decimal(p["mnozstvi_pouzite"]) for p in parovani if p["lot_id"] == "L1")
    assert used_L1 <= Decimal("1.0") + Decimal("0.00001")
    # Zbytek musí být pokryt phantom loty (jeden per prodej)
    used_phantom = sum(Decimal(p["mnozstvi_pouzite"]) for p in parovani
                       if p["lot_id"].startswith("phantom:"))
    assert used_phantom + used_L1 == Decimal("1.2")


def test_sale_fully_covered():
    """Σ x[i,j] přes všechny loty = sale.mnozstvi — každý prodej plně pokrytý."""
    loty = [
        _lot("La", "BTC", "2024-01-01", "0.3", "100000"),
        _lot("Lb", "BTC", "2024-02-01", "0.4", "120000"),
        _lot("Lc", "BTC", "2024-03-01", "0.5", "150000"),
    ]
    prodeje = [_sale("S", "BTC", "2024-06-01", "1.0", "200000")]
    parovani = _solve(loty, prodeje, {}, [])
    covered = sum(Decimal(p["mnozstvi_pouzite"]) for p in parovani)
    assert abs(covered - Decimal("1.0")) < Decimal("0.00001")


def test_no_future_dated_lot():
    """Lot s datem nákupu PO datu prodeje nesmí být použit (chronologie)."""
    loty = [
        _lot("Lpast",   "BTC", "2024-01-01", "0.5", "100000"),
        _lot("Lfuture", "BTC", "2024-12-01", "0.5", "100000"),  # po prodeji
    ]
    prodeje = [_sale("S", "BTC", "2024-06-01", "1.0", "200000")]
    parovani = _solve(loty, prodeje, {}, [])
    future_use = sum(Decimal(p["mnozstvi_pouzite"]) for p in parovani if p["lot_id"] == "Lfuture")
    assert future_use == Decimal("0"), "Budoucí lot byl chybně použit"
    # Zbytek (0.5 BTC) musí být pokryt phantomem, ne Lfuture
    phantom_use = sum(Decimal(p["mnozstvi_pouzite"]) for p in parovani
                      if p["lot_id"].startswith("phantom:"))
    assert abs(phantom_use - Decimal("0.5")) < Decimal("0.00001")


# ── Coin discipline — různé druhy nesmí splývat ─────────────────────────────

def test_cross_coin_pairing_impossible():
    """BTC lot nesmí pokrýt ETH prodej (různý druh příjmu, různá identita)."""
    loty = [_lot("Lbtc", "BTC", "2024-01-01", "10.0", "100000")]  # spousta BTC
    prodeje = [_sale("Seth", "ETH", "2024-06-01", "1.0", "50000")]  # ale prodej ETH
    parovani = _solve(loty, prodeje, {}, [])
    btc_use = sum(Decimal(p["mnozstvi_pouzite"]) for p in parovani if p["lot_id"] == "Lbtc")
    assert btc_use == Decimal("0"), "BTC lot chybně použit na ETH prodej"
    # ETH prodej musí být pokryt phantomem
    assert any(p["lot_id"].startswith("phantom:") and p["coin"] == "ETH" for p in parovani)
