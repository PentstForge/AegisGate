#!/usr/bin/env python3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.dns_logs import parse_log_file, batch_insert_queries, cleanup_old_queries

def main():
    entries = parse_log_file()
    if entries:
        batch = entries[-5000:]
        inserted = batch_insert_queries(batch)
        print(f"DNS log import: {inserted} entries from {len(batch)} batch ({len(entries)} total parsed)")
    else:
        print("DNS log: no entries to import")

    deleted = cleanup_old_queries(days=30)
    if deleted:
        print(f"DNS log cleanup: removed {deleted} old entries")

if __name__ == "__main__":
    main()