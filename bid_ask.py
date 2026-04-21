"""
Realtime Bid/Ask monitor — optimized async HTTP:
  - Auto-benchmark Binance endpoints at startup, pick the fastest
  - Batch both Binance symbols in one request (saves 1 round-trip)
  - Tuned TCP connector: keepalive, DNS cache, nodelay
"""

import asyncio
import os
import time
from datetime import datetime

import aiohttp

# ANSI colors
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

DISPLAY_INTERVAL = 0.5
POLL_INTERVAL    = 1.0

# Round-trip fees (%). 0% for maker on Binance BTCFDUSD/FDUSDUSDT (promo)
# and on MEXC BTCUSDT with maker. Bump up if you expect taker fills.
FEES_PCT = 0.0

BINANCE_HOSTS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
]
BINANCE_PATH = "/api/v3/ticker/bookTicker"

state        = {}
ORDER        = ["BTCUSDT", "BTCFDUSD", "FDUSDUSDT"]
best_host    = BINANCE_HOSTS[0]   # updated after benchmark


# ── TCP connector (tuned) ────────────────────────────────────────────────────

def make_connector() -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(
        ttl_dns_cache=300,       # cache DNS 5 min
        use_dns_cache=True,
        keepalive_timeout=30,    # keep TCP socket warm
        enable_cleanup_closed=True,
        force_close=False,
    )


# ── Binance host benchmark ───────────────────────────────────────────────────

async def benchmark_binance(session: aiohttp.ClientSession) -> str:
    print(f"Benchmarking {len(BINANCE_HOSTS)} Binance endpoints...")
    url_path = BINANCE_PATH + "?symbol=BTCFDUSD"

    async def probe(host: str):
        try:
            t0 = time.perf_counter()
            async with session.get(
                host + url_path, timeout=aiohttp.ClientTimeout(total=3)
            ) as r:
                await r.read()
            return (time.perf_counter() - t0) * 1000, host
        except Exception:
            return float("inf"), host

    results = await asyncio.gather(*(probe(h) for h in BINANCE_HOSTS))
    results.sort()
    for ms, host in results:
        tag = " ← best" if host == results[0][1] else ""
        label = "timeout" if ms == float("inf") else f"{ms:.1f}ms"
        print(f"  {host:<35}  {label}{tag}")
    return results[0][1]


# ── pollers ──────────────────────────────────────────────────────────────────

async def poll_binance(session: aiohttp.ClientSession):
    """Fetches BTCFDUSD + FDUSDUSDT in one batch request."""
    # Pass symbols directly in URL — aiohttp would URL-encode brackets otherwise
    url     = best_host + BINANCE_PATH + '?symbols=["BTCFDUSD","FDUSDUSDT"]'
    timeout = aiohttp.ClientTimeout(total=5)
    while True:
        t0 = time.perf_counter()
        try:
            async with session.get(url, timeout=timeout) as resp:
                ping_ms = (time.perf_counter() - t0) * 1000
                rows = await resp.json()
            if isinstance(rows, list):
                for d in rows:
                    state[d["symbol"]] = {
                        "exchange": "Binance",
                        "symbol":   d["symbol"],
                        "bid":      float(d["bidPrice"]),
                        "bid_qty":  float(d["bidQty"]),
                        "ask":      float(d["askPrice"]),
                        "ask_qty":  float(d["askQty"]),
                        "ping_ms":  ping_ms,
                    }
        except Exception:
            pass
        await asyncio.sleep(POLL_INTERVAL)


async def poll_mexc(session: aiohttp.ClientSession):
    url     = "https://api.mexc.com/api/v3/ticker/bookTicker"
    timeout = aiohttp.ClientTimeout(total=5)
    while True:
        t0 = time.perf_counter()
        try:
            async with session.get(url, params={"symbol": "BTCUSDT"}, timeout=timeout) as resp:
                ping_ms = (time.perf_counter() - t0) * 1000
                data = await resp.json()
            state["BTCUSDT"] = {
                "exchange": "MEXC",
                "symbol":   data["symbol"],
                "bid":      float(data["bidPrice"]),
                "bid_qty":  float(data["bidQty"]),
                "ask":      float(data["askPrice"]),
                "ask_qty":  float(data["askQty"]),
                "ping_ms":  ping_ms,
            }
        except Exception:
            pass
        await asyncio.sleep(POLL_INTERVAL)


async def start_pollers():
    global best_host
    async with aiohttp.ClientSession(connector=make_connector()) as session:
        best_host = await benchmark_binance(session)
        print(f"\nUsing {best_host}\n")
        await asyncio.sleep(0.5)
        await asyncio.gather(poll_binance(session), poll_mexc(session))


# ── display ──────────────────────────────────────────────────────────────────

