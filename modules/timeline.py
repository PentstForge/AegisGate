import json
import os
from datetime import datetime, timedelta

TIMELINE_CACHE = "/var/log/ram/timeline_cache.json"
LOG_FILE = "/var/log/ram/nft-drops.log"
AUTH_LOG = "/var/log/auth.log"
EVE_JSON = "/var/log/suricata/eve.json"

ZOOM_MAP = {
    "5m": {"key": "30s", "seconds": 30, "period": timedelta(minutes=5)},
    "1h": {"key": "2m", "seconds": 120, "period": timedelta(hours=1)},
    "24h": {"key": "30m", "seconds": 1800, "period": timedelta(hours=24)},
    "7d": {"key": "4h", "seconds": 14400, "period": timedelta(days=7)},
}


def bucket_time(ts_str, bucket_seconds):
    try:
        if "T" in ts_str:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        epoch = dt.timestamp()
        bucket_epoch = int(epoch // bucket_seconds) * bucket_seconds
        return datetime.fromtimestamp(bucket_epoch).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def build_timeline(zoom="24h"):
    cfg = ZOOM_MAP.get(zoom, ZOOM_MAP["24h"])
    cache = load_cache()
    if not cache:
        return {"buckets": [], "zoom": zoom}

    buckets_raw = cache.get("buckets", {}).get(cfg["key"], [])
    if not buckets_raw:
        for key in ["30s", "2m", "30m", "4h"]:
            buckets_raw = cache.get("buckets", {}).get(key, [])
            if buckets_raw:
                break

    cutoff = datetime.now() - cfg["period"]
    result = []
    for b in buckets_raw:
        try:
            ts = datetime.fromisoformat(b["ts"])
            if ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)
            if ts >= cutoff:
                result.append(b)
        except Exception:
            pass

    return {"buckets": result, "zoom": zoom}


def get_timeline_summary(zoom="24h"):
    tl = build_timeline(zoom)
    buckets = tl.get("buckets", [])
    if not buckets:
        return {"total_drops": 0, "total_ssh": 0, "total_suricata": 0, "total_cs_bans": 0,
                "max_drops": 0, "max_ssh": 0, "max_suricata": 0, "max_cs_bans": 0}
    total_drops = sum(b.get("drops", 0) for b in buckets)
    total_ssh = sum(b.get("ssh_fail", 0) for b in buckets)
    total_suricata = sum(b.get("suricata", 0) for b in buckets)
    total_cs_bans = sum(b.get("cs_bans", 0) for b in buckets)
    return {
        "total_drops": total_drops,
        "total_ssh": total_ssh,
        "total_suricata": total_suricata,
        "total_cs_bans": total_cs_bans,
        "max_drops": max(b.get("drops", 0) for b in buckets),
        "max_ssh": max(b.get("ssh_fail", 0) for b in buckets),
        "max_suricata": max(b.get("suricata", 0) for b in buckets),
        "max_cs_bans": max(b.get("cs_bans", 0) for b in buckets) if buckets else 0,
    }


def load_cache():
    try:
        with open(TIMELINE_CACHE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def save_cache(data):
    try:
        os.makedirs(os.path.dirname(TIMELINE_CACHE), exist_ok=True)
        with open(TIMELINE_CACHE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass