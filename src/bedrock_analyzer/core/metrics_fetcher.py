"""CloudWatch metrics fetching for Bedrock usage analysis"""

import os
import boto3
import numpy as np
from datetime import datetime, timedelta, timezone
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

logger = logging.getLogger(__name__)

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


