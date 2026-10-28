"""
collect_real_data_multi.py

Like collect_real_data.py, but watches SEVERAL tokens at once instead of
just one. SOL/USDC is extremely liquid and heavily arbitraged already (our
first test found real opportunities only twice in 2 days) -- less-watched
tokens sometimes have bigger, slower-closing gaps, at the cost of real
added risk (thinner liquidity, more erratic prices, higher rug risk).

Output format includes a "token" column so backtest.py can analyze each
token's price history separately (mixing different tokens into one
timeline would wrongly treat a token switch as a price jump).

Usage (defaults to 5 well-known tokens: SOL, JUP, BONK, WIF, RAY):
    python3 collect_real_data_multi.py --minutes 120 --interval 5

Leave it running in a terminal window. Ctrl+C to stop early -- already
collected rows are saved.
"""

import argparse
import csv
import time
import urllib.request
import json
import os
from datetime import datetime, timezone

DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"

# Verified mint addresses (checked against official docs/audits, not guessed).
DEFAULT_TOKENS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "RAY": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
}


def fetch_pairs(token_address: str):
    url = DEXSCREENER_URL.format(address=token_address)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    return data.get("pairs") or []


def best_price_for_dex(pairs, dex_id: str, quote_symbol: str = "USDC"):
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


def collect(tokens: dict, dex_a, dex_b, quote_symbol, interval_sec, total_minutes, out_path):
    end_time = time.time() + total_minutes * 60
    file_exists = os.path.exists(out_path)

    with open(out_path, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "token", "price_dex_a", "price_dex_b"])

        n_rows = 0
        n_skipped = 0
        print(f"Watching {len(tokens)} tokens: {', '.join(tokens.keys())}")
        print(f"Comparing {dex_a} vs {dex_b}, polling every {interval_sec}s for {total_minutes} minutes.")
        print(f"Writing to {out_path}. Ctrl+C to stop early.\n")

        while time.time() < end_time:
            for symbol, address in tokens.items():
                try:
                    pairs = fetch_pairs(address)
                    price_a, liq_a = best_price_for_dex(pairs, dex_a, quote_symbol)
                    price_b, liq_b = best_price_for_dex(pairs, dex_b, quote_symbol)

                    if price_a is not None and price_b is not None:
                        ts = datetime.now(timezone.utc).isoformat()
                        writer.writerow([ts, symbol, price_a, price_b])
                        f.flush()
                        n_rows += 1
                        spread_pct = abs(price_a - price_b) / min(price_a, price_b) * 100
                        print(f"{ts}  {symbol:5s}  {dex_a}=${price_a:.6f}  {dex_b}=${price_b:.6f}  "
                              f"gap={spread_pct:.3f}%")
                    else:
                        n_skipped += 1
                        print(f"[skip] {symbol}: no {quote_symbol}-quoted pair on both venues")

                except Exception as e:
                    n_skipped += 1
                    print(f"[error] {symbol}: {e}")

                # Small stagger between tokens so we don't fire all requests at once
                time.sleep(max(0.5, interval_sec / len(tokens)))

        print(f"\nDone. Wrote {n_rows} rows ({n_skipped} skipped/errors) to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect real live prices for multiple Solana tokens")
    parser.add_argument("--tokens", type=str, default=None,
                         help="comma-separated SYMBOL:MINT_ADDRESS pairs to override the default 5 tokens, "
                              "e.g. 'SOL:So111...112,JUP:JUPy...DvCN'")
    parser.add_argument("--dex-a", type=str, default="raydium")
    parser.add_argument("--dex-b", type=str, default="orca")
    parser.add_argument("--quote", type=str, default="USDC")
    parser.add_argument("--interval", type=int, default=5,
                         help="seconds between polls OF EACH TOKEN (total cycle time scales with token count)")
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--out", type=str, default="multi_token_prices.csv")
    args = parser.parse_args()

    if args.tokens:
        tokens = {}
        for pair in args.tokens.split(","):
            symbol, address = pair.split(":")
            tokens[symbol.strip()] = address.strip()
    else:
        tokens = DEFAULT_TOKENS

    collect(tokens, args.dex_a, args.dex_b, args.quote, args.interval, args.minutes, args.out)
