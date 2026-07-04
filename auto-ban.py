#!/usr/bin/env python3
import re
import json
import os
import subprocess
import ipaddress
import socket
from collections import Counter, defaultdict

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = "/var/log/ram/nft-drops.log"
NFT = "/usr/sbin/nft"
CONFIG_FILE = os.path.join(DASHBOARD_DIR, "data", "auto-ban-config.json")
ALLOWLIST_FILE = os.path.join(DASHBOARD_DIR, "data", "allowlist.json")
POLICY_FILE = os.path.join(DASHBOARD_DIR, "data", "policy.json")
NET_CONFIG_FILE = os.path.join(DASHBOARD_DIR, "data", "config.json")

SYN_FLOOD_THRESHOLD = 10
PORT_SCAN_THRESHOLD = 8
BRUTEFORCE_THRESHOLD = 5
SSH_BRUTEFORCE_THRESHOLD = 5
TIMEOUT = "24h"

SKIP_RULES = frozenset({
    "DROP_BLACKLIST4_IN", "DROP_BLACKLIST4_SRC", "DROP_BLACKLIST4_DST",
    "DROP_BLACKLIST6_IN", "DROP_BLACKLIST6_SRC", "DROP_BLACKLIST6_DST",
    "DROP_CROWDSEC4_IN", "DROP_CROWDSEC4_SRC", "DROP_CROWDSEC4_DST",
    "DROP_CROWDSEC6_IN", "DROP_CROWDSEC6_SRC", "DROP_CROWDSEC6_DST",
    "DROP_INVALID_IN", "DROP_INVALID_FWD",
})

SKIP_SRC_IPS = frozenset({
    "0.0.0.0", "::",
})

SKIP_RULE_PREFIXES = ("WG_ACL_",)

TRUSTED_REVERSE_SUFFIXES = (
    ".1e100.net",
    ".google.com",
    ".googleusercontent.com",
    ".googlevideo.com",
    ".youtube.com",
)

TRUSTED_CDN_CACHE = {}


