import asyncio
from dataclasses import dataclass
from time import sleep, time
from pybit.unified_trading import WebSocket

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from influxdb_client.domain.write_precision import WritePrecision

# Your Bybit API credentials
API_KEY = "your_api_key"
API_SECRET = "your_api_secret"

# Grafana InfluxDB settings
INFLUXDB_HOST = "localhost"
INFLUXDB_PORT = 8086
INFLUXDB_URL = f"http://{INFLUXDB_HOST}:{INFLUXDB_PORT}"
INFLUXDB_DB = "crypto_spreads"
INFLUXDB_BUCKET = "my-bucket"
# INFLUXDB_TOKEN = "1rzq4hs0F5LikWsdRvNQgx0_ZkpavY-UN2YavaA8xN8oM2hh_59WJYX5dWtvQmOxzDdCqeiEZqDy1g4vDQuSOw=="

influx_client = InfluxDBClient(
    url=INFLUXDB_URL, username="user", password="password", org="my-org"
)
write_api = influx_client.write_api(write_options=SYNCHRONOUS)


@dataclass
class BBA:
    bbp: float
    bbs: float
    bap: float
    bas: float
    timestamp: float

    def midprice(self):
        return (self.bap + self.bbp) / 2


ORDERBOOK: dict[str, BBA] = dict()
FUT_SYMBOLS = ["BTC-29MAR24", "BTC-24NOV23"]
SPOT_SYMBOLS = ["BTCUSDC", "BTCUSDT"]


# Function to process and log data to InfluxDB
async def log_to_influxdb(timestamp, spot_symbol, fut_symbol, spread):
    json_body = [
        {
            "measurement": "crypto_spreads",
            "tags": {"spot_symbol": spot_symbol, "fut_symbol": fut_symbol},
            "time": timestamp,
            "fields": {"spread": spread},
        }
    ]
    p = (
        Point("sp")
        .tag("spot", spot_symbol)
        .tag("fut", fut_symbol)
        .field("spread", spread)
        .time(timestamp, write_precision=WritePrecision.MS)
    )

    write_api.write(bucket=INFLUXDB_BUCKET, record=p)


def handle_orderbook(msg):
    data = msg["data"]
    symbol = data["s"]
    bids, asks = data["b"], data["a"]
    (best_bid_price, best_bid_size), (best_ask_price, best_ask_size) = bids[0], asks[0]
    ORDERBOOK[symbol] = BBA(
        float(best_bid_price),
        float(best_bid_size),
        float(best_ask_price),
        float(best_ask_size),
        float(msg["ts"]),
    )


def get_spreads(
    orderbook: dict[str, BBA], spot_symbols: list[str], fut_symbols: list[str]
) -> list[tuple[str, str, float]]:
    spreads = []
    for spot in spot_symbols:
        if spot not in orderbook:
            continue
        for fut in fut_symbols:
            if fut not in orderbook:
                continue
            spot_midprice = orderbook[spot].midprice()
            fut_midprice = orderbook[fut].midprice()

            if fut_midprice > spot_midprice:
                spread = fut_midprice / spot_midprice - 1
                spreads.append((spot, fut, spread))

    return spreads


def print_spreads(*args, **kwargs):
    if len(args) == 1:
        spreads = args[0]
    elif "spreads" in kwargs:
        spreads = kwargs["spreads"]
    else:
        spreads = get_spreads(*args, **kwargs)
    for spot, fut, spread in spreads:
        print(f"{spot.ljust(15)}\t{fut.ljust(15)}", f"{spread*100:.2f}%")


if __name__ == "__main__":
    fut_ws = WebSocket(channel_type="linear", testnet=False)
    fut_ws.orderbook_stream(1, FUT_SYMBOLS, handle_orderbook)

    spot_ws = WebSocket(channel_type="spot", testnet=False)
    spot_ws.orderbook_stream(1, SPOT_SYMBOLS, handle_orderbook)

    event_loop = asyncio.get_event_loop()

    while True:
        sleep(1)
        print("--" * 10)
        spreads = get_spreads(ORDERBOOK, SPOT_SYMBOLS, FUT_SYMBOLS)
        print_spreads(spreads)

        now = int(time() * 1000)
        event_loop.run_until_complete(
            asyncio.gather(
                *(
                    log_to_influxdb(now, spot_symbol, fut_symbol, spread)
                    for spot_symbol, fut_symbol, spread in spreads
                )
            )
        )
