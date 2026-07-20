#!/usr/bin/env python3
import subprocess
import json
import os
import re

NFT = "/usr/sbin/nft"
REDIRECT_COMMENT = "aegisgate_dns_redirect"


def _run_nft(args, timeout=10):
    try:
        r = subprocess.run([NFT] + args, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def setup_dns_redirect(wan_iface, lan_ifaces, dns_port=53, gateway_ip=None):
    if not gateway_ip or not lan_ifaces:
        return False, "Missing gateway_ip or lan_ifaces"

    ok, _, err = _run_nft(["list", "chain", "ip", "nat", "PREROUTING"])
    if not ok:
        return False, f"Missing ip nat PREROUTING chain: {err}"
    removed, remove_message = remove_dns_redirect()
    if not removed:
        return False, remove_message

    rules = []
    for iface in lan_ifaces:
        if iface == wan_iface:
            continue
        for protocol in ("udp", "tcp"):
            rules.append([
                "add", "rule", "ip", "nat", "PREROUTING",
                "iifname", iface, protocol, "dport", "53",
                "ip", "daddr", "!=", gateway_ip,
                "dnat", "to", f"{gateway_ip}:{dns_port}",
                "comment", REDIRECT_COMMENT,
            ])

    for rule in rules:
        ok, _, err = _run_nft(rule)
        if not ok:
            remove_dns_redirect()
            return False, f"Failed to add DNS redirect rule: {err}"
    return True, f"DNS redirect rules added: {len(rules)}"


def remove_dns_redirect():
    ok, out, err = _run_nft(["-a", "list", "chain", "ip", "nat", "PREROUTING"])
    if not ok:
        return False, f"Unable to inspect DNS redirects: {err}"
    handles = []
    for line in out.splitlines():
        if REDIRECT_COMMENT not in line:
            continue
        match = re.search(r"\bhandle\s+(\d+)\b", line)
        if match:
            handles.append(match.group(1))
    for handle in handles:
        deleted, _, delete_error = _run_nft([
            "delete", "rule", "ip", "nat", "PREROUTING", "handle", handle,
        ])
        if not deleted:
            return False, f"Failed to remove DNS redirect handle {handle}: {delete_error}"
    return True, f"DNS redirect rules removed: {len(handles)}"


def add_doh_bypass_ips(ip_list):
    _run_nft(["add", "set", "inet", "filter", "doh_providers", "{", "type", "ipv4_addr", ";", "flags", "interval", ";", "}"])
    for ip in ip_list:
        _run_nft(["add", "element", "inet", "filter", "doh_providers", f"{{ {ip} }}"])
    return True, f"Added {len(ip_list)} DoH provider IPs"


def remove_doh_bypass_ips():
    _run_nft(["delete", "set", "inet", "filter", "doh_providers"])
    return True, "DoH provider IPs removed"


def add_dns_strict_client(ip):
    _run_nft(["add", "set", "inet", "filter", "dns_strict_clients", "{", "type", "ipv4_addr", ";", "}"])
    _run_nft(["add", "element", "inet", "filter", "dns_strict_clients", f"{{ {ip} }}"])
    return True, f"Added {ip} to DNS strict clients"


def remove_dns_strict_client(ip):
    _run_nft(["delete", "element", "inet", "filter", "dns_strict_clients", f"{{ {ip} }}"])
    return True, f"Removed {ip} from DNS strict clients"


def get_dns_redirect_status():
    ok, out, err = _run_nft(["-a", "list", "chain", "ip", "nat", "PREROUTING"])
    active = ok and REDIRECT_COMMENT in out
    return {"active": active, "rules": out if ok else err}


def toggle_dns_redirect(enabled=True):
    status = get_dns_redirect_status()
    if enabled and not status.get("active"):
        from modules.ifaces import get_all as get_ifaces
        from modules.dns import get_status as dns_status
        result = get_ifaces()
        ifaces = result[0] if isinstance(result, tuple) else result
        wan = next((i for i in ifaces if i.get("role") == "wan"), None)
        lan_ifaces = [i["name"] for i in ifaces if i.get("role") == "lan"]
        if not wan or not lan_ifaces:
            return False, "No WAN or LAN interface configured"
        info = dns_status()
        gateway_ip = info.get("listen_addr", info.get("listen_address", ""))
        if not gateway_ip or gateway_ip == "0.0.0.0":
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "config.json")
            try:
                with open(config_path, encoding="utf-8") as handle:
                    gateway_ip = json.load(handle).get("lan_ip", "")
            except (OSError, ValueError, TypeError):
                gateway_ip = ""
        if not gateway_ip:
            return False, "LAN gateway IP is not configured"
        return setup_dns_redirect(wan["name"], lan_ifaces, gateway_ip=gateway_ip)
    elif not enabled and status.get("active"):
        return remove_dns_redirect()
    return True, "No change needed"
