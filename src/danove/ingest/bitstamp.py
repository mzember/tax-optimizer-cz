"""Ingest Bitstamp TransactionsExport.csv.

Header: ID,Account,Type,Subtype,Datetime,Amount,Amount currency,
        Value,Value currency,Rate,Rate currency,Fee,Fee currency,Order ID
Types:  Market/Buy, Market/Sell, Deposit, Withdrawal
        Sub Account Transfer = internal Martin↔Main move, skipped (not a real transfer).
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
        return abs(Decimal(str(s).strip().replace(",", ".")))
    except InvalidOperation:
        return Decimal("0")


def _parse_dt(s: str) -> str:
    """Normalize to YYYY-MM-DDTHH:MM:SSZ (already ISO 8601 from Bitstamp)."""
    s = s.strip()
    if not s.endswith("Z"):
        s += "Z"
    return s


def parse_file(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            typ = row.get("Type", "").strip()
            subtyp = row.get("Subtype", "").strip()
            datum = _parse_dt(row.get("Datetime", ""))
            trade_id = f"bitstamp:{row.get('ID', '').strip() or i}"
            zdroj = str(dict(row))

            coin = row.get("Amount currency", "").strip().upper()
            mnozstvi = _dec(row.get("Amount", "0"))
            proto_coin = row.get("Value currency", "").strip().upper()
            proto_mnozstvi = _dec(row.get("Value", "0"))
            fee_mnozstvi = _dec(row.get("Fee", "0"))
            fee_coin = row.get("Fee currency", "").strip().upper()

            if typ == "Market" and subtyp == "Buy":
                rows.append({
                    "id": trade_id, "burza": "bitstamp", "datum_utc": datum,
                    "typ": "NAKUP", "coin": coin, "mnozstvi": str(mnozstvi),
                    "protistrana_coin": proto_coin, "protistrana_mnozstvi": str(proto_mnozstvi),
                    "fee_mnozstvi": str(fee_mnozstvi), "fee_coin": fee_coin,
                    "zdroj_radek": zdroj,
                })
            elif typ == "Market" and subtyp == "Sell":
                rows.append({
                    "id": trade_id, "burza": "bitstamp", "datum_utc": datum,
                    "typ": "PRODEJ", "coin": coin, "mnozstvi": str(mnozstvi),
                    "protistrana_coin": proto_coin, "protistrana_mnozstvi": str(proto_mnozstvi),
                    "fee_mnozstvi": str(fee_mnozstvi), "fee_coin": fee_coin,
                    "zdroj_radek": zdroj,
                })
            elif typ == "Deposit":
                rows.append({
                    "id": trade_id, "burza": "bitstamp", "datum_utc": datum,
                    "typ": "DEPOSIT", "coin": coin, "mnozstvi": str(mnozstvi),
                    "protistrana_coin": "", "protistrana_mnozstvi": "0",
                    "fee_mnozstvi": str(fee_mnozstvi), "fee_coin": fee_coin,
                    "zdroj_radek": zdroj,
                })
            elif typ == "Withdrawal":
                rows.append({
                    "id": trade_id, "burza": "bitstamp", "datum_utc": datum,
                    "typ": "WITHDRAWAL", "coin": coin, "mnozstvi": str(mnozstvi),
                    "protistrana_coin": "", "protistrana_mnozstvi": "0",
                    "fee_mnozstvi": str(fee_mnozstvi), "fee_coin": fee_coin,
                    "zdroj_radek": zdroj,
                })
            # "Sub Account Transfer" is an internal Martin↔Main move within
            # Bitstamp — the crypto never leaves the exchange, so it is not a
            # real deposit/withdrawal and would only pollute transfer matching.
            # else: Sub Account Transfer / unknown type — skip

    return rows


def run(vstup: Path, vystup: Path) -> None:
    all_rows: list[dict] = []
    for f in sorted(vstup.glob("*.csv")):
        all_rows.extend(parse_file(f))

    vystup.parent.mkdir(parents=True, exist_ok=True)
    with vystup.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NORMALIZED_HEADER)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"bitstamp: {len(all_rows)} řádků → {vystup}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vstup", required=True, type=Path)
    parser.add_argument("--vystup", required=True, type=Path)
    args = parser.parse_args()
    run(args.vstup, args.vystup)
