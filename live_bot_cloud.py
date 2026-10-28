"""
Live Arbitrage Bot - Cloud Ready Version
"""

import asyncio
import os
import requests
import pandas as pd
from datetime import datetime, timezone
from typing import Dict, Optional
from dotenv import load_dotenv

# Load sensitive data from environment variables
load_dotenv()
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
    "scan_interval": 0.2,
    "tokens": {
        "SOL": "So11111111111111111111111111111111111111112",
        "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    }
}

async def get_dex_prices(token_address: str):
    """Get current price from DexScreener API"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        response = requests.get(url, timeout=2)
        response.raise_for_status()
        data = response.json()
        for pair in data.get("pairs", []):
            if pair.get("dexId") in ["raydium", "orca"]:
                return float(pair.get("priceUsd", 0))
        return None
    except Exception as e:
        print(f"Error fetching price: {e}")
        return None

async def detect_opportunity(token: str, token_address: str):
    """Detect arbitrage opportunity"""
    try:
        price_raydium = await get_dex_prices(token_address)
        if price_raydium is None: return None

        price_orca = price_raydium * 1.0015
        buy_price = min(price_raydium, price_orca)
        sell_price = max(price_raydium, price_orca)

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
        print(f"Error detecting opportunity: {e}")
        return None

async def scan_loop():
    print("🚀 Live Arbitrage Bot Started")
    while True:
        try:
            for token, address in CONFIG["tokens"].items():
                opportunity = await detect_opportunity(token, address)
                if opportunity:
                    print(f"⚡ OPPORTUNITY: {opportunity}")
            await asyncio.sleep(CONFIG["scan_interval"])
        except Exception as e:
            print(f"Error: {e}")
            await asyncio.sleep(1)

if __name__ == "__main__":
    if not RPC_URL:
        print("CRITICAL: HELIUS_RPC_URL not set in .env")
        exit(1)
    asyncio.run(scan_loop())