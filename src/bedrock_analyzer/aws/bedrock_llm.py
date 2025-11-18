"""Bedrock LLM invocation for intelligent quota mapping"""

import boto3
import sys
from typing import Optional, Dict, List


def extract_common_name(region: str, model_id: str, fm_model_id: str) -> Optional[str]:
    """Extract common model name using LLM
    
    Args:
        region: AWS region for Bedrock
        model_id: Model ID to use for extraction
        fm_model_id: Foundation model ID to extract name from
        
    Returns:
        Common name or None
    """
    prompt = f"""Extract the base model family name from this model ID: {fm_model_id}

Examples:
- "amazon.nova-lite-v1:0" → "nova"
- "anthropic.claude-3-5-sonnet-20241022-v2:0" → "claude"
- "us.anthropic.claude-haiku-4-5-20251001-v1:0" → "claude"

Return ONLY the base family name, nothing else."""

    try:
        client = boto3.client('bedrock-runtime', region_name=region)
        response = client.converse(
            modelId=model_id,
            messages=[{'role': 'user', 'content': [{'text': prompt}]}],
            inferenceConfig={'maxTokens': 50, 'temperature': 0}
        )
        text = response['output']['message']['content'][0]['text'].strip().lower()
        return text
    except Exception as e:
        print(f"Error extracting common name: {e}", file=sys.stderr)
        return None


def extract_quota_codes(region: str, model_id: str, fm_model_id: str, 
                       endpoint_type: str, matching_quotas: List[Dict]) -> Optional[Dict]:
    """Extract quota codes for all matching quotas in one LLM call
    
    Args:
        region: AWS region for Bedrock
        model_id: Model ID to use for extraction
        fm_model_id: Foundation model ID being mapped
        endpoint_type: Endpoint type (base/us/eu/global/etc)
        matching_quotas: List of matching quota dicts with 'name' and 'code'
        
    Returns:
        Dict with tpm/rpm/tpd/concurrent codes or None
    """
    client = boto3.client('bedrock-runtime', region_name=region)
    
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
        'us': 'cross-region inference profile',
        'eu': 'cross-region inference profile',
        'jp': 'cross-region inference profile',
        'au': 'cross-region inference profile',
        'apac': 'cross-region inference profile',
        'ca': 'cross-region inference profile',
        'global': 'global inference profile'
    }.get(endpoint_type, endpoint_type)
    
    prompt = f"""For the Bedrock model "{fm_model_id}" with {endpoint_desc} endpoint, identify which quota codes correspond to:
- TPM (Tokens Per Minute)
- RPM (Requests Per Minute)  
- TPD (Tokens Per Day)
- Concurrent Requests (if available, some models use this instead of or in addition to RPM)

Available quotas:
{quotas_text}

IMPORTANT: Pay close attention to the EXACT details in the model ID "{fm_model_id}":

1. MODEL VARIANT - Match the specific variant name:
   - If model ID contains "nova-sonic", select quotas for "Nova Sonic" (NOT Nova Lite, Nova Pro, etc.)
   - If model ID contains "nova-lite", select quotas for "Nova Lite" (NOT Nova Sonic, Nova Pro, etc.)
   - If model ID contains "nova-canvas", select quotas for "Nova Canvas" (NOT Nova Lite, Nova Sonic, etc.)
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
Some models may ONLY have concurrent requests or RPM. In that case DO not infer the TPM, RPM, and TPD unless the model variant, version, and type matches.

Use the report_quota_mapping tool to provide the quota codes. If a quota type is not found, use null."""
    
    try:
        response = client.converse(
            modelId=model_id,
            messages=[{'role': 'user', 'content': [{'text': prompt}]}],
            toolConfig=tool_config,
            inferenceConfig={'maxTokens': 500, 'temperature': 0}
        )
        
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
        print(f"Error extracting quota codes: {e}", file=sys.stderr)
        return None
