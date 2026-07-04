import json
import os
import subprocess
import ipaddress
import base64
import secrets
import time
import re
from datetime import datetime, timedelta
from collections import defaultdict

WG_DIR = "/etc/wireguard"
WG_CONFIG_DIR = "/opt/nft-dashboard/data/wireguard"
WG_STATE_FILE = os.path.join(WG_CONFIG_DIR, "state.json")
WG_STATS_FILE = os.path.join(WG_CONFIG_DIR, "stats_history.json")
WG = "/usr/bin/wg"
WG_QUICK = "/usr/bin/wg-quick"

DEFAULT_SERVER_CONFIG = {
    "interface": "wg0",
    "address": "10.0.0.1/24",
    "listen_port": 51820,
    "dns": "1.1.1.1, 8.8.8.8",
    "mtu": 1420,
    "post_up": "",
    "post_down": "",
}


def _get_wan():
    try:
        from modules.ifaces import get_wan
        return get_wan()
    except Exception:
        return "eth0"


def _get_vpn_ifaces():
    try:
        from modules.ifaces import get_by_role, get_vpn
        ifaces = get_by_role("vpn")
        if ifaces:
            return ifaces
        vpn = get_vpn()
        return [vpn] if vpn else ["wg0"]
    except Exception:
        return ["wg0"]


def _default_postup():
    wan = _get_wan()
    return f"iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o {wan} -j MASQUERADE"


def _default_postdown():
    wan = _get_wan()
    return f"iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o {wan} -j MASQUERADE"

PRESET_NETWORKS = None

def _get_preset_networks():
    global PRESET_NETWORKS
    if PRESET_NETWORKS is None:
        PRESET_NETWORKS = {
            "Internet (Full Access)": "0.0.0.0/0",
            "LAN": "192.168.1.0/24",
            "Gateway / VPN Network": "10.0.0.0/24",
            "DMZ Server": _get_dmz_ip() + "/32",
            "DNS Servers": "1.1.1.1/32,8.8.8.8/32",
        }
    return PRESET_NETWORKS


def detect_local_networks():
    networks = {}
    try:
        r = subprocess.run(["ip", "-4", "route", "show"], capture_output=True, text=True, timeout=5)
        seen = set()
        for line in r.stdout.splitlines():
            parts = line.split()
            if not parts or parts[0] in ("default",):
                continue
            net_str = parts[0]
            try:
                n = ipaddress.ip_network(net_str, strict=False)
                if n.is_loopback or n.is_multicast or n.is_link_local:
                    continue
                if str(n) in seen:
                    continue
                seen.add(str(n))
                if str(n).startswith("10.0.0."):
                    label = f"VPN Network ({n})"
                elif str(n).startswith("10."):
                    label = f"Private 10.x ({n})"
                elif str(n).startswith("172."):
                    label = f"LAN ({n})"
                elif str(n).startswith("192.168."):
                    label = f"WiFi/LAN ({n})"
                elif str(n).startswith("31."):
                    label = f"WAN ({n})"
                else:
                    label = f"Network ({n})"
                networks[label] = str(n)
            except ValueError:
                continue
    except Exception:
        pass
    return networks


def get_available_networks():
    static = dict(PRESET_NETWORKS)
    detected = detect_local_networks()
    for label, net in detected.items():
        if net not in static.values():
            static[label] = net
    return static


def _ensure_dirs():
    os.makedirs(WG_CONFIG_DIR, exist_ok=True)
    os.makedirs(WG_DIR, exist_ok=True)


def _load_state():
    _ensure_dirs()
    try:
        with open(WG_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"server": None, "peers": {}}


