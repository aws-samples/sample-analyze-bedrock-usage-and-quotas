#!/bin/bash

# Stress test script for parallel Bedrock inference data generation
# Runs concurrent inferences to generate high-volume metrics

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$PROJECT_ROOT/.venv"

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Activate virtual environment
source "$VENV_DIR/bin/activate"

# Install dependencies if needed
if ! python3 -c "import boto3" 2>/dev/null; then
    echo "Installing dependencies..."
    pip install -q -r "$PROJECT_ROOT/requirements.txt"
fi

# Run stress test
cd "$PROJECT_ROOT"
python3 utils/generate_test_data_parallel.py

deactivate
