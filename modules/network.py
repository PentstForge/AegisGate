#!/usr/bin/env python3
import os
import json
import subprocess
import socket
import re as _re

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
NET_FILE = os.path.join(DATA_DIR, "network.json")

_IP_RE = _re.compile(r'^[0-9a-fA-F:./]{1,43}$')
_IFACE_RE = _re.compile(r'^[a-zA-Z0-9_.\-]{1,15}$')


def _val_route_arg(v, max_len=43):
    if isinstance(v, str) and v and _IP_RE.match(v) and len(v) <= max_len:
        return v
    return None


def _val_iface(v):
    if isinstance(v, str) and _IFACE_RE.match(v):
        return v
    return None


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


def get_interfaces():
    ifaces = []
    ok, out, _ = _run(["ip", "-j", "-br", "addr"])
    if ok and out:
        try:
            for i in json.loads(out):
                name = i.get("ifname", "")
                if name == "lo":
                    continue
                addrs = i.get("addr_info", [])
                ipv4 = ""
                ipv6 = ""
                for a in addrs:
                    if a.get("family") == "inet" and not ipv4:
                        ipv4 = f"{a.get('local', '')}/{a.get('prefixlen', '')}"
                    elif a.get("family") == "inet6" and not ipv6 and not a.get("local", "").startswith("fe80"):
                        ipv6 = f"{a.get('local', '')}/{a.get('prefixlen', '')}"
                mtu = _get_mtu(name)
                mac = _get_mac(name)
                rx_bytes, tx_bytes = _get_if_counters(name)
                operstate = i.get("operstate", "UNKNOWN")
                if operstate == "UNKNOWN":
                    carrier_ok, carrier_out, _ = _run(["cat", f"/sys/class/net/{name}/carrier"])
                    if carrier_ok and carrier_out.strip() == "1":
                        operstate = "UP"
                link_type = _get_link_type(name)
                speed = _get_speed(name)
                ifaces.append({
                    "name": name,
                    "operstate": operstate,
                    "ipv4": ipv4,
                    "ipv6": ipv6,
                    "mtu": mtu,
                    "mac": mac,
                    "rx_bytes": rx_bytes,
                    "tx_bytes": tx_bytes,
                    "link_type": link_type,
                    "speed_mbps": speed
                })
        except Exception:
            pass
    return ifaces


def _get_mtu(iface):
    ok, out, _ = _run(["cat", f"/sys/class/net/{iface}/mtu"])
    if ok:
        try:
            return int(out.strip())
        except ValueError:
            pass
    return 1500


def _get_mac(iface):
    ok, out, _ = _run(["cat", f"/sys/class/net/{iface}/address"])
    return out.strip() if ok else ""


def _get_if_counters(iface):
    rx = 0
    tx = 0
    ok, out, _ = _run(["cat", f"/sys/class/net/{iface}/statistics/rx_bytes"])
    if ok:
        try:
            rx = int(out.strip())
        except ValueError:
            pass
    ok, out, _ = _run(["cat", f"/sys/class/net/{iface}/statistics/tx_bytes"])
    if ok:
        try:
            tx = int(out.strip())
        except ValueError:
            pass
    return rx, tx


def _get_link_type(iface):
    ok, out, _ = _run(["cat", f"/sys/class/net/{iface}/type"])
    if ok:
        t = out.strip()
        if t == "1":
            return "ethernet"
        if t == "772":
            return "loopback"
    if iface.startswith("wg"):
        return "wireguard"
    if iface.startswith("wlan") or iface.startswith("wifi"):
        return "wifi"
    if iface.startswith("br"):
        return "bridge"
    return "unknown"


def _get_speed(iface):
    ok, out, _ = _run(["cat", f"/sys/class/net/{iface}/speed"])
    if ok:
        try:
            return int(out.strip())
        except ValueError:
            pass
    return 0


def get_routes():
    routes = []
    ok, out, _ = _run(["ip", "-j", "route", "show"])
    if ok and out:
        try:
            for r in json.loads(out):
                routes.append({
                    "dst": r.get("dst", ""),
                    "dev": r.get("dev", ""),
                    "via": r.get("gateway", ""),
                    "metric": r.get("priority", 0),
                    "proto": r.get("protocol", ""),
                    "scope": r.get("scope", ""),
                    "prefsrc": r.get("prefsrc", ""),
                    "table": r.get("table", "main")
                })
        except Exception:
            pass
    return routes


def get_dns():
    dns = {"servers": [], "search": []}
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                line = line.strip()
                if line.startswith("nameserver"):
                    dns["servers"].append(line.split()[1])
                elif line.startswith("search"):
                    dns["search"] = line.split()[1:]
    except Exception:
        pass
    return dns


