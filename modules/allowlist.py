import json
import os
import subprocess
import ipaddress
from datetime import datetime

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWLIST_FILE = os.path.join(DASHBOARD_DIR, "data", "allowlist.json")
NFT = "/usr/sbin/nft"

_NET_CONFIG = None

def _load_net_config():
    global _NET_CONFIG
    if _NET_CONFIG is not None:
        return _NET_CONFIG
    try:
        with open(os.path.join(DASHBOARD_DIR, "data", "config.json")) as f:
            _NET_CONFIG = json.load(f)
    except Exception:
        _NET_CONFIG = {}
    return _NET_CONFIG

def _get_protected_ips():
    cfg = _load_net_config()
    ips = cfg.get("protected_ips", [])
    ips.append("127.0.0.1")
    return list(set(ips))

def _get_protected_nets():
    cfg = _load_net_config()
    nets = cfg.get("protected_nets", ["127.0.0.0/8"])
    lan_if = cfg.get("lan_interface", "")
    if lan_if:
        try:
            import subprocess as sp
            addr = sp.check_output(["ip", "-4", "addr", "show", lan_if], text=True)
            for line in addr.splitlines():
                if "inet " in line:
                    net = line.strip().split()[1]
                    nets.append(net)
        except Exception:
            pass
    return list(set(nets))


def _default_allowlist():
    return {
        "networks": [],
        "ips": [],
        "emergency_bypass": False,
        "emergency_bypass_since": None,
    }


