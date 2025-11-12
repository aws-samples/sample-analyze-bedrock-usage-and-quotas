#!/bin/bash

echo "Fetching enabled AWS regions..."

# Get enabled regions for this account
REGIONS=$(aws account list-regions --region-opt-status-contains ENABLED ENABLED_BY_DEFAULT --query 'Regions[*].RegionName' --output text)

# Write to YAML file
OUTPUT_FILE="metadata/regions.yml"
mkdir -p metadata
echo "regions:" > "$OUTPUT_FILE"
for region in $REGIONS; do
    echo "  - $region" >> "$OUTPUT_FILE"
done

REGION_COUNT=$(echo "$REGIONS" | wc -w)
echo "Saved $REGION_COUNT enabled regions to $OUTPUT_FILE"
