"""Zostatok lotov: koľko ešte ostáva predať a kedy bude osvobodené.

Číta build/obohaceno.csv (lot history) a build/parovani.csv (LP allocation).
Pre zadaný rok klasifikuje každý nevyčerpaný real lot podľa toho, kedy
prejde 3-ročný časový test (§4 ods. 1 písm. zk ZDP):

  CELÝ_ROK  : lot je osvobodený od 1.1. cieľového roku → predaj kedykoľvek bez dane
  POČAS     : prejde testom v priebehu cieľového roku → tax-free až od konkrétneho dňa
  PO        : prejde testom až po cieľovom roku → predaj v cieľovom roku je zdaniteľný

Phantom loty sa ignorujú — predstavujú nedoložené nákupy a nie sú reálnym
zostatkom v peňaženke.
"""

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from danove.optimalizace import _load_enriched
from danove.util import coin as coin_util

EPSILON = Decimal("0.00000001")
QUANT_AMT = Decimal("0.00000001")
QUANT_CZK = Decimal("0.01")


def _exempt_from(datum_nakupu: date) -> date:
    """Najskorší dátum predaja, kedy lot prejde 3-ročným testom (strictly >)."""
    try:
        hranice = datum_nakupu.replace(year=datum_nakupu.year + 3)
    except ValueError:
        hranice = datum_nakupu.replace(year=datum_nakupu.year + 3, day=28)
    return hranice + timedelta(days=1)


def _load_lp_usage(path: Path) -> dict[str, Decimal]:
    used: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lot_id = row["lot_id"]
            if lot_id.startswith("phantom:"):
                continue
            try:
                used[lot_id] += Decimal(row["mnozstvi_pouzite"].strip())
            except Exception:
                pass
    return used


