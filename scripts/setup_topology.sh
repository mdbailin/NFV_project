#!/usr/bin/env bash
# setup_topology.sh — Bring up the NFV project infrastructure (Docker + OVS)
#
# Usage:
#     sudo ./scripts/setup_topology.sh                    — full teardown + fresh setup
#     sudo ./scripts/setup_topology.sh clean        — teardown only
#
# Topology created:
#
#     src1 (192.168.1.2)    ─ port 2 ─┐                                                 ┌─ port 2 ─ dst1 (145.12.131.92)
#     src2 (192.168.1.3)    ─ port 3 ─┤    ovs-br1 ──[patch:1]── ovs-br2    ├─ port 3 ─ dst2 (145.12.131.93)
#                                                                    │    (DPID 1)                             (DPID 2) │
#                                                                    └── dynamic NF ports ───────────────┘
#
# Dynamic NF containers (fw, nat) are NOT created here.
# They are created later by the controller via /launch_sfc.
#
# Port 1 on each bridge is reserved for the inter-switch patch link.
# Endpoints occupy ports 2+ — these port numbers MUST match sfc_*.json SRC/DST.PORT values.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTROLLER_PY="$ROOT_DIR/src/controller/controller.py"
PIDFILE="/tmp/nfv_controller.pid"
LOG_DIR="$ROOT_DIR/logs"

BR1="ovs-br1"
BR2="ovs-br2"
CONTROLLER_TARGET="${NFV_CONTROLLER_TARGET:-tcp:127.0.0.1:6633}"

# ============================================================
# Controller lifecycle helpers
# ============================================================

# Stop a single PID gracefully (SIGTERM → short wait → SIGKILL).
_stop_pid() {
    local pid="$1"
    if kill -0 "$pid" 2>/dev/null; then
        echo "        sending SIGTERM to PID $pid..."
        kill -TERM "$pid" 2>/dev/null || true
        local i
        for i in 1 2 3 4 5; do
            sleep 0.5
            kill -0 "$pid" 2>/dev/null || return 0    # gone — success
        done
        echo "        process still alive after TERM, sending SIGKILL to PID $pid"
        kill -KILL "$pid" 2>/dev/null || true
        sleep 0.5
    fi
}

# Kill any stale controller using a relaxed two-token match:
#     token 1: launcher binary name (osken-manager or ryu-manager)
#     token 2: our controller script filename (controller.py)
# NOTE: We can also add extend match to include "os_ken.cmd.manager".
stop_stale_controllers() {
    echo "[*] Checking for stale controller processes..."

    # Check for pid file
    if [[ -f "$PIDFILE" ]]; then
        local saved_pid
        saved_pid="$(cat "$PIDFILE" 2>/dev/null || true)"
        if [[ -n "$saved_pid" ]]; then
            echo "        found pidfile (PID $saved_pid)"
            _stop_pid "$saved_pid"
        fi
        rm -f "$PIDFILE"
    fi

    # If no pid file then chekc running processes
    local stale_pids
    stale_pids=$(pgrep -af "osken-manager|ryu-manager" 2>/dev/null \
                             | grep "controller\.py" \
                             | awk '{print $1}' || true)

    if [[ -n "$stale_pids" ]]; then
        echo "        found stale controller PID(s): $stale_pids"
        while IFS= read -r pid; do
            [[ -n "$pid" ]] && _stop_pid "$pid"
        done <<< "$stale_pids"
    else
        echo "        no stale controllers found"
    fi
}

teardown() {
    set +e
    echo "[*] Tearing down existing topology..."

    stop_stale_controllers

    docker rm -f src1 src2 dst1 dst2 dst >/dev/null 2>&1 || true

    ovs-vsctl --if-exists del-br "$BR1"
    ovs-vsctl --if-exists del-br "$BR2"

    echo "[*] Teardown complete."
    set -e
}

