"""
collect_real_data.py

Polls DexScreener's free, public, no-API-key API for a token's REAL price on
two different Solana DEXs (default: Raydium and Orca), and logs it to a CSV
in the exact format backtest.py expects: timestamp, price_dex_a, price_dex_b.

DexScreener endpoint used:
    https://api.dexscreener.com/latest/dex/tokens/<TOKEN_ADDRESS>

This is a POLLING collector, not a historical-data downloader: real free
historical per-DEX data is hard to get, so instead this builds your own
real dataset by checking prices repeatedly over time. Run it for a few
hours (or a day) in the background, then feed the resulting CSV into
backtest.py.

Usage (defaults to SOL token, comparing Raydium vs Orca):
    python3 collect_real_data.py --minutes 120 --interval 5

Leave it running in a terminal window while you do other things. Press
Ctrl+C any time to stop early -- whatever was collected is already saved.
"""

import argparse
import csv
import time
import urllib.request
import json
import os
from datetime import datetime, timezone

DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"

# Native SOL token address -- a good, liquid default to test with.
DEFAULT_TOKEN_ADDRESS = "So11111111111111111111111111111111111111112"


def fetch_pairs(token_address: str):
    url = DEXSCREENER_URL.format(address=token_address)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    return data.get("pairs") or []


def best_price_for_dex(pairs, dex_id: str, quote_symbol: str = "USDC"):
    """
    Among all pairs on the given DEX, pick the one with the highest liquidity
    (most reliable price) quoted against the given stablecoin.
    Returns (price_usd, liquidity_usd) or (None, None) if not found.
    """
    candidates = [
        p for p in pairs
        if p.get("dexId", "").lower() == dex_id.lower()
        and p.get("quoteToken", {}).get("symbol", "").upper() == quote_symbol.upper()
        and p.get("priceUsd") is not None
    ]
    if not candidates:
        return None, None

    def liquidity(p):
        try:
            return float(p.get("liquidity", {}).get("usd") or 0)
        except (TypeError, ValueError):
            return 0

    best = max(candidates, key=liquidity)
    try:
        return float(best["priceUsd"]), liquidity(best)
    except (TypeError, ValueError):
        return None, None


def collect(token_address, dex_a, dex_b, quote_symbol, interval_sec, total_minutes, out_path):
    end_time = time.time() + total_minutes * 60
    file_exists = os.path.exists(out_path)

    with open(out_path, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "price_dex_a", "price_dex_b"])

        n_rows = 0
        n_errors = 0
        print(f"Collecting {dex_a} vs {dex_b} prices for token {token_address}")
        print(f"Polling every {interval_sec}s for {total_minutes} minutes. Ctrl+C to stop early.")
        print(f"Writing to {out_path}\n")

        while time.time() < end_time:
            try:
                pairs = fetch_pairs(token_address)
                price_a, liq_a = best_price_for_dex(pairs, dex_a, quote_symbol)
                price_b, liq_b = best_price_for_dex(pairs, dex_b, quote_symbol)

                if price_a is not None and price_b is not None:
                    ts = datetime.now(timezone.utc).isoformat()
                    writer.writerow([ts, price_a, price_b])
                    f.flush()
                    n_rows += 1
                    print(f"{ts}  {dex_a}=${price_a:.6f} (liq ${liq_a:,.0f})  "
                          f"{dex_b}=${price_b:.6f} (liq ${liq_b:,.0f})")
                else:
                    n_errors += 1
                    missing = []
                    if price_a is None:
                        missing.append(dex_a)
                    if price_b is None:
                        missing.append(dex_b)
                    print(f"[skip] no {quote_symbol}-quoted pair found on: {', '.join(missing)}")

            except Exception as e:
                n_errors += 1
                print(f"[error] {e} -- retrying next interval")

            time.sleep(interval_sec)

        print(f"\nDone. Wrote {n_rows} rows ({n_errors} skipped/errors) to {out_path}")
        if n_rows == 0:
            print("No data was collected. Check your internet connection, or try a different")
            print("--dex-a/--dex-b pair -- not every token trades on both venues.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect real live DEX prices from DexScreener")
    parser.add_argument("--token", type=str, default=DEFAULT_TOKEN_ADDRESS,
                         help="Solana token mint address (default: native SOL)")
    parser.add_argument("--dex-a", type=str, default="raydium", help="first DEX id (e.g. raydium)")
    parser.add_argument("--dex-b", type=str, default="orca", help="second DEX id (e.g. orca)")
    parser.add_argument("--quote", type=str, default="USDC", help="quote currency symbol")
    parser.add_argument("--interval", type=int, default=5, help="seconds between polls")
    parser.add_argument("--minutes", type=int, default=60, help="total minutes to collect for")
    parser.add_argument("--out", type=str, default="real_prices.csv", help="output CSV path")
    args = parser.parse_args()

    collect(args.token, args.dex_a, args.dex_b, args.quote, args.interval, args.minutes, args.out)
