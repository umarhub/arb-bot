"""
fetch_historical_data.py

Pulls REAL historical price candles for a token on two Solana DEXs (default:
Raydium and Orca), using:
  1. DexScreener's free public API (no key) -- to find the actual pool
     addresses currently trading this token on each DEX.
  2. Mobula's API (free tier, requires a free API key) -- to fetch real
     historical OHLCV candles for those exact pools, going back in time.

This extends your dataset BACKWARD, with real historical data, complementing
collect_real_data.py which builds your dataset FORWARD from live polling.

Setup (one-time, free, ~2 minutes):
    1. Go to https://admin.mobula.io/ and sign up (no credit card needed)
    2. Copy your API key from the dashboard
    3. Either:
       - pass it each time: --api-key YOUR_KEY
       - or set it once:  export MOBULA_API_KEY=YOUR_KEY   (Mac/Linux)
                           set MOBULA_API_KEY=YOUR_KEY      (Windows cmd)

Usage:
    python3 fetch_historical_data.py --days 7 --out historical_prices.csv

Then merge it with your live-collected data:
    python3 fetch_historical_data.py --days 7 --merge-with real_prices.csv --out combined_prices.csv
"""

import argparse
import csv
import json
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

import pandas as pd

DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"
MOBULA_OHLCV_URL = "https://api.mobula.io/api/2/market/ohlcv-history"
DEFAULT_TOKEN_ADDRESS = "So11111111111111111111111111111111111111112"  # native SOL


def http_get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def http_get_json_verbose(url, headers=None):
    """Like http_get_json, but on an HTTP error prints the server's actual
    error message (not just the status code) so we know what's really wrong."""
    import urllib.error
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"  [HTTP {e.code}] server said: {body[:300]}")
        return None


def find_pool_address(token_address: str, dex_id: str, quote_symbol: str = "USDC"):
    """Reuse DexScreener (no key needed) to find the real, currently-active pool
    address for this token on the given DEX, quoted in the given stablecoin."""
    data = http_get_json(DEXSCREENER_URL.format(address=token_address))
    pairs = data.get("pairs") or []

    candidates = [
        p for p in pairs
        if p.get("dexId", "").lower() == dex_id.lower()
        and p.get("quoteToken", {}).get("symbol", "").upper() == quote_symbol.upper()
    ]
    if not candidates:
        return None

    def liquidity(p):
        try:
            return float(p.get("liquidity", {}).get("usd") or 0)
        except (TypeError, ValueError):
            return 0

    best = max(candidates, key=liquidity)
    return best.get("pairAddress")


def fetch_ohlcv(pool_address: str, api_key: str, chain_id: str, period: str,
                 from_ms: int, to_ms: int, label: str = ""):
    """Fetch historical candles for one pool from Mobula. Returns a list of
    dicts with keys t (ms), o, h, l, c, v -- or an empty list on failure.

    Different Mobula endpoints/docs show the key passed either as an
    Authorization header or as an apiKey query parameter. Rather than guess
    wrong and fail silently, this tries a few real auth methods in order and
    reports exactly what each one returned, so a failure is diagnosable.
    """
    base_params = {
        "address": pool_address,
        "chainId": chain_id,
        "period": period,
        "from": from_ms,
        "to": to_ms,
        "usd": "true",
    }

    attempts = [
        ("Authorization header (raw key)", {"Authorization": api_key}, {}),
        ("Authorization header (Bearer)", {"Authorization": f"Bearer {api_key}"}, {}),
        ("apiKey query parameter", {}, {"apiKey": api_key}),
    ]

    for description, extra_headers, extra_params in attempts:
        params = {**base_params, **extra_params}
        url = f"{MOBULA_OHLCV_URL}?{urllib.parse.urlencode(params)}"
        headers = {"User-Agent": "Mozilla/5.0", **extra_headers}
        print(f"  [{label}] trying: {description} ...")
        data = http_get_json_verbose(url, headers=headers)
        if data is not None:
            print(f"  [{label}] success with: {description}")
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data") or data.get("ohlcv") or []
            return []

    print(f"  [{label}] all auth methods failed. Common causes: the key was copied with "
          f"extra spaces/newlines, the key hasn't finished activating yet (can take a "
          f"minute after signup), or this endpoint needs a different plan than Free.")
    return []


