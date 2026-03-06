#!/bin/bash

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Launching SFC 1..."
curl --header "Content-Type: application/json" \
     --request PUT \
     --data @"$script_dir/../configs/launch_sfc_1.json" \
     http://localhost:8080/launch_sfc
echo
echo

echo "Launching SFC 2..."
curl --header "Content-Type: application/json" \
     --request PUT \
     --data @"$script_dir/../configs/launch_sfc_2.json" \
     http://localhost:8080/launch_sfc
echo
echo
