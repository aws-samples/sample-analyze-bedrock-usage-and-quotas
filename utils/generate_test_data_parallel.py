#!/usr/bin/env python3
"""
Parallel stress test for Bedrock inference data generation.
Runs concurrent inferences to generate high-volume metrics for testing.
"""

import boto3
import time
import random
import yaml
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_inference_profile_arn(bedrock_client, model_id, profile_prefix, region):
    """Get the ARN of a system-defined inference profile"""
    try:
        paginator = bedrock_client.get_paginator('list_inference_profiles')
        for page in paginator.paginate():
            for profile in page.get('inferenceProfileSummaries', []):
                profile_id = profile.get('inferenceProfileId', '')
                if profile_id == f"{profile_prefix}.{model_id}":
                    return profile.get('inferenceProfileArn')
        return None
    except Exception as e:
        print(f"  Error listing inference profiles: {e}")
        return None

def create_application_inference_profile(bedrock_client, model_id, profile_prefix, region):
    """Create an application inference profile for tracking"""
    if profile_prefix and profile_prefix != 'null':
        source_arn = get_inference_profile_arn(bedrock_client, model_id, profile_prefix, region)
        if not source_arn:
            print(f"✗ Could not find inference profile: {profile_prefix}.{model_id}")
            return None
    else:
        source_arn = f"arn:aws:bedrock:{region}::foundation-model/{model_id}"
    
    profile_name = f"stress-test-{model_id.replace('.', '-').replace(':', '-')}-{uuid.uuid4().hex[:8]}"
    
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
            description=f"Stress test application inference profile for {model_id}",
            modelSource={'copyFrom': source_arn},
            tags=tags
        )
        profile_arn = response['inferenceProfileArn']
        print(f"✓ Created application inference profile: {profile_name}")
        print(f"  Tags: {', '.join([f'{t['key']}={t['value']}' for t in tags])}")
        return profile_arn
    except Exception as e:
        print(f"✗ Failed to create application inference profile: {e}")
        return None

def invoke_bedrock(bedrock_runtime, profile_arn, message, worker_id, iteration):
    """Single Bedrock inference call using converse API"""
    try:
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
        return {'worker': worker_id, 'iteration': iteration, 'status': 'success'}
    except Exception as e:
        return {'worker': worker_id, 'iteration': iteration, 'status': 'error', 'error': str(e)}

