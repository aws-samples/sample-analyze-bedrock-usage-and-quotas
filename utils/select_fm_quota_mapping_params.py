#!/usr/bin/env python3
"""Interactive selection for quota mapping parameters"""

import yaml
import sys


def main():
    # Step 1: Explain LLM usage
    print("\n" + "="*60)
    print("Foundation Model Quota Mapping Tool")
    print("="*60)
    print("\nThis tool will:")
    print("  • Process ALL enabled regions automatically")
    print("  • Use a Bedrock LLM to intelligently map service quotas")
    print("  • Cache L-codes (same across regions)")
    print("="*60)
    print()

    # Step 2: Select Bedrock region with numbered selection
    with open('metadata/regions.yml', 'r', encoding='utf-8') as f:
        regions_data = yaml.safe_load(f)
        all_regions = regions_data.get('regions', [])

    print("Step 1: Select AWS region to use for Bedrock API calls:")
    for i, region in enumerate(all_regions, 1):
        print(f"  {i}. {region}")
    
    while True:
        try:
            choice = int(input(f"\nSelect region (1-{len(all_regions)}): "))
            if 1 <= choice <= len(all_regions):
                bedrock_region = all_regions[choice - 1]
                break
            print(f"Please enter a number between 1 and {len(all_regions)}")
        except ValueError:
            print("Please enter a valid number")
        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.", file=sys.stderr)
            sys.exit(1)

    print(f"\n✓ Bedrock calls will use region: {bedrock_region}\n")

    # Step 3: Select model with numbered selection
    model_options = [
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
        "au.anthropic.claude-haiku-4-5-20251001-v1:0",
        "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
        "global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "au.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "jp.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
    ]

    print("Step 2: Select model to power the intelligent service quota mapping:")
    for i, model in enumerate(model_options, 1):
        print(f"  {i}. {model}")
    
    while True:
        try:
            choice = int(input(f"\nSelect model (1-{len(model_options)}): "))
            if 1 <= choice <= len(model_options):
                model_id = model_options[choice - 1]
                break
            print(f"Please enter a number between 1 and {len(model_options)}")
        except ValueError:
            print("Please enter a valid number")
        except (KeyboardInterrupt, EOFError):
            print("\nSelection cancelled.", file=sys.stderr)
            sys.exit(1)

    # Extract profile prefix from model_id
    profile_prefix = model_id.split('.')[0]

    # Validate region compatibility
    region_mapping = {
        'us': ['us-east-1', 'us-east-2', 'us-west-1', 'us-west-2'],
        'eu': ['eu-central-1', 'eu-west-1', 'eu-west-2', 'eu-west-3'],
        'au': ['ap-southeast-1', 'ap-southeast-2'],
        'jp': ['ap-northeast-1'],
        'global': all_regions
    }

    compatible_regions = region_mapping.get(profile_prefix, all_regions)
    if bedrock_region not in compatible_regions:
        print(f"\n✗ Error: {bedrock_region} is not compatible with {profile_prefix} profile", file=sys.stderr)
        print(f"  Compatible regions: {', '.join(compatible_regions)}", file=sys.stderr)
        sys.exit(1)

    print(f"\n✓ Using model: {model_id}\n")

    # Write output to file (not stdout, so script can use TTY)
    with open('.quota_mapping_params.tmp', 'w', encoding='utf-8') as f:
        f.write(f"BEDROCK_REGION={bedrock_region}\n")
        f.write(f"MODEL_ID={model_id}\n")


if __name__ == "__main__":
    main()
