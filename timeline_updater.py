#!/usr/bin/env python3
"""Timeline updater — reads events from SQLite (filled by ingest_events.py),
rebuilds bucket cache for the dashboard. Fast, no file I/O.
Runs every 5 minutes via cron.
"""
import os
import sys
import time
import fcntl
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules.timeline_db import (
    get_db, get_all_buckets, prune_buckets, get_events_since
)

LOCK_FILE = "/var/log/ram/timeline_updater.lock"
TIMELINE_CACHE = "/var/log/ram/timeline_cache.json"
BUCKET_CONFIGS = {"30s": 30, "2m": 120, "30m": 1800, "4h": 14400}
BUCKET_TTL_DAYS = 7

import json


def acquire_lock():
    try:
        fd = open(LOCK_FILE, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (IOError, OSError):
        sys.exit(0)


def bucket_ts(ts_str, seconds):
    try:
        if "T" in ts_str:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        epoch = dt.timestamp()
        bucket_epoch = int(epoch // seconds) * seconds
        return datetime.fromtimestamp(bucket_epoch).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def rebuild_buckets_from_db():
    conn = get_db()
    try:
        since = (datetime.now() - timedelta(days=BUCKET_TTL_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
        events = conn.execute(
            "SELECT ts, event_type FROM events WHERE ts >= ? ORDER BY ts",
            (since,)
        ).fetchall()
        conn.execute("DELETE FROM bucket_cache")
        col_map = {"drop": "drops", "ssh_fail": "ssh_fail", "suricata": "suricata", "cs_ban": "cs_bans"}
        bucket_rows = {}
        for ev in events:
            ts_str = ev["ts"]
            etype = ev["event_type"]
            col = col_map.get(etype)
            if not col:
                continue
            for bkey, bseconds in BUCKET_CONFIGS.items():
                bts = bucket_ts(ts_str, bseconds)
                if not bts:
                    continue
                bk_key = (bkey, bts)
                if bk_key not in bucket_rows:
                    bucket_rows[bk_key] = {"bucket_key": bkey, "ts": bts, "drops": 0, "ssh_fail": 0, "suricata": 0, "cs_bans": 0}
                if col == "drops":
                    bucket_rows[bk_key]["drops"] += 1
                elif col == "ssh_fail":
                    bucket_rows[bk_key]["ssh_fail"] += 1
                elif col == "suricata":
                    bucket_rows[bk_key]["suricata"] += 1
                elif col == "cs_bans":
                    bucket_rows[bk_key]["cs_bans"] += 1
        if bucket_rows:
            rows = [(v["bucket_key"], v["ts"], v["drops"], v["ssh_fail"], v["suricata"], v["cs_bans"])
                    for v in bucket_rows.values()]
            conn.executemany(
                "INSERT INTO bucket_cache (bucket_key, ts, drops, ssh_fail, suricata, cs_bans) VALUES (?, ?, ?, ?, ?, ?)",
                rows
            )
        conn.commit()
        return len(events), len(bucket_rows)
    finally:
        conn.close()


def write_cache_file():
    import json
    import tempfile
    since_7d = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    buckets = get_all_buckets(since_7d)
    cache = {
        "buckets": buckets,
        "last_update": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    }
    try:
        os.makedirs(os.path.dirname(TIMELINE_CACHE), exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(TIMELINE_CACHE), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(cache, f)
            os.replace(tmp_path, TIMELINE_CACHE)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        pass


def main():
    lock_fd = acquire_lock()
    t0 = time.time()
    n_events, n_buckets = rebuild_buckets_from_db()
    prune_buckets(BUCKET_TTL_DAYS)
    write_cache_file()
    elapsed = time.time() - t0
    print(f"Timeline rebuilt: {n_events} events -> {n_buckets} buckets in {elapsed:.2f}s")


if __name__ == "__main__":
    main()
