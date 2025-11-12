#!/usr/bin/env python3
"""Generate quota index CSV for validation"""

import boto3
import yaml
from defusedcsv import csv
import glob
import sys
from typing import Dict, Set


def load_all_fm_lists():
    """Load all fm-list YAML files with source region tracking per endpoint"""
    fm_files = glob.glob('metadata/fm-list-*.yml')
    
    if not fm_files:
        print("No fm-list files found in metadata/", file=sys.stderr)
        sys.exit(1)
    
    print(f"Found {len(fm_files)} fm-list files", file=sys.stderr)
    
    all_models = {}
    for fm_file in fm_files:
        # Extract region from filename: fm-list-{region}.yml
        region = fm_file.replace('metadata/fm-list-', '').replace('.yml', '')
        
        with open(fm_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            models = data.get('models', [])
            
            for model in models:
                model_id = model['model_id']
                if model_id not in all_models:
                    all_models[model_id] = model
                
                # Track source region per endpoint
                endpoints = model.get('endpoints', {})
                for endpoint_type, endpoint_data in endpoints.items():
                    if 'endpoints' not in all_models[model_id]:
                        all_models[model_id]['endpoints'] = {}
                    
                    # Store endpoint with its source region
                    all_models[model_id]['endpoints'][endpoint_type] = {
                        **endpoint_data,
                        '_source_region': region
                    }
    
    return all_models


def extract_quota_entries(models: Dict) -> list:
    """Extract all unique quota entries from models"""
    entries = []
    seen = set()
    
    for model_id, model in models.items():
        endpoints = model.get('endpoints', {})
        
        for endpoint_type, endpoint_data in endpoints.items():
            # Get source region from endpoint data
            source_region = endpoint_data.get('_source_region', 'us-east-1')
            quotas = endpoint_data.get('quotas', {})
            
            for quota_type, quota_code in quotas.items():
                if quota_code:
                    # Create unique key
                    key = (model_id, endpoint_type, quota_type, quota_code)
                    
                    if key not in seen:
                        seen.add(key)
                        entries.append({
                            'model_id': model_id,
                            'endpoint': endpoint_type,
                            'quota_type': quota_type,
                            'quota_code': quota_code,
                            'source_region': source_region
                        })
    
    return entries


def fetch_quota_details(quota_code: str, region: str) -> Dict:
    """Fetch quota details from AWS"""
    try:
        client = boto3.client('service-quotas', region_name=region)
        response = client.get_service_quota(
            ServiceCode='bedrock',
            QuotaCode=quota_code
        )
        
        return {
            'quota_name': response['Quota']['QuotaName'],
            'value': response['Quota']['Value']
        }
    except Exception as e:
        print(f"  Warning: Could not fetch {quota_code}: {e}", file=sys.stderr)
        return {'quota_name': 'ERROR', 'value': 0}


def cleanup_error_quotas(error_entries: list):
    """Remove ERROR quota mappings from YAML files"""
    if not error_entries:
        return
    
    print(f"\n⚠️  Found {len(error_entries)} ERROR entries. Cleaning up YAML files...", file=sys.stderr)
    
    # Group by source region
    by_region = {}
    for entry in error_entries:
        region = entry['source_region']
        if region not in by_region:
            by_region[region] = []
        by_region[region].append(entry)
    
    # Process each region file
    for region, entries in by_region.items():
        yaml_file = f'metadata/fm-list-{region}.yml'
        
        with open(yaml_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        modified = False
        for entry in entries:
            model_id = entry['model_id']
            endpoint = entry['endpoint']
            quota_type = entry['quota_type']
            
            # Find and update model
            for model in data.get('models', []):
                if model['model_id'] == model_id:
                    if 'endpoints' in model and endpoint in model['endpoints']:
                        if 'quotas' in model['endpoints'][endpoint]:
                            if quota_type in model['endpoints'][endpoint]['quotas']:
                                print(f"  Removing {model_id} -> {endpoint} -> {quota_type}", file=sys.stderr)
                                model['endpoints'][endpoint]['quotas'][quota_type] = None
                                modified = True
        
        if modified:
            with open(yaml_file, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            print(f"  ✓ Updated {yaml_file}", file=sys.stderr)


def generate_csv(entries: list, output_file: str):
    """Generate CSV file with quota details, cleanup errors immediately"""
    print(f"\nFetching quota details for {len(entries)} entries...", file=sys.stderr)
    
    error_entries = []
    valid_rows = []
    
    for i, entry in enumerate(entries, 1):
        print(f"  [{i}/{len(entries)}] {entry['model_id']} - {entry['endpoint']} - {entry['quota_type']}", 
              file=sys.stderr)
        
        details = fetch_quota_details(entry['quota_code'], entry['source_region'])
        
        if details['quota_name'] == 'ERROR':
            error_entries.append(entry)
        else:
            valid_rows.append([
                entry['model_id'],
                entry['endpoint'],
                entry['quota_type'],
                entry['quota_code'],
                details['quota_name']
            ])
    
    # Cleanup errors immediately
    if error_entries:
        cleanup_error_quotas(error_entries)
    
    # Write only valid rows to CSV
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['model_id', 'endpoint', 'quota_type', 'quota_code', 'quota_name'])
        writer.writerows(valid_rows)
    
    print(f"\n✓ Generated {output_file} with {len(valid_rows)} valid entries", file=sys.stderr)
    if error_entries:
        print(f"✓ Cleaned up {len(error_entries)} ERROR entries from YAML files", file=sys.stderr)


def main():
    print("Generating quota index for validation...\n", file=sys.stderr)
    
    # Load all models
    models = load_all_fm_lists()
    print(f"Loaded {len(models)} unique models\n", file=sys.stderr)
    
    # Extract quota entries
    entries = extract_quota_entries(models)
    print(f"Found {len(entries)} unique quota mappings\n", file=sys.stderr)
    
    # Generate CSV (cleanup happens inside if errors found)
    output_file = 'metadata/quota-index.csv'
    generate_csv(entries, output_file)
    
    print("\nQuota index generation complete!", file=sys.stderr)
    print(f"Review {output_file} to validate quota mappings", file=sys.stderr)


if __name__ == "__main__":
    main()
