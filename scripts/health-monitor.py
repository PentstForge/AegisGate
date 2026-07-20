#!/usr/bin/env python3
"""AegisGate runtime watchdog with bounded, state-aware recovery."""

import json
import os
import random
import signal
import socket
import sqlite3
import struct
import subprocess
import time
import urllib.parse
import urllib.request


APP_DIR = os.environ.get("AEGIS_APP_DIR", "/opt/nft-dashboard")
STATUS_DIR = "/run/aegisgate"
STATUS_FILE = os.path.join(STATUS_DIR, "health.json")
CONFIG_FILE = os.path.join(APP_DIR, "data", "config.json")
DNS_SETTINGS_FILE = os.path.join(APP_DIR, "data", "dns_settings.json")
WG_STATE_FILE = os.path.join(APP_DIR, "data", "wireguard", "state.json")
QOS_STATE_FILE = os.path.join(APP_DIR, "data", "qos.json")

SYSTEMCTL = "/usr/bin/systemctl"
WG = "/usr/bin/wg"
WG_QUICK = "/usr/bin/wg-quick"
NFT = "/usr/sbin/nft"
TC = "/usr/sbin/tc"
IP = "/usr/sbin/ip"

INTERVAL = max(10, int(os.environ.get("AEGIS_HEALTH_INTERVAL", "30")))
FAILURE_THRESHOLD = max(1, int(os.environ.get("AEGIS_HEALTH_FAILURE_THRESHOLD", "2")))
RECOVERY_COOLDOWN = max(60, int(os.environ.get("AEGIS_HEALTH_RECOVERY_COOLDOWN", "300")))

STOP = False
FAILURES = {}
LAST_RECOVERY = {}
LAST_ALERT_STATE = {}


def log(message):
    print(f"[health-monitor] {message}", flush=True)


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return {} if default is None else default


def load_dns_settings():
    settings = load_json(DNS_SETTINGS_FILE, {"dns_enabled": True, "dhcp_enabled": False})
    db_path = os.path.join(APP_DIR, "data", "dns.db")
    try:
        uri = f"file:{db_path}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as connection:
            rows = connection.execute(
                "SELECT key, value FROM dns_settings WHERE key IN ('dns_enabled', 'dhcp_enabled')"
            ).fetchall()
        for key, value in rows:
            try:
                settings[key] = json.loads(value)
            except (ValueError, TypeError):
                settings[key] = str(value).lower() in ("1", "true", "yes", "on")
    except (OSError, sqlite3.Error):
        pass
    return settings


def run(command, timeout=10):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        output = (result.stderr or result.stdout or "").strip()
        return result.returncode == 0, output
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def service_active(service):
    ok, output = run([SYSTEMCTL, "is-active", "--quiet", service], timeout=5)
    return ok, output or ("active" if ok else "inactive")


def dns_probe(address="127.0.0.1", port=53):
    query_id = random.randint(0, 65535)
    name = b"\x0caegis-health\x07invalid\x00"
    packet = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0) + name + struct.pack("!HH", 1, 1)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(3)
            sock.sendto(packet, (address, port))
            response, _ = sock.recvfrom(512)
        if len(response) < 12:
            return False, "short DNS response"
        response_id, flags = struct.unpack("!HH", response[:4])
        if response_id != query_id or not flags & 0x8000:
            return False, "invalid DNS response"
        return True, "DNS query answered"
    except OSError as exc:
        return False, str(exc)


def udp_port_listening(port):
    expected = f"{port:04X}"
    for path in ("/proc/net/udp", "/proc/net/udp6"):
        try:
            with open(path, encoding="ascii") as handle:
                for line in handle:
                    fields = line.split()
                    if len(fields) > 3 and fields[1].rsplit(":", 1)[-1].upper() == expected:
                        return True
        except OSError:
            continue
    return False


def interface_has_ip(interface, expected_ip):
    ok, output = run([IP, "-4", "-o", "address", "show", "dev", interface], timeout=5)
    if not ok:
        return False, output
    if expected_ip and expected_ip not in output:
        return False, f"{expected_ip} missing on {interface}"
    return True, f"{interface} address present"


