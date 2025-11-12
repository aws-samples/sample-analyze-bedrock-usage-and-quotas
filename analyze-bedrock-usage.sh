#!/bin/bash

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

# Run the Python script (in same directory)
python "$(dirname "$0")/analyze_bedrock_usage.py"

# Deactivate virtual environment
deactivate
