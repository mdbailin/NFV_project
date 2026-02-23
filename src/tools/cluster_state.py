# src/controller/cluster_state.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any
import threading


FlowKey = Tuple[str, int, str, int, str]  # (src_ip, src_port, dst_ip, dst_port, proto)


@dataclass
class Instance:
    instance_id: str
    name: str
    ip: str
    mac: str
    switch: str
    port: int
    nf_type: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "name": self.name,
            "ip": self.ip,
            "mac": self.mac,
            "switch": self.switch,
            "port": self.port,
            "nf_type": self.nf_type,
        }


@dataclass
class Chain:
    chain_id: str
    nf_chain: List[str]
    src: Dict[str, Any]
    dst: Dict[str, Any]
    instances: Dict[str, List[Instance]] = field(default_factory=dict)
    rr_index: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for nf in self.nf_chain:
            self.instances.setdefault(nf, [])
            self.rr_index.setdefault(nf, 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nf_chain": list(self.nf_chain),
            "instances": {
                nf: [inst.to_dict() for inst in insts]
                for nf, insts in self.instances.items()
            },
            "src": dict(self.src),
            "dst": dict(self.dst),
            "rr_index": dict(self.rr_index),
        }


@dataclass
class ClusterState:
    chains: Dict[str, Chain] = field(default_factory=dict)
    flow_affinity: Dict[FlowKey, str] = field(default_factory=dict)  # flow -> nat_instance_id
    ip_to_mac: Dict[str, str] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ---- registration ----
    def register_chain(self, chain_id: str, nf_chain: List[str], src: Dict[str, Any], dst: Dict[str, Any]) -> None:
        with self._lock:
            self.chains[chain_id] = Chain(
                chain_id=chain_id,
                nf_chain=nf_chain,
                src=src,
                dst=dst,
            )

    def add_instance(self, chain_id: str, inst: Instance) -> None:
        with self._lock:
            chain = self.chains[chain_id]
            chain.instances.setdefault(inst.nf_type, []).append(inst)
            self.ip_to_mac[inst.ip] = inst.mac

    # ---- selection ----
    def select_instance_rr(self, chain_id: str, nf_type: str) -> Instance:
        with self._lock:
            chain = self.chains[chain_id]
            insts = chain.instances.get(nf_type, [])
            if not insts:
                raise RuntimeError(f"No instances available for nf_type={nf_type} in chain={chain_id}")
            i = chain.rr_index[nf_type] % len(insts)
            chain.rr_index[nf_type] += 1
            return insts[i]

    def get_or_pin_nat(self, flow: FlowKey, chain_id: str) -> Instance:
        """
        NAT affinity: once a flow is assigned a NAT instance, keep using it.
        """
        with self._lock:
            nat_id = self.flow_affinity.get(flow)
            chain = self.chains[chain_id]
            nat_list = chain.instances.get("nat", [])
            if not nat_list:
                raise RuntimeError(f"No NAT instances in chain={chain_id}")

            if nat_id is not None:
                for inst in nat_list:
                    if inst.instance_id == nat_id:
                        return inst

            inst = self.select_instance_rr(chain_id, "nat")
            self.flow_affinity[flow] = inst.instance_id
            return inst

    # ---- arp helper ----
    def mac_for_ip(self, ip: str) -> Optional[str]:
        with self._lock:
            return self.ip_to_mac.get(ip)

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "chains": {cid: c.to_dict() for cid, c in self.chains.items()},
                "flow_affinity": {str(k): v for k, v in self.flow_affinity.items()},
                "ip_to_mac": dict(self.ip_to_mac),
