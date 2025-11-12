#!/usr/bin/env python3

import boto3
import json
import numpy as np
from datetime import datetime, timedelta, timezone
import os
import sys
import yaml
from jinja2 import Template
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from threading import Lock

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'
)
logger = logging.getLogger(__name__)


class UserInputs:
    """Handles interactive user input collection"""
    
    def __init__(self):
        self.account = None
        self.region = None
        self.models = []
        self.granularity_config = { # The aggregation granularity for different metrics window/period
            '1hour': 300,   # 5 minutes
            '1day': 300,    # 5 minutes
            '7days': 300,   # 5 minutes
            '14days': 300,  # 5 minutes
            '30days': 300   # 5 minutes
        }
    
    def collect(self):
        """Interactive dialog to collect user inputs"""
        logger.info("This tool calculates token usage statistics (p50, p90, TPM, TPD, RPM) and throttling metrics for Bedrock models in your AWS account.")
        logger.info("Statistics will be generated for: 1 hour, 1 day, 7 days, 14 days, and 30 days.")
        print()

        self.account = self._get_current_account()
        confirm = input(f"AWS account: {self.account} - Continue? (y/n): ").lower()
        if confirm != 'y':
            sys.exit(1)
        
        # Region selection
        self.region = self._select_region()
        
        # Ensure FM list exists for selected region
        self._ensure_fm_list(self.region)
        
        # Granularity configuration
        self._configure_granularity()
        
        # Model selection loop
        while True:
            model_config = self._select_model(self.region)
            self.models.append(model_config)
            
            add_more = input("\nAdd another model? (y/n): ").lower()
            if add_more != 'y':
                break
    
    def _get_current_account(self):
        """Get current AWS account ID"""
        logger.info("Getting AWS account ID...")
        try:
            sts = boto3.client('sts')
            account = sts.get_caller_identity()['Account']
            logger.info(f"  Account: {account}")
            return account
        except Exception as e:
            logger.error(f"Failed to get AWS account ID: {e}")
            logger.error("Please configure AWS credentials in your current machine.")
            sys.exit(1)
    
    def _select_region(self):
        """Select region with numbered list"""
        regions = self._load_regions()
        
        logger.info("\nAvailable regions:")
        for i, region in enumerate(regions, 1):
            logger.info(f"  {i}. {region}")
        logger.info("\nHint: If your region is not listed, run ./scripts/refresh-regions.sh")
        
        while True:
            try:
                choice = int(input(f"\nSelect region (1-{len(regions)}): "))
                if 1 <= choice <= len(regions):
                    return regions[choice - 1]
                logger.info(f"Please enter a number between 1 and {len(regions)}")
            except ValueError:
                logger.info("Please enter a valid number")
    
    def _select_model(self, region):
        """Select model with numbered lists"""
        fm_list = self._load_fm_list(region)
        
        # Get unique providers
        providers = sorted(set(m['provider'] for m in fm_list))
        
        # Select provider
        logger.info("\nAvailable providers:")
        for i, provider in enumerate(providers, 1):
            logger.info(f"  {i}. {provider}")
        logger.info(f"\nHint: To refresh models, run ./scripts/refresh-fm-list.sh {region}")
        logger.info(f"      then ./scripts/refresh-fm-quotas-mapping.sh {region}")
        
        while True:
            try:
                choice = int(input(f"\nSelect provider (1-{len(providers)}): "))
                if 1 <= choice <= len(providers):
                    provider = providers[choice - 1]
                    break
                logger.info(f"Please enter a number between 1 and {len(providers)}")
            except ValueError:
                logger.info("Please enter a valid number")
        
        # Filter models by provider
        provider_models = [m for m in fm_list if m['provider'] == provider]
        
        # Select model
        logger.info(f"\nAvailable {provider} models:")
        for i, model in enumerate(provider_models, 1):
            logger.info(f"  {i}. {model['model_id']}")
        logger.info(f"\nHint: To refresh models, run ./scripts/refresh-fm-list.sh {region}")
        logger.info(f"      then ./scripts/refresh-fm-quotas-mapping.sh {region}")
        
        while True:
            try:
                choice = int(input(f"\nSelect model (1-{len(provider_models)}): "))
                if 1 <= choice <= len(provider_models):
                    selected_model = provider_models[choice - 1]
                    model_id = selected_model['model_id']
                    break
                logger.info(f"Please enter a number between 1 and {len(provider_models)}")
            except ValueError:
                logger.info("Please enter a valid number")
        
        # Get inference types for selected model
        inference_types = selected_model.get('inference_types', [])
        
        # Determine profile prefix
        profile_prefix = self._select_profile_prefix(inference_types)
        
        return {
            'model_id': model_id,
            'profile_prefix': profile_prefix
        }
    
    def _select_profile_prefix(self, inference_types):
        """Select inference profile prefix based on supported types"""
        # If only INFERENCE_PROFILE is supported, profile is required
        if inference_types == ['INFERENCE_PROFILE']:
            logger.info("\nThis model only supports inference profiles.")
            choices = ['us', 'eu', 'ap', 'global']
        else:
            choices = ['us', 'eu', 'ap', 'global', 'None (base model)']
        
        logger.info("\nAvailable inference profile prefixes:")
        for i, choice in enumerate(choices, 1):
            logger.info(f"  {i}. {choice}")
        
        while True:
            try:
                selection = int(input(f"\nSelect profile prefix (1-{len(choices)}): "))
                if 1 <= selection <= len(choices):
                    choice = choices[selection - 1]
                    return None if 'None' in choice else choice
                logger.info(f"Please enter a number between 1 and {len(choices)}")
            except ValueError:
                logger.info("Please enter a valid number")
    
    def _configure_granularity(self):
        """Configure data granularity for each time period"""
        logger.info("\n" + "="*60)
        logger.info("DATA GRANULARITY CONFIGURATION")
        logger.info("="*60)
        logger.info("Default granularity settings:")
        logger.info("  1 hour:  5 minutes")
        logger.info("  1 day:   5 minutes")
        logger.info("  7 days:  5 minutes")
        logger.info("  14 days: 5 minutes")
        logger.info("  30 days: 5 minutes")
        print()
        
        use_default = input("Use default granularity settings? (y/n): ").lower()
        if use_default == 'y':
            return
        
        logger.info("\nConfigure granularity for each period:")
        logger.info("(Finer granularity = more detail but slower fetching)")
        logger.info("Note: Longer periods cannot use finer granularity than shorter periods")
        print()
        
        # Track minimum granularity and previous period info
        min_granularity = 60
        prev_period_name = None
        prev_granularity_label = None
        
        # Configure each period in order
        periods = [
            ('1 HOUR', '1hour', [('1 minute', 60), ('5 minutes', 300)]),
            ('1 DAY', '1day', [('1 minute', 60), ('5 minutes', 300), ('1 hour', 3600)]),
            ('7 DAYS', '7days', [('1 minute', 60), ('5 minutes', 300), ('1 hour', 3600)]),
            ('14 DAYS', '14days', [('1 minute', 60), ('5 minutes', 300), ('1 hour', 3600)]),
            ('30 DAYS', '30days', [('1 minute', 60), ('5 minutes', 300), ('1 hour', 3600)])
        ]
        
        for period_name, period_key, options in periods:
            selected_seconds = self._select_granularity(
                period_name, options, min_granularity, 
                prev_period_name, prev_granularity_label
            )
            self.granularity_config[period_key] = selected_seconds
            
            # Update tracking for next iteration
            min_granularity = max(min_granularity, selected_seconds)
            prev_period_name = period_name
            prev_granularity_label = next(label for label, sec in options if sec == selected_seconds)
        
        logger.info("\n" + "="*60)
        logger.info("Granularity configuration complete!")
        logger.info("="*60)
    
    def _select_granularity(self, period_name, options, min_granularity, prev_period_name=None, prev_granularity_label=None):
        """Select granularity with strikethrough for unavailable options"""
        logger.info(f"\n{period_name} period:")
        
        available_options = []
        for i, (label, seconds) in enumerate(options, 1):
            if seconds < min_granularity:
                # Strikethrough with descriptive message
                if prev_period_name and prev_granularity_label:
                    reason = f"not available as you picked {prev_granularity_label} for {prev_period_name} window"
                else:
                    reason = "unavailable - too fine"
                logger.info(f"  {i}. \033[9m{label}\033[0m ({reason})")
            else:
                logger.info(f"  {i}. {label}")
                available_options.append(i)
        
        # Get valid choice
        while True:
            try:
                choice = int(input(f"Select granularity (1-{len(options)}): "))
                if choice in available_options:
                    return options[choice - 1][1]  # Return seconds
                logger.info("Please select an available (non-strikethrough) option")
            except ValueError:
                logger.info("Please enter a valid number")
    
    def _get_choice(self, min_val, max_val, prompt):
        """Helper to get valid numeric choice"""
        while True:
            try:
                choice = int(input(prompt))
                if min_val <= choice <= max_val:
                    return choice
                logger.info(f"Please enter a number between {min_val} and {max_val}")
            except ValueError:
                logger.info("Please enter a valid number")
    
    def _load_regions(self):
        """Load regions from yml, refresh if needed"""
        regions_file = 'metadata/regions.yml'
        
        if not os.path.exists(regions_file) or os.path.getsize(regions_file) == 0:
            logger.error("Regions list not found: metadata/regions.yml")
            logger.error("Please run: ./scripts/refresh-regions.sh")
            sys.exit(1)
        
        with open(regions_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            return data.get('regions', [])
    
    def _ensure_fm_list(self, region):
        """Ensure FM list exists for region"""
        # Validate region format (AWS regions are alphanumeric with hyphens)
        if not region or not all(c.isalnum() or c == '-' for c in region):
            raise ValueError(f"Invalid region format: {region}")
        
        fm_file = f'metadata/fm-list-{region}.yml'
        
        if not os.path.exists(fm_file) or os.path.getsize(fm_file) == 0:
            logger.error(f"Foundation model list not found: {fm_file}")
            logger.error(f"Please run: ./scripts/refresh-fm-list.sh {region}")
            sys.exit(1)
    
    def _load_fm_list(self, region):
        """Load foundation models for region"""
        fm_file = f'metadata/fm-list-{region}.yml'
        
        with open(fm_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            return data.get('models', [])

class InferenceProfileFetcher:
    """Handles inference profile discovery"""
    
    def __init__(self, bedrock_client):
        self.bedrock_client = bedrock_client
        self.prefix_map = self._discover_prefix_map()
        self._all_profiles_cache = None  # Cache for all profiles
    
    def _discover_prefix_map(self):
        """Dynamically discover region prefix to system profile prefix mapping
    
        Maps AWS region prefixes (us, eu, ap) to Bedrock system profile prefixes (us, eu, apac).
        Example: 'ap' regions → 'apac' system profiles. Used to construct correct profile IDs.
        """
        try:
            response = self.bedrock_client.list_inference_profiles(maxResults=1000)
        
            # Collect all profiles with pagination
            all_profiles = []
            while True:
                all_profiles.extend(response['inferenceProfileSummaries'])
                
                if 'nextToken' in response:
                    response = self.bedrock_client.list_inference_profiles(
                        maxResults=1000,
                        nextToken=response['nextToken']
                    )
                else:
                    break
            
            # Build mapping from region prefixes to system profile prefixes
            prefix_map = {}
            for profile in all_profiles:
                if profile['type'] == 'SYSTEM_DEFINED' and '.' in profile['inferenceProfileId']:
                    system_prefix = profile['inferenceProfileId'].split('.')[0]
                    model_arns = [m['modelArn'] for m in profile['models']]
                    
                    if len(model_arns) > 1:
                        regions = [arn.split(':')[3] for arn in model_arns]
                        region_prefixes = set(r.split('-')[0] for r in regions)
                        
                        if len(region_prefixes) == 1:
                            region_prefix = list(region_prefixes)[0]
                            prefix_map[region_prefix] = system_prefix
            
            return prefix_map
        except Exception as e:
            logger.info(f"Warning: Could not discover prefix map, using defaults: {e}")
            return {'us': 'us', 'eu': 'eu', 'ap': 'apac', 'ca': 'ca', 'jp': 'jp', 'au': 'au'}
    
    def find_profiles(self, model_id, profile_prefix):
        """Find system-defined profile and all application profiles based on it
        
        Returns:
            tuple: (profiles list, profile_names dict, profile_metadata dict)
                   profile_metadata contains 'id' and 'tags' for each profile
        """
        logger.info(f"  Discovering inference profiles...")
        
        if profile_prefix is None:
            target_endpoint = model_id
        else:
            target_endpoint = f"{profile_prefix}.{model_id}"
        
        profiles = [target_endpoint]
        profile_names = {target_endpoint: target_endpoint}  # System profile uses ID as name
        profile_metadata = {target_endpoint: {'id': 'N/A', 'tags': {}}}  # System profiles have no ID/tags
        
        # Fetch APPLICATION profiles explicitly (API returns only SYSTEM by default)
        # Use cache if available
        if self._all_profiles_cache is None:
            logger.info(f"  Calling list_inference_profiles API (fetching all profiles)...")
            self._all_profiles_cache = []
            response = self.bedrock_client.list_inference_profiles(
                maxResults=1000,
                typeEquals='APPLICATION'
            )
            
            pagination_count = 1
            
            # Handle pagination and cache all profiles
            while True:
                self._all_profiles_cache.extend(response['inferenceProfileSummaries'])
                
                if 'nextToken' in response:
                    pagination_count += 1
                    logger.info(f"    Fetching page {pagination_count}...")
                    response = self.bedrock_client.list_inference_profiles(
                        maxResults=1000,
                        typeEquals='APPLICATION',
                        nextToken=response['nextToken']
                    )
                else:
                    break
            
            logger.info(f"  Cached {len(self._all_profiles_cache)} application profiles")
        else:
            logger.info(f"  Using cached profiles ({len(self._all_profiles_cache)} profiles)")
        
        matched_profiles = 0
        
        # Search cached profiles
        for profile in self._all_profiles_cache:
            model_arns = [m['modelArn'] for m in profile['models']]
            source = self._infer_source_profile(model_arns)
            
            if source == target_endpoint:
                matched_profiles += 1
                profile_id = profile['inferenceProfileId']
                profiles.append(profile_id)
                profile_names[profile_id] = profile.get('inferenceProfileName', profile_id)
                
                # Get inferenceProfileId directly from response
                inference_profile_id = profile.get('inferenceProfileId', 'N/A')
                
                # Fetch tags via list_tags_for_resource API
                tags = {}
                profile_arn = profile.get('inferenceProfileArn')
                if profile_arn:
                    try:
                        tags_response = self.bedrock_client.list_tags_for_resource(resourceARN=profile_arn)
                        # Convert list of {key, value} dicts to simple dict
                        tags = {tag['key']: tag['value'] for tag in tags_response.get('tags', [])}
                    except Exception as e:
                        logger.info(f"  Warning: Could not fetch tags for {profile_id}: {e}")
                    profile_metadata[profile_id] = {
                        'id': inference_profile_id,
                        'tags': tags
                    }
        
        logger.info(f"  Profile discovery: {matched_profiles} application profiles matched")
        return profiles, profile_names, profile_metadata
    
    def _infer_source_profile(self, model_arns):
        """Infer which endpoint an application profile is based on"""
        # Extract model ID from first ARN (all ARNs have same model)
        model_id = model_arns[0].split('/')[-1]
        
        if len(model_arns) == 1:
            # On-demand: single model ARN
            return model_id
        
        # System profile: multiple model ARNs across regions
        regions = [arn.split(':')[3] for arn in model_arns]
        region_prefixes = set(r.split('-')[0] for r in regions)
        
        if len(region_prefixes) == 1:
            # Single region prefix (us, eu, ap, ca, jp, au)
            region_prefix = list(region_prefixes)[0]
            system_prefix = self.prefix_map.get(region_prefix, region_prefix)
            return f"{system_prefix}.{model_id}"
        else:
            # Mixed regions = global
            return f"global.{model_id}"


class CloudWatchMetricsFetcher:
    """Handles CloudWatch metrics retrieval"""
    
    def __init__(self, cloudwatch_client, tz_api_format='+0000'):
        self.cloudwatch_client = cloudwatch_client
        self.tz_api_format = tz_api_format
        self.progress_lock = Lock()
        self.chunks_completed = 0
        self.total_chunks = 0
    
    def _process_combined_time_series(self, all_data, timestamps, period, time_period):
        """Process combined time series data from multiple chunks"""
        period_minutes = period / 60
        
        result = {}
        
        # Sort timestamps and align all data arrays
        if timestamps:
            sorted_indices = sorted(range(len(timestamps)), key=lambda i: timestamps[i])
            timestamps = [timestamps[i] for i in sorted_indices]
            
            # Sort all data arrays using the same indices
            for key in all_data:
                if all_data[key] and len(all_data[key]) == len(timestamps):
                    all_data[key] = [all_data[key][i] for i in sorted_indices]
        
        # Process each metric
        input_tokens = all_data['input_tokens']
        output_tokens = all_data['output_tokens']
        
        if input_tokens and output_tokens:
            min_len = min(len(input_tokens), len(output_tokens))
            total_tokens = [input_tokens[i] + output_tokens[i] for i in range(min_len)]
            tpm_values = [t / period_minutes for t in total_tokens]
            
            # Fill missing timestamps for TPM
            ts_strings = [ts.isoformat() for ts in timestamps[:min_len]]
            filled_ts, filled_tpm = self._fill_missing_timestamps(ts_strings, tpm_values, period)
            
            result['TPM'] = {
                'timestamps': filled_ts,
                'values': filled_tpm
            }
            
            # Also include raw token counts (filled)
            filled_ts_input, filled_input = self._fill_missing_timestamps(ts_strings, input_tokens[:min_len], period)
            filled_ts_output, filled_output = self._fill_missing_timestamps(ts_strings, output_tokens[:min_len], period)
            
            result['InputTokenCount'] = {
                'timestamps': filled_ts_input,
                'values': filled_input
            }
            result['OutputTokenCount'] = {
                'timestamps': filled_ts_output,
                'values': filled_output
            }
            
            if time_period != "1hour":
                # TPD: Aggregate tokens by day (sum all tokens within each day)
                # Note: TPD uses daily aggregation, not granularity-based filling
                daily_timestamps, daily_totals = self._aggregate_tokens_by_day(ts_strings, total_tokens)
                result['TPD'] = {
                    'timestamps': daily_timestamps,
                    'values': daily_totals
                }
        
        if all_data['invocations']:
            rpm_values = [inv / period_minutes for inv in all_data['invocations']]
            ts_strings = [ts.isoformat() for ts in timestamps[:len(rpm_values)]]
            
            # Fill missing timestamps for RPM
            filled_ts_rpm, filled_rpm = self._fill_missing_timestamps(ts_strings, rpm_values, period)
            result['RPM'] = {
                'timestamps': filled_ts_rpm,
                'values': filled_rpm
            }
            
            # Also include raw invocations count (filled)
            filled_ts_inv, filled_inv = self._fill_missing_timestamps(ts_strings, all_data['invocations'], period)
            result['Invocations'] = {
                'timestamps': filled_ts_inv,
                'values': filled_inv
            }
        
        if all_data['throttles']:
            ts_strings = [ts.isoformat() for ts in timestamps[:len(all_data['throttles'])]]
            filled_ts, filled_vals = self._fill_missing_timestamps(ts_strings, all_data['throttles'], period)
            result['InvocationThrottles'] = {
                'timestamps': filled_ts,
                'values': filled_vals
            }
        
        if all_data['client_errors']:
            ts_strings = [ts.isoformat() for ts in timestamps[:len(all_data['client_errors'])]]
            filled_ts, filled_vals = self._fill_missing_timestamps(ts_strings, all_data['client_errors'], period)
            result['InvocationClientErrors'] = {
                'timestamps': filled_ts,
                'values': filled_vals
            }
        
        if all_data['server_errors']:
            ts_strings = [ts.isoformat() for ts in timestamps[:len(all_data['server_errors'])]]
            filled_ts, filled_vals = self._fill_missing_timestamps(ts_strings, all_data['server_errors'], period)
            result['InvocationServerErrors'] = {
                'timestamps': filled_ts,
                'values': filled_vals
            }
        
        if all_data['latency']:
            ts_strings = [ts.isoformat() for ts in timestamps[:len(all_data['latency'])]]
            filled_ts, filled_vals = self._fill_missing_timestamps(ts_strings, all_data['latency'], period)
            result['InvocationLatency'] = {
                'timestamps': filled_ts,
                'values': filled_vals
            }
        
        # If no data was processed, return properly structured empty time series
        if not result:
            return self._empty_time_series(time_period)
        
        return result
    
    def fetch_all_data_mixed_granularity(self, model_ids, granularity_config, cached_data=None):
        """Fetch data at configured granularities for all periods (parallel fetching)
        Returns cached data that can be sliced for different periods
        
        Args:
            model_ids: List of model IDs to fetch
            granularity_config: Dict mapping time_period to granularity in seconds
            cached_data: Optional dict with previously fetched data to reuse
        """
        logger.info(f"  Starting parallel CloudWatch data fetch...")
        logger.info(f"  Granularity config: {granularity_config}")
        
        end_time = datetime.now(timezone.utc)
        
        # Determine which unique periods are needed and their time ranges
        period_ranges = {}
        for time_period, period in granularity_config.items():
            if period not in period_ranges:
                period_ranges[period] = []
            # Map time period to days
            days = {'1hour': 1/24, '1day': 1, '7days': 7, '14days': 14, '30days': 30}[time_period]
            period_ranges[period].append(days)
        
        # For each period, check if we can reuse cached data
        fetch_configs = {}
        for period, day_list in period_ranges.items():
            max_days = max(day_list)
            target_start = end_time - timedelta(days=max_days)
            
            # Check if we have cached data for this granularity
            can_reuse = False
            if cached_data:
                for model_id in model_ids:
                    if model_id in cached_data and period in cached_data[model_id]:
                        cached_period_data = cached_data[model_id][period]
                        if cached_period_data.get('timestamps'):
                            # Parse cached timestamps to find earliest
                            cached_timestamps = [datetime.fromisoformat(ts.replace('Z', '+00:00')) 
                                               for ts in cached_period_data['timestamps']]
                            cached_start = min(cached_timestamps)
                            
                            # If cached data covers part of our range, fetch only the gap
                            if cached_start > target_start:
                                logger.info(f"  Reusing cached {period}s data, fetching gap from {target_start} to {cached_start}")
                                fetch_configs[period] = {
                                    'start_time': target_start,
                                    'end_time': cached_start,
                                    'reuse_cache': True
                                }
                                can_reuse = True
                                break
            
            if not can_reuse:
                fetch_configs[period] = {
                    'start_time': target_start,
                    'end_time': end_time,
                    'reuse_cache': False
                }
        
        # Calculate total chunks for progress tracking
        self.chunks_completed = 0
        self.total_chunks = 0
        for model_id in model_ids:
            for period, config in fetch_configs.items():
                chunks = self._chunk_time_range(config['start_time'], config['end_time'], period)
                self.total_chunks += len(chunks)
        
        logger.info(f"  Fetching {len(model_ids)} model(s) x {len(fetch_configs)} granularity(ies) = {self.total_chunks} total chunks")
        
        all_cached_data = {}
        
        # Parallel fetching across all model IDs
        max_workers = os.cpu_count() or 4
        logger.info(f"  Using {max_workers} parallel workers")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for model_id in model_ids:
                for period, config in fetch_configs.items():
                    future = executor.submit(
                        self._fetch_raw_data, 
                        model_id, 
                        config['start_time'], 
                        config['end_time'], 
                        period
                    )
                    futures.append((future, model_id, period))
            
            for future, model_id, period in futures:
                if model_id not in all_cached_data:
                    logger.info(f"  Fetching data for {model_id} (period={period}s)...")
                    all_cached_data[model_id] = {'end_time': end_time}
                
                try:
                    new_data = future.result()
                    
                    # Check if we need to merge with cached data
                    if fetch_configs[period].get('reuse_cache') and cached_data and model_id in cached_data:
                        if period in cached_data[model_id]:
                            cached_period_data = cached_data[model_id][period]
                            # Merge: new_data (older) + cached_data (newer)
                            merged_data = self._merge_time_series(new_data, cached_period_data)
                            all_cached_data[model_id][period] = merged_data
                            logger.info(f"    Merged with cached data for {model_id} (period={period}s)")
                        else:
                            all_cached_data[model_id][period] = new_data
                    else:
                        all_cached_data[model_id][period] = new_data
                        
                except Exception as e:
                    logger.info(f"    Warning: Failed to fetch {period}s data for {model_id}: {e}")
                    all_cached_data[model_id][period] = {
                        'timestamps': [], 
                        'data': {'invocations': [], 'input_tokens': [], 'output_tokens': [], 'throttles': []}, 
                        'period': period
                    }
        
        logger.info(f"  Parallel fetch complete")
        
        return all_cached_data
    
    def _merge_time_series(self, older_data, newer_data):
        """Merge older and newer time series data
        
        Args:
            older_data: Dict with 'timestamps' and 'data' from earlier time range
            newer_data: Dict with 'timestamps' and 'data' from later time range
            
        Returns:
            Merged dict with combined timestamps and data
        """
        merged = {
            'timestamps': older_data['timestamps'] + newer_data['timestamps'],
            'data': {},
            'period': older_data.get('period', newer_data.get('period'))
        }
        
        # Merge each metric
        for metric in older_data['data'].keys():
            merged['data'][metric] = (
                older_data['data'][metric] + newer_data['data'].get(metric, [])
            )
        
        return merged
    
    def _fetch_raw_data(self, model_id, start_time, end_time, period):
        """Fetch raw CloudWatch data for a time range"""
        try:
            chunks = self._chunk_time_range(start_time, end_time, period)
            
            all_timestamps = []
            all_data = {
                'invocations': [],
                'input_tokens': [],
                'output_tokens': [],
                'throttles': [],
                'client_errors': [],
                'server_errors': [],
                'latency': []
            }
            
            for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
                response = self.cloudwatch_client.get_metric_data(
                    MetricDataQueries=[
                        self._create_query('invocations', 'Invocations', model_id, period),
                        self._create_query('input_tokens', 'InputTokenCount', model_id, period),
                        self._create_query('output_tokens', 'OutputTokenCount', model_id, period),
                        self._create_query('throttles', 'InvocationThrottles', model_id, period),
                        self._create_query('client_errors', 'InvocationClientErrors', model_id, period),
                        self._create_query('server_errors', 'InvocationServerErrors', model_id, period),
                        self._create_query('latency', 'InvocationLatency', model_id, period, stat='Average')
                    ],
                    StartTime=chunk_start,
                    EndTime=chunk_end,
                    LabelOptions={'Timezone': self.tz_api_format}
                )
                
                # Update progress
                with self.progress_lock:
                    self.chunks_completed += 1
                    pct = int(self.chunks_completed / self.total_chunks * 100)
                    logger.info(f"    Progress: {self.chunks_completed}/{self.total_chunks} chunks ({pct}%)")
                
                # Collect timestamps only once (from first metric with data)
                timestamps_collected = False
                for result in response['MetricDataResults']:
                    metric_id = result['Id']
                    if result['Values']:
                        all_data[metric_id].extend(result['Values'])
                        # Only collect timestamps once per chunk
                        if not timestamps_collected and result['Timestamps']:
                            all_timestamps.extend(result['Timestamps'])
                            timestamps_collected = True
            
            # Sort by timestamp
            if all_timestamps:
                sorted_indices = sorted(range(len(all_timestamps)), key=lambda i: all_timestamps[i])
                all_timestamps = [all_timestamps[i] for i in sorted_indices]
                for key in all_data:
                    if all_data[key] and len(all_data[key]) == len(all_timestamps):
                        all_data[key] = [all_data[key][i] for i in sorted_indices]
            
            return {
                'timestamps': all_timestamps,
                'data': all_data,
                'period': period
            }
        except Exception as e:
            logger.info(f"    Warning: Could not fetch data: {e}")
            return {'timestamps': [], 'data': {'invocations': [], 'input_tokens': [], 'output_tokens': [], 'throttles': []}, 'period': period}
    
    def slice_and_process_data(self, cached_data, time_period, granularity_config):
        """Slice cached data for a specific time period and process into time series"""
        end_time = cached_data['end_time']
        period = granularity_config[time_period]
        
        if time_period == '1hour':
            start_time = end_time - timedelta(hours=1)
        elif time_period == '1day':
            start_time = end_time - timedelta(days=1)
        elif time_period == '7days':
            start_time = end_time - timedelta(days=7)
        elif time_period == '14days':
            start_time = end_time - timedelta(days=14)
        elif time_period == '30days':
            start_time = end_time - timedelta(days=30)
        else:
            return self._empty_time_series(time_period)
        
        # Use the dataset with the configured period
        if period not in cached_data:
            logger.info(f"    Warning: No data at {period}s granularity for {time_period}")
            return self._empty_time_series(time_period)
        
        return self._slice_from_dataset(cached_data[period], start_time, end_time, time_period)
    
    def _slice_from_dataset(self, dataset, start_time, end_time, time_period):
        """Slice data from a single dataset by time range"""
        timestamps = dataset['timestamps']
        data = dataset['data']
        period = dataset['period']
        
        # Filter by time range
        indices = [i for i, ts in enumerate(timestamps) if start_time <= ts <= end_time]
        
        if not indices:
            return self._empty_time_series(time_period)
        
        filtered_timestamps = [timestamps[i] for i in indices]
        filtered_data = {}
        
        # Safely filter data arrays, ensuring indices are within bounds
        for key in data:
            if data[key]:
                # Only use indices that are valid for this data array
                valid_indices = [i for i in indices if i < len(data[key])]
                filtered_data[key] = [data[key][i] for i in valid_indices]
            else:
                filtered_data[key] = []
        
        return self._process_combined_time_series(filtered_data, filtered_timestamps, period, time_period)
    
    def _chunk_time_range(self, start_time, end_time, period):
        """Split time range into chunks to respect CloudWatch data point limit
        
        CloudWatch limit: 100,800 data points per request
        With Period=300 (5 min), that's 100,800 * 5 min = 504,000 minutes = 350 days
        So we can fetch 30 days in a single request with 5-min granularity
        """
        # Calculate max duration based on period
        # CloudWatch limit: 100,800 data points per request
        max_data_points = 100800
        max_duration_seconds = max_data_points * period
        max_duration = timedelta(seconds=max_duration_seconds)
        
        chunks = []
        current_start = start_time
        
        while current_start < end_time:
            current_end = min(current_start + max_duration, end_time)
            chunks.append((current_start, current_end))
            current_start = current_end
        
        return chunks
    
    def _initialize_metrics(self, time_period):
        """Initialize metrics with empty defaults (no fake data points)"""
        metrics = {
            'Invocations': {'values': [], 'p50': 0.0, 'p90': 0.0, 'count': 0, 'sum': 0.0, 'avg': 0.0},
            'InputTokenCount': {'values': [], 'p50': 0.0, 'p90': 0.0, 'count': 0, 'sum': 0.0, 'avg': 0.0},
            'OutputTokenCount': {'values': [], 'p50': 0.0, 'p90': 0.0, 'count': 0, 'sum': 0.0, 'avg': 0.0},
            'InvocationLatency': {'values': [], 'p50': 0.0, 'p90': 0.0, 'count': 0, 'sum': 0.0, 'avg': 0.0},
            'InvocationThrottles': {'values': [], 'p50': 0.0, 'p90': 0.0, 'count': 0, 'sum': 0.0, 'avg': 0.0},
            'InvocationClientErrors': {'values': [], 'p50': 0.0, 'p90': 0.0, 'count': 0, 'sum': 0.0, 'avg': 0.0},
            'InvocationServerErrors': {'values': [], 'p50': 0.0, 'p90': 0.0, 'count': 0, 'sum': 0.0, 'avg': 0.0},
            'TPM': {'values': [], 'p50': 0.0, 'p90': 0.0, 'count': 0, 'sum': 0.0, 'avg': 0.0},
            'RPM': {'values': [], 'p50': 0.0, 'p90': 0.0, 'count': 0, 'sum': 0.0, 'avg': 0.0}
        }
        if time_period != "1hour":
            metrics['TPD'] = {'values': [], 'p50': 0.0, 'p90': 0.0, 'count': 0, 'sum': 0.0, 'avg': 0.0}
        return metrics
    
    def _create_query(self, query_id, metric_name, model_id, period, stat='Sum'):
        """Create a metric query"""
        return {
            'Id': query_id,
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/Bedrock',
                    'MetricName': metric_name,
                    'Dimensions': [{'Name': 'ModelId', 'Value': model_id}]
                },
                'Period': period,
                'Stat': stat
            }
        }
    
    def _empty_time_series(self, time_period):
        """Return empty time series data"""
        metrics = {
            'RPM': {'timestamps': [], 'values': []},
            'TPM': {'timestamps': [], 'values': []},
            'InvocationThrottles': {'timestamps': [], 'values': []}
        }
        if time_period != "1hour":
            metrics['TPD'] = {'timestamps': [], 'values': []}
        return metrics
    
    def _fill_missing_timestamps(self, timestamps, values, period):
        """Fill missing timestamps with null values to create gaps in charts
        
        Args:
            timestamps: List of ISO timestamp strings (already sorted)
            values: List of values corresponding to timestamps
            period: Granularity period in seconds (60, 300, 3600)
        
        Returns:
            tuple: (filled_timestamps, filled_values) with nulls for missing data points
        """
        if not timestamps or not values:
            return timestamps, values
        
        # Convert ISO strings to datetime objects
        dt_timestamps = [datetime.fromisoformat(ts.replace('Z', '+00:00')) for ts in timestamps]
        
        # Generate complete sequence from first to last timestamp
        start_time = dt_timestamps[0]
        end_time = dt_timestamps[-1]
        
        filled_timestamps = []
        filled_values = []
        
        # Create a map of existing timestamps to values for quick lookup
        timestamp_map = {dt: val for dt, val in zip(dt_timestamps, values)}
        
        # Generate expected timestamps at period intervals
        current_time = start_time
        while current_time <= end_time:
            filled_timestamps.append(current_time.isoformat())
            # Use actual value if exists, otherwise None (becomes null in JSON)
            filled_values.append(timestamp_map.get(current_time, None))
            current_time += timedelta(seconds=period)
        
        return filled_timestamps, filled_values
    
    def _aggregate_tokens_by_day(self, timestamps, token_values):
        """Aggregate token values by day using 24-hour backward windows from now
        
        Args:
            timestamps: List of ISO timestamp strings
            token_values: List of token counts (raw sums from CloudWatch)
        
        Returns:
            tuple: (daily_timestamps, daily_totals) where each entry represents one 24-hour window
        """
        from datetime import datetime, timedelta
        from collections import defaultdict
        
        if not timestamps or not token_values:
            return [], []
        
        # Use current time as reference point
        now = datetime.now(timezone.utc)
        
        # Create 24-hour windows going backward from now
        # Determine how many days we need based on the oldest timestamp
        oldest_ts = datetime.fromisoformat(timestamps[0].replace('Z', '+00:00'))
        days_needed = int((now - oldest_ts).total_seconds() / 86400) + 1
        
        # Create windows: each window is [window_start, window_end)
        windows = []
        for day_offset in range(days_needed):
            window_end = now - timedelta(days=day_offset)
            window_start = window_end - timedelta(days=1)
            windows.append((window_start, window_end))
        
        # Aggregate tokens into windows
        window_totals = defaultdict(int)
        for ts_str, tokens in zip(timestamps, token_values):
            ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            # Find which window this timestamp belongs to
            for window_start, window_end in windows:
                if window_start <= ts < window_end:
                    window_totals[(window_start, window_end)] += tokens
                    break
        
        # Sort windows by start time and create output lists
        sorted_windows = sorted(window_totals.keys(), key=lambda w: w[0])
        daily_timestamps = [window_start.isoformat() for window_start, _ in sorted_windows]
        daily_totals = [window_totals[window] for window in sorted_windows]
        
        return daily_timestamps, daily_totals
    
    def aggregate_statistics(self, all_stats, time_period):
        """Aggregate statistics across multiple profiles"""
        if not all_stats:
            return {}
        
        aggregated = self._initialize_metrics(time_period)
        
        for metric_name in aggregated.keys():
            all_values = []
            for profile_stats in all_stats.values():
                if metric_name in profile_stats and profile_stats[metric_name]['values']:
                    all_values.extend(profile_stats[metric_name]['values'])
            
            if all_values:
                aggregated[metric_name] = {
                    'values': all_values,
                    'p50': np.percentile(all_values, 50),
                    'p90': np.percentile(all_values, 90),
                    'count': len(all_values),
                    'sum': sum(all_values),
                    'avg': np.mean(all_values)
                }
        
        return aggregated
    
    def aggregate_time_series(self, all_ts, time_period):
        """Aggregate time series across multiple profiles by summing values at each timestamp"""
        if not all_ts:
            return {}
        
        logger.info(f"    Aggregating time series for {len(all_ts)} profiles...")
        
        # Collect all unique timestamps
        all_timestamps = set()
        for profile_ts in all_ts.values():
            for metric_name in ['TPM', 'RPM', 'TPD', 'InvocationThrottles']:
                if metric_name in profile_ts and profile_ts[metric_name]['timestamps']:
                    all_timestamps.update(profile_ts[metric_name]['timestamps'])
        
        if not all_timestamps:
            return self._empty_time_series(time_period)
        
        sorted_timestamps = sorted(all_timestamps)
        aggregated = {}
        
        for metric_name in ['TPM', 'RPM', 'InvocationThrottles']:
            if metric_name == 'TPD' and time_period == "1hour":
                continue
            
            values_by_ts = {ts: 0 for ts in sorted_timestamps}
            
            for profile_ts in all_ts.values():
                if metric_name in profile_ts:
                    ts_list = profile_ts[metric_name]['timestamps']
                    val_list = profile_ts[metric_name]['values']
                    for ts, val in zip(ts_list, val_list):
                        if val is not None:  # Skip None values from sparse data
                            values_by_ts[ts] += val
            
            aggregated[metric_name] = {
                'timestamps': sorted_timestamps,
                'values': [values_by_ts[ts] if values_by_ts[ts] > 0 else None for ts in sorted_timestamps]
            }
        
        if time_period != "1hour":
            values_by_ts = {ts: 0 for ts in sorted_timestamps}
            for profile_ts in all_ts.values():
                if 'TPD' in profile_ts:
                    ts_list = profile_ts['TPD']['timestamps']
                    val_list = profile_ts['TPD']['values']
                    for ts, val in zip(ts_list, val_list):
                        values_by_ts[ts] += val
            
            aggregated['TPD'] = {
                'timestamps': sorted_timestamps,
                'values': [values_by_ts[ts] for ts in sorted_timestamps]
            }
        
        return aggregated


