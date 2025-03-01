# Module Project: Network Function Virtualization

This repo contains sample JSON files for this project. `sfc_<num>`.json shows JSON samples used to register a chain. `launch_sfc_<num>`.json shows JSON samples used to launch NF instances for a chain, and `scale_sfc_<num>.json` shows JSON samples used to scale and add additional NF instances for a chain.

Add your project code to the controller.py file  
  
You can use the following workflow to test your implementation:

1. `./step0_initialize_infra.sh`
2. `./step1_register.sh`
3. `./step2_launch.sh`
4. `./step3_scaleup.sh`

Finally you need to build a traffic generator that generates traffic between Docker containers based on the a profile of the format present in `traffic_profile.json`.

## 1. Expected outcome
The student is going to learn how to build an orchestration layer for NFV. This involves creating a web service for deploying virtual network functions which communicates with an SDN control application for configuring traffic forwarding to the created NF instances. The student will use the created setup for dynamically scaling the NF chain deployment while ensuring connection-affinity in packet forwarding.

**NOTE:** OS Ken, which this class now uses, is a fork of the Ryu repository. Any method or object found within Ryu should still be present in OS Ken, and any Ryu documentation should still prove useful.

## 2. Background
### 2.1 Network Function (NF) Chains
Network functions are seldom deployed in isolation. Oftentimes multiple network functions are chained together, such that the output of one NF forms the input of another NF. One example is an enterprise that deploys a Firewall and a NAT NF. Traffic leaving the enterprise first traverses the Firewall and then the NAT before emerging out of the enterprise. The reverse order of NFs is traversed by traffic entering the enterprise.
### 2.2 NFV Orchestrator
A typical NFV control plane consists of an NFV Infrastructure (NFVI) Manager and SDN Controller (see figure below). The functions of NFVI Manager and SDN Controller are described below.

<p align="center">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://github.gatech.edu/cs8803-SIC/project3/assets/59780/7add62bd-6204-4a77-b813-23ff466dfe06">
      <source media="(prefers-color-scheme: light)" srcset="https://github.gatech.edu/cs8803-SIC/project3/assets/59780/e411fe1a-16d8-4c46-8a1f-cf1f3b60b2fb">
      <img  src="https://github.gatech.edu/cs8803-SIC/project3/assets/59780/e411fe1a-16d8-4c46-8a1f-cf1f3b60b2fb">
    </picture>
</p>

<p align="center">
	<em>
		High Level Overview of a Typical NFV Orchestrator
	</em>
</p>


#### 2.2.1 NFVI Manager 

The NFVI Manager is responsible for managing the computational resources (servers) in the NFV cluster. It deploys network function (NF) instances on the servers and monitors them for failures and resource utilization. System administrators communicate with the NFVI Manager to register NF Chains for a specific tenant's traffic - this communication can be done using a high level API like REST. Information about registered NF chains and deployed NF instances is stored in the "*cluster state*".

#### 2.2.2 SDN Controller 

The SDN Controller is responsible for managing the network resources and configure packet forwarding so that end-to-end NF chains can be realized. It queries the "*cluster state*" to determine where specific NF instances are deployed. It also performs discovery of network topology and updates that information in the cluster state. Communication between SDN Controller and switches is done using the SDN southbound API (OpenFlow).

## 3. Download Repo
The repo for the NFV project provides scripts to setup topology as specified in section 4.1, as well as various scripts and JSON files to test your NFV orchestrator. Please use Dockerfiles from previous workshops to build Docker images for hosts and switches. Please use `osken app.py` and `workshop parent.py` from the previous workshop as a starting point for this project.

```bash
$ git clone https://github.gatech.edu/cs8803-SIC/project3.git 
```
Test code, traffic profiles, and other JSON code serve as a guideline for students to start with, students are expected to modify the test profiles for the demo.

## 4. Sections of the project
### 4.1 Network topology
The network topology of the infrastructure is as shown in the figure below. For the sake of simplicity, this is fixed for the project. Use this assumption to simplify your implementation.


<p align="center">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://github.gatech.edu/cs8803-SIC/project3/assets/59780/fd96df48-82c4-4702-aa8c-9d8e96fec03d">
      <source media="(prefers-color-scheme: light)" srcset="https://github.gatech.edu/cs8803-SIC/project3/assets/59780/2d55e3f3-6885-4051-be2d-565b59cbe090">
      <img  src="https://github.gatech.edu/cs8803-SIC/project3/assets/59780/2d55e3f3-6885-4051-be2d-565b59cbe090">
    </picture>
</p>

<p align="center">
	<em>
		Schematic of Network Topology
	</em>
</p>

Network functions that are deployed dynamically will be connected to one of the two switches (`ovs-br1` and `ovs-br2`).

