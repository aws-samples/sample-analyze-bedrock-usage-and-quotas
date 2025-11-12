#!/bin/bash

echo "=========================================="
echo "Quota Index Generator"
echo "=========================================="
echo ""
echo "This tool generates a CSV index of all quota mappings"
echo "for validation purposes."
echo ""

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

# Check if fm-list files exist
if ! ls metadata/fm-list-*.yml 1> /dev/null 2>&1; then
    echo "No fm-list files found in metadata/"
    echo "Run refresh-fm-list.sh first."
    deactivate
    exit 1
fi

# Run the Python script
python utils/refresh_quota_index.py

# Deactivate virtual environment
deactivate
