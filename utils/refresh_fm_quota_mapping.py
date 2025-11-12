#!/usr/bin/env python3

import boto3
import yaml
import sys
import copy
from typing import Dict, List, Optional


class QuotaMapper:
    """Maps foundation models to their service quotas using Bedrock"""
    
    def __init__(self, bedrock_region: str, model_id: str, target_region: Optional[str] = None):
        self.bedrock_region = bedrock_region
        self.model_id = model_id
        self.target_region = target_region
        self.common_name_cache = {}  # Cache for model common names
        self.lcode_cache = {}  # Cache for L-codes: {(base_model_id, endpoint_type): {tpm: L-xxx, ...}}
        
    def run(self):
        """Main execution flow"""
        print(f"Using model for mapping: {self.model_id}")
        print(f"Bedrock API region: {self.bedrock_region}")
        if self.target_region:
            print(f"Target region filter: {self.target_region}")
        print()
        
        # Load all regions
        with open('metadata/regions.yml', 'r', encoding='utf-8') as f:
            regions_data = yaml.safe_load(f)
            all_regions = regions_data.get('regions', [])
        
        # Filter to target region if specified
        if self.target_region:
            if self.target_region in all_regions:
                all_regions = [self.target_region]
            else:
                print(f"Error: Region '{self.target_region}' not found in regions.yml")
                return
        
        print(f"Processing {len(all_regions)} region(s)...")
        print()
        
        for region in all_regions:
            print(f"Region: {region}")
            
            # Fetch quotas for this region
            quotas = self._fetch_quotas(region)
            print(f"  Found {len(quotas)} Bedrock quotas")
            
            # Load FM list
            fm_list = self._load_fm_list(region)
            if not fm_list:
                print(f"  ⊘ No FM list found, skipping")
                continue
            
            print(f"  Mapping quotas for {len(fm_list)} models...")
            
            # Map quotas for each FM
            updated_count = 0
            for i, fm in enumerate(fm_list, 1):
                model_id = fm['model_id']
                print(f"    [{i}/{len(fm_list)}] {model_id}...", end=' ', flush=True)
                
                # Determine endpoints to process
                endpoints_to_process = self._get_endpoints_to_process(fm)
                
                if not endpoints_to_process:
                    print("⊘ (no endpoints)")
                    continue
                
                # Get common name (once per model)
                common_name = self._get_common_name(model_id)
                if not common_name:
                    print("✗ (no common name)")
                    continue
                
                # Process each endpoint
                endpoints_data = {}
                for endpoint_type in endpoints_to_process:
                    # Skip 'cross-region' - it's only for caching, not for saving
                    if endpoint_type == 'cross-region':
                        continue
                    
                    quota_mapping = self._get_quota_mapping(
                        region, model_id, common_name, endpoint_type, quotas
                    )
                    if quota_mapping:
                        endpoints_data[endpoint_type] = {'quotas': quota_mapping}
                
                if endpoints_data:
                    fm['endpoints'] = endpoints_data
                    updated_count += 1
                    endpoint_summary = ', '.join(endpoints_data.keys())
                    print(f"✓ ({endpoint_summary})")
                else:
                    print("✗ (no mappings)")
            
            # Save updated FM list
            self._save_fm_list(region, fm_list)
            print(f"  ✓ Updated {updated_count} models")
            print()
    
    def _get_endpoints_to_process(self, fm: Dict) -> List[str]:
        """Determine which endpoints to process for a model"""
        endpoints = []
        inference_types = fm.get('inference_types', [])
        
        # Add base if ON_DEMAND supported
        if 'ON_DEMAND' in inference_types:
            endpoints.append('base')
        
        # Add inference profiles
        if 'INFERENCE_PROFILE' in inference_types:
            profiles = fm.get('inference_profiles', [])
            
            # Group regional profiles as 'cross-region'
            regional_profiles = [p for p in profiles if p in ['us', 'eu', 'jp', 'au', 'apac', 'ca']]
            if regional_profiles:
                # Only need to process once for cross-region
                endpoints.append('cross-region')
                # But store all regional variants
                for profile in regional_profiles:
                    if profile not in endpoints:
                        endpoints.append(profile)
            
            # Add global if present
            if 'global' in profiles:
                endpoints.append('global')
        
        return endpoints
    
    def _get_quota_mapping(self, region: str, model_id: str, common_name: str, 
                          endpoint_type: str, quotas: List[Dict]) -> Optional[Dict]:
        """Get quota mapping for a specific endpoint"""
        
        # Check cache first
        cache_key = (model_id, endpoint_type if endpoint_type in ['base', 'cross-region', 'global'] else 'cross-region')
        if cache_key in self.lcode_cache:
            # Return a deep copy to avoid YAML anchors/aliases
            return copy.deepcopy(self.lcode_cache[cache_key])
        
        # Find matching quotas
        matching_quotas = self._find_matching_quotas(quotas, common_name, endpoint_type)
        if not matching_quotas:
            return None
        
        # Extract quota codes using Bedrock
        quota_mapping = self._extract_quota_codes(model_id, endpoint_type, matching_quotas)
        
        # Cache the result
        if quota_mapping:
            self.lcode_cache[cache_key] = quota_mapping
        
        return quota_mapping
    
    def _fetch_quotas(self, region: str) -> List[Dict]:
        """Fetch service quotas for Bedrock in region"""
        client = boto3.client('service-quotas', region_name=region)
        quotas = []
        
        try:
            paginator = client.get_paginator('list_service_quotas')
            for page in paginator.paginate(ServiceCode='bedrock'):
                quotas.extend(page['Quotas'])
        except Exception as e:
            print(f"  ✗ Error fetching quotas: {e}")
        
        return quotas
    
    def _load_fm_list(self, region: str) -> Optional[List[Dict]]:
        """Load FM list for region"""
        try:
            with open(f'metadata/fm-list-{region}.yml', 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                return data.get('models', [])
        except FileNotFoundError:
            return None
    
    def _save_fm_list(self, region: str, fm_list: List[Dict]):
        """Save updated FM list"""
        with open(f'metadata/fm-list-{region}.yml', 'w', encoding='utf-8') as f:
            yaml.dump({'models': fm_list}, f, default_flow_style=False, sort_keys=False)
    
    def _get_common_name(self, model_id: str) -> Optional[str]:
        """Get common name for model using Bedrock (with caching)"""
        if model_id in self.common_name_cache:
            return self.common_name_cache[model_id]
        
        bedrock_runtime = boto3.client('bedrock-runtime', region_name=self.bedrock_region)
        
        prompt = f"""Extract the base model family name from this Bedrock model ID: {model_id}

Rules:
1. Return ONLY the core family name (e.g., "nova", "claude", "llama", "gpt")
2. Remove ALL variants, sizes, and version suffixes
3. For compound names, use the shortest recognizable form

Examples:
- amazon.nova-lite-v1:0 → nova
- amazon.nova-pro-v1:0 → nova
- anthropic.claude-haiku-4-5-20251001-v1:0 → haiku
- anthropic.claude-3-haiku-20240307-v1:0 → haiku
- anthropic.claude-sonnet-4-5-20250929-v1:0 → sonnet
- meta.llama3-1-405b-instruct-v1:0 → llama
- cohere.command-r-plus-v1:0 → command
- qwen.qwen3-coder-30b-a3b-v1:0 → qwen
- qwen.qwen3-32b-v1:0 → qwen
- openai.gpt-oss-120b-1:0 → gpt
- openai.gpt-oss-20b-1:0 → gpt

Return ONLY the base family name, nothing else."""
        
        try:
            response = bedrock_runtime.converse(
                modelId=self.model_id,
                messages=[{
                    'role': 'user',
                    'content': [{'text': prompt}]
                }],
                inferenceConfig={'maxTokens': 20, 'temperature': 0}
            )
            
            common_name = response['output']['message']['content'][0]['text'].strip().lower()
            self.common_name_cache[model_id] = common_name
            return common_name
        
        except Exception as e:
            return None
    
    def _find_matching_quotas(self, quotas: List[Dict], common_name: str, endpoint_type: str) -> List[Dict]:
        """Find quotas matching the common name and endpoint type"""
        matching = []
        
        for quota in quotas:
            quota_name = quota.get('QuotaName', '').lower()
            
            # Check if quota name contains the common name
            if common_name not in quota_name:
                continue
            
            # Filter by endpoint type
            if endpoint_type == 'base':
                # On-demand quotas contain "on-demand"
                if 'on-demand' in quota_name:
                    matching.append({
                        'name': quota['QuotaName'],
                        'code': quota['QuotaCode'],
                        'value': quota.get('Value', 0)
                    })
            elif endpoint_type == 'cross-region' or endpoint_type in ['us', 'eu', 'jp', 'au', 'apac', 'ca']:
                # Cross-region quotas contain "cross-region"
                if 'cross-region' in quota_name:
                    matching.append({
                        'name': quota['QuotaName'],
                        'code': quota['QuotaCode'],
                        'value': quota.get('Value', 0)
                    })
            elif endpoint_type == 'global':
                # Global quotas contain "global"
                if 'global' in quota_name:
                    matching.append({
                        'name': quota['QuotaName'],
                        'code': quota['QuotaCode'],
                        'value': quota.get('Value', 0)
                    })
        
        return matching
    
    def _extract_quota_codes(self, model_id: str, endpoint_type: str, matching_quotas: List[Dict]) -> Optional[Dict]:
        """Extract TPM/RPM/TPD quota codes using Bedrock with tool use"""
        bedrock_runtime = boto3.client('bedrock-runtime', region_name=self.bedrock_region)
        
        # Define tool for structured output
        tool_config = {
            'tools': [{
                'toolSpec': {
                    'name': 'report_quota_mapping',
                    'description': 'Report the quota codes for TPM, RPM, TPD, and Concurrent Requests',
                    'inputSchema': {
                        'json': {
                            'type': 'object',
                            'properties': {
                                'tpm_quota_code': {
                                    'type': ['string', 'null'],
                                    'description': 'Quota code for Tokens Per Minute (TPM), or null if not found'
                                },
                                'rpm_quota_code': {
                                    'type': ['string', 'null'],
                                    'description': 'Quota code for Requests Per Minute (RPM), or null if not found'
                                },
                                'tpd_quota_code': {
                                    'type': ['string', 'null'],
                                    'description': 'Quota code for Tokens Per Day (TPD), or null if not found'
                                },
                                'concurrent_requests_quota_code': {
                                    'type': ['string', 'null'],
                                    'description': 'Quota code for Concurrent Requests, or null if not found'
                                }
                            },
                            'required': ['tpm_quota_code', 'rpm_quota_code', 'tpd_quota_code', 'concurrent_requests_quota_code']
                        }
                    }
                }
            }],
            'toolChoice': {'tool': {'name': 'report_quota_mapping'}}
        }
        
        quotas_text = "\n".join([f"- {q['name']} (code: {q['code']})" for q in matching_quotas])
        
        endpoint_desc = {
            'base': 'on-demand',
            'cross-region': 'cross-region inference profile',
            'global': 'global inference profile'
        }.get(endpoint_type, endpoint_type)
        
        prompt = f"""For the Bedrock model "{model_id}" with {endpoint_desc} endpoint, identify which quota codes correspond to:
- TPM (Tokens Per Minute)
- RPM (Requests Per Minute)  
- TPD (Tokens Per Day)
- Concurrent Requests (if available, some models use this instead of or in addition to RPM)

Available quotas:
{quotas_text}

IMPORTANT: Pay close attention to the EXACT details in the model ID "{model_id}":

1. MODEL VARIANT - Match the specific variant name:
   - If model ID contains "nova-sonic", select quotas for "Nova Sonic" (NOT Nova Lite, Nova Pro, etc.)
   - If model ID contains "nova-lite", select quotas for "Nova Lite" (NOT Nova Sonic, Nova Pro, etc.)
   - If model ID contains "claude-haiku", select quotas for "Haiku" (NOT Sonnet, Opus, etc.)

2. VERSION NUMBER - Match the exact version:
   - If model ID contains "v1:0" or date like "20240620", select V1 quotas (NOT V2)
   - If model ID contains "v2:0" or date like "20241022", select V2 quotas (NOT V1)
   - Version in model ID MUST match version in quota name

3. MODEL TYPE - Understand model type indicators:
   - "tg1" or "tg" = Text Generation models (NOT image models)
   - "text" = Text models
   - "image" = Image generation models
   - "embed" = Embedding models

Match the quota name to the specific model variant, version, and type in the model ID.
Some models may ONLY have concurrent. In that case DO not infer the TPM, RPM, and TPD unless the model variant, version, and type matches.

Use the report_quota_mapping tool to provide the quota codes. If a quota type is not found, use null."""
        
        try:
            response = bedrock_runtime.converse(
                modelId=self.model_id,
                messages=[{
                    'role': 'user',
                    'content': [{'text': prompt}]
                }],
                toolConfig=tool_config,
                inferenceConfig={'maxTokens': 500, 'temperature': 0}
            )
            
            # Extract tool use result
            content = response['output']['message']['content']
            for block in content:
                if 'toolUse' in block:
                    tool_input = block['toolUse']['input']
                    return {
                        'tpm': tool_input.get('tpm_quota_code'),
                        'rpm': tool_input.get('rpm_quota_code'),
                        'tpd': tool_input.get('tpd_quota_code'),
                        'concurrent': tool_input.get('concurrent_requests_quota_code')
                    }
            
            return None
        
        except Exception as e:
            return None


def main():
    if len(sys.argv) < 3:
        print("Usage: python refresh_fm_quota_mapping.py <bedrock_region> <model_id> [target_region]")
        sys.exit(1)
    
    bedrock_region = sys.argv[1]
    model_id = sys.argv[2]
    target_region = sys.argv[3] if len(sys.argv) > 3 else None
    
    mapper = QuotaMapper(bedrock_region, model_id, target_region)
    mapper.run()


if __name__ == "__main__":
    main()
