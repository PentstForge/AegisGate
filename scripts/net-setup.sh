#!/bin/bash
CFG="/opt/nft-dashboard/data/config.json"
[ -f "$CFG" ] || exit 0

WAN_IF=$(python3 -c "import json; print(json.load(open('$CFG')).get('wan_interface','eth0'))" 2>/dev/null || echo eth0)
LAN_IF=$(python3 -c "import json; print(json.load(open('$CFG')).get('lan_interface','eth1'))" 2>/dev/null || echo eth1)
WAN_IP=$(python3 -c "import json; print(json.load(open('$CFG')).get('wan_ip',''))" 2>/dev/null)
LAN_IP=$(python3 -c "import json; print(json.load(open('$CFG')).get('lan_ip',''))" 2>/dev/null)

for i in $(seq 1 30); do
    ifaces_up=0
    for iface in $WAN_IF $LAN_IF; do
        ip link show "$iface" >/dev/null 2>&1 && ifaces_up=$((ifaces_up + 1))
    done
    [ $ifaces_up -ge 1 ] && break
    sleep 1
done

for iface in $WAN_IF $LAN_IF; do
    ip link set "$iface" up 2>/dev/null || true
done

sleep 2

for iface in $WAN_IF $LAN_IF; do
    if ip link show "$iface" >/dev/null 2>&1; then
        if [ "$iface" = "$WAN_IF" ] && [ -n "$WAN_IP" ]; then
            ip addr replace "${WAN_IP}/24" dev "$iface" 2>/dev/null || true
        elif [ "$iface" = "$LAN_IF" ] && [ -n "$LAN_IP" ]; then
            ip addr replace "${LAN_IP}/24" dev "$iface" 2>/dev/null || true
        fi
    fi
done

for conn in eth0-static eth1-static $WAN_IF-static $LAN_IF-static; do
    nmcli connection up "$conn" 2>/dev/null || true
done

if [ -n "$WAN_IP" ]; then
    ip route replace default via "${WAN_IP%.*}.254" dev "$WAN_IF" 2>/dev/null || true
fi
