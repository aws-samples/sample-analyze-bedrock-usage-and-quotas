#!/bin/bash

# Parse optional region argument
TARGET_REGION=""
if [ -n "$1" ]; then
    TARGET_REGION="$1"
    echo "=========================================="
    echo "Foundation Model Quota Mapping Tool"
    echo "Target Region: $TARGET_REGION"
    echo "=========================================="
else
    echo "=========================================="
    echo "Foundation Model Quota Mapping Tool"
    echo "=========================================="
fi
echo ""
echo "⚠️  WARNING: This tool will consume tokens by calling Bedrock LLM"
echo "    to intelligently map AWS service quotas to foundation models."
echo ""
read -p "Do you want to continue? (y/n): " confirm
if [ "$confirm" != "y" ]; then
    echo "Aborted."
    exit 0
fi

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo ""
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

# Check if regions.yml exists
if [ ! -s "metadata/regions.yml" ]; then
    echo ""
    echo "Regions list not found. Fetching regions first..."
    ./scripts/refresh-regions.sh
fi

# Run Python script (writes to .quota_mapping_params.tmp)
python3 utils/select_fm_quota_mapping_params.py "$TARGET_REGION"

# Check if selection was successful
if [ ! -f ".quota_mapping_params.tmp" ]; then
    echo "Selection cancelled or failed."
    deactivate
    exit 1
fi

# Read parameters from temp file
source .quota_mapping_params.tmp

# Clean up temp file
rm -f .quota_mapping_params.tmp

if [ -z "$BEDROCK_REGION" ] || [ -z "$MODEL_ID" ]; then
    echo "Selection cancelled or failed."
    deactivate
    exit 1
fi

echo ""
echo "Starting quota mapping..."
echo ""

# Run the mapping script with optional region filter
if [ -n "$TARGET_REGION" ]; then
    python utils/refresh_fm_quota_mapping.py "$BEDROCK_REGION" "$MODEL_ID" "$TARGET_REGION"
else
    python utils/refresh_fm_quota_mapping.py "$BEDROCK_REGION" "$MODEL_ID"
fi

echo ""
echo "Quota mapping complete!"

# Deactivate virtual environment
deactivate
