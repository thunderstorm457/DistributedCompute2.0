DistributedCompute
A Self-Extending Volunteer Distributed Computing Network
About the Project

DistributedCompute is an ambitious open-source project that aims to build a large-scale, volunteer-powered distributed computing network.

The idea is simple: allow people to contribute the unused computing resources of their Apple Silicon Macs to a shared computing network.

Instead of relying on a single supercomputer or expensive centralized infrastructure, DistributedCompute combines the computing power of many independently owned computers into one coordinated system.

The network is designed to use the CPU, GPU, unified memory (RAM), Metal graphics acceleration, and Apple Neural Engine where supported.

Our goal is to create a general-purpose distributed computing platform capable of handling a wide variety of workloads, including:

General-purpose CPU computation.
GPU-accelerated mathematical computation.
Graphics and rendering workloads.
Artificial intelligence and machine learning.
Data processing and scientific computation.
Other workloads that can be divided into independent tasks.

The long-term vision is to create a computing network that grows stronger as more people participate.

The Self-Extending Network

One of DistributedCompute's central design principles is self-extension.

As more volunteers join, the network gains additional computing resources. Some eligible and authorized nodes can also contribute infrastructure services that help operate and expand the network.

This means that new participants don't just provide computing power. They can also help increase the network's ability to coordinate, store information, distribute workloads, and support additional participants.

The network will initially require bootstrap infrastructure, but its long-term architecture is intended to support increasing decentralization and redundancy.

Node Architecture

Every participating computer runs the same core Node Agent. The provisioning system determines which role each computer is assigned.

There are four primary node roles:

1. Main Compute Nodes

These are the primary computational workers of DistributedCompute.

They execute workloads assigned by the network and contribute their available CPU, GPU, memory, and supported AI acceleration resources.

2. Provisioning Nodes

These help coordinate network membership, node registration, resource allocation, and infrastructure management.

They assist the network in admitting new computers and determining how resources should be distributed.

3. ID Storage Nodes

These maintain replicated records of computer identities and associated network information.

Every registered computer receives a permanent node ID from the provisioning system.

When a volunteer restarts their computer, the Node Agent uses its locally stored cryptographic identity to authenticate with the network and recover its existing identity and assigned configuration.

Private cryptographic keys remain on the volunteer's computer.

4. Load Balancer Nodes

These help distribute computational workloads across available nodes.

They assist with preventing individual nodes from becoming overloaded and help the network make better use of its available computing resources.

How Volunteers Join

The intended volunteer experience is as automated as possible.

A volunteer downloads NodeScout.
NodeScout examines their computer and benchmarks its hardware.
A standardized benchmark report is generated.
The Node Agent submits the report to the provisioning infrastructure.
The provisioning system evaluates hardware capabilities and current network demand.
The provisioning system issues a permanent node ID and assigns an appropriate role.
The Node Agent authenticates and begins participating in the network.

Volunteers should not need to manually configure complicated distributed computing infrastructure.

The eventual goal is a simple installation and automatic participation experience.

Core Software Components
NodeScout

NodeScout is the hardware detection and benchmarking application.

It measures and reports hardware capabilities, including CPU performance, memory capacity, memory bandwidth, storage performance, and available accelerators.

Node Agent

The Node Agent is the software that runs on participating computers.

It handles registration, authentication, communication with the network, heartbeats, role management, and eventually workload execution.

Provisioning Server

The Provisioning Server coordinates network membership and infrastructure.

Its responsibilities include identity issuance, authentication, hardware evaluation, role allocation, node monitoring, and network coordination.

Compute Engine

The Compute Engine will eventually distribute computational tasks, manage execution, collect results, and coordinate workloads across volunteer computers.

Distributed Storage

The storage infrastructure will maintain replicated network records, computational data, and other information required for reliable operation.

Load Balancing Infrastructure

This component will distribute work across available computing resources while considering hardware capabilities, network conditions, and node availability.

Development Roadmap

DistributedCompute will be developed in major milestones rather than isolated, disconnected scripts.

Phase 1 — Network Foundation