def stress_test(config_file='code/utils/test_config.yaml'):
    """Run parallel stress test"""
    with open(config_file, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    model_id = config['models'][0]['model_id']
    profile_prefix = config['models'][0].get('profile_prefix')
    region = config['region']
    workers = config.get('parallel_workers', 10)
    iterations = config.get('iterations_per_worker', 10)
    duration_minutes = config.get('duration_minutes', 5)
    
    bedrock_client = boto3.client('bedrock', region_name=region)
    bedrock_runtime = boto3.client('bedrock-runtime', region_name=region)
    
    print(f"Creating application inference profile...")
    profile_arn = create_application_inference_profile(bedrock_client, model_id, profile_prefix, region)
    
    if not profile_arn:
        print("✗ Failed to create profile. Exiting.")
        return
    
    test_messages = [
        """Artificial intelligence and machine learning have revolutionized numerous industries over the past decade, transforming how businesses operate and how people interact with technology. From healthcare diagnostics to financial fraud detection, these technologies have demonstrated remarkable capabilities in solving complex problems that were previously thought to require human intelligence.

In the healthcare sector, AI-powered diagnostic tools are now capable of detecting diseases like cancer, diabetic retinopathy, and cardiovascular conditions with accuracy rates that match or exceed human experts. Machine learning algorithms analyze medical images, patient records, and genetic data to identify patterns and predict health outcomes. Hospitals and clinics worldwide are implementing these systems to improve patient care, reduce diagnostic errors, and optimize treatment plans.

The financial services industry has embraced AI for fraud detection, algorithmic trading, risk assessment, and customer service automation. Banks use machine learning models to analyze transaction patterns in real-time, identifying suspicious activities and preventing fraudulent transactions before they occur. Investment firms employ sophisticated algorithms to analyze market trends, execute trades, and manage portfolios with minimal human intervention.

Education technology has been transformed by adaptive learning systems that personalize educational content based on individual student performance and learning styles. These AI-powered platforms track student progress, identify knowledge gaps, and adjust difficulty levels automatically. Virtual tutors and intelligent assessment systems provide immediate feedback, helping students learn more effectively while reducing the burden on educators.

Transportation and logistics have seen significant improvements through AI optimization algorithms that route vehicles efficiently, predict maintenance needs, and manage supply chains. Autonomous vehicle technology continues to advance, with self-driving cars and trucks being tested in various environments. Delivery companies use machine learning to optimize routes, predict delivery times, and manage warehouse operations.

However, the rapid advancement of AI technologies raises important ethical considerations. Issues of bias in algorithms, privacy concerns related to data collection, job displacement due to automation, and the potential for misuse of AI systems require careful attention. Organizations must implement responsible AI practices, ensuring transparency, fairness, and accountability in their systems.

The challenge of algorithmic bias is particularly concerning, as AI systems trained on historical data may perpetuate or amplify existing societal biases. This can lead to discriminatory outcomes in hiring, lending, criminal justice, and other critical areas. Researchers and practitioners are developing techniques to detect and mitigate bias, but this remains an ongoing challenge.

Data privacy and security are paramount concerns as AI systems require vast amounts of data to function effectively. Organizations must balance the need for data with individuals' rights to privacy and control over their personal information. Regulations like GDPR and CCPA have established frameworks for data protection, but enforcement and compliance remain challenging.

Looking ahead, we can expect continued advancement in areas like natural language processing, computer vision, reinforcement learning, and neural architecture search. Quantum computing may eventually enable new classes of AI algorithms that are currently impractical. Edge AI will bring intelligence to devices, reducing latency and improving privacy by processing data locally rather than in the cloud.

The integration of AI into everyday life will continue to accelerate, with smart homes, personalized healthcare, autonomous systems, and intelligent assistants becoming increasingly sophisticated. Organizations that successfully adopt and implement AI technologies while addressing ethical concerns will gain significant competitive advantages. The key to success lies in thoughtful implementation, continuous monitoring, and a commitment to responsible innovation that benefits society as a whole.""",

        """Cloud-based microservices architecture has become the de facto standard for building scalable, resilient, and maintainable applications in modern software development. This architectural approach breaks down monolithic applications into smaller, independently deployable services that communicate through well-defined APIs. Each microservice focuses on a specific business capability and can be developed, deployed, and scaled independently.

The foundation of a robust microservices architecture begins with proper service decomposition. Services should be designed around business domains following Domain-Driven Design principles. Each service should have a single responsibility, maintain its own data store, and expose a clear API contract. This separation allows teams to work independently, choose appropriate technologies for each service, and deploy updates without affecting the entire system.

Load balancing is critical for distributing traffic across multiple instances of services. Application Load Balancers (ALB) or Network Load Balancers (NLB) in cloud environments can route requests based on various criteria including path, headers, or geographic location. Implementing health checks ensures that traffic is only sent to healthy instances. Auto-scaling groups automatically adjust the number of instances based on metrics like CPU utilization, request count, or custom application metrics.

Database architecture in microservices requires careful consideration. Each service should own its data and not share databases with other services. This ensures loose coupling and allows services to evolve independently. Database sharding distributes data across multiple database instances based on a shard key, improving performance and scalability. Read replicas can handle read-heavy workloads, while write operations go to the primary database.

Caching strategies significantly improve performance and reduce database load. Implement multi-level caching with in-memory caches like Redis or Memcached for frequently accessed data. Content Delivery Networks (CDN) cache static assets closer to users. Application-level caching stores computed results, API responses, and session data. Cache invalidation strategies must be carefully designed to ensure data consistency.

API Gateway patterns provide a single entry point for clients, handling cross-cutting concerns like authentication, rate limiting, request routing, and protocol translation. The gateway can aggregate responses from multiple services, transform data formats, and implement circuit breakers to prevent cascading failures. Popular API gateway solutions include Kong, AWS API Gateway, and custom implementations using NGINX or Envoy.

Service mesh implementations like Istio or Linkerd provide advanced networking capabilities including service discovery, load balancing, failure recovery, metrics collection, and distributed tracing. The service mesh operates at the infrastructure layer, allowing developers to focus on business logic while the mesh handles communication concerns. Sidecar proxies intercept all network traffic, enabling fine-grained control and observability.

Monitoring and observability are essential for understanding system behavior and troubleshooting issues. Implement the three pillars of observability: metrics, logs, and traces. Metrics provide quantitative measurements of system performance. Centralized logging aggregates logs from all services for analysis. Distributed tracing tracks requests as they flow through multiple services, helping identify bottlenecks and failures.

Disaster recovery procedures must be planned and tested regularly. Implement backup strategies for all data stores with automated backups and point-in-time recovery capabilities. Design for multi-region deployment to survive regional outages. Use chaos engineering practices to test system resilience by deliberately introducing failures. Document runbooks for common failure scenarios and conduct regular disaster recovery drills.

Security best practices include implementing defense in depth with multiple layers of security controls. Use mutual TLS for service-to-service communication. Implement proper authentication and authorization using OAuth 2.0 and JWT tokens. Encrypt data at rest and in transit. Regularly scan container images for vulnerabilities. Implement network segmentation and use security groups to restrict traffic. Follow the principle of least privilege for all service accounts and IAM roles.""",

        """Quantum computing represents a fundamental paradigm shift in computation, leveraging the principles of quantum mechanics to process information in ways that are impossible for classical computers. The theoretical foundations of quantum computing were laid in the 1980s by physicists like Richard Feynman and David Deutsch, who recognized that quantum systems could be harnessed to perform certain calculations exponentially faster than classical computers.

The concept of a quantum bit, or qubit, is central to quantum computing. Unlike classical bits that exist in either a 0 or 1 state, qubits can exist in a superposition of both states simultaneously. This property, combined with quantum entanglement, allows quantum computers to explore multiple solution paths in parallel. When measured, a qubit collapses to either 0 or 1, but during computation, it exists in a probabilistic state that encodes information in a fundamentally different way than classical bits.

Early theoretical work established that quantum computers could solve certain problems much faster than classical computers. Peter Shor's algorithm, developed in 1994, demonstrated that quantum computers could factor large numbers exponentially faster than the best-known classical algorithms. This discovery had profound implications for cryptography, as most modern encryption systems rely on the difficulty of factoring large numbers. Lov Grover's search algorithm, developed in 1996, showed that quantum computers could search unsorted databases quadratically faster than classical computers.

The path from theory to practical implementation has been challenging. Building quantum computers requires maintaining qubits in quantum states long enough to perform useful computations, a challenge known as quantum coherence. Environmental noise, temperature fluctuations, and electromagnetic interference can cause qubits to lose their quantum properties through a process called decoherence. Researchers have developed various approaches to building qubits, each with different advantages and challenges.

Superconducting qubits, used by companies like IBM, Google, and Rigetti, operate at temperatures near absolute zero. These qubits are created using superconducting circuits that can exist in quantum superposition states. Google's Sycamore processor achieved quantum supremacy in 2019 by performing a specific calculation in 200 seconds that would take classical supercomputers thousands of years. However, superconducting qubits require extremely low temperatures and sophisticated error correction.

Trapped ion quantum computers, developed by companies like IonQ and Honeywell, use individual atoms held in place by electromagnetic fields as qubits. Laser pulses manipulate the quantum states of these ions to perform computations. Trapped ion systems have demonstrated high-fidelity quantum gates and long coherence times, but scaling to large numbers of qubits presents engineering challenges. The precision required to control individual ions becomes increasingly difficult as systems grow larger.

Topological qubits represent a promising but still largely theoretical approach to quantum computing. Microsoft is investing heavily in this technology, which would use exotic quantum states called anyons that are inherently protected from certain types of errors. If successful, topological qubits could be more stable and require less error correction than other approaches, but creating and manipulating anyons remains an unsolved challenge.

Current quantum computers are in the Noisy Intermediate-Scale Quantum (NISQ) era, characterized by systems with 50-1000 qubits that are prone to errors. These systems can perform some useful calculations but require error mitigation techniques and are limited in the complexity of problems they can solve. Researchers are working on quantum error correction codes that can detect and correct errors, but these require significant qubit overhead.

Potential applications of quantum computing span numerous fields. In cryptography, quantum computers threaten current encryption methods but also enable quantum key distribution for provably secure communication. Drug discovery could be revolutionized by quantum simulations of molecular interactions, allowing researchers to design new medicines more efficiently. Optimization problems in logistics, finance, and machine learning could benefit from quantum algorithms that explore solution spaces more effectively than classical approaches.

The future of quantum computing likely involves hybrid classical-quantum systems where quantum processors handle specific tasks while classical computers manage overall computation. As hardware improves and error rates decrease, quantum computers will tackle increasingly complex problems. The development of quantum algorithms, programming languages, and software tools continues to advance, preparing for the day when quantum computers become practical tools for solving real-world problems. Major technology companies, governments, and research institutions worldwide are investing billions of dollars in quantum computing research, recognizing its potential to transform computing and solve problems currently beyond our reach.""",

        """Creating a sustainable technology startup focused on renewable energy solutions for urban environments requires careful planning, market analysis, and strategic execution. The global transition to renewable energy presents significant opportunities for innovative companies that can address the unique challenges of urban energy systems. Cities consume over two-thirds of global energy and produce more than 70% of carbon emissions, making them critical targets for sustainable energy solutions.

The market for urban renewable energy solutions is experiencing rapid growth driven by climate change concerns, government policies, and decreasing costs of renewable technologies. Solar panel costs have dropped by over 90% in the past decade, while battery storage technology has improved dramatically. Cities worldwide are setting ambitious carbon neutrality goals, creating demand for innovative energy solutions. The global smart city market is projected to reach hundreds of billions of dollars in the coming years, with energy management being a key component.

Our competitive landscape includes established energy companies transitioning to renewables, technology giants entering the energy sector, and numerous startups focusing on specific niches. However, most solutions are designed for suburban or rural environments and don't address the unique constraints of dense urban areas. Our competitive advantage lies in developing integrated systems specifically optimized for urban environments, combining solar, wind, energy storage, and smart grid technologies in ways that maximize efficiency in space-constrained settings.

The revenue model will be multi-faceted, including direct sales of hardware systems, subscription-based energy management software, installation and maintenance services, and energy-as-a-service offerings where customers pay for energy consumption rather than equipment. We'll also generate revenue through carbon credit trading, demand response programs, and partnerships with utilities. This diversified approach reduces risk and creates multiple paths to profitability.

Our go-to-market strategy focuses initially on commercial buildings and multi-family residential complexes in major metropolitan areas. These customers have significant energy costs, available roof and facade space, and strong incentives to reduce carbon footprints. We'll establish partnerships with property management companies, real estate developers, and architectural firms to integrate our solutions into new construction and renovation projects. A direct sales team will target large commercial customers while channel partners will serve smaller clients.

The technology stack combines cutting-edge hardware and software components. Our proprietary solar panels are designed for vertical installation on building facades, capturing sunlight throughout the day as the sun's angle changes. Small-scale wind turbines integrate into building designs, generating power from urban wind patterns. Advanced battery systems store excess energy for use during peak demand periods. An AI-powered energy management platform optimizes energy generation, storage, and consumption in real-time, learning from usage patterns and weather forecasts.

The team structure includes experienced executives from the renewable energy and technology sectors. Our CEO has 20 years of experience in clean energy, having led successful exits at two previous startups. The CTO brings deep expertise in power systems and IoT from a major technology company. The VP of Sales has established relationships with key customers and partners. We'll build teams in engineering, sales, operations, and customer success, scaling from 15 employees in year one to over 100 by year five.

Funding requirements total $25 million over the first three years. Seed funding of $3 million will support product development and initial market testing. Series A of $10 million in year two will fund manufacturing scale-up and market expansion. Series B of $12 million in year three will support geographic expansion and new product development. We're targeting venture capital firms specializing in clean technology, strategic investors from the energy sector, and impact investors focused on climate solutions.

Financial projections show revenue growing from $2 million in year one to $50 million by year five, with gross margins improving from 30% to 45% as we achieve manufacturing scale. We expect to reach cash flow breakeven in year four and profitability in year five. Key assumptions include capturing 2% of the addressable market in our initial cities, average contract values of $500,000 for commercial installations, and customer acquisition costs declining as brand recognition grows.

Risk assessment identifies several key challenges. Technology risk includes potential delays in product development or performance issues with new technologies. Market risk involves slower-than-expected adoption or increased competition. Regulatory risk includes changes in renewable energy incentives or building codes. Financial risk involves difficulty raising capital or higher-than-expected costs. Mitigation strategies include maintaining strong relationships with research institutions for technology support, diversifying across multiple customer segments and geographies, actively engaging with policymakers, and maintaining conservative financial planning with adequate cash reserves. Success requires excellent execution, strong partnerships, and unwavering commitment to our mission of accelerating urban sustainability.""",

        """Modern software development has evolved dramatically over the past two decades, with new practices, tools, and methodologies transforming how teams build and deliver software. DevOps represents a cultural and technical movement that breaks down traditional barriers between development and operations teams, emphasizing collaboration, automation, and continuous improvement. This approach recognizes that software delivery is not just about writing code but encompasses the entire lifecycle from development through production deployment and ongoing operations.

Continuous Integration and Continuous Deployment (CI/CD) pipelines automate the process of building, testing, and deploying software. Developers commit code changes frequently, triggering automated builds that compile code, run unit tests, perform static code analysis, and generate artifacts. Continuous Deployment extends this by automatically deploying successful builds to production environments. Tools like Jenkins, GitLab CI, GitHub Actions, and CircleCI orchestrate these pipelines, providing visibility into build status and deployment progress.

Infrastructure as Code (IaC) treats infrastructure configuration as software, storing it in version control and applying software development practices to infrastructure management. Tools like Terraform, CloudFormation, and Pulumi allow teams to define infrastructure using declarative or imperative code. This approach ensures consistency across environments, enables rapid provisioning of resources, and provides an audit trail of infrastructure changes. IaC eliminates manual configuration errors and makes infrastructure reproducible and version-controlled.

Containerization with Docker has revolutionized application packaging and deployment. Containers package applications with all their dependencies, ensuring consistency across development, testing, and production environments. Docker images are lightweight, start quickly, and provide isolation between applications. Container registries store and distribute images, while orchestration platforms manage container lifecycles. The container ecosystem includes tools for security scanning, image optimization, and multi-stage builds.

Kubernetes has become the de facto standard for container orchestration, managing the deployment, scaling, and operation of containerized applications across clusters of machines. Kubernetes provides service discovery, load balancing, automated rollouts and rollbacks, self-healing capabilities, and configuration management. The platform's declarative approach allows teams to describe desired state, and Kubernetes works to maintain that state. However, Kubernetes complexity requires significant expertise and careful planning.

Serverless architectures abstract away infrastructure management, allowing developers to focus purely on code. Functions-as-a-Service platforms like AWS Lambda, Azure Functions, and Google Cloud Functions execute code in response to events, automatically scaling based on demand. Serverless reduces operational overhead and costs for certain workloads, particularly those with variable or unpredictable traffic patterns. However, serverless introduces challenges around cold starts, debugging, and vendor lock-in.

Test-Driven Development (TDD) emphasizes writing tests before implementing functionality. Developers write a failing test, implement the minimum code to make it pass, then refactor while keeping tests green. This approach ensures comprehensive test coverage, drives better design decisions, and provides confidence when refactoring. TDD requires discipline and can slow initial development, but pays dividends in code quality and maintainability.

Behavior-Driven Development (BDD) extends TDD by focusing on system behavior from a user perspective. BDD uses natural language specifications that describe features in terms of scenarios with given-when-then statements. Tools like Cucumber and SpecFlow translate these specifications into executable tests. BDD improves communication between technical and non-technical stakeholders, ensuring everyone shares a common understanding of requirements.

Agile methodologies like Scrum and Kanban provide frameworks for iterative development and continuous improvement. Scrum organizes work into time-boxed sprints with defined ceremonies including sprint planning, daily standups, sprint reviews, and retrospectives. Kanban visualizes work in progress, limits WIP to prevent overload, and focuses on flow optimization. Both approaches emphasize customer collaboration, responding to change, and delivering working software frequently.

Code review processes ensure code quality, knowledge sharing, and collective code ownership. Pull requests or merge requests allow team members to review changes before merging into main branches. Effective code reviews check for correctness, maintainability, security issues, and adherence to coding standards. Automated tools can enforce style guidelines and catch common issues, allowing human reviewers to focus on logic and design. Code review culture should be constructive and educational, not punitive.

Version control strategies like Git Flow, GitHub Flow, and trunk-based development provide structure for managing code changes. Git Flow uses multiple long-lived branches for features, releases, and hotfixes. GitHub Flow simplifies this with a single main branch and feature branches. Trunk-based development emphasizes frequent integration to a single branch with feature flags controlling functionality. The choice depends on team size, release frequency, and deployment practices. Regardless of strategy, clear branching conventions and commit message standards improve collaboration and code history clarity."""
    ]
    
    print(f"\nStarting stress test:")
    print(f"  Model: {model_id}")
    print(f"  Workers: {workers}")
    print(f"  Iterations per batch: {iterations}")
    print(f"  Duration: {duration_minutes} minutes")
    
    start_time = time.time()
    end_time = start_time + (duration_minutes * 60)
    results = {'success': 0, 'error': 0, 'total': 0}
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        while time.time() < end_time:
            futures = []
            for worker_id in range(workers):
                for iteration in range(iterations):
                    message = random.choice(test_messages)
                    future = executor.submit(invoke_bedrock, bedrock_runtime, profile_arn, message, worker_id, iteration)
                    futures.append(future)
            
            for future in as_completed(futures):
                result = future.result()
                results['total'] += 1
                if result['status'] == 'success':
                    results['success'] += 1
                else:
                    results['error'] += 1
                    print(f"Error: {result.get('error', 'Unknown')}")
                
                if results['total'] % 100 == 0:
                    elapsed = time.time() - start_time
                    rate = results['total'] / elapsed
                    print(f"Progress: {results['total']} requests, {rate:.1f} req/s, "
                          f"{results['success']} success, {results['error']} errors")
    
    elapsed = time.time() - start_time
    print(f"\nStress test complete:")
    print(f"  Total: {results['total']}")
    print(f"  Success: {results['success']}")
    print(f"  Errors: {results['error']}")
    print(f"  Duration: {elapsed:.1f}s")
    print(f"  Rate: {results['total']/elapsed:.1f} req/s")

if __name__ == '__main__':
    stress_test()
