import json
import os
import time
from collections import Counter
from datetime import datetime

RISK_CACHE = "/var/log/ram/risk_cache.json"
RISK_TTL = 60
RISK_MAX_STORE = 200


def calculate_risk(ip, drops_data=None, ssh_data=None, cs_ips=None, suricata_ips=None, bl_history=None, bl_ips=None):
    tags = []
    hits = 0
    unique_ports = set()
    base_score = 0
    port_score = 0
    brute_score = 0
    cs_score = 0
    suric_score = 0
    repeat_score = 0

    if drops_data:
        hits = drops_data.get("hits", 0)
        unique_ports = set(drops_data.get("ports", []))
        base_score = min(hits / 50 * 20, 20)
        if len(unique_ports) >= 8:
            tags.append("scanner")
            port_score = min(len(unique_ports) / 8 * 15, 15)
        elif len(unique_ports) >= 3:
            port_score = min(len(unique_ports) / 8 * 15, 15)

    if ssh_data:
        ssh_fail_count = ssh_data
        if ssh_fail_count >= 5:
            tags.append("bruteforce")
        brute_score = min(ssh_fail_count / 10 * 20, 20)

    if cs_ips and ip in (cs_ips or set()):
        cs_score = 15
        tags.append("crowdsec")

    if suricata_ips and ip in (suricata_ips or set()):
        suric_score = 15
        tags.append("suricata")

    if bl_history:
        bl_reappearances = bl_history
        if bl_reappearances >= 3:
            tags.append("repeat")
        repeat_score = min(bl_reappearances / 3 * 15, 15)

    if ip in (bl_ips or set()):
        base_score = max(base_score, 30)
        if "blacklisted" not in tags:
            tags.append("blacklisted")

    total = min(base_score + port_score + brute_score + cs_score + suric_score + repeat_score, 100)

    if total <= 25:
        level = "low"
    elif total <= 50:
        level = "medium"
    elif total <= 75:
        level = "high"
    else:
        level = "critical"

    return {
        "ip": ip,
        "score": round(total),
        "level": level,
        "tags": tags,
        "hits": hits,
        "unique_ports": len(unique_ports),
        "ssh_fail_count": ssh_data or 0,
        "in_crowdsec": ip in (cs_ips or set()),
        "in_suricata": ip in (suricata_ips or set()),
        "details": {
            "base_score": round(base_score, 1),
            "port_score": round(port_score, 1),
            "brute_score": round(brute_score, 1),
            "cs_score": cs_score,
            "suric_score": suric_score,
            "repeat_score": round(repeat_score, 1),
        }
    }


def get_all_risks(force=False, limit=200):
    now = time.time()
    if not force:
        cached = _load_cache()
        if cached and now - cached.get("ts", 0) < RISK_TTL:
            risks = cached.get("risks", [])
            if limit and limit > 0:
                return risks[:limit]
            return risks

    from modules.parsers import parse_log, parse_auth_log, LOG_FILE
    from modules.nft_utils import nft_set_ips, cscli_json
    from modules.suricata import get_suricata_alerts

    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    entries = parse_log(lines)

    ip_hits = Counter()
    ip_ports = {}
    for e in entries:
        src = e.get("src", "")
        if src:
            ip_hits[src] += 1
            dpt = e.get("dpt", "")
            if dpt:
                ip_ports.setdefault(src, set()).add(dpt)

    ssh_fail, _ = parse_auth_log()

    bl_list = nft_set_ips("filter", "blacklist_ipv4")
    cs_list = nft_set_ips("filter", "crowdsec-blacklists")
    cs_ips = {e["ip"] for e in cs_list}
    bl_ips = {e["ip"] for e in bl_list}

    suricata_alerts = get_suricata_alerts(200)
    suricata_ips = {a.get("src", "") for a in suricata_alerts}

    all_ips = set(ip_hits.keys()) | set(ssh_fail.keys()) | cs_ips | suricata_ips | bl_ips

    risks = []
    for ip in all_ips:
        drops_data = {
            "hits": ip_hits.get(ip, 0),
            "ports": list(ip_ports.get(ip, set())),
        }
        ssh_data = ssh_fail.get(ip, 0)
        risk = calculate_risk(
            ip,
            drops_data=drops_data,
            ssh_data=ssh_data,
            cs_ips=cs_ips,
            suricata_ips=suricata_ips,
            bl_history=ip_hits.get(ip, 0) if ip in bl_ips else 0,
            bl_ips=bl_ips,
        )
        risks.append(risk)

    risks.sort(key=lambda x: x["score"], reverse=True)

    if limit and limit > 0:
        risks = risks[:limit]

    _save_cache({"ts": now, "risks": risks})
    return risks


def get_risk_for_ip(ip, force=False):
    risks = get_all_risks(force=force)
    for r in risks:
        if r["ip"] == ip:
            return r
    return None


def risk_badge(score):
    if score <= 25:
        return "low"
    elif score <= 50:
        return "medium"
    elif score <= 75:
        return "high"
    else:
        return "critical"


def _load_cache():
    try:
        with open(RISK_CACHE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(data):
    try:
        os.makedirs(os.path.dirname(RISK_CACHE), exist_ok=True)
        if len(data.get("risks", [])) > RISK_MAX_STORE:
            data = dict(data)
            data["risks"] = data["risks"][:RISK_MAX_STORE]
        with open(RISK_CACHE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass