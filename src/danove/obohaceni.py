"""Enrich normalized trades with CZK prices.

Adds: cena_za_kus_czk, celkem_czk, fee_czk, kurz_zdroj
Reads per-rok kurz config from settings.toml.
"""

import argparse
import csv
import sys
import tomllib
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from danove import ceny
from danove.util import coin as coin_util

ENRICHED_HEADER = [
    "id", "burza", "datum_utc", "typ", "coin", "mnozstvi",
    "protistrana_coin", "protistrana_mnozstvi",
    "fee_mnozstvi", "fee_coin",
    "cena_za_kus_czk", "celkem_czk", "fee_czk", "kurz_zdroj",
    "zdroj_radek",
]

QUANT = Decimal("0.00000001")


def _dec(s: str) -> Decimal:
    try:
        return Decimal(str(s).strip())
    except Exception:
        return Decimal("0")


def _load_config(config_path: Path) -> dict:
    with config_path.open("rb") as f:
        return tomllib.load(f)


def _kurz_typ_for_rok(cfg: dict, rok: int) -> str:
    rok_cfg = cfg.get("rok", {}).get(str(rok), {})
    return rok_cfg.get("kurz", cfg.get("obecne", {}).get("vychozi_kurz", "cnb_denni"))


def _enrich_row(row: dict, cfg: dict) -> dict | None:
    datum_str = row.get("datum_utc", "")[:10]
    try:
        d = date.fromisoformat(datum_str)
    except ValueError:
        print(f"WARN: neplatné datum {datum_str!r} v řádku {row.get('id')}", file=sys.stderr)
        return None

    rok = d.year
    kurz_typ = _kurz_typ_for_rok(cfg, rok)
    coin = coin_util.normalizuj(row.get("coin", ""))
    proto_coin = coin_util.normalizuj(row.get("protistrana_coin", ""))
    mnozstvi = _dec(row.get("mnozstvi", "0"))
    proto_mnozstvi = _dec(row.get("protistrana_mnozstvi", "0"))
    fee_mnozstvi = _dec(row.get("fee_mnozstvi", "0"))
    fee_coin_ticker = coin_util.normalizuj(row.get("fee_coin", "") or "")

    kurz_zdroj_parts = []

    # Price of coin in CZK
    if coin_util.je_fiat(proto_coin) and proto_mnozstvi > 0 and mnozstvi > 0:
        # Trade against fiat → derive price from trade itself
        try:
            proto_czk_rate = ceny.ziskej_kurz_cnb(proto_coin, d)
            cena_za_kus_czk = (proto_mnozstvi * proto_czk_rate / mnozstvi).quantize(QUANT)
            celkem_czk = (proto_mnozstvi * proto_czk_rate).quantize(QUANT)
            kurz_zdroj_parts.append(f"cnb:{proto_coin}:{proto_czk_rate:.4f}")
        except ValueError as e:
            print(f"WARN: {e} — řádek {row.get('id')}", file=sys.stderr)
            cena_za_kus_czk = Decimal("0")
            celkem_czk = Decimal("0")
            kurz_zdroj_parts.append("CHYBÍ:cnb")
    else:
        # Crypto-to-crypto or price from external API
        coin_czk = ceny.ziskej_cenu_czk(coin, d)
        if coin_czk is None and proto_coin and not coin_util.je_fiat(proto_coin) and proto_mnozstvi > 0:
            # Fall back: derive CZK value from protistrana price
            proto_czk = ceny.ziskej_cenu_czk(proto_coin, d)
            if proto_czk is not None:
                celkem_czk = (proto_mnozstvi * proto_czk).quantize(QUANT)
                cena_za_kus_czk = (celkem_czk / mnozstvi).quantize(QUANT) if mnozstvi > 0 else Decimal("0")
                kurz_zdroj_parts.append(f"cryptocompare:{proto_coin}:{proto_czk:.4f}(fallback)")
                print(f"WARN: CZK cena pro {coin} na {d} chybí — řádek {row.get('id')} "
                      f"(použita cena {proto_coin} jako záloha)", file=sys.stderr)
            else:
                print(f"WARN: CZK cena pro {coin} ani {proto_coin} na {d} chybí — řádek {row.get('id')} "
                      f"(neznámá/delistovaná mince, zkontrolujte ručně)", file=sys.stderr)
                cena_za_kus_czk = Decimal("0")
                celkem_czk = Decimal("0")
                kurz_zdroj_parts.append(f"CHYBÍ:{coin}")
        elif coin_czk is None:
            print(f"WARN: CZK cena pro {coin} na {d} chybí — řádek {row.get('id')} "
                  f"(neznámá/delistovaná mince, zkontrolujte ručně)", file=sys.stderr)
            cena_za_kus_czk = Decimal("0")
            celkem_czk = Decimal("0")
            kurz_zdroj_parts.append(f"CHYBÍ:{coin}")
        else:
            cena_za_kus_czk = coin_czk.quantize(QUANT)
            celkem_czk = (mnozstvi * cena_za_kus_czk).quantize(QUANT)
            kurz_zdroj_parts.append(f"cryptocompare:{coin}:{coin_czk:.4f}")

    # Fee in CZK
    if fee_mnozstvi > 0 and fee_coin_ticker:
        if fee_coin_ticker == coin:
            fee_czk = (fee_mnozstvi * cena_za_kus_czk).quantize(QUANT)
            kurz_zdroj_parts.append(f"fee={fee_coin_ticker}")
        elif fee_coin_ticker == proto_coin and coin_util.je_fiat(fee_coin_ticker):
            try:
                fee_rate = ceny.ziskej_kurz_cnb(fee_coin_ticker, d)
                fee_czk = (fee_mnozstvi * fee_rate).quantize(QUANT)
            except ValueError:
                fee_czk = Decimal("0")
                print(f"WARN: fee kurz pro {fee_coin_ticker} na {d} chybí", file=sys.stderr)
        else:
            fee_price = ceny.ziskej_cenu_czk(fee_coin_ticker, d)
            if fee_price is None:
                fee_czk = Decimal("0")
                print(f"WARN: fee cena pro {fee_coin_ticker} na {d} chybí", file=sys.stderr)
            else:
                fee_czk = (fee_mnozstvi * fee_price).quantize(QUANT)
                kurz_zdroj_parts.append(f"fee_coingecko:{fee_coin_ticker}")
    else:
        fee_czk = Decimal("0")

    result = dict(row)
    result["coin"] = coin
    result["protistrana_coin"] = proto_coin
    result["cena_za_kus_czk"] = str(cena_za_kus_czk)
    result["celkem_czk"] = str(celkem_czk)
    result["fee_czk"] = str(fee_czk)
    result["kurz_zdroj"] = ",".join(kurz_zdroj_parts)
    return result


def run(vstup: Path, vystup: Path, config_path: Path, cache_path: Path) -> None:
    cfg = _load_config(config_path)
    coin_util.init(config_path.parent)
    ceny.init_db(cache_path)

    rows_in = []
    with vstup.open(encoding="utf-8") as f:
        rows_in = list(csv.DictReader(f))

    rows_out = []
    errors = 0
    for row in rows_in:
        enriched = _enrich_row(row, cfg)
        if enriched is None:
            errors += 1
        else:
            rows_out.append(enriched)

    vystup.parent.mkdir(parents=True, exist_ok=True)
    with vystup.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ENRICHED_HEADER)
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"obohaceni: {len(rows_out)}/{len(rows_in)} řádků ({errors} chyb) → {vystup}",
          file=sys.stderr)
    if errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vstup", required=True, type=Path)
    parser.add_argument("--vystup", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    args = parser.parse_args()
    run(args.vstup, args.vystup, args.config, args.cache)
