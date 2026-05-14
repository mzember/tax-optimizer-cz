"""Integrity testy reportů: aritmetika, časový test, konzistence CSV⟷MD.

Checks applied to every build/report_{rok}.csv:
  1. ARITHMETIC  příjem - náklad == zisk  (per řádek, tolerance 0.01 Kč)
  2. EXEMPT_ANO  osvobozeno=ano → nakup+3 roky < prodej  (je_osvobozeno=True)
  3. EXEMPT_NE   osvobozeno=ne  → nakup+3 roky ≥ prodej  (je_osvobozeno=False)
  4. NON_NEG     zdanitelný základ §10 ZDP ≥ 0 (ztrátu nelze odečíst)
  5. CSV_MD_SYNC součty CSV se shodují s čísly v .md reportu (tolerance 0.02 Kč)

Testy jsou přeskočeny, pokud soubor neexistuje (build ještě neběžel).
"""

import csv
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from danove.util.datum import je_osvobozeno

BUILD_DIR = Path(__file__).parent.parent / "build"
ROKY = list(range(2017, 2028))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rows(rok: int) -> list[dict]:
    path = BUILD_DIR / f"report_{rok}.csv"
    if not path.exists():
        pytest.skip(f"build/report_{rok}.csv neexistuje")
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        pytest.skip(f"report_{rok}.csv je prázdný")
    return rows


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _dec(s: str) -> Decimal:
    return Decimal(str(s).strip())


def _zdanitelny(rows: list[dict]) -> Decimal:
    neosvob = [_dec(r["zisk_czk"]) for r in rows if r["osvobozeno"] == "ne"]
    netto = sum(neosvob, Decimal("0"))
    return max(Decimal("0"), netto)


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rok", ROKY)
def test_aritmetika(rok):
    """Každý řádek: abs(příjem - náklad - zisk) < 0.01 Kč."""
    for r in _rows(rok):
        diff = abs(_dec(r["prijem_czk"]) - _dec(r["naklad_czk"]) - _dec(r["zisk_czk"]))
        assert diff < Decimal("0.01"), (
            f"{rok} {r['datum_prodeje']} lot={r['lot_id']}: "
            f"příjem-náklad≠zisk, rozdíl={diff}"
        )


@pytest.mark.parametrize("rok", ROKY)
def test_osvobozenost_ano(rok):
    """Osvobozeno=ano → nakup + 3 roky < prodej (přesný kalendářní test §4 ZDP)."""
    for r in _rows(rok):
        if r["osvobozeno"] != "ano":
            continue
        nakup = _d(r["datum_nakupu_lotu"])
        prodej = _d(r["datum_prodeje"])
        assert je_osvobozeno(nakup, prodej), (
            f"{rok} lot={r['lot_id']}: osvobozeno=ano, ale "
            f"nakup={nakup} + 3 roky > prodej={prodej} — nesplňuje časový test"
        )


@pytest.mark.parametrize("rok", ROKY)
def test_osvobozenost_ne(rok):
    """Osvobozeno=ne → nakup + 3 roky ≥ prodej (žádná chybně nezdaněná položka)."""
    for r in _rows(rok):
        if r["osvobozeno"] != "ne":
            continue
        nakup = _d(r["datum_nakupu_lotu"])
        prodej = _d(r["datum_prodeje"])
        assert not je_osvobozeno(nakup, prodej), (
            f"{rok} lot={r['lot_id']}: osvobozeno=ne, ale "
            f"nakup={nakup} + 3 roky ≤ prodej={prodej} — mělo by být osvobozeno"
        )


@pytest.mark.parametrize("rok", ROKY)
def test_zdanitelny_nezaporny(rok):
    """Zdanitelný základ §10 ZDP musí být ≥ 0 (ztrátu nelze odečíst z jiných příjmů)."""
    assert _zdanitelny(_rows(rok)) >= Decimal("0")


@pytest.mark.parametrize("rok", ROKY)
def test_csv_md_soucty(rok):
    """Součty vypočtené z CSV se shodují s čísly uvedenými v .md reportu (tolerance 0.02 Kč)."""
    md_path = BUILD_DIR / f"report_{rok}.md"
    if not md_path.exists():
        pytest.skip(f"build/report_{rok}.md neexistuje")

    rows = _rows(rok)
    md = md_path.read_text(encoding="utf-8")
    TOL = Decimal("0.02")

    def _extract(pattern: str) -> Decimal | None:
        m = re.search(pattern, md)
        return Decimal(m.group(1)) if m else None

    csv_prijem = sum(_dec(r["prijem_czk"]) for r in rows)
    csv_naklad = sum(_dec(r["naklad_czk"]) for r in rows)
    csv_zisk = sum(_dec(r["zisk_czk"]) for r in rows)
    csv_zdan = _zdanitelny(rows)

    checks = [
        ("příjem",      _extract(r"Hrubý příjem:\s+\*\*([\d.]+)\s*Kč"),       csv_prijem),
        ("náklady",     _extract(r"Náklady:\s+\*\*([\d.]+)\s*Kč"),             csv_naklad),
        ("zisk",        _extract(r"Ekonomický zisk celkem:\s+\*\*([\d.]+)\s*Kč"), csv_zisk),
        ("zdanitelný",  _extract(r"Zdanitelný zisk §10 ZDP:\s*([\d.]+)\s*Kč"), csv_zdan),
    ]
    for label, md_val, csv_val in checks:
        if md_val is None:
            continue
        assert abs(csv_val - md_val) <= TOL, (
            f"{rok} {label}: CSV={csv_val:.2f} ≠ MD={md_val:.2f} (rozdíl > {TOL} Kč)"
        )