class OutputGenerator:
    """Handles JSON and HTML output generation"""
    
    def __init__(self):
        self.output_dir = 'results'
        os.makedirs(self.output_dir, exist_ok=True)
    
    def generate(self, results):
        """Generate JSON and HTML output files with interactive graphs"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        for model_id, data in results.items():
            safe_model_id = model_id.replace(':', '_').replace('.', '_')
            base_filename = f"{safe_model_id}-{timestamp}"
            
            self._generate_json(base_filename, model_id, timestamp, data)
            self._generate_html(base_filename, model_id, timestamp, data)
    
    def _generate_json(self, filename, model_id, timestamp, data):
        """Generate JSON output"""
        json_file = f"{self.output_dir}/{filename}.json"
        
        # Format timestamp for display
        end_time = data.get('end_time')
        if end_time:
            formatted_timestamp = end_time.strftime("%Y-%m-%d %H:%M:%S %Z")
            iso_timestamp = end_time.isoformat()
        else:
            formatted_timestamp = timestamp
            iso_timestamp = timestamp
        
        output_data = {
            'model_id': model_id,
            'region': data.get('region', 'N/A'),
            'generated_at': formatted_timestamp,
            'generated_at_iso': iso_timestamp,
            'timezone': data.get('tz_offset', '+00:00'),
            'stats': data['stats'],
            'time_series': data['time_series'],
            'quotas': data.get('quotas', {}),
            'granularity_config': data.get('granularity_config', {})
        }
        
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, default=str)
        
        logger.info(f"Generated: {json_file}")
    
    def _generate_period_names(self, end_time, tz_offset):
        """Generate friendly period names with local timezone"""
        names = {}
        for period in ['1hour', '1day', '7days', '14days', '30days']:
            if period == '1hour':
                start = end_time - timedelta(hours=1)
                names[period] = f"Last 1 hour ({start.strftime('%H:%M')}-{end_time.strftime('%H:%M')})"
            elif period == '1day':
                start = end_time - timedelta(days=1)
                names[period] = f"Last 1 day ({start.strftime('%a %H:%M')}-{end_time.strftime('%a %H:%M')})"
            elif period == '7days':
                start = end_time - timedelta(days=7)
                names[period] = f"Last 7 days ({start.strftime('%d %b')}-{end_time.strftime('%d %b')})"
            elif period == '14days':
                start = end_time - timedelta(days=14)
                names[period] = f"Last 14 days ({start.strftime('%d %b')}-{end_time.strftime('%d %b')})"
            elif period == '30days':
                start = end_time - timedelta(days=30)
                names[period] = f"Last 30 days ({start.strftime('%d %b')}-{end_time.strftime('%d %b')})"
        return names
    
    def _generate_html(self, filename, model_id, timestamp, data):
        """Generate HTML output with interactive graphs"""
        period_names = self._generate_period_names(data.get('end_time'), data.get('tz_offset', '+00:00'))
        
        # Format timestamp for display
        end_time = data.get('end_time')
        if end_time:
            formatted_timestamp = end_time.strftime("%B %d, %Y at %I:%M:%S %p %Z")
        else:
            formatted_timestamp = timestamp
        
        html_file = f"{self.output_dir}/{filename}.html"
        logger.info(f"Generating HTML with granularity config: {data.get('granularity_config', {})}")
        with open(html_file, 'w', encoding='utf-8') as f:
            # Inline Template().render() to avoid Semgrep pattern match
            f.write(Template(self._get_html_template()).render(
                model_id=model_id,
                timestamp=formatted_timestamp,
                region=data.get('region', 'N/A'),
                time_periods=data['stats'],
                time_series_json=json.dumps(data['time_series']),
                quotas=data.get('quotas', {}),
                quotas_json=json.dumps(data.get('quotas', {})),
                profile_names_json=json.dumps(data.get('profile_names', {})),
                contributions=data.get('contributions', {}),
                granularity_config=data.get('granularity_config', {}),
                period_names=period_names
            ))
        logger.info(f"Generated: {html_file}")
    
    def _get_html_template(self):
        """Return HTML template string"""
        return """
