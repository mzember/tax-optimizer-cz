"""Validation checks on enriched trades and transfers.

Outputs: build/kontroly.md with ERRORs and WARNs.
Pipeline exits with code 1 if any ERRORs found.
"""

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "GUSD", "PAX"}
DUST_CZK_THRESHOLD = Decimal("100")
FIAT_COINS = {"CZK", "EUR", "USD", "GBP", "CHF", "HUF", "PLN"}


def _dec(s: str) -> Decimal:
    try:
        return Decimal(str(s).strip())
    except Exception:
        return Decimal("0")


def _dt(s: str) -> datetime:
    s = s.strip().rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.min


def check_balance(obchody: list[dict]) -> list[str]:
    """Detect negative running balances per coin."""
    balance: dict[str, Decimal] = defaultdict(Decimal)
    issues = []

    for row in sorted(obchody, key=lambda r: r.get("datum_utc", "")):
        coin = row.get("coin", "")
        proto = row.get("protistrana_coin", "")
        typ = row.get("typ", "")
        mnozstvi = _dec(row.get("mnozstvi", "0"))
        proto_mnozstvi = _dec(row.get("protistrana_mnozstvi", "0"))
        datum = row.get("datum_utc", "")[:10]

        if typ == "NAKUP":
            if coin not in FIAT_COINS:
                balance[coin] += mnozstvi
            if proto and proto not in FIAT_COINS:
                balance[proto] -= proto_mnozstvi
                if balance[proto] < -Decimal("0.0000001"):
                    issues.append(
                        f"WARN: Záporný zůstatek {proto} po NAKUP {coin} dne {datum}: "
                        f"{balance[proto]:.8f} — chybí historická data nebo transfer z jiné burzy?"
                    )
        elif typ == "PRODEJ":
            if coin not in FIAT_COINS:
                balance[coin] -= mnozstvi
                if balance[coin] < -Decimal("0.0000001"):
                    issues.append(
                        f"WARN: Záporný zůstatek {coin} po PRODEJ dne {datum}: "
                        f"{balance[coin]:.8f} — chybí historická data nebo transfer z jiné burzy?"
                    )
            if proto and proto not in FIAT_COINS:
                balance[proto] += proto_mnozstvi

    return issues, balance


def check_sale_before_acq(obchody: list[dict]) -> list[str]:
    """Detect sales before any acquisition exists for that coin."""
    first_nakup: dict[str, str] = {}
    issues = []
    for row in sorted(obchody, key=lambda r: r.get("datum_utc", "")):
        coin = row.get("coin", "")
        typ = row.get("typ", "")
        datum = row.get("datum_utc", "")
        if typ == "NAKUP" and coin not in first_nakup:
            first_nakup[coin] = datum
        elif typ == "PRODEJ":
            if coin not in first_nakup:
                issues.append(
                    f"ERROR: PRODEJ {coin} dne {datum[:10]} bez předchozího NAKUP — "
                    "chybí historická data?"
                )
    return issues


def check_missing_price(obchody: list[dict]) -> list[str]:
    """Detect rows with no CZK price (cena_za_kus_czk missing or zero)."""
    issues = []
    for row in obchody:
        cena = _dec(row.get("cena_za_kus_czk", "0"))
        if cena == 0:
            issues.append(
                f"WARN: Chybí CZK cena pro {row.get('coin')} dne {row.get('datum_utc','')[:10]} "
                f"(id={row.get('id')})"
            )
    return issues


def check_duplicates(obchody: list[dict]) -> list[str]:
    """Detect duplicate transaction IDs or soft duplicates."""
    seen_ids: dict[str, int] = defaultdict(int)
    seen_soft: dict[tuple, str] = {}
    issues = []

    for row in obchody:
        rid = row.get("id", "")
        seen_ids[rid] += 1

        soft_key = (
            row.get("burza", ""),
            row.get("datum_utc", "")[:19],
            row.get("coin", ""),
            row.get("mnozstvi", ""),
            row.get("protistrana_mnozstvi", ""),
        )
        if soft_key in seen_soft:
            issues.append(
                f"WARN: Možný duplikát: {rid} a {seen_soft[soft_key]} mají stejný "
                f"burza+datum+coin+mnozstvi"
            )
        else:
            seen_soft[soft_key] = rid

    for rid, count in seen_ids.items():
        if count > 1:
            issues.append(f"WARN: Duplicitní ID {rid} se vyskytuje {count}×")

    return issues


