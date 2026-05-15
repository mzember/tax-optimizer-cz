"""Post-pairing audit: verifies parovani.csv against the full lot history.

Reads build/obohaceno.csv and build/parovani.csv. LP allocates lots across
all years (older years are informational), so the audit just checks that
each lot's LP allocation does not exceed its quantity and each sale is
fully covered.

Error codes:
  LOT_OVERAGE   lot allocated more than its original quantity
  BEFORE_BUY    lot acquired after the disposal date
  SALE_GAP      sale not fully covered in parovani
  ARITH_ERR     příjem - náklad ≠ zisk (tolerance 0.01 Kč)

Warning codes:
  WRONG_EXEMPT  osvobozeno flag inconsistent with 3-year test
"""

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from danove.optimalizace import Lot, _load_enriched
from danove.util import coin as coin_util
from danove.util.datum import je_osvobozeno

EPSILON = Decimal("0.00001")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_parovani(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _dec(s: str) -> Decimal:
    try:
        return Decimal(s.strip())
    except Exception:
        return Decimal(0)


# ── Audit ────────────────────────────────────────────────────────────────────

def run(parovani_path: Path, obchody_path: Path, vystup_path: Path,
        config_path: Path, od_roku: int = 2023) -> None:

    coin_util.init(config_path.parent)
    loty, prodeje = _load_enriched(obchody_path)

    lots_by_id: dict[str, Lot] = {lot.lot_id: lot for lot in loty}
    pre_count = sum(1 for s in prodeje if s.rok < od_roku)
    opt_count = sum(1 for s in prodeje if s.rok >= od_roku)

    # ── Load LP pairings ─────────────────────────────────────────────────────
    parovani = _load_parovani(parovani_path)

    lp_by_lot: dict[str, Decimal] = defaultdict(Decimal)
    lp_coverage: dict[str, Decimal] = defaultdict(Decimal)
    for row in parovani:
        qty = _dec(row["mnozstvi_pouzite"])
        lp_by_lot[row["lot_id"]] += qty
        lp_coverage[row["prodej_id"]] += qty

    # ── Check 1: LOT_OVERAGE ─────────────────────────────────────────────────
    lot_overage: list[dict] = []
    for lot in loty:
        lp = lp_by_lot.get(lot.lot_id, Decimal(0))
        if lp > lot.mnozstvi + EPSILON:
            lot_overage.append({
                "lot_id": lot.lot_id, "coin": lot.coin,
                "datum_nakupu": lot.datum_nakupu.isoformat(),
                "original": str(lot.mnozstvi),
                "lp_prideleno": str(lp.quantize(Decimal("0.00000001"))),
                "prebytek": str((lp - lot.mnozstvi).quantize(Decimal("0.00000001"))),
            })

    # ── Check 2: BEFORE_BUY ──────────────────────────────────────────────────
    before_buy: list[dict] = []
    for row in parovani:
        if row["lot_id"].startswith("phantom:"):
            continue
        lot = lots_by_id.get(row["lot_id"])
        if not lot:
            continue
        dp = _d(row["datum_prodeje"])
        dn = _d(row["datum_nakupu"])
        if dn > dp:
            before_buy.append({
                "prodej_id": row["prodej_id"], "lot_id": row["lot_id"],
                "coin": row["coin"], "datum_nakupu": row["datum_nakupu"],
                "datum_prodeje": row["datum_prodeje"],
            })

    # ── Check 3: SALE_GAP ────────────────────────────────────────────────────
    sale_gap: list[dict] = []
    sale_map = {s.sale_id: s for s in prodeje}
    for sale_id, sale in sale_map.items():
        covered = lp_coverage.get(sale_id, Decimal(0))
        gap = sale.mnozstvi - covered
        if gap > EPSILON:
            sale_gap.append({
                "sale_id": sale_id, "coin": sale.coin,
                "datum": sale.datum_prodeje.isoformat(),
                "pozadovano": str(sale.mnozstvi),
                "pokryto": str(covered.quantize(Decimal("0.00000001"))),
                "chybi": str(gap.quantize(Decimal("0.00000001"))),
            })

    # ── Check 4: ARITH_ERR ──────────────────────────────────────────────────
    arith_err: list[dict] = []
    for row in parovani:
        prijem = _dec(row.get("prijem_czk", "0"))
        naklad = _dec(row.get("naklad_czk", "0"))
        zisk = _dec(row.get("zisk_czk", "0"))
        diff = abs(prijem - naklad - zisk)
        if diff > Decimal("0.01"):
            arith_err.append({
                "prodej_id": row["prodej_id"], "lot_id": row["lot_id"],
                "coin": row["coin"], "datum_prodeje": row["datum_prodeje"],
                "prijem": str(prijem), "naklad": str(naklad), "zisk": str(zisk),
                "rozdil": str(diff.quantize(Decimal("0.01"))),
            })

    # ── Check 5: WRONG_EXEMPT ────────────────────────────────────────────────
    wrong_exempt: list[dict] = []
    for row in parovani:
        if row["lot_id"].startswith("phantom:"):
            expected = False
        else:
            lot = lots_by_id.get(row["lot_id"])
            if not lot:
                continue
            expected = je_osvobozeno(_d(row["datum_nakupu"]), _d(row["datum_prodeje"]))
        actual = row["osvobozeno"] == "ano"
        if expected != actual:
            wrong_exempt.append({
                "prodej_id": row["prodej_id"], "lot_id": row["lot_id"],
                "coin": row["coin"],
                "datum_nakupu": row["datum_nakupu"],
                "datum_prodeje": row["datum_prodeje"],
                "v_parovani": row["osvobozeno"],
                "spravne": "ano" if expected else "ne",
            })

    # ── Write report ─────────────────────────────────────────────────────────
    n_err = len(lot_overage) + len(before_buy) + len(sale_gap) + len(arith_err)
    n_warn = len(wrong_exempt)

    lines: list[str] = [
        "# Audit párování lotů",
        "",
        f"**Chyby: {n_err}** | **Varování: {n_warn}**",
        "",
        f"Sales celkom: {len(prodeje)} "
        f"(OFICIÁLNE {od_roku}+: {opt_count}, info <{od_roku}: {pre_count})",
        f"LP řádků párování: {len(parovani)}",
        "",
    ]

    if not n_err and not n_warn:
        lines.append("Žádné problémy nalezeny. ✓")
    else:
        if arith_err:
            lines += [
                "## ERR: ARITH_ERR — příjem - náklad ≠ zisk (tolerance 0.01 Kč)",
                "",
                "| prodej_id | lot_id | coin | datum_prodeje | příjem | náklad | zisk | rozdíl |",
                "|-----------|--------|------|---------------|--------|--------|------|--------|",
            ]
            for e in arith_err:
                lines.append(
                    f"| `{e['prodej_id']}` | `{e['lot_id']}` | {e['coin']} "
                    f"| {e['datum_prodeje']} | {e['prijem']} | {e['naklad']} "
                    f"| {e['zisk']} | **{e['rozdil']}** |"
                )
            lines.append("")

        if lot_overage:
            lines += [
                "## ERR: LOT_OVERAGE — lot použit více než dostupné množství",
                "",
                "| lot_id | coin | datum_nakupu | original | LP přiděleno | přebytek |",
                "|--------|------|--------------|----------|--------------|----------|",
            ]
            for e in lot_overage:
                lines.append(
                    f"| `{e['lot_id']}` | {e['coin']} | {e['datum_nakupu']} "
                    f"| {e['original']} | {e['lp_prideleno']} | **{e['prebytek']}** |"
                )
            lines.append("")

        if before_buy:
            lines += [
                "## ERR: BEFORE_BUY — lot nakoupen po datu prodeje",
                "",
                "| prodej_id | lot_id | coin | datum_nakupu | datum_prodeje |",
                "|-----------|--------|------|--------------|---------------|",
            ]
            for e in before_buy:
                lines.append(
                    f"| `{e['prodej_id']}` | `{e['lot_id']}` | {e['coin']} "
                    f"| {e['datum_nakupu']} | {e['datum_prodeje']} |"
                )
            lines.append("")

        if sale_gap:
            lines += [
                "## ERR: SALE_GAP — prodej není plně pokryt v párování",
                "",
                "| sale_id | coin | datum | požadováno | pokryto | chybí |",
                "|---------|------|-------|------------|---------|-------|",
            ]
            for e in sale_gap:
                lines.append(
                    f"| `{e['sale_id']}` | {e['coin']} | {e['datum']} "
                    f"| {e['pozadovano']} | {e['pokryto']} | **{e['chybi']}** |"
                )
            lines.append("")

        if wrong_exempt:
            lines += [
                "## WARN: WRONG_EXEMPT — příznak osvobození nesouhlasí s 3-letým testem",
                "",
                "| prodej_id | lot_id | coin | datum_nakupu | datum_prodeje | v_parovani | správně |",
                "|-----------|--------|------|--------------|---------------|------------|---------|",
            ]
            for w in wrong_exempt:
                lines.append(
                    f"| `{w['prodej_id']}` | `{w['lot_id']}` | {w['coin']} "
                    f"| {w['datum_nakupu']} | {w['datum_prodeje']} "
                    f"| {w['v_parovani']} | **{w['spravne']}** |"
                )
            lines.append("")

    vystup_path.parent.mkdir(parents=True, exist_ok=True)
    vystup_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"audit: {n_err} chyb, {n_warn} varování → {vystup_path}", file=sys.stderr)

    if n_err:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--parovani", required=True, type=Path)
    parser.add_argument("--obchody", required=True, type=Path)
    parser.add_argument("--vystup", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--od-roku", type=int, default=2023)
    args = parser.parse_args()
    run(args.parovani, args.obchody, args.vystup, args.config, od_roku=args.od_roku)
