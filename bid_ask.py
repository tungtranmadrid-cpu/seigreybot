"""
Realtime Bid/Ask monitor — hybrid transport:
  Binance  → WebSocket stream  (ping = WS ping/pong frame)
  MEXC     → async HTTP poll   (ping = HTTP round-trip, WS blocked by exchange)
"""

import asyncio
import json
import os
import time
from datetime import datetime

import aiohttp
import websockets

# ANSI colors
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

DISPLAY_INTERVAL  = 0.5   # screen refresh (seconds)
PING_INTERVAL     = 5.0   # re-measure Binance WS ping every N seconds
MEXC_POLL_INTERVAL = 1.0  # MEXC REST poll interval (seconds)

state = {}  # symbol -> latest price dict
ORDER = ["BTCUSDT", "BTCFDUSD", "FDUSDUSDT"]


# ── Binance WebSocket ────────────────────────────────────────────────────────

async def binance_ws_ping(ws) -> float:
    t0 = time.perf_counter()
    pong = await ws.ping()
    await asyncio.wait_for(pong, timeout=5.0)
    return (time.perf_counter() - t0) * 1000


async def binance_ws():
    url = "wss://stream.binance.com:9443/stream?streams=btcfdusd@bookTicker/fdusdusdt@bookTicker"
    async for ws in websockets.connect(url, ping_interval=None):
        try:
            ping_ms   = await binance_ws_ping(ws)
            last_ping = time.perf_counter()
            async for raw in ws:
                now = time.perf_counter()
                if now - last_ping >= PING_INTERVAL:
                    try:
                        ping_ms = await binance_ws_ping(ws)
                    except Exception:
                        pass
                    last_ping = now

                msg = json.loads(raw)
                if "data" not in msg:
                    continue
                d = msg["data"]
                state[d["s"]] = {
                    "exchange": "Binance",
                    "symbol":   d["s"],
                    "bid":      float(d["b"]),
                    "bid_qty":  float(d["B"]),
                    "ask":      float(d["a"]),
                    "ask_qty":  float(d["A"]),
                    "ping_ms":  ping_ms,
                    "transport": "WS",
                }
        except Exception:
            await asyncio.sleep(1)


# ── MEXC async HTTP poll ─────────────────────────────────────────────────────

async def mexc_poll():
    url = "https://api.mexc.com/api/v3/ticker/bookTicker"
    async with aiohttp.ClientSession() as session:
        while True:
            t0 = time.perf_counter()
            try:
                async with session.get(url, params={"symbol": "BTCUSDT"}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    ping_ms = (time.perf_counter() - t0) * 1000
                    data = await resp.json()
                state["BTCUSDT"] = {
                    "exchange":  "MEXC",
                    "symbol":    data["symbol"],
                    "bid":       float(data["bidPrice"]),
                    "bid_qty":   float(data["bidQty"]),
                    "ask":       float(data["askPrice"]),
                    "ask_qty":   float(data["askQty"]),
                    "ping_ms":   ping_ms,
                    "transport": "HTTP",
                }
            except Exception:
                pass
            await asyncio.sleep(MEXC_POLL_INTERVAL)


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
        "ask": 16, "ask_qty": 16, "ping": 12,
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
        f"({ready}/{len(ORDER)} feeds){RESET}\n"
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
        pc        = ping_color(r["ping_ms"])
        transport = f"[{r.get('transport','?')}]"
        ping_str  = f"{r['ping_ms']:.1f} {transport}"
        print(
            f"{r['exchange']:<{col['exchange']}} {r['symbol']:<{col['symbol']}} "
            f"{GREEN}{r['bid']:>{col['bid']}.6f}{RESET} "
            f"{r['bid_qty']:>{col['bid_qty']}.6f} "
            f"{RED}{r['ask']:>{col['ask']}.6f}{RESET} "
            f"{r['ask_qty']:>{col['ask_qty']}.6f} "
            f"{pc}{ping_str:>{col['ping']}}{RESET}"
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
    else:
        missing = needed - state.keys()
        print(f"\n{YELLOW}Alpha: waiting for {missing}...{RESET}")

    print(f"\n{YELLOW}Press Ctrl+C to stop.{RESET}")


async def display_loop():
    while True:
        clear()
        print_table()
        await asyncio.sleep(DISPLAY_INTERVAL)


# ── entry point ───────────────────────────────────────────────────────────────

async def main():
    print("Connecting... Press Ctrl+C to stop.")
    await asyncio.gather(binance_ws(), mexc_poll(), display_loop())


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Stopped.{RESET}")
