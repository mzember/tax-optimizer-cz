"""Ingest Bittrex exports. Three file formats:

1. 2018 fullOrders (UTF-16): OrderUuid,Exchange,Type,Quantity,Limit,CommissionPaid,Price,Opened,Closed
   Exchange = "BASE-QUOTE", Type = LIMIT_BUY / LIMIT_SELL
   LIMIT_BUY  on BASE-QUOTE = buying QUOTE with BASE
   LIMIT_SELL on BASE-QUOTE = selling QUOTE for BASE

2. OrderHistory 2024 (UTF-8): TXID,Time (UTC),Transaction,Order Type,Market,Base,Quote,...
   Possibly empty (only header rows).

3. Transaction History (UTF-8): Date,Currency,Type,Address,Memo/Tag,TxId,Amount
   Type = DEPOSIT / WITHDRAWAL
"""

import argparse
import csv
import io
import sys
from datetime import datetime, timezone
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


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    # Detect BOM for UTF-16
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def _detect_format(header_row: str) -> str:
    h = header_row.lower()
    if "orderuuid" in h:
        return "full2018"
    if "txid" in h and "market" in h and "base" in h:
        return "order2024"
    if "currency" in h and "txid" in h and "address" in h:
        return "txhistory"
    return "unknown"


def _parse_dt_us(s: str) -> str:
    """Parse MM/DD/YYYY H:MM:SS AM/PM → ISO UTC string."""
    s = s.strip()
    dt = datetime.strptime(s, "%m/%d/%Y %I:%M:%S %p")
    return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt_iso(s: str) -> str:
    s = s.strip()
    if not s.endswith("Z"):
        s += "Z"
    # Handle milliseconds
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s.rstrip("Z"), fmt.rstrip("Z"))
            return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return s


def _parse_full2018(reader: csv.DictReader, path: Path) -> list[dict]:
    rows = []
    for i, row in enumerate(reader):
        exchange = row.get("Exchange", "").strip()  # e.g. "BTC-XMR"
        typ_raw = row.get("Type", "").strip()  # LIMIT_BUY / LIMIT_SELL
        quantity = _dec(row.get("Quantity", "0"))   # QUOTE amount
        commission = _dec(row.get("CommissionPaid", "0"))  # BASE amount fee
        price = _dec(row.get("Price", "0"))  # total BASE spent/received
        closed = row.get("Closed", "").strip() or row.get("Opened", "").strip()
        datum = _parse_dt_us(closed) if closed else ""

        if "-" not in exchange:
            continue
        base, quote = exchange.split("-", 1)
        base = base.upper()
        quote = quote.upper()

        trade_id = f"bittrex:{row.get('OrderUuid', '').strip() or i}"
        zdroj = str(dict(row))

        if "BUY" in typ_raw.upper():
            # Buying QUOTE with BASE
            rows.append({
                "id": trade_id, "burza": "bittrex", "datum_utc": datum,
                "typ": "NAKUP", "coin": quote, "mnozstvi": str(quantity),
                "protistrana_coin": base, "protistrana_mnozstvi": str(price),
                "fee_mnozstvi": str(commission), "fee_coin": base,
                "zdroj_radek": zdroj,
            })
        elif "SELL" in typ_raw.upper():
            # Selling QUOTE for BASE
            rows.append({
                "id": trade_id, "burza": "bittrex", "datum_utc": datum,
                "typ": "PRODEJ", "coin": quote, "mnozstvi": str(quantity),
                "protistrana_coin": base, "protistrana_mnozstvi": str(price),
                "fee_mnozstvi": str(commission), "fee_coin": base,
                "zdroj_radek": zdroj,
            })
    return rows


