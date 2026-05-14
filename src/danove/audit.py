"""Post-pairing audit: verifies parovani.csv against the full lot history.

Reads build/obohaceno.csv and build/parovani.csv. Simulates pre-od_roku
sales via greedy HIFO to estimate historical lot consumption, then checks
LP assignments for consistency.

Error codes:
  LOT_OVERAGE   lot allocated more than its original quantity (pre+LP combined)
  BEFORE_BUY    lot acquired after the disposal date
  SALE_GAP      sale not fully covered in parovani

Warning codes:
  WRONG_EXEMPT  osvobozeno flag inconsistent with 3-year test
  PRE_UNCOVERED pre-od_roku sale could not be fully covered by existing lots
                (explains why phantom lots appear in the optimised years)
"""

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from danove.optimalizace import Lot, Sale, _load_enriched, _greedy_consume
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
    pre_prodeje = [s for s in prodeje if s.rok < od_roku]
    opt_prodeje = [s for s in prodeje if s.rok >= od_roku]

    # ── Step 1: simulate pre-od_roku HIFO consumption ────────────────────────
    pre_consumed = _greedy_consume(loty, pre_prodeje)

    # Also track which pre-sales were not fully coverable
    remaining_after_pre: dict[str, Decimal] = {
        lot.lot_id: lot.mnozstvi - pre_consumed.get(lot.lot_id, Decimal(0))
        for lot in loty
    }
    lots_by_coin_pre: dict[str, list[Lot]] = defaultdict(list)
    for lot in loty:
        lots_by_coin_pre[lot.coin].append(lot)

    pre_uncovered: list[dict] = []
    temp_remaining = {lot.lot_id: lot.mnozstvi for lot in loty}
    for sale in sorted(pre_prodeje, key=lambda s: s.datum_prodeje):
        need = sale.mnozstvi
        for lot in sorted(
            [l for l in lots_by_coin_pre[sale.coin]
             if l.datum_nakupu <= sale.datum_prodeje and temp_remaining[l.lot_id] > EPSILON],
            key=lambda l: (0 if je_osvobozeno(l.datum_nakupu, sale.datum_prodeje) else 1,
                           -l.cena_za_kus_czk)
        ):
            if need <= EPSILON:
                break
            use = min(need, temp_remaining[lot.lot_id])
            temp_remaining[lot.lot_id] -= use
            need -= use
        if need > EPSILON:
            pre_uncovered.append({
                "sale_id": sale.sale_id, "coin": sale.coin,
                "datum": sale.datum_prodeje.isoformat(),
                "rok": sale.rok, "chybi": str(need.quantize(Decimal("0.00000001"))),
            })

    # ── Step 2: load LP pairings ─────────────────────────────────────────────
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
        pre = pre_consumed.get(lot.lot_id, Decimal(0))
        lp = lp_by_lot.get(lot.lot_id, Decimal(0))
        total = pre + lp
        if total > lot.mnozstvi + EPSILON:
            lot_overage.append({
                "lot_id": lot.lot_id, "coin": lot.coin,
                "datum_nakupu": lot.datum_nakupu.isoformat(),
                "original": str(lot.mnozstvi),
                "pre_spotrebovano": str(pre.quantize(Decimal("0.00000001"))),
                "lp_prideleno": str(lp.quantize(Decimal("0.00000001"))),
                "prebytek": str((total - lot.mnozstvi).quantize(Decimal("0.00000001"))),
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
    opt_sale_map = {s.sale_id: s for s in opt_prodeje}
    for sale_id, sale in opt_sale_map.items():
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

    # ── Check 4: WRONG_EXEMPT ────────────────────────────────────────────────
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
    n_err = len(lot_overage) + len(before_buy) + len(sale_gap)
    n_warn = len(wrong_exempt) + len(pre_uncovered)

    lines: list[str] = [
        "# Audit párování lotů",
        "",
        f"**Chyby: {n_err}** | **Varování: {n_warn}**",
        "",
        f"Pre-{od_roku} prodeje: {len(pre_prodeje)} (simulováno greedy HIFO, ne skutečné podané párování)",
        f"LP prodeje ({od_roku}+): {len(opt_prodeje)}",
        f"LP řádků párování: {len(parovani)}",
        "",
    ]

    if not n_err and not n_warn:
        lines.append("Žádné problémy nalezeny. ✓")
    else:
        if lot_overage:
            lines += [
                "## ERR: LOT_OVERAGE — lot použit více než dostupné množství",
                "",
                "| lot_id | coin | datum_nakupu | original | pre-2023 (HIFO est.) | LP přiděleno | přebytek |",
                "|--------|------|--------------|----------|----------------------|--------------|----------|",
            ]
            for e in lot_overage:
                lines.append(
                    f"| `{e['lot_id']}` | {e['coin']} | {e['datum_nakupu']} "
                    f"| {e['original']} | {e['pre_spotrebovano']} "
                    f"| {e['lp_prideleno']} | **{e['prebytek']}** |"
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

        if pre_uncovered:
            lines += [
                f"## WARN: PRE_UNCOVERED — pre-{od_roku} prodej nepokrytý loty",
                "(Vysvětluje výskyt phantom lotů v optimalizovaných letech.)",
                "",
                "| sale_id | coin | datum | rok | chybí |",
                "|---------|------|-------|-----|-------|",
            ]
            for w in pre_uncovered:
                lines.append(
                    f"| `{w['sale_id']}` | {w['coin']} | {w['datum']} "
                    f"| {w['rok']} | {w['chybi']} |"
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
