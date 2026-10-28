# Solana Live Arbitrage Bot

A 24/7 automated arbitrage bot that scans Solana DEX prices and executes profitable trades.

## Setup

1. **Get Helius RPC URL**
   - Visit https://helius.dev/
   - Create a free account
   - Get your API key and create an RPC URL
   - Replace `YOUR_API_KEY` in `.env.example` with your actual key

2. **Create a Bot Wallet**
   - Use Phantom Wallet > Create new wallet
   - Export the public key (NOT private key)
   - Update `BOT_WALLET_PUBKEY` in `.env.example`
   - Fund the wallet with small amount of SOL for paper trading

3. **Deploy to Railway/Render**
   - Connect your GitHub repo to Railway
   - Set environment variables:
     - `HELIUS_RPC_URL`: Your Helius URL
     - `BOT_WALLET_PUBKEY`: Your wallet public key
   - Deploy!

## How It Works
- Scans Raydium and Orca every 0.2 seconds
- Detects arbitrage opportunities after fees/slippage
- Logs potential trades (paper trading mode)
- Can be upgraded to execute real swaps

## Security
- Never commit `.env` file
- Private keys never touch the code
- Use environment variables for secrets

## Next Steps
1. Deploy to Railway (https://railway.app/)
2. Test with paper trading
3. Add real Jupiter execution logic
4. Monitor performance 24/7