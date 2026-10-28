"""
generate_sample_data.py

Creates a synthetic historical price CSV simulating a token traded on two DEXs
(e.g., Raydium and Orca) with realistic random-walk prices plus occasional
small dislocations between venues (the kind of gap an arbitrage bot hunts for).

This is ONLY for testing the backtester's mechanics before you plug in real
historical data. Synthetic data will NOT tell you whether a real strategy is
profitable -- see README.md for how to pull real data.
"""

import numpy as np
import pandas as pd
import argparse


def generate(n_rows: int, seed: int, out_path: str, start_price: float):
    rng = np.random.default_rng(seed)

    # Base price: geometric random walk, 1-second bars
    minute_returns = rng.normal(loc=0.0, scale=0.0006, size=n_rows)
    base_price = start_price * np.exp(np.cumsum(minute_returns))

    # Each venue's observed price = base price + independent microstructure
    # noise + occasional short-lived dislocation (a "gap") that decays back.
    noise_a = rng.normal(0, 0.0004, size=n_rows)
    noise_b = rng.normal(0, 0.0004, size=n_rows)

    dislocation = np.zeros(n_rows)
    # Randomly scatter short dislocation events
    n_events = max(1, n_rows // 250)
    event_starts = rng.choice(n_rows - 20, size=n_events, replace=False)
    for s in event_starts:
        magnitude = rng.normal(0, 0.003)  # ~0.3% typical gap
        decay_len = rng.integers(3, 15)
        decay = magnitude * np.exp(-np.arange(decay_len) / (decay_len / 3))
        dislocation[s:s + decay_len] += decay[:len(dislocation[s:s + decay_len])]

    price_a = base_price * (1 + noise_a)
    price_b = base_price * (1 + noise_b + dislocation)

    timestamps = pd.date_range("2026-01-01", periods=n_rows, freq="s")

    df = pd.DataFrame({
        "timestamp": timestamps,
        "price_dex_a": price_a,
        "price_dex_b": price_b,
    })
    df.to_csv(out_path, index=False)
    print(f"Wrote {n_rows} rows of synthetic data to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic two-venue price data")
    parser.add_argument("--rows", type=int, default=20000, help="number of 1-second bars")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start-price", type=float, default=150.0, help="starting price (e.g. SOL/USDC)")
    parser.add_argument("--out", type=str, default="sample_prices.csv")
    args = parser.parse_args()

    generate(args.rows, args.seed, args.out, args.start_price)