setup() {
    echo "[*] Preflight checks..."

    if [[ "$EUID" -ne 0 ]]; then
        echo "ERROR: must run as root (sudo $0)"
        exit 1
    fi

    for cmd in docker ovs-vsctl ovs-docker ip sysctl; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            echo "ERROR: required command not found: $cmd"
            exit 1
        fi
    done

    if ! docker image inspect endpoint:latest >/dev/null 2>&1; then
        echo "ERROR: Docker image 'endpoint:latest' not found."
        echo "             Run scripts/build_required_images.sh first."
        exit 1
    fi

    echo "        all preflight checks passed"

    # ── OVS bridges ───────────────────────────────────────────
    echo "[*] Creating OVS bridges $BR1 (DPID 1) and $BR2 (DPID 2)..."
    ovs-vsctl add-br "$BR1"
    ovs-vsctl add-br "$BR2"

    ovs-vsctl set bridge "$BR1" protocols=OpenFlow13
    ovs-vsctl set bridge "$BR2" protocols=OpenFlow13

    ovs-vsctl set-fail-mode "$BR1" secure
    ovs-vsctl set-fail-mode "$BR2" secure

    ovs-vsctl set-controller "$BR1" "$CONTROLLER_TARGET"
    ovs-vsctl set-controller "$BR2" "$CONTROLLER_TARGET"

    ip link set "$BR1" up
    ip link set "$BR2" up

    # ── Inter-switch patch link (reserved as port 1 on both sides) ───
    echo "[*] Creating inter-switch patch link (port 1 on each bridge)..."
    ovs-vsctl \
        add-port "$BR1" patch-br1-br2 -- \
        set interface patch-br1-br2 type=patch options:peer=patch-br2-br1 -- \
        set interface patch-br1-br2 ofport_request=1
    ovs-vsctl \
        add-port "$BR2" patch-br2-br1 -- \
        set interface patch-br2-br1 type=patch options:peer=patch-br1-br2 -- \
        set interface patch-br2-br1 ofport_request=1

    # ── Endpoint containers ───────────────────────────────────
    echo "[*] Starting endpoint containers..."
    docker run -d --privileged --net=none --name src1 endpoint:latest sleep infinity
    docker run -d --privileged --net=none --name src2 endpoint:latest sleep infinity
    docker run -d --privileged --net=none --name dst1 endpoint:latest sleep infinity
    docker run -d --privileged --net=none --name dst2 endpoint:latest sleep infinity

    # ── Wire containers into OVS with MACs and IPs ───
    # MAC values must stay in sync with sfc_*.json SRC.MAC / DST.MAC.
    # Port numbers are assigned by OVS in the order add-port is called;
    echo "[*] Attaching endpoints to OVS bridges..."

    echo "    Attaching src1 -> $BR1 (expected port 2)"
    ovs-docker add-port "$BR1" eth0 src1 --ipaddress=192.168.1.2/24 --macaddress=00:00:00:00:00:01

    echo "    Attaching src2 -> $BR1 (expected port 3)"
    ovs-docker add-port "$BR1" eth0 src2 --ipaddress=192.168.1.3/24 --macaddress=00:00:00:00:00:02

    echo "    Attaching dst1 -> $BR2 (expected port 2)"
    ovs-docker add-port "$BR2" eth0 dst1 --ipaddress=145.12.131.92/24 --macaddress=00:00:00:00:01:01

    echo "    Attaching dst2 -> $BR2 (expected port 3)"
    ovs-docker add-port "$BR2" eth0 dst2 --ipaddress=145.12.131.93/24 --macaddress=00:00:00:00:01:02

    # ── Endpoint routes ───────────────────────────────────────
    # Routes point directly out eth0 with no static NF gateway — the SDN
    # controller will handle all inter-subnet forwarding via OpenFlow rules.
    echo "[*] Setting endpoint routes..."
    docker exec src1 ip route replace 145.12.131.0/24 dev eth0
    docker exec src2 ip route replace 145.12.131.0/24 dev eth0
    docker exec dst1 ip route replace 192.168.1.0/24    dev eth0
    docker exec dst2 ip route replace 192.168.1.0/24    dev eth0

    # ── Start controller ──────────────────────────────────────
    mkdir -p "$LOG_DIR"
    LOGFILE="$LOG_DIR/controller_$(date +%Y%m%d_%H%M%S).log"
    echo "[*] Starting OS-Ken controller (log: $LOGFILE)..."

    # Prefer project-local venv so sudo doesn't need the user's PATH exported.
    local launcher=""
    if     [[ -x "$ROOT_DIR/venv/bin/osken-manager" ]];    then launcher="$ROOT_DIR/venv/bin/osken-manager"
    elif command -v osken-manager    >/dev/null 2>&1;            then launcher="osken-manager"
    elif command -v ryu-manager        >/dev/null 2>&1;            then launcher="ryu-manager"
    else
        echo "ERROR: no controller launcher found."
        echo "             Checked: $ROOT_DIR/venv/bin/osken-manager, osken-manager (PATH), ryu-manager (PATH)"
        # NOTE (future upgrade): could also try 'python -m os_ken.cmd.manager' here.
        exit 1
    fi

    nohup "$launcher" "$CONTROLLER_PY" >"$LOGFILE" 2>&1 &
    local ctrl_pid=$!
    echo "$ctrl_pid" > "$PIDFILE"

    # Give the controller a moment then verify it is alive.
    sleep 1
    if ! kill -0 "$ctrl_pid" 2>/dev/null; then
        echo "ERROR: controller exited immediately. Check $LOGFILE for details."
        tail -n 20 "$LOGFILE" || true
        exit 1
    fi

    echo "        controller running (PID $ctrl_pid, launcher: $launcher)"

    echo ""
    echo "=========================================="
    echo " Topology ready"
    echo "=========================================="
    echo " Bridges:"
    echo "     $BR1 (DPID 1) — controller: $CONTROLLER_TARGET"
    echo "     $BR2 (DPID 2) — controller: $CONTROLLER_TARGET"
    echo " Endpoints:"
    echo "     src1    192.168.1.2         MAC 00:00:00:00:00:01    $BR1 port 2"
    echo "     src2    192.168.1.3         MAC 00:00:00:00:00:02    $BR1 port 3"
    echo "     dst1    145.12.131.92     MAC 00:00:00:00:01:01    $BR2 port 2"
    echo "     dst2    145.12.131.93     MAC 00:00:00:00:01:02    $BR2 port 3"
    echo " Controller:"
    echo "     PID $ctrl_pid    |    log: $LOGFILE"
    echo ""
    echo " Quick checks:"
    echo "     ovs-ofctl -O OpenFlow13 show $BR1"
    echo "     ovs-ofctl -O OpenFlow13 show $BR2"
    echo "     tail -f $LOGFILE"
    echo "=========================================="
}

# ============================================================
# Entry point
# ============================================================

case "${1:-}" in
    clean)
        teardown
        ;;
    "")
        teardown
        setup
        ;;
    *)
        echo "Usage: $0 [clean]"
        exit 1
        ;;
esac
