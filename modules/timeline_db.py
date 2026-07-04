#!/usr/bin/env python3
import os
import sqlite3
import time
import json

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "timeline.db"
)

DB_CREATE = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source_ip TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_unique ON events(ts, event_type, source_ip, detail);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_ts_type ON events(ts, event_type);

CREATE TABLE IF NOT EXISTS file_positions (
    file_path TEXT PRIMARY KEY,
    position INTEGER NOT NULL DEFAULT 0,
    inode INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bucket_cache (
    bucket_key TEXT NOT NULL,
    ts TEXT NOT NULL,
    drops INTEGER DEFAULT 0,
    ssh_fail INTEGER DEFAULT 0,
    suricata INTEGER DEFAULT 0,
    cs_bans INTEGER DEFAULT 0,
    PRIMARY KEY (bucket_key, ts)
);
CREATE INDEX IF NOT EXISTS idx_bucket_cache_ts ON bucket_cache(bucket_key, ts);
"""


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(DB_CREATE)
    return conn


def get_position(file_path):
    conn = get_db()
    try:
        row = conn.execute("SELECT position, inode FROM file_positions WHERE file_path=?", (file_path,)).fetchone()
        if row:
            return row["position"], row["inode"]
        return 0, 0
    finally:
        conn.close()


def set_position(file_path, position, inode=0):
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO file_positions (file_path, position, inode, updated_at) VALUES (?, ?, ?, ?)",
            (file_path, position, inode, int(time.time()))
        )
        conn.commit()
    finally:
        conn.close()


def insert_events(events):
    if not events:
        return 0
    conn = get_db()
    try:
        now = int(time.time())
        rows = [(e["ts"], e["event_type"], e.get("source_ip", ""), e.get("detail", ""), now) for e in events]
        conn.executemany(
            "INSERT OR IGNORE INTO events (ts, event_type, source_ip, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            rows
        )
        inserted = conn.total_changes
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_events_since(since_ts, event_type=None):
    conn = get_db()
    try:
        if event_type:
            rows = conn.execute(
                "SELECT ts, event_type, source_ip, detail FROM events WHERE ts >= ? AND event_type=? ORDER BY ts",
                (since_ts, event_type)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ts, event_type, source_ip, detail FROM events WHERE ts >= ? ORDER BY ts",
                (since_ts,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_event_count(since_ts=None, event_type=None):
    conn = get_db()
    try:
        if since_ts and event_type:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM events WHERE ts >= ? AND event_type=?",
                (since_ts, event_type)
            ).fetchone()
        elif since_ts:
            row = conn.execute("SELECT COUNT(*) as c FROM events WHERE ts >= ?", (since_ts,)).fetchone()
        elif event_type:
            row = conn.execute("SELECT COUNT(*) as c FROM events WHERE event_type=?", (event_type,)).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) as c FROM events").fetchone()
        return row["c"] if row else 0
    finally:
        conn.close()


def cleanup_old_events(days=30):
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - days * 86400))
    conn = get_db()
    try:
        deleted = conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,)).rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def upsert_bucket(bucket_key, ts, drops=0, ssh_fail=0, suricata=0, cs_bans=0):
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT drops, ssh_fail, suricata, cs_bans FROM bucket_cache WHERE bucket_key=? AND ts=?",
            (bucket_key, ts)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE bucket_cache SET drops=drops+?, ssh_fail=ssh_fail+?, suricata=suricata+?, cs_bans=cs_bans+? WHERE bucket_key=? AND ts=?",
                (drops, ssh_fail, suricata, cs_bans, bucket_key, ts)
            )
        else:
            conn.execute(
                "INSERT INTO bucket_cache (bucket_key, ts, drops, ssh_fail, suricata, cs_bans) VALUES (?, ?, ?, ?, ?, ?)",
                (bucket_key, ts, drops, ssh_fail, suricata, cs_bans)
            )
        conn.commit()
    finally:
        conn.close()


def get_buckets(bucket_key, since_ts):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT ts, drops, ssh_fail, suricata, cs_bans FROM bucket_cache WHERE bucket_key=? AND ts >= ? ORDER BY ts",
            (bucket_key, since_ts)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_buckets(since_ts):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT bucket_key, ts, drops, ssh_fail, suricata, cs_bans FROM bucket_cache WHERE ts >= ? ORDER BY bucket_key, ts",
            (since_ts,)
        ).fetchall()
        result = {}
        for r in rows:
            bk = r["bucket_key"]
            if bk not in result:
                result[bk] = []
            result[bk].append(dict(r))
        return result
    finally:
        conn.close()


def prune_buckets(days=7):
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - days * 86400))
    conn = get_db()
    try:
        deleted = conn.execute("DELETE FROM bucket_cache WHERE ts < ?", (cutoff,)).rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def rebuild_buckets_from_events(bucket_configs):
    conn = get_db()
    try:
        since_7d = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 7 * 86400))
        events = conn.execute(
            "SELECT ts, event_type FROM events WHERE ts >= ? ORDER BY ts",
            (since_7d,)
        ).fetchall()
        conn.execute("DELETE FROM bucket_cache")
        for ev in events:
            ts_str = ev["ts"]
            etype = ev["event_type"]
            col_map = {"drop": "drops", "ssh_fail": "ssh_fail", "suricata": "suricata", "cs_ban": "cs_bans"}
            col = col_map.get(etype)
            if not col:
                continue
            for bkey, bseconds in bucket_configs.items():
                dt = None
                try:
                    if "T" in ts_str:
                        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    else:
                        from datetime import datetime as _dt
                        dt = _dt.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
                    if dt.tzinfo is not None:
                        dt = dt.replace(tzinfo=None)
                    epoch = dt.timestamp()
                    bucket_epoch = int(epoch // bseconds) * bseconds
                    from datetime import datetime as _dt
                    bts = _dt.fromtimestamp(bucket_epoch).strftime("%Y-%m-%dT%H:%M:%S")
                    existing = conn.execute(
                        "SELECT 1 FROM bucket_cache WHERE bucket_key=? AND ts=?",
                        (bkey, bts)
                    ).fetchone()
                    if existing:
                        conn.execute(
                            f"UPDATE bucket_cache SET {col}={col}+1 WHERE bucket_key=? AND ts=?",
                            (bkey, bts)
                        )
                    else:
                        conn.execute(
                            "INSERT INTO bucket_cache (bucket_key, ts, drops, ssh_fail, suricata, cs_bans) VALUES (?, ?, 0, 0, 0, 0)",
                            (bkey, bts)
                        )
                        conn.execute(
                            f"UPDATE bucket_cache SET {col}=1 WHERE bucket_key=? AND ts=?",
                            (bkey, bts)
                        )
                except Exception:
                    pass
        conn.commit()
        return len(events)
    finally:
        conn.close()
