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

NORMALIZED := build/normalized/binance.csv build/normalized/bitstamp.csv \
              build/normalized/bittrex.csv build/normalized/coinmate.csv

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

report-vse: $(foreach rok,$(ROKY),build/report_$(rok).csv)

test:
	uv run pytest -v

clean:
	rm -rf build/
