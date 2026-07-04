import json
import subprocess
import time
from datetime import datetime, timedelta

_suricata_rules_cache = {"value": 0, "ts": 0}
_suricata_rules_ttl = 300
_ips_stats_cache = {"value": {}, "ts": 0}
_ips_stats_ttl = 15


_LOCAL_DROP_SIDS = set()

def _load_local_drop_sids():
    global _LOCAL_DROP_SIDS
    if _LOCAL_DROP_SIDS:
        return
    try:
        with open("/etc/suricata/rules/local-bridge.rules", "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("drop ") or line.startswith("drop\t"):
                    import re
                    m = re.search(r'sid:(\d+);', line)
                    if m:
                        _LOCAL_DROP_SIDS.add(int(m.group(1)))
    except Exception:
        pass

_load_local_drop_sids()


_ips_mode_cache = {"value": None, "ts": 0}
_ips_mode_ttl = 60


def get_suricata_ips_mode():
    global _ips_mode_cache
    now = time.time()
    if _ips_mode_cache["value"] is not None and now - _ips_mode_cache["ts"] < _ips_mode_ttl:
        return _ips_mode_cache["value"]
    try:
        r = subprocess.run(["ps", "-o", "args=", "-C", "suricata"],
                          capture_output=True, text=True, timeout=5)
        if "-q" in (r.stdout or ""):
            _ips_mode_cache = {"value": True, "ts": now}
            return True
        with open("/etc/suricata/suricata.yaml", "r") as f:
            content = f.read()
        result = "engine-mode: ips" in content or "copy-mode: ips" in content
        _ips_mode_cache = {"value": result, "ts": now}
        return result
    except Exception:
        _ips_mode_cache = {"value": False, "ts": now}
        return False


def get_suricata_alerts(n=20):
    alerts = []
    try:
        r = subprocess.run(
            ["sh", "-c", "grep '\"event_type\":\"alert\"' /var/log/suricata/eve.json | tail -1000"],
            capture_output=True, text=True, timeout=15
        )
        raw_alerts = []
        for line in r.stdout.strip().split("\n"):
            if '"event_type":"alert"' not in line:
                continue
            try:
                obj = json.loads(line)
                alert_obj = obj.get("alert", {})
                timestamp = obj.get("timestamp", "")
                raw_alerts.append({
                    "time": timestamp,
                    "time_iso": timestamp,
                    "src": obj.get("src_ip", ""),
                    "dst": obj.get("dest_ip", ""),
                    "proto": obj.get("proto", ""),
                    "dpt": str(obj.get("dest_port", "")),
                    "msg": alert_obj.get("signature", ""),
                    "sid": alert_obj.get("signature_id", ""),
                    "action": alert_obj.get("action", "allowed"),
                    "category": alert_obj.get("category", ""),
                    "severity": alert_obj.get("severity", 0),
                })
            except (json.JSONDecodeError, KeyError):
                pass
        raw_alerts.reverse()
        seen = set()
        for a in raw_alerts:
            key = (a["time"][:16], a["src"], a["sid"])
            if key in seen:
                continue
            seen.add(key)
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(a["time"].replace("Z", "+00:00"))
                a["time"] = dt.strftime("%m-%d %H:%M")
            except Exception:
                a["time"] = a["time"][:16] if len(a["time"]) > 16 else a["time"]
            if a["action"] == "dropped":
                a["action"] = "DROP"
            elif a["action"] == "allowed":
                sid = a.get("sid", "")
                try:
                    sid_int = int(sid)
                except (ValueError, TypeError):
                    sid_int = 0
                if sid_int in _LOCAL_DROP_SIDS and get_suricata_ips_mode():
                    a["action"] = "DROP"
                else:
                    a["action"] = "ALERT"
            else:
                a["action"] = a["action"].upper()
            alerts.append(a)
            if len(alerts) >= n:
                break
        drop_alerts = _get_drop_alerts_from_log()
        for da in drop_alerts:
            key = (da["time"][:16], da["src"], da["sid"])
            if key not in seen:
                seen.add(key)
                alerts.insert(0, da)
        alerts.sort(key=lambda x: x.get("time", ""), reverse=True)
        alerts = alerts[:n]
    except Exception:
        pass
    return alerts


def _get_drop_alerts_from_log(n=5):
    drops = []
    try:
        r = subprocess.run(
            ["sh", "-c", "grep '\"event_type\":\"drop\"' /var/log/suricata/eve.json | tail -100"],
            capture_output=True, text=True, timeout=10
        )
        for line in r.stdout.strip().split("\n"):
            if '"event_type":"drop"' not in line:
                continue
            try:
                obj = json.loads(line)
                drops.append({
                    "time": obj.get("timestamp", ""),
                    "time_iso": obj.get("timestamp", ""),
                    "src": obj.get("src_ip", ""),
                    "dst": obj.get("dest_ip", ""),
                    "proto": obj.get("proto", ""),
                    "dpt": str(obj.get("dest_port", "")),
                    "msg": "Packet dropped (IPS)",
                    "sid": "ips-drop",
                    "action": "DROP",
                    "category": "ips",
                    "severity": 1,
                })
            except (json.JSONDecodeError, KeyError):
                pass
        for d in drops:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(d["time"].replace("Z", "+00:00"))
                d["time"] = dt.strftime("%m-%d %H:%M")
            except Exception:
                d["time"] = d["time"][:16] if len(d["time"]) > 16 else d["time"]
        drops.reverse()
    except Exception:
        pass
    return drops[:n]


def _is_local_drop_rule(sid):
    return 9000000 <= sid < 9999999


def get_suricata_ips_stats():
    global _ips_stats_cache
    now = time.time()
    if now - _ips_stats_cache["ts"] < _ips_stats_ttl:
        return _ips_stats_cache["value"]
    stats = {"accepted": 0, "blocked": 0, "alerts": 0, "suppressed": 0, "drop_local": 0, "drop_stream": 0, "nfq_active": False}
    try:
        r = subprocess.run(
            ["suricatasc", "-c", "dump-counters"],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(r.stdout)
        msg = data.get("message", {})
        ips = msg.get("ips", {})
        detect = msg.get("detect", {})
        stats["accepted"] = ips.get("accepted", 0)
        stats["blocked"] = ips.get("blocked", 0)
        stats["alerts"] = detect.get("alert", 0)
        stats["suppressed"] = detect.get("alerts_suppressed", 0)
        dr = ips.get("drop_reason", {})
        stats["drop_stream"] = dr.get("stream_midstream", 0) + dr.get("stream_error", 0)
        stats["drop_local"] = dr.get("rules", 0)
        stats["nfq_active"] = _has_nfq_rules()
    except Exception:
        pass
    try:
        from modules.timeline_db import get_event_count
        now_dt = datetime.now()
        stats["historical_alerts_1h"] = get_event_count(
            since_ts=(now_dt - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"),
            event_type="suricata",
        )
        stats["historical_alerts_24h"] = get_event_count(
            since_ts=(now_dt - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S"),
            event_type="suricata",
        )
        stats["historical_alerts_7d"] = get_event_count(
            since_ts=(now_dt - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S"),
            event_type="suricata",
        )
    except Exception:
        stats.setdefault("historical_alerts_1h", 0)
        stats.setdefault("historical_alerts_24h", 0)
        stats.setdefault("historical_alerts_7d", 0)
    _ips_stats_cache = {"value": stats, "ts": now}
    return stats


def _has_nfq_rules():
    try:
        r = subprocess.run(["nft", "list", "ruleset"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and " queue" in (r.stdout or "")
    except Exception:
        return False


def get_suricata_rules():
    global _suricata_rules_cache
    now = time.time()
    if now - _suricata_rules_cache["ts"] < _suricata_rules_ttl:
        return _suricata_rules_cache["value"]
    suricata_rules = 0
    try:
        r = subprocess.run(
            ["tail", "-50000", "/var/log/suricata/eve.json"],
            capture_output=True, text=True, timeout=10
        )
        for line in r.stdout.strip().split("\n"):
            if '"event_type":"stats"' in line:
                try:
                    d = json.loads(line)
                    for eng in d.get("stats", {}).get("detect", {}).get("engines", []):
                        if eng.get("rules_loaded", 0) > 0:
                            suricata_rules = eng["rules_loaded"]
                except (json.JSONDecodeError, KeyError):
                    pass
    except Exception:
        pass
    _suricata_rules_cache = {"value": suricata_rules, "ts": now}
    return suricata_rules


def get_suricata_mode():
    try:
        r = subprocess.run(["ps", "-o", "args=", "-C", "suricata"],
                          capture_output=True, text=True, timeout=5)
        if "-q" in (r.stdout or ""):
            return "IPS"
        with open("/etc/suricata/suricata.yaml", "r") as f:
            content = f.read()
        if "engine-mode: ips" in content or "copy-mode: ips" in content:
            return "IPS"
        return "IDS"
    except Exception:
        return "IDS"
