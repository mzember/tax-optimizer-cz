"""Ingest Electrum wallet history CSV.

Columns: transaction_hash, label, confirmations, value, fiat_value, fee, fiat_fee, timestamp

value  > 0 → DEPOSIT  (BTC received into wallet, no fee)
             OR NAKUP  (if filename is in --jako-nakupy list)
value  < 0 → WITHDRAWAL (BTC sent from wallet)
            mnozstvi   = abs(value) - fee  (amount actually leaving to recipient)
            fee_mnozstvi = fee             (miner fee)

Rows with no timestamp (confirmations = 0, unconfirmed) are skipped.

--jako-nakupy SOUBOR [SOUBOR...]: treat incoming transactions in these files
  as NAKUP (acquisition lot) instead of DEPOSIT (transfer). Use for wallets
  where received BTC represents a real purchase/income, not a cross-exchange
  transfer. The enrichment step will assign the market BTC/CZK price at the
  transaction date as the cost basis.
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


def parse_file(path: Path, jako_nakup: bool = False) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f)):
            ts = row.get("timestamp", "").strip()
            if not ts:
                print(f"electrum: přeskočen řádek {i+2} bez timestamp "
                      f"(txhash={row.get('transaction_hash','')[:16]}…)", file=sys.stderr)
                continue

            txhash = row["transaction_hash"].strip()
            rid = f"electrum:{txhash}"
            datum = ts.replace(" ", "T") + "Z"
            value = _dec(row.get("value", "0"))
            fee = _dec(row.get("fee", "0") or "0")

            if value > 0:
                typ = "NAKUP" if jako_nakup else "DEPOSIT"
                rows.append({
                    "id": rid, "burza": "electrum", "datum_utc": datum,
                    "typ": typ, "coin": "BTC", "mnozstvi": str(value),
                    "protistrana_coin": "", "protistrana_mnozstvi": "0",
                    "fee_mnozstvi": "0", "fee_coin": "",
                    "zdroj_radek": str(dict(row)),
                })
            elif value < 0:
                sent = abs(value) - fee
                rows.append({
                    "id": rid, "burza": "electrum", "datum_utc": datum,
                    "typ": "WITHDRAWAL", "coin": "BTC", "mnozstvi": str(sent),
                    "protistrana_coin": "", "protistrana_mnozstvi": "0",
                    "fee_mnozstvi": str(fee), "fee_coin": "BTC",
                    "zdroj_radek": str(dict(row)),
                })
            # value == 0: interná konsolidácia (change output mimo peňaženky) — preskočiť

    return rows


def run(vstup: Path, vystup: Path, jako_nakupy: set[str] | None = None) -> None:
    all_rows: list[dict] = []
    for f in sorted(vstup.glob("*.csv")):
        jako_nakup = (jako_nakupy is not None) and (f.name in jako_nakupy)
        if jako_nakup:
            print(f"electrum: {f.name} — příchozí transakce jako NAKUP", file=sys.stderr)
        all_rows.extend(parse_file(f, jako_nakup=jako_nakup))

    all_rows.sort(key=lambda r: r["datum_utc"])

    vystup.parent.mkdir(parents=True, exist_ok=True)
    with vystup.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NORMALIZED_HEADER)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"electrum: {len(all_rows)} řádků → {vystup}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vstup", required=True, type=Path)
    parser.add_argument("--vystup", required=True, type=Path)
    parser.add_argument(
        "--jako-nakupy", nargs="*", metavar="SOUBOR", default=None,
        help="Názvy súborov (len basename), kde príchozí TX → NAKUP namiesto DEPOSIT",
    )
    args = parser.parse_args()
    jako_nakupy = set(args.jako_nakupy) if args.jako_nakupy is not None else None
    run(args.vstup, args.vystup, jako_nakupy=jako_nakupy)
