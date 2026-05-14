# Specifikace v2: Daňové přiznání z krypto obchodů (CZ) — multi-year LP

## Context

Uživatel potřebuje audit-trail nástroj pro české daňové přiznání z krypto obchodů, který **globálně minimalizuje daň přes všechny roky** (nikoliv jen per-rok greedy HIFO). Vstupem jsou CSV exporty z více burz (Bitstamp, Bittrex, Coinmate, Binance), výstupem rozpadnutý audit-trail report per rok ukazující, jak byl každý prodej spárován s nákupními loty.

**Důvod přepracování v2:** v1 navrhoval per-rok HIFO. To je lokálně optimální pro daný rok, ale globálně suboptimální napříč lety — laciné loty „vyšetřené" ve ztrátovém roce 2024 nemohou snížit zisk v ziskovém 2025, protože ztráty u §10 ZDP se nepřevádějí. Lineární program přes celý dataset je výrazně lepší.

## Pevně zafixovaná rozhodnutí

| # | Téma                            | Hodnota                                                                 |
|---|---------------------------------|-------------------------------------------------------------------------|
| 1 | Stack                           | **uv + Polars + DuckDB + OR-Tools (GLOP) + pytest + xlsxwriter**         |
| 2 | Monetary type                   | `polars.Decimal(38, 8)` + Python `Decimal` na hraně se solverem (LP koeficienty jako float, výsledné množství zpět na Decimal) |
| 3 | Kurz CZK                        | Obě varianty (ČNB denní + jednotný roční), per-rok config                |
| 4 | Optimalizace                    | **Globální LP přes všechny roky**, objektiv = Σ_y max(0, zdanitelný_zisk_y) |
| 5 | Lock-in již podaných let        | `config/zamknute_roky.toml` přidá hard constraints — historické párování se nezmění |
| 6 | Sazba daně                      | Tool ji nepočítá; vrací zdanitelný zisk per rok                          |
| 7 | Scope non-trade                 | Loguje se, ale neovlivňuje optimalizaci. Heuristika withdraw↔deposit běží v validaci |
| 8 | 100 000 Kč osvobození           | Tool ignoruje, jen poznámka v reportu                                    |
| 9 | Build                           | GNU Makefile (každý stupeň = `uv run python -m danove.<modul>`)          |
| 10| Fee                             | Při nákupu → zvyšuje cost basis lotu; při prodeji → snižuje proceeds; fee v krypto přepočet přes CoinGecko |
| 11| Cena pro crypto↔crypto          | CoinGecko historical (cache v DuckDB), USD→CZK přes ČNB                  |
| 12| Bittrex 2018 UTF-16             | Auto-detekce BOM v ingest                                                |
| 13| Výstup                          | CSV + MD + **XLSX** (formátovaný, highlight osvobozených řádků)          |
| 14| Validace                        | Plný balíček: balance, sale-before-acq, missing price, dup, stablecoin, dust, unmatched transfer, fee bez ceny |

## Adresářová struktura

