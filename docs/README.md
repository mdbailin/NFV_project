# Network Function Virtualization Orchestrator

This project implements a simplified **Network Function Virtualization (NFV) control plane** using an SDN controller. The system dynamically deploys network functions as Docker containers, installs OpenFlow forwarding rules, and maintains **connection affinity** while supporting **horizontal scaling of network functions**.

The orchestrator manages both **compute resources (NF instances)** and **network forwarding behavior**.

![Topology Diagram](diagram.svg)

---

# Architecture Overview

The NFV system consists of two logical components:

### NFVI Manager

Responsible for:

* launching network function containers
* managing cluster state
* handling REST API requests
* scaling NF instances

### SDN Controller

Responsible for:

* configuring OpenFlow rules on switches
* forwarding packets through NF chains
* implementing round-robin load balancing
* maintaining connection affinity

All orchestration logic runs inside a single **OS-Ken controller application**.

---

# Network Topology

The infrastructure consists of two Open vSwitch bridges and multiple Docker containers acting as endpoints and network functions.

The topology used for the project is shown below.

![Topology Diagram](diagram.svg)

Key components:

* **ovs-br1** – internal switch
* **ovs-br2** – external switch
* **src1 / src2** – internal hosts
* **dst1 / dst2** – external hosts
* **FW containers** – firewall network functions
* **NAT containers** – NAT network functions

Network functions are dynamically attached to switches using `ovs-docker`.

---

# Features

The NFV orchestrator supports:

* NF chain registration via REST API
* dynamic NF instance deployment
* traffic steering through NF chains
* round-robin load balancing across instances
* connection affinity preservation
* dynamic scale-up of NF instances
* OpenFlow rule installation on switches

---

# Repository Structure

```
src/
  controller/
    controller.py        # NFV controller implementation

configs/
  sfc_*.json             # chain registration configs
  launch_sfc_*.json      # initial NF deployment
  scale_sfc_*.json       # scale-up configs
  demo_profile_*.json    # traffic profiles

scripts/
  step0_initialize_infra.sh
  step1_register.sh
  step2_launch.sh
  step3_scaleup.sh

src/tools/
  traffic_generator.py

logs/
```

---

# Running the System

## 1. Initialize Infrastructure

Build Docker images and create the network topology.

```
sudo ./scripts/step0_initialize_infra.sh
```

This step:

* builds FW, NAT, and endpoint Docker images
* creates OVS bridges
* starts endpoint containers
* launches the OS-Ken controller

---

## 2. Register Service Chains

```
sudo ./scripts/step1_register.sh
```

Two chains are registered:

### Chain 1

```
src1 → FW → NAT → dst1
```

### Chain 2

```
src2 → NAT → dst2
```

---

## 3. Launch Initial NF Instances

```
sudo ./scripts/step2_launch.sh
```

Initial deployment:

Chain 1

* 1 FW instance
* 1 NAT instance

Chain 2

* 1 NAT instance

Verify cluster state:

```
curl http://localhost:8080/cluster_state | jq
```

---

# Traffic Testing

Traffic is generated using **iperf3** inside Docker containers.

Flows are defined in JSON traffic profiles.

Example profile:

```
configs/demo_profile_4flows.json
```

Each flow runs for **30 seconds**.

---

# Phase 1 Test (Before Scaling)

Kill any stale traffic generators:

```
sudo pkill -f iperf3 || true
```

Run traffic:

```
sudo python3 src/tools/traffic_generator.py configs/demo_profile_4flows.json --mode docker --log-dir logs
```

Expected behavior:

* 4 flows per chain
* 8 total flows
* all flows traverse the single available NF instance

Verify results:

```
curl http://localhost:8080/flow_affinity | jq
```

---

# Scaling the NF Chain

Scale up NF instances:

```
sudo ./scripts/step3_scaleup.sh
```

New instances created:

Chain 1

* additional FW
* additional NAT

Chain 2

* additional NAT

Verify cluster state:

```
curl http://localhost:8080/cluster_state | jq
```

---

# Phase 2 Test (After Scaling)

Run larger traffic profile:

```
sudo pkill -f iperf3 || true
sudo python3 src/tools/traffic_generator.py configs/demo_profile_8flows.json --mode docker --log-dir logs
```

Traffic profile:

* 8 flows per chain
* 16 total flows

Each flow lasts **30 seconds**.

---

# Verifying Correctness

## Cluster State

Check deployed instances:

```
curl http://localhost:8080/cluster_state | jq
```

---

## Connection Affinity

Check flow pinning:

```
curl http://localhost:8080/flow_affinity | jq
```

Expected behavior:

* existing flows remain pinned to original NF instances
* new flows are distributed across available instances

---

## OpenFlow Rules

Inspect switch flow tables:

```
sudo ovs-ofctl -O OpenFlow13 dump-flows ovs-br1
sudo ovs-ofctl -O OpenFlow13 dump-flows ovs-br2
```

Expected:

* default controller rules before traffic
* per-flow TCP forwarding rules after traffic generation

---

# Key Results

Testing confirmed:

* successful NF chain registration
* correct NF instance deployment
* traffic successfully traverses chains
* scaling adds additional NF instances
* connection affinity is preserved
* new flows are load balanced across instances
* OpenFlow rules are installed correctly

Example observation:

Before scaling:

```
flow → fw_1_59d45b1c → nat_1_d7235a24
```

After scaling:

Some flows continue using original instances, while new flows use the newly created instances.

---

# Conclusion

The NFV orchestrator correctly implements:

* dynamic NF deployment
* traffic steering through service chains
* round-robin load balancing
* connection affinity preservation
* dynamic scale-up of network functions

Testing confirms correct interaction between the **NFVI manager**, **SDN controller**, and **OpenFlow switches**.

---

If you'd like, I can also produce a **much stronger README version (the kind that gets full credit in networking courses)** that includes:

* architecture diagram explanation
* cluster_state data structure description
* NAT affinity mechanism explanation
* demo walkthrough section for graders.
