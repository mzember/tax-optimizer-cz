"""Merge all normalized per-exchange CSVs into vsechny_obchody.csv and vsechny_transfery.csv."""

import argparse
import csv
import sys
from pathlib import Path

NORMALIZED_HEADER = [
    "id", "burza", "datum_utc", "typ", "coin", "mnozstvi",
    "protistrana_coin", "protistrana_mnozstvi",
    "fee_mnozstvi", "fee_coin", "zdroj_radek",
]

TRADE_TYPY = {"NAKUP", "PRODEJ"}
TRANSFER_TYPY = {"DEPOSIT", "WITHDRAWAL"}


def _load_vyloucene(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ids.add(line)
    return ids


def run(vstup_dir: Path, vystup_obchody: Path, vystup_transfery: Path,
        vyloucene_path: Path | None = None) -> None:
    vyloucene = _load_vyloucene(vyloucene_path)
    obchody: list[dict] = []
    transfery: list[dict] = []
    seen_ids: set[str] = set()
    duplicates = 0
    vylouceno = 0

    for csv_file in sorted(vstup_dir.glob("*.csv")):
        with csv_file.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rid = row.get("id", "")
                if rid in seen_ids:
                    duplicates += 1
                    continue
                seen_ids.add(rid)
                if rid in vyloucene:
                    vylouceno += 1
                    continue

                typ = row.get("typ", "").strip().upper()
                if typ in TRADE_TYPY:
                    obchody.append(row)
                elif typ in TRANSFER_TYPY:
                    transfery.append(row)

    # Sort chronologically
    obchody.sort(key=lambda r: r.get("datum_utc", ""))
    transfery.sort(key=lambda r: r.get("datum_utc", ""))

    vystup_obchody.parent.mkdir(parents=True, exist_ok=True)
    with vystup_obchody.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NORMALIZED_HEADER)
        writer.writeheader()
        writer.writerows(obchody)

    with vystup_transfery.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NORMALIZED_HEADER)
        writer.writeheader()
        writer.writerows(transfery)

    vylouc_msg = f", {vylouceno} vyloučeno" if vylouceno else ""
    print(f"konsolidace: {len(obchody)} obchodů, {len(transfery)} transferů, "
          f"{duplicates} duplikátů přeskočeno{vylouc_msg} → {vystup_obchody}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vstup", required=True, type=Path)
    parser.add_argument("--vystup-obchody", required=True, type=Path)
    parser.add_argument("--vystup-transfery", required=True, type=Path)
    parser.add_argument("--vyloucene", type=Path, default=None)
    args = parser.parse_args()
    run(args.vstup, args.vystup_obchody, args.vystup_transfery, args.vyloucene)
