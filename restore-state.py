#!/usr/bin/env python3
"""AegisGate state restore — reads saved state and reapplies everything at boot."""
import json
import os
import sys
import subprocess
import time

DATA_DIR = "/opt/nft-dashboard/data"
NFT = "/usr/sbin/nft"
TC = "/sbin/tc"
WG_QUICK = "/usr/bin/wg-quick"
IP = "/sbin/ip"

log_messages = []


def log(msg):
    print(f"[restore-state] {msg}")
    log_messages.append(msg)


def run(cmd, **kw):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, **kw)
        return r.returncode == 0, r.stdout, r.stderr
    except Exception as e:
        return False, "", str(e)


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def wait_for_interface(iface, timeout=60):
    for _ in range(timeout):
        ok, out, _ = run([IP, "-j", "addr", "show", iface])
        if ok and out:
            try:
                data = json.loads(out)
                if data and data[0].get("operstate") == "UP":
                    return True
            except Exception:
                pass
        time.sleep(1)
    return False


def restore_nftables():
    log("Applying fail-open nftables rules...")
    safe_restore = "/opt/nft-dashboard/scripts/safe-nft-restore.sh"
    if os.path.exists(safe_restore):
        ok, _, err = run([safe_restore])
    else:
        ok, _, err = run([NFT, "-f", "/etc/nftables.conf"])
    if ok:
        log("nftables rules applied")
    else:
        log(f"nftables FAILED: {err[:200]}")
    return ok


