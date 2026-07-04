import re
import os
import json
import time
import gzip
import glob
import subprocess
import ipaddress
from datetime import datetime, timedelta
from collections import Counter, defaultdict

LOG_FILE = "/var/log/ram/nft-drops.log"
AUTH_LOG = "/var/log/auth.log"
EVE_JSON = "/var/log/suricata/eve.json"
CACHE_FILE = "/var/log/ram/port_stats_cache.json"
CACHE_TTL = 60
LAN_NETS = [ipaddress.ip_network(n) for n in ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10", "127.0.0.0/8", "fc00::/7"]]


def _is_local_ip(ip_str):
    try:
        addr = ipaddress.ip_address(ip_str)
        for net in LAN_NETS:
            if addr in net:
                return True
        return False
    except ValueError:
        return False

_PORT_SERVICE_MAP = {
    20: "FTP-DATA", 21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS",
    80: "HTTP", 110: "POP3", 123: "NTP", 143: "IMAP", 161: "SNMP", 194: "IRC",
    222: "SSH-Alt", 443: "HTTPS", 445: "SMB", 465: "SMTPS", 514: "Syslog",
    587: "SMTP-Sub", 631: "IPP", 636: "LDAPS", 853: "DNS-TLS", 993: "IMAPS",
    995: "POP3S", 1080: "SOCKS", 1433: "MSSQL", 1521: "Oracle", 1723: "PPTP",
    1883: "MQTT", 2222: "SSH-Alt", 3306: "MySQL", 3389: "RDP", 4443: "HTTPS-Alt",
    5000: "UPnP", 5060: "SIP", 5432: "PostgreSQL", 5672: "AMQP", 5900: "VNC",
    6379: "Redis", 6443: "K8s-API", 8080: "HTTP-Alt", 8443: "HTTPS-Alt",
    8888: "HTTP-Alt", 9000: "PHP-FPM", 9090: "Prometheus", 9200: "Elasticsearch",
    27017: "MongoDB", 51820: "WireGuard", 5678: "mDNS",
}

_RULE_COLORS = {
    "DROP_BLACKLIST4": "#f85149",
    "DROP_CROWDSEC4": "#a371f7",
    "DROP_INVALID": "#d29922",
    "DROP_DEFAULT": "#58a6ff",
    "DROP_XMAS": "#f0883e",
    "DROP_SYNFIN": "#f0883e",
    "DROP_XMAS2": "#f0883e",
    "DROP_FINRST": "#f0883e",
    "DROP_SYNRST": "#f0883e",
    "DROP_NULL": "#f0883e",
    "DROP_NOSYN": "#f0883e",
    "DROP_TINYMSS": "#f0883e",
    "DROP_PORT0": "#f0883e",
    "DROP_BOGON": "#8b949e",
    "DROP_REDIRECT": "#8b949e",
    "DROP_QUENCH": "#8b949e",
    "DROP_TTL0": "#8b949e",
    "DROP_TTL1": "#8b949e",
    "WG_ACL_DENY": "#ff7b72",
    "DROP_ABUSE": "#da3633",
    "DROP_SYN_FLOOD": "#ff7b72",
    "DROP_ICMP_FLOOD": "#ff7b72",
}


def _port_name(port):
    return _PORT_SERVICE_MAP.get(port, str(port))


def _rule_category(rule):
    for prefix, color in _RULE_COLORS.items():
        if rule.startswith(prefix):
            return prefix.replace("DROP_", "").replace("WG_ACL_", ""), color
    return "OTHER", "#8b949e"


_parse_cache = {}

PERIOD_SECONDS = {"5m": 300, "1h": 3600, "24h": 86400, "7d": 604800}


def _parse_line_ts(line):
    iso_m = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', line)
    if iso_m:
        try:
            dt = datetime.fromisoformat(iso_m.group(1))
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except Exception:
            pass
    return None


def _period_cutoff(period):
    if period and period in PERIOD_SECONDS:
        return datetime.now() - timedelta(seconds=PERIOD_SECONDS[period])
    return None


def _timeline_count(event_type, cutoff_dt):
    try:
        from modules.timeline_db import get_event_count
        since_ts = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S") if cutoff_dt else None
        return get_event_count(since_ts=since_ts, event_type=event_type)
    except Exception:
        return None


_DROP_RULE_RE = re.compile(r'kernel:\s+(\w+):')
_DROP_SRC_RE = re.compile(r'SRC=([0-9.]+)')
_DROP_DPT_RE = re.compile(r'DPT=(\d+)')
_DROP_PROTO_RE = re.compile(r'PROTO=(\w+)')


def _parse_drop_line(line, cutoff_dt=None):
    line_ts = _parse_line_ts(line)
    if cutoff_dt and line_ts and line_ts < cutoff_dt:
        return None
    rm = _DROP_RULE_RE.search(line)
    if not rm:
        return None
    rule = rm.group(1)
    sm = _DROP_SRC_RE.search(line)
    dm = _DROP_DPT_RE.search(line)
    pm = _DROP_PROTO_RE.search(line)
    if not dm:
        return None
    src_ip = sm.group(1) if sm else ""
    dpt = int(dm.group(1))
    proto = pm.group(1) if pm else "TCP"
    is_local_src = src_ip and _is_local_ip(src_ip)
    return rule, src_ip, dpt, proto, is_local_src


def parse_drops(period=None):
    global _parse_cache
    now = time.time()
    cache_key = period or "all"
    if cache_key in _parse_cache and now - _parse_cache[cache_key]["ts"] < CACHE_TTL:
        return _parse_cache[cache_key]["data"]

    cutoff_dt = _period_cutoff(period)

    port_hits = Counter()
    port_ips = defaultdict(Counter)
    port_rules = defaultdict(Counter)
    port_protos = defaultdict(Counter)
    rule_counts = Counter()
    src_ip_ports = defaultdict(set)
    total_drops = 0
    seen_lines = set()

    def _add_drop(rule, src_ip, dpt, proto, is_local_src):
        nonlocal total_drops
        total_drops += 1
        port_hits[dpt] += 1
        if src_ip and not is_local_src:
            port_ips[dpt][src_ip] += 1
        port_rules[dpt][rule] += 1
        port_protos[dpt][proto] += 1
        rule_counts[rule] += 1
        if src_ip and not is_local_src and len(src_ip_ports[src_ip]) < 20:
            src_ip_ports[src_ip].add(dpt)

    def _line_key(line):
        m = re.search(r'SRC=([0-9.]+)', line)
        d = re.search(r'DPT=(\d+)', line)
        r = re.search(r'kernel:\s+(\w+):', line)
        p = re.search(r'PROTO=(\w+)', line)
        return (r.group(1) if r else '', m.group(1) if m else '', d.group(1) if d else '', p.group(1) if p else '')

    try:
        with open(LOG_FILE, "r", errors="replace") as f:
            for line in f:
                parsed = _parse_drop_line(line, cutoff_dt)
                if parsed is None:
                    continue
                rule, src_ip, dpt, proto, is_local_src = parsed
                key = _line_key(line)
                seen_lines.add(key)
                _add_drop(rule, src_ip, dpt, proto, is_local_src)
    except Exception:
        pass

    if cutoff_dt:
        try:
            since_str = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
            proc = subprocess.run(
                ["journalctl", "-k", "--since", since_str, "--no-pager", "-o", "short-iso"],
                capture_output=True, text=True, timeout=10
            )
            if proc.returncode == 0 and proc.stdout:
                for line in proc.stdout.splitlines():
                    parsed = _parse_drop_line(line, cutoff_dt)
                    if parsed is None:
                        continue
                    key = _line_key(line)
                    if key in seen_lines:
                        continue
                    rule, src_ip, dpt, proto, is_local_src = parsed
                    _add_drop(rule, src_ip, dpt, proto, is_local_src)
        except Exception:
            pass

    ssh_fail_ips = Counter()
    ssh_total = 0
    local_offset = datetime.now().astimezone().utcoffset()
    if local_offset is None:
        local_offset = timedelta(hours=3)
    log_files = sorted(glob.glob(AUTH_LOG + "*"), reverse=True)
    try:
        for lf in log_files:
            try:
                if lf.endswith(".gz"):
                    fh = gzip.open(lf, "rt", errors="replace")
                else:
                    fh = open(lf, "r", errors="replace")
                with fh:
                    for line in fh:
                        if "Failed password" not in line and "Invalid user" not in line:
                            continue
                        if cutoff_dt:
                            line_ts = _parse_line_ts(line)
                            if line_ts is not None:
                                iso_m = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2})', line)
                                if iso_m:
                                    try:
                                        full_dt = datetime.fromisoformat(iso_m.group(1))
                                        utc_off = full_dt.utcoffset()
                                        line_ts = full_dt.replace(tzinfo=None)
                                        if utc_off and utc_off != local_offset:
                                            line_ts = line_ts + (local_offset - utc_off)
                                    except Exception:
                                        pass
                                if line_ts < cutoff_dt:
                                    continue
                        sm = re.search(r"from\s+([0-9.]+)", line)
                        if sm:
                            ip = sm.group(1)
                            if not _is_local_ip(ip):
                                ssh_fail_ips[ip] += 1
                                ssh_total += 1
            except Exception:
                continue
    except Exception:
        pass

    suricata_count = _timeline_count("suricata", cutoff_dt)
    if suricata_count is None:
        suricata_count = 0
        try:
            if os.path.exists(EVE_JSON):
                with open(EVE_JSON, "r", errors="replace") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    start = max(0, size - 5 * 1024 * 1024)
                    f.seek(start)
                    if start > 0:
                        f.readline()
                    for line in f:
                        try:
                            ev = json.loads(line)
                            if ev.get("event_type") == "alert":
                                if cutoff_dt:
                                    ts_str = ev.get("timestamp", "")[:19]
                                    if ts_str:
                                        try:
                                            ev_dt = datetime.fromisoformat(ts_str)
                                            if ev_dt.tzinfo is not None:
                                                ev_dt = ev_dt.replace(tzinfo=None)
                                            if ev_dt < cutoff_dt:
                                                continue
                                        except Exception:
                                            pass
                                suricata_count += 1
                        except Exception:
                            pass
        except Exception:
            pass

    top_ports = []
    for port, count in port_hits.most_common(20):
        ips = port_ips[port].most_common(10)
        rules = port_rules[port].most_common(5)
        protos = port_protos[port].most_common(3)
        top_ports.append({
            "port": port,
            "name": _port_name(port),
            "hits": count,
            "ips": [{"ip": ip, "count": c} for ip, c in ips],
            "rules": [{"rule": r, "count": c, "cat": _rule_category(r)[0], "color": _rule_category(r)[1]} for r, c in rules],
            "protos": [{"proto": p, "count": c} for p, c in protos],
        })

    top_attackers = []
    all_ip_counter = Counter()
    for port_data in port_ips.values():
        for ip, c in port_data.items():
            all_ip_counter[ip] += c
    for ip, count in all_ip_counter.most_common(30):
        top_attackers.append({"ip": ip, "hits": count, "ports_scanned": len(src_ip_ports.get(ip, set()))})

    result = {
        "total_drops": total_drops,
        "ssh_fails": ssh_total,
        "suricata_alerts": suricata_count,
        "cs_bans": _timeline_count("cs_ban", cutoff_dt) or 0,
        "top_ports": top_ports,
        "top_attackers": top_attackers,
        "rule_counts": [{"rule": r, "count": c, "cat": _rule_category(r)[0], "color": _rule_category(r)[1]} for r, c in rule_counts.most_common(15)],
        "ssh_top_ips": [{"ip": ip, "count": c} for ip, c in ssh_fail_ips.most_common(10)],
    }

    _parse_cache[cache_key] = {"ts": now, "data": result}
    return result