def run(obchody_path: Path, parovani_path: Path, vystup_path: Path,
        config_path: Path, rok: int) -> None:

    coin_util.init(config_path.parent)
    loty, _ = _load_enriched(obchody_path)
    used = _load_lp_usage(parovani_path)

    rok_start = date(rok, 1, 1)
    rok_end = date(rok, 12, 31)

    # ── Compute remaining inventory per lot ─────────────────────────────────
    rows: list[dict] = []
    for lot in loty:
        if lot.phantom:
            continue
        zostava = lot.mnozstvi - used.get(lot.lot_id, Decimal(0))
        if zostava <= EPSILON:
            continue
        osvob_od = _exempt_from(lot.datum_nakupu)
        if osvob_od <= rok_start:
            kategoria = "CELY_ROK"
        elif osvob_od <= rok_end:
            kategoria = "POCAS"
        else:
            kategoria = "PO"
        rows.append({
            "lot_id": lot.lot_id,
            "coin": lot.coin,
            "datum_nakupu": lot.datum_nakupu,
            "zostava": zostava,
            "cena_za_kus_czk": lot.cena_za_kus_czk,
            "naklad_zost": (zostava * lot.cena_za_kus_czk).quantize(QUANT_CZK),
            "osvob_od": osvob_od,
            "kategoria": kategoria,
        })

    # ── Aggregate per coin × kategoria ───────────────────────────────────────
    agg: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"mnozstvi": Decimal(0), "naklad": Decimal(0)}
    )
    for r in rows:
        key = (r["coin"], r["kategoria"])
        agg[key]["mnozstvi"] += r["zostava"]
        agg[key]["naklad"] += r["naklad_zost"]

    # Coiny zoradené podľa CZK hodnoty zostatku (tie najvýznamnejšie hore)
    naklad_per_coin: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for (coin, _), v in agg.items():
        naklad_per_coin[coin] += v["naklad"]
    coins = sorted(naklad_per_coin.keys(), key=lambda c: -naklad_per_coin[c])

    # ── Build markdown ───────────────────────────────────────────────────────
    lines: list[str] = [
        f"# Zostatok lotov — pohľad pre rok {rok}",
        "",
        "Reálne nakúpené loty mínus to, čo už LP priradil k predajom. "
        "Phantom loty (nedoložené nákupy) sa neuvádzajú. "
        "Coiny sú zoradené podľa CZK nákladu zostatku (najvyšší hore).",
        "",
        "## Sumár per coin a kategória",
        "",
        "| Coin | Kategória | Množstvo | Náklad CZK | Pozn. |",
        "|------|-----------|---------:|-----------:|-------|",
    ]
    kat_popis = {
        "CELY_ROK": f"osvobodené celý rok {rok} (predaj bez dane)",
        "POCAS":    f"osvobodené až počas roku {rok} (viď tabuľka nižšie)",
        "PO":       f"v roku {rok} ešte zdaniteľné (osvobodenie až {rok+1}+)",
    }
    for coin in coins:
        for kat in ("CELY_ROK", "POCAS", "PO"):
            v = agg.get((coin, kat))
            if not v or v["mnozstvi"] <= EPSILON:
                continue
            lines.append(
                f"| {coin} | {kat} "
                f"| {v['mnozstvi'].quantize(QUANT_AMT)} "
                f"| {v['naklad'].quantize(QUANT_CZK):,} "
                f"| {kat_popis[kat]} |"
            )
    lines.append("")

    # ── Detail: lots becoming exempt during the year ─────────────────────────
    pocas = sorted([r for r in rows if r["kategoria"] == "POCAS"],
                   key=lambda r: (r["coin"], r["osvob_od"]))
    if pocas:
        lines += [
            f"## Loty, ktoré prejdú 3-ročným testom v priebehu {rok}",
            "",
            "Predaj pred uvedeným dátumom = zdaniteľný; v deň/po dni = osvobodený.",
            "",
            "| Coin | Lot ID | Dátum nákupu | Osvob. od | Zostáva | Cena/ks CZK | Náklad CZK |",
            "|------|--------|--------------|-----------|--------:|------------:|-----------:|",
        ]
        for r in pocas:
            lines.append(
                f"| {r['coin']} | `{r['lot_id']}` | {r['datum_nakupu']} "
                f"| **{r['osvob_od']}** "
                f"| {r['zostava'].quantize(QUANT_AMT)} "
                f"| {r['cena_za_kus_czk'].quantize(QUANT_CZK):,} "
                f"| {r['naklad_zost']:,} |"
            )
        lines.append("")

    # ── Detail: lots still taxable in the target year ────────────────────────
    po = sorted([r for r in rows if r["kategoria"] == "PO"],
                key=lambda r: (r["coin"], r["osvob_od"]))
    if po:
        lines += [
            f"## Loty zdaniteľné v {rok} (osvobodenie až po {rok})",
            "",
            "| Coin | Lot ID | Dátum nákupu | Osvob. od | Zostáva | Cena/ks CZK | Náklad CZK |",
            "|------|--------|--------------|-----------|--------:|------------:|-----------:|",
        ]
        for r in po:
            lines.append(
                f"| {r['coin']} | `{r['lot_id']}` | {r['datum_nakupu']} "
                f"| {r['osvob_od']} "
                f"| {r['zostava'].quantize(QUANT_AMT)} "
                f"| {r['cena_za_kus_czk'].quantize(QUANT_CZK):,} "
                f"| {r['naklad_zost']:,} |"
            )
        lines.append("")

    # ── Záver pre cieľový rok ────────────────────────────────────────────────
    # Súčet CZK nákladov je porovnateľný naprieč coinmi (množstvá nie).
    naklad_taxfree = sum(
        (v["naklad"] for (c, k), v in agg.items() if k in ("CELY_ROK", "POCAS")),
        start=Decimal(0),
    )
    naklad_taxable = sum(
        (v["naklad"] for (c, k), v in agg.items() if k == "PO"),
        start=Decimal(0),
    )
    taxable_per_coin = [
        (c, v["mnozstvi"], v["naklad"])
        for (c, k), v in agg.items() if k == "PO" and v["mnozstvi"] > EPSILON
    ]
    taxable_per_coin.sort(key=lambda t: -t[2])

    lines += [
        f"## Čo ešte v {rok} môžem predať s minimálnou daňou",
        "",
        f"- **Bez dane (3-ročný test splnený):** zostatok s nákladovou bázou "
        f"**{naklad_taxfree.quantize(QUANT_CZK):,} Kč** "
        "(detail vyššie, množstvá per coin v ich vlastných jednotkách).",
        f"- **Zdaniteľná zásoba** (test ešte neuplynul): nákladová báza "
        f"**{naklad_taxable.quantize(QUANT_CZK):,} Kč**.",
    ]
    if taxable_per_coin:
        lines.append("  - Z toho per coin:")
        for c, m, n in taxable_per_coin:
            lines.append(
                f"    - {c}: {m.quantize(QUANT_AMT)} (náklad {n.quantize(QUANT_CZK):,} Kč)"
            )
    lines += [
        "",
        f"### Ak v {rok} pribudnú ďalšie nákupy",
        "",
        f"- Nový nákup v {rok} **nemôže byť osvobodený v {rok}** "
        f"(3-ročný test by uplynul až v {rok+3}+).",
        "- Môže však znížiť daň cez **vnútroročný netting** (§10 ods. 4 ZDP): "
        "ak ho v rovnakom roku predáš so stratou, tá sa odpočíta od ziskov "
        "z neosvobodených predajov daného roku. Cez roky sa straty neprenášajú.",
        "- Inak nový nákup len rozšíri zásobu, ktorá sa stane osvobodenou "
        "v príslušnom budúcom roku.",
        "",
    ]

    vystup_path.parent.mkdir(parents=True, exist_ok=True)
    vystup_path.write_text("\n".join(lines), encoding="utf-8")
    n_lots = len(rows)
    print(f"zostatok ({rok}): {n_lots} aktívnych lotov "
          f"→ tax-free {naklad_taxfree.quantize(QUANT_CZK):,} Kč náklad, "
          f"taxable {naklad_taxable.quantize(QUANT_CZK):,} Kč náklad "
          f"→ {vystup_path}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--obchody", required=True, type=Path)
    parser.add_argument("--parovani", required=True, type=Path)
    parser.add_argument("--vystup", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--rok", required=True, type=int)
    args = parser.parse_args()
    run(args.obchody, args.parovani, args.vystup, args.config, rok=args.rok)
