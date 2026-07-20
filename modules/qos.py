#!/usr/bin/env python3
import os
import json
import subprocess
import shutil
import copy
NFT = "/usr/sbin/nft"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
QOS_FILE = os.path.join(DATA_DIR, "qos.json")


def _get_qos_ifaces(profile=None, data=None):
    wan, lan, vpn_ifaces = _detect_ifaces()
    if profile is None:
        if data is None:
            data = _load()
        profile = data.get("profiles", {}).get(data.get("active_profile", "gaming"), {})
    ifaces = []
    if profile.get("qos_wan", True):
        ifaces.append(wan)
    if profile.get("qos_lan", True):
        ifaces.append(lan)
    if "qos_vpns" in profile:
        enabled_vpns = profile["qos_vpns"]
    else:
        enabled_vpns = vpn_ifaces
    for v in vpn_ifaces:
        if v in enabled_vpns:
            ifaces.append(v)
    return ifaces

DEFAULT_PROFILES = {
    "gaming": {
        "name": "Gaming",
        "icon": "\U0001f3ae",
        "description": "Ultra-low latency for competitive gaming. Aggressive queue management, small buffers.",
        "algorithm": "cake",
        "download_mbit": 20,
        "upload_mbit": 18,
        "cake_diffserv": "besteffort",
        "cake_flowmode": "dual-dsthost",
        "cake_nat": True,
        "cake_overhead": 38,
        "fq_codel_target": "3ms",
        "fq_codel_interval": "50ms",
        "fq_codel_limit": 1024,
        "priorities": {"game_ports": "high", "voip": "high", "web": "normal", "streaming": "low", "bulk": "lowest"}
    },
    "streaming": {
        "name": "Streaming",
        "icon": "\U0001f3ac",
        "description": "Maximum throughput for 4K video streaming. Large buffers, throughput-optimized.",
        "algorithm": "cake",
        "download_mbit": 20,
        "upload_mbit": 18,
        "cake_diffserv": "diffserv4",
        "cake_flowmode": "dual-dsthost",
        "cake_nat": True,
        "cake_overhead": 38,
        "fq_codel_target": "5ms",
        "fq_codel_interval": "100ms",
        "fq_codel_limit": 10240,
        "priorities": {"streaming": "high", "web": "normal", "voip": "normal", "game_ports": "normal", "bulk": "low"}
    },
    "office": {
        "name": "Office",
        "icon": "\U0001f4bb",
        "description": "Balanced for video calls, VoIP and web browsing. Fair sharing between all users.",
        "algorithm": "cake",
        "download_mbit": 20,
        "upload_mbit": 18,
        "cake_diffserv": "diffserv4",
        "cake_flowmode": "triple-isolate",
        "cake_nat": True,
        "cake_overhead": 38,
        "fq_codel_target": "5ms",
        "fq_codel_interval": "100ms",
        "fq_codel_limit": 4096,
        "priorities": {"voip": "high", "web": "high", "streaming": "normal", "game_ports": "normal", "bulk": "low"}
    },
    "iot": {
        "name": "IoT / Light",
        "icon": "\U0001f4e1",
        "description": "Minimal QoS for IoT-heavy networks. Low resource usage, basic fairness.",
        "algorithm": "fq_codel",
        "download_mbit": 20,
        "upload_mbit": 18,
        "cake_diffserv": "besteffort",
        "cake_flowmode": "dual-dsthost",
        "cake_nat": True,
        "cake_overhead": 38,
        "fq_codel_target": "5ms",
        "fq_codel_interval": "100ms",
        "fq_codel_limit": 1024,
        "priorities": {}
    },
    "custom": {
        "name": "Custom",
        "icon": "\u2699",
        "description": "Fully manual configuration. All values adjustable.",
        "algorithm": "cake",
        "download_mbit": 20,
        "upload_mbit": 18,
        "cake_diffserv": "besteffort",
        "cake_flowmode": "dual-dsthost",
        "cake_nat": True,
        "cake_overhead": 38,
        "cake_rtt": "100ms",
        "cake_wash": False,
        "cake_ack_filter": False,
        "cake_split_gso": True,
        "fq_codel_target": "5ms",
        "fq_codel_interval": "100ms",
        "fq_codel_limit": 4096,
        "fq_codel_flows": 4096,
        "priorities": {
            "game_ports": "normal",
            "voip": "normal",
            "web": "normal",
            "streaming": "normal",
            "bulk": "normal",
            "vpn": "normal",
            "lan_mgmt": "normal"
        }
    }
}

ALGORITHMS = {
    "cake": {
        "name": "CAKE",
        "description": "Common Applications Kept Enhanced. Best all-around qdisc. Handles bufferbloat, fairness, and diffserv automatically.",
        "params": ["diffserv", "flowmode", "nat", "overhead", "rtt", "wash", "ack_filter", "split_gso"]
    },
    "fq_codel": {
        "name": "fq_codel",
        "description": "Fair Queuing with Controlled Delay. Lightweight, effective bufferbloat control. Good for simpler setups.",
        "params": ["limit", "target", "interval", "flows", "quantum", "ecn", "memory_limit"]
    },
    "htb": {
        "name": "HTB + fq_codel",
        "description": "Hierarchical Token Bucket with fq_codel leaf. Class-based bandwidth allocation with per-class rate limits.",
        "params": ["rate", "ceil", "burst", "cburst"]
    },
    "hfsc": {
        "name": "HFSC",
        "description": "Hierarchical Fair Service Curve. Guarantees latency and bandwidth per class. Advanced but complex.",
        "params": ["sc", "ls", "rt", "ul"]
    }
}

CAKE_DIFFSERV = {
    "besteffort": {"label": "Best Effort", "desc": "All traffic treated equally. Simplest mode, best for gaming."},
    "diffserv4": {"label": "DiffServ 4-tier", "desc": "4 priority tiers (TOS-based). Good balance for mixed traffic."},
    "diffserv8": {"label": "DiffServ 8-tier", "desc": "8 priority tiers. Fine-grained QoS for complex networks."},
    "diffserv-llt": {"label": "DiffServ Low-Latency", "desc": "Low-latency traffic gets priority. VoIP/gaming optimized."},
}

CAKE_FLOWMODE = {
    "flowblind": {"label": "Flow Blind", "desc": "No flow isolation. All traffic in one bucket."},
    "srchost": {"label": "Source Host", "desc": "Fairness per source IP. Download direction."},
    "dsthost": {"label": "Dest Host", "desc": "Fairness per destination IP. Upload direction."},
    "hosts": {"label": "Hosts", "desc": "Fairness per host (both src and dst)."},
    "flows": {"label": "Flows", "desc": "Fairness per flow (5-tuple). Most granular."},
    "dual-srchost": {"label": "Dual SrcHost", "desc": "Per-source on download, per-flow on upload. Best for LAN gateways."},
    "dual-dsthost": {"label": "Dual DstHost", "desc": "Per-dest on upload, per-flow on download. Best for internet gateways."},
    "triple-isolate": {"label": "Triple Isolate", "desc": "Per-host, per-flow isolation in both directions. Fairest but uses more memory."},
}

