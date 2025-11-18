"""AWS regions management"""

import boto3
import sys
from typing import List

from bedrock_analyzer.utils.yaml_handler import save_yaml


def fetch_enabled_regions() -> List[str]:
    """Fetch enabled AWS regions for the account
    
    Returns:
        List of enabled region names
    """
    try:
        client = boto3.client('account')
        regions = []
        
        # Use paginator to get all regions
        paginator = client.get_paginator('list_regions')
        for page in paginator.paginate(RegionOptStatusContains=['ENABLED', 'ENABLED_BY_DEFAULT']):
            regions.extend([r['RegionName'] for r in page.get('Regions', [])])
        
        return sorted(regions)
    except Exception as e:
        print(f"Error fetching regions: {e}", file=sys.stderr)
        sys.exit(1)


def refresh_regions():
    """Refresh the regions list and save to metadata/regions.yml"""
    print("Fetching enabled AWS regions...", file=sys.stderr)
    
    regions = fetch_enabled_regions()
    
    if not regions:
        print("No regions found", file=sys.stderr)
        sys.exit(1)
    
    output_file = 'metadata/regions.yml'
    save_yaml(output_file, {'regions': regions})
    
    print(f"Saved {len(regions)} enabled regions to {output_file}", file=sys.stderr)


def main():
    """Main entry point"""
    refresh_regions()


if __name__ == "__main__":
    main()
