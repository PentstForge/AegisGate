#!/usr/bin/env bash
set -euo pipefail

NFT="${NFT:-/usr/sbin/nft}"
CONF_FILE="${1:-/etc/nftables.conf}"
APP_DIR="${APP_DIR:-/opt/nft-dashboard}"
TMP_DIR="$(mktemp -d /tmp/aegisgate-nft-restore.XXXXXX)"

cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

if [[ ! -x "$NFT" ]]; then
    printf 'AegisGate nft restore: nft binary not found: %s\n' "$NFT" >&2
    exit 1
fi
if [[ ! -s "$CONF_FILE" ]]; then
    printf 'AegisGate nft restore: config missing or empty: %s\n' "$CONF_FILE" >&2
    exit 1
fi
if grep -Eq '^[[:space:]]*flush[[:space:]]+ruleset([[:space:]]|$)' "$CONF_FILE"; then
    printf 'AegisGate nft restore: refusing config containing flush ruleset\n' >&2
    exit 1
fi

"$NFT" -c -f "$CONF_FILE"

had_filter=0
had_nat=0
if "$NFT" list table inet filter > "$TMP_DIR/inet-filter.nft" 2>/dev/null; then
    had_filter=1
fi
if "$NFT" list table ip nat > "$TMP_DIR/ip-nat.nft" 2>/dev/null; then
    had_nat=1
fi

rollback() {
    local keep_new_without_backup="${1:-0}"
    if [[ "$keep_new_without_backup" -eq 1 && ! -s "$TMP_DIR/inet-filter.nft" && ! -s "$TMP_DIR/ip-nat.nft" ]]; then
        return 0
    fi
    "$NFT" delete table inet filter >/dev/null 2>&1 || true
    "$NFT" delete table ip nat >/dev/null 2>&1 || true
    if [[ "$had_filter" -eq 1 ]]; then
        "$NFT" -f "$TMP_DIR/inet-filter.nft" >/dev/null 2>&1 || true
    fi
    if [[ "$had_nat" -eq 1 ]]; then
        "$NFT" -f "$TMP_DIR/ip-nat.nft" >/dev/null 2>&1 || true
    fi
}

"$NFT" delete table inet filter >/dev/null 2>&1 || true
"$NFT" delete table ip nat >/dev/null 2>&1 || true
if ! "$NFT" -f "$CONF_FILE"; then
    rollback
    printf 'AegisGate nft restore: apply failed; previous AegisGate tables restored\n' >&2
    exit 1
fi

if ! "$NFT" list table inet filter >/dev/null 2>&1 || ! "$NFT" list table ip nat >/dev/null 2>&1; then
    rollback
    printf 'AegisGate nft restore: required tables missing after apply; previous tables restored\n' >&2
    exit 1
fi

sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true

if [[ -d "$APP_DIR" ]]; then
    if ! (
        cd "$APP_DIR"
        python3 -c "from modules.ip_blocklists import restore_ipbl; restore_ipbl()"
    ) >/dev/null 2>&1; then
        rollback 1
        printf 'AegisGate nft restore: IP blocklist restore failed; previous tables restored\n' >&2
        exit 1
    fi
    if ! python3 "$APP_DIR/scripts/dns_apply_service_blocks.py" >/dev/null 2>&1; then
        rollback 1
        printf 'AegisGate nft restore: DNS service policy restore failed; previous tables restored\n' >&2
        exit 1
    fi
fi

printf 'AegisGate nft restore applied: %s\n' "$CONF_FILE"
