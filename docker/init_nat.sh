#!/usr/bin/env bash
set -euo pipefail

ETH0_CIDR="${1:-192.168.1.1/24}"
ETH1_CIDR="${2:-145.12.131.74/24}"

sysctl -w net.ipv4.ip_forward=1 >/dev/null

ip addr flush dev eth0 || true
ip addr flush dev eth1 || true
ip addr add "$ETH0_CIDR" dev eth0
ip addr add "$ETH1_CIDR" dev eth1
ip link set eth0 up
ip link set eth1 up

iptables -F
iptables -t nat -F

iptables -t nat -A POSTROUTING -o eth1 -j MASQUERADE
iptables -A FORWARD -i eth1 -o eth0 -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A FORWARD -i eth0 -o eth1 -j ACCEPT