def get_port_stats(period=None):
    data = parse_drops(period)
    from modules.geoip import bulk_lookup, get_flag
    attacker_ips = [a["ip"] for a in data.get("top_attackers", [])]
    ssh_ips = [a["ip"] for a in data.get("ssh_top_ips", [])]
    all_ips = list(dict.fromkeys(attacker_ips + ssh_ips))
    if all_ips:
        geo_data = bulk_lookup(all_ips, limit=100)
        geo_map = {g["ip"]: g for g in geo_data}
    else:
        geo_map = {}

    for entry in data.get("top_attackers", []):
        ip = entry["ip"]
        g = geo_map.get(ip, {})
        entry["country"] = g.get("country")
        entry["country_name"] = g.get("country_name")
        entry["flag"] = get_flag(g.get("country"))
        entry["asn_org"] = g.get("asn_org")

    for entry in data.get("ssh_top_ips", []):
        ip = entry["ip"]
        g = geo_map.get(ip, {})
        entry["country"] = g.get("country")
        entry["country_name"] = g.get("country_name")
        entry["flag"] = get_flag(g.get("country"))

    for pentry in data.get("top_ports", []):
        for ip_entry in pentry.get("ips", []):
            ip = ip_entry["ip"]
            g = geo_map.get(ip, {})
            ip_entry["country"] = g.get("country")
            ip_entry["flag"] = get_flag(g.get("country"))

    country_counts = Counter()
    for entry in data.get("top_attackers", []):
        cc = entry.get("country") or "XX"
        if cc in ("XX", "LOCAL", None):
            continue
        country_counts[cc] += entry.get("hits", 1)
    data["country_stats"] = [{"country": cc, "flag": get_flag(cc), "hits": count} for cc, count in country_counts.most_common(15)]

    return data
