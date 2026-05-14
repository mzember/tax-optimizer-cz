# Krypto daňový report (CZ)

Nástroj dostane CSV exporty z krypto burz a vygeneruje podklady k českému daňovému přiznání — přehledný Excel s každým prodejem, odkud byl nákupní lot a kolik z toho podléhá dani.

Podporované burzy: **Coinmate, Bitstamp, Binance, Bittrex, Bitfinex, Electrum, Atomic Wallet**.

---

## Instalace

```bash
pipx install uv      # správce prostředí, pokud ještě není
uv sync              # nainstaluje všechny závislosti
```

## Spuštění

```bash
# Umístěte CSV exporty z burz do raw_input_data/<nazev_burzy>/
make all             # spustí celý pipeline, výsledky v build/
```

Výstupy jsou v `build/`:

| Soubor | Obsah |
|--------|-------|
| `report_2025.xlsx` | Formátovaný Excel — zelené řádky osvobozeny, červené ztráty |
| `report_2025.md` | Souhrn: zdanitelný zisk, osvobozeno, celkový příjem |
| `report_2025.csv` | Strojově čitelný audit trail |
| `kontroly.md` | Varování: nespárované transfery, záporné zůstatky, dust |
| `audit.md` | Ověření integrity párování lotů |

---

## Jak se párují prodeje s nákupy

Každý nákup krypta vytvoří tzv. **lot** — záznam s datem, množstvím a cenou v Kč. Při prodeji musíte rozhodnout, ze kterého lotu (nebo jejich kombinace) prodáváte. Toto rozhodnutí přímo ovlivňuje výši zdanitelného zisku, protože zisk = příjem − náklad lotu.

**Pravidla, která musí platit vždy:**

- Nákup musí být starší než prodej — nelze prodat coin dřív, než byl nakoupen.
- Z jednoho lotu nelze prodat víc, než bylo nakoupeno.
- Jeden prodej může čerpat z více lotů, jeden lot může pokrýt více prodejů.
- Pokud pro prodej neexistuje žádný doložený nákup, použije se „phantom lot" s nulovou cenou — celý příjem se zdaní. Report na to upozorní a doporučí dohledat doklad.

**Tříletý časový test** (§4 odst. 1 písm. b ZDP): lot nakoupený před více než třemi lety je od daně osvobozen — nezáleží, jaký zisk vygeneruje. Nástroj to automaticky pozná a takové řádky označí jako `osvobozeno = ano`.

```
datum_prodeje  příjem_CZK  náklad_CZK  zisk_CZK  osvobozeno  datum_nakupu_lotu
2025-12-17     18 432      4 112        14 320    ano         2019-07-28   ← 6 let držby, nezdaní se
2025-12-17     231         173          58        ne          2024-08-29   ← 1,5 roku, zdaní se
```

Do přiznání vstupuje součet zisků a ztrát z řádků označených `ne` — v rámci jednoho roku se ztráty a zisky vzájemně krátí. Pokud vyjde záporné číslo, základ je nula — roční ztrátu z krypta nelze odečíst od jiných příjmů ani přenést do dalšího roku (§10 odst. 4 ZDP).

### Proč záleží, který lot přiřadíte

Různá přiřazení dávají různý výsledek. Příklad: na účtu jsou dva loty BTC a ve dvou různých letech proběhnou dva prodeje.

```
Loty:    Lot A — nakoupeno za 500 000 Kč
         Lot B — nakoupeno za 1 000 000 Kč

Prodeje: Prodej 1 — za 700 000 Kč  (rok 2024)
         Prodej 2 — za 1 200 000 Kč (rok 2025)
```

**Varianta 1** — nejdražší lot k prvnímu prodeji:

| Rok | Prodej | Lot | Příjem | Náklad | Zisk/ztráta | Zdanitelný základ |
|-----|--------|-----|--------|--------|-------------|-------------------|
| 2024 | Prodej 1 | Lot B (1 000 000) | 700 000 | 1 000 000 | −300 000 | **0** (ztráta propadá) |
| 2025 | Prodej 2 | Lot A (500 000)   | 1 200 000 | 500 000 | +700 000 | **700 000** |

Celkem za obě léta: **700 000 Kč** — ztráta z roku 2024 propadla, nedá se přenést.

**Varianta 2** — levnější lot k prvnímu prodeji:

| Rok | Prodej | Lot | Příjem | Náklad | Zisk/ztráta | Zdanitelný základ |
|-----|--------|-----|--------|--------|-------------|-------------------|
| 2024 | Prodej 1 | Lot A (500 000)   | 700 000 | 500 000 | +200 000 | **200 000** |
| 2025 | Prodej 2 | Lot B (1 000 000) | 1 200 000 | 1 000 000 | +200 000 | **200 000** |

Celkem za obě léta: **400 000 Kč**

Rozdíl 300 000 Kč — ze stejných obchodů, jen jiným přiřazením.

### Jak nástroj hledá nejlepší přiřazení

Ruční procházení všech kombinací je při stovkách lotů a prodejů přes více let nereálné. Nástroj formuluje celý problém jako soustavu nerovnic a předá ji solverovi lineárního programování (knihovna OR-Tools), který systematicky prohledá všechny přípustné kombinace a najde tu s nejnižším celkovým zdanitelným ziskem přes všechna léta dohromady.

Klíčové omezení: roční ztrátu **nelze** přenést do dalšího roku (§10 ZDP). Kdyby nástroj optimalizoval rok po roce nezávisle, mohl by v roce se ztrátou „spotřebovat" drahé loty, které by jinak mohly snížit zisk v roce ziskovém. Optimalizace přes celé portfolio najednou tomuto problému předchází.

---

## Pipeline

```
raw_input_data/<burza>/*.csv
    → [ingest]       build/normalized/<burza>.csv   (sjednocený formát)
    → [konsolidace]  build/vsechny_obchody.csv       (všechny burzy dohromady)
    → [obohacení]    build/obohaceno.csv             (+ CZK ceny z ČNB a CoinGecko)
    → [validace]     build/kontroly.md               (chyby a varování)
    → [LP solver]    build/parovani.csv              (globální přiřazení lot → prodej)
    → [report]       build/report_<rok>.*            (CSV + MD + XLSX)
```

Make sleduje závislosti — přidáte-li nový soubor do `raw_input_data/`, přepočítají se jen ovlivněné kroky.

---

## Konfigurace

| Soubor | Účel |
|--------|------|
| `config/settings.toml` | Typ kurzu CZK per rok (ČNB denní / jednotný roční) |
| `config/zamknute_roky.toml` | Roky již podaných přiznání — LP je nezmění |
| `config/coin_aliases.csv` | Mapování tickerů (XBT→BTC) a CoinGecko ID |
| `config/transferove_mapovani.csv` | Ruční párování withdrawal↔deposit mezi burzami |

---

## Ostatní příkazy

```bash
make test                      # automatizované testy (pytest)
make build/report_2024.csv     # jen jeden rok
make clean                     # smaže build/
```

---

## Omezení

- **Osvobození 100 000 Kč/rok** (§4 ZDP): tool čísla vypočítá, uplatnění je na uživateli
- **Sazba daně** (15 % / 23 %): závisí na ostatních příjmech, tool ji nepočítá
- **Staking, mining, airdropy**: logují se, ale nevstupují do lotů pro optimalizaci
- **Cross-exchange transfery**: heuristické párování; přesné přiřazení lze upřesnit v `transferove_mapovani.csv`
