#!/usr/bin/env python3
import os
import json
import subprocess
import re as _re

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
IFACES_FILE = os.path.join(DATA_DIR, "ifaces.json")

_IFACE_RE = _re.compile(r'^[a-zA-Z0-9_.\-]{1,15}$')
_VID_RE = _re.compile(r'^[1-9][0-9]{0,3}$')
_IPCIDR_RE = _re.compile(r'^[0-9a-fA-F:./]{1,43}$')

ROLES = {
    "wan": {"label": "WAN", "icon": "\U0001f310", "desc": "Internet uplink", "color": "#58a6ff"},
    "lan": {"label": "LAN", "icon": "\U0001f3e0", "desc": "Local network", "color": "#3fb950"},
    "wifi": {"label": "WiFi", "icon": "\U0001f4f6", "desc": "Wireless access point", "color": "#d29922"},
    "vpn": {"label": "VPN", "icon": "\U0001f512", "desc": "VPN tunnel", "color": "#8b5cf6"},
    "dmz": {"label": "DMZ", "icon": "\U0001f6e1", "desc": "Demilitarized zone", "color": "#f85149"},
    "mgmt": {"label": "Management", "icon": "\u2699", "desc": "Management/OutOfBand", "color": "#e3b341"},
    "voip": {"label": "VoIP", "icon": "\U0001f4de", "desc": "Voice over IP VLAN", "color": "#79c0ff"},
    "iot": {"label": "IoT", "icon": "\U0001f4e1", "desc": "IoT devices VLAN", "color": "#f0883e"},
    "guest": {"label": "Guest", "icon": "\U0001f91d", "desc": "Guest network VLAN", "color": "#a371f7"},
    "unused": {"label": "Unused", "icon": "\u2796", "desc": "Not assigned", "color": "#6e7681"},
}


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def _run_sh(cmd, timeout=10):
    try:
        r = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def _default_config():
    wan, lan, vpns = _auto_detect()
    cfg = {
        "interfaces": {},
        "vlans": {},
        "default_wan": wan,
        "default_lan": lan,
        "default_vpn": vpns[0] if vpns else "",
    }
    all_ifaces = _scan_system()
    for iface in all_ifaces:
        name = iface["name"]
        role = "unused"
        if name == wan:
            role = "wan"
        elif name == lan:
            role = "lan"
        elif name in vpns:
            role = "vpn"
        elif iface.get("link_type") == "wifi" or name.startswith("wlan"):
            role = "wifi"
        cfg["interfaces"][name] = {
            "role": role,
            "label": ROLES[role]["label"] + ": " + name,
            "vlan_id": None,
            "vlan_parent": None,
            "mtu": iface.get("mtu", 1500),
            "enabled": iface.get("operstate") == "UP",
            "managed": role in ("wan", "lan", "vpn", "wifi"),
        }
    return cfg


def _auto_detect():
    wan = None
    ok, out, _ = _run(["ip", "-j", "route", "show", "default"])
    if ok and out:
        try:
            for r in json.loads(out):
                dev = r.get("dev", "")
                if dev and dev != "lo":
                    wan = dev
                    break
        except Exception:
            pass
    if not wan:
        ok, out, _ = _run(["ip", "route", "show", "default"])
        if ok and out:
            parts = out.strip().split()
            for i, p in enumerate(parts):
                if p == "dev" and i + 1 < len(parts):
                    wan = parts[i + 1]
    lan = None
    vpns = []
    ok, out, _ = _run(["ip", "-j", "addr", "show"])
    if ok and out:
        try:
            for i in json.loads(out):
                name = i.get("ifname", "")
                state = i.get("operstate", "")
                if name == "lo" or name == wan:
                    continue
                if name.startswith("wg"):
                    vpns.append(name)
                    continue
                if name.startswith("docker") or name.startswith("br-") or name.startswith("veth"):
                    continue
                if state in ("UP", "UNKNOWN", "DORMANT") and not lan:
                    lan = name
        except Exception:
            pass
    return wan or "eth0", lan or "eth1", vpns