def check_all():
    config = load_json(CONFIG_FILE)
    dns_settings = load_dns_settings()
    wg_state = load_json(WG_STATE_FILE)
    checks = {}

    dashboard_ok, dashboard_detail = service_active("nft-dashboard.service")
    checks["dashboard"] = {"ok": dashboard_ok, "detail": dashboard_detail}

    dns_expected = bool(dns_settings.get("dns_enabled", True))
    dns_service_ok, dns_service_detail = service_active("dnsmasq.service")
    if dns_expected and dns_service_ok:
        dns_ok, dns_detail = dns_probe()
    elif dns_expected:
        dns_ok, dns_detail = False, dns_service_detail
    else:
        dns_ok = not dns_service_ok
        dns_detail = "disabled and stopped" if dns_ok else "unexpectedly active while disabled"
    checks["dns"] = {"ok": dns_ok, "detail": dns_detail, "expected": dns_expected}

    dhcp_expected = dns_expected and bool(dns_settings.get("dhcp_enabled", False))
    dhcp_ok = not dhcp_expected or (dns_ok and udp_port_listening(67))
    checks["dhcp"] = {
        "ok": dhcp_ok,
        "detail": "UDP/67 listening" if dhcp_expected and dhcp_ok else
                  ("disabled by configuration" if not dhcp_expected else "UDP/67 not listening"),
        "expected": dhcp_expected,
    }

    wg_server = wg_state.get("server") or {}
    wg_expected = bool(wg_server.get("running", False))
    wg_iface = wg_server.get("interface", "wg0")
    wg_active, wg_output = run([WG, "show", wg_iface], timeout=5)
    if wg_expected:
        wg_ok, wg_detail = wg_active, wg_output
    else:
        wg_ok = not wg_active
        wg_detail = "disabled and stopped" if wg_ok else "unexpectedly active while disabled"
    checks["wireguard"] = {
        "ok": wg_ok,
        "detail": "interface present" if wg_expected and wg_ok else wg_detail,
        "expected": wg_expected,
        "interface": wg_iface,
    }

    qos_state = load_json(QOS_STATE_FILE)
    qos_expected = bool(qos_state.get("enabled", False))
    qos_ok, qos_detail = service_active("qos-setup.service") if qos_expected else (True, "disabled by configuration")
    if qos_expected and qos_ok:
        profile = (qos_state.get("profiles") or {}).get(qos_state.get("active_profile", "gaming"), {})
        algorithm = profile.get("algorithm", "cake")
        qos_ifaces = []
        if profile.get("qos_wan", True):
            qos_ifaces.append(config.get("wan_interface", "eth0"))
        if profile.get("qos_lan", True):
            qos_ifaces.append(config.get("lan_interface", "eth1"))
        qos_ifaces.extend(profile.get("qos_vpns", [wg_iface] if wg_expected else []))
        for interface in qos_ifaces:
            if not os.path.exists(f"/sys/class/net/{interface}"):
                continue
            qdisc_ok, qdisc_output = run([TC, "qdisc", "show", "dev", interface], timeout=5)
            if not qdisc_ok or f"qdisc {algorithm}" not in qdisc_output:
                qos_ok = False
                qos_detail = f"{algorithm} qdisc missing on {interface}"
                break
        else:
            qos_detail = f"{algorithm} qdisc active"
    elif not qos_expected:
        stale_algorithms = ("cake", "htb", "hfsc", "tbf")
        for interface in (config.get("wan_interface", "eth0"), config.get("lan_interface", "eth1"), wg_iface):
            if not os.path.exists(f"/sys/class/net/{interface}"):
                continue
            _, qdisc_output = run([TC, "qdisc", "show", "dev", interface], timeout=5)
            stale_algorithm = next((name for name in stale_algorithms if f"qdisc {name}" in qdisc_output), None)
            if stale_algorithm:
                qos_ok = False
                qos_detail = f"stale {stale_algorithm} qdisc on {interface}"
                break
    checks["qos"] = {"ok": qos_ok, "detail": qos_detail, "expected": qos_expected}

    filter_ok, filter_detail = run([NFT, "list", "table", "inet", "filter"], timeout=8)
    nat_ok, nat_detail = run([NFT, "list", "table", "ip", "nat"], timeout=8)
    nft_ok = filter_ok and nat_ok
    nft_detail = "inet filter and ip nat present" if nft_ok else (filter_detail if not filter_ok else nat_detail)
    checks["nftables"] = {"ok": nft_ok, "detail": nft_detail}

    for role, iface_key, ip_key in (("wan", "wan_interface", "wan_ip"), ("lan", "lan_interface", "lan_ip")):
        interface = config.get(iface_key, "eth0" if role == "wan" else "eth1")
        ok, detail = interface_has_ip(interface, config.get(ip_key, ""))
        checks[role] = {"ok": ok, "detail": detail, "interface": interface}

    return checks


def restart_service(check_name, service):
    now = time.time()
    if now - LAST_RECOVERY.get(check_name, 0) < RECOVERY_COOLDOWN:
        return None
    if service == "dnsmasq.service":
        run([SYSTEMCTL, "enable", service], timeout=15)
        ok, detail = run([SYSTEMCTL, "restart", service], timeout=45)
    elif service.startswith("wg-quick@"):
        run([SYSTEMCTL, "stop", service], timeout=30)
        run([SYSTEMCTL, "reset-failed", service], timeout=10)
        ok, detail = run([SYSTEMCTL, "start", service], timeout=45)
    else:
        ok, detail = run([SYSTEMCTL, "restart", service], timeout=45)
    LAST_RECOVERY[check_name] = now
    action = {"check": check_name, "service": service, "ok": ok, "detail": detail, "ts": int(now)}
    log(f"recovery {service}: {'ok' if ok else 'failed'} {detail}")
    return action