def add_route(dst, via, dev="", metric=0):
    dst = _val_route_arg(dst)
    if not dst:
        return False, "Invalid destination"
    args = ["ip", "route", "add", dst]
    via_val = _val_route_arg(via) if via else None
    if via_val:
        args += ["via", via_val]
    dev_val = _val_iface(dev) if dev else None
    if dev_val:
        args += ["dev", dev_val]
    try:
        metric = int(metric)
        if metric > 0:
            args += ["metric", str(metric)]
    except (ValueError, TypeError):
        pass
    ok, _, err = _run(args)
    if ok:
        return True, f"Route added: {dst} via {via}"
    return False, f"Failed to add route: {err}"


def delete_route(dst, via="", dev=""):
    dst = _val_route_arg(dst)
    if not dst:
        return False, "Invalid destination"
    args = ["ip", "route", "del", dst]
    via_val = _val_route_arg(via) if via else None
    if via_val:
        args += ["via", via_val]
    dev_val = _val_iface(dev) if dev else None
    if dev_val:
        args += ["dev", dev_val]
    ok, _, err = _run(args)
    if ok:
        return True, f"Route deleted: {dst}"
    return False, f"Failed to delete route: {err}"


def set_interface(iface, action):
    if action == "up":
        ok, _, err = _run(["ip", "link", "set", iface, "up"])
    elif action == "down":
        ok, _, err = _run(["ip", "link", "set", iface, "down"])
    else:
        return False, f"Unknown action: {action}"
    if ok:
        return True, f"Interface {iface} {action}"
    return False, f"Failed: {err}"


def set_ip_address(iface, ip_cidr, action="add"):
    ok, _, err = _run(["ip", "addr", action, ip_cidr, "dev", iface])
    if ok:
        return True, f"Address {ip_cidr} {action}ed on {iface}"
    return False, f"Failed: {err}"


def set_mtu(iface, mtu):
    ok, _, err = _run(["ip", "link", "set", iface, "mtu", str(mtu)])
    if ok:
        return True, f"MTU set to {mtu} on {iface}"
    return False, f"Failed: {err}"


def get_frr_status():
    status = {"installed": False, "bgp": None, "ospf": None, "rip": None,
               "ospf6": None, "ripng": None, "version": "",
               "routes_output": "", "bgp_summary": ""}
    vtysh_path = None
    for p in ["/usr/bin/vtysh", "/usr/sbin/vtysh", "/usr/lib/frr/vtysh", "/usr/local/bin/vtysh"]:
        if os.path.exists(p):
            vtysh_path = p
            break
    if not vtysh_path:
        ok, _, _ = _run_sh("command -v vtysh")
        if ok:
            vtysh_path = "vtysh"
    if not vtysh_path:
        ok, _, _ = _run(["dpkg", "-l", "frr"])
        if not ok:
            return status
        vtysh_path = "vtysh"
    status["installed"] = True
    ok, out, _ = _run([vtysh_path, "-c", "show version"])
    if ok:
        status["version"] = out.split("\n")[0] if out else ""
    daemon_map = {
        "bgpd": "bgp", "ospfd": "ospf", "ripd": "rip",
        "ospf6d": "ospf6", "ripngd": "ripng",
        "staticd": "static", "bfdd": "bfd", "pimd": "pim",
        "ldpd": "ldp", "eigrpd": "eigrp"
    }
    for daemon, key in daemon_map.items():
        ok, out, _ = _run_sh(f"test -f /var/run/frr/{daemon}.pid && echo active || echo inactive")
        if ok and out.strip() == "active":
            status[key] = True
        else:
            ok2, out2, _ = _run_sh(f"systemctl is-active frr 2>/dev/null && grep -q '^{daemon}d\\b\\|= {daemon}d\\b' /etc/frr/daemons && echo active || echo inactive")
            if ok2 and "active" in out2:
                status[key] = True
            else:
                status[key] = False
    ok, out, _ = _run([vtysh_path, "-c", "show ip route"])
    if ok:
        status["routes_output"] = out
    ok, out, _ = _run([vtysh_path, "-c", "show ip bgp summary"])
    if ok:
        status["bgp_summary"] = out
    return status


def get_conntrack_stats():
    stats = {"count": 0, "max": 0, "percentage": 0}
    try:
        with open("/proc/sys/net/netfilter/nf_conntrack_count") as f:
            stats["count"] = int(f.read().strip())
        with open("/proc/sys/net/netfilter/nf_conntrack_max") as f:
            stats["max"] = int(f.read().strip())
        if stats["max"] > 0:
            stats["percentage"] = round(stats["count"] / stats["max"] * 100, 1)
    except Exception:
        pass
    return stats


def get_network_state():
    return {
        "interfaces": get_interfaces(),
        "routes": get_routes(),
        "dns": get_dns(),
        "conntrack": get_conntrack_stats(),
        "frr": get_frr_status(),
        "hostname": socket.gethostname(),
        "default_gw": _get_default_gw()
    }


def _get_default_gw():
    ok, out, _ = _run(["ip", "route", "show", "default"])
    if ok and out:
        parts = out.strip().split()
        for i, p in enumerate(parts):
            if p == "via" and i + 1 < len(parts):
                return parts[i + 1]
    return ""