Establish the core infrastructure:

Provisioning API.
Database architecture.
Permanent node identities.
Secure authentication.
Hardware-aware role allocation.
Node health monitoring.
Network administration.
Phase 2 — Distributed Network Infrastructure

Build the systems that allow computers to communicate and cooperate:

Node discovery.
Node-to-node communication.
Infrastructure replication.
Dynamic role management.
Failure detection and recovery.
Network coordination.
Phase 3 — Distributed Compute Engine

Build the actual computational platform:

Workload submission.
Task scheduling.
Task distribution.
CPU execution.
GPU execution through Apple Metal.
AI acceleration through Core ML and supported Neural Engine hardware.
Result collection and verification.
Phase 4 — Distributed Storage

Develop reliable distributed data infrastructure:

Replicated identity storage.
Distributed task storage.
Result storage.
Data integrity verification.
Recovery and replication.
Phase 5 — Intelligent Network Optimization

Make the network more efficient as it grows:

Performance-aware scheduling.
Dynamic resource allocation.
Load balancing.
Failure-aware task placement.
Automatic infrastructure scaling.
Improved resource utilization.
Phase 6 — Public Volunteer Release

Prepare DistributedCompute for real-world participation:

Automated installation.
Background Node Agent.
Volunteer dashboard.
Resource usage controls.
Security auditing.
Documentation.
Public deployment infrastructure.
Development Philosophy

DistributedCompute is intended to be open-source, modular, scalable, and designed for long-term growth.

Our development principles include:

1. Automation

Volunteer computers should be able to join and participate with minimal manual configuration.

2. Scalability

The architecture should support growth from a small development network to potentially hundreds of thousands of participating computers or more.

3. Hardware awareness

Different computers have different capabilities. Workloads and infrastructure roles should be assigned according to actual hardware performance and availability.

4. Reliability

The network should be designed to tolerate computers disconnecting, restarting, or becoming unavailable.

5. Security

Node identities, authentication, workload isolation, and data integrity are essential. Volunteer computers must remain under their owners' control.

6. Modularity

Each major component should be independently maintainable while integrating with the larger network.

7. Efficient resource utilization

The network should make useful contributions from hardware that would otherwise be idle, while respecting volunteer resource limits.

Current Development Status

DistributedCompute is currently in its early development stage.

The repository currently contains prototypes for:

Provisioning API with self-service node registration, signed identity recovery, heartbeats, and admin controls.
PostgreSQL-compatible schema migrations and Redis-backed ephemeral node state.
Mac Node Agent with Keychain identity storage and a development LaunchAgent installer.
CPU and memory Compute Credit quote and settlement calculations.
An admin-only distributed `sum_squares` diagnostic that requires distinct online compute nodes and verifies shard coverage/results.

The target product is a serverless-style compute grid: users submit bounded container workloads, opted-in nodes execute eligible tasks, and contributors earn internal credits for verified resources delivered. A user's credits can pay for capacity across many providers; they do not need to own as many cores as the workload requests. Work must expose parallel tasks or distributed-service behavior to use multiple independent computers; arbitrary containers cannot be split automatically. The initial CPU/RAM credit definition is documented in docs/economics.md.

The repository does not yet include NodeScout benchmarks, a user workload scheduler or isolated container runtime, an auditable credit ledger or economy-node quorum, or a signed and notarized Mac package. The built-in diagnostic is not a user workload runner. The prototypes are not production-ready and must not run arbitrary submitted containers.

The immediate priority is a persistent staging deployment with PostgreSQL and Redis, followed by a sandboxed first workload and verifiable credit accounting.

The Long-Term Vision

We envision a large, self-extending network of volunteer computers capable of working together as a shared computational resource.

A network where someone with a single Mac can contribute, where thousands of computers can cooperate, and where the infrastructure can grow alongside the community supporting it.

Rather than building another centralized supercomputer, DistributedCompute aims to build a distributed computing ecosystem powered by its participants.

One computer contributes. Thousands cooperate. The network grows together.

DistributedCompute — Open-source volunteer distributed computing.