def check_unmatched_transfers(transfery: list[dict], manual_mapovani: Path | None) -> list[str]:
    """Heuristic: find withdrawal→deposit pairs across exchanges."""
    manual_pairs: set[tuple[str, str]] = set()
    if manual_mapovani and manual_mapovani.exists():
        with manual_mapovani.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                w = row.get("withdraw_id", "").strip()
                d = row.get("deposit_id", "").strip()
                if w and d and not w.startswith("#"):
                    manual_pairs.add((w, d))

    withdrawals = [r for r in transfery if r.get("typ") == "WITHDRAWAL"]
    deposits = [r for r in transfery if r.get("typ") == "DEPOSIT"]

    issues = []
    matched_w: set[str] = set()
    matched_d: set[str] = set()

    for w, d in manual_pairs:
        matched_w.add(w)
        matched_d.add(d)

    # Heuristic matching
    for wd in withdrawals:
        if wd.get("id") in matched_w:
            continue
        w_coin = wd.get("coin", "")
        w_amt = _dec(wd.get("mnozstvi", "0"))
        w_dt = _dt(wd.get("datum_utc", ""))
        w_burza = wd.get("burza", "")

        found = False
        for dp in deposits:
            if dp.get("id") in matched_d:
                continue
            if dp.get("coin", "") != w_coin:
                continue
            d_amt = _dec(dp.get("mnozstvi", "0"))
            d_dt = _dt(dp.get("datum_utc", ""))
            d_burza = dp.get("burza", "")
            if d_burza == w_burza:
                continue  # same exchange
            diff_amt = abs(w_amt - d_amt)
            diff_time = d_dt - w_dt
            if diff_amt <= max(Decimal("0.001"), w_amt * Decimal("0.01")) and timedelta(0) <= diff_time <= timedelta(hours=48):
                matched_w.add(wd["id"])
                matched_d.add(dp["id"])
                found = True
                break
        if not found:
            issues.append(
                f"WARN: Nespárovaný WITHDRAWAL {w_coin} {w_amt:.8f} dne "
                f"{wd.get('datum_utc','')[:10]} z {w_burza} (id={wd.get('id')})"
            )

    return issues


def check_stablecoin(obchody: list[dict]) -> list[str]:
    """Warn about stablecoin trades which are taxable disposals in CZ."""
    issues = []
    for row in obchody:
        coin = row.get("coin", "").upper()
        proto = row.get("protistrana_coin", "").upper()
        if coin in STABLECOINS or proto in STABLECOINS:
            issues.append(
                f"WARN: Stablecoin trade {coin}/{proto} dne {row.get('datum_utc','')[:10]} — "
                "v CZ je USDT/USDC technicky krypto, disposal je zdanitelná událost "
                f"(id={row.get('id')})"
            )
    return issues


def check_dust(obchody: list[dict]) -> list[str]:
    """Report tiny remaining balances after all trades."""
    balance: dict[str, Decimal] = defaultdict(Decimal)
    last_price: dict[str, Decimal] = {}

    for row in sorted(obchody, key=lambda r: r.get("datum_utc", "")):
        coin = row.get("coin", "")
        proto = row.get("protistrana_coin", "")
        typ = row.get("typ", "")
        mnozstvi = _dec(row.get("mnozstvi", "0"))
        proto_mnozstvi = _dec(row.get("protistrana_mnozstvi", "0"))
        cena = _dec(row.get("cena_za_kus_czk", "0"))

        if cena > 0:
            last_price[coin] = cena

        if typ == "NAKUP":
            balance[coin] += mnozstvi
            if proto:
                balance[proto] -= proto_mnozstvi
        elif typ == "PRODEJ":
            balance[coin] -= mnozstvi
            if proto:
                balance[proto] += proto_mnozstvi

    issues = []
    for coin, bal in balance.items():
        if bal <= 0:
            continue
        price = last_price.get(coin, Decimal("0"))
        value_czk = bal * price
        if 0 < value_czk < DUST_CZK_THRESHOLD:
            issues.append(
                f"WARN: Dust {coin}: zůstatek {bal:.8f} ≈ {value_czk:.2f} CZK — "
                "možný zapomenutý prodej nebo poplatek"
            )
    return issues


def run(
    vstup_obchody: Path,
    vstup_transfery: Path,
    vystup: Path,
    manual_mapovani: Path | None = None,
) -> None:
    obchody: list[dict] = []
    transfery: list[dict] = []

    with vstup_obchody.open(encoding="utf-8") as f:
        obchody = list(csv.DictReader(f))
    with vstup_transfery.open(encoding="utf-8") as f:
        transfery = list(csv.DictReader(f))

    all_issues = []
    _, final_balance = check_balance(obchody)
    balance_issues, _ = check_balance(obchody)
    all_issues.extend(balance_issues)
    all_issues.extend(check_sale_before_acq(obchody))
    all_issues.extend(check_missing_price(obchody))
    all_issues.extend(check_duplicates(obchody))
    all_issues.extend(check_unmatched_transfers(transfery, manual_mapovani))
    all_issues.extend(check_stablecoin(obchody))
    all_issues.extend(check_dust(obchody))

    errors = [i for i in all_issues if i.startswith("ERROR")]
    warns = [i for i in all_issues if i.startswith("WARN")]

    vystup.parent.mkdir(parents=True, exist_ok=True)
    with vystup.open("w", encoding="utf-8") as f:
        f.write("# Validační report\n\n")
        f.write(f"**{len(errors)} chyb, {len(warns)} varování**\n\n")
        if errors:
            f.write("## Chyby (blokují pipeline)\n\n")
            for e in errors:
                f.write(f"- {e}\n")
            f.write("\n")
        if warns:
            f.write("## Varování\n\n")
            for w in warns:
                f.write(f"- {w}\n")
            f.write("\n")
        if not all_issues:
            f.write("Vše v pořádku.\n")

    print(f"validace: {len(errors)} ERRORů, {len(warns)} WARNů → {vystup}", file=sys.stderr)
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--obchody", required=True, type=Path)
    parser.add_argument("--transfery", required=True, type=Path)
    parser.add_argument("--vystup", required=True, type=Path)
    parser.add_argument("--mapovani", type=Path, default=None)
    args = parser.parse_args()
    run(args.obchody, args.transfery, args.vystup, args.mapovani)
