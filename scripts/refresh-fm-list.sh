#!/bin/bash

SPECIFIC_REGION="$1"

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    
    # Activate and install dependencies
    source .venv/bin/activate
    echo "Installing dependencies..."
    pip install -r requirements.txt > /dev/null 2>&1
else
    # Activate existing virtual environment
    source .venv/bin/activate
fi

# Run Python utility
if [ -n "$SPECIFIC_REGION" ]; then
    python3 utils/refresh_fm_list.py "$SPECIFIC_REGION"
else
    python3 utils/refresh_fm_list.py
fi

# Deactivate virtual environment
deactivate
