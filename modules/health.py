import os
import json
import shutil
import time

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "config.json")
HEALTH_STATUS_FILE = "/run/aegisgate/health.json"

def _load_net_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def get_cpu_usage(sample_interval=1.0):
    try:
        with open("/proc/stat", "r") as f:
            line1 = f.readline()
        vals1 = list(map(int, line1.split()[1:]))
        idle1 = vals1[3]
        total1 = sum(vals1)
        time.sleep(sample_interval)
        with open("/proc/stat", "r") as f:
            line2 = f.readline()
        vals2 = list(map(int, line2.split()[1:]))
        idle2 = vals2[3]
        total2 = sum(vals2)
        idle_diff = idle2 - idle1
        total_diff = total2 - total1
        if total_diff == 0:
            return 0.0
        usage = (1.0 - idle_diff / total_diff) * 100.0
        return round(usage, 1)
    except Exception:
        return 0.0


def get_load_avg():
    try:
        return tuple(round(x, 2) for x in os.getloadavg())
    except Exception:
        return (0.0, 0.0, 0.0)


def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        try:
            result = __import__("subprocess").run(
                ["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=5
            )
            m = __import__("re").search(r"temp=([\d.]+)'C", result.stdout)
            if m:
                return float(m.group(1))
        except Exception:
            pass
    return 0.0


def get_memory():
    info = {}
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    val = int(parts[1])
                    info[key] = val
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", 0)
        free = info.get("MemFree", 0)
        buffers = info.get("Buffers", 0)
        cached = info.get("Cached", 0)
        swap_total = info.get("SwapTotal", 0)
        swap_free = info.get("SwapFree", 0)
        used = total - available
        pct = round(used / total * 100, 1) if total else 0
        return {
            "total_mb": round(total / 1024, 1),
            "used_mb": round(used / 1024, 1),
            "available_mb": round(available / 1024, 1),
            "pct": pct,
            "swap_total_mb": round(swap_total / 1024, 1),
            "swap_used_mb": round((swap_total - swap_free) / 1024, 1),
            "swap_pct": round((swap_total - swap_free) / swap_total * 100, 1) if swap_total else 0,
        }
    except Exception:
        return {"total_mb": 0, "used_mb": 0, "available_mb": 0, "pct": 0,
                "swap_total_mb": 0, "swap_used_mb": 0, "swap_pct": 0}


def get_disk():
    result = {}
    for path in ["/", "/var/log/ram"]:
        try:
            usage = shutil.disk_usage(path)
            pct = round(usage.used / usage.total * 100, 1)
            result[path] = {
                "total_gb": round(usage.total / (1024**3), 2),
                "used_gb": round(usage.used / (1024**3), 2),
                "free_gb": round(usage.free / (1024**3), 2),
                "pct": pct,
            }
        except Exception:
            result[path] = {"total_gb": 0, "used_gb": 0, "free_gb": 0, "pct": 0}
    return result


def get_network():
    ifaces = {}
    try:
        with open("/proc/net/dev", "r") as f:
            lines = f.readlines()[2:]
        for line in lines:
            parts = line.strip().split(":")
            if len(parts) != 2:
                continue
            iface = parts[0].strip()
            if iface == "lo":
                continue
            stats = parts[1].split()
            if len(stats) < 16:
                continue
            _cfg = _load_net_config()
            _wan = _cfg.get("wan_interface", "eth0")
            _lan = _cfg.get("lan_interface", "eth1")
            iface_type = "WAN" if iface == _wan else ("LAN" if iface == _lan else iface)
            ifaces[iface] = {
                "type": iface_type,
                "rx_bytes": int(stats[0]),
                "rx_packets": int(stats[1]),
                "rx_errors": int(stats[2]),
                "rx_drops": int(stats[3]),
                "tx_bytes": int(stats[8]),
                "tx_packets": int(stats[9]),
                "tx_errors": int(stats[10]),
                "tx_drops": int(stats[11]),
            }
    except Exception:
        pass
    return ifaces


def get_conntrack():
    try:
        with open("/proc/sys/net/netfilter/nf_conntrack_count", "r") as f:
            count = int(f.read().strip())
        with open("/proc/sys/net/netfilter/nf_conntrack_max", "r") as f:
            limit = int(f.read().strip())
        pct = round(count / limit * 100, 1) if limit else 0
        return {"count": count, "limit": limit, "pct": pct}
    except Exception:
        return {"count": 0, "limit": 0, "pct": 0}


def get_uptime():
    try:
        with open("/proc/uptime", "r") as f:
            secs = float(f.read().split()[0])
        days = int(secs // 86400)
        hours = int((secs % 86400) // 3600)
        mins = int((secs % 3600) // 60)
        return f"{days}d {hours}h {mins}m"
    except Exception:
        return "unknown"


def get_services(recovery=None):
    from modules.nft_utils import svc_status, svc_uptime
    svc_names = [
        "nftables", "dnsmasq", "wg-quick@wg0", "nft-dashboard",
        "aegisgate-health", "qos-setup", "crowdsec",
        "crowdsec-firewall-bouncer", "suricata", "ssh",
    ]
    expected_checks = {
        "dnsmasq": "dns",
        "wg-quick@wg0": "wireguard",
        "qos-setup": "qos",
    }
    checks = (recovery or {}).get("checks", {})
    result = []
    for name in svc_names:
        check = checks.get(expected_checks.get(name, ""), {})
        expected = check.get("expected", True)
        st = svc_status(name) if expected else "disabled"
        up = svc_uptime(name) if st == "active" else ""
        result.append({"name": name, "status": st, "uptime": up, "expected": expected})
    return result


def get_recovery_status():
    try:
        with open(HEALTH_STATUS_FILE) as handle:
            data = json.load(handle)
        checked_at = int(data.get("checked_at", 0))
        data["stale"] = not checked_at or time.time() - checked_at > 120
        return data
    except (OSError, ValueError, TypeError):
        return {"overall": "unavailable", "checked_at": 0, "checks": {}, "actions": [], "stale": True}


def get_alert_level(value, warn_threshold, crit_threshold):
    if value >= crit_threshold:
        return "critical"
    elif value >= warn_threshold:
        return "warning"
    return "ok"


def get_health():
    cpu_usage = get_cpu_usage(0.3)
    mem = get_memory()
    disk = get_disk()
    net = get_network()
    ct = get_conntrack()
    cpu_temp = get_cpu_temp()
    load = get_load_avg()
    uptime = get_uptime()
    recovery = get_recovery_status()
    services = get_services(recovery)
    from modules.suricata import get_suricata_rules, get_suricata_mode
    suricata_rules = get_suricata_rules()
    suricata_mode = get_suricata_mode()

    cpu_temp_alert = get_alert_level(cpu_temp, 70, 80)
    cpu_usage_alert = get_alert_level(cpu_usage, 80, 95)
    mem_alert = get_alert_level(mem["pct"], 85, 95)
    disk_alert = get_alert_level(disk.get("/", {}).get("pct", 0), 85, 95)
    ct_alert = get_alert_level(ct["pct"], 70, 90)

    net_alerts = {}
    for iface, data in net.items():
        drops = data.get("rx_drops", 0) + data.get("tx_drops", 0)
        errors = data.get("rx_errors", 0) + data.get("tx_errors", 0)
        net_alerts[iface] = get_alert_level(drops, 1, 100)

    svc_alerts = {}
    for svc in services:
        svc_alerts[svc["name"]] = "ok" if svc["status"] in ("active", "disabled") else "critical"

    return {
        "cpu_usage": cpu_usage,
        "cpu_temp": cpu_temp,
        "cpu_alert": cpu_temp_alert,
        "cpu_usage_alert": cpu_usage_alert,
        "load": load,
        "memory": mem,
        "mem_alert": mem_alert,
        "disk": disk,
        "disk_alert": disk_alert,
        "network": net,
        "net_alerts": net_alerts,
        "conntrack": ct,
        "ct_alert": ct_alert,
        "uptime": uptime,
        "services": services,
        "recovery": recovery,
        "svc_alerts": svc_alerts,
        "suricata_rules": suricata_rules,
        "suricata_mode": suricata_mode,
    }
