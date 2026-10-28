# Dockerfile for Live Arbitrage Bot

# Use official Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY live_bot_cloud.py .

# Set environment variables (will be overridden by cloud provider)
ENV HELIUS_RPC_URL=""
ENV BOT_WALLET_PUBKEY=""

# Run the application
CMD ["python", "live_bot_cloud.py"]