def _save_state(state):
    _ensure_dirs()
    with open(WG_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    try:
        os.chmod(WG_STATE_FILE, 0o600)
    except OSError:
        pass


def _gen_private_key():
    r = subprocess.run(["wg", "genkey"], capture_output=True, text=True)
    return r.stdout.strip()


def _gen_public_key(private_key):
    r = subprocess.run(["wg", "pubkey"], input=private_key, capture_output=True, text=True)
    return r.stdout.strip()


def _gen_psk():
    r = subprocess.run(["wg", "genpsk"], capture_output=True, text=True)
    return r.stdout.strip()


def _get_wan_interface():
    return _get_wan()


def _get_wan_ip():
    try:
        with open("/opt/nft-dashboard/data/config.json") as f:
            cfg = json.load(f)
            return cfg.get("wan_ip", "")
    except Exception:
        return ""


def get_server_status():
    try:
        r = subprocess.run([WG, "show", "wg0"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return {"running": False, "interface": "wg0"}
        info = {"running": True, "interface": "wg0", "peers": []}
        current_peer = None
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("interface:"):
                info["interface"] = line.split(":", 1)[1].strip()
            elif line.startswith("listening port:"):
                info["listen_port"] = int(line.split(":", 1)[1].strip())
            elif line.startswith("public key:"):
                if current_peer is None:
                    info["public_key"] = line.split(":", 1)[1].strip()
                else:
                    current_peer["public_key"] = line.split(":", 1)[1].strip()
            elif line.startswith("peer:"):
                current_peer = {"public_key": line.split(":", 1)[1].strip(), "latest_handshake": None,
                                 "transfer_rx": 0, "transfer_tx": 0, "endpoint": None,
                                 "allowed_ips": [], "persistent_keepalive": None}
                info["peers"].append(current_peer)
            elif line.startswith("endpoint:"):
                if current_peer:
                    current_peer["endpoint"] = line.split(":", 1)[1].strip()
            elif line.startswith("allowed ips:"):
                if current_peer:
                    current_peer["allowed_ips"] = [x.strip() for x in line.split(":", 1)[1].strip().split(",")]
            elif line.startswith("latest handshake:"):
                if current_peer:
                    current_peer["latest_handshake"] = line.split(":", 1)[1].strip()
            elif line.startswith("transfer:"):
                if current_peer:
                    val = line.split(":", 1)[1].strip()
                    m = re.search(r'([\d.]+)\s*(?:B|KiB|MiB|GiB)?\s*received.*?([\d.]+)\s*(?:B|KiB|MiB|GiB)?\s*sent', val)
                    if m:
                        rx_val = float(m.group(1))
                        tx_val = float(m.group(2))
                        rx_unit = re.search(r'([\d.]+)\s*(B|KiB|MiB|GiB)\s*received', val)
                        tx_unit = re.search(r'([\d.]+)\s*(B|KiB|MiB|GiB)\s*sent', val)
                        if rx_unit:
                            mult = {'B': 1, 'KiB': 1024, 'MiB': 1024*1024, 'GiB': 1024*1024*1024}
                            rx_val = float(rx_unit.group(1)) * mult.get(rx_unit.group(2), 1)
                        if tx_unit:
                            mult = {'B': 1, 'KiB': 1024, 'MiB': 1024*1024, 'GiB': 1024*1024*1024}
                            tx_val = float(tx_unit.group(1)) * mult.get(tx_unit.group(2), 1)
                        current_peer["transfer_rx"] = int(rx_val)
                        current_peer["transfer_tx"] = int(tx_val)
                    else:
                        parts = val.split(",")
                        for part in parts:
                            part = part.strip()
                            nums = re.findall(r'[\d.]+', part)
                            if nums and "received" in part:
                                current_peer["transfer_rx"] = int(float(nums[0]))
                            elif nums and "sent" in part:
                                current_peer["transfer_tx"] = int(float(nums[0]))
            elif line.startswith("persistent keepalive:"):
                if current_peer:
                    current_peer["persistent_keepalive"] = line.split(":", 1)[1].strip()
        return info
    except Exception:
        return {"running": False, "interface": "wg0"}


def get_server_config():
    state = _load_state()
    return state.get("server") or DEFAULT_SERVER_CONFIG.copy()


def _open_wg_port(port):
    try:
        port_int = int(port)
        result = subprocess.run([NFT, "-a", "list", "chain", "inet", "filter", "input"],
                               capture_output=True, text=True, timeout=5)
        if f"udp dport {port_int} accept" in result.stdout:
            return
        subprocess.run([NFT, "add", "rule", "inet", "filter", "input",
                        "udp", "dport", str(port_int), "accept"],
                      capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        with open("/etc/nftables.conf") as f:
            content = f.read()
        if f"udp dport {port_int} accept" not in content:
            content = content.replace(
                "udp dport { 546, 547 } accept",
                f"udp dport {{ 546, 547 }} accept\n\t\tudp dport {port_int} accept"
            )
            with open("/etc/nftables.conf", "w") as f:
                f.write(content)
    except Exception:
        pass


def _close_wg_port(port):
    try:
        port_int = int(port)
        result = subprocess.run([NFT, "-a", "list", "chain", "inet", "filter", "input"],
                               capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if f"udp dport {port_int} accept" in line and "546" not in line and "547" not in line:
                handle = line.strip().split("handle")[-1].strip()
                if handle:
                    subprocess.run([NFT, "delete", "rule", "inet", "filter", "input", "handle", handle],
                                  capture_output=True, timeout=5)
        with open("/etc/nftables.conf") as f:
            lines = f.readlines()
        lines = [l for l in lines if f"udp dport {port_int} accept" not in l]
        with open("/etc/nftables.conf", "w") as f:
            f.writelines(lines)
    except Exception:
        pass


def _add_wg_to_flowtable(iface="wg0"):
    try:
        r = subprocess.run([NFT, "list", "flowtable", "inet", "filter", "f"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0 or iface in r.stdout:
            return
        devices_line = ""
        for line in r.stdout.splitlines():
            if "devices" in line:
                devices_line = line.strip()
                break
        if not devices_line:
            return
        m = re.search(r"devices\s*=\s*\{([^}]+)\}", devices_line)
        if not m:
            return
        current = [d.strip().rstrip(",") for d in m.group(1).split(",") if d.strip()]
        if iface not in current:
            current.append(iface)
        new_line = f"\t\tdevices = {{ {', '.join(current)} }}"
        subprocess.run([NFT, "delete", "flowtable", "inet", "filter", "f"],
                       capture_output=True, timeout=5)
        subprocess.run([NFT, "add", "flowtable", "inet", "filter", "f",
                        "hook", "ingress", "priority", "filter",
                        "devices", "=", "{", *current, "}"],
                       capture_output=True, timeout=5)
        from modules.firewall import _full_config_sync
        _full_config_sync()
    except Exception:
        pass


def _remove_wg_from_flowtable(iface="wg0"):
    try:
        r = subprocess.run([NFT, "list", "flowtable", "inet", "filter", "f"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0 or iface not in r.stdout:
            return
        devices_line = ""
        for line in r.stdout.splitlines():
            if "devices" in line:
                devices_line = line.strip()
                break
        if not devices_line:
            return
        m = re.search(r"devices\s*=\s*\{([^}]+)\}", devices_line)
        if not m:
            return
        current = [d.strip().rstrip(",") for d in m.group(1).split(",") if d.strip()]
        new_devices = [d for d in current if d != iface]
        if not new_devices:
            return
        subprocess.run([NFT, "delete", "flowtable", "inet", "filter", "f"],
                       capture_output=True, timeout=5)
        subprocess.run([NFT, "add", "flowtable", "inet", "filter", "f",
                        "hook", "ingress", "priority", "filter",
                        "devices", "=", "{", *new_devices, "}"],
                       capture_output=True, timeout=5)
        from modules.firewall import _full_config_sync
        _full_config_sync()
    except Exception:
        pass


def init_server(address=None, listen_port=None, dns=None, mtu=None):
    state = _load_state()
    if state.get("server"):
        return False, "Server already initialized. Use update_server() to change settings."
    _ensure_dirs()
    private_key = _gen_private_key()
    public_key = _gen_public_key(private_key)
    config = DEFAULT_SERVER_CONFIG.copy()
    if address:
        config["address"] = address
    if listen_port:
        config["listen_port"] = int(listen_port)
    if dns:
        config["dns"] = dns
    if mtu:
        config["mtu"] = int(mtu)
    config["private_key"] = private_key
    config["public_key"] = public_key
    config["created_at"] = datetime.now().isoformat()
    config["running"] = False
    state["server"] = config
    _save_state(state)
    _write_server_config(config)
    _open_wg_port(config["listen_port"])
    return True, "WireGuard server initialized"


def update_server(address=None, listen_port=None, dns=None, mtu=None):
    state = _load_state()
    if not state.get("server"):
        return False, "Server not initialized"
    config = state["server"]
    old_port = config.get("listen_port", 51820)
    if address:
        config["address"] = address
    if listen_port:
        new_port = int(listen_port)
        if new_port != old_port:
            _close_wg_port(old_port)
        config["listen_port"] = new_port
    if dns:
        config["dns"] = dns
    if mtu:
        config["mtu"] = int(mtu)
    _write_server_config(config)
    state["server"] = config
    _save_state(state)
    if get_server_status()["running"]:
        subprocess.run([WG_QUICK, "down", config["interface"]], capture_output=True, timeout=10)
        subprocess.run([WG_QUICK, "up", config["interface"]], capture_output=True, timeout=10)
        _open_wg_port(config.get("listen_port", 51820))
    return True, "Server config updated"


def _write_server_config(config):
    iface = config["interface"]
    cfg_path = os.path.join(WG_DIR, f"{iface}.conf")
    lines = [
        "[Interface]",
        f"PrivateKey = {config['private_key']}",
        f"Address = {config['address']}",
        f"ListenPort = {config['listen_port']}",
    ]
    if config.get("mtu"):
        lines.append(f"MTU = {config['mtu']}")
    wan_if = _get_wan_interface()
    post_up = config.get("post_up", "") or _default_postup()
    post_down = config.get("post_down", "") or _default_postdown()
    if "eth0" in post_up:
        post_up = post_up.replace("eth0", wan_if)
    if "eth0" in post_down:
        post_down = post_down.replace("eth0", wan_if)
    if post_up:
        lines.append(f"PostUp = {post_up}")
    if post_down:
        lines.append(f"PostDown = {post_down}")
    state = _load_state()
    for peer_id, peer in sorted(state.get("peers", {}).items(), key=lambda x: x[1].get("name", "")):
        if not peer.get("enabled", True):
            lines.append("")
            lines.append(f"# {peer.get('name', peer_id)} [DISABLED]")
            lines.append(f"# PublicKey = {peer['public_key']}")
            lines.append(f"# PresharedKey = {peer['preshared_key']}")
            lines.append(f"# AllowedIPs = {peer['allowed_ips']}")
            continue
        lines.append("")
        lines.append(f"[Peer]")
        lines.append(f"# {peer.get('name', peer_id)}")
        lines.append(f"PublicKey = {peer['public_key']}")
        if peer.get("preshared_key"):
            lines.append(f"PresharedKey = {peer['preshared_key']}")
        lines.append(f"AllowedIPs = {peer['address']}/32")
        if peer.get("persistent_keepalive"):
            lines.append(f"PersistentKeepalive = {peer['persistent_keepalive']}")
    with open(cfg_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(cfg_path, 0o600)


def start_server():
    state = _load_state()
    if not state.get("server"):
        return False, "Server not initialized"
    iface = state["server"]["interface"]
    port = state["server"].get("listen_port", 51820)
    r = subprocess.run([WG_QUICK, "up", iface], capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        return False, f"Failed to start: {r.stderr.strip()}"
    state["server"]["running"] = True
    _save_state(state)
    _enable_forwarding()
    _open_wg_port(port)
    _sync_all_peers()
    _apply_all_firewall_rules()
    _add_wg_to_flowtable(iface)
    return True, "WireGuard server started"


def stop_server():
    state = _load_state()
    if not state.get("server"):
        return False, "Server not initialized"
    iface = state["server"]["interface"]
    port = state["server"].get("listen_port", 51820)
    r = subprocess.run([WG_QUICK, "down", iface], capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        return False, f"Failed to stop: {r.stderr.strip()}"
    state["server"]["running"] = False
    _save_state(state)
    _close_wg_port(port)
    _remove_wg_from_flowtable(iface)
    return True, "WireGuard server stopped"


def restart_server():
    stop_server()
    time.sleep(1)
    return start_server()


def _enable_forwarding():
    try:
        with open("/proc/sys/net/ipv4/ip_forward") as f:
            if f.read().strip() == "0":
                with open("/proc/sys/net/ipv4/ip_forward", "w") as fw:
                    fw.write("1\n")
                with open("/etc/sysctl.d/99-wireguard.conf", "w") as fw:
                    fw.write("net.ipv4.ip_forward = 1\n")
    except Exception:
        pass


def _sync_all_peers():
    state = _load_state()
    if not state.get("server"):
        return
    status = get_server_status()
    if not status.get("running"):
        return
    iface = state["server"]["interface"]
    for peer_id, peer in state.get("peers", {}).items():
        if peer.get("enabled", True):
            _sync_peer_to_runtime(peer)
        else:
            subprocess.run([WG, "set", iface, "peer", peer["public_key"], "remove"],
                          capture_output=True, timeout=10)


def _apply_all_firewall_rules():
    state = _load_state()
    for peer_id, peer in list(state.get("peers", {}).items()):
        peer_ip = peer.get("address")
        if peer_ip:
            old_chain = f"wg_peer_{peer_ip.replace('.', '_')}"
            try:
                result = subprocess.run([NFT, "-a", "list", "chain", "inet", "filter", "wg_acl"],
                                       capture_output=True, text=True, timeout=5)
                for line in result.stdout.splitlines():
                    if peer_ip in line and "jump" in line:
                        handle = line.strip().split("handle")[-1].strip()
                        if handle:
                            subprocess.run([NFT, "delete", "rule", "inet", "filter", "wg_acl", "handle", handle],
                                          capture_output=True, timeout=5)
            except Exception:
                pass
            try:
                subprocess.run([NFT, "flush", "chain", "inet", "filter", old_chain],
                              capture_output=True, timeout=5)
            except Exception:
                pass
            try:
                subprocess.run([NFT, "delete", "chain", "inet", "filter", old_chain],
                              capture_output=True, timeout=5)
            except Exception:
                pass
    try:
        subprocess.run([NFT, "add", "chain", "inet", "filter", "wg_acl"],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        subprocess.run([NFT, "flush", "chain", "inet", "filter", "wg_acl"],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    for peer_id in state.get("peers", {}):
        _apply_firewall_rules(peer_id)
    _ensure_wg_acl_jump()


def _ensure_wg_acl_jump():
    vpn_ifaces = _get_vpn_ifaces()
    if not vpn_ifaces:
        return
    try:
        result = subprocess.run([NFT, "-a", "list", "chain", "inet", "filter", "forward"],
                               capture_output=True, text=True, timeout=5)
        for vpn_if in vpn_ifaces:
            jump_rule = f'iifname "{vpn_if}" jump wg_acl'
            if jump_rule in result.stdout:
                continue
            insert_before = None
            for line in result.stdout.splitlines():
                if "handle" in line:
                    if 'iifname "' + vpn_if + '" accept' in line or "lan_trusted" in line or "@allowlist" in line:
                        parts = line.strip().split("handle")
                        h = parts[-1].strip()
                        if h.isdigit():
                            insert_before = h
                            break
            if insert_before:
                subprocess.run([NFT, "insert", "rule", "inet", "filter", "forward",
                                "position", insert_before, "iifname", vpn_if, "jump", "wg_acl"],
                               capture_output=True, timeout=5)
            else:
                subprocess.run([NFT, "add", "rule", "inet", "filter", "forward",
                                "iifname", vpn_if, "jump", "wg_acl"],
                               capture_output=True, timeout=5)
    except Exception:
        pass


def _ensure_wg_acl_chain():
    try:
        subprocess.run([NFT, "add", "chain", "inet", "filter", "wg_acl"],
                      capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        subprocess.run([NFT, "flush", "chain", "inet", "filter", "wg_acl"],
                      capture_output=True, timeout=5)
    except Exception:
        pass
    for peer_id, peer in _load_state().get("peers", {}).items():
        peer_ip = peer.get("address")
        if peer_ip:
            sub_chain = f"wg_peer_{peer_ip.replace('.', '_')}"
            subprocess.run([NFT, "add", "rule", "inet", "filter", "wg_acl",
                            "ip", "saddr", peer_ip, "jump", sub_chain],
                          capture_output=True, timeout=5)


def _apply_firewall_rules(peer_id):
    state = _load_state()
    if peer_id not in state.get("peers", {}):
        return
    peer = state["peers"][peer_id]
    peer_ip = peer.get("address")
    if not peer_ip:
        return
    acl = peer.get("acl", {})
    sub_chain = f"wg_peer_{peer_ip.replace('.', '_')}"
    try:
        result = subprocess.run([NFT, "-a", "list", "chain", "inet", "filter", "wg_acl"],
                               capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if peer_ip in line and "jump" in line:
                handle = line.strip().split("handle")[-1].strip()
                if handle:
                    subprocess.run([NFT, "delete", "rule", "inet", "filter", "wg_acl", "handle", handle],
                                  capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        subprocess.run([NFT, "flush", "chain", "inet", "filter", sub_chain],
                      capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        subprocess.run([NFT, "delete", "chain", "inet", "filter", sub_chain],
                      capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        subprocess.run([NFT, "add", "chain", "inet", "filter", sub_chain],
                      capture_output=True, timeout=5)
    except Exception:
        pass

    if not peer.get("enabled", True):
        subprocess.run([NFT, "add", "rule", "inet", "filter", sub_chain,
                        "ip", "saddr", peer_ip, "log", "prefix",
                        f'"WG_DENY_{peer_ip}: "', "drop"],
                      capture_output=True, timeout=5)
    elif acl.get("internet"):
        subprocess.run([NFT, "add", "rule", "inet", "filter", sub_chain,
                        "ip", "saddr", peer_ip, "accept"],
                      capture_output=True, timeout=5)
    else:
        server = state.get("server", {})
        server_ip = server.get("address", "10.0.0.1/24").split("/")[0]
        allowed_dest = [f"{server_ip}/32"]
        if acl.get("lan"):
            local_nets = _get_local_networks()
            for net in local_nets:
                if net not in allowed_dest:
                    allowed_dest.append(net)
            if "10.0.0.0/24" not in allowed_dest:
                allowed_dest.append("10.0.0.0/24")
        if acl.get("dmz"):
            allowed_dest.append(f"{_get_dmz_ip()}/32")
        for cn in acl.get("custom_networks", []):
            try:
                ipaddress.ip_network(cn, strict=False)
                if cn not in allowed_dest:
                    allowed_dest.append(cn)
            except ValueError:
                pass
        allowed_dest = list(dict.fromkeys(allowed_dest))
        for dest in allowed_dest:
            subprocess.run([NFT, "add", "rule", "inet", "filter", sub_chain,
                            "ip", "saddr", peer_ip, "ip", "daddr", dest, "accept"],
                          capture_output=True, timeout=5)
        subprocess.run([NFT, "add", "rule", "inet", "filter", sub_chain,
                        "ip", "saddr", peer_ip, "udp", "dport", "53", "accept"],
                      capture_output=True, timeout=5)
        subprocess.run([NFT, "add", "rule", "inet", "filter", sub_chain,
                        "ip", "saddr", peer_ip, "tcp", "dport", "53", "accept"],
                      capture_output=True, timeout=5)
        subprocess.run([NFT, "add", "rule", "inet", "filter", sub_chain,
                        "ip", "saddr", peer_ip, "ct", "state", "established,related", "accept"],
                      capture_output=True, timeout=5)
        subprocess.run([NFT, "add", "rule", "inet", "filter", sub_chain,
                        "ip", "saddr", peer_ip, "log", "prefix",
                        f'"WG_ACL_DENY_{peer_ip}: "', "drop"],
                      capture_output=True, timeout=5)

    try:
        jump_exists = False
        result = subprocess.run([NFT, "-a", "list", "chain", "inet", "filter", "wg_acl"],
                               capture_output=True, text=True, timeout=5)
        if peer_ip in result.stdout:
            jump_exists = True
        if not jump_exists:
            subprocess.run([NFT, "add", "rule", "inet", "filter", "wg_acl",
                            "ip", "saddr", peer_ip, "jump", sub_chain],
                          capture_output=True, timeout=5)
    except Exception:
        pass


def _get_dmz_ip():
    try:
        with open("/opt/nft-dashboard/data/config.json") as f:
            cfg = json.load(f)
        return cfg.get("lan_ip", "192.168.1.1")
    except Exception:
        return "192.168.1.1"


def _get_local_networks():
    nets = []
    try:
        r = subprocess.run(["ip", "-4", "route", "show"], capture_output=True, text=True, timeout=5)
        seen = set()
        for line in r.stdout.splitlines():
            parts = line.split()
            if not parts or parts[0] in ("default",):
                continue
            net_str = parts[0]
            try:
                n = ipaddress.ip_network(net_str, strict=False)
                if n.is_loopback or n.is_multicast or n.is_link_local:
                    continue
                if str(n) in seen:
                    continue
                seen.add(str(n))
                if str(n).startswith("10.0.0."):
                    continue
                if str(n).startswith("31."):
                    continue
                nets.append(str(n))
            except ValueError:
                continue
    except Exception:
        nets = ["192.168.1.0/24"]
    return list(dict.fromkeys(nets))


def _detect_events():
    state = _load_state()
    if not state.get("server"):
        return
    status = get_server_status()
    if not status.get("running"):
        return
    runtime_peers = {p.get("public_key"): p for p in status.get("peers", [])}
    events_file = os.path.join(WG_CONFIG_DIR, "events.json")
    prev_state = {}
    try:
        with open(events_file) as f:
            for e in json.load(f):
                if e.get("peer_id") and e.get("online") is not None:
                    prev_state[e["peer_id"]] = e
    except Exception:
        pass
    new_events = []
    now = time.time()
    for peer_id, peer in state.get("peers", {}).items():
        rp = runtime_peers.get(peer.get("public_key"))
        is_online = rp is not None and rp.get("latest_handshake") is not None
        was_online = prev_state.get(peer_id, {}).get("online", False)
        if is_online and not was_online:
            new_events.append({
                "ts": now,
                "ts_formatted": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "peer_id": peer_id,
                "name": peer.get("name", peer_id),
                "type": "connected",
                "endpoint": rp.get("endpoint") if rp else None,
                "online": True,
            })
        elif not is_online and was_online:
            new_events.append({
                "ts": now,
                "ts_formatted": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "peer_id": peer_id,
                "name": peer.get("name", peer_id),
                "type": "disconnected",
                "endpoint": None,
                "online": False,
            })
    all_events = []
    try:
        with open(events_file) as f:
            all_events = json.load(f)
    except Exception:
        pass
    all_events.extend(new_events)
    for peer_id, peer in state.get("peers", {}).items():
        rp = runtime_peers.get(peer.get("public_key"))
        is_online = rp is not None and rp.get("latest_handshake") is not None
        endpoint = rp.get("endpoint") if rp else None
        handshake = rp.get("latest_handshake") if rp else None
        entry = {"peer_id": peer_id, "online": is_online, "endpoint": endpoint, "handshake": handshake, "ts": now}
        all_events = [e for e in all_events if not (e.get("peer_id") == peer_id and e.get("type") is None)]
        all_events.append(entry)
    cutoff = now - 86400 * 7
    all_events = [e for e in all_events if e.get("ts", 0) > cutoff]
    all_events.sort(key=lambda x: x.get("ts", 0), reverse=True)
    with open(events_file, "w") as f:
        json.dump(all_events, f, indent=2)


def get_events(limit=50):
    events_file = os.path.join(WG_CONFIG_DIR, "events.json")
    try:
        with open(events_file) as f:
            events = json.load(f)
    except Exception:
        events = []
    events = [e for e in events if e.get("type")]
    events.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return events[:limit]


def _aip_in_lan(aip):
    try:
        net = ipaddress.ip_network(aip, strict=False)
        return net == ipaddress.ip_network("192.168.1.0/24") or net == ipaddress.ip_network("10.0.0.0/24")
    except ValueError:
        return False


def _aip_is_dmz(aip):
    try:
        return aip.split("/")[0] == _get_dmz_ip()
    except Exception:
        return False


def add_peer(name, allowed_ips=None, persistent_keepalive=25, notes=""):
    state = _load_state()
    if not state.get("server"):
        return None, "Server not initialized"
    private_key = _gen_private_key()
    public_key = _gen_public_key(private_key)
    psk = _gen_psk()
    server_addr = state["server"]["address"]
    network = ipaddress.ip_network(server_addr, strict=False)
    used_ips = {str(network.network_address)}
    used_ips.add(server_addr.split("/")[0])
    for peer in state["peers"].values():
        pa = peer.get("address", "")
        if pa:
            used_ips.add(pa)
        for aip in peer.get("allowed_ips", "").split(","):
            aip = aip.strip().split("/")[0]
            if aip:
                used_ips.add(aip)
    host_bits = network.max_prefixlen - network.prefixlen
    peer_ip = None
    for i in range(2, (1 << host_bits) - 1):
        candidate = str(network.network_address + i)
        if candidate not in used_ips:
            peer_ip = candidate
            break
    if not peer_ip:
        return None, "No available IP addresses in server subnet"
    if not allowed_ips:
        allowed_ips = "0.0.0.0/0"
    peer_id = secrets.token_hex(4)
    peer = {
        "id": peer_id,
        "name": name or f"peer-{peer_id}",
        "private_key": private_key,
        "public_key": public_key,
        "preshared_key": psk,
        "address": peer_ip,
        "allowed_ips": allowed_ips,
        "persistent_keepalive": str(persistent_keepalive) if persistent_keepalive else None,
        "enabled": True,
        "created_at": datetime.now().isoformat(),
        "notes": notes or "",
        "acl": {
            "internet": any(aip.strip() == "0.0.0.0/0" or aip.strip() == "::/0" for aip in allowed_ips.split(",")),
            "lan": any(_aip_in_lan(aip.strip()) for aip in allowed_ips.split(",")),
            "dmz": any(_aip_is_dmz(aip.strip()) for aip in allowed_ips.split(",")),
            "custom_networks": [],
        },
    }
    state["peers"][peer_id] = peer
    _save_state(state)
    _write_server_config(state["server"])
    if get_server_status()["running"]:
        _sync_peer_to_runtime(peer)
        _apply_firewall_rules(peer_id)
    return peer, "Peer added"


def update_peer(peer_id, name=None, allowed_ips=None, notes=None, persistent_keepalive=None):
    state = _load_state()
    if peer_id not in state.get("peers", {}):
        return False, "Peer not found"
    peer = state["peers"][peer_id]
    if name is not None:
        peer["name"] = name
    if allowed_ips is not None:
        peer["allowed_ips"] = allowed_ips
        peer["acl"] = _calc_acl(allowed_ips)
    if notes is not None:
        peer["notes"] = notes
    if persistent_keepalive is not None:
        peer["persistent_keepalive"] = str(persistent_keepalive) if persistent_keepalive else None
    _save_state(state)
    _write_server_config(state["server"])
    if get_server_status()["running"]:
        if peer.get("enabled", True):
            subprocess.run([WG, "set", state["server"]["interface"], "peer", peer["public_key"], "remove"],
                          capture_output=True, timeout=10)
            _sync_peer_to_runtime(peer)
        else:
            subprocess.run([WG, "set", state["server"]["interface"], "peer", peer["public_key"], "remove"],
                          capture_output=True, timeout=10)
    return True, "Peer updated"


def _calc_acl(allowed_ips):
    local_nets = _get_local_networks()
    parts = [aip.strip() for aip in allowed_ips.split(",") if aip.strip()]
    is_internet = "0.0.0.0/0" in parts
    is_lan = any(p in local_nets for p in parts) or any(p.startswith("10.0.0.") and "/24" in p for p in parts)
    is_dmz = any(p == f"{_get_dmz_ip()}/32" or p == "192.168.1.204/32" for p in parts)
    acls = {
        "internet": is_internet,
        "lan": is_lan,
        "dmz": is_dmz and not is_lan,
        "custom_networks": [],
    }
    known = {"0.0.0.0/0", "10.0.0.0/24", "10.0.0.1/32", "10.0.0.2/32"}
    known.update(local_nets)
    if is_dmz:
        known.add(f"{_get_dmz_ip()}/32")
        known.add("192.168.1.204/32")
    for aip in parts:
        if aip not in known and "/" in aip:
            acls["custom_networks"].append(aip)
    return acls


def update_peer_acl(peer_id, internet=False, lan=False, dmz=False, custom_networks=None):
    state = _load_state()
    if peer_id not in state.get("peers", {}):
        return False, "Peer not found"
    peer = state["peers"][peer_id]
    parts = []
    if internet:
        parts.append("0.0.0.0/0")
    else:
        parts.append(f"{peer['address']}/32")
        parts.append("10.0.0.1/32")
        if lan:
            for net in _get_local_networks():
                if net not in parts:
                    parts.append(net)
            if "10.0.0.0/24" not in parts:
                parts.append("10.0.0.0/24")
        if dmz:
            parts.append(f"{_get_dmz_ip()}/32")
    if custom_networks:
        for cn in custom_networks:
            cn = cn.strip()
            if cn and cn not in parts:
                try:
                    ipaddress.ip_network(cn, strict=False)
                    parts.append(cn)
                except ValueError:
                    pass
    if not parts:
        parts = [f"{peer['address']}/32"]
    allowed_ips = ",".join(parts)
    peer["allowed_ips"] = allowed_ips
    peer["acl"] = {
        "internet": internet,
        "lan": lan,
        "dmz": dmz,
        "custom_networks": list(custom_networks) if custom_networks else [],
    }
    _save_state(state)
    _write_server_config(state["server"])
    if get_server_status()["running"]:
        if peer.get("enabled", True):
            subprocess.run([WG, "set", state["server"]["interface"], "peer", peer["public_key"], "remove"],
                          capture_output=True, timeout=10)
            _sync_peer_to_runtime(peer)
            _apply_firewall_rules(peer_id)
        else:
            subprocess.run([WG, "set", state["server"]["interface"], "peer", peer["public_key"], "remove"],
                          capture_output=True, timeout=10)
    return True, "ACL updated"


def remove_peer(peer_id):
    state = _load_state()
    if peer_id not in state.get("peers", {}):
        return False, "Peer not found"
    peer = state["peers"][peer_id]
    status = get_server_status()
    if status["running"]:
        subprocess.run([WG, "set", state["server"]["interface"], "peer", peer["public_key"], "remove"],
                       capture_output=True, timeout=10)
    _remove_firewall_rules(peer.get("address"))
    del state["peers"][peer_id]
    _save_state(state)
    _write_server_config(state["server"])
    return True, f"Peer '{peer.get('name', peer_id)}' removed"


def toggle_peer(peer_id, enabled=None):
    state = _load_state()
    if peer_id not in state.get("peers", {}):
        return False, "Peer not found"
    peer = state["peers"][peer_id]
    new_enabled = not peer.get("enabled", True) if enabled is None else enabled
    peer["enabled"] = new_enabled
    status = get_server_status()
    if status["running"]:
        if new_enabled:
            _sync_peer_to_runtime(peer)
            _apply_firewall_rules(peer_id)
        else:
            subprocess.run([WG, "set", state["server"]["interface"], "peer", peer["public_key"], "remove"],
                          capture_output=True, timeout=10)
            _apply_firewall_rules(peer_id)
    _save_state(state)
    _write_server_config(state["server"])
    return True, f"Peer {'enabled' if new_enabled else 'disabled'}"


def _sync_peer_to_runtime(peer):
    if not peer.get("enabled", True):
        return
    iface = _load_state()["server"]["interface"]
    import tempfile
    fd, psk_file = tempfile.mkstemp(prefix="wg_psk_", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(peer["preshared_key"])
        cmd = [WG, "set", iface, "peer", peer["public_key"],
               "preshared-key", psk_file,
               "allowed-ips", f"{peer['address']}/32"]
        if peer.get("persistent_keepalive"):
            cmd.extend(["persistent-keepalive", str(peer["persistent_keepalive"])])
        subprocess.run(cmd, capture_output=True, timeout=10)
    finally:
        try:
            os.unlink(psk_file)
        except Exception:
            pass


def get_peer_config(peer_id):
    state = _load_state()
    if peer_id not in state.get("peers", {}):
        return None
    peer = state["peers"][peer_id]
    server = state["server"]
    wan_ip = _get_wan_ip() or server.get("address", "").split("/")[0]
    dns = server.get("dns", "1.1.1.1")
    client_allowed = "0.0.0.0/0, ::/0"
    acl = peer.get("acl", {})
    if not acl.get("internet", False):
        parts = [f"{peer['address']}/32", "10.0.0.1/32"]
        if acl.get("lan"):
            for net in _get_local_networks():
                if net not in parts:
                    parts.append(net)
            if "10.0.0.0/24" not in parts:
                parts.append("10.0.0.0/24")
        if acl.get("dmz"):
            parts.append(f"{_get_dmz_ip()}/32")
        for cn in acl.get("custom_networks", []):
            if cn not in parts:
                parts.append(cn)
        if parts:
            client_allowed = ", ".join(parts)
    client_config = f"""[Interface]
PrivateKey = {peer['private_key']}
Address = {peer['address']}/32
DNS = {dns}

[Peer]
PublicKey = {server['public_key']}
PresharedKey = {peer['preshared_key']}
Endpoint = {wan_ip}:{server['listen_port']}
AllowedIPs = {client_allowed}
PersistentKeepalive = {peer.get('persistent_keepalive', '25')}
"""
    return client_config


def get_peer_qr_base64(peer_id):
    try:
        import qrcode
        from io import BytesIO
        config = get_peer_config(peer_id)
        if not config:
            return None
        qr = qrcode.QRCode(border=2)
        qr.add_data(config)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def get_all_peers_with_status():
    state = _load_state()
    peers = state.get("peers", {})
    status = get_server_status()
    runtime_peers = {p.get("public_key"): p for p in status.get("peers", [])}
    try:
        _record_stats()
    except Exception:
        pass
    result = []
    for peer_id, peer in peers.items():
        p = dict(peer)
        rp = runtime_peers.get(p["public_key"])
        if rp:
            p["connected"] = rp.get("latest_handshake") is not None
            p["last_handshake"] = rp.get("latest_handshake")
            p["transfer_rx"] = rp.get("transfer_rx", 0)
            p["transfer_tx"] = rp.get("transfer_tx", 0)
            p["endpoint"] = rp.get("endpoint")
        else:
            p["connected"] = False
            p["last_handshake"] = None
            p["transfer_rx"] = 0
            p["transfer_tx"] = 0
            p["endpoint"] = None
        p["id"] = peer_id
        if "acl" not in p:
            p["acl"] = _calc_acl(p.get("allowed_ips", ""))
        result.append(p)
    return result


def get_preset_networks():
    return get_available_networks()


def _record_stats():
    status = get_server_status()
    if not status.get("running"):
        return
    _detect_events()
    state = _load_state()
    now = time.time()
    stats = _load_stats_history()
    entry = {"ts": now, "peers": {}}
    for p in status.get("peers", []):
        pk = p.get("public_key", "")
        for pid, pdata in state.get("peers", {}).items():
            if pdata.get("public_key") == pk:
                entry["peers"][pid] = {
                    "name": pdata.get("name", pid),
                    "rx": p.get("transfer_rx", 0),
                    "tx": p.get("transfer_tx", 0),
                    "handshake": p.get("latest_handshake"),
                    "endpoint": p.get("endpoint"),
                }
                break
    stats.append(entry)
    cutoff = now - 86400
    stats = [e for e in stats if e["ts"] > cutoff]
    with open(WG_STATS_FILE, "w") as f:
        json.dump(stats, f)


def _load_stats_history():
    try:
        with open(WG_STATS_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def get_stats(hours=24):
    stats = _load_stats_history()
    cutoff = time.time() - hours * 3600
    return [e for e in stats if e["ts"] > cutoff]


def get_bandwidth_summary(hours=24):
    stats = get_stats(hours)
    if not stats:
        return {"total_rx": 0, "total_tx": 0, "peers": {}}
    state = _load_state()
    peers_rx = defaultdict(int)
    peers_tx = defaultdict(int)
    for e in stats:
        for pid, pdata in e.get("peers", {}).items():
            peers_rx[pid] = max(peers_rx[pid], pdata.get("rx", 0))
            peers_tx[pid] = max(peers_tx[pid], pdata.get("tx", 0))
    first_rx = defaultdict(int)
    first_tx = defaultdict(int)
    if stats:
        for pid, pdata in stats[0].get("peers", {}).items():
            first_rx[pid] = pdata.get("rx", 0)
            first_tx[pid] = pdata.get("tx", 0)
    result_peers = {}
    for pid in peers_rx:
        name = pid
        if pid in state.get("peers", {}):
            name = state["peers"][pid].get("name", pid)
        result_peers[pid] = {
            "name": name,
            "rx_delta": peers_rx.get(pid, 0) - first_rx.get(pid, 0),
            "tx_delta": peers_tx.get(pid, 0) - first_tx.get(pid, 0),
            "rx_total": peers_rx.get(pid, 0),
            "tx_total": peers_tx.get(pid, 0),
        }
    return {
        "total_rx": sum(p["rx_total"] for p in result_peers.values()),
        "total_tx": sum(p["tx_total"] for p in result_peers.values()),
        "peers": result_peers,
    }


def get_connection_events(hours=24):
    return get_events(limit=100)


NFT = "/usr/sbin/nft"


def _remove_firewall_rules(peer_ip):
    if not peer_ip:
        return
    sub_chain = f"wg_peer_{peer_ip.replace('.', '_')}"
    try:
        result = subprocess.run([NFT, "-a", "list", "chain", "inet", "filter", "wg_acl"],
                               capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if peer_ip in line and "jump" in line:
                handle = line.strip().split("handle")[-1].strip()
                if handle:
                    subprocess.run([NFT, "delete", "rule", "inet", "filter", "wg_acl", "handle", handle],
                                  capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        subprocess.run([NFT, "delete", "chain", "inet", "filter", sub_chain],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def format_bytes(b):
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b/1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b/1024/1024:.1f} MB"
    else:
        return f"{b/1024/1024/1024:.2f} GB"


def format_timestamp(ts_str):
    if not ts_str:
        return "Never"
    try:
        if isinstance(ts_str, str):
            return ts_str
        return datetime.fromtimestamp(ts_str).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts_str)
