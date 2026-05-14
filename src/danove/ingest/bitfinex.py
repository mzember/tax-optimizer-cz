"""Ingest Bitfinex exports.

Two relevant files per export batch (matched by glob pattern):
  *trades*.csv     — executed trades
  *movements*.csv  — deposits and withdrawals
  *orders*.csv     — skipped (redundant with trades)

Trades columns:
  #, PAIR, AMOUNT, PRICE, FEE, FEE PERC, FEE CURRENCY, DATE, ORDER ID

  PAIR = "BASE/QUOTE"
  AMOUNT > 0 → NAKUP of BASE (paid QUOTE * PRICE)
  AMOUNT < 0 → PRODEJ of BASE (received QUOTE * PRICE)

Movements columns:
  #, STARTED, UPDATED, CURRENCY, STATUS, AMOUNT, FEES, DESCRIPTION, TRANSACTION ID, NOTE

  STATUS != COMPLETED → skip
  AMOUNT < 0 → WITHDRAWAL: mnozstvi = abs(AMOUNT), fee = abs(FEES)
  AMOUNT > 0 → DEPOSIT:    mnozstvi = AMOUNT

Bitfinex-specific tickers remapped via coin_aliases.csv:
  DSH → DASH,  IOT → MIOTA (IOTA),  BCH → BCH
"""

import argparse
import csv
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

NORMALIZED_HEADER = [
    "id", "burza", "datum_utc", "typ", "coin", "mnozstvi",
    "protistrana_coin", "protistrana_mnozstvi",
    "fee_mnozstvi", "fee_coin", "zdroj_radek",
]


def _dec(s: str) -> Decimal:
    try:
        return Decimal(str(s).strip())
    except InvalidOperation:
        return Decimal("0")


def _parse_dt(s: str) -> str:
    return s.strip().replace(" ", "T") + "Z"


def _parse_trades(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pair = row["PAIR"].strip()
            if "/" not in pair:
                continue
            base, quote = pair.split("/", 1)
            amount = _dec(row["AMOUNT"])
            price = _dec(row["PRICE"])
            fee = abs(_dec(row["FEE"]))
            fee_coin = row["FEE CURRENCY"].strip()
            datum = _parse_dt(row["DATE"])
            rid = f"bitfinex:{row['#'].strip()}"

            proto_mnozstvi = (abs(amount) * price).quantize(Decimal("0.00000001"))

            if amount > 0:
                rows.append({
                    "id": rid, "burza": "bitfinex", "datum_utc": datum,
                    "typ": "NAKUP", "coin": base, "mnozstvi": str(amount),
                    "protistrana_coin": quote,
                    "protistrana_mnozstvi": str(proto_mnozstvi),
                    "fee_mnozstvi": str(fee), "fee_coin": fee_coin,
                    "zdroj_radek": str(dict(row)),
                })
            elif amount < 0:
                rows.append({
                    "id": rid, "burza": "bitfinex", "datum_utc": datum,
                    "typ": "PRODEJ", "coin": base, "mnozstvi": str(abs(amount)),
                    "protistrana_coin": quote,
                    "protistrana_mnozstvi": str(proto_mnozstvi),
                    "fee_mnozstvi": str(fee), "fee_coin": fee_coin,
                    "zdroj_radek": str(dict(row)),
                })
    return rows


def _parse_movements(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["STATUS"].strip().upper() != "COMPLETED":
                continue
            amount = _dec(row["AMOUNT"])
            fee = abs(_dec(row["FEES"]))
            coin = row["CURRENCY"].strip()
            datum = _parse_dt(row["STARTED"])
            rid = f"bitfinex:m{row['#'].strip()}"

            if amount < 0:
                rows.append({
                    "id": rid, "burza": "bitfinex", "datum_utc": datum,
                    "typ": "WITHDRAWAL", "coin": coin,
                    "mnozstvi": str(abs(amount)),
                    "protistrana_coin": "", "protistrana_mnozstvi": "0",
                    "fee_mnozstvi": str(fee), "fee_coin": coin,
                    "zdroj_radek": str(dict(row)),
                })
            elif amount > 0:
                rows.append({
                    "id": rid, "burza": "bitfinex", "datum_utc": datum,
                    "typ": "DEPOSIT", "coin": coin,
                    "mnozstvi": str(amount),
                    "protistrana_coin": "", "protistrana_mnozstvi": "0",
                    "fee_mnozstvi": "0", "fee_coin": "",
                    "zdroj_radek": str(dict(row)),
                })
    return rows


def run(vstup: Path, vystup: Path) -> None:
    all_rows: list[dict] = []

    for f in sorted(vstup.glob("*trades*.csv")):
        all_rows.extend(_parse_trades(f))
    for f in sorted(vstup.glob("*movements*.csv")):
        all_rows.extend(_parse_movements(f))

    all_rows.sort(key=lambda r: r["datum_utc"])

    vystup.parent.mkdir(parents=True, exist_ok=True)
    with vystup.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NORMALIZED_HEADER)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"bitfinex: {len(all_rows)} řádků → {vystup}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vstup", required=True, type=Path)
    parser.add_argument("--vystup", required=True, type=Path)
    args = parser.parse_args()
    run(args.vstup, args.vystup)
