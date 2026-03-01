# src/controller/cluster_state.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any
import threading


FlowKey = Tuple[str, int, str, int, str]  # (src_ip, src_port, dst_ip, dst_port, proto)


@dataclass(frozen=True)
class Endpoint:
    mac: str
    ip: str
    switch_dpid: int
    port: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "MAC": self.mac,
            "IP": self.ip,
            "SWITCH_DPID": self.switch_dpid,
            "PORT": self.port,
        }

EndpointPairKey = Tuple[Endpoint, Endpoint]  # (src, dst) — uniqueness key for a chain


@dataclass
class NFSpec:
    image: str
    init_script: str
    interfaces: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "image": self.image,
            "init_script": self.init_script,
            "interfaces": list(self.interfaces),
        }


@dataclass
class NFPort:
    name: str # interface name, e.g. "eth0" — used for Docker/OVS CLI commands
    ip: str
    mac: str
    switch_port: int  # OVS port number on the switch

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ip": self.ip,
            "mac": self.mac,
            "switch_port": self.switch_port,
        }


@dataclass
class Instance:
    instance_id: str
    name: str
    nf_type: str
    switch_dpid: int
    ports: Dict[str, NFPort] # interface name -> NFPort

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "name": self.name,
            "nf_type": self.nf_type,
            "switch_dpid": self.switch_dpid,
            "ports": {name: p.to_dict() for name, p in self.ports.items()},
        }


