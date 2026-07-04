#!/usr/bin/env python3
"""Lightweight event ingest — reads only NEW lines from log files, writes to SQLite.
Runs every 1 minute via cron. Tracks file positions to avoid re-reading.
"""
import os
import re
import sys
import json
import fcntl
import subprocess
import gzip
import glob
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules.timeline_db import (
    get_db, get_position, set_position, insert_events,
    cleanup_old_events, upsert_bucket, prune_buckets
)

LOCK_FILE = "/var/log/ram/ingest_events.lock"
LOG_FILE = "/var/log/ram/nft-drops.log"
AUTH_LOG = "/var/log/auth.log"
EVE_JSON = "/var/log/suricata/eve.json"

BUCKET_CONFIGS = {"30s": 30, "2m": 120, "30m": 1800, "4h": 14400}


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


def ingest_drops():
    events = []
    try:
        fsize = os.path.getsize(LOG_FILE)
    except Exception:
        return 0
    last_pos, last_inode = get_position("nft_drops")
    if fsize < last_pos:
        last_pos = 0
    try:
        with open(LOG_FILE, "r") as f:
            st = os.fstat(f.fileno())
            inode = st.st_ino
            if inode != last_inode:
                last_pos = 0
            if last_pos > 0:
                f.seek(last_pos)
            for line in f:
                if re.search(r'(DROP\w+):', line):
                    ts_match = re.match(r'^(\S+)', line)
                    if ts_match:
                        ts_raw = ts_match.group(1)
                        try:
                            if 'T' in ts_raw:
                                ts = ts_raw[:19]
                                src_ip = ""
                                ip_m = re.search(r'SRC=(\d+\.\d+\.\d+\.\d+)', line)
                                if ip_m:
                                    src_ip = ip_m.group(1)
                                events.append({"ts": ts, "event_type": "drop", "source_ip": src_ip, "detail": ""})
                        except Exception:
                            pass
            new_pos = f.tell()
            set_position("nft_drops", new_pos, inode)
    except Exception:
        pass
    return insert_events(events)


def ingest_ssh_fails():
    events = []
    cutoff = datetime.now() - timedelta(hours=2)
    local_offset = datetime.now().astimezone().utcoffset() or timedelta(hours=3)
    try:
        result = subprocess.run(
            ["journalctl", "-u", "ssh", "--since", cutoff.strftime("%Y-%m-%d %H:%M:%S"),
             "-o", "short-iso", "--no-pager"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                m = re.search(r'Failed password for (?:invalid user )?(\S+) from (\d+\.\d+\.\d+\.\d+)', line)
                if not m:
                    continue
                username = m.group(1)
                src_ip = m.group(2)
                iso_m = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', line)
                if iso_m:
                    try:
                        dt = datetime.fromisoformat(iso_m.group(1))
                        if dt >= cutoff:
                            events.append({"ts": dt.strftime("%Y-%m-%dT%H:%M:%S"), "event_type": "ssh_fail", "source_ip": src_ip, "detail": username})
                    except Exception:
                        pass
    except Exception:
        pass
    log_files = sorted(glob.glob(AUTH_LOG + "*"), reverse=True)[:2]
    seen = set((e["ts"], e["source_ip"]) for e in events)
    for lf in log_files:
        try:
            if lf.endswith(".gz"):
                fh = gzip.open(lf, "rt", errors="replace")
            else:
                fh = open(lf, "r", errors="replace")
            with fh:
                for line in fh:
                    m = re.search(r'Failed password for (?:invalid user )?(\S+) from (\d+\.\d+\.\d+\.\d+)', line)
                    if not m:
                        continue
                    username, src_ip = m.group(1), m.group(2)
                    ts = None
                    iso_m = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', line)
                    if iso_m:
                        try:
                            dt = datetime.fromisoformat(iso_m.group(1))
                            if dt.tzinfo is not None:
                                utc_off = dt.utcoffset()
                                dt = dt.replace(tzinfo=None)
                                if utc_off and utc_off != local_offset:
                                    dt = dt + (local_offset - utc_off)
                            if dt >= cutoff:
                                ts = dt.strftime("%Y-%m-%dT%H:%M:%S")
                        except Exception:
                            pass
                    if ts and (ts, src_ip) not in seen:
                        events.append({"ts": ts, "event_type": "ssh_fail", "source_ip": src_ip, "detail": username})
                        seen.add((ts, src_ip))
        except Exception:
            continue
    return insert_events(events)


def ingest_suricata():
    events = []
    try:
        fsize = os.path.getsize(EVE_JSON)
    except Exception:
        return 0
    last_pos, last_inode = get_position("eve_json")
    if fsize < last_pos or last_pos == 0:
        if fsize > 50 * 1024 * 1024:
            last_pos = fsize - 50 * 1024 * 1024
        else:
            last_pos = 0
    try:
        with open(EVE_JSON, "r", errors="replace") as f:
            st = os.fstat(f.fileno())
            inode = st.st_ino
            if inode != last_inode:
                last_pos = max(0, fsize - 50 * 1024 * 1024)
            f.seek(last_pos)
            if last_pos > 0:
                f.readline()
            for line in f:
                if '"event_type":"alert"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                    ts = obj.get("timestamp", "")[:19]
                    src_ip = obj.get("src_ip", "")
                    alert = obj.get("alert", {})
                    detail = alert.get("signature", "") or alert.get("category", "")
                    if ts:
                        events.append({"ts": ts, "event_type": "suricata", "source_ip": src_ip, "detail": detail})
                except Exception:
                    pass
            new_pos = f.tell()
            set_position("eve_json", new_pos, inode)
    except Exception:
        pass
    return insert_events(events)


def ingest_cs_bans():
    events = []
    try:
        result = subprocess.run(
            ["cscli", "decisions", "list", "-o", "json"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            decisions = json.loads(result.stdout)
            cutoff_dt = datetime.now() - timedelta(hours=2)
            for d in decisions:
                if not d.get("decisions"):
                    continue
                ts_raw = d.get("created_at", "")
                if not ts_raw:
                    continue
                try:
                    ts_dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    if ts_dt.tzinfo is not None:
                        ts_dt = ts_dt.astimezone().replace(tzinfo=None)
                except Exception:
                    continue
                if ts_dt < cutoff_dt:
                    continue
                src_ip = d.get("source", {}).get("ip", "")
                scenario = d.get("scenario", "")
                for decision in d.get("decisions") or [{}]:
                    if decision.get("type") and decision.get("type") != "ban":
                        continue
                    events.append({
                        "ts": ts_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                        "event_type": "cs_ban",
                        "source_ip": decision.get("value") or src_ip,
                        "detail": decision.get("scenario") or scenario,
                    })
    except Exception:
        pass
    return insert_events(events)


def main():
    lock_fd = acquire_lock()
    n_drops = ingest_drops()
    n_ssh = ingest_ssh_fails()
    n_suricata = ingest_suricata()
    n_cs = ingest_cs_bans()
    total = n_drops + n_ssh + n_suricata + n_cs
    cleanup_old_events(days=30)
    if total > 0:
        print(f"Ingested: {n_drops} drops, {n_ssh} ssh, {n_suricata} suricata, {n_cs} cs_bans")


if __name__ == "__main__":
    main()
