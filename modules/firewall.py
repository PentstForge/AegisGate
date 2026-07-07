#!/usr/bin/env python3
import subprocess
import re
import json
import os
import copy

NFT = "/usr/sbin/nft"
NFT_CONF = "/etc/nftables.conf"
DMZ_IP = "172.24.1.204"
VPN_NET = "10.0.0.0/24"


def _get_ifaces():
    try:
        from modules.ifaces import get_wan, get_lan
        return get_wan(), get_lan()
    except Exception:
        return "eth0", "eth1"


def _get_wan_ip():
    wan = _get_ifaces()[0]
    try:
        import subprocess
        r = subprocess.run(["ip", "-j", "addr", "show", wan], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            for i in __import__("json").loads(r.stdout):
                for a in i.get("addr_info", []):
                    if a.get("family") == "inet" or (not a.get("family") and ":" not in a.get("local", "")):
                        return a.get("local", "")
    except Exception:
        pass
    return "31.172.140.234"


LAN_NET = "172.24.1.0/24"


def _run_nft(args, timeout=10):
    try:
        r = subprocess.run([NFT] + args, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", str(e).strip()


def get_nft_tables():
    ok, out, _ = _run_nft(["list", "ruleset"])
    if not ok:
        return []
    tables = []
    current_table = None
    current_chain = None
    for line in out.splitlines():
        stripped = line.strip()
        m = re.match(r'table\s+(\w+)\s+(\S+)\s*\{', stripped)
        if m:
            current_table = {"family": m.group(1), "name": m.group(2), "chains": [], "sets": []}
            continue
        if stripped == '}':
            if current_chain:
                current_table["chains"].append(current_chain) if current_table else None
                current_chain = None
            elif current_table:
                tables.append(current_table)
                current_table = None
            continue
        m2 = re.match(r'chain\s+(\S+)\s*\{', stripped)
        if m2 and current_table:
            if current_chain:
                current_table["chains"].append(current_chain)
            current_chain = {"name": m2.group(1), "rules": []}
            continue
        m3 = re.match(r'set\s+(\S+)\s*\{', stripped)
        if m3 and current_table:
            current_table["sets"].append(m3.group(1))
            current_chain = None
            continue
        if current_chain is not None and stripped and not stripped.startswith('#'):
            current_chain["rules"].append(stripped)
    return tables


def get_nat_rules():
    ok, out, _ = _run_nft(["-a", "list", "table", "ip", "nat"])
    if not ok:
        return {"prerouting": [], "postrouting": [], "input": [], "output": []}
    result = {"prerouting": [], "postrouting": [], "input": [], "output": []}
    current_chain = None
    for line in out.splitlines():
        stripped = line.strip()
        if "chain prerouting" in stripped.lower():
            current_chain = "prerouting"
            continue
        elif "chain postrouting" in stripped.lower():
            current_chain = "postrouting"
            continue
        elif "chain input" in stripped.lower():
            current_chain = "input"
            continue
        elif "chain output" in stripped.lower():
            current_chain = "output"
            continue
        elif stripped == "}" or stripped.startswith("table ") or stripped.startswith("chain "):
            if stripped.startswith("chain ") and "prerouting" not in stripped.lower() and "postrouting" not in stripped.lower() and "input" not in stripped.lower() and "output" not in stripped.lower():
                current_chain = None
            continue
        if current_chain and stripped and not stripped.startswith("#"):
            handle_m = re.search(r'handle\s+(\d+)', stripped)
            handle = handle_m.group(1) if handle_m else ""
            counter_m = re.search(r'counter\s+packets\s+(\d+)\s+bytes\s+(\d+)', stripped)
            packets = int(counter_m.group(1)) if counter_m else 0
            bytes_ = int(counter_m.group(2)) if counter_m else 0
            rule_type = "unknown"
            dest = ""
            iface = ""
            proto = ""
            dport = ""
            target_ip = ""
            target_port = ""
            if "dnat to" in stripped:
                rule_type = "dnat"
                dnat_m = re.search(r'dnat\s+to\s+(\S+)', stripped)
                if dnat_m:
                    target_ip = dnat_m.group(1)
                    if ":" in target_ip:
                        target_ip, target_port = target_ip.rsplit(":", 1)
                ip_m = re.search(r'ip\s+daddr\s+(\S+)', stripped)
                if ip_m:
                    dest = ip_m.group(1)
                iface_m = re.search(r'iifname\s+"(\S+)"', stripped)
                if iface_m:
                    iface = iface_m.group(1)
                proto_m = re.search(r'(tcp|udp)\s+dport\s+(\S+)', stripped)
                if proto_m:
                    proto = proto_m.group(1)
                    dport = proto_m.group(2).strip("{}")
            elif "masquerade" in stripped:
                rule_type = "masquerade"
                saddr_m = re.search(r'ip\s+saddr\s+(\S+)', stripped)
                if saddr_m:
                    dest = saddr_m.group(1)
                daddr_m = re.search(r'ip\s+daddr\s+(\S+)', stripped)
                if daddr_m:
                    target_ip = daddr_m.group(1)
                oiface_m = re.search(r'oifname\s+"(\S+)"', stripped)
                if oiface_m:
                    iface = oiface_m.group(1)
            elif "snat to" in stripped:
                rule_type = "snat"
                snat_m = re.search(r'snat\s+to\s+(\S+)', stripped)
                if snat_m:
                    target_ip = snat_m.group(1)
            rule = {
                "type": rule_type,
                "raw": stripped,
                "handle": handle,
                "iface": iface,
                "dest": dest,
                "proto": proto,
                "dport": dport,
                "target_ip": target_ip,
                "target_port": target_port,
                "packets": packets,
                "bytes": bytes_,
                "chain": current_chain,
            }
            result[current_chain].append(rule)
    return result


def get_filter_rules():
    ok, out, _ = _run_nft(["-a", "list", "chain", "inet", "filter", "input"])
    if not ok:
        return []
    rules = []
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("chain") or stripped.startswith("table") or stripped == "}":
            continue
        handle_m = re.search(r'handle\s+(\d+)', stripped)
        handle = handle_m.group(1) if handle_m else ""
        counter_m = re.search(r'counter\s+packets\s+(\d+)\s+bytes\s+(\d+)', stripped)
        packets = int(counter_m.group(1)) if counter_m else 0
        bytes_ = int(counter_m.group(2)) if counter_m else 0
        policy = ""
        if "policy drop" in stripped:
            policy = "drop"
        elif "policy accept" in stripped:
            policy = "accept"
        action = ""
        if " accept" in stripped or stripped.endswith("accept"):
            action = "accept"
        elif " drop" in stripped or stripped.endswith("drop"):
            action = "drop"
        elif " log " in stripped and "drop" in stripped:
            action = "log+drop"
        elif " jump " in stripped:
            action = "jump"
        elif " masquerade" in stripped:
            action = "masquerade"
        rule = {
            "raw": stripped,
            "handle": handle,
            "action": action,
            "policy": policy,
            "packets": packets,
            "bytes": bytes_,
        }
        if handle or policy:
            rules.append(rule)
    return rules


def get_filter_chain(chain_name):
    ok, out, _ = _run_nft(["-a", "list", "chain", "inet", "filter", chain_name])
    if not ok:
        return []
    rules = []
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("chain") or stripped.startswith("table") or stripped == "}":
            continue
        handle_m = re.search(r'handle\s+(\d+)', stripped)
        handle = handle_m.group(1) if handle_m else ""
        counter_m = re.search(r'counter\s+packets\s+(\d+)\s+bytes\s+(\d+)', stripped)
        packets = int(counter_m.group(1)) if counter_m else 0
        bytes_ = int(counter_m.group(2)) if counter_m else 0
        action = ""
        if " accept" in stripped:
            action = "accept"
        elif " drop" in stripped:
            action = "drop"
        elif " jump " in stripped:
            action = "jump"
        elif " masquerade" in stripped:
            action = "masquerade"
        elif " log " in stripped:
            action = "log"
        rules.append({
            "raw": stripped,
            "handle": handle,
            "action": action,
            "packets": packets,
            "bytes": bytes_,
        })
    return rules


def get_dnat_rules():
    nat = get_nat_rules()
    return [r for r in nat.get("prerouting", []) if r["type"] == "dnat"]


def get_masquerade_rules():
    nat = get_nat_rules()
    return [r for r in nat.get("postrouting", []) if r["type"] == "masquerade"]


def _get_vpn_ifaces():
    try:
        r = subprocess.run(["ip", "-j", "link", "show"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return [i["ifname"] for i in json.loads(r.stdout)
                    if i.get("ifname", "").startswith("wg") and i.get("operstate") in ("UP", "UNKNOWN")]
    except Exception:
        pass
    return []


def add_dnat_rule(proto, dport, target_ip, target_port=None, iface=None, dest_ip=None):
    WAN_IFACE, LAN_IFACE = _get_ifaces()
    if iface is None:
        iface = WAN_IFACE
    if dest_ip is None:
        dest_ip = _get_wan_ip()
    if target_port is None:
        target_port = dport
    port_spec = f"{proto} dport {dport}"
    rule_parts = [f'ip daddr {dest_ip}']
    if iface:
        rule_parts.append(f'iifname "{iface}"')
    rule_parts.append(port_spec)
    rule_parts.append(f'dnat to {target_ip}:{target_port}')
    rule_str = " ".join(rule_parts)
    ok, out, err = _run_nft(["add", "rule", "ip", "nat", "PREROUTING"] + rule_str.split())
    if ok:
        _save_dnat_to_config()
        if iface in (WAN_IFACE, LAN_IFACE):
            vpn_ifaces = _get_vpn_ifaces()
            for vpn_if in vpn_ifaces:
                vpn_parts = ['ip', 'daddr', dest_ip, 'iifname', vpn_if, proto, 'dport', str(dport), 'dnat', 'to', f'{target_ip}:{target_port}']
                vok, _, _ = _run_nft(["add", "rule", "ip", "nat", "PREROUTING"] + vpn_parts)
                if vok:
                    _save_dnat_to_config()
    return ok, f"DNAT rule added: {proto}/{dport} -> {target_ip}:{target_port}" if ok else f"Failed: {err}"


def remove_dnat_rule(handle):
    rule_info = None
    ok_list, out_list, _ = _run_nft(["-a", "list", "chain", "ip", "nat", "PREROUTING"])
    if ok_list:
        for line in out_list.splitlines():
            if f"handle {handle}" in line and "dnat to" in line:
                rule_info = line.strip()
                break
    ok, _, err = _run_nft(["delete", "rule", "ip", "nat", "PREROUTING", "handle", handle])
    if ok:
        if rule_info:
            parts = rule_info.split()
            proto = dport = target = ""
            for i, p in enumerate(parts):
                if p in ("tcp", "udp"):
                    proto = p
                if p == "dport" and i + 1 < len(parts):
                    dport = parts[i + 1]
                if p == "to" and i + 1 < len(parts):
                    target = parts[i + 1]
            if proto and dport:
                vpn_ifaces = _get_vpn_ifaces()
                vok, vout, _ = _run_nft(["-a", "list", "chain", "ip", "nat", "PREROUTING"])
                if vok:
                    for vline in vout.splitlines():
                        vline = vline.strip()
                        has_vpn = any(f'iifname "{vif}"' in vline for vif in vpn_ifaces)
                        vparts = vline.split()
                        dport_match = any(
                            vparts[i] == "dport" and i + 1 < len(vparts) and vparts[i + 1] == dport
                            for i in range(len(vparts))
                        )
                        if has_vpn and dport_match and f"dnat to {target}" in vline:
                            vhandle = vline.split("handle")[-1].strip()
                            _run_nft(["delete", "rule", "ip", "nat", "PREROUTING", "handle", vhandle])
        _save_dnat_to_config()
    return ok, f"DNAT rule removed" if ok else f"Failed: {err}"


def update_dnat_rule(handle, proto, dport, target_ip, target_port=None, iface=None, dest_ip=None):
    ok, _, _ = _run_nft(["delete", "rule", "ip", "nat", "PREROUTING", "handle", handle])
    if not ok:
        return False, "Failed to remove old DNAT rule"
    ok2, msg = add_dnat_rule(proto, dport, target_ip, target_port, iface, dest_ip)
    if not ok2:
        add_dnat_rule(proto, dport, target_ip, target_port, iface, dest_ip)
        return False, "Failed to add updated DNAT rule"
    return True, f"DNAT rule updated: {proto}/{dport} -> {target_ip}:{target_port or dport}"


def add_masquerade_rule(source_net, iface):
    rule_str = f'ip saddr {source_net} oifname "{iface}" masquerade'
    ok, out, err = _run_nft(["add", "rule", "ip", "nat", "POSTROUTING"] + rule_str.split())
    if ok:
        _save_nat_to_config()
    return ok, f"Masquerade rule added: {source_net} -> {iface}" if ok else f"Failed: {err}"


def remove_masquerade_rule(handle):
    ok, _, err = _run_nft(["delete", "rule", "ip", "nat", "POSTROUTING", "handle", handle])
    if ok:
        _save_nat_to_config()
    return ok, f"Masquerade rule removed" if ok else f"Failed: {err}"


def update_masquerade_rule(handle, source_net, iface):
    ok, _, _ = _run_nft(["delete", "rule", "ip", "nat", "POSTROUTING", "handle", handle])
    if not ok:
        return False, "Failed to remove old masquerade rule"
    ok2, msg = add_masquerade_rule(source_net, iface)
    if not ok2:
        return False, "Failed to add updated masquerade rule"
    return True, f"Masquerade rule updated: {source_net} -> {iface}"


def add_input_rule(action, proto, port="", saddr="", comment=""):
    parts = []
    if saddr:
        parts.append(f'ip saddr {saddr}')
    if proto and port:
        parts.append(f'{proto} dport {port}')
    elif proto and proto == "icmp":
        parts.append('icmp')
    parts.append(action)
    rule_str = " ".join(parts)
    ok, _, err = _run_nft(["add", "rule", "inet", "filter", "input"] + rule_str.split())
    if ok:
        _save_filter_to_config()
    return ok, f"Input rule added: {rule_str}" if ok else f"Failed: {err}"


def add_forward_rule(action, iifname="", oifname="", proto="", saddr="", daddr="", dport="", sport="", comment=""):
    parts = []
    if iifname:
        parts.append(f'iifname "{iifname}"')
    if oifname:
        parts.append(f'oifname "{oifname}"')
    if saddr:
        parts.append(f'ip saddr {saddr}')
    if daddr:
        parts.append(f'ip daddr {daddr}')
    if proto and (dport or sport):
        parts.append(proto)
    if sport:
        parts.append(f'sport {sport}')
    if dport:
        parts.append(f'dport {dport}')
    if comment:
        parts.append(f'comment "{comment}"')
    parts.append(action)
    rule_str = " ".join(parts)
    ok, _, err = _run_nft(["add", "rule", "inet", "filter", "forward"] + rule_str.split())
    if ok:
        _save_filter_to_config()
    return ok, f"Forward rule added: {rule_str}" if ok else f"Failed: {err}"


def remove_filter_rule(chain, handle):
    ok, _, err = _run_nft(["delete", "rule", "inet", "filter", chain, "handle", handle])
    if ok:
        _save_filter_to_config()
    return ok, f"Rule removed from {chain}" if ok else f"Failed: {err}"


def get_firewall_overview():
    nat = get_nat_rules()
    dnat_count = len([r for r in nat.get("prerouting", []) if r["type"] == "dnat"])
    masq_count = len([r for r in nat.get("postrouting", []) if r["type"] == "masquerade"])
    input_rules = get_filter_rules()
    forward_rules = get_filter_chain("forward")
    input_accept = len([r for r in input_rules if r["action"] == "accept"])
    input_drop = len([r for r in input_rules if r["action"] in ("drop", "log+drop")])
    forward_accept = len([r for r in forward_rules if r["action"] == "accept"])
    forward_drop = len([r for r in forward_rules if r["action"] in ("drop", "log+drop", "log")])
    return {
        "dnat_rules": dnat_count,
        "masquerade_rules": masq_count,
        "input_accept": input_accept,
        "input_drop": input_drop,
        "forward_accept": forward_accept,
        "forward_drop": forward_drop,
        "input_total": len(input_rules),
        "forward_total": len(forward_rules),
        "wg_peers": len(get_filter_chain("wg_acl")),
    }


def format_bytes(b):
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b/1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b/1024/1024:.1f} MB"
    else:
        return f"{b/1024/1024/1024:.2f} GB"


def _read_nft_conf():
    try:
        with open(NFT_CONF, "r") as f:
            return f.read()
    except Exception:
        return ""


def _write_nft_conf(content):
    with open(NFT_CONF, "w") as f:
        f.write(content)


def _save_dnat_to_config():
    _sync_nat_to_config()


def _save_nat_to_config():
    _sync_nat_to_config()


def _save_filter_to_config():
    _sync_filter_to_config()


def _strip_handle_counter(line):
    line = re.sub(r'\s+handle\s+\d+', '', line)
    line = re.sub(r'\s+counter\s+packets\s+\d+\s+bytes\s+\d+', ' counter', line)
    line = re.sub(r'\s+#\s*$', '', line)
    return line


def _find_table_block(conf, table_header):
    start = conf.find(table_header)
    if start == -1:
        return -1, -1
    depth = 0
    end = start
    for i in range(start, len(conf)):
        if conf[i] == '{':
            depth += 1
        elif conf[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    return start, end


def _clean_nft_output(output):
    lines = output.splitlines()
    result = []
    for line in lines:
        s = line.rstrip()
        s = re.sub(r'\s+handle\s+\d+', '', s)
        s = re.sub(r'\s+counter\s+packets\s+\d+\s+bytes\s+\d+', ' counter', s)
        s = s.rstrip()
        if s:
            result.append(s)
    return result


def _sync_nat_to_config():
    _full_config_sync()


def _sync_filter_to_config():
    _full_config_sync()


def _existing_ifaces():
    ifaces = set()
    try:
        r = subprocess.run(["ip", "-j", "link", "show"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            for i in json.loads(r.stdout):
                n = i.get("ifname", "")
                if n and n != "lo":
                    ifaces.add(n)
    except Exception:
        pass
    return ifaces


def _fix_flowtable_devices(conf):
    existing = _existing_ifaces()
    def replace_devices(m):
        devices = [d.strip().rstrip(",") for d in m.group(1).split(",") if d.strip()]
        kept = [d for d in devices if d in existing]
        return f"devices = {{ {', '.join(kept)} }}"
    conf = re.sub(r"devices\s*=\s*\{([^}]+)\}", replace_devices, conf)
    return conf


def _full_config_sync():
    tables_to_save = [
        ("inet", "filter"),
        ("ip", "nat"),
        ("ip", "filter"),
        ("bridge", "brfilter"),
    ]
    conf_parts = []
    for family, name in tables_to_save:
        ok, out, _ = _run_nft(["list", "table", family, name])
        if ok:
            cleaned = _clean_nft_output(out)
            if cleaned:
                conf_parts.append("\n".join(cleaned) + "\n")
    if not conf_parts:
        return
    conf = "\n".join(conf_parts)
    conf = _fix_flowtable_devices(conf)
    _write_nft_conf(conf)
