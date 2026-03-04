from __future__ import annotations

import logging
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from cluster_state import ClusterState, Instance, NFPort, NFSpec

_MAC_RE = re.compile(r"link/ether\s+([0-9a-fA-F:]{17})")

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
    launched: List[Dict]  # to_dict() snapshot for each successfully registered instance
    failed: List[Dict]  # {nf_type, instance_index, reason} for each failure


class NFLaunchService:
    def __init__(
        self, cluster_state: ClusterState, logger: Optional[logging.Logger] = None
    ) -> None:
        self._state = cluster_state
        self._switch_rr = 0
        self._logger = logger if logger is not None else logging.getLogger(__name__)

    def launch_instances(
        self, chain_id: int, requested_NFs: Dict[str, List[InstanceSpec]]
    ) -> LaunchResult:
        launched: List[Dict] = []
        failed: List[Dict] = []

        for nf_type, specs in requested_NFs.items():
            chain = self._state.chains.get(chain_id)
            if chain is None:
                failed.append(
                    {
                        "nf_type": nf_type,
                        "reason": f"Chain {chain_id} not found in cluster state",
                    }
                )
                continue

            nf_spec = chain.nf_specs.get(nf_type)
            if nf_spec is None:
                failed.append(
                    {
                        "nf_type": nf_type,
                        "reason": f"No NFSpec registered for '{nf_type}' in chain {chain_id}",
                    }
                )
                continue

            for i, spec in enumerate(specs):
                switch_dpid, bridge = self._next_switch()
                inst = self._launch_one(
                    chain_id, nf_type, spec, nf_spec, switch_dpid, bridge
                )
                if inst is None:
                    failed.append(
                        {
                            "nf_type": nf_type,
                            "instance_index": i,
                            "reason": "Container launch failed — check controller logs",
                        }
                    )
                    continue

                err = self._state.add_instance(chain_id, inst)
                if err:
                    _cleanup_container(inst.name)
                    failed.append(
                        {"nf_type": nf_type, "instance_index": i, "reason": err}
                    )
                else:
                    launched.append(inst.to_dict())

        return LaunchResult(launched=launched, failed=failed)

    def _next_switch(self) -> Tuple[int, str]:
        entry = _SWITCHES[self._switch_rr % len(_SWITCHES)]
        self._switch_rr += 1
        return entry

    def _launch_one(
        self,
        chain_id: int,
        nf_type: str,
        spec: InstanceSpec,
        nf_spec: NFSpec,
        switch_dpid: int,
        bridge: str,
    ) -> Optional[Instance]:
        instance_id = uuid.uuid4().hex[:8]
        container_name = f"{nf_type}_{chain_id}_{instance_id}"

        # 1. Start container (keep it alive for wiring + init)
        # Option A (recommended): minimal caps + sysctl
        ok, out = _run([
            "docker", "run", "-d",
            "--net=none",
            "--name", container_name,

            "--cap-add=NET_ADMIN",
            "--cap-add=NET_RAW",
            "--sysctl", "net.ipv4.ip_forward=1",

            "--entrypoint", "/bin/sh",
            nf_spec.image,
            "-c", "while true; do sleep 3600; done",
        ])

        # Option B (if Option A still hits "read-only file system"/iptables perms):
        # ok, out = _run([
        #     "docker", "run", "-d",
        #     "--net=none",
        #     "--name", container_name,
        #     "--privileged",
        #     "--entrypoint", "/bin/sh",
        #     nf_spec.image,
        #     "-c", "while true; do sleep 3600; done",
        # ])

        if not ok:
            self._logger.error(
                "[nf_launch] docker run failed for %s: %s",
                container_name, out,
                extra={"sw_id": "nf_launch"},
            )
            return None

        ok_state, state = _run(["docker", "inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", container_name])
        if ok_state and state.strip().startswith("exited"):
            ok_logs, logs = _run(["docker", "logs", "--tail", "200", container_name])
            self._logger.error(
                "[nf_launch] %s exited immediately: %s\nlogs:\n%s",
                container_name, state.strip(), logs if ok_logs else "(failed to read logs)",
                extra={"sw_id": "nf_launch"},
            )
            _cleanup_container(container_name)
            return None

        ports: Dict[str, NFPort] = {}

        for iface in nf_spec.interfaces:
            ok_b, before_out = _run(["ovs-vsctl", "list-ports", bridge])
            before = set(p.strip().strip('"') for p in before_out.splitlines() if p.strip()) if ok_b else set()

            ok, out = _run(["ovs-docker", "add-port", bridge, iface, container_name])
            if not ok:
                self._logger.error(
                    "[nf_launch] ovs-docker add-port failed for %s/%s: %s",
                    container_name, iface, out,
                    extra={"sw_id": "nf_launch"},
                )
                _cleanup_container(container_name)
                return None

            ok_a, after_out = _run(["ovs-vsctl", "list-ports", bridge])
            if not ok_a:
                self._logger.error(
                    "[nf_launch] ovs-vsctl list-ports failed after add-port (%s/%s): %s",
                    container_name, iface, after_out,
                    extra={"sw_id": "nf_launch"},
                )
                _cleanup_container(container_name)
                return None

            after = set(p.strip().strip('"') for p in after_out.splitlines() if p.strip())
            new_ports = list(after - before)

            if len(new_ports) != 1:
                self._logger.error(
                    "[nf_launch] expected 1 new port after add-port for %s/%s, got: %s",
                    container_name, iface, new_ports,
                    extra={"sw_id": "nf_launch"},
                )
                _cleanup_container(container_name)
                return None

            created_port = new_ports[0]

            mac = _get_mac(container_name, iface)
            if mac is None:
                self._logger.error(
                    "[nf_launch] could not read MAC for %s/%s",
                    container_name, iface,
                    extra={"sw_id": "nf_launch"},
                )
                _cleanup_container(container_name)
                return None

            ovs_port: Optional[int] = None
            for _ in range(50):
                ok_p, out_p = _run(["ovs-vsctl", "get", "Interface", created_port, "ofport"])
                if ok_p:
                    try:
                        p = int(out_p.strip())
                        if p >= 0:
                            ovs_port = p
                            break
                    except ValueError:
                        pass
                time.sleep(0.2)

            if ovs_port is None:
                ok_dbg, dbg = _run([
                    "ovs-vsctl",
                    "--columns=name,ofport,admin_state,link_state,error",
                    "list",
                    "Interface",
                    created_port,
                ])
                self._logger.error(
                    "[nf_launch] could not read usable ofport for %s/%s (iface=%s). dbg=%s",
                    container_name, iface, created_port, dbg if ok_dbg else "(no dbg)",
                    extra={"sw_id": "nf_launch"},
                )
                _cleanup_container(container_name)
                return None

            ip = spec.ip_by_iface.get(iface, "")
            ports[iface] = NFPort(name=iface, ip=ip, mac=mac, switch_port=ovs_port)

        # 5. Run the NF init script as root
        cmd = ["docker", "exec", "-u", "0", container_name, nf_spec.init_script] + list(spec.args)
        ok, out = _run(cmd)
        if not ok:
            self._logger.error(
                "[nf_launch] init script failed for %s: %s",
                container_name, out,
                extra={"sw_id": "nf_launch"},
            )
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
    for _ in range(50):
        ok, out = _run(
            ["docker", "exec", container, "cat", f"/sys/class/net/{iface}/address"]
        )
        if ok:
            mac = out.strip()
            if mac:
                return mac

        ok, out = _run(["docker", "exec", container, "ip", "link", "show", iface])
        if ok:
            m = _MAC_RE.search(out)
            if m:
                return m.group(1).lower()

        time.sleep(0.2)

    return None


def _get_ovs_port(bridge: str, container: str, iface: str) -> Optional[int]:
    # NOTE: your local ovs-docker DOES NOT have "get-port".
    # Leaving this function here for completeness, but it will not work on your system as-is.
    for _ in range(50):
        ok, veth = _run(["ovs-docker", "get-port", bridge, iface, container])
        if ok:
            veth_name = veth.strip().splitlines()[-1].strip()
            if veth_name:
                ok2, out = _run(["ovs-vsctl", "get", "Interface", veth_name, "ofport"])
                if ok2:
                    s = out.strip().splitlines()[-1].strip()
                    try:
                        port = int(s)
                        if port >= 0:
                            return port
                    except ValueError:
                        pass
        time.sleep(0.2)
    return None


def _cleanup_container(container: str) -> None:
    _run(["docker", "rm", "-f", container])
