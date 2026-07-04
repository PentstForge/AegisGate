#!/usr/bin/env python3
"""Cron script to update enabled IP blocklists.
Run daily via crontab: 0 4 * * * /opt/nft-dashboard/scripts/ip_blocklist_cron.py
"""
import sys
import os

sys.path.insert(0, "/opt/nft-dashboard")
os.chdir("/opt/nft-dashboard")

from modules.ip_blocklists import download_all_lists, init_db

if __name__ == "__main__":
    init_db()
    results = download_all_lists()
    for r in results:
        status = "OK" if r["ok"] else f"FAIL: {r['msg']}"
        print(f"  {r['name']}: {status}")
    if not results:
        print("No enabled lists to update")