import requests
import time
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime


ENDPOINTS = [
    {
        "exchange": "MEXC",
        "symbol": "BTCUSDT",
        "url": "https://api.mexc.com/api/v3/ticker/bookTicker",
        "params": {"symbol": "BTCUSDT"},
    },
    {
        "exchange": "Binance",
        "symbol": "BTCFDUSD",
        "url": "https://api.binance.com/api/v3/ticker/bookTicker",
        "params": {"symbol": "BTCFDUSD"},
    },
    {
        "exchange": "Binance",
        "symbol": "FDUSDUSDT",
        "url": "https://api.binance.com/api/v3/ticker/bookTicker",
        "params": {"symbol": "FDUSDUSDT"},
    },
]

# ANSI color codes
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

REFRESH_INTERVAL = 1  # seconds


def fetch_bid_ask(endpoint: dict) -> dict:
    t0 = time.perf_counter()
    try:
        response = requests.get(endpoint["url"], params=endpoint["params"], timeout=10)
        ping_ms = (time.perf_counter() - t0) * 1000
        response.raise_for_status()
        data = response.json()
        return {
            "exchange": endpoint["exchange"],
            "symbol":   data["symbol"],
            "bid":      float(data["bidPrice"]),
            "bid_qty":  float(data["bidQty"]),
            "ask":      float(data["askPrice"]),
            "ask_qty":  float(data["askQty"]),
            "ping_ms":  ping_ms,
            "error":    None,
        }
    except Exception as e:
        return {
            "exchange": endpoint["exchange"],
            "symbol":   endpoint["symbol"],
            "error":    str(e),
            "ping_ms":  (time.perf_counter() - t0) * 1000,
        }


def ping_color(ms: float) -> str:
    if ms < 300:
        return GREEN
    if ms < 700:
        return YELLOW
    return RED


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def print_table(results: list, elapsed: float):
    col = {
        "exchange": 10,
        "symbol":   12,
        "bid":      16,
        "bid_qty":  16,
        "ask":      16,
        "ask_qty":  16,
        "ping":     10,
    }

    header = (
        f"{BOLD}{CYAN}"
        f"{'Exchange':<{col['exchange']}} "
        f"{'Symbol':<{col['symbol']}} "
        f"{'Bid':>{col['bid']}} "
        f"{'Bid Qty':>{col['bid_qty']}} "
        f"{'Ask':>{col['ask']}} "
        f"{'Ask Qty':>{col['ask_qty']}} "
        f"{'Ping (ms)':>{col['ping']}}"
        f"{RESET}"
    )
    sep = "-" * (col["exchange"] + col["symbol"] + col["bid"] + col["bid_qty"] +
                 col["ask"] + col["ask_qty"] + col["ping"] + 6)

    print(f"{BOLD}Realtime Bid/Ask  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  "
          f"(refresh: {REFRESH_INTERVAL}s  |  fetch: {elapsed*1000:.0f}ms){RESET}\n")
    print(header)
    print(sep)

    for r in results:
        if r["error"]:
            pc = RED + f"{'ERROR':>{col['ping']}}" + RESET
            print(
                f"{r['exchange']:<{col['exchange']}} "
                f"{r['symbol']:<{col['symbol']}} "
                f"{RED}{'—':>{col['bid']}} {'—':>{col['bid_qty']}} "
                f"{'—':>{col['ask']}} {'—':>{col['ask_qty']}}{RESET} {pc}"
            )
            print(f"  {RED}↳ {r['error']}{RESET}")
        else:
            pc = ping_color(r["ping_ms"])
            print(
                f"{r['exchange']:<{col['exchange']}} "
                f"{r['symbol']:<{col['symbol']}} "
                f"{GREEN}{r['bid']:>{col['bid']}.6f}{RESET} "
                f"{r['bid_qty']:>{col['bid_qty']}.6f} "
                f"{RED}{r['ask']:>{col['ask']}.6f}{RESET} "
                f"{r['ask_qty']:>{col['ask_qty']}.6f} "
                f"{pc}{r['ping_ms']:>{col['ping']}.1f}{RESET}"
            )

    # Alpha = (BTCFDUSD ask * FDUSDUSDT bid) / BTCUSDT bid
    by_symbol = {r["symbol"]: r for r in results if not r.get("error")}
    needed = {"BTCFDUSD", "FDUSDUSDT", "BTCUSDT"}
    if needed.issubset(by_symbol):
        btcfdusd_ask  = by_symbol["BTCFDUSD"]["ask"]
        fdusdusdt_bid = by_symbol["FDUSDUSDT"]["bid"]
        btcusdt_bid   = by_symbol["BTCUSDT"]["bid"]
        alpha = (btcfdusd_ask * fdusdusdt_bid) / btcusdt_bid
        alpha_color = GREEN if alpha > 1 else RED
        ab = (alpha - 1) * 100
        ab_color = GREEN if ab > 0 else RED
        print(f"\n{BOLD}Alpha = (BTCFDUSD ask × FDUSDUSDT bid) / BTCUSDT bid{RESET}")
        print(f"      = ({btcfdusd_ask:.6f} × {fdusdusdt_bid:.6f}) / {btcusdt_bid:.6f}")
        print(f"      = {alpha_color}{BOLD}{alpha:.8f}{RESET}")
        print(f"   Ab = (Alpha - 1) × 100%  =  {ab_color}{BOLD}{ab:+.6f}%{RESET}")
    else:
        missing = needed - by_symbol.keys()
        print(f"\n{RED}Alpha: cannot compute — missing data for {missing}{RESET}")

    print(f"\n{YELLOW}Press Ctrl+C to stop.{RESET}")


def main():
    print("Starting realtime feed... Press Ctrl+C to stop.")
    time.sleep(0.5)

    with ThreadPoolExecutor(max_workers=len(ENDPOINTS)) as pool:
        try:
            while True:
                t0 = time.perf_counter()
                results = list(pool.map(fetch_bid_ask, ENDPOINTS))
                elapsed = time.perf_counter() - t0

                clear()
                print_table(results, elapsed)

                time.sleep(max(0, REFRESH_INTERVAL - elapsed))
        except KeyboardInterrupt:
            print(f"\n{YELLOW}Stopped.{RESET}")


if __name__ == "__main__":
    main()
