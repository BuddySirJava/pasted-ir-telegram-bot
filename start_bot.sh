#!/bin/bash

# Pastebinir Telegram Bot Startup Script

echo "Starting Pastebinir Telegram Bot..."

# Check if config.env exists
if [ ! -f "config.env" ]; then
    echo "Error: config.env file not found!"
    echo "Please copy config.env.example to config.env and configure it first."
    exit 1
fi

# Check if main.py exists
if [ ! -f "main.py" ]; then
    echo "Error: main.py not found!"
    exit 1
fi

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "Error: uv is not installed!"
    echo "Please install uv first: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Load environment variables
set -a
source config.env
set +a

# Check if TELEGRAM_TOKEN is set
if [ -z "$TELEGRAM_TOKEN" ]; then
    echo "Error: TELEGRAM_TOKEN not set in config.env!"
    exit 1
fi

# Install dependencies using uv
echo "Installing dependencies with uv..."
uv sync

# Start the bot using uv
echo "Starting bot with token: ${TELEGRAM_TOKEN:0:10}..."
uv run start 