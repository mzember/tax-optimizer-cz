"""Ingest Coinmate transaction_history.csv.

Delimiter: semicolon. Two variants:
  v1: ID;Datum;Typ;Částka;Částka měny;Cena;Cena měny;Poplatek;Poplatek měny;Celkem;Celkem měny;...
  v2: ID;Datum;Účet;Typ;Částka;... (extra Účet column)

Types: BUY, SELL, WITHDRAWAL, QUICK_BUY, QUICK_SELL, DEPOSIT
Cena = price per unit of Částka měny (in Cena měny)
protistrana_mnozstvi = |Cena| * |Částka| (gross, before fee)
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
    """Convert 'YYYY-MM-DD HH:MM:SS' to ISO UTC."""
    s = s.strip()
    return s.replace(" ", "T") + "Z"


def _detect_has_ucet(header: list[str]) -> bool:
    return any("čet" in h.lower() or "ucet" in h.lower() for h in header)


def _map_header(header: list[str]) -> dict[str, str]:
    """Return normalized key→column mapping for both v1 and v2 variants."""
    m = {}
    for h in header:
        lh = h.strip().lower()
        if lh == "id":
            m["id"] = h
        elif "datum" in lh:
            m["datum"] = h
        elif "čet" in lh or "ucet" in lh:
            m["ucet"] = h
        elif "typ" in lh:
            m["typ"] = h
        elif "částka měny" in lh or "castka meny" in lh:
            m["castka_meny"] = h
        elif "částka" in lh or "castka" in lh:
            m["castka"] = h
        elif "cena měny" in lh or "cena meny" in lh:
            m["cena_meny"] = h
        elif "cena" in lh:
            m["cena"] = h
        elif "poplatek měny" in lh or "poplatek meny" in lh:
            m["poplatek_meny"] = h
        elif "poplatek" in lh:
            m["poplatek"] = h
        elif "celkem měny" in lh or "celkem meny" in lh:
            m["celkem_meny"] = h
        elif "celkem" in lh:
            m["celkem"] = h
        elif "status" in lh:
            m["status"] = h
    return m


def parse_file(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        hdr = reader.fieldnames or []
        m = _map_header(list(hdr))

        for i, row in enumerate(reader):
            status = row.get(m.get("status", "Status"), "").strip().upper()
            if status and status not in ("OK", "COMPLETED", ""):
                continue

            rid = f"coinmate:{row.get(m.get('id', 'ID'), '').strip() or i}"
            datum = _parse_dt(row.get(m.get("datum", "Datum"), ""))
            typ_raw = row.get(m.get("typ", "Typ"), "").strip().upper()
            coin = row.get(m.get("castka_meny", "Částka měny"), "").strip().upper()
            castka = _dec(row.get(m.get("castka", "Částka"), "0"))
            cena = _dec(row.get(m.get("cena", "Cena"), "0"))
            proto_coin = row.get(m.get("cena_meny", "Cena měny"), "").strip().upper()
            poplatek = _dec(row.get(m.get("poplatek", "Poplatek"), "0"))
            poplatek_meny = row.get(m.get("poplatek_meny", "Poplatek měny"), "").strip().upper()
            zdroj = str(dict(row))

            # gross proceeds/cost = price * quantity
            proto_mnozstvi = cena * castka

            if typ_raw in ("BUY", "QUICK_BUY"):
                rows.append({
                    "id": rid, "burza": "coinmate", "datum_utc": datum,
                    "typ": "NAKUP", "coin": coin, "mnozstvi": str(castka),
                    "protistrana_coin": proto_coin,
                    "protistrana_mnozstvi": str(proto_mnozstvi),
                    "fee_mnozstvi": str(poplatek), "fee_coin": poplatek_meny,
                    "zdroj_radek": zdroj,
                })
            elif typ_raw in ("SELL", "QUICK_SELL"):
                rows.append({
                    "id": rid, "burza": "coinmate", "datum_utc": datum,
                    "typ": "PRODEJ", "coin": coin, "mnozstvi": str(castka),
                    "protistrana_coin": proto_coin,
                    "protistrana_mnozstvi": str(proto_mnozstvi),
                    "fee_mnozstvi": str(poplatek), "fee_coin": poplatek_meny,
                    "zdroj_radek": zdroj,
                })
            elif typ_raw == "WITHDRAWAL":
                rows.append({
                    "id": rid, "burza": "coinmate", "datum_utc": datum,
                    "typ": "WITHDRAWAL", "coin": coin, "mnozstvi": str(castka),
                    "protistrana_coin": "", "protistrana_mnozstvi": "0",
                    "fee_mnozstvi": str(poplatek), "fee_coin": poplatek_meny,
                    "zdroj_radek": zdroj,
                })
            elif typ_raw == "DEPOSIT":
                rows.append({
                    "id": rid, "burza": "coinmate", "datum_utc": datum,
                    "typ": "DEPOSIT", "coin": coin, "mnozstvi": str(castka),
                    "protistrana_coin": "", "protistrana_mnozstvi": "0",
                    "fee_mnozstvi": "0", "fee_coin": "",
                    "zdroj_radek": zdroj,
                })
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
    print(f"coinmate: {len(all_rows)} řádků → {vystup}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vstup", required=True, type=Path)
    parser.add_argument("--vystup", required=True, type=Path)
    args = parser.parse_args()
    run(args.vstup, args.vystup)
