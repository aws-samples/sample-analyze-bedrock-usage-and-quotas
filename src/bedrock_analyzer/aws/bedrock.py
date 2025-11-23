"""AWS Bedrock service operations"""

import boto3
import sys
from typing import List, Dict, Optional


# Quota keyword constants
QUOTA_KEYWORD_ON_DEMAND = 'on-demand'
QUOTA_KEYWORD_CROSS_REGION = 'cross-region'
QUOTA_KEYWORD_GLOBAL = 'global'

# Single source of truth for inference profile prefix mappings
# Add new prefixes here - all derived constants will update automatically
FM_PREFIX_MAPPING = [
    {
        'prefix': 'base',
        'quota_keyword': QUOTA_KEYWORD_ON_DEMAND,
        'description': 'on-demand',
        'is_regional': False
    },
    {
        'prefix': 'us',
        'quota_keyword': QUOTA_KEYWORD_CROSS_REGION,
        'description': 'cross-region inference profile',
        'is_regional': True
    },
    {
        'prefix': 'eu',
        'quota_keyword': QUOTA_KEYWORD_CROSS_REGION,
        'description': 'cross-region inference profile',
        'is_regional': True
    },
    {
        'prefix': 'jp',
        'quota_keyword': QUOTA_KEYWORD_CROSS_REGION,
        'description': 'cross-region inference profile',
        'is_regional': True
    },
    {
        'prefix': 'au',
        'quota_keyword': QUOTA_KEYWORD_CROSS_REGION,
        'description': 'cross-region inference profile',
        'is_regional': True
    },
    {
        'prefix': 'apac',
        'quota_keyword': QUOTA_KEYWORD_CROSS_REGION,
        'description': 'cross-region inference profile',
        'is_regional': True
    },
    {
        'prefix': 'ca',
        'quota_keyword': QUOTA_KEYWORD_CROSS_REGION,
        'description': 'cross-region inference profile',
        'is_regional': True
    },
    {
        'prefix': 'global',
        'quota_keyword': QUOTA_KEYWORD_GLOBAL,
        'description': 'global inference profile',
        'is_regional': False
    }
]

# Derived constants - automatically generated from FM_PREFIX_MAPPING
ENDPOINT_QUOTA_KEYWORDS = {m['prefix']: m['quota_keyword'] for m in FM_PREFIX_MAPPING}
ENDPOINT_DESCRIPTIONS = {m['prefix']: m['description'] for m in FM_PREFIX_MAPPING}
REGIONAL_PROFILE_PREFIXES = [m['prefix'] for m in FM_PREFIX_MAPPING if m['is_regional']]
DEFAULT_REGION_PREFIX_MAP = {m['prefix']: m['prefix'] for m in FM_PREFIX_MAPPING if m['is_regional']}
DEFAULT_REGION_PREFIX_MAP['ap'] = 'apac'  # Special case: 'ap' region prefix maps to 'apac' system profile



def fetch_foundation_models(region: str) -> Optional[List[Dict]]:
    """Fetch foundation models for a region
    
    Args:
        region: AWS region name
        
    Returns:
        List of model dictionaries or None if access denied
    """
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


def fetch_all_inference_profiles(region: str) -> List[Dict]:
    """Fetch ALL inference profiles in region
    This fetches only system inference profile, not application inference profile
    The purpose is to list down the available system inference profiles for a given FM.
    
    Args:
        region: AWS region name
        
    Returns:
        List of inference profile dictionaries
    """
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
    """Build mapping: model_id → [profile_prefixes]
    Basically given a list of inference profiles (each profile with the FM it is for), it builds a map with FM key first, then list of profiles for each FM.
    
    Args:
        profiles: List of inference profile dictionaries
        
    Returns:
        Dictionary mapping model IDs to list of profile prefixes
    """
    profile_map = {}
    
    for profile in profiles:
        profile_id = profile.get('inferenceProfileId', '')
        
        # Extract prefix (us, eu, jp, au, apac, global)
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


def get_inference_profile_arn(bedrock_client, model_id: str, profile_prefix: str) -> Optional[str]:
    """Get the ARN of a system-defined inference profile
    
    Args:
        bedrock_client: Boto3 Bedrock client
        model_id: Model ID
        profile_prefix: Profile prefix (us, eu, etc.)
        
    Returns:
        Profile ARN or None if not found
    """
    try:
        target_profile_id = f"{profile_prefix}.{model_id}"
        next_token = None
        
        while True:
            params = {'maxResults': 1000}
            if next_token:
                params['nextToken'] = next_token
            
            response = bedrock_client.list_inference_profiles(**params)
            
            for profile in response.get('inferenceProfileSummaries', []):
                if profile.get('inferenceProfileId') == target_profile_id:
                    return profile.get('inferenceProfileArn')
            
            next_token = response.get('nextToken')
            if not next_token:
                break
        
        return None
    except Exception as e:
        print(f"Error fetching inference profile: {e}", file=sys.stderr)
        return None


def create_application_inference_profile(bedrock_client, model_id: str, profile_prefix: Optional[str], region: str, profile_name: str) -> Optional[str]:
    """Create an application inference profile
    
    Args:
        bedrock_client: Boto3 Bedrock client
        model_id: Model ID
        profile_prefix: Profile prefix or None for base model
        region: AWS region
        profile_name: Name for the application profile
        
    Returns:
        Profile ARN or None if creation failed
    """
    try:
        # Determine source ARN
        if profile_prefix and profile_prefix != 'null':
            source_arn = get_inference_profile_arn(bedrock_client, model_id, profile_prefix)
            if not source_arn:
                print(f"Could not find system profile for {profile_prefix}.{model_id}", file=sys.stderr)
                return None
        else:
            # Base model ARN
            source_arn = f"arn:aws:bedrock:{region}::foundation-model/{model_id}"
        
        # Create application profile
        response = bedrock_client.create_inference_profile(
            inferenceProfileName=profile_name,
            modelSource={'copyFrom': source_arn}
        )
        
        return response.get('inferenceProfileArn')
        
    except Exception as e:
        print(f"Error creating application profile: {e}", file=sys.stderr)
        return None
