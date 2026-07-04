#!/bin/bash
# AegisGate State Restore — master boot script
# Restores all saved state: nftables, WG, QoS, VLANs, policy, rules
set -e

echo "[restore-state] Waiting for network..."
sleep 5

# Wait for WAN interface to be UP
WAN_IF=$(python3 -c "
import json
try:
    with open('/opt/nft-dashboard/data/ifaces.json') as f:
        d=json.load(f)
    for n,c in d.get('interfaces',{}).items():
        if c.get('role')=='wan': print(n); break
    else: print('eth0')
except: print('eth0')
" 2>/dev/null || echo "eth0")

echo "[restore-state] WAN interface: $WAN_IF"

# Wait for WAN interface
for i in $(seq 1 60); do
    if ip addr show "$WAN_IF" &>/dev/null && ip link show "$WAN_IF" | grep -q "state UP" 2>/dev/null; then
        echo "[restore-state] $WAN_IF is UP"
        break
    fi
    echo "[restore-state] Waiting for $WAN_IF... ($i/60)"
    sleep 1
done

echo "[restore-state] Running Python state restore..."
cd /opt/nft-dashboard
python3 restore-state.py

echo "[restore-state] Done"