def candles_to_series(candles, label):
    if not candles:
        return pd.DataFrame(columns=["timestamp", label])
    rows = []
    for c in candles:
        t_ms = c.get("t")
        close = c.get("c")
        if t_ms is None or close is None:
            continue
        rows.append({
            "timestamp": pd.to_datetime(t_ms, unit="ms", utc=True),
            label: float(close),
        })
    return pd.DataFrame(rows).sort_values("timestamp")


def main():
    parser = argparse.ArgumentParser(description="Fetch real historical two-DEX price data")
    parser.add_argument("--token", type=str, default=DEFAULT_TOKEN_ADDRESS,
                         help="Solana token mint address (default: native SOL)")
    parser.add_argument("--dex-a", type=str, default="raydium")
    parser.add_argument("--dex-b", type=str, default="orca")
    parser.add_argument("--quote", type=str, default="USDC")
    parser.add_argument("--chain-id", type=str, default="solana")
    parser.add_argument("--period", type=str, default="5m",
                         help="candle size: 1m, 5m, 15m, 1h, etc.")
    parser.add_argument("--days", type=int, default=7, help="how many days back to fetch")
    parser.add_argument("--api-key", type=str, default=os.environ.get("MOBULA_API_KEY"),
                         help="Mobula API key (or set MOBULA_API_KEY env var)")
    parser.add_argument("--out", type=str, default="historical_prices.csv")
    parser.add_argument("--merge-with", type=str, default=None,
                         help="existing CSV (e.g. real_prices.csv) to merge this historical "
                              "data with, chronologically, into --out")
    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: No Mobula API key found.")
        print("Get a free one at https://admin.mobula.io/ and pass it with --api-key YOUR_KEY")
        print("or set it as an environment variable: MOBULA_API_KEY")
        return

    to_ms = int(time.time() * 1000)
    from_ms = to_ms - args.days * 24 * 60 * 60 * 1000

    print(f"Looking up real pool addresses for token {args.token} ...")
    pool_a = find_pool_address(args.token, args.dex_a, args.quote)
    pool_b = find_pool_address(args.token, args.dex_b, args.quote)

    if not pool_a or not pool_b:
        missing = [name for name, p in [(args.dex_a, pool_a), (args.dex_b, pool_b)] if not p]
        print(f"Could not find a {args.quote}-quoted pool on: {', '.join(missing)}. "
              f"Try a different token or venue.")
        return

    print(f"  {args.dex_a} pool: {pool_a}")
    print(f"  {args.dex_b} pool: {pool_b}")
    print(f"Fetching {args.days} days of {args.period} candles from Mobula ...")

    candles_a = fetch_ohlcv(pool_a, args.api_key, args.chain_id, args.period, from_ms, to_ms, label=args.dex_a)
    candles_b = fetch_ohlcv(pool_b, args.api_key, args.chain_id, args.period, from_ms, to_ms, label=args.dex_b)

    if not candles_a or not candles_b:
        print("No candle data returned for one or both pools. This can happen if the pool "
              "is too new/thin for Mobula to have indexed history for it yet.")
        return

    series_a = candles_to_series(candles_a, "price_dex_a")
    series_b = candles_to_series(candles_b, "price_dex_b")

    # Align the two series on nearest matching timestamp (candles from different
    # pools won't land on identical millisecond boundaries).
    merged = pd.merge_asof(
        series_a.sort_values("timestamp"),
        series_b.sort_values("timestamp"),
        on="timestamp",
        direction="nearest",
        tolerance=pd.Timedelta(minutes=10),
    ).dropna()

    print(f"Got {len(merged)} matched historical price points.")

    if args.merge_with and os.path.exists(args.merge_with):
        print(f"Merging with existing file: {args.merge_with}")
        existing = pd.read_csv(args.merge_with, parse_dates=["timestamp"])
        # Normalize timezone so concat/sort works cleanly
        if existing["timestamp"].dt.tz is None:
            existing["timestamp"] = existing["timestamp"].dt.tz_localize("UTC")
        combined = pd.concat([merged, existing], ignore_index=True)
        combined = combined.drop_duplicates(subset="timestamp").sort_values("timestamp")
        combined.to_csv(args.out, index=False)
        print(f"Wrote {len(combined)} total rows (historical + live) to {args.out}")
    else:
        merged.to_csv(args.out, index=False)
        print(f"Wrote {len(merged)} historical rows to {args.out}")


if __name__ == "__main__":
    main()
