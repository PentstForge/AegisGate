#!/usr/bin/env bash
# AegisGate State Restore — master boot script
# Restores all saved state: nftables, WG, QoS, VLANs, policy, rules
set -euo pipefail

APP_DIR="${AEGIS_APP_DIR:-/opt/nft-dashboard}"
cd "$APP_DIR"
exec python3 restore-state.py
