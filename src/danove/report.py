"""Generate per-year reports: CSV, Markdown, XLSX."""

import argparse
import csv
import sys
from decimal import Decimal
from pathlib import Path

REPORT_HEADER = [
    "datum_prodeje", "coin", "mnozstvi_z_lotu",
    "prijem_czk", "naklad_czk", "fee_v_nakladu_czk", "zisk_czk",
    "osvobozeno", "datum_nakupu_lotu",
    "prodej_id", "lot_id",
]


def _dec(s: str) -> Decimal:
    try:
        return Decimal(str(s).strip())
    except Exception:
        return Decimal("0")


def _load_parovani(path: Path, rok: int) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if int(row.get("rok_prodeje", "0")) == rok:
                rows.append(row)
    rows.sort(key=lambda r: r.get("datum_prodeje", ""))
    return rows


def _build_report_row(p: dict) -> dict:
    return {
        "datum_prodeje": p.get("datum_prodeje", ""),
        "coin": p.get("coin", ""),
        "mnozstvi_z_lotu": p.get("mnozstvi_pouzite", ""),
        "prijem_czk": p.get("prijem_czk", ""),
        "naklad_czk": p.get("naklad_czk", ""),
        "fee_v_nakladu_czk": "0",  # already embedded in naklad_czk
        "zisk_czk": p.get("zisk_czk", ""),
        "osvobozeno": p.get("osvobozeno", ""),
        "datum_nakupu_lotu": p.get("datum_nakupu", ""),
        "prodej_id": p.get("prodej_id", ""),
        "lot_id": p.get("lot_id", ""),
    }


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def _compute_sums(rows: list[dict]) -> dict:
    def _empty():
        return {"prijem": Decimal("0"), "naklad": Decimal("0"),
                "zisk": Decimal("0"), "osvobozeno": Decimal("0"),
                "zdanitelny": Decimal("0"), "neosv_strata": Decimal("0")}

    totals: dict[str, dict] = {}
    grand = _empty()

    for r in rows:
        coin = r.get("coin", "?")
        prijem = _dec(r.get("prijem_czk", "0"))
        naklad = _dec(r.get("naklad_czk", "0"))
        zisk = _dec(r.get("zisk_czk", "0"))
        osv = r.get("osvobozeno", "ne") == "ano"

        if coin not in totals:
            totals[coin] = _empty()
        totals[coin]["prijem"] += prijem
        totals[coin]["naklad"] += naklad
        totals[coin]["zisk"] += zisk
        if osv:
            totals[coin]["osvobozeno"] += zisk
        elif zisk > 0:
            totals[coin]["zdanitelny"] += zisk
        else:
            totals[coin]["neosv_strata"] += zisk  # záporné — straty neosvobozených

        grand["prijem"] += prijem
        grand["naklad"] += naklad
        grand["zisk"] += zisk
        if osv:
            grand["osvobozeno"] += zisk
        elif zisk > 0:
            grand["zdanitelny"] += zisk
        else:
            grand["neosv_strata"] += zisk  # záporné — straty neosvobozených

    return {"coins": totals, "celkem": grand}


