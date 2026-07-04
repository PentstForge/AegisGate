#!/usr/bin/env bash
set -euo pipefail
# Truncate large ram logs to prevent tmpfs overflow
for f in /var/log/ram/nft-drops.log /var/log/ram/dnsmasq-queries.log /var/log/ram/dns_apply_schedules.log /var/log/ram/dns_apply_service_blocks.log; do
    if [ -f "$f" ]; then
        size=$(stat -c%s "$f" 2>/dev/null || echo 0)
        if [ "$size" -gt 52428800 ]; then
            truncate -s 0 "$f"
            echo "Truncated $f (was ${size} bytes)"
        fi
    fi
done
# Reset ingest position if nft-drops.log was truncated
if [ ! -s /var/log/ram/nft-drops.log ]; then
    cd /opt/nft-dashboard 2>/dev/null && python3 -c "from modules.timeline_db import set_position; set_position('nft_drops', 0, 0)" 2>/dev/null || true
fi
# Restart rsyslog if it has write errors
if journalctl -u rsyslog --since "10 min ago" 2>/dev/null | grep -q "write error"; then
    systemctl restart rsyslog 2>/dev/null || true
    echo "Restarted rsyslog (had write errors)"
fi
