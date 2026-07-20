#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${AEGIS_APP_DIR:-/opt/nft-dashboard}"
cd "$APP_DIR"
exec /usr/bin/python3 -c "from modules.wg_manager import _apply_all_firewall_rules; raise SystemExit(0 if _apply_all_firewall_rules() else 1)"
