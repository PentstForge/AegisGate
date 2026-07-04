import re
import ipaddress
import json
import os
import time
from collections import Counter

NFT = "/usr/sbin/nft"
CSCLI = "/usr/bin/cscli"
LOG_FILE = "/var/log/ram/nft-drops.log"
AUTH_LOG = "/var/log/auth.log"
_CONFIG = None

def _load_config():
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG
    try:
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "config.json")
        with open(cfg_path) as f:
            _CONFIG = json.load(f)
    except Exception:
        _CONFIG = {}
    return _CONFIG


_excluded_nets_cache = None
_excluded_nets_ts = 0

def _get_excluded_networks():
    global _excluded_nets_cache, _excluded_nets_ts
    now = time.time()
    if _excluded_nets_cache is not None and now - _excluded_nets_ts < 300:
        return _excluded_nets_cache
    cfg = _load_config()
    nets = cfg.get("protected_nets", ["192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "169.254.0.0/16"])
    wan_ip = cfg.get("wan_ip", "")
    result = set()
    for n in nets:
        try:
            result.add(ipaddress.ip_network(n, strict=False))
        except Exception:
            pass
    if wan_ip:
        try:
            result.add(ipaddress.ip_network(f"{wan_ip}/32"))
        except Exception:
            pass
    result.update([
        ipaddress.ip_network("0.0.0.0/32"),
        ipaddress.ip_network("fe80::/10"),
        ipaddress.ip_network("ff00::/8"),
    ])
    _excluded_nets_cache = list(result)
    _excluded_nets_ts = now
    return _excluded_nets_cache


EXCLUDED_NETWORKS = []
EXCLUDED_IPS = set()
EXCLUDED_IFACES = set()
MAC_RE = re.compile(r'^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$')

_DROP_RE = re.compile(r'(DROP\w+):')
_SRC_RE = re.compile(r'SRC=(\S+)')
_DST_RE = re.compile(r'DST=(\S+)')
_PROTO_RE = re.compile(r'PROTO=(\w+)')
_IFACE_RE = re.compile(r'IN=(\S+)')
_DPT_RE = re.compile(r'DPT=(\d+)')
_SPT_RE = re.compile(r'SPT=(\d+)')
_TS_RE = re.compile(r'^(\S+)')
_FAIL_RE = re.compile(r'Failed password for (?:invalid user )?\S+ from (\d+\.\d+\.\d+\.\d+)')
_ACCEPT_RE = re.compile(r'Accepted \w+ for \w+ from (\d+\.\d+\.\d+\.\d+)')

PORT_NAMES = {
    "20": "FTP-DATA", "21": "FTP", "22": "SSH", "23": "Telnet",
    "25": "SMTP", "53": "DNS", "80": "HTTP", "110": "POP3",
    "111": "RPCBind", "123": "NTP", "135": "MSRPC", "137": "NetBIOS",
    "138": "NetBIOS", "139": "NetBIOS", "143": "IMAP", "161": "SNMP",
    "162": "SNMP-Trap", "222": "SSH-Alt", "389": "LDAP", "443": "HTTPS",
    "445": "SMB", "465": "SMTPS", "514": "Syslog", "587": "SMTP-Sub",
    "631": "IPP", "993": "IMAPS", "995": "POP3S", "1433": "MSSQL",
    "1521": "Oracle", "1900": "SSDP", "3306": "MySQL", "3389": "RDP",
    "4443": "HTTPS-Alt", "5222": "XMPP", "5223": "XMPP-C2S",
    "5269": "XMPP-S2S", "5353": "mDNS", "5678": "VNC-Repeater",
    "5900": "VNC", "6379": "Redis", "8080": "HTTP-Alt", "8443": "HTTPS-Alt",
    "8888": "HTTP-Alt", "9090": "Prometheus", "27017": "MongoDB",
}


def port_name(p):
    return PORT_NAMES.get(p, "")


def is_excluded(ip_str):
    if ip_str in EXCLUDED_IPS:
        return True
    try:
        addr = ipaddress.ip_address(ip_str)
        for net in _get_excluded_networks():
            if addr in net:
                return True
    except ValueError:
        pass
    return False


def is_internal(ip_str):
    return is_excluded(ip_str)


def parse_log(lines):
    from datetime import datetime as _dt
    excluded_nets = _get_excluded_networks()
    excluded_ips = EXCLUDED_IPS
    excluded_ifaces = EXCLUDED_IFACES
    port_names = PORT_NAMES
    _ip_addr = ipaddress.ip_address
    entries = []
    append = entries.append
    for line in lines:
        rule = _DROP_RE.search(line)
        if not rule:
            continue
        src = _SRC_RE.search(line)
        dst = _DST_RE.search(line)
        proto = _PROTO_RE.search(line)
        iface = _IFACE_RE.search(line)
        dpt = _DPT_RE.search(line)
        spt = _SPT_RE.search(line)
        ts_match = _TS_RE.match(line)
        ts_raw = ts_match.group(1) if ts_match else ""
        ts_display = ""
        ts_iso = ""
        try:
            dt = _dt.fromisoformat(ts_raw.replace("Z", "+00:00"))
            ts_display = dt.strftime("%m-%d %H:%M")
            ts_iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            ts_display = ts_raw[:16] if len(ts_raw) > 16 else ts_raw
            ts_iso = ts_raw[:19] if len(ts_raw) > 19 else ts_raw
        src_val = src.group(1) if src else ""
        dst_val = dst.group(1) if dst else ""
        iface_val = iface.group(1) if iface else ""
        if iface_val in excluded_ifaces:
            continue
        if src_val in excluded_ips or dst_val in excluded_ips:
            continue
        try:
            if src_val:
                src_addr = _ip_addr(src_val)
                if any(src_addr in net for net in excluded_nets):
                    continue
        except ValueError:
            pass
        append({
            "rule": rule.group(1),
            "src": src_val,
            "dst": dst_val,
            "proto": proto.group(1) if proto else "",
            "iface": iface_val,
            "dpt": dpt.group(1) if dpt else "",
            "dpt_name": port_names.get(dpt.group(1), "") if dpt else "",
            "spt": spt.group(1) if spt else "",
            "time": ts_display,
            "time_iso": ts_iso,
        })
    return entries


def parse_auth_log():
    ssh_fail = Counter()
    ssh_success = set()
    import glob
    excluded_nets = _get_excluded_networks()
    excluded_ips = EXCLUDED_IPS
    log_files = sorted(glob.glob(AUTH_LOG + "*"), reverse=True)
    for lf in log_files:
        if lf.endswith(".gz"):
            import gzip
            try:
                with gzip.open(lf, "rt", errors="replace") as f:
                    for line in f:
                        m = _FAIL_RE.search(line)
                        if m:
                            ip_str = m.group(1)
                            if ip_str not in excluded_ips:
                                try:
                                    if not any(ipaddress.ip_address(ip_str) in net for net in excluded_nets):
                                        ssh_fail[ip_str] += 1
                                except ValueError:
                                    ssh_fail[ip_str] += 1
                        m2 = _ACCEPT_RE.search(line)
                        if m2:
                            ssh_success.add(m2.group(1))
            except Exception:
                pass
        else:
            try:
                with open(lf, "r", errors="replace") as f:
                    for line in f:
                        m = _FAIL_RE.search(line)
                        if m:
                            ip_str = m.group(1)
                            if ip_str not in excluded_ips:
                                try:
                                    if not any(ipaddress.ip_address(ip_str) in net for net in excluded_nets):
                                        ssh_fail[ip_str] += 1
                                except ValueError:
                                    ssh_fail[ip_str] += 1
                        m2 = _ACCEPT_RE.search(line)
                        if m2:
                            ssh_success.add(m2.group(1))
            except Exception:
                pass
    return ssh_fail, ssh_success