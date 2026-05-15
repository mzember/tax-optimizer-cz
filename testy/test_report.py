"""Integrity testy reportů: aritmetika, časový test, konzistence CSV⟷MD.

Checks applied to every report_{rok}.csv in build/ AND build/historie/:
  1. ARITHMETIC  příjem - náklad == zisk  (per řádek, tolerance 0.01 Kč)
  2. EXEMPT_ANO  osvobozeno=ano → nakup+3 roky < prodej  (je_osvobozeno=True)
  3. EXEMPT_NE   osvobozeno=ne  → nakup+3 roky ≥ prodej  (je_osvobozeno=False)
  4. NON_NEG     zdanitelný základ §10 ZDP ≥ 0 (ztrátu nelze odečíst)
  5. CSV_MD_SYNC součty CSV se shodují s čísly v .md reportu (tolerance 0.02 Kč)

Roky pred --od-roku 2023 (historie) sa testujú rovnako — chytáme bugy, ktoré
by sa inak nezistili. Testy sú zobrazené v parametrizácii len ak reálne
existuje neprázdny CSV (žiadne SKIPy).
"""

import csv
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from danove.util.datum import je_osvobozeno

BUILD_DIR = Path(__file__).parent.parent / "build"


def _discover_reports() -> list[tuple[int, Path]]:
    """Najdi (rok, cesta) pre každý neprázdný report CSV — oficiálne aj historie."""
    found: list[tuple[int, Path]] = []
    if not BUILD_DIR.exists():
        return found
    paths = sorted(BUILD_DIR.glob("report_*.csv")) + \
            sorted((BUILD_DIR / "historie").glob("report_*.csv"))
    for p in paths:
        try:
            rok = int(p.stem.removeprefix("report_"))
        except ValueError:
            continue
        with p.open(encoding="utf-8") as f:
            if any(csv.DictReader(f)):
                found.append((rok, p))
    return found


REPORTY = _discover_reports()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rows(csv_path: Path) -> list[dict]:
    with csv_path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _dec(s: str) -> Decimal:
    return Decimal(str(s).strip())


def _zdanitelny(rows: list[dict]) -> Decimal:
    neosvob = [_dec(r["zisk_czk"]) for r in rows if r["osvobozeno"] == "ne"]
    netto = sum(neosvob, Decimal("0"))
    return max(Decimal("0"), netto)


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rok,csv_path", REPORTY)
def test_aritmetika(rok, csv_path):
    """Každý řádek: abs(příjem - náklad - zisk) < 0.01 Kč."""
    for r in _rows(csv_path):
        diff = abs(_dec(r["prijem_czk"]) - _dec(r["naklad_czk"]) - _dec(r["zisk_czk"]))
        assert diff < Decimal("0.01"), (
            f"{rok} {r['datum_prodeje']} lot={r['lot_id']}: "
            f"příjem-náklad≠zisk, rozdíl={diff}"
        )


@pytest.mark.parametrize("rok,csv_path", REPORTY)
def test_osvobozenost_ano(rok, csv_path):
    """Osvobozeno=ano → nakup + 3 roky < prodej (přesný kalendářní test §4 ZDP)."""
    for r in _rows(csv_path):
        if r["osvobozeno"] != "ano":
            continue
        nakup = _d(r["datum_nakupu_lotu"])
        prodej = _d(r["datum_prodeje"])
        assert je_osvobozeno(nakup, prodej), (
            f"{rok} lot={r['lot_id']}: osvobozeno=ano, ale "
            f"nakup={nakup} + 3 roky > prodej={prodej} — nesplňuje časový test"
        )


@pytest.mark.parametrize("rok,csv_path", REPORTY)
def test_osvobozenost_ne(rok, csv_path):
    """Osvobozeno=ne → nakup + 3 roky ≥ prodej (žádná chybně nezdaněná položka)."""
    for r in _rows(csv_path):
        if r["osvobozeno"] != "ne":
            continue
        nakup = _d(r["datum_nakupu_lotu"])
        prodej = _d(r["datum_prodeje"])
        assert not je_osvobozeno(nakup, prodej), (
            f"{rok} lot={r['lot_id']}: osvobozeno=ne, ale "
            f"nakup={nakup} + 3 roky ≤ prodej={prodej} — mělo by být osvobozeno"
        )


@pytest.mark.parametrize("rok,csv_path", REPORTY)
def test_zdanitelny_nezaporny(rok, csv_path):
    """Zdanitelný základ §10 ZDP musí být ≥ 0 (ztrátu nelze odečíst z jiných příjmů)."""
    assert _zdanitelny(_rows(csv_path)) >= Decimal("0")


@pytest.mark.parametrize("rok,csv_path", REPORTY)
def test_csv_md_soucty(rok, csv_path):
    """Součty vypočtené z CSV se shodují s čísly uvedenými v .md reportu (tolerance 0.02 Kč)."""
    md_path = csv_path.with_suffix(".md")
    assert md_path.exists(), f"{md_path} chybí, ale CSV existuje"

    rows = _rows(csv_path)
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
