"""Global LP lot-matching optimizer across all years.

Minimizes total taxable gain across all years simultaneously using OR-Tools GLOP.
Czech law (§10 ZDP): losses cannot be carried forward — each year's loss is discarded.
This makes globally optimal matching fundamentally better than per-year greedy HIFO.

LP formulation:
  Variables: x[i,j] >= 0 = quantity of lot i allocated to sale j
             tax_base[y] >= 0 = positive part of yearly gain (pays tax)
  Constraints:
    (1) ∀i: Σ_j x[i,j] <= lot[i].mnozstvi          (don't oversell a lot)
    (2) ∀j: Σ_i x[i,j] = sale[j].mnozstvi          (fully cover each sale)
    (3) ∀y: tax_base[y] >= gain[y]                  (positive part)
    (4) lock-in: x[i,j] = locked_value for locked years
  Objective: minimize Σ_y tax_base[y]

Tie-breaking (secondary LP): minimize Σ[i,j] days_held[i,j] * x[i,j]
  (prefer older lot → FIFO-like stable output)
"""

import argparse
import csv
import sys
import tomllib
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import NamedTuple

from ortools.linear_solver import pywraplp

from danove.util import coin as coin_util
from danove.util.datum import je_osvobozeno

QUANT = Decimal("0.00000001")
PAROVANI_HEADER = [
    "prodej_id", "lot_id", "coin", "datum_prodeje", "datum_nakupu",
    "mnozstvi_pouzite", "prijem_czk", "naklad_czk", "zisk_czk",
    "osvobozeno", "rok_prodeje",
]


# ── Data classes ──────────────────────────────────────────────────────────────

class Lot(NamedTuple):
    lot_id: str
    coin: str
    datum_nakupu: date
    mnozstvi: Decimal
    cena_za_kus_czk: Decimal
    fee_czk_total: Decimal
    phantom: bool = False
    phantom_for_sale_id: str | None = None  # restricts pairing to one sale only


class Sale(NamedTuple):
    sale_id: str
    coin: str
    datum_prodeje: date
    mnozstvi: Decimal
    prijem_za_kus_czk: Decimal  # proceeds per unit (net of sale fee)
    rok: int


# ── Parsers ───────────────────────────────────────────────────────────────────

def _dec(s: str) -> Decimal:
    try:
        return Decimal(str(s).strip())
    except Exception:
        return Decimal("0")


