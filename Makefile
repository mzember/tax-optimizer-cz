PYTHON := uv run python
ROKY := 2017 2018 2019 2020 2021 2022 2023 2024 2025 2026

# Note: we depend on the directory, not individual files, to avoid Make's
# inability to handle spaces in filenames (e.g. "2018btc bittrex fullOrders.csv").
# If you ADD a new raw CSV, the directory mtime updates → rebuild triggers.
# If you MODIFY an existing raw CSV, run: make clean && make all

.PHONY: all clean test sync report-vse

all: report-vse

sync:
	uv sync

build/normalized/binance.csv: raw_input_data/binance src/danove/ingest/binance.py
	@mkdir -p build/normalized
	$(PYTHON) -m danove.ingest.binance --vstup raw_input_data/binance --vystup $@

build/normalized/bitstamp.csv: raw_input_data/bitstamp src/danove/ingest/bitstamp.py
	@mkdir -p build/normalized
	$(PYTHON) -m danove.ingest.bitstamp --vstup raw_input_data/bitstamp --vystup $@

build/normalized/bittrex.csv: raw_input_data/bittrex src/danove/ingest/bittrex.py
	@mkdir -p build/normalized
	$(PYTHON) -m danove.ingest.bittrex --vstup raw_input_data/bittrex --vystup $@

build/normalized/coinmate.csv: raw_input_data/coinmate src/danove/ingest/coinmate.py
	@mkdir -p build/normalized
	$(PYTHON) -m danove.ingest.coinmate --vstup raw_input_data/coinmate --vystup $@

build/normalized/bitfinex.csv: raw_input_data/bitfinex src/danove/ingest/bitfinex.py
	@mkdir -p build/normalized
	$(PYTHON) -m danove.ingest.bitfinex --vstup raw_input_data/bitfinex --vystup $@

build/normalized/electrum.csv: raw_input_data/electrum src/danove/ingest/electrum.py
	@mkdir -p build/normalized
	$(PYTHON) -m danove.ingest.electrum --vstup raw_input_data/electrum --vystup $@ \
		--jako-nakupy electrum-history-projekt.csv electrum-history_receiving_from_m.csv

build/normalized/atomic.csv: raw_input_data/atomic src/danove/ingest/atomic.py
	@mkdir -p build/normalized
	$(PYTHON) -m danove.ingest.atomic --vstup raw_input_data/atomic --vystup $@

build/normalized/paperwallet.csv: raw_input_data/paperwallet src/danove/ingest/electrum.py
	@mkdir -p build/normalized
	$(PYTHON) -m danove.ingest.electrum --vstup raw_input_data/paperwallet --vystup $@ \
		--jako-nakupy paperwallet-btc-2015.csv

NORMALIZED := build/normalized/binance.csv build/normalized/bitstamp.csv \
              build/normalized/bittrex.csv build/normalized/coinmate.csv \
              build/normalized/bitfinex.csv build/normalized/electrum.csv \
              build/normalized/atomic.csv build/normalized/paperwallet.csv

build/vsechny_obchody.csv build/vsechny_transfery.csv: $(NORMALIZED) src/danove/konsolidace.py config/vyloucene_transakce.txt
	$(PYTHON) -m danove.konsolidace \
		--vstup build/normalized \
		--vystup-obchody build/vsechny_obchody.csv \
		--vystup-transfery build/vsechny_transfery.csv \
		--vyloucene config/vyloucene_transakce.txt

build/obohaceno.csv: build/vsechny_obchody.csv src/danove/obohaceni.py config/settings.toml
	$(PYTHON) -m danove.obohaceni \
		--vstup $< \
		--vystup $@ \
		--config config/settings.toml \
		--cache build/cache.duckdb

build/kontroly.md: build/obohaceno.csv build/vsechny_transfery.csv src/danove/validace.py
	$(PYTHON) -m danove.validace \
		--obchody build/obohaceno.csv \
		--transfery build/vsechny_transfery.csv \
		--vystup $@ \
		--mapovani config/transferove_mapovani.csv

build/parovani.csv: build/obohaceno.csv build/kontroly.md \
                    src/danove/optimalizace.py config/zamknute_roky.toml
	$(PYTHON) -m danove.optimalizace \
		--vstup build/obohaceno.csv \
		--vystup $@ \
		--config config/settings.toml \
		--lock config/zamknute_roky.toml \
		--od-roku 2023

build/audit.md: build/parovani.csv build/obohaceno.csv src/danove/audit.py
	$(PYTHON) -m danove.audit \
		--parovani build/parovani.csv \
		--obchody build/obohaceno.csv \
		--vystup $@ \
		--config config/settings.toml \
		--od-roku 2023

define REPORT_RULE
build/report_$(1).csv build/report_$(1).md build/report_$(1).xlsx: \
    build/parovani.csv src/danove/report.py
	$(PYTHON) -m danove.report \
		--vstup build/parovani.csv \
		--rok $(1) \
		--vystup-csv build/report_$(1).csv \
		--vystup-md build/report_$(1).md \
		--vystup-xlsx build/report_$(1).xlsx
endef

$(foreach rok,$(ROKY),$(eval $(call REPORT_RULE,$(rok))))

report-vse: build/audit.md $(foreach rok,$(ROKY),build/report_$(rok).csv)

test:
	uv run pytest -v

clean:
	rm -rf build/
