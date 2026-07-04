#!/bin/bash
cd /opt/nft-dashboard
/usr/bin/python3 -c "from modules.wg_manager import _apply_all_firewall_rules; _apply_all_firewall_rules()" 2>/dev/null
