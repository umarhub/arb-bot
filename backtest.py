"""
backtest.py

A backtester for a simple two-venue DEX arbitrage strategy:

    Watch price_dex_a and price_dex_b for the same asset.
    Whenever the spread between them exceeds a threshold (big enough to
    cover fees + slippage + gas), simulate buying on the cheap venue and
    selling on the expensive venue, in the same bar.

This is intentionally conservative: it charges fees on both legs, applies a
slippage haircut, and charges a fixed gas/priority-fee cost per round trip.
Skipping these costs is exactly why naive backtests look far more profitable
than real trading -- don't remove them.

Usage:
    python backtest.py --data sample_prices.csv --capital 1000

Input CSV must have columns: timestamp, price_dex_a, price_dex_b
"""

import argparse
import pandas as pd
import numpy as np


def mark_implausible_rows(df: pd.DataFrame, max_spread_bps: float = 200.0):
    """
    Flag rows where the spread between the two venues is implausibly large
    for a liquid, well-arbitraged pair (default cutoff: 200 bps = 2%).

    On a highly liquid, heavily-watched pair, a real price gap that big
    would be closed by faster bots almost instantly. If your data shows one
    anyway, it is far more likely to be a data artifact -- a stale price, a
    misrouted pool, or an API glitch -- than a real, tradeable opportunity.
    Trading on these would badly overstate real results.

    Returns the dataframe with a new boolean column "is_implausible".
    """
    df = df.copy()
    lo = df[["price_dex_a", "price_dex_b"]].min(axis=1)
    hi = df[["price_dex_a", "price_dex_b"]].max(axis=1)
    spread_bps = (hi - lo) / lo * 10_000
    df["is_implausible"] = spread_bps > max_spread_bps
    return df


def mark_stale_rows(df: pd.DataFrame, min_repeats: int = 3):
    """
    Flag rows where EITHER price_dex_a or price_dex_b has been unchanged
    for at least `min_repeats` consecutive polls (not just both together).

    This matters because a free price feed can update one venue far less
    often than the other (e.g. a thin/low-volume pool whose indexed price
    barely moves). A "gap" against a frozen, stale-looking price on one
    side is not a real, simultaneously-tradeable opportunity -- by the time
    you could act on it, that side's real price has likely already moved.
    Trading on it would badly overstate real results.

    Returns the dataframe with a new boolean column "is_stale".
    """
    df = df.copy()

    def run_length(col):
        same_as_prev = df[col] == df[col].shift(1)
        run_id = (~same_as_prev).cumsum()
        return df.groupby(run_id).cumcount() + 1

    run_len_a = run_length("price_dex_a")
    run_len_b = run_length("price_dex_b")

    df["is_stale"] = (run_len_a >= min_repeats) | (run_len_b >= min_repeats)
    return df


