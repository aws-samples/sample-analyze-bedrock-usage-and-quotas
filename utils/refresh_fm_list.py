#!/usr/bin/env python3
"""Refresh foundation model lists with inference profiles"""

import boto3
import yaml
import sys
from typing import List, Dict, Optional


def fetch_foundation_models(region: str) -> Optional[List[Dict]]:
    """Fetch foundation models for a region"""
    try:
        bedrock = boto3.client('bedrock', region_name=region)
        response = bedrock.list_foundation_models()
        
        models = []
        for model in response.get('modelSummaries', []):
            models.append({
                'model_id': model['modelId'],
                'provider': model['providerName'],
                'inference_types': model.get('inferenceTypesSupported', [])
            })
        
        return models
    
    except Exception as e:
        error_msg = str(e)
        if any(x in error_msg for x in ['AccessDenied', 'UnauthorizedOperation', 'not enabled', 'not subscribed']):
            print(f"  ⊘ Skipping {region} (access denied or not enabled)", file=sys.stderr)
        else:
            print(f"  ✗ Failed to fetch models for {region}: {e}", file=sys.stderr)
        return None


def fetch_inference_profiles(region: str, model_id: str) -> List[str]:
    """Fetch available inference profiles for a model (DEPRECATED - use build_profile_map instead)"""
    try:
        bedrock = boto3.client('bedrock', region_name=region)
        response = bedrock.list_inference_profiles()
        
        profiles = []
        for profile in response.get('inferenceProfileSummaries', []):
            # Check if this profile is for our model
            for model in profile.get('models', []):
                if model.get('modelArn', '').endswith(model_id):
                    # Extract profile prefix (us, eu, jp, au, global)
                    profile_id = profile.get('inferenceProfileId', '')
                    if '.' in profile_id:
                        prefix = profile_id.split('.')[0]
                        if prefix not in profiles:
                            profiles.append(prefix)
        
        return sorted(profiles)
    
    except Exception as e:
        # Inference profiles might not be available in all regions
        return []


def fetch_all_inference_profiles(region: str) -> List[Dict]:
    """Fetch ALL inference profiles in region (called once)"""
    try:
        bedrock = boto3.client('bedrock', region_name=region)
        
        # Use paginator to handle large result sets
        paginator = bedrock.get_paginator('list_inference_profiles')
        all_profiles = []
        
        for page in paginator.paginate():
            all_profiles.extend(page.get('inferenceProfileSummaries', []))
        
        return all_profiles
    
    except Exception as e:
        # Inference profiles might not be available in all regions
        return []


def build_profile_map(profiles: List[Dict]) -> Dict[str, List[str]]:
    """Build mapping: model_id → [profile_prefixes]"""
    profile_map = {}
    
    for profile in profiles:
        profile_id = profile.get('inferenceProfileId', '')
        
        # Extract prefix (us, eu, jp, au, global)
        if '.' not in profile_id:
            continue
        prefix = profile_id.split('.')[0]
        
        # Add this prefix to all models in this profile
        for model in profile.get('models', []):
            model_arn = model.get('modelArn', '')
            
            # Extract model_id from ARN (format: arn:aws:bedrock:region::foundation-model/model-id)
            if ':foundation-model/' in model_arn:
                model_id = model_arn.split(':foundation-model/')[-1]
                
                if model_id not in profile_map:
                    profile_map[model_id] = []
                if prefix not in profile_map[model_id]:
                    profile_map[model_id].append(prefix)
    
    # Sort prefixes for consistency
    for model_id in profile_map:
        profile_map[model_id] = sorted(profile_map[model_id])
    
    return profile_map


def load_existing_models(filepath: str) -> Dict[str, Dict]:
    """Load existing models from YAML file"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            if data and 'models' in data:
                return {m['model_id']: m for m in data['models']}
    except (FileNotFoundError, yaml.YAMLError, KeyError) as e:
        # File doesn't exist or is invalid - return empty dict to start fresh
        print(f"Warning: Could not load existing models from {filepath}: {e}")
    return {}


def save_models(filepath: str, models: List[Dict]):
    """Save models to YAML file"""
    sorted_models = sorted(models, key=lambda x: (x['provider'], x['model_id']))
    
    with open(filepath, 'w', encoding='utf-8') as f:
        yaml.dump({'models': sorted_models}, f, default_flow_style=False, sort_keys=False)


def refresh_region(region: str):
    """Refresh foundation models for a region"""
    print(f"\nProcessing region: {region}", file=sys.stderr)
    
    output_file = f'metadata/fm-list-{region}.yml'
    
    # Fetch foundation models
    models = fetch_foundation_models(region)
    if models is None:
        return
    
    # Load existing models
    existing_models = load_existing_models(output_file)
    
    # Fetch ALL inference profiles once (optimized)
    print(f"  Fetching inference profiles...", file=sys.stderr)
    all_profiles = fetch_all_inference_profiles(region)
    profile_map = build_profile_map(all_profiles)
    print(f"  Found {len(profile_map)} models with inference profiles", file=sys.stderr)
    
    # Apply inference profiles to models
    for model in models:
        model_id = model['model_id']
        
        # Check if this model has inference profiles (always check, not conditional)
        if model_id in profile_map:
            model['inference_profiles'] = profile_map[model_id]
            
            # Add INFERENCE_PROFILE to inference_types if not present
            if 'INFERENCE_PROFILE' not in model.get('inference_types', []):
                model['inference_types'].append('INFERENCE_PROFILE')
        
        # Preserve existing endpoint quotas if they exist
        if model_id in existing_models and 'endpoints' in existing_models[model_id]:
            model['endpoints'] = existing_models[model_id]['endpoints']
        # Initialize endpoints.base.quotas for ON_DEMAND models if not present
        elif 'ON_DEMAND' in model.get('inference_types', []):
            model['endpoints'] = {
                'base': {
                    'quotas': {
                        'tpm': None,
                        'rpm': None,
                        'tpd': None,
                        'concurrent': None
                    }
                }
            }
    
    # Merge with existing
    for model in models:
        existing_models[model['model_id']] = model
    
    # Save
    save_models(output_file, list(existing_models.values()))
    
    model_count = len(existing_models)
    print(f"  ✓ Saved {model_count} models to {output_file}", file=sys.stderr)


def main():
    import os
    
    specific_region = sys.argv[1] if len(sys.argv) > 1 else None
    
    # Check if regions.yml exists
    if not os.path.exists('metadata/regions.yml') or os.path.getsize('metadata/regions.yml') == 0:
        print("Regions list not found or empty. Run refresh-regions.sh first.", file=sys.stderr)
        sys.exit(1)
    
    # Load regions
    with open('metadata/regions.yml', 'r', encoding='utf-8') as f:
        regions_data = yaml.safe_load(f)
        all_regions = regions_data.get('regions', [])
    
    # Determine which regions to process
    if specific_region:
        regions = [specific_region]
        print(f"Fetching foundation models for region: {specific_region}", file=sys.stderr)
    else:
        regions = all_regions
        print("Fetching foundation models for all regions...", file=sys.stderr)
    
    # Process each region
    for region in regions:
        refresh_region(region)
    
    print("\nFoundation model list refresh complete!", file=sys.stderr)


if __name__ == "__main__":
    main()