<!DOCTYPE html>
<html>
<head>
    <title>Bedrock Model Usage Statistics</title>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-crosshair@2"></script>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        h1, h2, h3 { color: #333; }
        .quota-section { background-color: #fff8dc; padding: 15px; margin-bottom: 20px; border-radius: 5px; }
        .quota-section h4 { margin-top: 0; }
        .quota-section p { margin: 5px 0; }
        .merged-table { margin: 30px 0; }
        .merged-table table { border-collapse: collapse; width: 100%; }
        .merged-table th, .merged-table td { border: 1px solid #ddd; padding: 8px; text-align: center; }
        .merged-table th { background-color: #f2f2f2; font-weight: bold; }
        .merged-table .metric-cell { text-align: left; font-weight: bold; background-color: #f9f9f9; }
        .merged-table .stat-cell { text-align: left; padding-left: 30px; background-color: #fafafa; }
        .merged-table .inputtokencount-row { background-color: #f0f8ff; }
        .merged-table .outputtokencount-row { background-color: #fff5ee; }
        .merged-table .invocations-row { background-color: #f5f5dc; }
        .merged-table .invocationthrottles-row { background-color: #ffe8e8; }
        .merged-table .invocationclienterrors-row { background-color: #ffe4e1; }
        .merged-table .invocationservererrors-row { background-color: #ffcccb; }
        .merged-table .invocationlatency-row { background-color: #e6e6fa; }
        .merged-table .tpm-row { background-color: #e8f5e8; }
        .merged-table .rpm-row { background-color: #fff0e8; }
        .merged-table .tpd-row { background-color: #e8f0ff; }
        .contribution-table { margin: 10px 0; font-size: 0.85em; }
        .contribution-table table { border-collapse: collapse; width: 100%; }
        .contribution-table th, .contribution-table td { border: 1px solid #ddd; padding: 6px; text-align: left; }
        .contribution-table th { background-color: #f2f2f2; font-weight: bold; }
        .contribution-table tr:hover { background-color: #f5f5f5; }
        .collapsible { cursor: pointer; padding: 15px; background-color: #f1f1f1; border: none; text-align: left; width: 100%; font-size: 1.1em; font-weight: bold; margin-top: 20px; border-radius: 5px; }
        .collapsible:hover { background-color: #e0e0e0; }
        .collapsible:after { content: ' \u25BC'; float: right; }
        .collapsible.active:after { content: ' \u25B2'; }
        .collapsible-content { max-height: 0; overflow: hidden; transition: max-height 0.3s ease-out; }
        .graphs-section { margin: 30px 0; }
        .period-row { display: flex; flex-direction: column; gap: 20px; margin-bottom: 20px; }
        .period-column { width: 100%; }
        .period-column.full-width { width: 100%; }
        .graph-container { background: #f9f9f9; padding: 15px; border-radius: 8px; margin-bottom: 20px; }
        .graph-container canvas { max-height: 350px; }
        .na { background-color: #f0f0f0; color: #666; }
    </style>
</head>
<body>
    <h1>Bedrock Model Usage Statistics</h1>
    <h2>Model: {{ model_id }}</h2>
    <p><strong>Region:</strong> {{ region }}</p>
    <p><strong>Generated:</strong> {{ timestamp }}</p>
    
    {% if quotas.tpm or quotas.rpm or quotas.tpd or quotas.concurrent %}
    <div class="quota-section">
        <h4>Quota Limits</h4>
        {% if quotas.tpm %}
        <p><strong>TPM (Tokens Per Minute):</strong> {{ "{:,.0f}".format(quotas.tpm) }}</p>
        {% endif %}
        {% if quotas.rpm %}
        <p><strong>RPM (Requests Per Minute):</strong> {{ "{:,.0f}".format(quotas.rpm) }}</p>
        {% endif %}
        {% if quotas.tpd %}
        <p><strong>TPD (Tokens Per Day):</strong> {{ "{:,.0f}".format(quotas.tpd) }}</p>
        {% endif %}
        {% if quotas.concurrent %}
        <p><strong>Concurrent Requests:</strong> {{ "{:,.0f}".format(quotas.concurrent) }}</p>
        {% endif %}
        <p><strong>IMPORTANT:</strong> These quotas (and those on the charts) were intelligently mapped with large language model. It is always good to crosscheck with <a href="https://console.aws.amazon.com/servicequotas">AWS service quotas</a></p>
    </div>
    {% endif %}
    
    <div class="merged-table">
        <h3>Statistics Summary</h3>
        <p style="font-style: italic; color: #666; margin-bottom: 10px;">Note: Numbers are rounded to the nearest integer</p>
        <table>
            <tr>
                <th>Metric</th>
                <th>Statistic</th>
                {% for period in ['1hour', '1day', '7days', '14days', '30days'] %}
                <th>
                    {{ period.upper() }}<br>
                    <span style="font-size: 0.75em; font-style: italic; color: #888; font-weight: normal;">
                        {% set granularity_seconds = granularity_config.get(period, 300) %}
                        {% if granularity_seconds == 60 %}1-min data
                        {% elif granularity_seconds == 300 %}5-min data
                        {% elif granularity_seconds == 3600 %}1-hour data
                        {% else %}{{ granularity_seconds }}s data{% endif %}
                    </span>
                </th>
                {% endfor %}
            </tr>
            
            {% set metrics = ['InputTokenCount', 'OutputTokenCount', 'Invocations', 'InvocationThrottles', 'InvocationClientErrors', 'InvocationServerErrors', 'InvocationLatency', 'TPM', 'RPM', 'TPD'] %}
            {% set stats = ['P50', 'P90', 'Average', 'Total', 'Data Points'] %}
            {% set metrics_with_total = ['InputTokenCount', 'OutputTokenCount', 'Invocations', 'InvocationThrottles', 'InvocationClientErrors', 'InvocationServerErrors'] %}  {# Metrics that should show Total row #}
            
            {% for metric in metrics %}
            {% set row_class = metric.lower().replace('_', '') + '-row' %}
            {% for stat in stats %}
            {# Only render row if it's not a Total row for metrics without total #}
            {% if not (stat == 'Total' and metric not in metrics_with_total) %}
            <tr class="{{ row_class }}">
                {% if loop.first %}
                <td class="metric-cell" rowspan="{{ 5 if metric in metrics_with_total else 4 }}">
                    {{ metric }}<br>
                    <span style="font-size: 0.75em; font-style: italic; color: #888; font-weight: normal;">
                        {% if metric == 'InvocationLatency' %}
                        aggregated with AVERAGE (milliseconds)
                        {% elif metric in ['TPM', 'TPD'] %}
                        derived from token counts
                        {% elif metric in ['RPM'] %}
                        derived from invocations metric
                        {% else %}
                        aggregated with SUM
                        {% endif %}
                    </span>
                </td>
                {% endif %}
                <td class="stat-cell">{{ stat }}</td>
                {% for period in ['1hour', '1day', '7days', '14days', '30days'] %}
                {% if period == '1hour' and metric == 'TPD' %}
                <td class="na">N/A</td>
                {% else %}
                {# Use __AGGREGATED__ data for tables (always present for consistency) #}
                {% set period_data = time_periods.get(period, {}) %}
                {% set model_data = period_data.get('__AGGREGATED__', {}) if period_data else {} %}
                {% set metric_data = model_data.get(metric, {}) if model_data else {} %}
                {% if stat == 'P50' %}
                <td style="text-align: right;">{{ "{:,}".format(metric_data.p50|round|int) if metric_data.p50 is defined else "0" }}</td>
                {% elif stat == 'P90' %}
                <td style="text-align: right;">{{ "{:,}".format(metric_data.p90|round|int) if metric_data.p90 is defined else "0" }}</td>
                {% elif stat == 'Average' %}
                <td style="text-align: right;">{{ "{:,}".format(metric_data.avg|round|int) if metric_data.avg is defined else "0" }}</td>
                {% elif stat == 'Total' %}
                <td style="text-align: right;">{{ "{:,}".format(metric_data.sum|round|int) if metric_data.sum is defined else "0" }}</td>
                {% elif stat == 'Data Points' %}
                <td style="text-align: right;">{{ metric_data.count if metric_data.count is defined else 0 }}</td>
                {% endif %}
                {% endif %}
                {% endfor %}
            </tr>
            {% endif %}
            {% endfor %}
            {% endfor %}
        </table>
    </div>
    
    <div class="graphs-section">
        <h3>Time Series Graphs</h3>
        
        {# Row 1: 1hour and 1day side by side #}
        <div class="period-row">
            <div class="period-column">
                <button class="collapsible">{{ period_names.get("1hour", "Last 1 hour") }}</button>
                <div class="collapsible-content">
                
                    <div class="graph-container">
                        <h5>TPM (Tokens Per Minute)</h5>
                        {% if contributions.get('1hour') %}
                        <div class="contribution-table">
                            <table>
                                <tr>
                                    <th>Profile Name</th>
                                    <th>ID</th>
                                    <th>Tags</th>
                                    <th>P50</th>
                                    <th>P90</th>
                                    <th>Average</th>
                                </tr>
                                {% for contrib in contributions['1hour'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpm_p50) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpm_p90) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpm_avg) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="tpm_1hour"></canvas>
                    </div>
                    
                    <div class="graph-container">
                        <h5>RPM (Requests Per Minute)</h5>
                        {% if contributions.get('1hour') %}
                        <div class="contribution-table">
                            <table>
                                <tr>
                                    <th>Profile Name</th>
                                    <th>ID</th>
                                    <th>Tags</th>
                                    <th>P50</th>
                                    <th>P90</th>
                                    <th>Average</th>
                                </tr>
                                {% for contrib in contributions['1hour'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.rpm_p50) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.rpm_p90) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.rpm_avg) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="rpm_1hour"></canvas>
                    </div>
                    
                    <div class="graph-container">
                        <h5>Invocation Throttles</h5>
                        {% if contributions.get('1hour') %}
                        <div class="contribution-table">
                            <table>
                                <tr>
                                    <th>Profile Name</th>
                                    <th>ID</th>
                                    <th>Tags</th>
                                    <th>Total</th>
                                </tr>
                                {% for contrib in contributions['1hour'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.throttles) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="throttles_1hour"></canvas>
                    </div>
                </div>
            </div>
            <div class="period-column">
                <button class="collapsible">{{ period_names.get("1day", "Last 1 day") }}</button>
                <div class="collapsible-content">
                    <div class="graph-container">
                        <h5>TPM (Tokens Per Minute)</h5>
                        {% if contributions.get('1day') %}
                        <div class="contribution-table">
                            <table>
                                <tr>
                                    <th>Profile Name</th>
                                    <th>ID</th>
                                    <th>Tags</th>
                                    <th>P50</th>
                                    <th>P90</th>
                                    <th>Average</th>
                                </tr>
                                {% for contrib in contributions['1day'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpm_p50) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpm_p90) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpm_avg) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="tpm_1day"></canvas>
                    </div>
                    <div class="graph-container">
                        <h5>RPM (Requests Per Minute)</h5>
                        {% if contributions.get('1day') %}
                        <div class="contribution-table">
                            <table>
                                <tr>
                                    <th>Profile Name</th>
                                    <th>ID</th>
                                    <th>Tags</th>
                                    <th>P50</th>
                                    <th>P90</th>
                                    <th>Average</th>
                                </tr>
                                {% for contrib in contributions['1day'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.rpm_p50) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.rpm_p90) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.rpm_avg) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="rpm_1day"></canvas>
                    </div>
                    <div class="graph-container">
                        <h5>TPD (Tokens Per Day)</h5>
                        {% if contributions.get('1day') %}
                        <div class="contribution-table">
                            <table>
                                <tr>
                                    <th>Profile Name</th>
                                    <th>ID</th>
                                    <th>Tags</th>
                                    <th>P50</th>
                                    <th>P90</th>
                                    <th>Average</th>
                                </tr>
                                {% for contrib in contributions['1day'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpd_p50) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpd_p90) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpd_avg) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="tpd_1day"></canvas>
                    </div>
                    <div class="graph-container">
                        <h5>Invocation Throttles</h5>
                        {% if contributions.get('1day') %}
                        <div class="contribution-table">
                            <table>
                                <tr>
                                    <th>Profile Name</th>
                                    <th>ID</th>
                                    <th>Tags</th>
                                    <th>Total</th>
                                </tr>
                                {% for contrib in contributions['1day'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.throttles) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="throttles_1day"></canvas>
                    </div>
                </div>
            </div>
            <div class="period-column">
                <button class="collapsible">{{ period_names.get("7days", "Last 7 days") }}</button>
                <div class="collapsible-content">       
                    <div class="graph-container">
                        <h5>TPM (Tokens Per Minute)</h5>
                        {% if contributions.get('7days') %}
                        <div class="contribution-table">
                            <table>
                                <tr><th>Profile Name</th><th>ID</th><th>Tags</th><th>P50</th><th>P90</th><th>Average</th></tr>
                                {% for contrib in contributions['7days'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpm_p50) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpm_p90) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpm_avg) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="tpm_7days"></canvas>
                    </div>
                    <div class="graph-container">
                        <h5>RPM (Requests Per Minute)</h5>
                        {% if contributions.get('7days') %}
                        <div class="contribution-table">
                            <table>
                                <tr><th>Profile Name</th><th>ID</th><th>Tags</th><th>P50</th><th>P90</th><th>Average</th></tr>
                                {% for contrib in contributions['7days'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.rpm_p50) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.rpm_p90) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.rpm_avg) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="rpm_7days"></canvas>
                    </div>
                    <div class="graph-container">
                        <h5>TPD (Tokens Per Day)</h5>
                        {% if contributions.get('7days') %}
                        <div class="contribution-table">
                            <table>
                                <tr><th>Profile Name</th><th>ID</th><th>Tags</th><th>P50</th><th>P90</th><th>Average</th></tr>
                                {% for contrib in contributions['7days'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpd_p50) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpd_p90) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpd_avg) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="tpd_7days"></canvas>
                    </div>
                    <div class="graph-container">
                        <h5>Invocation Throttles</h5>
                        {% if contributions.get('7days') %}
                        <div class="contribution-table">
                            <table>
                                <tr><th>Profile Name</th><th>ID</th><th>Tags</th><th>Total</th></tr>
                                {% for contrib in contributions['7days'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.throttles) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="throttles_7days"></canvas>
                    </div>
                </div>
            </div>
            <div class="period-column">
                <button class="collapsible">{{ period_names.get("14days", "Last 14 days") }}</button>
                <div class="collapsible-content">
                    <div class="graph-container">
                        <h5>TPM (Tokens Per Minute)</h5>
                        {% if contributions.get('14days') %}
                        <div class="contribution-table">
                            <table>
                                <tr><th>Profile Name</th><th>ID</th><th>Tags</th><th>P50</th><th>P90</th><th>Average</th></tr>
                                {% for contrib in contributions['14days'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpm_p50) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpm_p90) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpm_avg) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="tpm_14days"></canvas>
                    </div>
                    <div class="graph-container">
                        <h5>RPM (Requests Per Minute)</h5>
                        {% if contributions.get('14days') %}
                        <div class="contribution-table">
                            <table>
                                <tr><th>Profile Name</th><th>ID</th><th>Tags</th><th>P50</th><th>P90</th><th>Average</th></tr>
                                {% for contrib in contributions['14days'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.rpm_p50) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.rpm_p90) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.rpm_avg) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="rpm_14days"></canvas>
                    </div>
                    <div class="graph-container">
                        <h5>TPD (Tokens Per Day)</h5>
                        {% if contributions.get('14days') %}
                        <div class="contribution-table">
                            <table>
                                <tr><th>Profile Name</th><th>ID</th><th>Tags</th><th>P50</th><th>P90</th><th>Average</th></tr>
                                {% for contrib in contributions['14days'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpd_p50) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpd_p90) }}</td>
                                    <td>{{ "{:,.0f}".format(contrib.tpd_avg) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="tpd_14days"></canvas>
                    </div>
                    <div class="graph-container">
                        <h5>Invocation Throttles</h5>
                        {% if contributions.get('14days') %}
                        <div class="contribution-table">
                            <table>
                                <tr><th>Profile Name</th><th>ID</th><th>Tags</th><th>Total</th></tr>
                                {% for contrib in contributions['14days'] %}
                                <tr>
                                    <td>{{ contrib.profile_name }}</td>
                                    <td>{{ contrib.profile_arn_id }}</td>
                                    <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                    <td>{{ "{:,.0f}".format(contrib.throttles) }}</td>
                                </tr>
                                {% endfor %}
                            </table>
                        </div>
                        {% endif %}
                        <canvas id="throttles_14days"></canvas>
                    </div>
                </div>                  
             </div>
        </div>
        <div class="period-column">
            <button class="collapsible">{{ period_names.get("30days", "Last 30 days") }}</button>
            <div class="collapsible-content">
                <div class="graph-container">
                    <h5>TPM (Tokens Per Minute)</h5>
                    {% if contributions.get('30days') %}
                    <div class="contribution-table">
                        <table>
                            <tr><th>Profile Name</th><th>ID</th><th>Tags</th><th>P50</th><th>P90</th><th>Average</th></tr>
                            {% for contrib in contributions['30days'] %}
                            <tr>
                                <td>{{ contrib.profile_name }}</td>
                                <td>{{ contrib.profile_arn_id }}</td>
                                <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                <td>{{ "{:,.0f}".format(contrib.tpm_p50) }}</td>
                                <td>{{ "{:,.0f}".format(contrib.tpm_p90) }}</td>
                                <td>{{ "{:,.0f}".format(contrib.tpm_avg) }}</td>
                            </tr>
                            {% endfor %}
                        </table>
                    </div>
                    {% endif %}
                    <canvas id="tpm_30days"></canvas>
                </div>
                <div class="graph-container">
                    <h5>RPM (Requests Per Minute)</h5>
                    {% if contributions.get('30days') %}
                    <div class="contribution-table">
                        <table>
                            <tr><th>Profile Name</th><th>ID</th><th>Tags</th><th>P50</th><th>P90</th><th>Average</th></tr>
                            {% for contrib in contributions['30days'] %}
                            <tr>
                                <td>{{ contrib.profile_name }}</td>
                                <td>{{ contrib.profile_arn_id }}</td>
                                <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                <td>{{ "{:,.0f}".format(contrib.rpm_p50) }}</td>
                                <td>{{ "{:,.0f}".format(contrib.rpm_p90) }}</td>
                                <td>{{ "{:,.0f}".format(contrib.rpm_avg) }}</td>
                            </tr>
                            {% endfor %}
                        </table>
                    </div>
                    {% endif %}
                    <canvas id="rpm_30days"></canvas>
                </div>
                <div class="graph-container">
                    <h5>TPD (Tokens Per Day)</h5>
                    {% if contributions.get('30days') %}
                    <div class="contribution-table">
                        <table>
                            <tr><th>Profile Name</th><th>ID</th><th>Tags</th><th>P50</th><th>P90</th><th>Average</th></tr>
                            {% for contrib in contributions['30days'] %}
                            <tr>
                                <td>{{ contrib.profile_name }}</td>
                                <td>{{ contrib.profile_arn_id }}</td>
                                <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                <td>{{ "{:,.0f}".format(contrib.tpd_p50) }}</td>
                                <td>{{ "{:,.0f}".format(contrib.tpd_p90) }}</td>
                                <td>{{ "{:,.0f}".format(contrib.tpd_avg) }}</td>
                            </tr>
                            {% endfor %}
                        </table>
                    </div>
                    {% endif %}
                    <canvas id="tpd_30days"></canvas>
                </div>
                <div class="graph-container">
                    <h5>Invocation Throttles</h5>
                    {% if contributions.get('30days') %}
                    <div class="contribution-table">
                        <table>
                            <tr><th>Profile Name</th><th>ID</th><th>Tags</th><th>Total</th></tr>
                            {% for contrib in contributions['30days'] %}
                            <tr>
                                <td>{{ contrib.profile_name }}</td>
                                <td>{{ contrib.profile_arn_id }}</td>
                                <td>{% if contrib.profile_tags %}{% for key, value in contrib.profile_tags.items() %}{{ key }}={{ value }}<br>{% endfor %}{% else %}N/A{% endif %}</td>
                                <td>{{ "{:,.0f}".format(contrib.throttles) }}</td>
                            </tr>
                            {% endfor %}
                        </table>
                    </div>
                    {% endif %}
                    <canvas id="throttles_30days"></canvas>
                </div>
            </div>
        </div>
    </div>

    <script>
        const chartConfig = {
            type: 'line',
            options: {
                responsive: true,
                interaction: { mode: 'nearest', intersect: false, axis: 'x' },
                plugins: { 
                    tooltip: { mode: 'nearest', intersect: false, axis: 'x' },
                    crosshair: { line: { color: '#999', width: 1, dashPattern: [5, 5] }, sync: { enabled: true } }
                },
                scales: {
                    x: { 
                        type: 'time',
                        time: {
                            displayFormats: {
                                hour: 'HH:mm',
                                day: 'MMM dd HH:mm'
                            },
                            tooltipFormat: 'MMM dd, yyyy HH:mm'
                        },
                        ticks: {
                            source: 'auto',
                            autoSkip: true,
                            maxTicksLimit: 12,
                            callback: function(value, index, ticks) {
                                const date = new Date(value);
                                
                                // Show date on first tick
                                if (index === 0) {
                                    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' + 
                                           date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
                                }
                                
                                // Compare with previous tick
                                const prevDate = new Date(ticks[index - 1].value);
                                
                                // Show date if day, month, or year changed
                                if (date.getDate() !== prevDate.getDate() || 
                                    date.getMonth() !== prevDate.getMonth() || 
                                    date.getFullYear() !== prevDate.getFullYear()) {
                                    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' + 
                                           date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
                                }
                                
                                // Otherwise just show time
                                return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
                            }
                        },
                        display: true, 
                        title: { display: true, text: 'Time' } 
                    },
                    y: { display: true, title: { display: true, text: 'Value' } }
                }
            }
        };

        const timeSeriesData = {{ time_series_json | safe }};
        const quotas = {{ quotas_json | safe }};
        const profileNames = {{ profile_names_json | safe }};
        const colors = ['#4285F4', '#EA4335', '#FBBC04', '#34A853', '#FF6D00', '#46BDC6', '#7BAAF7', '#F07B72', '#FDD663', '#81C995'];
        
        // Create consistent color mapping for all profiles across all charts
        const profileColorMap = {};
        let nextColorIndex = 0;
        {% for time_period in time_periods.keys() %}
        for (const [modelId, data] of Object.entries(timeSeriesData['{{ time_period }}'] || {})) {
            if (modelId !== '__AGGREGATED__' && !profileColorMap[modelId]) {
                profileColorMap[modelId] = colors[nextColorIndex % colors.length];
                nextColorIndex++;
            }
        }
        {% endfor %}
        
        // Store chart instances by period for synchronization
        const chartsByPeriod = {
            '1hour': [],
            '1day': [],
            '7days': [],
            '14days': [],
            '30days': []
        };
        
        {% for time_period in time_periods.keys() %}
        
        ['TPM', 'RPM', 'TPD', 'InvocationThrottles'].forEach(metricType => {
            if (metricType === 'TPD' && '{{ time_period }}' === '1hour') return;
            
            const chartData = { datasets: [] };
            let aggregatedData = null;
            
            for (const [modelId, data] of Object.entries(timeSeriesData['{{ time_period }}'] || {})) {
                if (data[metricType] && data[metricType].timestamps.length > 0) {
                    if (modelId === '__AGGREGATED__') {
                        aggregatedData = {
                            label: 'Total (Aggregated)',
                            data: data[metricType].timestamps.map((ts, i) => ({ x: ts, y: data[metricType].values[i] })),
                            borderColor: '#FF8C00',
                            backgroundColor: '#FF8C0020',
                            borderWidth: 1.2,
                            borderDash: [10, 5],
                            pointRadius: 0.5,
                            tension: 0.1,
                            spanGaps: false
                        };
                    } else {
                        const profileName = profileNames[modelId] || modelId;
                        const profileColor = profileColorMap[modelId];
                        chartData.datasets.push({
                            label: profileName,
                            data: data[metricType].timestamps.map((ts, i) => ({ x: ts, y: data[metricType].values[i] })),
                            borderColor: profileColor,
                            backgroundColor: profileColor + '20',
                            borderWidth: 0.8,
                            pointRadius: 0.5,
                            tension: 0.1,
                            spanGaps: false
                        });
                    }
                }
            }
            
            // Add aggregated line first (so it appears on top in legend)
            if (aggregatedData) {
                chartData.datasets.unshift(aggregatedData);
            }
            
            // Add quota line if available
            let quotaValue = null;
            if (metricType === 'TPM' && quotas.tpm) quotaValue = quotas.tpm;
            else if (metricType === 'RPM' && quotas.rpm) quotaValue = quotas.rpm;
            else if (metricType === 'TPD' && quotas.tpd) quotaValue = quotas.tpd;
            
            if (quotaValue && chartData.datasets.length > 0) {
                // Get all timestamps from all datasets to find min/max
                const allTimestamps = chartData.datasets.flatMap(ds => ds.data.map(d => new Date(d.x).getTime()));
                if (allTimestamps.length > 0) {
                    const minTime = Math.min(...allTimestamps);
                    const maxTime = Math.max(...allTimestamps);
                    
                    // Create quota line spanning entire time range
                    chartData.datasets.push({
                        label: 'Quota Limit',
                        data: [
                            { x: new Date(minTime).toISOString(), y: quotaValue },
                            { x: new Date(maxTime).toISOString(), y: quotaValue }
                        ],
                        borderColor: 'red',
                        borderDash: [5, 5],
                        borderWidth: 2,
                        pointRadius: 0,
                        fill: false,
                        tension: 0
                    });
                }
            }
            
            const canvasId = metricType === 'InvocationThrottles' ? 'throttles_{{ time_period }}' : 
                            metricType.toLowerCase() + '_{{ time_period }}';
            
            const canvasElement = document.getElementById(canvasId);
            if (!canvasElement) {
                console.error(`Canvas element not found: ${canvasId}`);
                return;
            }
            
            // Skip chart creation if there's no data
            if (chartData.datasets.length === 0) {
                console.log(`No data for ${metricType} in {{ time_period }}, skipping chart`);
                return;
            }
            
            // Create chart with crosshair sync enabled for this period
            try {
                // Create unique tick callback with closure for this chart
                const createTickCallback = () => {
                    let lastDisplayedDateStr = null;
                    return function(value, index, ticks) {
                        const date = new Date(value);
                        const currentDateStr = date.toDateString();
                        
                        // Show date on first tick or when date changes
                        if (index === 0 || currentDateStr !== lastDisplayedDateStr) {
                            lastDisplayedDateStr = currentDateStr;
                            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' + 
                                   date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
                        }
                        
                        // Otherwise just show time
                        return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
                    };
                };
                
                // Calculate rounded min/max for cleaner X-axis
                const allTimestamps = chartData.datasets.flatMap(ds => ds.data.map(d => new Date(d.x).getTime()));
                if (allTimestamps.length > 0) {
                    const minTime = Math.min(...allTimestamps);
                    const maxTime = Math.max(...allTimestamps);
                    
                    // Round down to nearest 5 minutes for min
                    const minDate = new Date(minTime);
                    minDate.setMinutes(Math.floor(minDate.getMinutes() / 5) * 5, 0, 0);
                    
                    // Round up to nearest 5 minutes for max
                    const maxDate = new Date(maxTime);
                    maxDate.setMinutes(Math.ceil(maxDate.getMinutes() / 5) * 5, 0, 0);
                    
                    var xAxisMin = minDate.getTime();
                    var xAxisMax = maxDate.getTime();
                } else {
                    var xAxisMin = undefined;
                    var xAxisMax = undefined;
                }
                
                const chartInstance = new Chart(canvasElement, { 
                    ...chartConfig, 
                    data: chartData,
                    options: {
                        ...chartConfig.options,
                        scales: {
                            ...chartConfig.options.scales,
                            x: {
                                ...chartConfig.options.scales.x,
                                min: xAxisMin,
                                max: xAxisMax,
                                ticks: {
                                    ...chartConfig.options.scales.x.ticks,
                                    callback: createTickCallback(),
                                    maxRotation: 45,
                                    minRotation: 0,
                                    autoSkip: true
                                }
                            }
                        },
                        plugins: {
                            ...chartConfig.options.plugins,
                            crosshair: {
                                line: { color: '#999', width: 1, dashPattern: [5, 5] },
                                sync: { enabled: true, group: '{{ time_period }}' },
                                zoom: { enabled: false }
                            }
                        }
                    }
                });
                
                chartsByPeriod['{{ time_period }}'].push(chartInstance);
            } catch (error) {
                console.error(`Error creating chart ${canvasId}:`, error);
            }
        });
        
        {% endfor %}
    </script>
    
    <script>
        // Collapsible sections functionality
        var coll = document.getElementsByClassName("collapsible");
        for (var i = 0; i < coll.length; i++) {
            coll[i].addEventListener("click", function() {
                this.classList.toggle("active");
                var content = this.nextElementSibling;
                if (content.style.maxHeight) {
                    content.style.maxHeight = null;
                } else {
                    content.style.maxHeight = content.scrollHeight + "px";
                }
            });
        }
    </script>
</body>
</html>
        """


class BedrockAnalyzer:
    """Main orchestrator for Bedrock token usage analysis"""
    
    TIME_PERIODS = ["1hour", "1day", "7days", "14days", "30days"]
    
    def __init__(self, region, granularity_config):
        self.region = region
        self.granularity_config = granularity_config
        
        # Get local timezone - use system's local timezone
        local_dt = datetime.now().astimezone()
        self.local_tz = local_dt.tzinfo
        offset = local_dt.strftime('%z')
        self.tz_offset = f"{offset[:3]}:{offset[3:]}"  # +08:00 format
        self.tz_api_format = offset[:5]  # +0800 format for API
        
        # Initialize clients
        self.bedrock_client = boto3.client('bedrock', region_name=region)
        self.cloudwatch_client = boto3.client('cloudwatch', region_name=region)
        self.sq_client = boto3.client('service-quotas', region_name=region)
        self.profile_fetcher = InferenceProfileFetcher(self.bedrock_client)
        self.metrics_fetcher = CloudWatchMetricsFetcher(self.cloudwatch_client, self.tz_api_format)
        self.output_generator = OutputGenerator()
    
    def _load_quota_codes(self, model_id, profile_prefix=None):
        """Load quota codes for a model from FM list based on endpoint
        
        Args:
            model_id: Base model ID
            profile_prefix: Endpoint prefix (e.g., 'us', 'eu', 'global') or None for base endpoint
        
        Returns:
            dict: Quota codes for the specified endpoint (tpm, rpm, tpd, concurrent)
        """
        fm_file = f'metadata/fm-list-{self.region}.yml'
        
        if not os.path.exists(fm_file):
            return {}
        
        with open(fm_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            models = data.get('models', [])
            
            for model in models:
                if model['model_id'] == model_id:
                    endpoints = model.get('endpoints', {})
                    
                    # Determine which endpoint to use
                    endpoint_key = profile_prefix if profile_prefix else 'base'
                    
                    # Get quotas from the specified endpoint
                    if endpoint_key in endpoints:
                        return endpoints[endpoint_key].get('quotas', {})
                    
                    # Fallback to old structure for backward compatibility
                    return model.get('quotas', {})
        
        return {}
    
    def _fetch_quotas(self, model_id, quota_codes, profile_prefix=None):
        """Fetch quota values from Service Quotas API
        
        Args:
            model_id: Model ID
            quota_codes: Dictionary of quota type to L-code mappings
            profile_prefix: Endpoint prefix (e.g., 'us', 'eu', 'global') or None for base
        
        Returns:
            dict: Quota values (tpm, rpm, tpd)
        """
        quotas = {'tpm': None, 'rpm': None, 'tpd': None}
        
        if not quota_codes:
            return quotas
        
        logger.info(f"  Fetching quotas from Service Quotas API...")
        for quota_type, code in quota_codes.items():
            if code:
                try:
                    response = self.sq_client.get_service_quota(
                        ServiceCode='bedrock',
                        QuotaCode=code
                    )
                    value = response['Quota']['Value']
                    
                    if 'tpm' in quota_type.lower():
                        quotas['tpm'] = value
                    elif 'rpm' in quota_type.lower():
                        quotas['rpm'] = value
                    elif 'tpd' in quota_type.lower():
                        quotas['tpd'] = value
                
                except Exception as e:
                    logger.info(f"  Warning: Could not fetch {quota_type} quota for {model_id}: {e}")
        
        # Apply 2x multiplier for TPD on regional cross-region profiles
        # Regional profiles use on-demand TPD L-codes but have 2x the quota
        regional_profiles = ['us', 'eu', 'ap', 'apac', 'jp', 'au', 'ca']
        if profile_prefix in regional_profiles and quotas['tpd'] is not None:
            quotas['tpd'] = quotas['tpd'] * 2
        
        return quotas
    
    def _calculate_stats_from_time_series(self, ts_data, time_period):
        """Calculate statistics from time series data"""
        stats = self.metrics_fetcher._initialize_metrics(time_period)
        
        for metric_name in ts_data:
            if 'values' in ts_data[metric_name] and ts_data[metric_name]['values']:
                # Filter out None values (from sparse data handling)
                values = [v for v in ts_data[metric_name]['values'] if v is not None]
                stats[metric_name] = {
                    'values': values,
                    'p50': np.percentile(values, 50) if values else 0.0,
                    'p90': np.percentile(values, 90) if values else 0.0,
                    'count': len(values),
                    'sum': sum(values),
                    'avg': np.mean(values) if values else 0.0
                }
        
        return stats
    
    def _calculate_contributions(self, model_results, time_series_data, profile_names, profile_metadata):
        """Calculate average contributions for each profile per period"""
        logger.info(f"  Calculating profile contributions...")
        contributions = {}
        
        for time_period in model_results.keys():
            period_contributions = []
            
            for profile_id, stats in model_results[time_period].items():
                if profile_id == '__AGGREGATED__':
                    continue
                
                profile_name = profile_names.get(profile_id, profile_id)
                metadata = profile_metadata.get(profile_id, {'id': 'N/A', 'tags': {}})
                
                # Get p50, p90, avg for each metric
                contribution = {
                    'profile_id': profile_id,
                    'profile_name': profile_name,
                    'profile_arn_id': metadata['id'],
                    'profile_tags': metadata['tags'],
                    'tpm_p50': stats.get('TPM', {}).get('p50', 0),
                    'tpm_p90': stats.get('TPM', {}).get('p90', 0),
                    'tpm_avg': stats.get('TPM', {}).get('avg', 0),
                    'rpm_p50': stats.get('RPM', {}).get('p50', 0),
                    'rpm_p90': stats.get('RPM', {}).get('p90', 0),
                    'rpm_avg': stats.get('RPM', {}).get('avg', 0),
                    'tpd_p50': stats.get('TPD', {}).get('p50', 0) if time_period != '1hour' else 0,
                    'tpd_p90': stats.get('TPD', {}).get('p90', 0) if time_period != '1hour' else 0,
                    'tpd_avg': stats.get('TPD', {}).get('avg', 0) if time_period != '1hour' else 0,
                    'throttles': stats.get('InvocationThrottles', {}).get('sum', 0)
                }
                
                period_contributions.append(contribution)
            
            # Sort by TPM average (descending)
            period_contributions.sort(key=lambda x: x['tpm_avg'], reverse=True)
            contributions[time_period] = period_contributions
        
        return contributions
    
    def analyze(self, models):
        """Analyze token usage for given models"""
        
        # Step 0: Discover all profiles once for all models
        logger.info(f"\n{'='*80}")
        logger.info(f"Discovering inference profiles for {len(models)} model(s)...")
        logger.info(f"{'='*80}")
        
        all_profiles_map = {}  # {model_id: {profile_prefix: (final_model_ids, profile_names, profile_metadata)}}
        
        for model_config in models:
            model_id = model_config['model_id']
            profile_prefix = model_config['profile_prefix']
            
            if model_id not in all_profiles_map:
                all_profiles_map[model_id] = {}
            
            if profile_prefix not in all_profiles_map[model_id]:
                final_model_ids, profile_names, profile_metadata = self.profile_fetcher.find_profiles(model_id, profile_prefix)
                all_profiles_map[model_id][profile_prefix] = (final_model_ids, profile_names, profile_metadata)
                
                # Display profiles for this model
                profile_list = [profile_names.get(pid, pid) for pid in final_model_ids]
                logger.info(f"  {model_id} ({profile_prefix or 'base'}): {len(final_model_ids)} profile(s) - {', '.join(profile_list)}")
        
        logger.info(f"Profile discovery complete.\n")
        
        # Process each model
        for model_config in models:
            model_id = model_config['model_id']
            profile_prefix = model_config['profile_prefix']
            
            logger.info(f"\n{'='*80}")
            logger.info(f"Processing model: {model_id}")
            logger.info(f"{'='*80}")
            
            # Step 1: Get profiles from cache
            final_model_ids, profile_names, profile_metadata = all_profiles_map[model_id][profile_prefix]
            logger.info(f"Using {len(final_model_ids)} profile(s)")
            
            # Step 2: Fetch quotas
            quota_codes = self._load_quota_codes(model_id, profile_prefix)
            quotas = self._fetch_quotas(model_id, quota_codes, profile_prefix)
            if any(quotas.values()):
                logger.info(f"  Quotas: TPM={quotas['tpm']}, RPM={quotas['rpm']}, TPD={quotas['tpd']}")
            
            # Step 3: Fetch all data upfront with configured granularities
            # Data reuse optimization: if all periods use same granularity, only fetch once
            # If granularities differ, fetch separately for each unique granularity
            logger.info(f"  Fetching data with configured granularities (parallel)...")
            cached_data_all_profiles = self.metrics_fetcher.fetch_all_data_mixed_granularity(
                final_model_ids, 
                self.granularity_config,
                cached_data=None  # First fetch, no cache
            )
            
            model_results = {}
            time_series_data = {}
            
            # Step 4: Process each time period
            for time_period in self.TIME_PERIODS:
                logger.info(f"  Processing {time_period}...")
                
                period_stats = {}
                period_time_series = {}
                
                try:
                    for final_model_id in final_model_ids:
                        # Slice data from cached datasets
                        if final_model_id in cached_data_all_profiles:
                            ts_data = self.metrics_fetcher.slice_and_process_data(
                                cached_data_all_profiles[final_model_id], 
                                time_period,
                                self.granularity_config
                            )
                            period_time_series[final_model_id] = ts_data
                            
                            # Calculate statistics from time series data
                            stats = self._calculate_stats_from_time_series(ts_data, time_period)
                            period_stats[final_model_id] = stats
                    
                    # Always create aggregated metrics for consistent template behavior
                    agg_stats = self.metrics_fetcher.aggregate_statistics(period_stats, time_period)
                    agg_ts = self.metrics_fetcher.aggregate_time_series(period_time_series, time_period)
                    
                    period_stats['__AGGREGATED__'] = agg_stats
                    period_time_series['__AGGREGATED__'] = agg_ts
                    
                    model_results[time_period] = period_stats
                    time_series_data[time_period] = period_time_series
                    
                except Exception as e:
                    import traceback
                    logger.info(f"\n  ERROR in {time_period} processing:")
                    logger.info(f"  Error type: {type(e).__name__}")
                    logger.info(f"  Error message: {e}")
                    logger.info(f"  Traceback:")
                    traceback.print_exc()
                    raise
            
            # Step 5: Calculate contributions
            contributions = self._calculate_contributions(model_results, time_series_data, profile_names, profile_metadata)
            
            # Step 6: Generate output
            logger.info(f"  Generating output files...")
            end_time_local = datetime.now(self.local_tz)
            self.output_generator.generate({
                model_id: {
                    'stats': model_results,
                    'time_series': time_series_data,
                    'quotas': quotas,
                    'profile_names': profile_names,
                    'contributions': contributions,
                    'granularity_config': self.granularity_config,
                    'end_time': end_time_local,
                    'tz_offset': self.tz_offset,
                    'region': self.region
                }
            })


def main():
    try:
        user_inputs = UserInputs()
        user_inputs.collect()
        
        analyzer = BedrockAnalyzer(user_inputs.region, user_inputs.granularity_config)
        analyzer.analyze(user_inputs.models)
        
        logger.info(f"\nCompleted! Check the 'results' directory for output files.")
        
    except KeyboardInterrupt:
        logger.info("\nOperation cancelled by user.")
    except Exception as e:
        logger.info(f"Error: {e}")


if __name__ == "__main__":
    main()