def _load_config():
    try:
        with open(os.path.join(DATA_DIR, "config.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def _get_ifaces_from_config():
    cfg = _load_config()
    wan_if = cfg.get("wan_interface", "eth0")
    lan_if = cfg.get("lan_interface", "eth1")
    wan_ip = cfg.get("wan_ip", "")
    lan_ip = cfg.get("lan_ip", "")
    lan_net = ""
    if lan_ip:
        parts = lan_ip.split(".")
        lan_net = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
    return wan_if, lan_if, wan_ip, lan_ip, lan_net


def _get_vpn_ifaces():
    ifaces = []
    try:
        r = subprocess.run(["ip", "-j", "link", "show"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            for i in json.loads(r.stdout):
                name = i.get("ifname", "")
                if name.startswith("wg") and name != "wg0":
                    ifaces.append(name)
    except Exception:
        pass
    try:
        wg_state = load_json(os.path.join(DATA_DIR, "wireguard", "state.json"))
        if wg_state and wg_state.get("server", {}).get("running"):
            iface = wg_state["server"].get("interface", "wg0")
            if iface not in ifaces:
                ifaces.insert(0, iface)
    except Exception:
        pass
    return ifaces if ifaces else ["wg0"]


def _get_vpn_net():
    try:
        wg_state = load_json(os.path.join(DATA_DIR, "wireguard", "state.json"))
        if wg_state and wg_state.get("server"):
            addr = wg_state["server"].get("address", "10.0.0.1/24")
            if "/" in addr:
                ip_part = addr.split("/")[0]
                prefix = addr.split("/")[1]
                octets = ip_part.split(".")
                if prefix == "24":
                    return f"{octets[0]}.{octets[1]}.{octets[2]}.0/{prefix}"
                return f"{octets[0]}.{octets[1]}.{octets[2]}.0/{prefix}"
            return "10.0.0.0/24"
    except Exception:
        pass
    return "10.0.0.0/24"


def restore_vpn_masquerade():
    log("Restoring VPN masquerade rules...")
    wan_if, lan_if, wan_ip, lan_ip, lan_net = _get_ifaces_from_config()
    vpn_net = _get_vpn_net()
    vpn_ifaces = _get_vpn_ifaces()
    rules = [
        ["ip", "saddr", vpn_net, "oifname", wan_if, "masquerade"],
        ["ip", "saddr", vpn_net, "oifname", lan_if, "masquerade"],
    ]
    if lan_net:
        for vpn_if in vpn_ifaces:
            rules.append(["ip", "saddr", lan_net, "oifname", vpn_if, "masquerade"])
    ok_list, existing, _ = run([NFT, "-a", "list", "chain", "ip", "nat", "POSTROUTING"])
    existing_norm = existing.replace('"', '') if ok_list else ""
    added = 0
    for r in rules:
        rule_norm = " ".join(r).replace('"', '')
        if rule_norm in existing_norm:
            continue
        ok, _, _ = run([NFT, "add", "rule", "ip", "nat", "POSTROUTING"] + r)
        if ok:
            added += 1
    log(f"VPN masquerade rules restored ({added} added, {len(rules) - added} already present)")


def restore_vpn_dnat():
    log("Mirroring DNAT rules for VPN interfaces...")
    wan_if, lan_if, wan_ip, lan_ip, lan_net = _get_ifaces_from_config()
    if not wan_ip:
        log("No WAN IP, skipping VPN DNAT mirror")
        return
    vpn_ifaces = _get_vpn_ifaces()
    ok, out, _ = run([NFT, "-a", "list", "chain", "ip", "nat", "PREROUTING"])
    if not ok:
        log("Cannot read PREROUTING chain, skipping VPN DNAT mirror")
        return
    mirrored = 0
    for line in out.splitlines():
        line = line.strip()
        if "dnat to" not in line:
            continue
        for wan_if_name in (wan_if, lan_if):
            if f'iifname "{wan_if_name}"' not in line:
                continue
            parts = line.split()
            proto = ""
            dport = ""
            target = ""
            dest_ip = ""
            for i, p in enumerate(parts):
                if p in ("tcp", "udp", "icmp"):
                    proto = p
                if p == "dport" and i + 1 < len(parts):
                    dport = parts[i + 1]
                if p == "to" and i + 1 < len(parts):
                    target = parts[i + 1]
                if p == "daddr" and i + 1 < len(parts):
                    dest_ip = parts[i + 1]
            if not dport or not target or not proto:
                continue
            already_mirrored = any(f'iifname "{vif}"' in line for vif in vpn_ifaces)
            if already_mirrored:
                continue
            for vpn_if in vpn_ifaces:
                already = False
                ok2, out2, _ = run([NFT, "-a", "list", "chain", "ip", "nat", "PREROUTING"])
                if ok2:
                    for el in out2.splitlines():
                        if f'iifname "{vpn_if}"' in el and f'{proto} dport {dport}' in el and "dnat to" in el:
                            already = True
                            break
                if already:
                    continue
                rule_parts = ['ip', 'daddr', wan_ip, 'iifname', vpn_if, proto, 'dport', dport, 'dnat', 'to', target]
                ok3, _, _ = run([NFT, "add", "rule", "ip", "nat", "PREROUTING"] + rule_parts)
                if ok3:
                    mirrored += 1
                    log(f"  VPN DNAT: {vpn_if} {proto}/{dport} -> {target}")
            break
    log(f"VPN DNAT mirror: {mirrored} rules added")


def restore_wg():
    state = load_json(os.path.join(DATA_DIR, "wireguard", "state.json"))
    if not state or not state.get("server"):
        log("WireGuard: no server config, skipping")
        return
    if not state["server"].get("running"):
        log("WireGuard: was stopped, not starting")
        return
    iface = state["server"].get("interface", "wg0")
    ok, out, _ = run([IP, "-j", "addr", "show", iface])
    if ok and out:
        try:
            data = json.loads(out)
            if data and data[0].get("operstate") in ("UP", "UNKNOWN"):
                log(f"WireGuard: {iface} already running, syncing peers...")
                run([sys.executable, "-c",
                     "from modules.wg_manager import _sync_all_peers, _apply_all_firewall_rules; "
                     "_sync_all_peers(); _apply_all_firewall_rules()"],
                    cwd="/opt/nft-dashboard")
                return
        except Exception:
            pass
    log(f"WireGuard: was running, starting {iface}...")
    wait_for_interface(state["server"].get("listen_if", "eth0"), timeout=30)
    ok, _, err = run([WG_QUICK, "up", iface])
    if ok:
        log(f"WireGuard {iface} started")
        run([sys.executable, "-c",
             "from modules.wg_manager import _sync_all_peers, _apply_all_firewall_rules, _open_wg_port, _add_wg_to_flowtable; "
             f"_sync_all_peers(); _apply_all_firewall_rules(); _open_wg_port({state['server'].get('listen_port', 51820)}); _add_wg_to_flowtable('{iface}')"],
            cwd="/opt/nft-dashboard")
    else:
        log(f"WireGuard FAILED: {err[:200]}")


def restore_qos():
    data = load_json(os.path.join(DATA_DIR, "qos.json"))
    if not data:
        log("QoS: no config, skipping")
        return
    if not data.get("enabled", True):
        log("QoS: was disabled, restoring fq_codel defaults")
        wan, lan = "eth0", "eth1"
        ifaces = load_json(os.path.join(DATA_DIR, "ifaces.json"))
        if ifaces:
            for name, icfg in ifaces.get("interfaces", {}).items():
                if icfg.get("role") == "wan":
                    wan = name
                elif icfg.get("role") == "lan":
                    lan = name
        run([TC, "qdisc", "replace", "dev", wan, "root", "fq_codel"])
        run([TC, "qdisc", "replace", "dev", lan, "root", "fq_codel"])
        return
    log(f"QoS: was enabled, applying profile '{data.get('active_profile', 'gaming')}'...")
    run([sys.executable, "-c",
         "from modules.qos import apply_profile, _load; d=_load(); apply_profile(d.get('active_profile','gaming'))"],
        cwd="/opt/nft-dashboard")


def restore_rules_state():
    state = load_json(os.path.join(DATA_DIR, "rules_state.json"))
    if not state:
        log("Rules state: no file, skipping")
        return
    log("Rules state: restoring toggle states...")
    run([sys.executable, "-c",
         "from modules.rules_ui import restore_all_rules; restore_all_rules()"],
        cwd="/opt/nft-dashboard")


def restore_policy():
    policy = load_json(os.path.join(DATA_DIR, "policy.json"))
    if not policy:
        log("Policy: no config, skipping")
        return
    mode = policy.get("mode", "balanced")
    log(f"Policy: restoring '{mode}'...")
    run([sys.executable, "-c",
         f"from modules.policy import set_policy; set_policy('{mode}', 'system-boot')"],
        cwd="/opt/nft-dashboard")


def restore_vlans():
    ifaces = load_json(os.path.join(DATA_DIR, "ifaces.json"))
    if not ifaces or not ifaces.get("vlans"):
        log("VLANs: none configured")
        return
    vlans = ifaces["vlans"]
    log(f"VLANs: restoring {len(vlans)} VLANs...")
    for vname, vcfg in vlans.items():
        parent = vcfg.get("parent", "")
        vid = vcfg.get("vlan_id", 0)
        ip_cidr = vcfg.get("ip_cidr", "")
        log(f"  VLAN {vname} (parent={parent}, id={vid})...")
        ok, _, _ = run([IP, "link", "add", "link", parent, "name", vname, "type", "vlan", "id", str(vid)])
        if ok or "already exists" in _:
            if ip_cidr:
                run([IP, "addr", "replace", ip_cidr, "dev", vname])
            run([IP, "link", "set", vname, "up"])
            log(f"  VLAN {vname} UP")
        else:
            log(f"  VLAN {vname} FAILED")


def restore_gro_rps():
    log("Applying GRO/RPS optimizations...")
    ifaces = load_json(os.path.join(DATA_DIR, "ifaces.json"))
    if not ifaces:
        return
    for name, icfg in ifaces.get("interfaces", {}).items():
        if icfg.get("role") in ("wan", "lan") and icfg.get("enabled", True):
            run(["ethtool", "-K", name, "gro", "on"])
            rps_path = f"/sys/class/net/{name}/queues/rx-0/rps_cpus"
            if os.path.exists(rps_path):
                try:
                    with open(rps_path, "w") as f:
                        f.write("f")
                except Exception:
                    pass


def restore_auto_ban_config():
    cfg = load_json(os.path.join(DATA_DIR, "auto-ban-config.json"))
    if not cfg:
        return
    log(f"Auto-ban config: thresholds ssh={cfg.get('ssh_threshold',5)}, scan={cfg.get('port_scan_threshold',8)}, syn={cfg.get('syn_flood_threshold',10)}, ttl={cfg.get('blacklist_ttl','24h')}")


def restore_crowdsec():
    log("Checking CrowdSec bouncer...")
    ok, _, _ = run(["systemctl", "is-active", "--quiet", "crowdsec-firewall-bouncer"])
    if not ok:
        run(["systemctl", "start", "crowdsec-firewall-bouncer"])
        log("CrowdSec bouncer started")
    else:
        run(["systemctl", "restart", "crowdsec-firewall-bouncer"])
        log("CrowdSec bouncer restarted to repopulate nft sets")


def restore_suricata():
    log("Checking Suricata...")
    ok, _, _ = run(["systemctl", "is-active", "--quiet", "suricata"])
    NFT = "/usr/sbin/nft"
    wan_if, _, _, _, _ = _get_ifaces_from_config()

    r = subprocess.run([NFT, "-a", "list", "chain", "inet", "filter", "input"],
                       capture_output=True, text=True, timeout=10)
    queue_lines = [l for l in r.stdout.splitlines() if "queue" in l and "handle" in l]

    if not ok:
        log("Suricata not running — removing NFQ rule if present")
        for line in queue_lines:
            h = line.rsplit("handle", 1)[-1].strip().split()[0]
            subprocess.run([NFT, "delete", "rule", "inet", "filter", "input", "handle", h],
                          capture_output=True, text=True, timeout=10)
        return

    log("Suricata active (IPS/NFQ mode)")
    if not queue_lines:
        subprocess.run([NFT, "insert", "rule", "inet", "filter", "input",
                        "iifname", wan_if, "ct", "state", "new",
                        "queue", "num", "0"],
                       capture_output=True, text=True, timeout=10)
        log("NFQ rule added")
    else:
        log("NFQ rule already present")


def restore_dns_dhcp():
    log("Restoring DNS/DHCP state...")
    dns_settings = load_json(os.path.join(DATA_DIR, "dns_settings.json"))
    if not dns_settings:
        dns_settings = {}
    dns_enabled = dns_settings.get("dns_enabled", True)
    dhcp_enabled = dns_settings.get("dhcp_enabled", False)

    wan_if, lan_if, wan_ip, lan_ip, lan_net = _get_ifaces_from_config()

    if dns_enabled:
        ok, _, _ = run(["systemctl", "is-active", "--quiet", "dnsmasq"])
        if ok:
            log("dnsmasq already running")
        else:
            ok, out, err = run([sys.executable, "-c",
                "from modules.dns import apply_config; apply_config()"],
                cwd="/opt/nft-dashboard")
            if ok:
                log("dnsmasq config generated and started")
            else:
                log(f"dnsmasq start failed: {err[:200]}")

        gateway_ip = lan_ip or dns_settings.get("dns_listen_addr", "192.168.1.1")
        lan_ifaces = [lan_if] if lan_if else ["eth1"]
        ok, out, err = run([sys.executable, "-c",
            f"from modules.dns_nft import setup_dns_redirect; "
            f"setup_dns_redirect('{wan_if}', {lan_ifaces!r}, gateway_ip='{gateway_ip}')"],
            cwd="/opt/nft-dashboard")
        if ok:
            log("DNS nft redirect applied")
        else:
            log(f"DNS nft redirect failed: {err[:200]}")
    else:
        log("DNS disabled, skipping dnsmasq")

    if dhcp_enabled:
        log("DHCP enabled — dnsmasq handles both DNS and DHCP")
    else:
        log("DHCP disabled")


def restore_ip_blocklists():
    log("Restoring IP Blocklists...")
    try:
        from modules.ip_blocklists import ensure_nft_sets, ensure_nft_rules, init_db, get_custom_entries
        init_db()
        ensure_nft_sets()
        ensure_nft_rules()
        entries = get_custom_entries()
        NFT = "/usr/sbin/nft"
        for e in entries:
            set_name = "ipbl_ipv6" if ":" in e["ip_or_cidr"] else "ipbl_ipv4"
            try:
                subprocess.run([NFT, "add", "element", "inet", "filter", set_name,
                               f"{{ {e['ip_or_cidr']} }}"], capture_output=True, text=True, timeout=10)
            except Exception:
                pass
        log(f"IP Blocklists restored: {len(entries)} custom entries, nft rules ensured")
    except Exception as e:
        log(f"IP Blocklists restore failed: {e}")


def main():
    log("=== AegisGate State Restore ===")

    log("Step 1: Waiting for network interfaces...")
    wan_if = "eth0"
    ifaces = load_json(os.path.join(DATA_DIR, "ifaces.json"))
    if ifaces:
        for name, icfg in ifaces.get("interfaces", {}).items():
            if icfg.get("role") == "wan":
                wan_if = name
                break
    wait_for_interface(wan_if, timeout=90)
    time.sleep(2)

    log("Step 2: Restoring nftables rules...")
    restore_nftables()

    log("Step 3: Restoring WireGuard ACL rules...")
    ok, _, _ = run([sys.executable, "-c",
        "from modules.wg_manager import _apply_all_firewall_rules; _apply_all_firewall_rules()"],
        cwd="/opt/nft-dashboard")

    log("Step 4: Restoring VLANs...")
    restore_vlans()

    log("Step 5: Restoring QoS...")
    restore_qos()

    log("Step 6: Applying GRO/RPS...")
    restore_gro_rps()

    log("Step 7: Restoring policy (rules state)...")
    restore_rules_state()
    restore_policy()

    log("Step 8: Restoring CrowdSec/Suricata...")
    restore_crowdsec()
    restore_suricata()

    log("Step 9: Restoring WireGuard (if was running)...")
    restore_wg()

    log("Step 10: Restoring VPN masquerade...")
    restore_vpn_masquerade()

    log("Step 11: Mirroring DNAT for VPN...")
    restore_vpn_dnat()

    log("Step 12: Restoring DNS/DHCP...")
    restore_dns_dhcp()

    log("Step 13: Restoring IP Blocklists...")
    restore_ip_blocklists()

    log("=== AegisGate State Restore Complete ===")


if __name__ == "__main__":
    main()
