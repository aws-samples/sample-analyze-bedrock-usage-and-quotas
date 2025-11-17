"""CLI commands for refreshing metadata"""

import sys
import logging

from bedrock_analyzer.utils.yaml_handler import load_yaml
from bedrock_analyzer.metadata.fm_list import refresh_region, refresh_all_regions

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def refresh_fm_list_command(region: str = None):
    """Refresh foundation model lists
    
    Args:
        region: Specific region to refresh, or None for all regions
    """
    if region:
        # Refresh specific region
        refresh_region(region)
    else:
        # Refresh all regions
        try:
            regions_data = load_yaml('metadata/regions.yml')
            regions = regions_data.get('regions', [])
            
            if not regions:
                logger.error("No regions found in metadata/regions.yml")
                logger.error("Please run: refresh-regions first")
                sys.exit(1)
            
            logger.info(f"Refreshing {len(regions)} regions...")
            refresh_all_regions(regions)
            logger.info("\n✓ All regions refreshed")
            
        except FileNotFoundError:
            logger.error("Regions file not found: metadata/regions.yml")
            logger.error("Please run: refresh-regions first")
            sys.exit(1)


def main():
    """Main entry point for bedrock-refresh command"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Refresh Bedrock metadata')
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # FM list refresh
    fm_parser = subparsers.add_parser('fm-list', help='Refresh foundation model lists')
    fm_parser.add_argument('region', nargs='?', help='Specific region to refresh (optional)')
    
    args = parser.parse_args()
    
    if args.command == 'fm-list':
        refresh_fm_list_command(args.region)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
