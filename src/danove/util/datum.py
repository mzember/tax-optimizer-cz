"""Date utilities: 3-year tax exemption test, ČNB weekend rollback."""

from datetime import date, timedelta


def je_osvobozeno(datum_nakupu: date, datum_prodeje: date) -> bool:
    """True if disposal is more than 3 calendar years after acquisition (§4 ZDP)."""
    try:
        hranice = datum_nakupu.replace(year=datum_nakupu.year + 3)
    except ValueError:
        # 29 Feb edge case → use 28 Feb
        hranice = datum_nakupu.replace(year=datum_nakupu.year + 3, day=28)
    return datum_prodeje > hranice


def predchozi_pracovni_den(d: date) -> date:
    """Return d if weekday, else last Friday (ČNB doesn't publish on weekends/holidays)."""
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d -= timedelta(days=1)
    return d


def parse_date_czk(s: str) -> date:
    """Parse 'DD.MM.YYYY' as used in ČNB URLs."""
    d, m, y = s.split(".")
    return date(int(y), int(m), int(d))


def format_date_cnb(d: date) -> str:
    """Format date as 'DD.MM.YYYY' for ČNB API."""
    return d.strftime("%d.%m.%Y")


def format_date_coingecko(d: date) -> str:
    """Format date as 'DD-MM-YYYY' for CoinGecko API."""
    return d.strftime("%d-%m-%Y")
