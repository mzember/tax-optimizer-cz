"""Ingest Binance Transaction History CSV.

Format: User ID,Time,Account,Operation,Coin,Change,Remark
Date:   YY-MM-DD HH:MM:SS (2-digit year)
Trades: one logical trade = 2-3 atomic rows with same timestamp:
          Sell <coin> <negative>  → protistrana_coin
          Buy  <coin> <positive>  → coin acquired
          Fee  <coin> <negative>  → fee (often same coin as Buy)
"""

import argparse
import csv
import hashlib
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from itertools import groupby
from pathlib import Path

NORMALIZED_HEADER = [
    "id", "burza", "datum_utc", "typ", "coin", "mnozstvi",
    "protistrana_coin", "protistrana_mnozstvi",
    "fee_mnozstvi", "fee_coin", "zdroj_radek",
]


def _parse_dt(s: str) -> datetime:
    dt = datetime.strptime(s.strip(), "%y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)


def _dec(s: str) -> Decimal:
    try:
        return abs(Decimal(s.strip()))
    except InvalidOperation:
        return Decimal("0")


def _row_id(burza: str, ts: str, coin: str, change: str) -> str:
    h = hashlib.sha1(f"{ts}|{coin}|{change}".encode()).hexdigest()[:12]
    return f"{burza}:{h}"


def _cluster_to_trade(rows: list[dict], datum_utc: str) -> dict | None:
    """Convert a cluster of same-timestamp rows into one normalized trade row."""
    buy_rows = [r for r in rows if r["Operation"] == "Buy"]
    sell_rows = [r for r in rows if r["Operation"] == "Sell"]
    fee_rows = [r for r in rows if r["Operation"] == "Fee"]

    if not buy_rows or not sell_rows:
        return None

    buy = buy_rows[0]
    sell = sell_rows[0]
    fee = fee_rows[0] if fee_rows else None

    coin = buy["Coin"].strip().upper()
    proto_coin = sell["Coin"].strip().upper()
    mnozstvi = _dec(buy["Change"])
    proto_mnozstvi = _dec(sell["Change"])
    fee_mnozstvi = _dec(fee["Change"]) if fee else Decimal("0")
    fee_coin = fee["Coin"].strip().upper() if fee else ""

    raw = "|".join(r["Change"] + r["Coin"] for r in rows)
    trade_id = _row_id("binance", datum_utc, coin, raw)

    return {
        "id": trade_id,
        "burza": "binance",
        "datum_utc": datum_utc,
        "typ": "NAKUP",
        "coin": coin,
        "mnozstvi": str(mnozstvi),
        "protistrana_coin": proto_coin,
        "protistrana_mnozstvi": str(proto_mnozstvi),
        "fee_mnozstvi": str(fee_mnozstvi),
        "fee_coin": fee_coin,
        "zdroj_radek": f"cluster:{len(rows)} rows",
    }


def _deposit_row(row: dict, datum_utc: str) -> dict:
    coin = row["Coin"].strip().upper()
    rid = _row_id("binance", datum_utc, coin, row["Change"])
    return {
        "id": rid,
        "burza": "binance",
        "datum_utc": datum_utc,
        "typ": "DEPOSIT",
        "coin": coin,
        "mnozstvi": _dec(row["Change"]),
        "protistrana_coin": "",
        "protistrana_mnozstvi": "0",
        "fee_mnozstvi": "0",
        "fee_coin": "",
        "zdroj_radek": str(row),
    }


def _withdrawal_row(row: dict, datum_utc: str) -> dict:
    r = _deposit_row(row, datum_utc)
    r["typ"] = "WITHDRAWAL"
    return r


def parse_file(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        all_rows = list(reader)

    trade_ops = {"Buy", "Sell", "Fee"}
    deposit_ops = {"Deposit", "deposit"}
    withdrawal_ops = {"Withdrawal", "withdrawal"}
    ignore_ops = {"Distribution", "distribution", "Savings distribution",
                  "Staking Rewards", "POS savings interest", "Launchpool Earnings"}

    # Group by timestamp for trade clustering
    all_rows.sort(key=lambda r: r.get("Time", ""))

    for ts, group in groupby(all_rows, key=lambda r: r.get("Time", "")):
        cluster = list(group)
        datum_utc = _parse_dt(ts).strftime("%Y-%m-%dT%H:%M:%SZ")

        trade_cluster = [r for r in cluster if r.get("Operation", "") in trade_ops]
        other_rows = [r for r in cluster if r.get("Operation", "") not in trade_ops]

        if trade_cluster:
            trade = _cluster_to_trade(trade_cluster, datum_utc)
            if trade:
                rows.append(trade)

        for row in other_rows:
            op = row.get("Operation", "")
            if op in deposit_ops:
                rows.append(_deposit_row(row, datum_utc))
            elif op in withdrawal_ops:
                rows.append(_withdrawal_row(row, datum_utc))
            elif op in ignore_ops:
                pass  # staking/savings — out of scope for V1
            # else: unknown op, skip silently

    return rows


def run(vstup: Path, vystup: Path) -> None:
    all_rows: list[dict] = []
    csv_files = sorted(vstup.glob("*.csv"))
    for f in csv_files:
        all_rows.extend(parse_file(f))

    vystup.parent.mkdir(parents=True, exist_ok=True)
    with vystup.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NORMALIZED_HEADER)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"binance: {len(all_rows)} řádků → {vystup}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vstup", required=True, type=Path)
    parser.add_argument("--vystup", required=True, type=Path)
    args = parser.parse_args()
    run(args.vstup, args.vystup)
