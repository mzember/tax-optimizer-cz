"""Helper script to create a UTF-16 Bittrex fixture file."""
from pathlib import Path

content = (
    "OrderUuid,Exchange,Type,Quantity,Limit,CommissionPaid,Price,Opened,Closed\n"
    "aaaa-1111,BTC-LTC,LIMIT_BUY,10.0,0.005,0.0001,0.05,1/1/2020 10:00:00 AM,1/1/2020 10:01:00 AM\n"
    "bbbb-2222,USDT-BTC,LIMIT_SELL,0.5,9000,4.5,4495.5,6/1/2023 09:00:00 AM,6/1/2023 09:01:00 AM\n"
)

out = Path(__file__).parent / "ukazka_bittrex.csv"
out.write_bytes(b"\xff\xfe" + content.encode("utf-16-le"))
print(f"Created {out}")

if __name__ == "__main__":
    pass