PRIORITY_CLASSES = {
    "game_ports": {"label": "Gaming", "ports": "3074,3478-3479,27000-27100,7777-7780", "networks": "", "protocols": "", "icon": "\U0001f3ae", "editable": True},
    "voip": {"label": "VoIP / Video Calls", "ports": "5060-5061,10000-20000,3478", "networks": "", "protocols": "udp", "icon": "\U0001f4de", "editable": True},
    "web": {"label": "Web Browsing", "ports": "80,443", "networks": "", "protocols": "", "icon": "\U0001f310", "editable": True},
    "streaming": {"label": "Streaming", "ports": "1935,1936,8000-8010,554", "networks": "", "protocols": "", "icon": "\U0001f3ac", "editable": True},
    "bulk": {"label": "Bulk / Downloads", "ports": "20-21,69,119,445,873,3389", "networks": "", "protocols": "", "icon": "\U0001f4e5", "editable": True},
    "vpn": {"label": "VPN Traffic", "ports": "51820", "networks": "10.0.0.0/24", "protocols": "udp", "icon": "\U0001f512", "editable": True},
    "lan_mgmt": {"label": "LAN Management", "ports": "22,8080,8443", "networks": "172.24.1.0/24", "protocols": "", "icon": "\U0001f527", "editable": True},
}

DSCP_MAP = {
    "highest": "cs7",
    "high": "cs5",
    "normal": "cs0",
    "low": "cs1",
    "lowest": "cs1",
}

PRIORITY_LEVELS = {
    "highest": {"label": "Highest", "value": 7},
    "high": {"label": "High", "value": 5},
    "normal": {"label": "Normal", "value": 3},
    "low": {"label": "Low", "value": 1},
    "lowest": {"label": "Lowest", "value": 0},
}

NFT_QOS_TABLE = "qos_marks"

import re as _re

_IFACE_RE = _re.compile(r'^[a-zA-Z0-9_.\-]{1,15}$')
_TIME_RE = _re.compile(r'^\d+(ms|s|us)?$', _re.IGNORECASE)
_SIZE_RE = _re.compile(r'^\d+(b|kb|mb|gb|kbit|mbit|gbit)?$', _re.IGNORECASE)
_RATE_RE = _re.compile(r'^\d+(mbit|kbit|bit|mb|kb|b)?$', _re.IGNORECASE)
_BURST_RE = _re.compile(r'^\d+(b|kb|mb)?$', _re.IGNORECASE)
_PORTS_RE = _re.compile(r'^[\d,\-\s]+$')
_NET_RE = _re.compile(r'^[\d\.\-/,a-fA-F:\s]+$')
_PROTO_RE = _re.compile(r'^(tcp|udp|icmp|6|17|1)$', _re.IGNORECASE)
_OVERHEAD_RE = _re.compile(r'^\d+$')


def _val_iface(v, default="eth0"):
    if isinstance(v, str) and _IFACE_RE.match(v):
        return v
    return default


def _val_time(v, default="100ms"):
    if isinstance(v, str) and _TIME_RE.match(v):
        return v
    return default


def _val_size(v, default="32Mb"):
    if isinstance(v, str) and _SIZE_RE.match(v):
        return v
    if isinstance(v, int) and v > 0:
        return str(v)
    return default


def _val_rate(v, default="20mbit"):
    if isinstance(v, (int, float)) and v > 0:
        return f"{int(v)}mbit"
    if isinstance(v, str) and _RATE_RE.match(v):
        return v
    return default


def _val_burst(v, default="15kb"):
    if isinstance(v, str) and _BURST_RE.match(v):
        return v
    if isinstance(v, int) and v > 0:
        return f"{v}b"
    return default


def _val_overhead(v, default=38):
    if isinstance(v, int) and 0 <= v <= 1000:
        return v
    if isinstance(v, str) and _OVERHEAD_RE.match(v):
        return int(v)
    return default


def _val_ports(v, default=""):
    if isinstance(v, str) and _PORTS_RE.match(v):
        return v
    return default


def _val_networks(v, default=""):
    if isinstance(v, str) and _NET_RE.match(v):
        return v
    return default


def _val_proto(v, default=""):
    if isinstance(v, str) and _PROTO_RE.match(v):
        return v
    return default


def _val_int(v, default=0, minimum=0, maximum=2**31 - 1):
    try:
        n = int(v)
        if minimum <= n <= maximum:
            return n
    except (ValueError, TypeError):
        pass
    return default


def _val_diffserv(v, default="besteffort"):
    if v in CAKE_DIFFSERV:
        return v
    return default


def _val_flowmode(v, default="dual-dsthost"):
    if v in CAKE_FLOWMODE:
        return v
    return default