def _parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _load_enriched(path: Path) -> tuple[list[Lot], list[Sale]]:
    """Extract acquisition lots and disposal events from enriched CSV.

    For crypto-to-crypto trades (protistrana_coin is not fiat):
      - NAKUP: creates acquisition lot for coin AND a disposal event for protistrana_coin
      - PRODEJ: creates disposal for coin AND acquisition lot for protistrana_coin

    In both cases, the CZK value used is celkem_czk (derived from coin's CoinGecko price).
    """
    loty: list[Lot] = []
    prodeje: list[Sale] = []

    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    lot_counter: dict[tuple, int] = {}

    def make_lot_id(base_id: str, suffix: str = "") -> str:
        key = (base_id, suffix)
        lot_counter[key] = lot_counter.get(key, 0) + 1
        n = lot_counter[key]
        return f"{base_id}{suffix}" if n == 1 else f"{base_id}{suffix}#{n}"

    for row in rows:
        typ = row.get("typ", "")
        coin = coin_util.normalizuj(row.get("coin", ""))
        proto_coin = coin_util.normalizuj(row.get("protistrana_coin", "") or "")
        datum = _parse_date(row.get("datum_utc", ""))
        mnozstvi = _dec(row.get("mnozstvi", "0"))
        proto_mnozstvi = _dec(row.get("protistrana_mnozstvi", "0"))
        celkem_czk = _dec(row.get("celkem_czk", "0"))
        fee_czk = _dec(row.get("fee_czk", "0"))
        cena_za_kus = _dec(row.get("cena_za_kus_czk", "0"))
        rid = row.get("id", "")

        if mnozstvi == 0 or celkem_czk == 0:
            continue

        proto_is_fiat = coin_util.je_fiat(proto_coin) if proto_coin else True

        if typ == "NAKUP":
            # Acquisition lot for coin
            loty.append(Lot(
                lot_id=make_lot_id(rid),
                coin=coin,
                datum_nakupu=datum,
                mnozstvi=mnozstvi,
                cena_za_kus_czk=cena_za_kus,
                fee_czk_total=fee_czk,
            ))
            # If crypto-to-crypto: also a disposal of protistrana_coin
            if proto_coin and not proto_is_fiat and proto_mnozstvi > 0:
                # proceeds = celkem_czk (both sides valued at same CZK amount)
                prijem_za_kus = (celkem_czk / proto_mnozstvi).quantize(QUANT) if proto_mnozstvi > 0 else Decimal("0")
                prodeje.append(Sale(
                    sale_id=make_lot_id(rid, ":proto"),
                    coin=proto_coin,
                    datum_prodeje=datum,
                    mnozstvi=proto_mnozstvi,
                    prijem_za_kus_czk=prijem_za_kus,
                    rok=datum.year,
                ))

        elif typ == "PRODEJ":
            # Proceeds per unit (sale fee reduces proceeds)
            prijem_net_czk = celkem_czk - fee_czk
            prijem_za_kus = (prijem_net_czk / mnozstvi).quantize(QUANT) if mnozstvi > 0 else Decimal("0")
            prodeje.append(Sale(
                sale_id=make_lot_id(rid),
                coin=coin,
                datum_prodeje=datum,
                mnozstvi=mnozstvi,
                prijem_za_kus_czk=prijem_za_kus,
                rok=datum.year,
            ))
            # If crypto-to-crypto: also an acquisition of protistrana_coin
            if proto_coin and not proto_is_fiat and proto_mnozstvi > 0:
                proto_cena_za_kus = (celkem_czk / proto_mnozstvi).quantize(QUANT) if proto_mnozstvi > 0 else Decimal("0")
                loty.append(Lot(
                    lot_id=make_lot_id(rid, ":proto"),
                    coin=proto_coin,
                    datum_nakupu=datum,
                    mnozstvi=proto_mnozstvi,
                    cena_za_kus_czk=proto_cena_za_kus,
                    fee_czk_total=Decimal("0"),
                ))

    return loty, prodeje


def _load_locked(locked_path: Path) -> dict[str, Decimal]:
    """Load locked lot-sale assignments: {(sale_id,lot_id): quantity}."""
    result = {}
    if not locked_path.exists():
        return result
    with locked_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row.get("prodej_id", ""), row.get("lot_id", ""))
            qty = _dec(row.get("mnozstvi_pouzite", "0"))
            if qty > 0:
                result[key] = qty
    return result


# ── Pre-LP: greedy HIFO consumption of pre-optimisation sales ────────────────

def _greedy_consume(loty: list[Lot], prodeje: list[Sale]) -> dict[str, Decimal]:
    """Simulate exempt-first HIFO on `prodeje`, return {lot_id: consumed_qty}.

    Used to subtract pre-od_roku sales from lot inventory before the LP runs,
    so the LP cannot re-use lots that were already disposed of historically.
    """
    remaining: dict[str, Decimal] = {lot.lot_id: lot.mnozstvi for lot in loty}
    consumed: dict[str, Decimal] = defaultdict(Decimal)

    lots_by_coin: dict[str, list[Lot]] = defaultdict(list)
    for lot in loty:
        lots_by_coin[lot.coin].append(lot)

    for sale in sorted(prodeje, key=lambda s: s.datum_prodeje):
        need = sale.mnozstvi
        eligible = [
            l for l in lots_by_coin[sale.coin]
            if l.datum_nakupu <= sale.datum_prodeje and remaining[l.lot_id] > Decimal("1e-9")
        ]
        # Prefer exempt lots first, then highest cost basis (HIFO)
        eligible.sort(key=lambda l: (
            0 if je_osvobozeno(l.datum_nakupu, sale.datum_prodeje) else 1,
            -l.cena_za_kus_czk,
        ))
        for lot in eligible:
            if need <= Decimal("1e-9"):
                break
            use = min(need, remaining[lot.lot_id])
            remaining[lot.lot_id] -= use
            consumed[lot.lot_id] += use
            need -= use

    return dict(consumed)


