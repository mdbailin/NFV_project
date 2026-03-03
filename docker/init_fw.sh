#!/usr/bin/env bash
set -euo pipefail

INTERNAL_SUBNET="${1:-192.168.1.0/24}"
EXTERNAL_SUBNET="${2:-145.12.131.0/24}"

sysctl -w net.ipv4.ip_forward=1 >/dev/null

iptables -F
iptables -t nat -F

iptables -A FORWARD -i eth1 -o eth0 -p tcp --dport 22 -j DROP

iptables -A FORWARD -i eth0 -o eth1 -s "$INTERNAL_SUBNET" -d "$EXTERNAL_SUBNET" -j ACCEPT
iptables -A FORWARD -i eth1 -o eth0 -s "$EXTERNAL_SUBNET" -d "$INTERNAL_SUBNET" -j ACCEPT
