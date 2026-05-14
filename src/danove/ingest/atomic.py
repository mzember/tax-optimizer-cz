"""Ingest Atomic Wallet history CSV.

Columns: DATE, TYPE, OUTAMOUNT, OUTCURRENCY, FEEAMOUNT, FEECURRENCY,
         OUTTXID, OUTTXURL, INAMOUNT, INCURRENCY, INTXID, INTXURL, ORDERID, ADDRESSTO

TYPE semantics:
  Transfer + OUTAMOUNT filled, INCURRENCY="-"  → WITHDRAWAL
  Transfer + INAMOUNT filled,  OUTCURRENCY="-" → DEPOSIT
  "-"      + INAMOUNT filled                   → DEPOSIT (claim / staking reward)
  regular / vote / freeze                       → skip (TRX internals)

Date format: "14 May 2026 at 01:17:41 CEST"  (CEST=UTC+2, CET=UTC+1)
"""

import argparse
import csv
import sys
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

NORMALIZED_HEADER = [
    "id", "burza", "datum_utc", "typ", "coin", "mnozstvi",
    "protistrana_coin", "protistrana_mnozstvi",
    "fee_mnozstvi", "fee_coin", "zdroj_radek",
]

_SKIP_TYPES = {"regular", "vote", "freeze"}


def _dec(s: str) -> Decimal:
    try:
        return Decimal(str(s).strip())
    except InvalidOperation:
        return Decimal("0")


def _parse_dt(s: str) -> str:
    s = s.strip().strip('"')
    if "CEST" in s:
        offset = timedelta(hours=2)
        s = s.replace(" CEST", "")
    elif "CET" in s:
        offset = timedelta(hours=1)
        s = s.replace(" CET", "")
    else:
        offset = timedelta(0)
    dt = datetime.strptime(s, "%d %B %Y at %H:%M:%S") - offset
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_empty(val: str) -> bool:
    return val.strip().strip('"') in ("-", "")


def parse_file(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f)):
            typ = row["TYPE"].strip().strip('"')
            if typ in _SKIP_TYPES:
                continue

            date_str = row["DATE"].strip()
            try:
                datum = _parse_dt(date_str)
            except ValueError:
                print(f"atomic: neznamý formát datumu na řádku {i+2}: {date_str!r}", file=sys.stderr)
                continue

            order_id = row["ORDERID"].strip().strip('"')
            rid = f"atomic:{order_id}"

            out_amount = row["OUTAMOUNT"].strip().strip('"')
            out_coin = row["OUTCURRENCY"].strip().strip('"')
            in_amount = row["INAMOUNT"].strip().strip('"')
            in_coin = row["INCURRENCY"].strip().strip('"')
            fee_amount = _dec(row["FEEAMOUNT"].strip().strip('"') or "0")
            fee_coin = row["FEECURRENCY"].strip().strip('"')

            if not _is_empty(out_amount) and _is_empty(in_amount):
                # outgoing transfer
                rows.append({
                    "id": rid, "burza": "atomic", "datum_utc": datum,
                    "typ": "WITHDRAWAL", "coin": out_coin,
                    "mnozstvi": out_amount,
                    "protistrana_coin": "", "protistrana_mnozstvi": "0",
                    "fee_mnozstvi": str(fee_amount), "fee_coin": fee_coin,
                    "zdroj_radek": str(dict(row)),
                })
            elif not _is_empty(in_amount):
                # incoming transfer or claim
                rows.append({
                    "id": rid, "burza": "atomic", "datum_utc": datum,
                    "typ": "DEPOSIT", "coin": in_coin,
                    "mnozstvi": in_amount,
                    "protistrana_coin": "", "protistrana_mnozstvi": "0",
                    "fee_mnozstvi": str(fee_amount), "fee_coin": fee_coin,
                    "zdroj_radek": str(dict(row)),
                })

    return rows


def run(vstup: Path, vystup: Path) -> None:
    all_rows: list[dict] = []
    for f in sorted(vstup.glob("*.csv")):
        all_rows.extend(parse_file(f))

    all_rows.sort(key=lambda r: r["datum_utc"])

    vystup.parent.mkdir(parents=True, exist_ok=True)
    with vystup.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NORMALIZED_HEADER)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"atomic: {len(all_rows)} řádků → {vystup}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vstup", required=True, type=Path)
    parser.add_argument("--vystup", required=True, type=Path)
    args = parser.parse_args()
    run(args.vstup, args.vystup)
