#!/usr/bin/env python3
import subprocess

NFT = "/usr/sbin/nft"


def _run_nft(args, timeout=10):
    try:
        r = subprocess.run([NFT] + args, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def setup_dns_redirect(wan_iface, lan_ifaces, dns_port=53, gateway_ip=None):
    if not gateway_ip or not lan_ifaces:
        return False, "Missing gateway_ip or lan_ifaces"

    ok, out, err = _run_nft(["list", "table", "inet", "filter"])
    has_dns_chain = "dns_redirect" in out if ok else False

    if not has_dns_chain:
        ok, out, err = _run_nft([
            "add", "chain", "inet", "filter", "dns_redirect",
            "{", "type", "nat", "hook", "prerouting", "priority", "dstnat", ";", "}",
        ])
        if not ok:
            return False, f"Failed to create dns_redirect chain: {err}"

    rules = []
    for iface in lan_ifaces:
        if iface == wan_iface:
            continue
        rules.append(f"iifname \"{iface}\" udp dport 53 not ip daddr {gateway_ip} counter jump dns_redirect")
        rules.append(f"iifname \"{iface}\" tcp dport 53 not ip daddr {gateway_ip} counter jump dns_redirect")

    for rule in rules:
        _run_nft(["add", "rule", "inet", "filter", "dns_redirect"] + rule.split())

    ok, out, err = _run_nft([
        "add", "rule", "inet", "filter", "dns_redirect",
        "udp", "dport", "53", "dnat", "to", f"{gateway_ip}:{dns_port}",
    ])
    ok2, out2, err2 = _run_nft([
        "add", "rule", "inet", "filter", "dns_redirect",
        "tcp", "dport", "53", "dnat", "to", f"{gateway_ip}:{dns_port}",
    ])
    if ok or ok2:
        return True, "DNS redirect rules added"
    return False, f"Failed to add DNAT rules: {err} {err2}"


def remove_dns_redirect():
    ok, out, err = _run_nft(["list", "table", "inet", "filter"])
    if ok and "dns_redirect" in out:
        _run_nft(["delete", "chain", "inet", "filter", "dns_redirect"])
    return True, "DNS redirect removed"


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
    ok, out, err = _run_nft(["list", "chain", "inet", "filter", "dns_redirect"])
    return {"active": ok and "dns_redirect" in out, "rules": out if ok else err}


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
        gateway_ip = info.get("listen_addr", info.get("listen_address", "172.24.1.1"))
        return setup_dns_redirect(wan["name"], lan_ifaces, gateway_ip=gateway_ip)
    elif not enabled and status.get("active"):
        return remove_dns_redirect()
    return True, "No change needed"