"""
Live Arbitrage Bot - Cloud Ready Version
"""

import asyncio
import os
import sys
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Optional
from dotenv import load_dotenv

# Ensure unbuffered output so print statements display immediately
sys.stdout.reconfigure(line_buffering=True)

# Automatically locate and load .env relative to this script file
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

RPC_URL = os.getenv("HELIUS_RPC_URL")
BOT_WALLET_PUBKEY = os.getenv("BOT_WALLET_PUBKEY")

# Configuration
CONFIG = {
    "jupiter_api_url": "https://quote-api.jup.ag/v6",
    "capital_usd": 1000.0,
    "max_position_fraction": 0.1,
    "min_profit_bps": 10,
    "gas_usd": 0.005,
    "slippage_bps": 10,
    "scan_interval": 30.0,  # 30 seconds to avoid rate limits
    "tokens": {
        "SOL": "So11111111111111111111111111111111111111112",
        "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    }
}

async def get_price_from_jupiter(input_mint: str, output_mint: str, amount: float = 1):
    """Get price from Jupiter API"""
    try:
        url = f"{CONFIG['jupiter_api_url']}/quote?inputMint={input_mint}&outputMint={output_mint}&amount={amount}&slippageBps={CONFIG['slippage_bps']}"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        return float(data.get("outAmount", 0))
    except Exception as e:
        print(f"Error fetching price from Jupiter: {e}", flush=True)
        return None

async def get_price_from_dexscreener(token_address: str):
    """Get price from DexScreener API (fallback)"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        for pair in data.get("pairs", []):
            if pair.get("dexId") in ["raydium", "orca"]:
                return float(pair.get("priceUsd", 0))
        return None
    except Exception as e:
        print(f"Error fetching price from DexScreener: {e}", flush=True)
        return None

async def get_token_price(token_address: str):
    """Get token price using Jupiter API as primary source"""
    try:
        # Use USDC as the output mint for price
        usdc_price = await get_price_from_jupiter(token_address, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 1)
        if usdc_price:
            return usdc_price

        # Fallback to DexScreener
        return await get_price_from_dexscreener(token_address)
    except Exception as e:
        print(f"Error getting token price: {e}", flush=True)
        return None

async def detect_opportunity(token: str, token_address: str):
    """Detect arbitrage opportunity"""
    try:
        price = await get_token_price(token_address)
        if price is None:
            return None

        # Simulate cross-venue spread (in reality, you'd query both DEXs separately)
        price_orca = price * 1.0015
        buy_price = min(price, price_orca)
        sell_price = max(price, price_orca)

        gross_spread = (sell_price - buy_price) / buy_price * 10000
        net_edge_bps = gross_spread - 20

        if net_edge_bps > CONFIG["min_profit_bps"]:
            trade_size = CONFIG["capital_usd"] * CONFIG["max_position_fraction"]
            profit_usd = trade_size * (net_edge_bps / 10000) - CONFIG["gas_usd"]
            return {
                "token": token,
                "buy_price": buy_price,
                "sell_price": sell_price,
                "gross_spread_bps": gross_spread,
                "net_edge_bps": net_edge_bps,
                "profit_usd": profit_usd,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        return None
    except Exception as e:
        print(f"Error detecting opportunity: {e}", flush=True)
        return None

async def scan_loop():
    print("🚀 Live Arbitrage Bot Started", flush=True)
    print(f"Connected to Jupiter API: {CONFIG['jupiter_api_url']}", flush=True)
    while True:
        try:
            for token, address in CONFIG["tokens"].items():
                opportunity = await detect_opportunity(token, address)
                if opportunity:
                    print(f"⚡ OPPORTUNITY: {opportunity}", flush=True)
            await asyncio.sleep(CONFIG["scan_interval"])
        except Exception as e:
            print(f"Error: {e}", flush=True)
            await asyncio.sleep(300)  # Wait 5 minutes before retrying

if __name__ == "__main__":
    if not RPC_URL:
        print("CRITICAL: HELIUS_RPC_URL not set in .env", flush=True)
        sys.exit(1)
    asyncio.run(scan_loop())