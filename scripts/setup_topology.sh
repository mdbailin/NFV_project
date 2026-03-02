#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDFILE="/tmp/nfv_controller.pid"
LOGFILE="/tmp/nfv_controller.log"

BR1="ovs-br1"
BR2="ovs-br2"

cleanup() {
  set +e

  # Stop controller if we started it
  if [[ -f "$PIDFILE" ]]; then
    PID="$(cat "$PIDFILE")"
    kill "$PID" >/dev/null 2>&1 || true
    rm -f "$PIDFILE"
  fi

  # Delete OVS bridges
  ovs-vsctl --if-exists del-br "$BR1"
  ovs-vsctl --if-exists del-br "$BR2"

  # Delete namespaces
  for ns in src1 src2 dst1 dst2; do
    ip netns del "$ns" >/dev/null 2>&1 || true
  done

  # Delete stray veths if any remain
  for dev in v_src1 v_src2 v_dst1 v_dst2 v_br1br2; do
    ip link del "$dev" >/dev/null 2>&1 || true
  done

  set -e
}

# If script called with "clean", just teardown and exit
if [[ "${1:-}" == "clean" ]]; then
  cleanup
  exit 0
fi

# Needs root for netns/ovs
if [[ "$EUID" -ne 0 ]]; then
  echo "Run with sudo: sudo $0"
  exit 1
fi

cleanup

echo "[*] Creating OVS bridges $BR1 and $BR2"
ovs-vsctl add-br "$BR1"
ovs-vsctl add-br "$BR2"

# Use OpenFlow13 (matches your controller)
ovs-vsctl set bridge "$BR1" protocols=OpenFlow13
ovs-vsctl set bridge "$BR2" protocols=OpenFlow13

ip link set "$BR1" up
ip link set "$BR2" up

echo "[*] Creating namespaces and veth links"

# src1
ip netns add src1
ip link add v_src1 type veth peer name v_src1_br
ip link set v_src1 netns src1
ip netns exec src1 ip link set lo up
ip netns exec src1 ip addr add 192.168.1.2/24 dev v_src1
ip netns exec src1 ip link set v_src1 up
ip link set v_src1_br up
ovs-vsctl add-port "$BR1" v_src1_br -- set Interface v_src1_br ofport_request=2

# src2
ip netns add src2
ip link add v_src2 type veth peer name v_src2_br
ip link set v_src2 netns src2
ip netns exec src2 ip link set lo up
ip netns exec src2 ip addr add 192.168.1.3/24 dev v_src2
ip netns exec src2 ip link set v_src2 up
ip link set v_src2_br up
ovs-vsctl add-port "$BR1" v_src2_br -- set Interface v_src2_br ofport_request=3

# dst1
ip netns add dst1
ip link add v_dst1 type veth peer name v_dst1_br
ip link set v_dst1 netns dst1
ip netns exec dst1 ip link set lo up
ip netns exec dst1 ip addr add 145.12.131.92/24 dev v_dst1
ip netns exec dst1 ip link set v_dst1 up
ip link set v_dst1_br up
ovs-vsctl add-port "$BR2" v_dst1_br -- set Interface v_dst1_br ofport_request=2

# dst2
ip netns add dst2
ip link add v_dst2 type veth peer name v_dst2_br
ip link set v_dst2 netns dst2
ip netns exec dst2 ip link set lo up
ip netns exec dst2 ip addr add 145.12.131.93/24 dev v_dst2
ip netns exec dst2 ip link set v_dst2 up
ip link set v_dst2_br up
ovs-vsctl add-port "$BR2" v_dst2_br -- set Interface v_dst2_br ofport_request=3

# Link br1 <-> br2 on port 1 both sides
ip link add v_br1br2 type veth peer name v_br2br1
ip link set v_br1br2 up
ip link set v_br2br1 up
ovs-vsctl add-port "$BR1" v_br1br2 -- set Interface v_br1br2 ofport_request=1
ovs-vsctl add-port "$BR2" v_br2br1 -- set Interface v_br2br1 ofport_request=1

echo "[*] Starting OS-Ken controller (logs: $LOGFILE)"
# Adjust this path if your controller entrypoint differs
CONTROLLER_PY="$ROOT_DIR/src/controller/controller.py"

# Start in background; OS-Ken binary name may vary by install:
# Try osken-manager first, fallback to ryu-manager if needed.
if command -v osken-manager >/dev/null 2>&1; then
  nohup osken-manager "$CONTROLLER_PY" >"$LOGFILE" 2>&1 &
elif command -v ryu-manager >/dev/null 2>&1; then
  nohup ryu-manager "$CONTROLLER_PY" >"$LOGFILE" 2>&1 &
else
  echo "ERROR: neither osken-manager nor ryu-manager found in PATH"
  exit 1
fi

echo $! > "$PIDFILE"

echo "[*] Topology ready."
echo "    Bridges: $BR1, $BR2"
echo "    src1: 192.168.1.2/24  src2: 192.168.1.3/24"
echo "    dst1: 145.12.131.92/24 dst2: 145.12.131.93/24"
echo
echo "Check ports:"
echo "  ovs-ofctl -O OpenFlow13 show $BR1"
echo "  ovs-ofctl -O OpenFlow13 show $BR2"