def _run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def _run_nft_stdin(ruleset, timeout=10):
    try:
        r = subprocess.run([NFT, "-f", "-"], input=ruleset, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def _run_sh_safe(cmd_list, timeout=15):
    try:
        r = subprocess.run(cmd_list, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def _load():
    try:
        with open(QOS_FILE) as f:
            return json.load(f)
    except Exception:
        return _default_state()


def _default_state():
    return {
        "enabled": True,
        "active_profile": "gaming",
        "profiles": dict(DEFAULT_PROFILES),
        "manual_rules": [],
        "interfaces": _detect_interfaces()
    }


def _save(data):
    with open(QOS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _detect_interfaces():
    ifaces = []
    ok, out, _ = _run(["ip", "-j", "-br", "addr"])
    if ok and out:
        try:
            for i in json.loads(out):
                ifaces.append({
                    "name": i.get("ifname", ""),
                    "operstate": i.get("operstate", "UNKNOWN"),
                    "addr_info": i.get("addr_info", []),
                    "mtu": 1500,
                    "qdisc": "",
                    "rx_bytes": 0,
                    "tx_bytes": 0
                })
        except Exception:
            pass
    for iface in ifaces:
        name = iface["name"]
        ok_mtu, out_mtu, _ = _run(["cat", f"/sys/class/net/{name}/mtu"])
        if ok_mtu:
            try:
                iface["mtu"] = int(out_mtu.strip())
            except ValueError:
                pass
        ok_qd, out_qd, _ = _run(["tc", "qdisc", "show", "dev", name])
        if ok_qd and out_qd:
            iface["qdisc"] = out_qd.split("\n")[0] if out_qd else ""
        ok_rx, out_rx, _ = _run(["cat", f"/sys/class/net/{name}/statistics/rx_bytes"])
        if ok_rx:
            try:
                iface["rx_bytes"] = int(out_rx.strip())
            except ValueError:
                pass
        ok_tx, out_tx, _ = _run(["cat", f"/sys/class/net/{name}/statistics/tx_bytes"])
        if ok_tx:
            try:
                iface["tx_bytes"] = int(out_tx.strip())
            except ValueError:
                pass
    return ifaces


def get_qos_state():
    data = _load()
    data["interfaces"] = _detect_interfaces()
    data["algorithms"] = ALGORITHMS
    data["cake_diffserv_options"] = CAKE_DIFFSERV
    data["cake_flowmode_options"] = CAKE_FLOWMODE
    data["priority_classes"] = get_all_priority_classes()
    data["priority_levels"] = PRIORITY_LEVELS
    wan, lan, vpn = _detect_ifaces()
    data["wan_iface"] = wan
    data["lan_iface"] = lan
    data["vpn_ifaces"] = vpn
    return data


def get_current_qdisc(iface):
    ok, out, _ = _run(["tc", "qdisc", "show", "dev", iface])
    if ok and out:
        lines = out.strip().split("\n")
        for line in lines:
            if "cake" in line.lower():
                return {"type": "cake", "raw": line.strip()}
            if "fq_codel" in line.lower():
                return {"type": "fq_codel", "raw": line.strip()}
            if "htb" in line.lower():
                return {"type": "htb", "raw": line.strip()}
            if "hfsc" in line.lower():
                return {"type": "hfsc", "raw": line.strip()}
            if "noqueue" in line.lower():
                return {"type": "noqueue", "raw": line.strip()}
            if "pfifo_fast" in line.lower():
                return {"type": "pfifo_fast", "raw": line.strip()}
            if "mq" in line.lower():
                continue
        return {"type": "unknown", "raw": lines[-1].strip() if lines else ""}
    return {"type": "none", "raw": ""}


def get_manual_rules_stats():
    wan, lan, vpn = _detect_ifaces()
    data = _load()
    qos_ifaces = _get_qos_ifaces(data=data)
    shaping_ifaces = [i for i in qos_ifaces if i != wan]
    result = []
    for iface in shaping_ifaces:
        ok, out, _ = _run(["tc", "-s", "filter", "show", "dev", iface, "parent", "1:"], timeout=10)
        if not ok or not out:
            continue
        current_filter = {}
        for line in out.split("\n"):
            ls = line.strip()
            if ls.startswith("match"):
                if current_filter:
                    result.append(dict(current_filter))
                parts = ls.split()
                current_filter = {"iface": iface, "match": ls, "pkts": 0, "bytes": 0, "drops": 0, "overlimits": 0}
            elif "action" in ls and "police" in ls:
                current_filter["action_raw"] = ls
            elif "Sent" in ls:
                parts = ls.split()
                for i, p in enumerate(parts):
                    if p == "Sent":
                        try:
                            current_filter["bytes"] = int(parts[i + 1])
                            current_filter["pkts"] = int(parts[i + 3])
                        except (IndexError, ValueError):
                            pass
            elif "dropped" in ls:
                parts = ls.replace("(", "").replace(")", "").replace(",", "").split()
                for i, p in enumerate(parts):
                    if p == "dropped":
                        try:
                            current_filter["drops"] = int(parts[i + 1])
                        except (IndexError, ValueError):
                            pass
                    if p == "overlimits":
                        try:
                            current_filter["overlimits"] = int(parts[i + 1])
                        except (IndexError, ValueError):
                            pass
        if current_filter:
            result.append(dict(current_filter))
    return result


def get_qos_stats(iface):
    ok, out, _ = _run(["tc", "-s", "qdisc", "show", "dev", iface])
    if not ok:
        return {"raw": "", "sent_bytes": 0, "sent_packets": 0, "dropped": 0, "overlimits": 0, "backlog": 0}
    stats = {"raw": out, "sent_bytes": 0, "sent_packets": 0, "dropped": 0, "overlimits": 0, "backlog": 0}
    for line in out.split("\n"):
        line = line.strip()
        if "Sent" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "Sent":
                    try:
                        stats["sent_bytes"] = int(parts[i + 1])
                        stats["sent_packets"] = int(parts[i + 3])
                    except (IndexError, ValueError):
                        pass
        if "dropped" in line:
            parts = line.replace("(", "").replace(")", "").replace(",", "").split()
            for i, p in enumerate(parts):
                if p == "dropped":
                    try:
                        stats["dropped"] = int(parts[i + 1])
                    except (IndexError, ValueError):
                        pass
                if p == "overlimits":
                    try:
                        stats["overlimits"] = int(parts[i + 1])
                    except (IndexError, ValueError):
                        pass
        if "backlog" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "backlog":
                    try:
                        stats["backlog"] = int(parts[i + 1])
                    except (IndexError, ValueError):
                        pass
    return stats


TIN_NAMES = {
    "Bulk": {"icon": "\U0001f4e5", "color": "#8b949e", "dscp": "CS1 (Low)"},
    "Best Effort": {"icon": "\U0001f310", "color": "#58a6ff", "dscp": "CS0 (Normal)"},
    "Video": {"icon": "\U0001f3ac", "color": "#d29922", "dscp": "CS4/AF41"},
    "Voice": {"icon": "\U0001f4de", "color": "#3fb950", "dscp": "CS5/EF"},
}


def get_cake_tin_stats(iface):
    ok, out, _ = _run(["tc", "-s", "qdisc", "show", "dev", iface])
    if not ok:
        return {"iface": iface, "type": "unknown", "tins": [], "total": {}}
    qdisc_type = "unknown"
    for line in out.splitlines():
        if "cake" in line.lower():
            qdisc_type = "cake"
            break
        if "fq_codel" in line.lower():
            qdisc_type = "fq_codel"
            break
    if qdisc_type != "cake":
        stats = get_qos_stats(iface)
        stats["iface"] = iface
        stats["type"] = qdisc_type
        stats["tins"] = []
        return stats
    result = {"iface": iface, "type": "cake", "tins": [], "total": {}}
    total_sent_bytes = total_sent_pkts = total_drops = total_overlimits = 0
    for line in out.splitlines():
        ls = line.strip()
        if ls.startswith("Sent"):
            parts = ls.split()
            for i, p in enumerate(parts):
                if p == "Sent":
                    try:
                        total_sent_bytes = int(parts[i + 1])
                        total_sent_pkts = int(parts[i + 3])
                    except (IndexError, ValueError):
                        pass
        if "dropped" in ls and "overlimits" in ls:
            parts = ls.replace("(", "").replace(")", "").replace(",", "").split()
            for i, p in enumerate(parts):
                if p == "dropped":
                    try: total_drops = int(parts[i + 1])
                    except (IndexError, ValueError): pass
                if p == "overlimits":
                    try: total_overlimits = int(parts[i + 1])
                    except (IndexError, ValueError): pass
    result["total"] = {"bytes": total_sent_bytes, "pkts": total_sent_pkts, "drops": total_drops, "overlimits": total_overlimits}

    tin_names = ["Bulk", "Best Effort", "Video", "Voice"]
    for tn in tin_names:
        tin = {"name": tn, "pkts": 0, "bytes": 0, "drops": 0, "marks": 0,
               "way_inds": 0, "way_miss": 0, "way_cols": 0,
               "pk_delay": "-", "av_delay": "-", "sp_delay": "-",
               "backlog": "-", "thresh": "-",
               "sp_flows": 0, "bk_flows": 0, "max_len": 0}
        tin.update(TIN_NAMES.get(tn, {}))
        result["tins"].append(tin)

    in_tins = False
    for line in out.splitlines():
        ls = line.rstrip()
        if "Bulk" in ls and "Best Effort" in ls:
            in_tins = True
            continue
        if not in_tins:
            continue
        parts = ls.split()
        if len(parts) < 5:
            continue
        label = parts[0]
        vals = parts[1:5]
        idx_map = {"pkts": "pkts", "bytes": "bytes", "drops": "drops", "marks": "marks",
                    "way_inds": "way_inds", "way_miss": "way_miss", "way_cols": "way_cols",
                    "max_len": "max_len", "sp_flows": "sp_flows", "bk_flows": "bk_flows"}
        delay_map = {"pk_delay": "pk_delay", "av_delay": "av_delay", "sp_delay": "sp_delay"}
        if label in idx_map:
            key = idx_map[label]
            for i, v in enumerate(vals):
                if i < len(result["tins"]):
                    try:
                        result["tins"][i][key] = int(v)
                    except (ValueError, TypeError):
                        pass
        elif label in delay_map:
            key = delay_map[label]
            for i, v in enumerate(vals):
                if i < len(result["tins"]):
                    result["tins"][i][key] = v
        elif label == "thresh":
            for i, v in enumerate(vals):
                if i < len(result["tins"]):
                    result["tins"][i]["thresh"] = v
        elif label == "backlog":
            for i, v in enumerate(vals):
                if i < len(result["tins"]):
                    result["tins"][i]["backlog"] = v
    return result


def apply_profile(profile_id):
    data = _load()
    profiles = data.get("profiles", {})
    if profile_id not in profiles and profile_id not in DEFAULT_PROFILES:
        return False, f"Unknown profile: {profile_id}"
    profile = profiles.get(profile_id, DEFAULT_PROFILES.get(profile_id))
    if not profile:
        return False, f"Profile not found: {profile_id}"
    if not data.get("enabled", True):
        return False, "QoS is disabled. Enable it first."
    wan_iface, lan_iface, vpn_ifaces = _detect_ifaces()
    qos_ifaces = _get_qos_ifaces(profile, data)
    algorithm = profile.get("algorithm", "cake")
    dl = profile.get("download_mbit", 20)
    ul = profile.get("upload_mbit", 18)
    if dl < 1 or ul < 1:
        return False, f"Invalid bandwidth: download={dl}, upload={ul}. Must be >= 1 Mbit."
    priorities = profile.get("priorities", {})
    try:
        for iface in qos_ifaces:
            is_lan = iface != wan_iface
            bw = dl if is_lan else ul
            if algorithm == "cake":
                ok = _apply_cake(iface, bw, profile, is_lan=is_lan)
            elif algorithm == "fq_codel":
                ok = _apply_fq_codel(iface, profile)
            elif algorithm == "htb":
                ok = _apply_htb(iface, bw, profile)
            elif algorithm == "hfsc":
                ok = _apply_hfsc(iface, bw, profile)
            else:
                return False, f"Unknown algorithm: {algorithm}"
            if not ok:
                return False, f"Failed to apply qdisc on {iface}. Check tc support."
    except Exception as e:
        return False, f"Error applying profile: {e}"
    if not _apply_dscp_marks(priorities, wan_iface, lan_iface):
        return False, "Failed to apply QoS DSCP nft rules"
    if not _apply_manual_rules(qos_ifaces):
        return False, "Failed to apply one or more QoS traffic filters"
    data["active_profile"] = profile_id
    _save(data)
    _update_qos_script(data)
    return True, f"Profile '{profile.get('name', profile_id)}' applied ({algorithm}, {dl}/{ul} Mbit) on {', '.join(qos_ifaces)}"


def _detect_ifaces():
    try:
        from modules.ifaces import get_wan, get_lan, get_by_role
        wan = get_wan()
        lan = get_lan()
        vpn_ifaces = get_by_role("vpn")
        return wan, lan, vpn_ifaces
    except Exception:
        pass
    wan = None
    lan = None
    vpn_ifaces = []
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
    if not wan:
        wan = _first_iface_by_role("wan")
    ok, out, _ = _run(["ip", "-j", "-br", "addr"])
    if ok and out:
        try:
            for i in json.loads(out):
                name = i.get("ifname", "")
                state = i.get("operstate", "")
                if name in ("lo", wan) or name.startswith("wg") or name.startswith("docker") or name.startswith("br-") or name.startswith("veth"):
                    if name.startswith("wg"):
                        vpn_ifaces.append(name)
                    continue
                if state in ("UP", "UNKNOWN", "DORMANT") and not lan:
                    lan = name
        except Exception:
            pass
    if not lan:
        lan = _first_iface_by_role("lan", exclude={wan} | set(vpn_ifaces))
    ok2, out2, _ = _run(["ip", "-br", "addr"])
    if ok2 and out2 and not lan:
        for line in out2.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 2 and parts[0] not in ("lo", wan) and not parts[0].startswith("wg"):
                if parts[1] in ("UP", "UNKNOWN"):
                    lan = parts[0]
                    break
    if not vpn_ifaces:
        ok, out, _ = _run(["ip", "-j", "-br", "addr"])
        if ok and out:
            try:
                for i in json.loads(out):
                    name = i.get("ifname", "")
                    if name.startswith("wg"):
                        vpn_ifaces.append(name)
            except Exception:
                pass
    return wan or "eth0", lan or "eth1", vpn_ifaces


def _first_iface_by_role(role, exclude=None):
    exclude = exclude or set()
    ok, out, _ = _run(["ip", "-j", "-br", "addr"])
    if not ok or not out:
        return None
    try:
        ifaces = json.loads(out)
    except Exception:
        return None
    for i in ifaces:
        name = i.get("ifname", "")
        if name in exclude or name == "lo" or name.startswith("wg") or name.startswith("docker"):
            continue
        state = i.get("operstate", "")
        if state not in ("UP", "UNKNOWN", "DORMANT"):
            continue
        if role == "wan" and not name.startswith("wlan") and not name.startswith("br"):
            return name
        if role == "lan":
            return name
    for i in ifaces:
        name = i.get("ifname", "")
        if name not in exclude and name != "lo" and not name.startswith("wg"):
            return name
    return None


def _apply_cake(iface, bandwidth_mbit, profile, is_lan=False):
    diffserv = profile.get("cake_diffserv", "besteffort")
    priorities = profile.get("priorities", {})
    has_custom_prio = any(v != "normal" for v in priorities.values()) if priorities else False
    if diffserv == "besteffort" and has_custom_prio:
        diffserv = "diffserv4"
    flowmode = profile.get("cake_flowmode", "dual-dsthost")
    nat = "nat" if profile.get("cake_nat", True) else "nonat"
    overhead = profile.get("cake_overhead", 38)
    rtt = _val_time(profile.get("cake_rtt", "100ms"))
    wash = "wash" if profile.get("cake_wash", False) else "nowash"
    ack = "ack-filter" if profile.get("cake_ack_filter", False) else "no-ack-filter"
    split_gso = "split-gso" if profile.get("cake_split_gso", True) else "no-split-gso"
    if is_lan:
        if "dual-dsthost" in flowmode:
            flowmode = "dual-srchost"
        elif "dsthost" in flowmode:
            flowmode = "srchost"
    iface = _val_iface(iface)
    bandwidth_mbit = _val_int(bandwidth_mbit, default=20, minimum=1, maximum=100000)
    _run(["tc", "qdisc", "del", "dev", iface, "root"], timeout=5)
    cake_args = ["tc", "qdisc", "add", "dev", iface, "root", "handle", "1:", "cake",
                 f"bandwidth", f"{bandwidth_mbit}Mbit", "ethernet", diffserv, flowmode,
                 nat, wash, ack, split_gso, "rtt", rtt, "noatm", "overhead", str(overhead), "mpu", "84"]
    ok, out, err = _run_sh_safe(cake_args)
    if not ok:
        cake_args[2] = "replace"
        ok, _, err = _run_sh_safe(cake_args)
    return ok


def _apply_fq_codel(iface, profile):
    limit = _val_int(profile.get("fq_codel_limit", 1024), default=1024, maximum=1048576)
    target = _val_time(profile.get("fq_codel_target", "5ms"), "5ms")
    interval = _val_time(profile.get("fq_codel_interval", "100ms"), "100ms")
    flows = _val_int(profile.get("fq_codel_flows", 1024), default=1024, maximum=1048576)
    quantum = _val_int(profile.get("fq_codel_quantum", 1514), default=1514, maximum=65535)
    ecn = "ecn" if profile.get("fq_codel_ecn", True) else "noecn"
    memory = _val_size(profile.get("fq_codel_memory", "32Mb"), "32Mb")
    iface = _val_iface(iface)
    _run(["tc", "qdisc", "del", "dev", iface, "root"], timeout=5)
    args = ["tc", "qdisc", "add", "dev", iface, "root", "fq_codel",
            "limit", str(limit), "target", target, "interval", interval,
            "flows", str(flows), "quantum", str(quantum), ecn, "memory_limit", memory]
    ok, _, err = _run_sh_safe(args)
    if not ok:
        args[2] = "replace"
        ok, _, err = _run_sh_safe(args)
    return ok


def _apply_htb(iface, bandwidth_mbit, profile):
    iface = _val_iface(iface)
    bandwidth_mbit = _val_int(bandwidth_mbit, default=20, minimum=1, maximum=100000)
    ok1, _, _ = _run(["tc", "qdisc", "replace", "dev", iface, "root", "handle", "1:", "htb", "default", "30"])
    if not ok1:
        return False
    rate = _val_rate(profile.get("htb_rate", f"{bandwidth_mbit}mbit"), f"{bandwidth_mbit}mbit")
    ceil = _val_rate(profile.get("htb_ceil", rate), rate)
    burst = _val_burst(profile.get("htb_burst", "15kb"), "15kb")
    cburst = _val_burst(profile.get("htb_cburst", "1600b"), "1600b")
    ok2, _, _ = _run(["tc", "class", "replace", "dev", iface, "parent", "1:", "classid", "1:30",
                     "htb", "rate", rate, "ceil", ceil, "burst", burst, "cburst", cburst])
    if not ok2:
        return False
    _run(["tc", "qdisc", "replace", "dev", iface, "parent", "1:30", "handle", "30:", "fq_codel"])
    return True


def _apply_hfsc(iface, bandwidth_mbit, profile):
    iface = _val_iface(iface)
    bandwidth_mbit = _val_int(bandwidth_mbit, default=20, minimum=1, maximum=100000)
    ok1, _, _ = _run(["tc", "qdisc", "replace", "dev", iface, "root", "handle", "1:", "hfsc", "default", "30"])
    if not ok1:
        return False
    rate = _val_rate(profile.get("hfsc_rate", f"{bandwidth_mbit}mbit"), f"{bandwidth_mbit}mbit")
    ok2, _, _ = _run(["tc", "class", "replace", "dev", iface, "parent", "1:", "classid", "1:30",
                     "hfsc", "sc", "rate", rate, "ul", "rate", rate])
    return ok2


def update_profile(profile_id, updates):
    data = _load()
    previous_data = copy.deepcopy(data)
    profiles = data.get("profiles", {})
    if profile_id not in profiles and profile_id not in DEFAULT_PROFILES:
        return False, f"Unknown profile: {profile_id}"
    if profile_id not in profiles:
        profiles[profile_id] = dict(DEFAULT_PROFILES[profile_id])
    profile = profiles[profile_id]
    for k, v in updates.items():
        if k == "priorities" and isinstance(v, dict):
            merged = {}
            for cls_key, cls_val in v.items():
                if isinstance(cls_val, dict):
                    prio_val = cls_val.get("priority", "normal")
                    merged[cls_key] = prio_val
                    if any(ck in cls_val for ck in ("ports", "networks", "protocols")):
                        if "custom_classes" not in profile:
                            profile["custom_classes"] = {}
                        profile["custom_classes"][cls_key] = {
                            kk: cls_val[kk] for kk in ("ports", "networks", "protocols") if kk in cls_val
                        }
                elif isinstance(cls_val, str):
                    merged[cls_key] = cls_val
            profile["priorities"] = merged
        elif k == "custom_classes" and isinstance(v, dict):
            sanitized = {}
            for ck, cv in v.items():
                if isinstance(cv, dict):
                    sanitized[ck] = {
                        "ports": _val_ports(cv.get("ports", "")),
                        "networks": _val_networks(cv.get("networks", "")),
                        "protocols": _val_proto(cv.get("protocols", "")),
                    }
            profile["custom_classes"] = sanitized
        elif k in profile:
            profile[k] = v
        else:
            profile[k] = v
    if "priorities" in profile and "custom_classes" in profile:
        for cls_key, cls_overrides in profile.get("custom_classes", {}).items():
            if cls_key in profile["priorities"]:
                pass
    data["profiles"] = profiles
    _save(data)
    if data.get("active_profile") == profile_id and data.get("enabled", True):
        ok, message = apply_profile(profile_id)
        if not ok:
            _save(previous_data)
            rollback_ok, rollback_message = apply_profile(previous_data.get("active_profile", "gaming"))
            suffix = "" if rollback_ok else f"; rollback failed: {rollback_message}"
            return False, f"Profile update failed: {message}{suffix}"
    return True, f"Profile '{profile.get('name', profile_id)}' updated"


def toggle_qos(enabled):
    data = _load()
    previous = bool(data.get("enabled", True))
    data["enabled"] = enabled
    _save(data)
    if enabled:
        profile_id = data.get("active_profile", "gaming")
        ok, msg = apply_profile(profile_id)
        if not ok:
            data["enabled"] = previous
            _save(data)
            _update_qos_script(data)
            return False, f"QoS enable failed: {msg}"
        _update_qos_script(data)
        return True, f"QoS enabled. {msg}"
    else:
        ok, message = _disable_qos_runtime(data)
        if not ok:
            data["enabled"] = previous
            _save(data)
            return False, f"QoS disable failed: {message}"
        data["enabled"] = False
        _save(data)
        _update_qos_script(data)
        return True, "QoS disabled. Default fq_codel applied."


def _disable_qos_runtime(data):
    wan, lan, vpn_ifaces = _detect_ifaces()
    for iface in [wan, lan] + vpn_ifaces:
        iface = _val_iface(iface)
        if not os.path.exists(f"/sys/class/net/{iface}"):
            continue
        _run(["tc", "qdisc", "del", "dev", iface, "root"], timeout=5)
        ok, _, error = _run(["tc", "qdisc", "add", "dev", iface, "root", "fq_codel"])
        if not ok:
            return False, f"fq_codel failed on {iface}: {error}"
    table_exists, _, _ = _run([NFT, "list", "table", "inet", NFT_QOS_TABLE], timeout=5)
    if table_exists:
        ok, _, error = _run([NFT, "delete", "table", "inet", NFT_QOS_TABLE], timeout=5)
        if not ok:
            return False, f"QoS nft cleanup failed: {error}"
    _remove_qos_jumps()
    return True, "QoS runtime state removed"


def add_manual_rule(rule_type, match_value, bandwidth_kbit, priority, comment=""):
    data = _load()
    rules = data.get("manual_rules", [])
    previous = [dict(item) for item in rules]
    rule = {
        "id": f"rule_{len(rules) + 1}_{int(__import__('time').time())}",
        "type": rule_type,
        "match": match_value,
        "bandwidth_kbit": int(bandwidth_kbit),
        "priority": int(priority),
        "comment": comment,
        "enabled": True
    }
    rules.append(rule)
    data["manual_rules"] = rules
    _save(data)
    if not _apply_manual_rules(_get_qos_ifaces(data=data)):
        data["manual_rules"] = previous
        _save(data)
        _apply_manual_rules(_get_qos_ifaces(data=data))
        return False, "Failed to apply rule; previous rules restored"
    return True, f"Rule added: {rule_type} {match_value}"


def remove_manual_rule(rule_id):
    data = _load()
    rules = data.get("manual_rules", [])
    previous = [dict(item) for item in rules]
    data["manual_rules"] = [r for r in rules if r.get("id") != rule_id]
    _save(data)
    if not _apply_manual_rules(_get_qos_ifaces(data=data)):
        data["manual_rules"] = previous
        _save(data)
        _apply_manual_rules(_get_qos_ifaces(data=data))
        return False, "Failed to remove rule; previous rules restored"
    return True, f"Rule removed"


def toggle_manual_rule(rule_id, enabled):
    data = _load()
    rules = data.get("manual_rules", [])
    previous = [dict(item) for item in rules]
    for r in rules:
        if r.get("id") == rule_id:
            r["enabled"] = enabled
    data["manual_rules"] = rules
    _save(data)
    if not _apply_manual_rules(_get_qos_ifaces(data=data)):
        data["manual_rules"] = previous
        _save(data)
        _apply_manual_rules(_get_qos_ifaces(data=data))
        return False, "Failed to update rule; previous rules restored"
    return True, f"Rule {'enabled' if enabled else 'disabled'}"


def _get_class_config(profile, cls_key):
    all_classes = get_all_priority_classes()
    cls = dict(all_classes.get(cls_key, {}))
    if profile and "custom_classes" in profile:
        overrides = profile["custom_classes"].get(cls_key, {})
        cls.update(overrides)
    return cls


def _apply_dscp_marks(priorities, wan, lan):
    _run([NFT, "delete", "table", "inet", NFT_QOS_TABLE], timeout=5)
    _remove_qos_jumps()
    has_marks = any(v != "normal" for v in priorities.values()) if priorities else False
    if not has_marks:
        return True
    data = _load()
    profile = data.get("profiles", {}).get(data.get("active_profile", "gaming"), {})
    lines = [
        f"table inet {NFT_QOS_TABLE} {{",
        "  chain mark_forward {",
        f"    type filter hook forward priority mangle; policy accept;",
    ]
    for cls_key, prio_level in priorities.items():
        if prio_level == "normal":
            continue
        dscp = DSCP_MAP.get(prio_level, "cs0")
        cls = _get_class_config(profile, cls_key)
        ports = _val_ports(cls.get("ports", ""))
        networks = _val_networks(cls.get("networks", ""))
        protocols = _val_proto(cls.get("protocols", ""))
        if ports:
            if protocols != "udp":
                lines.append(f"    tcp dport {{ {ports} }} ip dscp set {dscp}")
                lines.append(f"    tcp sport {{ {ports} }} ip dscp set {dscp}")
            lines.append(f"    udp dport {{ {ports} }} ip dscp set {dscp}")
            lines.append(f"    udp sport {{ {ports} }} ip dscp set {dscp}")
        if networks:
            for net in [n.strip() for n in networks.split(",") if n.strip()]:
                lines.append(f"    ip daddr {net} ip dscp set {dscp}")
                lines.append(f"    ip saddr {net} ip dscp set {dscp}")
    lines.append("  }")
    lines.append("}")
    ruleset = "\n".join(lines)
    ok, _, err = _run_nft_stdin(ruleset)
    if not ok:
        _run([NFT, "delete", "table", "inet", NFT_QOS_TABLE], timeout=5)
        return False
    return True


def _remove_qos_jumps():
    ok, out, _ = _run([NFT, "-s", "list", "chain", "inet", "filter", "forward"])
    if not ok or not out:
        return
    handles = []
    for line in out.split("\n"):
        stripped = line.strip()
        if "jump mark_forward" in stripped:
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "handle" and i + 1 < len(parts):
                    try:
                        handles.append(int(parts[i + 1]))
                    except ValueError:
                        pass
    for h in handles:
        _run([NFT, "delete", "rule", "inet", "filter", "forward", "handle", str(h)], timeout=5)


def _apply_manual_rules(qos_ifaces=None):
    data = _load()
    rules = [r for r in data.get("manual_rules", []) if r.get("enabled", True)]
    wan, lan, vpn_ifaces = _detect_ifaces()
    all_ifaces = [wan, lan] + vpn_ifaces
    if qos_ifaces is None:
        qos_ifaces = _get_qos_ifaces(data=data)
    shaping_ifaces = [i for i in qos_ifaces if i != wan]
    success = True
    for iface in all_ifaces:
        if iface and iface not in shaping_ifaces:
            _run(["tc", "filter", "del", "dev", iface, "parent", "1:"], timeout=5)
    for iface in shaping_ifaces:
        iface = _val_iface(iface)
        _run(["tc", "filter", "del", "dev", iface, "parent", "1:"], timeout=5)
    if not rules:
        return True
    for idx, r in enumerate(rules):
        rtype = r.get("type", "")
        match_val = r.get("match", "").strip()
        bw = _val_int(r.get("bandwidth_kbit", 0), default=0, maximum=10**9)
        if bw <= 0 or not match_val:
            continue
        burst = max(bw * 2, 64)
        prio = 10 + idx * 10
        for iface in shaping_ifaces:
            iface = _val_iface(iface)
            if rtype == "port":
                try:
                    port_str = match_val.split("-")[0].strip()
                    port_num = int(port_str)
                    if not (0 <= port_num <= 65535):
                        continue
                    ok, _, _ = _run(["tc", "filter", "add", "dev", iface, "parent", "1:", "protocol", "ip",
                                     "prio", str(prio), "u32", "match", "ip", "dport", str(port_num),
                                     "0xffff", "action", "police", "rate", f"{bw}kbit", "burst", f"{burst}kbit",
                                     "conform-exceed", "pass/continue"], timeout=5)
                    success = success and ok
                except (ValueError, IndexError):
                    continue
            elif rtype == "ip":
                import ipaddress
                try:
                    ipaddress.ip_address(match_val.split("/")[0])
                    dst = match_val if "/" in match_val else f"{match_val}/32"
                    ok, _, _ = _run(["tc", "filter", "add", "dev", iface, "parent", "1:", "protocol", "ip",
                                     "prio", str(prio), "u32", "match", "ip", "dst", dst,
                                     "action", "police", "rate", f"{bw}kbit", "burst", f"{burst}kbit",
                                     "conform-exceed", "pass/continue"], timeout=5)
                    success = success and ok
                except (ValueError, TypeError):
                    continue
            elif rtype == "protocol":
                proto_map = {"tcp": "6", "udp": "17", "icmp": "1"}
                proto_num = proto_map.get(match_val.lower(), "")
                if not proto_num or not proto_num.isdigit():
                    continue
                ok, _, _ = _run(["tc", "filter", "add", "dev", iface, "parent", "1:", "protocol", "ip",
                                 "prio", str(prio), "u32", "match", "ip", "protocol", proto_num,
                                 "0xff", "action", "police", "rate", f"{bw}kbit", "burst", f"{burst}kbit",
                                 "conform-exceed", "pass/continue"], timeout=5)
                success = success and ok
    return success


def _update_qos_script(data):
    script_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
    script_path = os.path.join(script_dir, "qos-setup.sh")
    bootstrap = """#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${AEGIS_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$APP_DIR"
exec /usr/bin/python3 - <<'PY'
from modules.qos import _load, apply_profile

data = _load()
if not data.get("enabled", True):
    from modules.qos import _disable_qos_runtime
    ok, message = _disable_qos_runtime(data)
    print(message)
    raise SystemExit(0 if ok else 1)
ok, message = apply_profile(data.get("active_profile", "gaming"))
print(message)
raise SystemExit(0 if ok else 1)
PY
"""
    with open(script_path, "w") as f:
        f.write(bootstrap)
    os.chmod(script_path, 0o755)
    return
    if not data.get("enabled", True):
        with open(script_path, "w") as f:
            f.write("#!/bin/bash\n# QoS disabled\n")
        return
    wan, lan, vpn = _detect_ifaces()
    qos_ifaces = _get_qos_ifaces(data=data)
    lines = ["#!/bin/bash"]
    for iface in qos_ifaces:
        lines.append(f"ethtool -K {iface} gro on 2>/dev/null")
    nproc_ok, nproc_out, _ = _run(["nproc"])
    cpu_mask = "f"
    if nproc_ok and nproc_out:
        try:
            n = int(nproc_out.strip())
            cpu_mask = format((1 << n) - 1, 'x')
        except ValueError:
            pass
    for iface in qos_ifaces:
        lines.append(f"echo {cpu_mask} > /sys/class/net/{iface}/queues/rx-0/rps_cpus 2>/dev/null")
    profile_id = data.get("active_profile", "gaming")
    profiles = data.get("profiles", {})
    profile = profiles.get(profile_id, DEFAULT_PROFILES.get(profile_id, DEFAULT_PROFILES["gaming"]))
    algorithm = profile.get("algorithm", "cake")
    dl = profile.get("download_mbit", 20)
    ul = profile.get("upload_mbit", 18)
    if algorithm == "cake":
        diffserv = profile.get("cake_diffserv", "besteffort")
        priorities = profile.get("priorities", {})
        has_custom_prio = any(v != "normal" for v in priorities.values()) if priorities else False
        if diffserv == "besteffort" and has_custom_prio:
            diffserv = "diffserv4"
        wan_flow = profile.get("cake_flowmode", "dual-dsthost")
        lan_flow = "dual-srchost" if "dual-dsthost" in wan_flow else ("srchost" if "dsthost" in wan_flow else wan_flow)
        nat = "nat" if profile.get("cake_nat", True) else "nonat"
        overhead = profile.get("cake_overhead", 38)
        rtt = profile.get("cake_rtt", "100ms")
        wash = "wash" if profile.get("cake_wash", False) else "nowash"
        ack = "ack-filter" if profile.get("cake_ack_filter", False) else "no-ack-filter"
        split_gso = "split-gso" if profile.get("cake_split_gso", True) else "no-split-gso"
        for iface in qos_ifaces:
            is_lan = iface != wan
            flow = lan_flow if is_lan else wan_flow
            bw = dl if is_lan else ul
            lines.append(f"tc qdisc del dev {iface} root 2>/dev/null")
            lines.append(f"for i in 1 2 3 4 5 6 7 8; do tc qdisc del dev {iface} parent 1:$i 2>/dev/null; done")
            lines.append(f"tc qdisc add dev {iface} root handle 1: cake bandwidth {bw}Mbit ethernet {diffserv} {flow} {nat} {wash} {ack} {split_gso} rtt {rtt} noatm overhead {overhead} mpu 84 2>/dev/null || tc qdisc replace dev {iface} root handle 1: cake bandwidth {bw}Mbit ethernet {diffserv} {flow} {nat} {wash} {ack} {split_gso} rtt {rtt} noatm overhead {overhead} mpu 84 2>/dev/null")
    elif algorithm == "fq_codel":
        limit = profile.get("fq_codel_limit", 1024)
        target = profile.get("fq_codel_target", "5ms")
        interval = profile.get("fq_codel_interval", "100ms")
        flows = profile.get("fq_codel_flows", 1024)
        ecn = "ecn" if profile.get("fq_codel_ecn", True) else "noecn"
        memory = profile.get("fq_codel_memory", "32Mb")
        for iface in qos_ifaces:
            lines.append(f"tc qdisc del dev {iface} root 2>/dev/null")
            lines.append(f"tc qdisc add dev {iface} root handle 1: fq_codel limit {limit} target {target} interval {interval} flows {flows} quantum 1514 {ecn} memory_limit {memory} 2>/dev/null || tc qdisc replace dev {iface} root handle 1: fq_codel limit {limit} target {target} interval {interval} flows {flows} quantum 1514 {ecn} memory_limit {memory} 2>/dev/null")
    elif algorithm == "htb":
        for iface in qos_ifaces:
            is_lan = iface != wan
            bw = dl if is_lan else ul
            lines.append(f"tc qdisc del dev {iface} root 2>/dev/null")
            lines.append(f"tc qdisc add dev {iface} root handle 1: htb default 30 2>/dev/null || tc qdisc replace dev {iface} root handle 1: htb default 30 2>/dev/null")
            lines.append(f"tc class replace dev {iface} parent 1: classid 1:30 htb rate {bw}mbit ceil {bw}mbit burst 15kb cburst 1600b 2>/dev/null")
            lines.append(f"tc qdisc add dev {iface} parent 1:30 handle 30: fq_codel 2>/dev/null || tc qdisc replace dev {iface} parent 1:30 handle 30: fq_codel 2>/dev/null")
    elif algorithm == "hfsc":
        for iface in qos_ifaces:
            is_lan = iface != wan
            bw = dl if is_lan else ul
            lines.append(f"tc qdisc del dev {iface} root 2>/dev/null")
            lines.append(f"tc qdisc add dev {iface} root handle 1: hfsc default 30 2>/dev/null || tc qdisc replace dev {iface} root handle 1: hfsc default 30 2>/dev/null")
            lines.append(f"tc class replace dev {iface} parent 1: classid 1:30 hfsc sc rate {bw}mbit ul rate {bw}mbit 2>/dev/null")
    priorities = profile.get("priorities", {})
    has_marks = any(v != "normal" for v in priorities.values()) if priorities else False
    lines.append(f"{NFT} delete table inet {NFT_QOS_TABLE} 2>/dev/null")
    if has_marks:
        lines.append(f"cat <<'NFT_EOF' | {NFT} -f -")
        lines.append(f"table inet {NFT_QOS_TABLE} {{")
        lines.append("  chain mark_forward {")
        lines.append("    type filter hook forward priority mangle; policy accept;")
        for cls_key, prio_level in (priorities or {}).items():
            if prio_level == "normal":
                continue
            dscp = DSCP_MAP.get(prio_level, "cs0")
            cls = _get_class_config(profile, cls_key)
            ports = cls.get("ports", "")
            networks = cls.get("networks", "")
            protocols = cls.get("protocols", "")
            if ports:
                if protocols != "udp":
                    lines.append(f"    tcp dport {{ {ports} }} ip dscp set {dscp}")
                    lines.append(f"    tcp sport {{ {ports} }} ip dscp set {dscp}")
                lines.append(f"    udp dport {{ {ports} }} ip dscp set {dscp}")
                lines.append(f"    udp sport {{ {ports} }} ip dscp set {dscp}")
            if networks:
                for net in [n.strip() for n in networks.split(",") if n.strip()]:
                    lines.append(f"    ip daddr {net} ip dscp set {dscp}")
                    lines.append(f"    ip saddr {net} ip dscp set {dscp}")
        lines.append("  }")
        lines.append("}")
        lines.append("NFT_EOF")
    shaping_ifaces = [i for i in qos_ifaces if i != wan]
    rules = [r for r in data.get("manual_rules", []) if r.get("enabled", True)]
    for iface in shaping_ifaces:
        lines.append(f"tc filter del dev {iface} parent 1: 2>/dev/null")
    for idx, r in enumerate(rules):
        rtype = r.get("type", "")
        match_val = r.get("match", "").strip()
        bw = r.get("bandwidth_kbit", 0)
        try:
            bw = int(bw)
        except (ValueError, TypeError):
            bw = 0
        if bw <= 0 or not match_val:
            continue
        burst = max(bw * 2, 64)
        prio = 10 + idx * 10
        for iface in shaping_ifaces:
            if rtype == "port":
                try:
                    port_str = match_val.split("-")[0].strip()
                    port_num = int(port_str)
                    if not (0 <= port_num <= 65535):
                        continue
                    lines.append(f"tc filter add dev {iface} parent 1: protocol ip prio {prio} u32 match ip dport {port_num} 0xffff action police rate {bw}kbit burst {burst}kbit conform-exceed pass/continue 2>/dev/null")
                except (ValueError, IndexError):
                    continue
            elif rtype == "ip":
                if "/" in match_val:
                    lines.append(f"tc filter add dev {iface} parent 1: protocol ip prio {prio} u32 match ip dst {match_val} action police rate {bw}kbit burst {burst}kbit conform-exceed pass/continue 2>/dev/null")
                else:
                    lines.append(f"tc filter add dev {iface} parent 1: protocol ip prio {prio} u32 match ip dst {match_val}/32 action police rate {bw}kbit burst {burst}kbit conform-exceed pass/continue 2>/dev/null")
            elif rtype == "protocol":
                proto_map = {"tcp": "6", "udp": "17", "icmp": "1"}
                proto_num = proto_map.get(match_val.lower(), match_val)
                lines.append(f"tc filter add dev {iface} parent 1: protocol ip prio {prio} u32 match ip protocol {proto_num} 0xff action police rate {bw}kbit burst {burst}kbit conform-exceed pass/continue 2>/dev/null")
    with open(script_path, "w") as f:
        f.write("\n".join(lines + ["exit 0"]) + "\n")
    _run(["chmod", "+x", script_path])


def reset_profile(profile_id):
    if profile_id in DEFAULT_PROFILES:
        data = _load()
        previous_data = copy.deepcopy(data)
        data["profiles"][profile_id] = copy.deepcopy(DEFAULT_PROFILES[profile_id])
        _save(data)
        if data.get("active_profile") == profile_id and data.get("enabled", True):
            ok, message = apply_profile(profile_id)
            if not ok:
                _save(previous_data)
                rollback_ok, rollback_message = apply_profile(previous_data.get("active_profile", "gaming"))
                suffix = "" if rollback_ok else f"; rollback failed: {rollback_message}"
                return False, f"Profile reset failed: {message}{suffix}"
        return True, f"Profile '{profile_id}' reset to defaults"
    return False, f"Cannot reset: unknown profile"


def add_priority_class(class_key, label, ports="", networks="", protocols="", icon=""):
    import re
    if not re.match(r'^[a-z][a-z0-9_]*$', class_key):
        return False, "Key must be lowercase alphanumeric with underscores, start with letter"
    if class_key in PRIORITY_CLASSES:
        return False, f"Class '{class_key}' already exists as built-in"
    data = _load()
    custom = data.get("custom_priority_classes", {})
    if class_key in custom:
        return False, f"Custom class '{class_key}' already exists"
    custom[class_key] = {
        "label": label or class_key,
        "ports": ports,
        "networks": networks,
        "protocols": protocols,
        "icon": icon or "\U0001f4cc",
        "custom": True
    }
    data["custom_priority_classes"] = custom
    profile = data.get("profiles", {}).get(data.get("active_profile", "gaming"), {})
    if "priorities" not in profile:
        profile["priorities"] = {}
    profile.setdefault("priorities", {})[class_key] = "normal"
    data["profiles"][data.get("active_profile", "gaming")] = profile
    _save(data)
    if data.get("enabled", True):
        apply_profile(data.get("active_profile", "gaming"))
    return True, f"Class '{label}' added"


def remove_priority_class(class_key):
    data = _load()
    custom = data.get("custom_priority_classes", {})
    if class_key not in custom:
        return False, f"Custom class '{class_key}' not found"
    del custom[class_key]
    data["custom_priority_classes"] = custom
    for pid, profile in data.get("profiles", {}).items():
        profile.get("priorities", {}).pop(class_key, None)
        cc = profile.get("custom_classes", {})
        cc.pop(class_key, None)
    _save(data)
    if data.get("enabled", True):
        apply_profile(data.get("active_profile", "gaming"))
    return True, f"Class '{class_key}' removed"


def get_all_priority_classes():
    all_classes = dict(PRIORITY_CLASSES)
    data = _load()
    all_classes.update(data.get("custom_priority_classes", {}))
    return all_classes


def run_speedtest():
    ok, out, err = _run(["speedtest-cli", "--json"], timeout=90)
    if ok and out and "403" not in (err or ""):
        try:
            d = json.loads(out)
            dl = d.get("download", 0)
            ul = d.get("upload", 0)
            ping = d.get("ping", 0)
            srv = d.get("server", {})
            cli = d.get("client", {})
            return {"ok": True, "download_mbit": round(dl / 1e6, 2),
                    "upload_mbit": round(ul / 1e6, 2),
                    "ping_ms": round(ping, 1),
                    "server_name": srv.get("name", ""),
                    "server_sponsor": srv.get("sponsor", ""),
                    "server_country": srv.get("cc", ""),
                    "server_latency": round(srv.get("latency", 0), 1),
                    "client_ip": cli.get("ip", ""),
                    "client_isp": cli.get("isp", ""),
                    "bytes_sent": d.get("bytes_sent", 0),
                    "bytes_received": d.get("bytes_received", 0)}
        except (json.JSONDecodeError, ValueError):
            pass

    dl_sizes = [1_000_000, 5_000_000, 25_000_000, 50_000_000]
    ul_sizes = [500_000, 2_000_000, 5_000_000, 10_000_000]
    dl_speeds = []
    for sz in dl_sizes:
        ok, out, _ = _run(["curl", "-sL", "-o", "/dev/null", "-w",
            "%{speed_download}", f"https://speed.cloudflare.com/__down?bytes={sz}"], timeout=30)
        if ok and out:
            try:
                spd = float(out.strip())
                if spd > 0:
                    dl_speeds.append(spd)
            except (ValueError, TypeError):
                pass
        if len(dl_speeds) >= 2:
            break
    dl_mbit = 0
    if dl_speeds:
        avg = sum(dl_speeds[1:]) / len(dl_speeds[1:]) if len(dl_speeds) > 1 else dl_speeds[0]
        dl_mbit = round(avg * 8 / 1e6, 2)

    ul_speeds = []
    for sz in ul_sizes:
        tmp = f"/tmp/qos_ul_{sz}"
        try:
            with open(tmp, "wb") as f:
                f.write(os.urandom(sz))
        except Exception:
            continue
        ok, out, _ = _run(["curl", "-sL", "-X", "POST", "-H",
            "Content-Type: application/octet-stream", "-w",
            "%{speed_upload}", "--data-binary", f"@{tmp}",
            "https://speed.cloudflare.com/__up", "-o", "/dev/null"], timeout=30)
        try:
            os.unlink(tmp)
        except Exception:
            pass
        if ok and out:
            try:
                spd = float(out.strip())
                if spd > 0:
                    ul_speeds.append(spd)
            except (ValueError, TypeError):
                pass
        if len(ul_speeds) >= 2:
            break
    ul_mbit = 0
    if ul_speeds:
        avg = sum(ul_speeds[1:]) / len(ul_speeds[1:]) if len(ul_speeds) > 1 else ul_speeds[0]
        ul_mbit = round(avg * 8 / 1e6, 2)

    ping_ms = 0
    ok, out, _ = _run(["curl", "-sL", "-o", "/dev/null", "-w",
        "%{time_starttransfer}", f"https://speed.cloudflare.com/__down?bytes=0"], timeout=10)
    if ok and out:
        try:
            ping_ms = round(float(out.strip()) * 1000, 1)
        except (ValueError, TypeError):
            pass

    return {"ok": True, "download_mbit": dl_mbit, "upload_mbit": ul_mbit,
            "ping_ms": ping_ms,
            "server_name": "Cloudflare", "server_sponsor": "Cloudflare Edge",
            "server_country": "", "server_latency": ping_ms,
            "client_ip": "", "client_isp": "",
            "bytes_sent": sum(ul_sizes[:len(ul_speeds)]) if ul_speeds else 0,
            "bytes_received": sum(dl_sizes[:len(dl_speeds)]) if dl_speeds else 0,
            "note": "Measured via Cloudflare edge (speedtest-cli unavailable)"}