# ── LP solver ────────────────────────────────────────────────────────────────

CZK_SCALE = 1e-6  # LP works in MCZK units; condition number ≈ 2e5 vs 2e11 without scaling


def _solve(
    loty: list[Lot],
    prodeje: list[Sale],
    locked: dict[tuple, Decimal],
    zamknute_roky: list[int],
) -> list[dict]:
    """Run GLOP LP and return parovani rows."""

    if not prodeje:
        return []

    # Pre-processing: every sale gets its own phantom lot — zero cost basis,
    # never exempt, restricted to that one sale via phantom_for_sale_id.
    # The LP only draws on a phantom when real date-compatible lots cannot
    # cover the sale (a phantom is always the most expensive option tax-wise,
    # so the LP avoids it otherwise). This guarantees every sale is coverable
    # and correctly taxes any sale whose earlier purchase is undocumented —
    # including a sale that chronologically precedes all real lots of its coin
    # even though that coin has enough lots in total.
    phantom_lots: list[Lot] = [
        Lot(
            lot_id=f"phantom:{sale.sale_id}",
            coin=sale.coin,
            datum_nakupu=sale.datum_prodeje,      # gap = 0 → je_osvobozeno = False
            mnozstvi=sale.mnozstvi,               # covers the whole sale if needed
            cena_za_kus_czk=Decimal(0),           # no provable cost basis → full proceeds taxable
            fee_czk_total=Decimal(0),
            phantom=True,
            phantom_for_sale_id=sale.sale_id,
        )
        for sale in prodeje
    ]

    loty = list(loty) + phantom_lots

    # Compatible pairs P
    pairs: list[tuple[int, int]] = []  # (lot_idx, sale_idx)
    for j, sale in enumerate(prodeje):
        for i, lot in enumerate(loty):
            if lot.coin != sale.coin:
                continue
            if lot.phantom_for_sale_id and lot.phantom_for_sale_id != sale.sale_id:
                continue  # phantom restricted to a specific sale
            if lot.datum_nakupu > sale.datum_prodeje:
                continue
            pairs.append((i, j))

    if not pairs:
        print("WARN: Žádné kompatibilní páry lot→prodej — chybí historická data?",
              file=sys.stderr)
        return []

    years = sorted({s.rok for s in prodeje})

    solver = pywraplp.Solver.CreateSolver("GLOP")
    if not solver:
        raise RuntimeError("OR-Tools GLOP solver není dostupný")

    solver.SuppressOutput()

    # Variables x[i,j]
    x: dict[tuple[int, int], pywraplp.Variable] = {}
    for i, j in pairs:
        lot = loty[i]
        sale = prodeje[j]
        key = (sale.sale_id, lot.lot_id)
        ub = float(lot.mnozstvi)

        if sale.rok in zamknute_roky and key in locked:
            # Fix locked variables as equality
            v = solver.NumVar(float(locked[key]), float(locked[key]), f"x_{i}_{j}")
        else:
            v = solver.NumVar(0.0, ub, f"x_{i}_{j}")
        x[(i, j)] = v

    # (1) Lot capacity constraints
    for i, lot in enumerate(loty):
        lot_pairs = [(i, j) for (ii, j) in pairs if ii == i]
        if not lot_pairs:
            continue
        c = solver.Constraint(0.0, float(lot.mnozstvi))
        for pair in lot_pairs:
            c.SetCoefficient(x[pair], 1.0)

    # (2) Sale coverage constraints
    for j, sale in enumerate(prodeje):
        sale_pairs = [(i, j) for (i, jj) in pairs if jj == j]
        if not sale_pairs:
            print(f"WARN: Prodej {sale.sale_id} ({sale.coin} {sale.datum_prodeje}) "
                  f"nemá žádný kompatibilní lot — chybí historická data, prodej přeskočen",
                  file=sys.stderr)
            continue
        c = solver.Constraint(float(sale.mnozstvi), float(sale.mnozstvi))
        for pair in sale_pairs:
            c.SetCoefficient(x[pair], 1.0)

    # (3) gain_y and tax_base_y variables
    gain: dict[int, pywraplp.Variable] = {}
    tax_base: dict[int, pywraplp.Variable] = {}
    for y in years:
        gain[y] = solver.NumVar(-solver.infinity(), solver.infinity(), f"gain_{y}")
        tax_base[y] = solver.NumVar(0.0, solver.infinity(), f"tax_base_{y}")

    # gain_y = Σ over taxable pairs in year y: x[i,j] * gain_per_unit[i,j] - fees
    # gain_per_unit[i,j] = prijem_za_kus[j] - cena_za_kus[i]
    # lot fee: allocated proportionally x[i,j]/lot.mnozstvi * lot.fee_czk_total
    for y in years:
        # Build gain constraint: gain[y] = Σ contributions
        # LHS: gain[y] - Σ ... = -Σ fee_prodej
        sales_y = [(j, sale) for j, sale in enumerate(prodeje) if sale.rok == y]

        # Total sale fees for year y (constant, not a variable)
        total_sale_fee = sum(
            float(sale.prijem_za_kus_czk) * 0  # fee already baked into prijem_za_kus_czk
            for j, sale in sales_y
        )
        # Note: sale fee is already subtracted in prijem_za_kus_czk = (celkem-fee)/mnozstvi
        # So gain_per_unit = prijem_za_kus_czk - cena_za_kus_czk (fees already handled)

        # Constraint: gain[y] = Σ_{taxable pairs j in y} x[i,j] * (prijem[j] - cost[i] - alloc_fee[i])
        pairs_y = [(i, j) for (i, j) in pairs if prodeje[j].rok == y]
        if not pairs_y:
            # No sales this year — gain is 0
            c = solver.Constraint(0.0, 0.0)
            c.SetCoefficient(gain[y], 1.0)
            continue

        c = solver.Constraint(0.0, 0.0)
        c.SetCoefficient(gain[y], 1.0)

        for i, j in pairs_y:
            lot = loty[i]
            sale = prodeje[j]
            if je_osvobozeno(lot.datum_nakupu, sale.datum_prodeje):
                continue  # exempt pairs don't contribute to gain
            # gain per unit = sale price - lot cost - lot fee allocation
            gain_unit = float(sale.prijem_za_kus_czk - lot.cena_za_kus_czk)
            # lot fee per unit = fee_czk_total / mnozstvi (allocated proportionally)
            lot_fee_per_unit = float(lot.fee_czk_total / lot.mnozstvi) if lot.mnozstvi > 0 else 0.0
            # Scale to MCZK: keeps max matrix entry ~2 vs ~2M, condition number ≈ 1e5 vs 1e11
            net_gain_unit = (gain_unit - lot_fee_per_unit) * CZK_SCALE
            c.SetCoefficient(x[(i, j)], -net_gain_unit)  # note: move to RHS

        # tax_base[y] >= gain[y]
        tc = solver.Constraint(0.0, solver.infinity())
        tc.SetCoefficient(tax_base[y], 1.0)
        tc.SetCoefficient(gain[y], -1.0)

    # Objective: minimize Σ tax_base[y]
    objective = solver.Objective()
    for y in years:
        objective.SetCoefficient(tax_base[y], 1.0)
    objective.SetMinimization()

    status = solver.Solve()
    primary_ok = status in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE)
    if not primary_ok:
        print(f"WARN: LP solver vrátil status {status} — výsledky mohou být neúplné",
              file=sys.stderr)

    # ── Secondary LP: tie-break by preferring older lots (FIFO-like stability) ──
    # Only run if primary LP succeeded.
    # Capture primary solution values BEFORE modifying the model — accessing
    # solution_value() after model changes returns stale/zero values in GLOP.
    use_primary_fallback = False
    primary_x_vals: dict[tuple[int, int], float] = {}

    if primary_ok:
        primary_x_vals = {(i, j): x[(i, j)].solution_value() for (i, j) in pairs}
        primary_tax_base = {y: tax_base[y].solution_value() for y in years}

        for y in years:
            v = primary_tax_base[y]
            tol = 1.0 * CZK_SCALE  # ±1 CZK in scaled units
            c_fix = solver.Constraint(v - tol, v + tol)
            c_fix.SetCoefficient(tax_base[y], 1.0)

        # Tie-break objective: among tax-equal solutions, (a) never draw on a
        # phantom lot when real lots can cover the sale, (b) otherwise prefer
        # older lots (FIFO-like stability). days is normalised to [0,1] so the
        # fixed phantom penalty reliably dominates any FIFO preference — the
        # most a FIFO swing can gain per unit shifted is 1, and 10 ≫ 1, so an
        # optional phantom is always re-routed to a real lot. Only phantoms
        # forced by genuine lack of a date-compatible real lot survive.
        max_days = max(
            (prodeje[j].datum_prodeje - loty[i].datum_nakupu).days
            for i, j in pairs
        ) or 1
        PHANTOM_PENALTY = 10.0
        objective2 = solver.Objective()
        for y in years:
            objective2.SetCoefficient(tax_base[y], 0.0)
        for i, j in pairs:
            lot = loty[i]
            sale = prodeje[j]
            days = (sale.datum_prodeje - lot.datum_nakupu).days
            coef = -days / max_days  # ∈ [-1, 0]: prefer older lots (FIFO)
            if lot.phantom:
                coef += PHANTOM_PENALTY
            objective2.SetCoefficient(x[(i, j)], coef)
        objective2.SetMinimization()
        secondary_status = solver.Solve()
        if secondary_status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
            use_primary_fallback = True

    # ── Extract results ──────────────────────────────────────────────────────
    parovani = []
    for i, j in pairs:
        qty = (primary_x_vals.get((i, j), 0.0)
               if use_primary_fallback
               else x[(i, j)].solution_value())
        if qty < 1e-9:
            continue
        lot = loty[i]
        sale = prodeje[j]
        qty_dec = Decimal(str(qty)).quantize(QUANT)
        prijem = (qty_dec * sale.prijem_za_kus_czk).quantize(QUANT)
        # cost = qty * cena_za_kus + allocated fee
        naklad_base = (qty_dec * lot.cena_za_kus_czk).quantize(QUANT)
        alloc_fee = (qty_dec / lot.mnozstvi * lot.fee_czk_total).quantize(QUANT) if lot.mnozstvi > 0 else Decimal("0")
        naklad = naklad_base + alloc_fee
        zisk = prijem - naklad
        osvobozeno = "ano" if je_osvobozeno(lot.datum_nakupu, sale.datum_prodeje) else "ne"

        parovani.append({
            "prodej_id": sale.sale_id,
            "lot_id": lot.lot_id,
            "coin": sale.coin,
            "datum_prodeje": sale.datum_prodeje.isoformat(),
            "datum_nakupu": lot.datum_nakupu.isoformat(),
            "mnozstvi_pouzite": str(qty_dec),
            "prijem_czk": str(prijem),
            "naklad_czk": str(naklad),
            "zisk_czk": str(zisk),
            "osvobozeno": osvobozeno,
            "rok_prodeje": str(sale.rok),
        })

    # Warn about sales drawing on a phantom lot — earlier purchase undocumented.
    for p in parovani:
        if p["lot_id"].startswith("phantom:"):
            print(f"WARN: predaj {p['prodej_id']} ({p['coin']} {p['datum_prodeje']}): "
                  f"{p['mnozstvi_pouzite']} ks bez doloženého nákupu — zdanené v plnej "
                  f"výške ({p['prijem_czk']} CZK); doloženie skoršieho nákupu by daň znížilo",
                  file=sys.stderr)

    return parovani