def _load_net_config():
    try:
        with open(NET_CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _get_skip_nets():
    cfg = _load_net_config()
    nets = cfg.get("protected_nets", ["192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "169.254.0.0/16"])
    wan_ip = cfg.get("wan_ip", "")
    if wan_ip:
        nets.append(f"{wan_ip}/32")
    lan_ip = cfg.get("lan_ip", "")
    if lan_ip:
        nets.append(f"{lan_ip}/32")
    result = []
    for n in nets:
        try:
            result.append(ipaddress.ip_network(n, strict=False))
        except Exception:
            pass
    return list(set(result))


def _get_wan_ip():
    cfg = _load_net_config()
    return cfg.get("wan_ip", "")


def _get_all_protected_ips():
    cfg = _load_net_config()
    ips = set()
    for key in ("wan_ip", "lan_ip"):
        v = cfg.get(key, "")
        if v:
            ips.add(v)
    ips.update(cfg.get("protected_ips", []))
    return ips


def load_config():
    global SYN_FLOOD_THRESHOLD, PORT_SCAN_THRESHOLD, BRUTEFORCE_THRESHOLD
    global SSH_BRUTEFORCE_THRESHOLD, TIMEOUT
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        SSH_BRUTEFORCE_THRESHOLD = BRUTEFORCE_THRESHOLD = cfg.get("ssh_threshold", 5)
        PORT_SCAN_THRESHOLD = cfg.get("port_scan_threshold", 8)
        SYN_FLOOD_THRESHOLD = cfg.get("syn_flood_threshold", 10)
        TIMEOUT = cfg.get("blacklist_ttl", "24h")
        if TIMEOUT == "0" or TIMEOUT == 0:
            TIMEOUT = "24h"
    except Exception:
        pass


def load_allowlist():
    try:
        with open(ALLOWLIST_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        return set(), set()
    ips = {e["ip"] for e in data.get("ips", [])}
    nets = {ipaddress.ip_network(n["network"]) for n in data.get("networks", [])}
    return ips, nets


def is_protected(ip_str):
    try:
        addr = ipaddress.ip_address(ip_str)
        if ip_str in _get_all_protected_ips():
            return True
        if is_local(ip_str):
            return True
        return False
    except ValueError:
        return True


def is_allowlisted(ip_str, allowlist_ips, allowlist_nets):
    if ip_str in allowlist_ips:
        return True
    if ip_str in _get_all_protected_ips():
        return True
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in allowlist_nets)
    except ValueError:
        return True


def is_local(ip_str):
    try:
        addr = ipaddress.ip_address(ip_str)
        if ip_str in SKIP_SRC_IPS:
            return True
        if addr.is_multicast or addr.is_reserved:
            return True
        return any(addr in net for net in _get_skip_nets())
    except ValueError:
        return True


def is_trusted_cdn(ip_str):
    cached = TRUSTED_CDN_CACHE.get(ip_str)
    if cached is not None:
        return cached
    try:
        addr = ipaddress.ip_address(ip_str)
        if addr.is_private or addr.is_loopback or addr.is_multicast or addr.is_unspecified:
            TRUSTED_CDN_CACHE[ip_str] = True
            return True
        hostname = socket.gethostbyaddr(ip_str)[0].rstrip(".").lower()
        trusted = any(hostname.endswith(suffix) for suffix in TRUSTED_REVERSE_SUFFIXES)
        TRUSTED_CDN_CACHE[ip_str] = trusted
        return trusted
    except Exception:
        TRUSTED_CDN_CACHE[ip_str] = False
        return False


def is_dst_our_ip(ip_str):
    return ip_str in _get_all_protected_ips()


def get_existing_blacklist():
    existing = set()
    for family in ("inet",):
        try:
            result = subprocess.run(
                [NFT, "list", "set", family, "filter", "blacklist_ipv4"],
                capture_output=True, text=True, timeout=10
            )
            for match in re.finditer(r'(\d+\.\d+\.\d+\.\d+)', result.stdout):
                existing.add(match.group(1))
        except Exception:
            pass
    return existing


def _should_skip_rule(rule):
    if rule in SKIP_RULES:
        return True
    for prefix in SKIP_RULE_PREFIXES:
        if rule.startswith(prefix):
            return True
    return False


def parse_auth_log():
    ssh_fail_count = Counter()
    import glob as _glob
    log_files = sorted(_glob.glob("/var/log/auth.log*"), reverse=True)
    for lf in log_files:
        if lf.endswith(".gz"):
            import gzip
            try:
                with gzip.open(lf, "rt", errors="replace") as f:
                    for line in f:
                        m = re.search(r'Failed password for (?:invalid user )?\S+ from (\d+\.\d+\.\d+\.\d+)', line)
                        if m:
                            ip = m.group(1)
                            if not is_local(ip) and not is_protected(ip):
                                ssh_fail_count[ip] += 1
            except Exception:
                pass
        else:
            try:
                with open(lf, "r", errors="replace") as f:
                    for line in f:
                        m = re.search(r'Failed password for (?:invalid user )?\S+ from (\d+\.\d+\.\d+\.\d+)', line)
                        if m:
                            ip = m.group(1)
                            if not is_local(ip) and not is_protected(ip):
                                ssh_fail_count[ip] += 1
            except Exception:
                pass
    return ssh_fail_count


def parse_attacks():
    ip_ports = defaultdict(set)
    ip_syn_count = Counter()
    ip_bruteforce = Counter()
    ip_rules = defaultdict(set)

    with open(LOG_FILE, "r") as f:
        for line in f:
            rule_m = re.search(r'(DROP_\w+):', line)
            if not rule_m:
                continue
            rule = rule_m.group(1)
            if _should_skip_rule(rule):
                continue

            src_m = re.search(r'SRC=([^\s]+)', line)
            dst_m = re.search(r'DST=([^\s]+)', line)
            if not src_m:
                continue
            ip = src_m.group(1)
            if not re.match(r'^\d+\.\d+\.\d+\.\d+$', ip):
                continue
            if ip in SKIP_SRC_IPS:
                continue
            if is_protected(ip) or is_local(ip):
                continue

            proto_m = re.search(r'PROTO=(\w+)', line)
            dpt_m = re.search(r'DPT=(\d+)', line)

            if rule == "DROP_DEFAULT_IN":
                if not dpt_m:
                    continue
                port = int(dpt_m.group(1))
                ip_ports[ip].add(port)
                if proto_m and proto_m.group(1) == "TCP" and 'SYN' in line:
                    ip_syn_count[ip] += 1
                    if port in (22, 222):
                        ip_bruteforce[ip] += 1
                continue

            if rule == "DROP_SPOOF_RFC1918":
                continue

            if rule.startswith("DROP_BOGON") or rule.startswith("DROP_LOOPBACK") or \
               rule.startswith("DROP_MCAST") or rule.startswith("DROP_BCAST"):
                continue

            if rule in ("DROP_ICMPFLOOD_IN", "DROP_ICMPFLOOD_FWD", "DROP_ICMPFLOOD_KNOWN",
                        "DROP_ICMP_L2", "DROP_ICMPV6_IN"):
                continue

            if rule.startswith("DROP_TTL"):
                continue

            ip_rules[ip].add(rule)

            if proto_m and proto_m.group(1) == "TCP" and dpt_m:
                port = int(dpt_m.group(1))
                if port in (22, 222):
                    ip_bruteforce[ip] += 1

    ban_ips = set()

    for ip, ports in ip_ports.items():
        if len(ports) >= PORT_SCAN_THRESHOLD:
            ban_ips.add(ip)
            continue

    for ip, count in ip_syn_count.items():
        if count >= SYN_FLOOD_THRESHOLD:
            ban_ips.add(ip)

    for ip, count in ip_bruteforce.items():
        if count >= BRUTEFORCE_THRESHOLD:
            ban_ips.add(ip)

    for ip, rules in ip_rules.items():
        attack_rules = rules - {"DROP_DEFAULT_IN", "DROP_XMAS_IN", "DROP_XMAS2_IN",
                                "DROP_SYNFIN_IN", "DROP_FINRST_IN", "DROP_SYNRST_IN",
                                "DROP_NULL_IN", "DROP_XMAS_FWD", "DROP_XMAS2_FWD",
                                "DROP_SYNFIN_FWD", "DROP_FINRST_FWD", "DROP_SYNRST_FWD",
                                "DROP_NULL_FWD", "DROP_NOSYN_FWD", "DROP_TINYMSS_FWD",
                                "DROP_PORT0_FWD"}
        if len(attack_rules) >= 2:
            ban_ips.add(ip)

    ssh_fail_count = parse_auth_log()
    for ip, count in ssh_fail_count.items():
        if count >= SSH_BRUTEFORCE_THRESHOLD:
            ban_ips.add(ip)

    suricata_alerts = parse_suricata_alerts()
    for ip, count in suricata_alerts.items():
        if count >= 10:
            ban_ips.add(ip)

    return ban_ips


def ban_ip(ip, timeout):
    if ip in SKIP_SRC_IPS or ip == "0.0.0.0" or is_trusted_cdn(ip):
        return
    subprocess.run([NFT, "add", "element", "inet", "filter", "blacklist_ipv4",
                    f"{{ {ip} timeout {timeout} }}"], capture_output=True)


def parse_suricata_alerts():
    ip_suricata = defaultdict(int)
    try:
        eve_file = "/var/log/suricata/eve.json"
        if not os.path.exists(eve_file):
            return ip_suricata
        with open(eve_file, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            tail_size = min(size, 20 * 1024 * 1024)
            f.seek(-tail_size, os.SEEK_END)
            if tail_size != size:
                f.readline()
            lines = [line.decode("utf-8", "replace") for line in f]
            for line in lines:
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("event_type") != "alert":
                    continue
                alert = ev.get("alert", {})
                severity = alert.get("severity", 99)
                if severity > 1:
                    continue
                category = alert.get("category", "").lower()
                if any(kw in category for kw in ("denial of service", "dos", "ddos",
                                                    "udp flood", "bridge")):
                    continue
                src = ev.get("src_ip", "")
                if not src or not re.match(r'^\d+\.\d+\.\d+\.\d+$', src):
                    continue
                if src in SKIP_SRC_IPS:
                    continue
                if is_protected(src) or is_local(src):
                    continue
                ip_suricata[src] += 1
    except Exception:
        pass
    return ip_suricata


def main():
    load_config()
    allowlist_ips, allowlist_nets = load_allowlist()
    existing = get_existing_blacklist()
    ban_ips = parse_attacks()
    new_bans = 0
    skipped = 0
    protected_skipped = 0
    for ip in ban_ips:
        if is_protected(ip):
            protected_skipped += 1
            continue
        if is_allowlisted(ip, allowlist_ips, allowlist_nets):
            skipped += 1
            continue
        if ip not in existing:
            ban_ip(ip, TIMEOUT)
            new_bans += 1
    print(f"Attackers: {len(ban_ips)}, newly banned: {new_bans}, existing: {len(existing)}, allowlist skipped: {skipped}, protected skipped: {protected_skipped}")


if __name__ == "__main__":
    main()