```
danove-priznanie-coin/
├── Makefile
├── pyproject.toml                 (uv-spravované; deps: polars, duckdb, ortools, pytest, xlsxwriter)
├── uv.lock
├── README.md                      (česky, jak spustit)
├── config/
│   ├── settings.toml              (per-rok: kurz_typ, mimo-krypto-přijmy pro daň brackets — nepoužitý ve V1)
│   ├── zamknute_roky.toml         (které roky lock-in)
│   ├── transferove_mapovani.csv   (manuální withdrawal↔deposit páry, override heuristiky)
│   └── coin_aliases.csv           (XBT→BTC, BCC→BCH, USD↔USDT klasifikace)
├── raw_input_data/
│   ├── binance/  bitstamp/  bittrex/  coinmate/
├── build/                         (gitignored)
│   ├── normalized/<burza>.csv
│   ├── vsechny_obchody.csv
│   ├── vsechny_transfery.csv
│   ├── obohaceno.csv
│   ├── parovani.csv               (jeden soubor pro všechny roky, vystoupí z LP)
│   ├── kontroly.md                (validační report, globální)
│   ├── report_<rok>.csv
│   ├── report_<rok>.md
│   ├── report_<rok>.xlsx
│   └── cache.duckdb               (kurzy ČNB + CoinGecko, perzistentní)
├── src/danove/
│   ├── __init__.py
│   ├── ingest/
│   │   ├── binance.py             (agregace 3-row Sell/Buy/Fee clusteru)
│   │   ├── bitstamp.py
│   │   ├── bittrex.py             (UTF-16 + UTF-8, 3 různé hlavičky)
│   │   └── coinmate.py            (semicolon, české sloupce)
│   ├── konsolidace.py
│   ├── ceny.py                    (ČNB + CoinGecko, cache do duckdb)
│   ├── obohaceni.py               (přidá CZK ceny ke každému tradu)
│   ├── validace.py                (všech 8 kontrol)
│   ├── optimalizace.py            (OR-Tools GLOP LP)
│   ├── report.py                  (CSV + MD + XLSX)
│   └── util/
│       ├── datum.py               (3-roky test + ČNB weekend rollback)
│       └── coin.py                (alias normalizace)
└── testy/
    ├── conftest.py
    ├── fixtures/
    │   ├── ukazka_*.csv
    │   ├── ocekavany_report_*.csv
    │   └── lp_scenare/            (golden LP problémy)
    ├── test_ingest_*.py
    ├── test_ceny.py               (mock HTTP, cache hit)
    ├── test_validace.py
    ├── test_optimalizace.py       (golden: HIFO vs LP rozdíl prokazatelný)
    └── test_e2e.py
```

## Datový model — normalized formát

Hlavička (česky, srozumitelně; `fee` ponecháno jako anglické dle CLAUDE.md):

```
id, burza, datum_utc, typ, coin, mnozstvi, protistrana_coin, protistrana_mnozstvi, fee_mnozstvi, fee_coin, zdroj_radek
```

| sloupec                  | popis                                                                                 |
|--------------------------|---------------------------------------------------------------------------------------|
| `id`                     | `<burza>:<original_id_or_hash>` — stabilní pro deduplikaci                            |
| `burza`                  | binance/bitstamp/bittrex/coinmate                                                     |
| `datum_utc`              | `YYYY-MM-DDTHH:MM:SSZ`                                                                |
| `typ`                    | `NAKUP` / `PRODEJ` / `WITHDRAWAL` / `DEPOSIT` (poslední dvě → `vsechny_transfery.csv`)|
| `coin`                   | alias-normalizovaný ticker                                                            |
| `mnozstvi`               | `Decimal(38,8)`, kladné                                                               |
| `protistrana_coin`       | EUR/USD/CZK/BTC/LTC/…                                                                 |
| `protistrana_mnozstvi`   | `Decimal(38,8)`, kladné                                                               |
| `fee_mnozstvi`, `fee_coin` | volitelné                                                                           |
| `zdroj_radek`            | originální řádek pro audit                                                            |

Pravidlo `typ`: z pohledu primárního `coin`. NAKUP znamená získání `coin` výměnou za `protistrana_coin`. Crypto↔crypto trade (BTC/LTC) se zapíše jako 1 řádek typu NAKUP/PRODEJ s `coin=BTC` (nebo opačně); enrich rozloží na 2 události se stejnou CZK hodnotou pro účely LP.

## Enriched formát (přidá CZK ceny)

```
... normalized sloupce ..., cena_za_kus_czk, celkem_czk, fee_czk, kurz_zdroj
```

Logika:
1. `protistrana_coin ∈ {CZK, EUR, USD, GBP, ...}` (fiat) → ČNB denní/jednotný (per-rok config) → `cena_za_kus_czk = protistrana_mnozstvi * cnb_rate / mnozstvi`
2. `protistrana_coin` je krypto (BTC/LTC/USDT/…) → CoinGecko historical pro `coin` v USD → ČNB USD/CZK → `cena_za_kus_czk`
3. Fee: stejná logika; pokud `fee_coin` je krypto, CoinGecko; pokud chybí cena → warn + fee=0 (zaznamenané v `kurz_zdroj`)

