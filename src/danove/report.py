"""Generate per-year reports: CSV, Markdown, XLSX."""

import argparse
import csv
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from danove.util.datum import je_osvobozeno

REPORT_HEADER = [
    "datum_prodeje", "coin", "mnozstvi_z_lotu",
    "prijem_czk", "naklad_czk", "fee_v_nakladu_czk", "zisk_czk",
    "osvobozeno", "datum_nakupu_lotu",
    "prodej_id", "lot_id",
]

# §4 odst. 1 písm. zj ZDP — od 1.1.2025: úhrn ročných príjmov z úplatného
# prevodu krypto-aktiv do 100 000 Kč → osvobodené. Cliff (prekročenie o 1 Kč
# stráca celé osvobodenie).
ZJ_OD_ROKU = 2025
ZJ_LIMIT = Decimal("100000")


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
        return {
            "prijem": Decimal("0"), "naklad": Decimal("0"), "zisk": Decimal("0"),
            "osvobozeno": Decimal("0"),
            # §10 ZDP — co jde do Přílohy č. 2 (jen neosvobozené obchody):
            "neosv_prijem": Decimal("0"),        # Σ příjem non-exempt rows
            "neosv_naklad": Decimal("0"),        # Σ náklad non-exempt rows
            # Non-exempt breakdown (gross, informational):
            "neosv_hrube_zisky": Decimal("0"),   # Σ positive non-exempt rows
            "neosv_hrube_straty": Decimal("0"),  # Σ negative non-exempt rows (záporné)
            # Net non-exempt = hrube_zisky + hrube_straty = neosv_prijem - neosv_naklad;
            # §10 ZDP základ = max(0, netto):
        }

    def zdanitelny(s: dict) -> Decimal:
        netto = s["neosv_hrube_zisky"] + s["neosv_hrube_straty"]
        return max(Decimal("0"), netto)

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
        else:
            totals[coin]["neosv_prijem"] += prijem
            totals[coin]["neosv_naklad"] += naklad
            if zisk > 0:
                totals[coin]["neosv_hrube_zisky"] += zisk
            else:
                totals[coin]["neosv_hrube_straty"] += zisk

        grand["prijem"] += prijem
        grand["naklad"] += naklad
        grand["zisk"] += zisk
        if osv:
            grand["osvobozeno"] += zisk
        else:
            grand["neosv_prijem"] += prijem
            grand["neosv_naklad"] += naklad
            if zisk > 0:
                grand["neosv_hrube_zisky"] += zisk
            else:
                grand["neosv_hrube_straty"] += zisk

    return {"coins": totals, "celkem": grand, "_zdanitelny": zdanitelny}


