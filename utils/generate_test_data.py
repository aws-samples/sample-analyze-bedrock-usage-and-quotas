#!/usr/bin/env python3

import boto3
import time
import random
import yaml
import uuid

def load_config():
    """Load test configuration"""
    with open('./code/utils/test_config.yaml', 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def get_inference_profile_arn(bedrock_client, model_id, profile_prefix, region):
    """Get the ARN of a system-defined inference profile"""
    try:
        paginator = bedrock_client.get_paginator('list_inference_profiles')
        
        for page in paginator.paginate():
            for profile in page.get('inferenceProfileSummaries', []):
                profile_id = profile.get('inferenceProfileId', '')
                
                # Match the profile ID pattern: prefix.model_id
                if profile_id == f"{profile_prefix}.{model_id}":
                    return profile.get('inferenceProfileArn')
        
        return None
        
    except Exception as e:
        print(f"  Error listing inference profiles: {e}")
        return None


def create_application_inference_profile(bedrock_client, model_id, profile_prefix, region):
    """Create an application inference profile for tracking"""
    # Determine source ARN
    if profile_prefix and profile_prefix != 'null':
        # Get system-defined inference profile ARN
        source_arn = get_inference_profile_arn(bedrock_client, model_id, profile_prefix, region)
        if not source_arn:
            print(f"✗ Could not find inference profile: {profile_prefix}.{model_id}")
            return None
    else:
        # Use foundation model ARN
        source_arn = f"arn:aws:bedrock:{region}::foundation-model/{model_id}"
    
    profile_name = f"test-profile-{model_id.replace('.', '-').replace(':', '-')}-{uuid.uuid4().hex[:8]}"
    
    # Generate mock tags for the profile
    import random
    projects = ['analytics', 'chatbot', 'summarization', 'translation']
    environments = ['development', 'staging', 'production', 'testing']
    teams = ['data-science', 'ai-platform', 'content-ops', 'ml-engineering']
    
    tags = [
        {'key': 'project', 'value': random.choice(projects)},
        {'key': 'environment', 'value': random.choice(environments)},
        {'key': 'team', 'value': random.choice(teams)}
    ]
    
    try:
        response = bedrock_client.create_inference_profile(
            inferenceProfileName=profile_name,
            description=f"Test application inference profile for {model_id}",
            modelSource={'copyFrom': source_arn},
            tags=tags
        )
        
        profile_arn = response['inferenceProfileArn']
        print(f"✓ Created application inference profile: {profile_name}")
        print(f"  Source: {source_arn}")
        print(f"  ARN: {profile_arn}")
        print(f"  Tags: {', '.join([f'{t['key']}={t['value']}' for t in tags])}")
        return profile_arn
        
    except Exception as e:
        print(f"✗ Failed to create application inference profile: {e}")
        print(f"  Attempted source: {source_arn}")
        return None

def run_test_inferences(config):
    """Run test inferences to generate CloudWatch metrics data"""
    region = config['region']
    models = config['models']
    
    bedrock_client = boto3.client('bedrock', region_name=region)
    bedrock_runtime = boto3.client('bedrock-runtime', region_name=region)
    
    # Create application inference profiles for each model
    print("Creating application inference profiles...\n")
    profile_arns = []
    
    for model_config in models:
        model_id = model_config['model_id']
        profile_prefix = model_config.get('profile_prefix')
        
        profile_arn = create_application_inference_profile(
            bedrock_client, model_id, profile_prefix, region
        )
        
        if profile_arn:
            profile_arns.append({
                'arn': profile_arn,
                'model_id': model_id,
                'profile_prefix': profile_prefix
            })
        else:
            print(f"⚠ Skipping {model_id} - failed to create profile")
    
    if not profile_arns:
        print("\n✗ No application inference profiles created. Exiting.")
        return
    
    print(f"\n✓ Created {len(profile_arns)} application inference profiles")
    
    test_messages = [
        "Hello, how are you?",
        "What is the capital of France?",
        "Explain quantum computing in simple terms.",
        "Write a short poem about technology.",
        "What are the benefits of cloud computing?"
    ]
    
    print(f"\nRunning test inferences in {region} over 15 minutes...")
    
    start_time = time.time()
    end_time = start_time + (5 * 60)  # 5 minutes
    
    inference_count = 0
    
    while time.time() < end_time:
        for profile_info in profile_arns:
            profile_arn = profile_info['arn']
            model_id = profile_info['model_id']
            
            try:
                message = random.choice(test_messages)
                
                response = bedrock_runtime.converse(
                    modelId=profile_arn,
                    messages=[{
                        'role': 'user',
                        'content': [{'text': message}]
                    }],
                    inferenceConfig={
                        'maxTokens': 100,
                        'temperature': 0.7
                    }
                )
                
                inference_count += 1
                print(f"Inference {inference_count}: {model_id} via app profile - {message[:30]}...")
                
            except Exception as e:
                print(f"Inference failed for {model_id}: {e}")
        
        # Sleep for 0.5-1 minutes between batches
        sleep_time = random.randint(30, 60)
        remaining_time = end_time - time.time()
        
        if remaining_time > sleep_time:
            print(f"Sleeping for {sleep_time} seconds...")
            time.sleep(sleep_time)
        else:
            break
    
    print(f"\nCompleted {inference_count} test inferences")
    print("Waiting 5 minutes for metrics to propagate to CloudWatch...")
    #time.sleep(300)

def main():
    print("Starting test data generation for Bedrock token usage statistics...")
    
    try:
        config = load_config()
        run_test_inferences(config)
        
        print("\nTest data generation completed!")
        print("You can now run test_usage_analysis.py to analyze the metrics.")
        
    except KeyboardInterrupt:
        print("\nData generation cancelled by user.")
    except Exception as e:
        print(f"Data generation failed: {e}")

if __name__ == "__main__":
    main()
