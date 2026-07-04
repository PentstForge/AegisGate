#!/usr/bin/env python3
"""Cron job: resolve all hostname rules and update nft sets + DNS policies."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.hostname_resolver import resolve_all, sync_dns_client_hostnames


def main():
    try:
        sync_dns_client_hostnames()
    except Exception as e:
        print(f"[hostname_cron] sync_dns_client_hostnames error: {e}", file=sys.stderr)

    try:
        results = resolve_all()
        changed = sum(1 for v in results if v.get("status") == "ok")
        print(f"[hostname_cron] Resolved {len(results)} hostnames, {changed} resolved OK")
    except Exception as e:
        print(f"[hostname_cron] resolve_all error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()