def ping_color(ms: float) -> str:
    if ms < 100:
        return GREEN
    if ms < 300:
        return YELLOW
    return RED


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def print_table():
    col = {
        "exchange": 10, "symbol": 12, "bid": 16, "bid_qty": 16,
        "ask": 16, "ask_qty": 16, "ping": 10,
    }
    sep_len = sum(col.values()) + len(col) - 1

    header = (
        f"{BOLD}{CYAN}"
        f"{'Exchange':<{col['exchange']}} {'Symbol':<{col['symbol']}} "
        f"{'Bid':>{col['bid']}} {'Bid Qty':>{col['bid_qty']}} "
        f"{'Ask':>{col['ask']}} {'Ask Qty':>{col['ask_qty']}} "
        f"{'Ping (ms)':>{col['ping']}}{RESET}"
    )

    ready = sum(1 for s in ORDER if s in state)
    print(
        f"{BOLD}Realtime Bid/Ask  —  "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  "
        f"({ready}/{len(ORDER)} feeds  |  {best_host}){RESET}\n"
    )
    print(header)
    print("-" * sep_len)

    for sym in ORDER:
        r = state.get(sym)
        if not r:
            print(
                f"{'':>{col['exchange']}} {sym:<{col['symbol']}} "
                f"{YELLOW}{'connecting...':>{col['bid']}}{RESET}"
            )
            continue
        pc = ping_color(r["ping_ms"])
        print(
            f"{r['exchange']:<{col['exchange']}} {r['symbol']:<{col['symbol']}} "
            f"{GREEN}{r['bid']:>{col['bid']}.6f}{RESET} "
            f"{r['bid_qty']:>{col['bid_qty']}.6f} "
            f"{RED}{r['ask']:>{col['ask']}.6f}{RESET} "
            f"{r['ask_qty']:>{col['ask_qty']}.6f} "
            f"{pc}{r['ping_ms']:>{col['ping']}.1f}{RESET}"
        )

    # Alpha / Ab
    needed = {"BTCFDUSD", "FDUSDUSDT", "BTCUSDT"}
    if needed.issubset(state):
        btcfdusd_ask  = state["BTCFDUSD"]["ask"]
        fdusdusdt_bid = state["FDUSDUSDT"]["bid"]
        btcusdt_bid   = state["BTCUSDT"]["bid"]
        alpha = (btcfdusd_ask * fdusdusdt_bid) / btcusdt_bid
        ab    = (alpha - 1) * 100
        print(f"\n{BOLD}Alpha = (BTCFDUSD ask × FDUSDUSDT bid) / BTCUSDT bid{RESET}")
        print(f"      = ({btcfdusd_ask:.6f} × {fdusdusdt_bid:.6f}) / {btcusdt_bid:.6f}")
        print(f"      = {(GREEN if alpha > 1 else RED)}{BOLD}{alpha:.8f}{RESET}")
        print(f"   Ab = (Alpha - 1) × 100%  =  {(GREEN if ab > 0 else RED)}{BOLD}{ab:+.6f}%{RESET}")

        # Beta / Bb
        fdusdusdt_ask = state["FDUSDUSDT"]["ask"]
        btcfdusd_bid  = state["BTCFDUSD"]["bid"]
        btcusdt_ask   = state["BTCUSDT"]["ask"]
        beta = (fdusdusdt_ask * btcfdusd_bid) / btcusdt_ask
        bb   = (beta - 1) * 100
        print(f"\n{BOLD}Beta  = (FDUSDUSDT ask × BTCFDUSD bid) / BTCUSDT ask{RESET}")
        print(f"      = ({fdusdusdt_ask:.6f} × {btcfdusd_bid:.6f}) / {btcusdt_ask:.6f}")
        print(f"      = {(GREEN if beta > 1 else RED)}{BOLD}{beta:.8f}{RESET}")
        print(f"   Bb = (Beta  - 1) × 100%  =  {(GREEN if bb > 0 else RED)}{BOLD}{bb:+.6f}%{RESET}")

        # ── Trading signal matrix (Ab × Bb sign) ─────────────────────────
        if ab > 0 and bb > 0:
            scenario = "Binance PREMIUM  (++)"
            action   = "SELL BTC on Binance (via FDUSD)  →  BUY on MEXC"
            edge_pct = min(ab, bb)              # conservative: smaller leg
        elif ab < 0 and bb < 0:
            scenario = "Binance DISCOUNT (--)"
            action   = "BUY BTC on Binance (via FDUSD)   →  SELL on MEXC"
            edge_pct = min(abs(ab), abs(bb))
        elif ab > 0 and bb < 0:
            scenario = "WIDE SPREAD      (+-)"
            action   = "WAIT — no-arb zone, spread too wide to cross"
            edge_pct = 0.0
        else:  # ab < 0, bb > 0
            scenario = "TWO-SIDED        (-+)"
            action   = "CHECK DATA — rare cross-spread window"
            edge_pct = min(abs(ab), bb)

        net_pct = edge_pct - FEES_PCT
        if net_pct > FEES_PCT:
            verdict, vcolor = "STRONG",       GREEN
        elif net_pct > 0:
            verdict, vcolor = "marginal",     YELLOW
        else:
            verdict, vcolor = "below noise",  RED

        scen_color = GREEN if edge_pct > 0 else YELLOW

        print(f"\n{BOLD}── Trading Signal ─────────────────────────────────────────────{RESET}")
        print(f"  State:    {scen_color}{BOLD}{scenario}{RESET}")
        print(f"  Action:   {BOLD}{action}{RESET}")
        print(f"  Edge:     {edge_pct:.6f}%  gross   |   "
              f"{vcolor}{net_pct:+.6f}%{RESET} net  (fees est. {FEES_PCT}%)")
        print(f"  Verdict:  {vcolor}{BOLD}{verdict}{RESET}")
    else:
        missing = needed - state.keys()
        print(f"\n{YELLOW}Alpha/Beta: waiting for {missing}...{RESET}")

    print(f"\n{YELLOW}Press Ctrl+C to stop.{RESET}")


async def display_loop():
    while True:
        clear()
        print_table()
        await asyncio.sleep(DISPLAY_INTERVAL)


# ── entry point ───────────────────────────────────────────────────────────────

async def main():
    await asyncio.gather(start_pollers(), display_loop())


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Stopped.{RESET}")
