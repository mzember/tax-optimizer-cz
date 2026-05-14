# Daňové přiznání z krypto obchodů (CZ)

Nástroj pro generování audit-trail reportu k českému daňovému přiznání z krypto obchodů (§10 ZDP).
Zpracuje historii transakcí z více burz a globálně minimalizuje zdanitelný zisk přes všechny roky
pomocí lineárního programování (OR-Tools GLOP).

> Vzniklo jako utility pro konkrétního uživatele; zveřejněno pro případné další zájemce.

## Instalace

```bash
pipx install uv        # pokud uv ještě není
uv sync --group dev    # nainstaluje vše vč. pytest
```

## Spuštění

```bash
make all               # celý pipeline → reporty pro všechny roky
make build/report_2024.csv   # jen 2024
make test              # automatizované testy
make clean             # smaže build/
```

## Pipeline

```
raw_input_data/<burza>/*.csv
    → [ingest]        build/normalized/<burza>.csv
    → [konsolidace]   build/vsechny_obchody.csv + vsechny_transfery.csv
    → [obohaceni]     build/obohaceno.csv  (přidá CZK ceny z ČNB + CoinGecko)
    → [validace]      build/kontroly.md   (chyby + varování)
    → [optimalizace]  build/parovani.csv  (globální LP přes všechny roky)
    → [report]        build/report_<rok>.csv + .md + .xlsx
```

Make sleduje mtime — přidáte-li nový soubor do `raw_input_data/`, přepočítají se jen závislé kroky.

## Konfigurace

- `config/settings.toml` — kurz CZK (ČNB denní / jednotný) per rok
- `config/zamknute_roky.toml` — roky již podaných přiznání (LP je nezměni)
- `config/coin_aliases.csv` — mapování tickerů (XBT→BTC) a CoinGecko ID
- `config/transferove_mapovani.csv` — ruční párování withdrawal↔deposit mezi burzami

## Jak to funguje — párování lotů

Místo per-rok greedy HIFO řeší **globální lineární program** přes celé datové portfolio:
- Proměnné `x[lot, prodej]` = kolik jednotek lotu se použije pro daný prodej
- Loty >3 roky (§4 ZDP časový test) jsou osvobozeny — LP je použije přednostně
- Zdanitelný zisk = součet `max(0, roční_zisk)` přes všechny roky
- Ztráta v jednom roce NELZE přenést do dalšího (§10 ZDP ostatní příjmy)
- LP najde globální optimum, kde HIFO by laciné loty ztratil ve ztrátovém roce

## Výstupy

`build/report_<rok>.xlsx` — formátovaný Excel:
- Zelené řádky = osvobozeno (časový test 3 roky)
- Červené hodnoty = záporný zisk
- Sumární řádek dole

`build/report_<rok>.md` — shrnutí: zdanitelný zisk, osvobozeno, celkový příjem

`build/report_<rok>.csv` — strojově čitelný audit trail (jeden řádek per disposal-lot pár)

## Výslovné omezení V1

- Staking/mining/airdrops: loguje se, ale nevstupuje do lotů
- Osvobození 100 000 Kč/rok (§4 ZDP): vypočtena čísla, uplatnění na uživateli
- Tax sazba (15 % / 23 %): tool počítá zdanitelný zisk, ne daň
- Cross-exchange transfer matching: heuristický, manuální override přes config