# ── Main ──────────────────────────────────────────────────────────────────────

def run(
    vstup: Path,
    vystup: Path,
    config_path: Path,
    lock_config_path: Path,
    od_roku: int = 2023,
) -> None:
    coin_util.init(config_path.parent)

    with lock_config_path.open("rb") as f:
        lock_cfg = tomllib.load(f) if lock_config_path.exists() else {}

    zamknute_roky: list[int] = lock_cfg.get("zamknute", [])
    locked_parovani_path = Path(lock_cfg.get("zamcene_parovani", "build/parovani_zamcene.csv"))
    locked = _load_locked(locked_parovani_path)

    loty, prodeje = _load_enriched(vstup)

    # Filter sales to optimized years only (earlier years are statute-of-limitations or already filed)
    prodeje_opt = [s for s in prodeje if s.rok >= od_roku]
    pre_prodeje = [s for s in prodeje if s.rok < od_roku]
    vyrazeno = len(pre_prodeje)
    if vyrazeno:
        print(f"optimalizace: přeskočeno {vyrazeno} prodejů před rokem {od_roku} (promlčené/podané)",
              file=sys.stderr)

    # Subtract pre-od_roku lot consumption so LP cannot reuse already-disposed lots
    if pre_prodeje:
        pre_consumed = _greedy_consume(loty, pre_prodeje)
        loty_residual = []
        plne_spotrebovano = 0
        for lot in loty:
            used = pre_consumed.get(lot.lot_id, Decimal(0))
            residual = lot.mnozstvi - used
            if residual > Decimal("1e-9"):
                # Proportionally reduce fee so fee/unit stays constant → prevents
                # LP numerical overflow when residual is near-zero (e.g. 1 satoshi).
                fee_residual = (
                    lot.fee_czk_total * residual / lot.mnozstvi
                    if lot.mnozstvi > 0 else Decimal(0)
                )
                loty_residual.append(lot._replace(mnozstvi=residual, fee_czk_total=fee_residual))
            else:
                plne_spotrebovano += 1
        if plne_spotrebovano:
            print(f"optimalizace: {plne_spotrebovano} lotů plně spotřebováno před {od_roku} — vyřazeno z LP",
                  file=sys.stderr)
        loty = loty_residual

    print(f"optimalizace: {len(loty)} lotů, {len(prodeje_opt)} prodejů ({od_roku}+), "
          f"zamčené roky: {zamknute_roky}", file=sys.stderr)

    parovani = _solve(loty, prodeje_opt, locked, zamknute_roky)

    vystup.parent.mkdir(parents=True, exist_ok=True)
    with vystup.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PAROVANI_HEADER)
        writer.writeheader()
        writer.writerows(parovani)

    # Print summary per year
    roky: dict[int, dict] = {}
    for p in parovani:
        y = int(p["rok_prodeje"])
        if y not in roky:
            roky[y] = {"osvobozeno_zisk": Decimal("0"), "neosv_netto": Decimal("0")}
        zisk = Decimal(p["zisk_czk"])
        if p["osvobozeno"] == "ano":
            roky[y]["osvobozeno_zisk"] += zisk
        else:
            roky[y]["neosv_netto"] += zisk

    print("\n=== Souhrn po letech ===", file=sys.stderr)
    for y in sorted(roky):
        r = roky[y]
        zdanitelny = max(Decimal("0"), r["neosv_netto"])
        print(f"  {y}: zdanitelný zisk {zdanitelny:.2f} CZK "
              f"(neosvobozené netto {r['neosv_netto']:.2f} CZK), "
              f"osvobozeno {r['osvobozeno_zisk']:.2f} CZK", file=sys.stderr)
    print(f"\nParování: {len(parovani)} řádků → {vystup}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vstup", required=True, type=Path)
    parser.add_argument("--vystup", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--od-roku", type=int, default=2023,
                        help="Optimalizovat pouze prodeje od tohoto roku (dřívější jsou promlčené)")
    args = parser.parse_args()
    run(args.vstup, args.vystup, args.config, args.lock, od_roku=args.od_roku)