### 4.2 Running NFV Control Plane on a single-node
This project is designed to be deployed on a single machine, as the previous workshops. The entire NFV control plane, i.e. NFVI Manager and SDN Controller are implemented as a Python process (as an OS Ken application running with `osken-manager`). The "*cluster state*" is maintained as in-memory data structures. The NFVI Manager should use the `subprocess` module in Python to execute commands for creating Docker containers on the host machine.

### 4.3 NFVI Manager: Web service for launching network functions
You must to listen to deployment and scale-up REST requests coming from the system administrator. The requests are sent using the HTTP `PUT` method and the body of the request is represented as a JSON. HTTP requests containing a JSON body can be sent using the following bash command.


```bash
$ curl --header "Content-Type:application/json" --request PUT --data @sfc_1.json  http://localhost:8080/register_sfc
```

You need to support two types of requests: 
1. Registering a chain of network functions
2. Launching instances of network functions belonging to a service chain

For receiving REST requests, the starter code for this project includes a wrapper for the Ryu WSGI linkage feature ([Tutorial Here](https://osrg.github.io/ryu-book/en/html/rest_api.html)). Additionally, a simple web server can be setup using the Python Flask Framework ([Docs Here](https://flask.palletsprojects.com/en/stable/)).


#### 4.3.1 Registering a network function chain
The JSON document containing request body for registering a network function chain should look like the following and contain all the mentioned fields.

```js
{
    "nf_chain":["fw", "nat"], // custom identifiers for NFs (referenced below)
    "chain_id" : 1,
    "nat" : { // reference to the 2nd NF in the nf_chain field
        "image" : "nat", // name of Docker image
        "interfaces" : ["eth0", "eth1"], // interfaces to be created on container
        "init_script" : "/init_nat.sh" // script to be run on container startup
    },
    "fw": { // reference to the 1st NF in the nf_chain field
        "image" : "fw", // name of Docker image
        "interfaces" : ["eth0", "eth1"], // interfaces to be created on container
        "init_script" : "/init_fw.sh"
    },
    "SRC":{ // Specifies where the src host is connected in the nw topology
        "MAC" : "00:00:00:00:00:01",
        "IP" : "192.168.1.2",
        "SWITCH_DPID" : 1, // switch DPID that SRC connects to
        "PORT" : 1 // switch port that SRC connects to
    },
    "DST" : { // Specifies where the dest host is connected in the nw topology
        "MAC" : "00:00:00:00:00:02",
        "IP" : "145.12.131.92",
        "SWITCH_DPID" : 2, // switch DPID that SRC connects to
        "PORT" : 1 // switch port that DST connects to
    }
}
```
Each chain will be associated with a single SRC-DST host pair. A single SRC-DST host pair can only have a single chain associated with it.

**IMPORTANT**. For this project, since NF instances are created on the fly, all configuration would have to be done on the fly. You will therefore need to maintain two Docker images, one for each NF ( `fw` and `nat` in this case). Each image should contain an executable script that will be called upon container creation with the appropriate arguments for configuring the container. For instance, in the above NF chain registration request, the NF `nat` has an initialization script in the file `/init-nat.sh` which contains all the commands for configuring the container to start performing address translation, given that it is called with the right arguments. The arguments for this script will be described in the next section (Section [4.3.2](#432-launching-instances-of-an-nf-chain)).

#### 4.3.2 Launching instances of an NF chain

```js
{
    "chain_id":1,
    "nat" : [ // array with each element being a nat instance
            { // instance 1 of nat
                "args":[ // arguments for init script
                	"192.168.1.1/24", "145.12.131.74/24"
                ],
                "ip" : { // IPs for interfaces (optional)
                	"eth0":"192.168.1.1", 
                	"eth1":"145.12.131.74"
                }
            },
            { // instance 2 of nat
                "args":["192.168.1.11/24", "145.12.131.75/24"],
                "ip" : {"eth0":"192.168.1.11","eth1":"145.12.131.75"}
            }
        ],
    "fw" : [ // array with each element being a fw instance
	    {"args":["192.168.1.0/24", "145.12.131.0/24"]} // instance 1 of fw
    ]
}
```

Note that this API is not restricted to launching the initial instances of the network functions. The student should implement the API in such a way that is can be used to add NF instances to an existing/running NF chain as well.

**In this project, students are not required to implement the scaling down primitive, as that requires complicated monitoring of flow termination.**

### 4.4 Launching network functions
Upon receiving the request to launch NF instances for a given NF chain, you need to launch Docker containers. When launching Docker containers you need to take the following steps:

1. Select the switch on which to connect the container.

2. Run the container using the Docker CLI.

3. Add ports to the container and the chosen switch using `ovs-docker` CLI.

4. Run the initialization script in the container with arguments provided in the JSON body of launch request (Section [4.3.2](#432-launching-instances-of-an-nf-chain)).

5. Extract relevant information from the container (e.g. MAC addresses) and add inform the NF chaining application (running in the SDN controller) of the new NF instance.
### 4.5 Load balancing between network function instances
The load balancing policy to be used in this project is basic round-robin. Previously unseen flows need to be balanced between instances of a given network function in the chain using the round-robin policy.
#### 4.5.1 Connection-affinity for NAT NF
As you have seen in previous workshops that the NAT NF modifies packet headers. Therefore, maintaining connection affinity for a chain containing such an NF is more complex than by performing a lookup of previously installed flows.

One hack that you can use is to maintain another table in the SDN controller that keeps track of which instance of an NF did a particular flow emerges from. For example when NAT instance 2 changes the header of packets in a flow and sends them to the connecting switch, you would know that the modified flow was processed by NAT instance 2 and packets of the opposite direction flow need to be sent to NAT instance 2.

### 4.6 Responding to ARP requests
In the JSON specification for registering an NF chain, the admin provides the location in network topology where the endpoints are connected (check the sample JSON listing in Section [4.3.1](#431-registering-a-network-function-chain)). In addition to the endpoints, when creating each NF instance you are also supposed to record the location in network topology where the NF instance was deployed. The endpoints and NF instances are the two sources from where ARP requests can originate.

Now you know all the points where an ARP request can originate for a given NF chain. So when the SDN controller receives an ARP request, it would know the NF chain that the request corresponds to. You are also supposed to maintain a list of all the IP addresses allotted for that NF chain, including the IP addresses of endpoints as well as the NF instances. Search through these IP addresses to find out the one requested by the ARP request and respond with the corresponding MAC address.

## 5. Testing
For testing the implementation, you need to create a traffic profile with an increasing number of flows with time. For simplicity, you can divide testing period into multiple periods, and each period should have a certain number of active flows. You can create new flows in each period (flows don't have to live beyond the period). The time-based profile of the traffic is shown in the following.


```js
{
	"profiles": [
        {
            "src_container":"src1",
            "dst_container":"dst1",
            "dst_ip":"145.12.131.92",
            "flows" : [
                { "start_time":0, "end_time":10, "num_flows":5 },
                { "start_time":10, "end_time":30, "num_flows":15 }
            ]
        },
        {
            "src_container":"src2",
            "dst_container":"dst2",
            "dst_ip":"145.12.131.92",
            "flows" : [
                { "start_time":0, "end_time":10, "num_flows":5 },
                { "start_time":10, "end_time":30, "num_flows":15 }
            ]
        }
        ...
    ]
}
```
Create a traffic generator that takes a configuration file (like above) as input and generates network traffic between the specified containers.

During the period of the profile, generate scripts to increase the number of instances of particular NF in a particular NF chain. This can be done through a configuration file too.
## 6. Deliverables
### 6.1 Code
Include the code for all the previous sections. It should contain a `README` that explains the dependencies required to be installed for the code to work.

### 6.2 Test cases
Include the various traffic profiles that you tested the end-to-end system with.

### 6.3 Demo
For the demo, students are expected to have at least 2 chains deployed on the shared switch infrastructure. Chain 1 will use src1 to dst1 endpoints, chain2 will use src2 to dst2 endpoints.

The flow of the demo would be as follows.

1. Setup the topology with all the Internal and External hosts and switches.

2. Register 2 chains. The first chain should be a FW-NAT chain. You can choose what the component functions of the chains are for the second chain - it can be only FW or only NAT or identical to the 1st chain.

3. Launch both chains with 1 instance of each component NF in each chain.

4. Execute the test profile script with a given number of flows. You can use the following template.
For each chain, create 4 flows from the internal endpoint to external endpoint of that chain, and make these 4 flows last for 30 seconds.

5. After the 1st 30 seconds in the above test profile, add an additional instance of each NF in the first chain (FW-NAT). Wait for the containers are created.

6. Then use a new traffic profile wherein for each chain, create 8 flows from the internal endpoint to external endpoint of that chain, and make these 8 flows last for 30 seconds.
We should be able to see that the new flows are passing through the new instances created by the scale command in Step 6 and that they follow connection affinity
### 6.4 Written report
Submit a report that outlines the implementation of the NFV Control Plane. The report should cover the following points:

- Description of data structures used to maintain *cluster state*

- Technique used for maintaining connection affinity in spite of a packet modifying NAT NF
### 6.5 Important Details about Demo

When running the demo, the grading team will be using the `ovs-ofctl` commands to view the flows that are created to verify connection affinity and correctness. Following a single flow through the system and demonstrating where it is going to demonstrate correctness is paramount to success in this project. We expect teams to know what ports corresponds to which instances to simplify this process, and to make it simple for the TAs to view exactly where a particular flow goes.

Something that makes this easier is to use the `grep` command to grep for specific tcp ports that are used in the project. These ports typically follow a single flow.