def write_md(rows: list[dict], rok: int, path: Path) -> None:
    sums = _compute_sums(rows)
    zd = sums["_zdanitelny"]
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
                    f"{s['zisk']:.2f} | {s['osvobozeno']:.2f} | {zd(s):.2f} |\n")
        c = sums["celkem"]
        f.write(f"| **CELKEM** | **{c['prijem']:.2f}** | **{c['naklad']:.2f}** | "
                f"**{c['zisk']:.2f}** | **{c['osvobozeno']:.2f}** | "
                f"**{zd(c):.2f}** |\n\n")
        f.write("## Celkem\n\n")
        f.write(f"- Hrubý příjem: **{c['prijem']:.2f} Kč**\n")
        f.write(f"- Náklady: **{c['naklad']:.2f} Kč**\n")
        f.write(f"- Ekonomický zisk celkem: **{c['zisk']:.2f} Kč**\n")
        f.write(f"  - z toho osvobozeno (časový test 3 roky): {c['osvobozeno']:.2f} Kč\n")
        neosv_netto = c["neosv_hrube_zisky"] + c["neosv_hrube_straty"]
        f.write(f"  - z toho neosvobozené (netto): {neosv_netto:.2f} Kč\n")
        f.write(f"- **Zdanitelný zisk §10 ZDP: {zd(c):.2f} Kč** ← §10 dílčí základ\n")
        f.write("\n")

        # ── Do přiznání (§10 ZDP) ─────────────────────────────────────────────
        f.write("## Do přiznání — Příloha č. 2 (§10 ZDP)\n\n")
        f.write("Formulář chce **Příjmy** a **Výdaje** zvlášť (jejich rozdíl je až "
                "dílčí základ daně). Osvobozené obchody (3-letý test §4 ods. 1 písm. zk) "
                "se do §10 **vůbec neuvádějí** — proto jsou níže nižší čísla než "
                "v sumáři per coin nahoře.\n\n")
        f.write("| Položka | CZK |\n")
        f.write("|---------|----:|\n")
        f.write(f"| Příjmy §10 (jen neosvobozené) | **{c['neosv_prijem']:.2f}** |\n")
        f.write(f"| Výdaje §10 (jen neosvobozené) | **{c['neosv_naklad']:.2f}** |\n")
        f.write(f"| Dílčí základ §10 = max(0, Příjmy − Výdaje) | **{zd(c):.2f}** |\n\n")
        if len(sums["coins"]) > 1:
            f.write("### Per coin (rozpis §10)\n\n")
            f.write("| Coin | Příjmy §10 | Výdaje §10 | Základ §10 |\n")
            f.write("|------|-----------:|-----------:|-----------:|\n")
            for coin, s in sorted(sums["coins"].items()):
                if s["neosv_prijem"] == 0 and s["neosv_naklad"] == 0:
                    continue
                f.write(f"| {coin} | {s['neosv_prijem']:.2f} | "
                        f"{s['neosv_naklad']:.2f} | {zd(s):.2f} |\n")
            f.write("\n")

        # ── §4 odst. 1 písm. zj — 100k limit na úhrn príjmov (od 2025) ────────
        zaklad_pred_zj = zd(c)
        if rok >= ZJ_OD_ROKU:
            uhrn = c["prijem"]
            zj_uplatnene = uhrn <= ZJ_LIMIT
            zaklad_po_zj = Decimal("0") if zj_uplatnene else zaklad_pred_zj
            f.write("## §4 odst. 1 písm. zj — limit 100 000 Kč úhrnu příjmů z krypto\n\n")
            f.write("Od 1.1.2025: pokud **úhrn všech příjmů z úplatného převodu "
                    "krypto-aktiv** v roce nepřesáhne 100 000 Kč, **všechny** krypto "
                    "prodeje jsou osvobozené (i bez 3-letého testu). "
                    "Limit je **cliff** — překročení i o 1 Kč ruší osvobození pro celý úhrn.\n\n")
            f.write(f"- Úhrn příjmů z prodeje krypto za rok: **{uhrn:.2f} Kč**\n")
            f.write(f"- Limit §4 zj: **{ZJ_LIMIT:.0f} Kč**\n")
            if zj_uplatnene:
                f.write("- **Status: ✅ osvobozeno** — úhrn pod limitem, "
                        "do §10 přiznání NEZADÁVAT (Příjmy 0, Výdaje 0, Základ 0).\n\n")
            else:
                f.write(f"- **Status: ❌ neuplatní se** — úhrn překračuje "
                        f"limit o {uhrn - ZJ_LIMIT:.2f} Kč. "
                        "Postupujte podle §10 dílčího základu výše.\n\n")
        else:
            zaklad_po_zj = zaklad_pred_zj

        # ── Konečný daňový základ z krypto za rok ────────────────────────────
        f.write(f"## Konečný daňový základ z krypto za {rok}\n\n")
        f.write(f"**{zaklad_po_zj:.2f} Kč** ← toto je číslo, ktoré ide do §10 přiznání.\n\n")
        if rok >= ZJ_OD_ROKU and zaklad_po_zj == 0 and zaklad_pred_zj > 0:
            f.write(f"_(§10 dílčí základ by bol {zaklad_pred_zj:.2f} Kč, "
                    "ale §4 zj ho vynuluje — úhrn príjmov pod limitom 100k.)_\n\n")

        # ── Detail: §10 odst. 4 within-year netting ──────────────────────────
        # Drill-down do toho, ako z jednotlivých (lot ↔ prodej) párov vznikne
        # §10 dílčí základ. Zámerne oddelené od hlavného flow — pre tých, čo
        # chcú vidieť mechaniku nettingu strát so ziskmi v rámci roku.
        hz = c["neosv_hrube_zisky"]
        hs = c["neosv_hrube_straty"]
        zaklad_10 = zd(c)
        usetreno = hz - zaklad_10  # bez nettingu by bol základ = hz
        f.write("## Detail: jak vzniká §10 dílčí základ (within-year netting)\n\n")
        f.write("Mechanika §10 odst. 4 ZDP. Pre vyplnenie priznania nepotrebné — "
                "len pre tých, čo chcú vidieť, ako sa zo ziskov a strát "
                "jednotlivých párov vypočíta dílčí základ.\n\n")
        f.write("**Pojmy:**\n")
        f.write("- **Hrubé zisky** = súčet len kladných ziskov z neosvobozených "
                "(lot ↔ prodej) párov.\n")
        f.write("- **Hrubé straty** = súčet len záporných ziskov (vyjadrené záporne).\n")
        f.write("- **Netto** = ich algebraický súčet. Matematicky totožné "
                "s `Príjmy §10 − Výdaje §10` (rovnaký netto, len rozdelený podľa "
                "znamienka jednotlivých párov).\n")
        f.write("- **Within-year netting (§10 odst. 4 ZDP)** — straty v rámci "
                "roku odpočítavajú zisky toho istého roku, ale do ďalšieho roku "
                "sa neprenášajú.\n\n")
        f.write(f"**Čísla za {rok}:**\n\n")
        f.write("| Položka | CZK |\n")
        f.write("|---------|----:|\n")
        f.write(f"| Hrubé zisky (Σ kladné) | +{hz:.2f} |\n")
        f.write(f"| Hrubé straty (Σ záporné) | {hs:.2f} |\n")
        f.write(f"| Netto = hrubé zisky + hrubé straty | {hz + hs:.2f} |\n")
        f.write(f"| **Dílčí základ §10 = max(0, netto)** | **{zaklad_10:.2f}** |\n")
        f.write(f"| → ušetrené nettingom strát voči ziskom | {usetreno:.2f} |\n\n")
        if hz + hs < 0:
            f.write(f"_Netto je záporný ({hz + hs:.2f} Kč) — celoročná strata "
                    "sa NEodpočítava z iných príjmov, ani sa neprenáša do ďalšieho "
                    "roku. Základ §10 = 0._\n\n")
        elif usetreno > 0:
            f.write(f"_Bez možnosti netovať straty by bol §10 dílčí základ rovný "
                    f"hrubým ziskom ({hz:.2f} Kč). Netting v rámci roku ušetril "
                    f"{usetreno:.2f} Kč na daňovom základe._\n\n")
        phantom_rows = [r for r in rows if r.get("lot_id", "").startswith("phantom:")]
        if phantom_rows:
            f.write("## Nedoložené nákupy\n\n")
            f.write("Tyto prodeje nemají doložený dřívější nákup. Jsou zdaněny "
                    "v plné výši příjmu — nulový náklad, bez nároku na 3-leté "
                    "osvobození.\n\n")
            f.write("**Doporučení:** dohledat nákupní doklad k těmto coinům. "
                    "Doložením se daň sníží — buď se prodej osvobodí (držba 3+ "
                    "roky od data nákupu), nebo se alespoň započítá nákladová "
                    "cena a zdaní se jen skutečný zisk.\n\n")
            f.write("| Datum prodeje | Coin | Množství | Zdaněný příjem CZK |\n")
            f.write("|---------------|------|----------|--------------------|\n")
            for r in phantom_rows:
                f.write(f"| {r['datum_prodeje']} | {r['coin']} | "
                        f"{r['mnozstvi_z_lotu']} | {r['prijem_czk']} |\n")
            f.write("\n")
        f.write("## Poznámky\n\n")
        if rok >= ZJ_OD_ROKU:
            f.write("- Osvobození §4 zj (úhrn ≤ 100 000 Kč) tool **aplikuje automaticky** "
                    "(viz sekce výše). Cliff — překročení = ztráta osvobození pro celý úhrn.\n")
        else:
            f.write(f"- §4 zj (100k limit) se uplatňuje od r. {ZJ_OD_ROKU}; pre {rok} neplatí.\n")
        f.write("- Sazba daně (15 % / 23 %) záleží na ostatních příjmech — není v reportu.\n")
        f.write("- §10 dílčí základ = max(0, netto neosvobozených obchodů) dle §10 odst. 4 ZDP.\n")
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
    phantom_fmt = wb.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"})
    phantom_num = wb.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006",
                                 "num_format": "#,##0.00"})

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
        is_phantom = r.get("lot_id", "").startswith("phantom:")
        zisk = _dec(r.get("zisk_czk", "0"))
        is_loss = zisk < 0

        for col, key in enumerate(REPORT_HEADER):
            val = r.get(key, "")
            if key in num_cols:
                try:
                    num_val = float(val)
                    fmt = (phantom_num if is_phantom else
                           exempt_num if is_exempt else
                           loss_num if (key == "zisk_czk" and is_loss) else
                           taxable_num if not is_exempt else num_fmt)
                    ws.write_number(row_num, col, num_val, fmt)
                except (ValueError, TypeError):
                    ws.write(row_num, col, val)
            else:
                fmt = phantom_fmt if is_phantom else (exempt_fmt if is_exempt else None)
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
    neosv_netto = c["neosv_hrube_zisky"] + c["neosv_hrube_straty"]
    ws.write(sum_row + 1, 0, "Neosvobozené netto:", info_fmt)
    ws.write_number(sum_row + 1, 3, float(neosv_netto), info_fmt)
    zdanitelny_val = float(max(Decimal("0"), neosv_netto))
    bold_warn = wb.add_format({"bold": True, "font_color": "#9C0006"})
    ws.write(sum_row + 2, 0, "Zdanitelný zisk §10 ZDP:", bold_warn)
    ws.write_number(sum_row + 2, 3, zdanitelny_val, bold_warn)

    # Do přiznání — §10 ZDP příjmy/výdaje (Příloha č. 2)
    section_fmt = wb.add_format({"bold": True, "italic": True, "font_color": "#2F5496"})
    ws.write(sum_row + 4, 0, "Do přiznání §10 ZDP (Příloha č. 2):", section_fmt)
    ws.write(sum_row + 5, 0, "  Příjmy §10 (jen neosvobozené):", info_fmt)
    ws.write_number(sum_row + 5, 3, float(c["neosv_prijem"]), info_fmt)
    ws.write(sum_row + 6, 0, "  Výdaje §10 (jen neosvobozené):", info_fmt)
    ws.write_number(sum_row + 6, 3, float(c["neosv_naklad"]), info_fmt)
    ws.write(sum_row + 7, 0, "  Dílčí základ §10 = max(0, P − V):", info_fmt)
    ws.write_number(sum_row + 7, 3, zdanitelny_val, info_fmt)

    # §4 zj (100k limit) + konečný daňový základ
    zaklad_pred_zj = Decimal(str(zdanitelny_val))
    if rok >= ZJ_OD_ROKU:
        uhrn = c["prijem"]
        zj_uplatnene = uhrn <= ZJ_LIMIT
        zaklad_po_zj = Decimal("0") if zj_uplatnene else zaklad_pred_zj
        ws.write(sum_row + 9, 0, "§4 zj limit (úhrn ≤ 100 000 Kč → vše osvobozeno):", section_fmt)
        ws.write(sum_row + 10, 0, f"  Úhrn příjmů z prodeje krypto za {rok}:", info_fmt)
        ws.write_number(sum_row + 10, 3, float(uhrn), info_fmt)
        status_txt = ("  → ✅ osvobozeno (§4 zj)" if zj_uplatnene
                      else f"  → ❌ neuplatní se (překročeno o {uhrn - ZJ_LIMIT:.2f} Kč)")
        ws.write(sum_row + 11, 0, status_txt, info_fmt)
    else:
        zaklad_po_zj = zaklad_pred_zj
        ws.write(sum_row + 9, 0, f"§4 zj — neplatí pre {rok} (od r. {ZJ_OD_ROKU})", info_fmt)

    final_fmt = wb.add_format({"bold": True, "font_color": "white", "bg_color": "#9C0006"})
    ws.write(sum_row + 13, 0, f"KONEČNÝ DAŇOVÝ ZÁKLAD Z KRYPTO ({rok}):", final_fmt)
    ws.write_number(sum_row + 13, 3, float(zaklad_po_zj), final_fmt)

    # Detail: §10 odst. 4 within-year netting (oddelený drill-down)
    netting_row = sum_row + 15
    ws.write(netting_row, 0, "Detail: §10 odst. 4 within-year netting (mechanika):",
             section_fmt)
    ws.write(netting_row + 1, 0, "  Hrubé zisky (Σ kladných):", info_fmt)
    ws.write_number(netting_row + 1, 3, float(c["neosv_hrube_zisky"]), info_fmt)
    ws.write(netting_row + 2, 0, "  Hrubé straty (Σ záporných):", warn_fmt)
    ws.write_number(netting_row + 2, 3, float(c["neosv_hrube_straty"]), warn_fmt)
    ws.write(netting_row + 3, 0, "  Netto = súčet (= Príjmy §10 − Výdaje §10):", info_fmt)
    ws.write_number(netting_row + 3, 3, float(neosv_netto), info_fmt)
    ws.write(netting_row + 4, 0, "  → ušetrené nettingom:", info_fmt)
    ws.write_number(netting_row + 4, 3, float(c["neosv_hrube_zisky"]) - zdanitelny_val,
                    info_fmt)

    phantom_rows = [r for r in rows if r.get("lot_id", "").startswith("phantom:")]
    if phantom_rows:
        phantom_prijem = sum(_dec(r.get("prijem_czk", "0")) for r in phantom_rows)
        ws.write(netting_row + 6, 0, "Nedoložené nákupy (phantom):", bold_warn)
        ws.write_number(netting_row + 6, 3, float(phantom_prijem), bold_warn)
        ws.write(netting_row + 7, 0,
                 "→ doložením nákupního dokladu se daň sníží "
                 "(osvobození 3+ roky nebo započtení nákladu)", warn_fmt)

    ws.autofilter(0, 0, len(rows), len(REPORT_HEADER) - 1)
    ws.set_column(0, 0, 14)
    ws.set_column(1, 1, 8)
    ws.set_column(2, 5, 16)
    ws.set_column(6, 6, 16)
    ws.set_column(7, 7, 12)
    ws.set_column(8, 10, 20)

    wb.close()