def write_md(rows: list[dict], rok: int, path: Path) -> None:
    sums = _compute_sums(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# Daňové shrnutí {rok}\n\n")
        f.write("## Sumář per coin\n\n")
        f.write("| Coin | Hrubý příjem CZK | Náklady CZK | Zisk CZK | "
                "Osvobozeno CZK | Zdanitelný zisk CZK |\n")
        f.write("|------|-------------------|-------------|----------|"
                "----------------|--------------------|\n")
        for coin, s in sorted(sums["coins"].items()):
            f.write(f"| {coin} | {s['prijem']:.2f} | {s['naklad']:.2f} | "
                    f"{s['zisk']:.2f} | {s['osvobozeno']:.2f} | {s['zdanitelny']:.2f} |\n")
        c = sums["celkem"]
        f.write(f"| **CELKEM** | **{c['prijem']:.2f}** | **{c['naklad']:.2f}** | "
                f"**{c['zisk']:.2f}** | **{c['osvobozeno']:.2f}** | "
                f"**{c['zdanitelny']:.2f}** |\n\n")
        f.write("## Celkem\n\n")
        f.write(f"- Hrubý příjem: **{c['prijem']:.2f} Kč**\n")
        f.write(f"- Náklady: **{c['naklad']:.2f} Kč**\n")
        f.write(f"- Ekonomický zisk celkem: **{c['zisk']:.2f} Kč**\n")
        f.write(f"  - z toho osvobozeno (časový test 3 roky): {c['osvobozeno']:.2f} Kč\n")
        neosv_netto = c["zdanitelny"] + c["neosv_strata"]
        f.write(f"  - z toho neosvobozené obchody (ekonomicky): {neosv_netto:.2f} Kč"
                f"  _(zisky: +{c['zdanitelny']:.2f}, straty: {c['neosv_strata']:.2f})_\n")
        f.write(f"- **Zdanitelný zisk §10 ZDP: {c['zdanitelny']:.2f} Kč** ← do přiznání\n")
        if c["neosv_strata"] < 0:
            f.write(f"  _(straty {c['neosv_strata']:.2f} Kč z neosvobozených obchodů"
                    f" jsou daňově neodpočitatelné)_\n")
        f.write("\n")
        f.write("## Poznámky\n\n")
        f.write("- Osvobození 100 000 Kč/rok (§4 ZDP) tool **neaplikuje** — zvažte ručně.\n")
        f.write("- Sazba daně (15 % / 23 %) záleží na ostatních příjmech — není v reportu.\n")
        f.write("- Validační report: `build/kontroly.md`\n")


def write_xlsx(rows: list[dict], rok: int, path: Path) -> None:
    try:
        import xlsxwriter
    except ImportError:
        print("WARN: xlsxwriter není nainstalován — přeskakuji XLSX výstup", file=sys.stderr)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(path))
    ws = wb.add_worksheet(f"Report {rok}")

    # Formats
    header_fmt = wb.add_format({"bold": True, "bg_color": "#2F5496", "font_color": "white",
                                 "border": 1})
    exempt_fmt = wb.add_format({"bg_color": "#C6EFCE"})
    taxable_fmt = wb.add_format({"bg_color": "#FFEB9C"})
    loss_fmt = wb.add_format({"font_color": "#9C0006"})
    total_fmt = wb.add_format({"bold": True, "top": 2})
    num_fmt = wb.add_format({"num_format": "#,##0.00"})
    exempt_num = wb.add_format({"bg_color": "#C6EFCE", "num_format": "#,##0.00"})
    taxable_num = wb.add_format({"bg_color": "#FFEB9C", "num_format": "#,##0.00"})
    loss_num = wb.add_format({"font_color": "#9C0006", "num_format": "#,##0.00"})

    headers_cz = {
        "datum_prodeje": "Datum prodeje",
        "coin": "Coin",
        "mnozstvi_z_lotu": "Množství",
        "prijem_czk": "Příjem CZK",
        "naklad_czk": "Náklad CZK",
        "fee_v_nakladu_czk": "Fee v nákladu CZK",
        "zisk_czk": "Zisk CZK",
        "osvobozeno": "Osvobozeno",
        "datum_nakupu_lotu": "Datum nákupu lotu",
        "prodej_id": "ID prodeje",
        "lot_id": "ID lotu",
    }

    for col, key in enumerate(REPORT_HEADER):
        ws.write(0, col, headers_cz.get(key, key), header_fmt)

    num_cols = {"prijem_czk", "naklad_czk", "fee_v_nakladu_czk", "zisk_czk", "mnozstvi_z_lotu"}

    for row_num, r in enumerate(rows, start=1):
        is_exempt = r.get("osvobozeno") == "ano"
        zisk = _dec(r.get("zisk_czk", "0"))
        is_loss = zisk < 0

        for col, key in enumerate(REPORT_HEADER):
            val = r.get(key, "")
            if key in num_cols:
                try:
                    num_val = float(val)
                    fmt = (exempt_num if is_exempt else
                           loss_num if (key == "zisk_czk" and is_loss) else
                           taxable_num if not is_exempt else num_fmt)
                    ws.write_number(row_num, col, num_val, fmt)
                except (ValueError, TypeError):
                    ws.write(row_num, col, val)
            else:
                fmt = exempt_fmt if is_exempt else None
                ws.write(row_num, col, val, fmt)

    # Totals row
    tot_row = len(rows) + 1
    sums = _compute_sums(rows)
    c = sums["celkem"]
    ws.write(tot_row, 0, "CELKEM", total_fmt)
    ws.write_number(tot_row, 3, float(c["prijem"]), total_fmt)
    ws.write_number(tot_row, 4, float(c["naklad"]), total_fmt)
    ws.write_number(tot_row, 6, float(c["zisk"]), total_fmt)

    # Summary below totals: economic breakdown
    info_fmt = wb.add_format({"italic": True, "font_color": "#595959"})
    warn_fmt = wb.add_format({"italic": True, "font_color": "#9C0006"})
    sum_row = tot_row + 2
    ws.write(sum_row,     0, "Osvobozeno (3-letý test):", info_fmt)
    ws.write_number(sum_row, 3, float(c["osvobozeno"]), info_fmt)
    ws.write(sum_row + 1, 0, "Neosvobozené zisky (zdanitelné §10):", info_fmt)
    ws.write_number(sum_row + 1, 3, float(c["zdanitelny"]), info_fmt)
    ws.write(sum_row + 2, 0, "Neosvobozené straty (daňově neodpočitatelné):", warn_fmt)
    ws.write_number(sum_row + 2, 3, float(c["neosv_strata"]), warn_fmt)
    neosv_netto = c["zdanitelny"] + c["neosv_strata"]
    ws.write(sum_row + 3, 0, "Neosvobozené obchody — ekonomicky:", info_fmt)
    ws.write_number(sum_row + 3, 3, float(neosv_netto), info_fmt)

    ws.autofilter(0, 0, len(rows), len(REPORT_HEADER) - 1)
    ws.set_column(0, 0, 14)
    ws.set_column(1, 1, 8)
    ws.set_column(2, 5, 16)
    ws.set_column(6, 6, 16)
    ws.set_column(7, 7, 12)
    ws.set_column(8, 10, 20)

    wb.close()


def run(vstup: Path, rok: int, vystup_csv: Path, vystup_md: Path, vystup_xlsx: Path) -> None:
    parovani = _load_parovani(vstup, rok)
    if not parovani:
        print(f"report {rok}: žádná data pro tento rok", file=sys.stderr)
        # Write empty files so Make targets are satisfied
        write_csv([], vystup_csv)
        write_md([], rok, vystup_md)
        return

    rows = [_build_report_row(p) for p in parovani]
    write_csv(rows, vystup_csv)
    write_md(rows, rok, vystup_md)
    write_xlsx(rows, rok, vystup_xlsx)
    print(f"report {rok}: {len(rows)} řádků → {vystup_csv}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vstup", required=True, type=Path)
    parser.add_argument("--rok", required=True, type=int)
    parser.add_argument("--vystup-csv", required=True, type=Path)
    parser.add_argument("--vystup-md", required=True, type=Path)
    parser.add_argument("--vystup-xlsx", required=True, type=Path)
    args = parser.parse_args()
    run(args.vstup, args.rok, args.vystup_csv, args.vystup_md, args.vystup_xlsx)
