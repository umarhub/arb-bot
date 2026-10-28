"""
backtest_vectorized.py

Optimized version of backtest.py using pandas vectorization.
This replaces the row-by-row iteration with fast array operations,
making it significantly faster for large datasets.
"""

import argparse
import pandas as pd
import numpy as np


def run_backtest_vectorized(
    df: pd.DataFrame,
    capital: float,
    fee_bps_per_leg: float,
    slippage_bps_per_leg: float,
    gas_cost_usd: float,
    min_profit_bps: float,
    max_position_fraction: float,
    filter_stale: bool = True,
    stale_min_repeats: int = 3,
    filter_implausible: bool = True,
    max_spread_bps: float = 200.0,
):
    df = df.copy()

    # 1. Filter stale data (vectorized)
    if filter_stale:
        is_stale_a = df["price_dex_a"].groupby((df["price_dex_a"] != df["price_dex_a"].shift()).cumsum()).cumcount() + 1 >= stale_min_repeats
        is_stale_b = df["price_dex_b"].groupby((df["price_dex_b"] != df["price_dex_b"].shift()).cumsum()).cumcount() + 1 >= stale_min_repeats
        df["is_stale"] = is_stale_a | is_stale_b
    else:
        df["is_stale"] = False

    # 2. Filter implausible spreads (vectorized)
    if filter_implausible:
        lo = df[["price_dex_a", "price_dex_b"]].min(axis=1)
        hi = df[["price_dex_a", "price_dex_b"]].max(axis=1)
        spread_bps = (hi - lo) / lo * 10_000
        df["is_implausible"] = spread_bps > max_spread_bps
    else:
        df["is_implausible"] = False

    # 3. Calculate trade conditions (vectorized)
    total_cost_bps = 2 * fee_bps_per_leg + 2 * slippage_bps_per_leg

    # Track which venue is cheap and which is expensive
    df["buy_price"] = df[["price_dex_a", "price_dex_b"]].min(axis=1)
    df["sell_price"] = df[["price_dex_a", "price_dex_b"]].max(axis=1)

    df["gross_spread_bps"] = (df["sell_price"] - df["buy_price"]) / df["buy_price"] * 10_000
    df["net_edge_bps"] = df["gross_spread_bps"] - total_cost_bps

    # Trade logic
    trade_size_usd = capital * max_position_fraction
    df["can_trade"] = (df["net_edge_bps"] > min_profit_bps) & (~df["is_stale"]) & (~df["is_implausible"]) & (df["buy_price"] > 0)

    # 4. Calculate PnL
    df["profit_usd"] = 0.0
    df.loc[df["can_trade"], "profit_usd"] = trade_size_usd * (df["net_edge_bps"] / 10_000) - gas_cost_usd

    # Running cash
    df["cash"] = capital + df["profit_usd"].cumsum()
    df["equity"] = df["cash"]

    # 5. Output trades dataframe
    trades_df = df[df["can_trade"]].copy()

    if not trades_df.empty:
        trades_df["trade_size_usd"] = trade_size_usd
        # Keep columns matching original output
        trades_df = trades_df[["timestamp", "buy_price", "sell_price", "gross_spread_bps",
                               "net_edge_bps", "trade_size_usd", "profit_usd", "cash"]]
        trades_df = trades_df.rename(columns={"cash": "cash_after"})
    else:
        # Create empty df with right columns
        trades_df = pd.DataFrame(columns=["timestamp", "buy_price", "sell_price", "gross_spread_bps",
                                          "net_edge_bps", "trade_size_usd", "profit_usd", "cash_after"])

    return df, trades_df


def run_backtest_multi_vectorized(data: pd.DataFrame, capital: float, **kwargs):
    tokens = sorted(data["token"].unique())
    capital_per_token = capital / len(tokens)
    all_trades = []
    all_res = []

    for token in tokens:
        subset = data[data["token"] == token].sort_values("timestamp").reset_index(drop=True)
        result_df, trades_df = run_backtest_vectorized(subset, capital=capital_per_token, **kwargs)

        if not trades_df.empty:
            trades_df["token"] = token
            all_trades.append(trades_df)

        result_df["token"] = token
        all_res.append(result_df)

        final_equity = result_df["equity"].iloc[-1]
        print(f"Token {token:5s} | Trades: {len(trades_df):4d} | Final: ${final_equity:8.2f}")

    combined_trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    combined_res = pd.concat(all_res, ignore_index=True)

    total_final = sum(r["equity"].iloc[-1] for r in all_res)
    return combined_res, combined_trades, total_final


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vectorized backtester")
    parser.add_argument("--data", type=str, required=True,
                        help="CSV with timestamp, price_dex_a, price_dex_b")
    parser.add_argument("--capital", type=float, default=1000.0,
                        help="starting capital in USD")
    parser.add_argument("--fee-bps", type=float, default=25.0, help="swap fee per leg, in bps")
    parser.add_argument("--slippage-bps", type=float, default=10.0, help="expected slippage per leg, in bps")
    parser.add_argument("--gas-usd", type=float, default=0.05, help="fixed cost per round-trip trade, in USD")
    parser.add_argument("--min-profit-bps", type=float, default=5.0, help="minimum required edge after costs, in bps")
    parser.add_argument("--max-position-fraction", type=float, default=0.5, help="fraction of capital per trade (0-1)")
    args = parser.parse_args()

    try:
        data = pd.read_csv(args.data, parse_dates=["timestamp"])
    except Exception as e:
        print(f"Error reading CSV: {e}")
        exit(1)

    shared_params = {
        "fee_bps_per_leg": args.fee_bps,
        "slippage_bps_per_leg": args.slippage_bps,
        "gas_cost_usd": args.gas_usd,
        "min_profit_bps": args.min_profit_bps,
        "max_position_fraction": args.max_position_fraction
    }

    if "token" in data.columns:
        print(f"Multi-token file detected ({data['token'].nunique()} tokens).")
        res, trades, total_final = run_backtest_multi_vectorized(data, args.capital, **shared_params)
    else:
        res, trades = run_backtest_vectorized(data, args.capital, **shared_params)
        total_final = res["equity"].iloc[-1]

    print("=" * 50)
    print(f"FINAL TOTAL EQUITY: ${total_final:,.2f} ({(total_final/args.capital - 1)*100:+.2f}%)")
    print(f"TOTAL TRADES:       {len(trades)}")
    print("=" * 50)

    # Save equity curve
    res.to_csv("equity_curve_vectorized.csv", index=False)
    print("\nEquity curve saved to equity_curve_vectorized.csv")
    if len(trades) > 0:
        trades.to_csv("trades_vectorized.csv", index=False)
        print("Trade log saved to trades_vectorized.csv")