def _scan_system():
    ifaces = []
    ok, out, _ = _run(["ip", "-j", "addr", "show"])
    if not ok or not out:
        return ifaces
    try:
        raw = json.loads(out)
    except Exception:
        return ifaces
    for i in raw:
        name = i.get("ifname", "")
        if name == "lo":
            continue
        ipv4 = ""
        ipv6 = ""
        for a in i.get("addr_info", []):
            fam = a.get("family", "")
            local = a.get("local", "")
            pfx = a.get("prefixlen", "")
            if fam == "inet" and not ipv4:
                ipv4 = f"{local}/{pfx}"
            elif fam == "inet6" and not ipv6 and not local.startswith("fe80"):
                ipv6 = f"{local}/{pfx}"
            elif not fam and local and ":" not in local and not ipv4:
                ipv4 = f"{local}/{pfx}"
            elif not fam and local and ":" in local and not local.startswith("fe80") and not ipv6:
                ipv6 = f"{local}/{pfx}"
        mtu = 1500
        ok_m, out_m, _ = _run(["cat", f"/sys/class/net/{name}/mtu"])
        if ok_m:
            try:
                mtu = int(out_m.strip())
            except ValueError:
                pass
        mac = ""
        ok_mac, out_mac, _ = _run(["cat", f"/sys/class/net/{name}/address"])
        if ok_mac:
            mac = out_mac.strip()
        link_type = "ethernet"
        if name.startswith("wg"):
            link_type = "wireguard"
        elif name.startswith("wlan") or name.startswith("wifi"):
            link_type = "wifi"
        elif name.startswith("br"):
            link_type = "bridge"
        elif "." in name and name.split(".")[-1].isdigit():
            link_type = "vlan"
        speed = 0
        ok_s, out_s, _ = _run(["cat", f"/sys/class/net/{name}/speed"])
        if ok_s:
            try:
                speed = int(out_s.strip())
            except ValueError:
                pass
        rx_bytes = 0
        tx_bytes = 0
        ok_rx, out_rx, _ = _run(["cat", f"/sys/class/net/{name}/statistics/rx_bytes"])
        if ok_rx:
            try:
                rx_bytes = int(out_rx.strip())
            except ValueError:
                pass
        ok_tx, out_tx, _ = _run(["cat", f"/sys/class/net/{name}/statistics/tx_bytes"])
        if ok_tx:
            try:
                tx_bytes = int(out_tx.strip())
            except ValueError:
                pass
        vlan_id = None
        vlan_parent = None
        if "." in name:
            parts = name.rsplit(".", 1)
            if parts[1].isdigit():
                vlan_parent = parts[0]
                vlan_id = int(parts[1])
                link_type = "vlan"
        ifaces.append({
            "name": name,
            "operstate": i.get("operstate", "UNKNOWN"),
            "ipv4": ipv4,
            "ipv6": ipv6,
            "mtu": mtu,
            "mac": mac,
            "link_type": link_type,
            "speed_mbps": speed,
            "rx_bytes": rx_bytes,
            "tx_bytes": tx_bytes,
            "vlan_id": vlan_id,
            "vlan_parent": vlan_parent,
        })
    return ifaces


def load():
    try:
        with open(IFACES_FILE) as f:
            return json.load(f)
    except Exception:
        cfg = _default_config()
        save(cfg)
        return cfg