def _validate_rows(rows: list[dict], rok: int) -> int:
    """Validates generated report rows; returns error count (writes to stderr)."""
    errors = 0
    for r in rows:
        prijem = _dec(r.get("prijem_czk", "0"))
        naklad = _dec(r.get("naklad_czk", "0"))
        zisk = _dec(r.get("zisk_czk", "0"))
        if abs(prijem - naklad - zisk) > Decimal("0.01"):
            print(
                f"ERR report {rok}: aritmetika lot={r['lot_id']}: "
                f"{prijem} - {naklad} ≠ {zisk}",
                file=sys.stderr,
            )
            errors += 1

        osv_flag = r.get("osvobozeno")
        if osv_flag in ("ano", "ne"):
            nakup = date.fromisoformat(r["datum_nakupu_lotu"][:10])
            prodej = date.fromisoformat(r["datum_prodeje"][:10])
            expected = je_osvobozeno(nakup, prodej)
            actual = osv_flag == "ano"
            if expected != actual:
                spravne = "ano" if expected else "ne"
                print(
                    f"ERR report {rok}: časový test lot={r['lot_id']} "
                    f"nakup={nakup} prodej={prodej}: "
                    f"označeno={osv_flag}, správně={spravne}",
                    file=sys.stderr,
                )
                errors += 1

    return errors


def run(vstup: Path, rok: int, vystup_csv: Path, vystup_md: Path, vystup_xlsx: Path) -> None:
    parovani = _load_parovani(vstup, rok)
    if not parovani:
        print(f"report {rok}: žádná data pro tento rok", file=sys.stderr)
        # Write empty files so Make targets are satisfied
        write_csv([], vystup_csv)
        write_md([], rok, vystup_md)
        return

    rows = [_build_report_row(p) for p in parovani]

    errors = _validate_rows(rows, rok)
    if errors:
        sys.exit(1)

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