DuckDB hraje roli "join enginu":
```sql
SELECT t.*,
       k.kurz_czk AS protistrana_kurz_czk,
       (t.protistrana_mnozstvi * k.kurz_czk / t.mnozstvi) AS cena_za_kus_czk
FROM read_csv_auto('build/vsechny_obchody.csv') t
LEFT JOIN kurzy k
  ON k.coin = t.protistrana_coin AND k.datum = date_trunc('day', t.datum_utc)
```

## LP formulace — `optimalizace.py`

### Vstup
- Loty L (z NAKUPů): `lot_i = (id_i, coin_i, datum_nakupu_i, mnozstvi_i, cena_czk_per_unit_i, fee_czk_total_i)`
- Prodeje S (z PRODEJů): `sale_j = (id_j, coin_j, datum_prodeje_j, mnozstvi_j, prijem_czk_per_unit_j, fee_czk_total_j, rok_j)`

### Kompatibilní páry
`P = {(i,j) : coin_i == coin_j AND datum_nakupu_i ≤ datum_prodeje_j}`

Konstanta per pár:
- `osvobozeno_ij = 1` pokud `datum_prodeje_j - datum_nakupu_i > 3 roky` (přesný kalendářní výpočet, viz `util/datum.py`)
- `gain_per_unit_ij = prijem_czk_per_unit_j - cena_czk_per_unit_i` (fee se alokuje proporcionálně níže)

### Proměnné
- `x_{i,j} ≥ 0` (Decimal jednotek lotu i alokovaných k prodeji j) — **continuous**
- `tax_base_y ≥ 0` per rok (positive part of yearly gain)

### Omezení
```
(1) ∀i:   Σ_{(i,j)∈P} x_{i,j} ≤ mnozstvi_i                  (lot nelze přečerpat)
(2) ∀j:   Σ_{(i,j)∈P} x_{i,j} = mnozstvi_j                  (prodej musí být plně pokrytý)
(3) ∀y:   gain_y = Σ_{j: rok_j=y} [
              − fee_czk_total_j
              + Σ_{i: (i,j)∈P, osvobozeno_ij=0} x_{i,j} · gain_per_unit_ij
              − Σ_{i: (i,j)∈P, osvobozeno_ij=0} (x_{i,j}/mnozstvi_i) · fee_czk_total_i
          ]
(4) ∀y:   tax_base_y ≥ gain_y                                (positive part)
(5) Lock-in (volitelně z config/zamknute_roky.toml):
          x_{i,j} = predchozi_hodnota_ij  pro páry kde rok_j ∈ zamknute_roky
```

### Objektivní funkce
```
minimize  Σ_y tax_base_y
```

