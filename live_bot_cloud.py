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
from http.server import HTTPServer, BaseHTTPRequestHandler

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
    "scan_interval": 60.0,  # 1 minute to avoid rate limits
    "tokens": {
        "SOL": "So11111111111111111111111111111111111111112",
        "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    }
}

# Fallback price data (simulated for demo)
FALLBACK_PRICES = {
    "So11111111111111111111111111111111111111112": 150.0,  # SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": 1.0,   # USDC
}

async def get_price_from_jupiter(input_mint: str, output_mint: str, amount: float = 1):
    """Get price from Jupiter API with fallback"""
    try:
        url = f"{CONFIG['jupiter_api_url']}/quote?inputMint={input_mint}&outputMint={output_mint}&amount={amount}&slippageBps={CONFIG['slippage_bps']}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return float(data.get("outAmount", 0))
    except Exception as e:
        print(f"Jupiter API failed: {e}", flush=True)

    # Fallback to hardcoded prices
    if input_mint in FALLBACK_PRICES:
        return FALLBACK_PRICES[input_mint]
    return None

async def get_token_price(token_address: str):
    """Get token price using Jupiter API or fallback"""
    try:
        price = await get_price_from_jupiter(token_address, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 1)
        if price:
            return price

        # Fallback to hardcoded price
        if token_address in FALLBACK_PRICES:
            return FALLBACK_PRICES[token_address]
        return None
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
    print(f"Using fallback prices (Jupiter API unavailable)", flush=True)
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

# Simple HTTP server to keep Render free tier happy
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Solana Arbitrage Bot is running")

    def log_message(self, format, *args):
        pass  # Suppress HTTP server logs

def start_health_check_server():
    """Start a simple HTTP server on port 8080 to satisfy Render's port requirement"""
    server = HTTPServer(("0.0.0.0", 8080), HealthCheckHandler)
    print("✅ Health check server running on port 8080", flush=True)
    server.serve_forever()

if __name__ == "__main__":
    if not RPC_URL:
        print("CRITICAL: HELIUS_RPC_URL not set in .env", flush=True)
        sys.exit(1)

    # Start the health check server in a separate thread
    import threading
    health_thread = threading.Thread(target=start_health_check_server, daemon=True)
    health_thread.start()

    # Start the bot scanning loop
    asyncio.run(scan_loop())