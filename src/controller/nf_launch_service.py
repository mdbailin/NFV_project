from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from cluster_state import ClusterState, Instance, NFPort, NFSpec


_SWITCHES: List[Tuple[int, str]] = [
    (1, "ovs-br1"),
    (2, "ovs-br2"),
]

@dataclass
class InstanceSpec:
    args: List[str]  # passed verbatim to the NF init script
    ip_by_iface: Dict[str, str] = field(default_factory=dict)  # iface -> IP (optional)


@dataclass
class LaunchResult:
    launched: List[Dict]   # to_dict() snapshot for each successfully registered instance
    failed: List[Dict]     # {nf_type, instance_index, reason} for each failure


class NFLaunchService:

    def __init__(self, cluster_state: ClusterState) -> None:
        self._state = cluster_state
        self._switch_rr = 0

    def launch_instances(self, chain_id: int, requested_NFs: Dict[str, List[InstanceSpec]] ) -> LaunchResult:
        launched = []
        failed = []

        for nf_type, specs in requested_NFs.items():
            chain = self._state.chains.get(chain_id)
            if chain is None:
                failed.append({
                    "nf_type": nf_type,
                    "reason": f"Chain {chain_id} not found in cluster state",
                })
                continue

            nf_spec = chain.nf_specs.get(nf_type)
            if nf_spec is None:
                failed.append({
                    "nf_type": nf_type,
                    "reason": f"No NFSpec registered for '{nf_type}' in chain {chain_id}",
                })
                continue

            for i, spec in enumerate(specs):
                switch_dpid, bridge = self._next_switch()
                inst = self._launch_one(chain_id, nf_type, spec, nf_spec, switch_dpid, bridge)
                if inst is None:
                    failed.append({
                        "nf_type": nf_type,
                        "instance_index": i,
                        "reason": "Container launch failed — check controller logs",
                    })
                    continue

                err = self._state.add_instance(chain_id, inst)
                # Instance launched but state rejected it — still clean up container
                if err:
                    _cleanup_container(inst.name)
                    failed.append({"nf_type": nf_type, "instance_index": i, "reason": err})
                else:
                    launched.append(inst.to_dict())

        return LaunchResult(launched=launched, failed=failed)

    def _next_switch(self) -> Tuple[int, str]:
        entry = _SWITCHES[self._switch_rr % len(_SWITCHES)]
        self._switch_rr += 1
        return entry

    def _launch_one(self, chain_id: int, nf_type: str, spec: InstanceSpec, nf_spec: NFSpec, 
                    switch_dpid: int, bridge: str ) -> Optional[Instance]:
        """
          1. docker run
          2. ovs-docker add-port for each interface
          3. read MAC addresses from the container
          4. read OVS port numbers from the bridge
          5. run the NF init script
          6. return a populated Instance, or None on any failure (with container cleaned up)
        """
        instance_id = uuid.uuid4().hex[:8]
        container_name = f"{nf_type}_{chain_id}_{instance_id}"

        # 1. Start container
        ok, out = _run(["docker", "run", "-d", "--net=none", "--name", container_name, nf_spec.image])
        if not ok:
            print(f"[nf_launch] docker run failed for {container_name}: {out}")
            return None

        # 2 & 3 & 4. Attach each interface, then collect MAC and OVS port
        ports: Dict[str, NFPort] = {}
        for iface in nf_spec.interfaces:
            ok, out = _run(["ovs-docker", "add-port", bridge, iface, container_name])
            if not ok:
                print(f"[nf_launch] ovs-docker add-port failed for {container_name}/{iface}: {out}")
                _cleanup_container(container_name)
                return None

            mac = _get_mac(container_name, iface)
            if mac is None:
                print(f"[nf_launch] could not read MAC for {container_name}/{iface}")
                _cleanup_container(container_name)
                return None

            ovs_port = _get_ovs_port(bridge, container_name, iface)
            if ovs_port is None:
                print(f"[nf_launch] could not read OVS port for {container_name}/{iface}")
                _cleanup_container(container_name)
                return None

            ip = spec.ip_by_iface.get(iface, "")
            ports[iface] = NFPort(name=iface, ip=ip, mac=mac, switch_port=ovs_port)

        # 5. Run the NF init script with caller-supplied arguments
        cmd = ["docker", "exec", container_name, nf_spec.init_script] + list(spec.args)
        ok, out = _run(cmd)
        if not ok:
            print(f"[nf_launch] init script failed for {container_name}: {out}")
            _cleanup_container(container_name)
            return None

        return Instance(
            instance_id=instance_id,
            name=container_name,
            nf_type=nf_type,
            switch_dpid=switch_dpid,
            ports=ports,
        )

def _run(cmd: List[str]) -> Tuple[bool, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)


def _get_mac(container: str, iface: str) -> Optional[str]:
    ok, out = _run(["docker", "exec", container, "cat", f"/sys/class/net/{iface}/address"])
    if not ok:
        return None
    mac = out.strip()
    return mac if mac else None


def _get_ovs_port(bridge: str, container: str, iface: str) -> Optional[int]:
    """
    ovs-docker get-port returns the veth interface name that was created on the bridge.
    We query that interface's OpenFlow port number from OVS directly.
    """
    ok, veth = _run(["ovs-docker", "get-port", bridge, iface, container])
    if not ok:
        return None
    veth = veth.strip()
    ok, out = _run(["ovs-vsctl", "get", "Interface", veth, "ofport"])
    if not ok:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def _cleanup_container(container: str) -> None:
    _run(["docker", "rm", "-f", container])
