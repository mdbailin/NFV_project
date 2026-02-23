#!/bin/bash
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
curl --header "Content-Type: application/json"   --request PUT   --data @"$script_dir/../configs/sfc_1.json"  http://localhost:8080/register_sfc
curl --header "Content-Type: application/json"   --request PUT   --data @"$script_dir/../configs/sfc_2.json"  http://localhost:8080/register_sfc