def recover(checks):
    actions = []
    for name, result in checks.items():
        FAILURES[name] = 0 if result["ok"] else FAILURES.get(name, 0) + 1

    if FAILURES.get("dashboard", 0) >= FAILURE_THRESHOLD:
        action = restart_service("dashboard", "nft-dashboard.service")
        if action:
            actions.append(action)

    if max(FAILURES.get("dns", 0), FAILURES.get("dhcp", 0)) >= FAILURE_THRESHOLD:
        if checks.get("dns", {}).get("expected", True):
            action = restart_service("dns", "dnsmasq.service")
        elif time.time() - LAST_RECOVERY.get("dns", 0) < RECOVERY_COOLDOWN:
            action = None
        else:
            ok, detail = run([SYSTEMCTL, "stop", "dnsmasq.service"], timeout=30)
            LAST_RECOVERY["dns"] = time.time()
            action = {"check": "dns", "service": "dnsmasq.service", "ok": ok, "detail": detail, "ts": int(time.time())}
        if action:
            actions.append(action)
            LAST_RECOVERY["dhcp"] = LAST_RECOVERY["dns"]

    wg = checks.get("wireguard", {})
    if FAILURES.get("wireguard", 0) >= FAILURE_THRESHOLD:
        service = f"wg-quick@{wg.get('interface', 'wg0')}.service"
        if wg.get("expected"):
            action = restart_service("wireguard", service)
        elif time.time() - LAST_RECOVERY.get("wireguard", 0) < RECOVERY_COOLDOWN:
            action = None
        else:
            ok, detail = run([SYSTEMCTL, "disable", "--now", service], timeout=45)
            still_active, _ = run([WG, "show", wg.get("interface", "wg0")], timeout=5)
            if still_active:
                fallback_ok, fallback_detail = run([WG_QUICK, "down", wg.get("interface", "wg0")], timeout=30)
                ok = ok and fallback_ok
                detail = f"{detail}; wg-quick fallback: {fallback_detail}"
            LAST_RECOVERY["wireguard"] = time.time()
            action = {"check": "wireguard", "service": service, "ok": ok, "detail": detail, "ts": int(time.time())}
        if action:
            actions.append(action)

    qos = checks.get("qos", {})
    if FAILURES.get("qos", 0) >= FAILURE_THRESHOLD:
        action = restart_service("qos", "qos-setup.service")
        if action:
            actions.append(action)

    if FAILURES.get("nftables", 0) >= FAILURE_THRESHOLD:
        action = restart_service("nftables", "nftables.service")
        if action:
            actions.append(action)

    if max(FAILURES.get("wan", 0), FAILURES.get("lan", 0)) >= FAILURE_THRESHOLD:
        action = restart_service("network", "aegisgate-net-setup.service")
        if action:
            actions.append(action)
            LAST_RECOVERY["wan"] = LAST_RECOVERY["network"]
            LAST_RECOVERY["lan"] = LAST_RECOVERY["network"]

    return actions


def send_alert(checks, actions):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    changed = []
    for name, result in checks.items():
        state = bool(result["ok"])
        if name not in LAST_ALERT_STATE and not state:
            changed.append(f"{name}: failed ({result['detail']})")
        elif name in LAST_ALERT_STATE and LAST_ALERT_STATE[name] != state:
            changed.append(f"{name}: {'recovered' if state else 'failed'} ({result['detail']})")
        LAST_ALERT_STATE[name] = state
    for action in actions:
        changed.append(f"recovery {action['service']}: {'ok' if action['ok'] else 'failed'}")
    if not changed:
        return
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": "AegisGate health\n" + "\n".join(changed)}).encode()
    try:
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload)
        with urllib.request.urlopen(request, timeout=8):
            pass
    except OSError as exc:
        log(f"telegram alert failed: {exc}")


def write_status(checks, actions):
    os.makedirs(STATUS_DIR, mode=0o755, exist_ok=True)
    payload = {
        "checked_at": int(time.time()),
        "overall": "healthy" if all(item["ok"] for item in checks.values()) else "degraded",
        "checks": checks,
        "failures": dict(FAILURES),
        "actions": actions,
        "interval_seconds": INTERVAL,
    }
    temporary = STATUS_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.replace(temporary, STATUS_FILE)


def stop_handler(_signum, _frame):
    global STOP
    STOP = True


def main():
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    log(f"started interval={INTERVAL}s threshold={FAILURE_THRESHOLD} cooldown={RECOVERY_COOLDOWN}s")
    while not STOP:
        try:
            checks = check_all()
            actions = recover(checks)
            write_status(checks, actions)
            send_alert(checks, actions)
        except Exception as exc:
            log(f"check cycle failed: {exc}")
        for _ in range(INTERVAL):
            if STOP:
                break
            time.sleep(1)
    log("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