def run_backtest(
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
    """
    Simulate the arbitrage strategy bar by bar.

    fee_bps_per_leg: DEX swap fee, in basis points, charged on each leg (buy + sell)
    slippage_bps_per_leg: expected slippage/price-impact, in basis points, per leg
    gas_cost_usd: fixed cost per round-trip transaction (network fee + priority fee)
    min_profit_bps: minimum expected edge (after costs) required to take a trade
    max_position_fraction: fraction of current capital risked per trade (0-1)
    filter_stale: skip trades where the price feed appears frozen/stale (recommended)
    stale_min_repeats: how many identical consecutive rows count as "stale"
    filter_implausible: skip trades where the spread is unrealistically large for
        a liquid pair, i.e. more likely a data glitch than a real gap (recommended)
    max_spread_bps: spread above this (in bps) is treated as implausible/bad data
    """
    cash = capital
    equity_curve = []
    trades = []
    n_skipped_stale = 0
    n_skipped_implausible = 0

    total_cost_bps = 2 * fee_bps_per_leg + 2 * slippage_bps_per_leg  # both legs, both cost types

    if filter_stale:
        df = mark_stale_rows(df, min_repeats=stale_min_repeats)
    else:
        df = df.copy()
        df["is_stale"] = False

    if filter_implausible:
        df = mark_implausible_rows(df, max_spread_bps=max_spread_bps)
    else:
        df["is_implausible"] = False

    for _, row in df.iterrows():
        pa, pb = row["price_dex_a"], row["price_dex_b"]

        if pa <= 0 or pb <= 0:
            equity_curve.append(cash)
            continue

        # Determine cheap venue (buy) and expensive venue (sell)
        if pa < pb:
            buy_price, sell_price = pa, pb
        else:
            buy_price, sell_price = pb, pa

        gross_spread_bps = (sell_price - buy_price) / buy_price * 10_000
        net_edge_bps = gross_spread_bps - total_cost_bps

        trade_size_usd = cash * max_position_fraction

        # Gas is a fixed cost, so it only makes sense once the edge clears it
        # as a fraction of trade size.
        gas_bps_equivalent = (gas_cost_usd / trade_size_usd * 10_000) if trade_size_usd > 0 else np.inf
        required_bps = min_profit_bps + gas_bps_equivalent

        would_trade = net_edge_bps > required_bps and trade_size_usd > 0

        if would_trade and row["is_stale"]:
            n_skipped_stale += 1
            equity_curve.append(cash)
            continue

        if would_trade and row["is_implausible"]:
            n_skipped_implausible += 1
            equity_curve.append(cash)
            continue

        if would_trade:
            profit_usd = trade_size_usd * (net_edge_bps / 10_000) - gas_cost_usd
            cash += profit_usd
            trades.append({
                "timestamp": row["timestamp"],
                "buy_price": buy_price,
                "sell_price": sell_price,
                "gross_spread_bps": gross_spread_bps,
                "net_edge_bps": net_edge_bps,
                "trade_size_usd": trade_size_usd,
                "profit_usd": profit_usd,
                "cash_after": cash,
            })

        equity_curve.append(cash)

    df = df.copy()
    df["equity"] = equity_curve
    trades_df = pd.DataFrame(trades)
    if n_skipped_stale > 0:
        print(f"[filter] Skipped {n_skipped_stale} would-be trades during stale/frozen price windows "
              f"(same price repeated {stale_min_repeats}+ times in a row).")
    if n_skipped_implausible > 0:
        print(f"[filter] Skipped {n_skipped_implausible} would-be trades with an implausibly large spread "
              f"(> {max_spread_bps:.0f} bps) -- likely bad data, not a real opportunity.")
    return df, trades_df


def summarize(df: pd.DataFrame, trades_df: pd.DataFrame, capital: float):
    final_equity = df["equity"].iloc[-1]
    total_return_pct = (final_equity / capital - 1) * 100

    running_max = df["equity"].cummax()
    drawdown = (df["equity"] - running_max) / running_max
    max_drawdown_pct = drawdown.min() * 100

    n_trades = len(trades_df)
    win_rate = (trades_df["profit_usd"] > 0).mean() * 100 if n_trades else 0.0
    avg_profit = trades_df["profit_usd"].mean() if n_trades else 0.0

    print("=" * 50)
    print("BACKTEST SUMMARY")
    print("=" * 50)
    print(f"Starting capital:     ${capital:,.2f}")
    print(f"Final equity:         ${final_equity:,.2f}")
    print(f"Total return:         {total_return_pct:+.2f}%")
    print(f"Max drawdown:         {max_drawdown_pct:.2f}%")
    print(f"Number of trades:     {n_trades}")
    print(f"Win rate:             {win_rate:.1f}%")
    print(f"Avg profit per trade: ${avg_profit:,.4f}")
    print("=" * 50)
    if n_trades == 0:
        print("No trades were taken -- the spread never cleared costs.")
        print("This is common and realistic. Try loosening --min-profit-bps")
        print("or check whether your data actually contains real gaps.")


def run_backtest_multi(data: pd.DataFrame, capital: float, **kwargs):
    """
    Run the backtest separately for each token in a multi-token dataset
    (a "token" column present), so one token's prices are never treated as
    a continuous series with another's. Capital is split evenly across
    tokens, as if you were running the strategy on all of them at once
    with a fixed total budget. Returns a dict of per-token (result_df,
    trades_df) plus a combined trades_df across all tokens.
    """
    tokens = sorted(data["token"].unique())
    capital_per_token = capital / len(tokens)
    per_token_results = {}
    all_trades = []

    for token in tokens:
        print(f"\n--- {token} ---")
        subset = data[data["token"] == token].sort_values("timestamp").reset_index(drop=True)
        result_df, trades_df = run_backtest(subset, capital=capital_per_token, **kwargs)
        per_token_results[token] = (result_df, trades_df)
        if len(trades_df) > 0:
            trades_df = trades_df.copy()
            trades_df["token"] = token
            all_trades.append(trades_df)
        summarize(result_df, trades_df, capital_per_token)

    combined_trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    combined_final_equity = sum(r["equity"].iloc[-1] for r, _ in per_token_results.values())
    combined_return_pct = (combined_final_equity / capital - 1) * 100

    print("\n" + "=" * 50)
    print("COMBINED SUMMARY (all tokens)")
    print("=" * 50)
    print(f"Starting capital (total):  ${capital:,.2f}")
    print(f"Final equity (total):      ${combined_final_equity:,.2f}")
    print(f"Total return:              {combined_return_pct:+.2f}%")
    print(f"Total trades (all tokens): {len(combined_trades)}")
    print("=" * 50)

    return per_token_results, combined_trades


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest a two-venue DEX arbitrage strategy")
    parser.add_argument("--data", type=str, required=True,
                         help="CSV with timestamp, price_dex_a, price_dex_b "
                              "(optionally a 'token' column for multi-token files)")
    parser.add_argument("--capital", type=float, default=1000.0, help="starting capital in USD")
    parser.add_argument("--fee-bps", type=float, default=25.0, help="swap fee per leg, in bps (25 bps = 0.25%%)")
    parser.add_argument("--slippage-bps", type=float, default=10.0, help="expected slippage per leg, in bps")
    parser.add_argument("--gas-usd", type=float, default=0.05, help="fixed cost per round-trip trade, in USD")
    parser.add_argument("--min-profit-bps", type=float, default=5.0, help="minimum required edge after costs, in bps")
    parser.add_argument("--max-position-fraction", type=float, default=0.5, help="fraction of capital per trade (0-1)")
    parser.add_argument("--trades-out", type=str, default="trades.csv", help="where to save the trade log")
    parser.add_argument("--no-stale-filter", action="store_true",
                         help="disable the stale/frozen-price filter (not recommended -- inflates results)")
    parser.add_argument("--stale-min-repeats", type=int, default=3,
                         help="consecutive identical price rows before a window counts as stale")
    parser.add_argument("--no-implausible-filter", action="store_true",
                         help="disable the implausible-spread filter (not recommended)")
    parser.add_argument("--max-spread-bps", type=float, default=200.0,
                         help="spreads above this (bps) are treated as bad data, not a real gap")
    args = parser.parse_args()

    data = pd.read_csv(args.data, parse_dates=["timestamp"])

    shared_kwargs = dict(
        fee_bps_per_leg=args.fee_bps,
        slippage_bps_per_leg=args.slippage_bps,
        gas_cost_usd=args.gas_usd,
        min_profit_bps=args.min_profit_bps,
        max_position_fraction=args.max_position_fraction,
        filter_stale=not args.no_stale_filter,
        stale_min_repeats=args.stale_min_repeats,
        filter_implausible=not args.no_implausible_filter,
        max_spread_bps=args.max_spread_bps,
    )

    if "token" in data.columns:
        print(f"Multi-token file detected ({data['token'].nunique()} tokens). "
              f"Running each token's price history as its own independent series.")
        per_token_results, combined_trades = run_backtest_multi(data, capital=args.capital, **shared_kwargs)

        if len(combined_trades) > 0:
            combined_trades.to_csv(args.trades_out, index=False)
            print(f"\nCombined trade log (all tokens) saved to {args.trades_out}")

        equity_frames = []
        for token, (result_df, _) in per_token_results.items():
            frame = result_df[["timestamp", "equity"]].copy()
            frame["token"] = token
            equity_frames.append(frame)
        pd.concat(equity_frames, ignore_index=True).to_csv("equity_curve.csv", index=False)
        print("Per-token equity curves saved to equity_curve.csv")

    else:
        result_df, trades_df = run_backtest(data, capital=args.capital, **shared_kwargs)

        summarize(result_df, trades_df, args.capital)

        if len(trades_df) > 0:
            trades_df.to_csv(args.trades_out, index=False)
            print(f"\nTrade log saved to {args.trades_out}")

        result_df[["timestamp", "equity"]].to_csv("equity_curve.csv", index=False)
        print("Equity curve saved to equity_curve.csv")
