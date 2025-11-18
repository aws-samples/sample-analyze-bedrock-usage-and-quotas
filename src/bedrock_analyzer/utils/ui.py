"""Interactive UI for quota mapping parameter selection"""

import sys
from typing import Tuple

from bedrock_analyzer.utils.yaml_handler import load_yaml


def select_from_list(prompt: str, options: list, allow_cancel: bool = True) -> str:
    """Generic numbered selection from list
    
    Args:
        prompt: Prompt message
        options: List of options
        allow_cancel: Allow cancellation with Ctrl+C
        
    Returns:
        Selected option
    """
    print(f"\n{prompt}")
    for i, option in enumerate(options, 1):
        print(f"  {i}. {option}")
    
    while True:
        try:
            choice = int(input(f"\nSelect (1-{len(options)}): "))
            if 1 <= choice <= len(options):
                return options[choice - 1]
            print(f"Please enter a number between 1 and {len(options)}")
        except ValueError:
            print("Please enter a valid number")
        except (KeyboardInterrupt, EOFError):
            if allow_cancel:
                print("\nSelection cancelled.", file=sys.stderr)
                sys.exit(1)
            raise


def select_quota_mapping_params() -> Tuple[str, str, str]:
    """Interactive selection for quota mapping parameters
    
    Returns:
        Tuple of (bedrock_region, model_id, target_region)
    """
    print("\n" + "="*60)
    print("Foundation Model Quota Mapping Tool")
    print("="*60)
    print("\nThis tool will:")
    print("  • Process ALL enabled regions automatically")
    print("  • Use a Bedrock LLM to intelligently map service quotas")
    print("  • Cache L-codes (same across regions)")
    print("="*60)
    
    # Load regions
    regions_data = load_yaml('metadata/regions.yml')
    all_regions = regions_data.get('regions', [])
    
    # Step 1: Select Bedrock API region
    bedrock_region = select_from_list(
        "Step 1: Select AWS region to use for Bedrock API calls:",
        all_regions
    )
    print(f"\n✓ Bedrock calls will use region: {bedrock_region}")
    
    # Step 2: Select model for mapping
    model_options = [
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
        "au.anthropic.claude-haiku-4-5-20251001-v1:0",
        "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
        "global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "anthropic.claude-3-5-haiku-20241022-v1:0"
    ]
    
    model_id = select_from_list(
        "Step 2: Select Claude model to use for intelligent mapping:",
        model_options
    )
    print(f"\n✓ Will use model: {model_id}")
    
    # Step 3: Optional target region filter
    print("\nStep 3: Target region filter (optional)")
    print("  1. Process ALL regions")
    print("  2. Process specific region only")
    
    while True:
        try:
            choice = int(input("\nSelect (1-2): "))
            if choice == 1:
                target_region = None
                print("\n✓ Will process all regions")
                break
            elif choice == 2:
                target_region = select_from_list(
                    "Select target region:",
                    all_regions
                )
                print(f"\n✓ Will process only: {target_region}")
                break
            else:
                print("Please enter 1 or 2")
        except ValueError:
            print("Please enter a valid number")
        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.", file=sys.stderr)
            sys.exit(1)
    
    return bedrock_region, model_id, target_region


def main():
    """Main entry point"""
    return select_quota_mapping_params()


if __name__ == "__main__":
    main()