Pokud `gain_y ≤ 0` v některém roce, LP automaticky položí `tax_base_y = 0` (a ten rok do součtu nepřispěje — ztráta se „zahodí", což přesně odpovídá CZ pravidlu pro §10 ostatní příjmy).

### Solver
OR-Tools **GLOP** (čistá LP, žádné integers). Edge case: pokud uživatel později přidá brackety (15/23 %), přepneme na **PDLP** nebo **SCIP** s indikator constraints — modul to musí umět přepnout, ale ve V1 jen GLOP.

### Tie-breaking
LP může mít více optimálních řešení (stejná hodnota objektu). Pro stabilitu/predikovatelnost přidáme **sekundární objekt**:
- Po vyřešení primárního LP vezmi `tax_base_y*` a přidej equality constraint pro každý rok
- Pak minimalizuj druhý objekt: `Σ_{(i,j)∈P} (datum_prodeje_j - datum_nakupu_i).days · x_{i,j}` (preferuj brzké páry — FIFO-like tie-break)
- Tím zaručíme, že re-runy nad nezměněnými daty produkují stejný výstup

### Pre-grouping pro výkonnost
Loty se shodným `(coin, datum_nakupu, cena_czk_per_unit)` se sečtou do super-lotu. Sale se shodným `(coin, datum_prodeje, prijem_czk_per_unit)` rovněž. Snížení velikosti |L|×|S| typicky 5-10×.

## Výstupní soubory

### `build/parovani.csv` (interní detail, jeden soubor pro vše)
```
prodej_id, lot_id, coin, datum_prodeje, datum_nakupu, mnozstvi_pouzite,
prijem_czk, naklad_czk, zisk_czk, osvobozeno, rok_prodeje
```

### `build/report_<rok>.csv` (audit trail per rok)
```
datum_prodeje, burza_prodej, coin, mnozstvi_z_lotu,
prijem_czk, naklad_czk, fee_prodej_czk, fee_nakup_czk, zisk_czk,
osvobozeno, datum_nakupu_lotu, burza_nakup, cena_lotu_za_kus_czk,
kurz_zdroj_prodej, kurz_zdroj_nakup, prodej_id, lot_id
```

Sloupec `osvobozeno` má hodnotu `ano` (časový test 3 roky) nebo `ne`.

### `build/report_<rok>.md`
```markdown
# Daňové shrnutí <rok>

## Sumář per coin
| Coin | Prodáno (jednotek) | Hrubý příjem CZK | Náklady CZK | Zisk CZK | Osvobozeno CZK | Zdanitelný zisk CZK |
|------|---------------------|-------------------|--------------|----------|----------------|---------------------|

## Celkem
- Hrubý příjem: …
- Náklady: …
- **Zdanitelný zisk: … Kč** ← do § 10 ZDP přiznání
- Z toho osvobozeno (časový test 3 roky): …

## Použité parametry
- Kurz CZK: ČNB denní | jednotný (per config)
- Strategie párování: globální LP optimalizace
- Lock-in roky: …

## Poznámky
- Osvobození 100 000 Kč/rok (§4 ZDP) tool neaplikuje — zvažte ručně.
- Sazba daně (15 % / 23 %) není v reportu — záleží na ostatních příjmech.
- Validační report: build/kontroly.md (X warningů, Y errors).
```

### `build/report_<rok>.xlsx`
Stejné sloupce jako CSV + xlsxwriter formátování:
- Řádky kde `osvobozeno=ano` → světle zelený background
- `zisk_czk < 0` → červený text
- Sumární řádek dole bold + tlustá horní hranice
- Autofilter

## Validace — `validace.py` (výstup `build/kontroly.md`)

Spouští se jako vlastní stupeň před optimalizací. Pokud najde `ERROR`, pipeline padne; `WARN` jen reportuje.

| Kontrola | Severity | Pravidlo |
|----------|----------|---------|
| Záporný zůstatek coinu | ERROR | Pro každý coin spočti running balance v chronologii. Pokud kdykoliv <0 → ERROR s časovým razítkem a chybějícím množstvím |
| Sale před první acquisition | ERROR | Speciální případ výše; samostatná diagnostika |
| Chybějící CZK cena | ERROR | Pokud ČNB ani CoinGecko nevrátí pro datum → ERROR (uživatel doplní cache ručně nebo upraví config) |
| Duplicitní transakce | WARN | DuckDB `GROUP BY (burza, id) HAVING count(*) > 1`; nebo soft duplicity: `(burza, datum_utc, coin, mnozstvi, protistrana_mnozstvi)` |
| Nesparovany withdraw/deposit | WARN | Heuristika: pro každý WITHDRAW najdi DEPOSIT na jiné burze: stejný coin, |mnozstvi_w - mnozstvi_d| ≤ max(fee, 0.001), 0 < (datum_d - datum_w) ≤ 24h. Nesparované → výpis. `config/transferove_mapovani.csv` overrides. |
| Fee v coinu bez ceny | WARN | `fee_coin` nemá CZK kurz pro datum → fee=0 a varování |
| Stablecoin nejasná klasifikace | WARN | Trade s `coin` v {USDT, USDC, BUSD, DAI, …} proti USD/EUR s kurzem v [0.99, 1.01] → varování „technicky disposal v CZ, zvažte správné období" |
| Dust balances | WARN | Na konci timeline pro každý coin: pokud `zustatek > 0` a `zustatek * posledni_cena_czk < 100 Kč` → výpis (možná zapomenutý prodej / poplatková drobnost) |

## Makefile

```makefile
UV := uv run
PYTHON := $(UV) python
RAW_DIRS := binance bitstamp bittrex coinmate
NORMALIZED := $(addprefix build/normalized/,$(addsuffix .csv,$(RAW_DIRS)))
ROKY := 2018 2019 2020 2021 2022 2023 2024 2025 2026

.PHONY: all clean test report-vse

all: report-vse

build/normalized/%.csv: raw_input_data/% src/danove/ingest/%.py
	@mkdir -p build/normalized
	$(PYTHON) -m danove.ingest.$* --vstup $< --vystup $@

build/vsechny_obchody.csv build/vsechny_transfery.csv: $(NORMALIZED) src/danove/konsolidace.py
	$(PYTHON) -m danove.konsolidace --vstup build/normalized --vystup-obchody build/vsechny_obchody.csv --vystup-transfery build/vsechny_transfery.csv

build/obohaceno.csv: build/vsechny_obchody.csv src/danove/obohaceni.py config/settings.toml
	$(PYTHON) -m danove.obohaceni --vstup $< --vystup $@ --config config/settings.toml --cache build/cache.duckdb

build/kontroly.md: build/obohaceno.csv build/vsechny_transfery.csv src/danove/validace.py
	$(PYTHON) -m danove.validace --obchody $< --transfery build/vsechny_transfery.csv --vystup $@

build/parovani.csv: build/obohaceno.csv build/kontroly.md src/danove/optimalizace.py config/zamknute_roky.toml
	$(PYTHON) -m danove.optimalizace --vstup $< --vystup $@ --lock config/zamknute_roky.toml

build/report_%.csv build/report_%.md build/report_%.xlsx: build/parovani.csv src/danove/report.py
	$(PYTHON) -m danove.report --vstup $< --rok $* --vystup-csv build/report_$*.csv --vystup-md build/report_$*.md --vystup-xlsx build/report_$*.xlsx

report-vse: $(foreach r,$(ROKY),build/report_$(r).csv)

test:
	$(UV) pytest -v

clean:
	rm -rf build/
```

Spuštění:
- `make` → vše
- `make build/report_2024.csv` → jen jeden rok
- `make test` → testy
- `touch raw_input_data/binance/*.csv && make` → incremental rebuild

## Config soubory

### `config/settings.toml`
```toml
[obecne]
vychozi_kurz = "cnb_denni"

[rok.2018]
kurz = "cnb_jednotny"

[rok.2024]
kurz = "cnb_denni"
```

### `config/zamknute_roky.toml`
```toml
# Roky které už byly podány v daňovém přiznání.
# Optimalizace pro tyto roky fixuje x_{i,j} na hodnoty z předchozího běhu uloženého v build/parovani_zamcene.csv.
zamknute = [2018, 2019, 2020, 2021, 2022]
zamcene_parovani = "build/parovani_zamcene.csv"
```

### `config/transferove_mapovani.csv`
```csv
withdraw_id,deposit_id,poznamka
bittrex:abc123,coinmate:def456,manual_potvrzeno
```

### `config/coin_aliases.csv`
```csv
alias,kanonicky_ticker,coingecko_id,klasifikace
XBT,BTC,bitcoin,crypto
BCC,BCH,bitcoin-cash,crypto
USDT,USDT,tether,stablecoin
USDC,USDC,usd-coin,stablecoin
USD,USD,,fiat
EUR,EUR,,fiat
CZK,CZK,,fiat
```

## Externí API

### ČNB denní
- `https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt?date=DD.MM.YYYY`
- Žádný API key. Víkend/svátek = ČNB sám vrátí poslední platný.
- Cache: `cache.duckdb` tabulka `kurzy_cnb(mena, datum, mnozstvi, kurz_czk, retrieved_at)`.

### ČNB jednotný kurz (§38 ZDP)
- Publikuje ČNB pokyn D-… každý leden. Není scrape-friendly → manuální `config/jednotny_kurz_<rok>.csv` (`mena,kurz_czk`).

### CoinGecko historical
- `https://api.coingecko.com/api/v3/coins/{coingecko_id}/history?date={DD-MM-YYYY}&localization=false`
- Free tier ~10-30 req/min. `time.sleep(2.5)` mezi requesty. Cache permanentně (historie se nemění).
- Tabulka `kurzy_coingecko(coingecko_id, datum, cena_usd, cena_eur, retrieved_at)`.

## Testy (pytest)

`testy/conftest.py` má fixture `tmp_cache_duckdb` a `mock_cnb`, `mock_coingecko` (`pytest_httpserver` nebo ručně přes `urllib.request` monkey-patch — nechci pip dep navíc, použiju vlastní mock vrstvu v `src/danove/util/http.py` s `inject_mock_response()`).

### Klíčové scénáře

**S1 — Assignmentový scénář (round numbers):**
- 2020-01-01: nákup 2 BTC za 200 000 CZK (100 000 Kč/BTC)
- 2023-06-01: prodej 1 BTC za LTC při ceně 300 000 Kč/BTC → 3y test splněn → osvobozeno
- 2024-06-01: prodej 2. BTC za LTC při ceně 300 000 Kč/BTC → osvobozeno
- prodej LTC za CZK → zisk z LTC odpovídající
- ověř: report_2023, report_2024 ukazují osvobozeno=ano pro BTC disposaly

**S2 — LP poráží HIFO:**
- 2020-06-01: nákup 1 BTC @ 1 000 000 Kč
- 2020-06-02: nákup 1 BTC @ 500 000 Kč  
- 2023-12-15: prodej 1 BTC @ 800 000 Kč (3y test pro tyto loty nesplněn → 2023-12-15 < 2023-06-01+3roky)
- 2024-07-01: prodej 1 BTC @ 2 000 000 Kč (3y test pro 2020-06-02 splněn, pro 2020-06-01 splněn)
- HIFO výsledek: 2023 použije 1M lot → ztráta 200k (zahozená), 2024 použije 0.5M lot → zisk osvobozený 1.5M (osvobozeno). Daň = 0. 
- Naivní FIFO: 2023 použije 1M lot → ztráta 200k, 2024 použije 0.5M lot → zisk 1.5M (osvobozeno) — totéž.
- LP: stejné. 
- Lepší test: kdy LP volí strategie jinak — scénář kde jeden z prodejů spadá DO 3y testu pro jeden lot a MIMO pro druhý. Solver pak musí volit, který lot „obětovat" pro zdaňovaný prodej.

**S2 (revidovaný) — LP optimalizuje napříč ztrátovým a ziskovým rokem:**
- 2022-01-01: nákup 1 BTC @ 1 000 000 Kč
- 2022-01-02: nákup 1 BTC @ 500 000 Kč
- 2024-06-01: prodej 1 BTC @ 700 000 Kč (2024 ziskový kdyby použil 500k lot, ztrátový kdyby 1M lot; 3y test nesplněn ani jednou)
- 2025-06-01: prodej 1 BTC @ 1 200 000 Kč (3y test nesplněn ani jednou — leden 2025 < 2022-01-02+3roky=2025-01-02; tedy 2025-06-01 > 2025-01-02 → SPLNĚN)
- 2024 použije 1M lot → ztráta 300k (zahozená); 2025 použije 500k lot → osvobozeno
- HIFO 2024: použije 1M → ztráta 300k zahozená; 2025 použije 500k → osvobozeno. Tax = 0. OK
- Co kdyby 2025 byl jen 3y-mínus-1den (datum 2025-01-01)? Pak 2025 nesplní → zisk 700k zdanitelný. LP by raději v 2024 použil 500k (zisk 200k zdanitelný), v 2025 1M (zisk 200k zdanitelný) → celkem 400k zdanitelných. HIFO by udělal 2024: 1M → ztráta 300k zahozená; 2025: 500k → zisk 700k zdanitelných. Celkem 700k zdanitelných. **LP ušetří 300k** ← golden test musí toto prokázat.

**S3** — UTF-16 Bittrex parsing.
**S4** — Binance multi-row trade aggregace.
**S5** — Coinmate semicolon + české hlavičky.
**S6** — Lock-in: spusť LP, ulož jako zamcene, přidej nový rok, spusť znovu, ověř že historické páry se nezměnily.
**S7** — Cache CoinGecko hit: druhý běh nedělá HTTP.
**S8** — Záporný balance detekce.
**S9** — Stablecoin warning.
**S10** — Dust report.

## Implementační poznámky pro downstream LLM

- **Decimal hygienia**: Polars `Decimal(38,8)` všude v dataframes. OR-Tools přijímá float64 koeficienty → konverze přes `float(Decimal)` v okamžiku stavby LP, ALE výsledky `x_{i,j}` zpět na `Decimal` před zápisem do CSV (kvantizace na 8 desetinných míst).
- **3-rok kalendářní test**: `util/datum.py:je_osvobozeno(datum_nakupu, datum_prodeje) -> bool`. Implementace: `datum_prodeje > datum_nakupu.replace(year=datum_nakupu.year + 3)`. Edge case 29.2 → ošetři přes try/except + replace(day=28).
- **DuckDB v processu**: `duckdb.connect('build/cache.duckdb')`. Read CSV: `SELECT * FROM read_csv_auto(?, header=true)`. Vyhneme se intermediate pandas/polars konverzím tam, kde DuckDB SQL stačí.
- **CoinGecko ID mapping**: nutné mapovat `coin → coingecko_id` přes `config/coin_aliases.csv` (BTC→bitcoin, LTC→litecoin, ETH→ethereum, …).
- **Bittrex multi-format ingest**: `ingest/bittrex.py` musí mít routing: detekuj hlavičku v prvním řádku (po UTF normalizaci) a zvol jeden ze 3 parserů.
- **Modulární CLI**: každý modul má `if __name__ == '__main__': ...` s argparse. Sdílené flagy `--vstup`, `--vystup`. Konzistence názvů.
- **LP tie-break**: jak popsáno, sekundární objektiv jako lineární penalty na věk lot-pairu. Vyhne se nedeterministickým výstupům.

## Otevřené body (vědomě V1 nedělá)

- Tax brackety 15/23 % (vyžaduje další uživatelský config + MIP s indikátory)
- 100 000 Kč osvobození (jen poznámka)
- Mining/staking/airdrop/fork (jen log v `vsechny_transfery.csv`)
- PDF příloha
- Crypto-to-fiat fee disposal optimalizace přes různé burzy (fee má vlastní cost-basis implikace pokud fee_coin = BNB s vlastní historií)

## Verifikace

```bash
uv sync                                          # nainstaluje deps
make test                                        # 10+ scénářů
make                                             # full pipeline všech roků
uv run python -m danove.optimalizace --vstup build/obohaceno.csv --dry-run --vysvetli  # vypiše LP problém
diff <(sort build/report_2024.csv) <(sort testy/fixtures/ocekavany_report_2024.csv)
touch raw_input_data/binance/*.csv && make      # incremental
libreoffice --calc build/report_2024.xlsx       # vizuální review
```

## Kritické soubory pro implementaci (priorita)

1. `src/danove/optimalizace.py` — LP formulace (nejhodnotnější část)
2. `src/danove/ingest/binance.py` — 3-row cluster aggregace, nejtěžší ingest
3. `src/danove/ingest/bittrex.py` — UTF-16 + 3 formáty
4. `src/danove/ceny.py` — externí API + cache (HTTP retry, rate limit)
5. `src/danove/validace.py` — 8 kontrol
6. `src/danove/report.py` — XLSX formátování

## Další kroky po schválení

1. Uložit do memory dvě poučení z této session:
   - **feedback** memory: stdlib-only constraint byl arbitrární; když je daný úkol vhodný pro knihovny (OR-Tools pro LP, DuckDB pro SQL nad CSV, Polars pro Decimal-safe DataFrame, pytest, xlsxwriter), použij je. uv řeší env management.
   - **feedback** memory: u finančních/daňových algoritmů uvažuj globálně optimálně přes celý horizont (multi-year LP/MIP), ne lokálně greedy per-rok. Lokální HIFO/FIFO ignoruje, že ztráty v jednom roce nesnižují daň v jiném roce u §10 ZDP.
2. Implementaci spustit shora: `pyproject.toml` + `uv sync`, pak `util/datum.py` + `util/coin.py`, pak ingest moduly s testy, pak konsolidace/obohacení, pak validace, pak `optimalizace.py` (golden test S2 musí prokázat výhodu LP vs HIFO), pak report.
3. Před implementací potvrdit u uživatele: má LLM-implementer pokračovat v této session, nebo má spec sloužit jako vstup pro samostatný běh (v jiné session/repu)?
