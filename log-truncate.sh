#!/bin/sh
MAX_LINES=50000
LOG="/var/log/ram/nft-drops.log"

# Healthcheck: if nft-drops.log is stale (no writes in 5 min), restart rsyslog
if [ -f "$LOG" ]; then
    STALE=$(find "$LOG" -mmin +5 2>/dev/null)
    if [ -n "$STALE" ]; then
        RECENT=$(journalctl -k --since "2 min ago" --no-pager -g "DROP_" 2>/dev/null | head -1)
        if [ -n "$RECENT" ]; then
            systemctl restart rsyslog 2>/dev/null
        fi
    fi
fi

LINES=$(wc -l < "$LOG" 2>/dev/null)
if [ "$LINES" -gt "$MAX_LINES" ] 2>/dev/null; then
    tail -n $((MAX_LINES / 2)) "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
    systemctl restart rsyslog
fi

# Truncate dnsmasq query log (max 10MB)
DNS_LOG="/var/log/ram/dnsmasq-queries.log"
MAX_DNS_SIZE=10485760
DNS_SIZE=$(stat -c%s "$DNS_LOG" 2>/dev/null || echo 0)
if [ "$DNS_SIZE" -gt "$MAX_DNS_SIZE" ] 2>/dev/null; then
    tail -c $((MAX_DNS_SIZE / 2)) "$DNS_LOG" > "$DNS_LOG.tmp" && mv "$DNS_LOG.tmp" "$DNS_LOG"
    chmod 666 "$DNS_LOG" 2>/dev/null || true
fi

# Remove oversized timeline cache (>50MB)
TL_CACHE="/var/log/ram/timeline_cache.json"
TL_SIZE=$(stat -c%s "$TL_CACHE" 2>/dev/null || echo 0)
if [ "$TL_SIZE" -gt 52428800 ] 2>/dev/null; then
    rm -f "$TL_CACHE"
fi