def _parse_order2024(reader: csv.DictReader, path: Path) -> list[dict]:
    rows = []
    for i, row in enumerate(reader):
        # Skip metadata lines (Name:, UserID:, Date Generated:)
        if not row.get("TXID", "").strip() or row.get("TXID", "").startswith("Name"):
            continue
        base = row.get("Base", "").strip().upper()
        quote = row.get("Quote", "").strip().upper()
        # Older exports: Order Type = "LIMIT_BUY" / "LIMIT_SELL"
        # Newer exports: Order Type = "Limit"/"Market", Transaction = "Bought"/"Sold"
        order_type = row.get("Order Type", "").strip().upper()
        transaction = row.get("Transaction", "").strip().upper()
        is_buy = "BUY" in order_type or transaction in ("BOUGHT", "BUY")
        is_sell = "SELL" in order_type or transaction in ("SOLD", "SELL")
        price = _dec(row.get("Price", "0"))
        qty_base = _dec(row.get("Quantity (Base)", "0"))
        fees_quote = _dec(row.get("Fees (Quote)", "0"))
        datum = _parse_dt_iso(row.get("Time (UTC)", ""))
        trade_id = f"bittrex:{row.get('TXID', '').strip() or i}"
        zdroj = str(dict(row))

        if is_buy and base:
            rows.append({
                "id": trade_id, "burza": "bittrex", "datum_utc": datum,
                "typ": "NAKUP", "coin": base, "mnozstvi": str(qty_base),
                "protistrana_coin": quote,
                "protistrana_mnozstvi": str(qty_base * price),
                "fee_mnozstvi": str(fees_quote), "fee_coin": quote,
                "zdroj_radek": zdroj,
            })
        elif is_sell and base:
            rows.append({
                "id": trade_id, "burza": "bittrex", "datum_utc": datum,
                "typ": "PRODEJ", "coin": base, "mnozstvi": str(qty_base),
                "protistrana_coin": quote,
                "protistrana_mnozstvi": str(qty_base * price),
                "fee_mnozstvi": str(fees_quote), "fee_coin": quote,
                "zdroj_radek": zdroj,
            })
    return rows


def _parse_txhistory(reader: csv.DictReader, path: Path) -> list[dict]:
    rows = []
    for i, row in enumerate(reader):
        typ_raw = row.get("Type", "").strip().upper()
        coin = row.get("Currency", "").strip().upper()
        amount = _dec(row.get("Amount", "0"))
        datum = _parse_dt_iso(row.get("Date", ""))
        txid = row.get("TxId", "").strip() or str(i)
        trade_id = f"bittrex:tx:{txid}"
        zdroj = str(dict(row))

        if typ_raw in ("DEPOSIT", "DEPOSITS"):
            rows.append({
                "id": trade_id, "burza": "bittrex", "datum_utc": datum,
                "typ": "DEPOSIT", "coin": coin, "mnozstvi": str(amount),
                "protistrana_coin": "", "protistrana_mnozstvi": "0",
                "fee_mnozstvi": "0", "fee_coin": "",
                "zdroj_radek": zdroj,
            })
        elif typ_raw in ("WITHDRAWAL", "WITHDRAWALS"):
            rows.append({
                "id": trade_id, "burza": "bittrex", "datum_utc": datum,
                "typ": "WITHDRAWAL", "coin": coin, "mnozstvi": str(amount),
                "protistrana_coin": "", "protistrana_mnozstvi": "0",
                "fee_mnozstvi": "0", "fee_coin": "",
                "zdroj_radek": zdroj,
            })
    return rows


def parse_file(path: Path) -> list[dict]:
    text = _read_text(path)
    # Strip null bytes left by some UTF-16 decoders
    text = text.replace("\x00", "")
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []

    # Skip Bittrex metadata preamble lines ("Name:", "UserID:", "Date Generated:")
    # that appear before the actual CSV header in newer exports.
    header_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("Name:") or stripped.startswith("UserID:") or stripped.startswith("Date Generated:"):
            continue
        header_idx = i
        break

    lines = lines[header_idx:]
    fmt = _detect_format(lines[0])
    reader = csv.DictReader(io.StringIO("\n".join(lines)))

    if fmt == "full2018":
        return _parse_full2018(reader, path)
    if fmt == "order2024":
        return _parse_order2024(reader, path)
    if fmt == "txhistory":
        return _parse_txhistory(reader, path)
    return []


def run(vstup: Path, vystup: Path) -> None:
    all_rows: list[dict] = []
    for f in sorted(vstup.glob("*.csv")):
        all_rows.extend(parse_file(f))

    vystup.parent.mkdir(parents=True, exist_ok=True)
    with vystup.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NORMALIZED_HEADER)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"bittrex: {len(all_rows)} řádků → {vystup}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vstup", required=True, type=Path)
    parser.add_argument("--vystup", required=True, type=Path)
    args = parser.parse_args()
    run(args.vstup, args.vystup)