def save(cfg):
    with open(IFACES_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def get_all():
    cfg = load()
    system = _scan_system()
    sys_map = {i["name"]: i for i in system}
    cfg_ifaces = cfg.get("interfaces", {})
    known_vlan_parents = set()
    for name, icfg in cfg_ifaces.items():
        if icfg.get("vlan_parent"):
            known_vlan_parents.add(icfg["vlan_parent"])
    result = []
    for s in system:
        name = s["name"]
        icfg = cfg_ifaces.get(name, {})
        role = icfg.get("role", "unused")
        if s.get("link_type") == "vlan" and role == "unused":
            parent_role = cfg_ifaces.get(s.get("vlan_parent", ""), {}).get("role", "unused")
            if parent_role == "lan":
                role = "lan"
            elif parent_role == "wan":
                role = "wan"
        entry = {**s, "role": role, "label": icfg.get("label", ""), "managed": icfg.get("managed", role != "unused")}
        result.append(entry)
    return result, cfg


def get_wan():
    cfg = load()
    for name, icfg in cfg.get("interfaces", {}).items():
        if icfg.get("role") == "wan":
            return name
    return cfg.get("default_wan", "eth0")


def get_lan():
    cfg = load()
    lan_ifaces = []
    for name, icfg in cfg.get("interfaces", {}).items():
        if icfg.get("role") == "lan":
            lan_ifaces.append(name)
    if lan_ifaces:
        return lan_ifaces[0]
    return cfg.get("default_lan", "eth1")


def get_lan_all():
    cfg = load()
    return [name for name, icfg in cfg.get("interfaces", {}).items() if icfg.get("role") == "lan"]


def get_vpn():
    cfg = load()
    for name, icfg in cfg.get("interfaces", {}).items():
        if icfg.get("role") == "vpn":
            return name
    return cfg.get("default_vpn", "wg0")


def get_by_role(role):
    cfg = load()
    return [name for name, icfg in cfg.get("interfaces", {}).items() if icfg.get("role") == role]


def get_managed_ifaces():
    cfg = load()
    return [name for name, icfg in cfg.get("interfaces", {}).items() if icfg.get("managed", False)]


def get_all_role_iface_map():
    cfg = load()
    result = {}
    for name, icfg in cfg.get("interfaces", {}).items():
        role = icfg.get("role", "unused")
        if role not in result:
            result[role] = []
        result[role].append(name)
    return result


def set_role(iface_name, role, label=None):
    if role not in ROLES:
        return False, f"Unknown role: {role}"
    cfg = load()
    ifaces = cfg.get("interfaces", {})
    if iface_name not in ifaces:
        ifaces[iface_name] = {}
    old_role = ifaces[iface_name].get("role", "unused")
    if role == "wan":
        for name, icfg in ifaces.items():
            if icfg.get("role") == "wan" and name != iface_name:
                icfg["role"] = "unused"
                icfg["managed"] = False
                icfg["label"] = ""
    ifaces[iface_name]["role"] = role
    ifaces[iface_name]["managed"] = role != "unused"
    if label:
        ifaces[iface_name]["label"] = label
    else:
        ifaces[iface_name]["label"] = ROLES[role]["label"] + ": " + iface_name
    cfg["interfaces"] = ifaces
    if role == "wan":
        cfg["default_wan"] = iface_name
    elif role == "lan":
        cfg["default_lan"] = iface_name
    elif role == "vpn":
        cfg["default_vpn"] = iface_name
    save(cfg)
    return True, f"{iface_name} set to {ROLES[role]['label']}"


def create_vlan(parent_iface, vlan_id, ip_cidr, role="lan", label=None):
    try:
        vid = int(vlan_id)
    except ValueError:
        return False, f"Invalid VLAN ID: {vlan_id}"
    if vid < 1 or vid > 4094:
        return False, f"VLAN ID must be 1-4094"
    vlan_name = f"{parent_iface}.{vid}"
    ok, _, err = _run(["ip", "link", "add", "link", parent_iface, "name", vlan_name, "type", "vlan", "id", str(vid)])
    if not ok:
        return False, f"Failed to create VLAN: {err}"
    if ip_cidr:
        ok2, _, err2 = _run(["ip", "addr", "add", ip_cidr, "dev", vlan_name])
        if not ok2:
            _run(["ip", "link", "del", vlan_name])
            return False, f"Failed to assign IP: {err2}"
    ok3, _, _ = _run(["ip", "link", "set", vlan_name, "up"])
    cfg = load()
    ifaces = cfg.get("interfaces", {})
    ifaces[vlan_name] = {
        "role": role,
        "label": label or ROLES.get(role, ROLES["lan"])["label"] + f" VLAN {vid}",
        "vlan_id": vid,
        "vlan_parent": parent_iface,
        "mtu": 1500,
        "enabled": True,
        "managed": True,
    }
    vlans = cfg.get("vlans", {})
    vlans[vlan_name] = {
        "parent": parent_iface,
        "vlan_id": vid,
        "ip_cidr": ip_cidr,
        "role": role,
        "label": label or "",
    }
    cfg["interfaces"] = ifaces
    cfg["vlans"] = vlans
    save(cfg)
    return True, f"VLAN {vid} created on {parent_iface} ({vlan_name})"


def delete_vlan(vlan_name):
    ok, _, err = _run(["ip", "link", "del", vlan_name])
    if not ok:
        return False, f"Failed to delete VLAN: {err}"
    cfg = load()
    ifaces = cfg.get("interfaces", {})
    vlans = cfg.get("vlans", {})
    ifaces.pop(vlan_name, None)
    vlans.pop(vlan_name, None)
    cfg["interfaces"] = ifaces
    cfg["vlans"] = vlans
    save(cfg)
    return True, f"VLAN {vlan_name} deleted"


def get_vlans():
    cfg = load()
    return cfg.get("vlans", {})


def get_iface_select_options():
    all_ifaces, cfg = get_all()
    opts = []
    for i in all_ifaces:
        role = i.get("role", "unused")
        label = i.get("label") or ROLES.get(role, ROLES["unused"])["label"]
        opts.append({"value": i["name"], "label": f"{i['name']} ({label})", "role": role})
    return opts


def persist_vlans():
    cfg = load()
    vlans = cfg.get("vlans", {})
    lines = ["#!/bin/bash"]
    for vname, vcfg in vlans.items():
        parent = vcfg.get("parent", "")
        vid = vcfg.get("vlan_id", 0)
        ip_cidr = vcfg.get("ip_cidr", "")
        if not parent or not vid:
            continue
        if not _IFACE_RE.match(str(parent)) or not _IFACE_RE.match(str(vname)):
            continue
        if not _VID_RE.match(str(vid)):
            continue
        lines.append(f"ip link add link {parent} name {vname} type vlan id {vid} 2>/dev/null")
        if ip_cidr and _IPCIDR_RE.match(str(ip_cidr)):
            lines.append(f"ip addr add {ip_cidr} dev {vname} 2>/dev/null")
        lines.append(f"ip link set {vname} up 2>/dev/null")
    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "vlan-setup.sh")
    with open(script_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    _run(["chmod", "+x", script_path])
    return len(vlans)