def load_allowlist():
    try:
        with open(ALLOWLIST_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        data = _default_allowlist()
    if "emergency_bypass" not in data:
        data["emergency_bypass"] = False
    if "emergency_bypass_since" not in data:
        data["emergency_bypass_since"] = None
    return data


def save_allowlist(data):
    os.makedirs(os.path.dirname(ALLOWLIST_FILE), exist_ok=True)
    with open(ALLOWLIST_FILE, "w") as f:
        json.dump(data, f, indent=2)
    sync_nft_sets(data)


def get_all_entries():
    data = load_allowlist()
    entries = []
    for net in _get_protected_nets():
        entries.append({"value": net, "comment": "LAN/Protected", "protected": True, "type": "network"})
    for ip in _get_protected_ips():
        entries.append({"value": ip, "comment": "WAN IP/Protected", "protected": True, "type": "ip"})
    for net in data.get("networks", []):
        entries.append({"value": net["network"], "comment": net.get("comment", ""), "protected": False,
                        "added_at": net.get("added_at", ""), "type": "network"})
    for ip_entry in data.get("ips", []):
        entries.append({"value": ip_entry["ip"], "comment": ip_entry.get("comment", ""), "protected": False,
                        "added_at": ip_entry.get("added_at", ""), "type": "ip"})
    return entries


def add_ip(ip, comment=""):
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return False, f"Invalid IP: {ip}"
    data = load_allowlist()
    for entry in data.get("ips", []):
        if entry["ip"] == ip:
            return False, f"IP {ip} already in allowlist"
    data.setdefault("ips", []).append({
        "ip": ip,
        "comment": comment,
        "added_by": "admin",
        "added_at": datetime.now().isoformat(),
    })
    save_allowlist(data)
    from modules.nft_utils import unban_ip, unban_cs_decision
    unban_ip(ip)
    unban_cs_decision(ip)
    return True, f"Added {ip} to allowlist and unbanned"


def add_network(network, comment=""):
    try:
        ipaddress.ip_network(network, strict=False)
    except ValueError:
        return False, f"Invalid network: {network}"
    data = load_allowlist()
    for entry in data.get("networks", []):
        if entry["network"] == network:
            return False, f"Network {network} already in allowlist"
    data.setdefault("networks", []).append({
        "network": network,
        "comment": comment,
        "added_by": "admin",
        "added_at": datetime.now().isoformat(),
    })
    save_allowlist(data)
    from modules.nft_utils import unban_network, unban_cs_decision
    removed = unban_network(network)
    net = ipaddress.ip_network(network, strict=False)
    cs = __import__("modules.nft_utils", fromlist=["nft_set_ips"]).nft_set_ips("filter", "crowdsec-blacklists")
    for entry in cs:
        try:
            if ipaddress.ip_address(entry["ip"]) in net:
                unban_cs_decision(entry["ip"])
        except ValueError:
            continue
    if removed:
        return True, f"Added {network} to allowlist and unbanned matching IPs"
    return True, f"Added {network} to allowlist"


def remove_ip(ip):
    if ip in _get_protected_ips():
        return False, f"Cannot remove protected IP: {ip}"
    data = load_allowlist()
    before = len(data.get("ips", []))
    data["ips"] = [e for e in data.get("ips", []) if e["ip"] != ip]
    if len(data["ips"]) == before:
        return False, f"IP {ip} not found in allowlist"
    save_allowlist(data)
    _remove_nft_element(ip)
    return True, f"Removed {ip} from allowlist"


def remove_network(network):
    if network in _get_protected_nets():
        return False, f"Cannot remove protected network: {network}"
    data = load_allowlist()
    before = len(data.get("networks", []))
    data["networks"] = [e for e in data.get("networks", []) if e["network"] != network]
    if len(data["networks"]) == before:
        return False, f"Network {network} not found in allowlist"
    save_allowlist(data)
    _remove_nft_element(network)
    return True, f"Removed {network} from allowlist"


def _remove_nft_element(addr):
    try:
        subprocess.run([NFT, "delete", "element", "inet", "filter", "allowlist_ipv4",
                       f"{{ {addr} }}"], capture_output=True, text=True, timeout=5)
    except Exception:
        pass


def is_allowlisted(ip_str):
    data = load_allowlist()
    for entry in data.get("ips", []):
        if entry["ip"] == ip_str:
            return True
    for entry in data.get("networks", []):
        try:
            if ipaddress.ip_address(ip_str) in ipaddress.ip_network(entry["network"], strict=False):
                return True
        except ValueError:
            pass
    if ip_str in _get_protected_ips():
        return True
    for net in _get_protected_nets():
        try:
            if ipaddress.ip_address(ip_str) in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:
            pass
    return False


def toggle_emergency_bypass(enable):
    data = load_allowlist()
    data["emergency_bypass"] = enable
    data["emergency_bypass_since"] = datetime.now().isoformat() if enable else None
    save_allowlist(data)
    return True, f"Emergency bypass {'enabled' if enable else 'disabled'}"


def get_emergency_bypass():
    data = load_allowlist()
    return data.get("emergency_bypass", False)


def sync_nft_sets(data):
    try:
        existing = subprocess.run([NFT, "-j", "list", "set", "inet", "filter", "allowlist_ipv4"],
                                  capture_output=True, text=True, timeout=5)
        if existing.returncode != 0:
            subprocess.run([NFT, "add", "set", "inet", "filter", "allowlist_ipv4",
                           "{ type ipv4_addr ; flags interval,timeout ; auto-merge ; }"],
                          capture_output=True, text=True, timeout=5)
    except Exception:
        pass

    all_ips = set(_get_protected_ips())
    for entry in data.get("ips", []):
        all_ips.add(entry["ip"])
    for network_str in _get_protected_nets():
        try:
            net = ipaddress.ip_network(network_str, strict=False)
            all_ips.add(str(net))
        except ValueError:
            pass
    for entry in data.get("networks", []):
        try:
            net = ipaddress.ip_network(entry["network"], strict=False)
            all_ips.add(str(net))
        except ValueError:
            pass

    for addr in all_ips:
        try:
            subprocess.run([NFT, "add", "element", "inet", "filter", "allowlist_ipv4",
                           f"{{ {addr} }}"], capture_output=True, text=True, timeout=5)
        except Exception:
            pass


def ensure_nft_sets():
    try:
        subprocess.run([NFT, "add", "set", "inet", "filter", "allowlist_ipv4",
                       "{ type ipv4_addr ; flags interval,timeout ; auto-merge ; }"],
                      capture_output=True, text=True, timeout=5)
    except Exception:
        pass
    try:
        subprocess.run([NFT, "add", "set", "inet", "filter", "allowlist_ipv6",
                       "{ type ipv6_addr ; flags interval,timeout ; auto-merge ; }"],
                      capture_output=True, text=True, timeout=5)
    except Exception:
        pass
    data = load_allowlist()
    sync_nft_sets(data)