@dataclass
class Chain:
    chain_id: str
    nf_chain: List[str]
    src: Endpoint
    dst: Endpoint
    nf_specs: Dict[str, NFSpec]
    instances: Dict[str, List[Instance]] = field(default_factory=dict)
    rr_index: Dict[str, int] = field(default_factory=dict)
    # Per-chain flow affinity: FlowKey -> {nf_type -> instance_id}
    flow_affinity: Dict[FlowKey, Dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for nf in self.nf_chain:
            self.instances.setdefault(nf, [])
            self.rr_index.setdefault(nf, 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "nf_chain": list(self.nf_chain),
            "nf_specs": {nf: spec.to_dict() for nf, spec in self.nf_specs.items()},
            "instances": {
                nf: [inst.to_dict() for inst in insts]
                for nf, insts in self.instances.items()
            },
            "src": self.src.to_dict(),
            "dst": self.dst.to_dict(),
            "rr_index": dict(self.rr_index),
            "flow_affinity": {str(k): dict(v) for k, v in self.flow_affinity.items()},
        }


@dataclass
class ClusterState:
    chains: Dict[str, Chain] = field(default_factory=dict)
    endpoints_to_chain: Dict[EndpointPairKey, str] = field(default_factory=dict)
    ip_to_mac: Dict[str, str] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ---- registration ----
    def register_chain(self, chain_id: str, nf_chain: List[str], src: Endpoint, dst: Endpoint, nf_specs: Dict[str, NFSpec]) -> Optional[str]:
        """Register a chain. Returns None on success, or an error string on failure."""
        with self._lock:
            if chain_id in self.chains:
                return f"Chain '{chain_id}' is already registered."
            pair_key: EndpointPairKey = (src, dst)
            if pair_key in self.endpoints_to_chain:
                existing = self.endpoints_to_chain[pair_key]
                return f"Endpoint pair (src={src.ip}, dst={dst.ip}) already has a chain: '{existing}'."
            missing = [nf for nf in nf_chain if nf not in nf_specs]
            if missing:
                return f"Missing NFSpec for NF(s): {missing}."
            self.chains[chain_id] = Chain(
                chain_id=chain_id,
                nf_chain=nf_chain,
                src=src,
                dst=dst,
                nf_specs=nf_specs,
            )
            self.endpoints_to_chain[pair_key] = chain_id

            self.ip_to_mac[src.ip] = src.mac
            self.ip_to_mac[dst.ip] = dst.mac
            return None

    def add_instance(self, chain_id: str, inst: Instance) -> Optional[str]:
        with self._lock:
            chain = self.chains.get(chain_id)
            if chain is None:
                return f"Chain '{chain_id}' not found."
            chain.instances.setdefault(inst.nf_type, []).append(inst)
            # Register all port IPs for ARP lookup
            for port in inst.ports.values():
                self.ip_to_mac[port.ip] = port.mac
            return None

    # ---- selection ----
    def select_instance_rr(self, chain_id: str, nf_type: str) -> Optional[Instance]:
        with self._lock:
            chain = self.chains.get(chain_id)
            if chain is None:
                return None
            insts = chain.instances.get(nf_type, [])
            if not insts:
                return None
            i = chain.rr_index[nf_type] % len(insts)
            chain.rr_index[nf_type] += 1
            return insts[i]

    # def get_or_pin(self, flow: FlowKey, chain_id: str, nf_type: str) -> Optional        
    #     """
    #     Connection affinity: once a flow is assigned an instance of nf_type, keep using it.
    #     If the pinned instance no longer exists, re-pins via RR.

    #     Returns None if chain/nftype does not exist
    #     """
    #     with self._lock:
    #         chain = self.chains.get(chain_id)
    #         if chain is None:
    #             return None
    #         inst_list = chain.instances.get(nf_type, [])
    #         if not inst_list:
    #             return None

    #         pinned_id = chain.flow_affinity.get(flow, {}).get(nf_type)
    #         if pinned_id is not None:
    #             for inst in inst_list:
    #                 if inst.instance_id == pinned_id:
    #                     return inst
    #             # Pinned instance no longer exists — fall through to re-pin.

    #         inst = self.select_instance_rr(chain_id, nf_type)
    #         if inst is not None:
    #             chain.flow_affinity.setdefault(flow, {})[nf_type] = inst.instance_id
    #         return inst

    def get_or_pin_path(self, flow: FlowKey, chain_id: str) -> Optional[List[Instance]]:
        """
        Return the full ordered list of instances for this flow (one per NF type, in nf_chain order),
        pinning each atomically. If any NF type has no instances, returns None.
        Re-pins any instance that no longer exists.

        Returns None if chain does not exist or there is a NF type with no instances
        """
        with self._lock:
            chain = self.chains.get(chain_id)
            if chain is None:
                return None

            path: List[Instance] = []
            for nf_type in chain.nf_chain:
                inst_list = chain.instances.get(nf_type, [])
                if not inst_list:
                    return None

                pinned_id = chain.flow_affinity.get(flow, {}).get(nf_type)
                pinned_inst = None
                if pinned_id is not None:
                    pinned_inst = next((i for i in inst_list if i.instance_id == pinned_id), None)

                if pinned_inst is None:
                    # RR-select and pin (inline to stay inside the same lock hold)
                    idx = chain.rr_index[nf_type] % len(inst_list)
                    chain.rr_index[nf_type] += 1
                    pinned_inst = inst_list[idx]
                    chain.flow_affinity.setdefault(flow, {})[nf_type] = pinned_inst.instance_id

                path.append(pinned_inst)

            return path

    def get_chain_by_endpoints(self, src: Endpoint, dst: Endpoint) -> Optional[Chain]:
        """Return the chain for a given SRC->DST endpoint pair, or None."""
        with self._lock:
            chain_id = self.endpoints_to_chain.get((src, dst))
            return self.chains.get(chain_id) if chain_id is not None else None

        def validate(self) -> List[str]:
        """
        Sanity-check internal consistency. Returns a list of error strings;
        an empty list means the state looks coherent.
        """
        errors: List[str] = []
        with self._lock:
            # Every chain_id referenced in endpoints_to_chain must exist in chains
            for pair_key, chain_id in self.endpoints_to_chain.items():
                if chain_id not in self.chains:
                    errors.append(f"endpoints_to_chain references unknown chain_id '{chain_id}' for pair {pair_key}")

            for chain_id, chain in self.chains.items():
                # endpoints_to_chain must have the reverse mapping
                pair_key = (chain.src, chain.dst)
                if self.endpoints_to_chain.get(pair_key) != chain_id:
                    errors.append(f"Chain '{chain_id}' has no matching entry in endpoints_to_chain")

                # Endpoint IPs must be in ip_to_mac
                for label, ep in (("src", chain.src), ("dst", chain.dst)):
                    if self.ip_to_mac.get(ep.ip) != ep.mac:
                        errors.append(f"Chain '{chain_id}' {label} IP {ep.ip} missing or wrong in ip_to_mac")

                # All nf_chain types must have an nf_specs entry
                for nf_type in chain.nf_chain:
                    if nf_type not in chain.nf_specs:
                        errors.append(f"Chain '{chain_id}' nf_type '{nf_type}' has no NFSpec")

                # All instances must belong to a type in nf_chain
                for nf_type, insts in chain.instances.items():
                    if nf_type not in chain.nf_chain:
                        errors.append(f"Chain '{chain_id}' has instances for unknown nf_type '{nf_type}'")
                    # Instance port IPs must be in ip_to_mac
                    for inst in insts:
                        for port in inst.ports.values():
                            if self.ip_to_mac.get(port.ip) != port.mac:
                                errors.append(
                                    f"Chain '{chain_id}' instance '{inst.instance_id}' "
                                    f"port '{port.name}' IP {port.ip} missing or wrong in ip_to_mac"
                                )
        return errors

    # ---- arp helper ----
    def mac_for_ip(self, ip: str) -> Optional[str]:
        with self._lock:
            return self.ip_to_mac.get(ip)

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "chains": {cid: c.to_dict() for cid, c in self.chains.items()},
                "endpoints_to_chain": {str(k): v for k, v in self.endpoints_to_chain.items()},
                "ip_to_mac": dict(self.ip_to_mac),
            }
