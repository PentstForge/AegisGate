#!/usr/bin/env python3
import os
import re
import shutil
import sqlite3
import subprocess
import time

import json

CONF_DIR = "/etc/dnsmasq.d"
MAIN_CONF = os.path.join(CONF_DIR, "aegisgate.conf")
BLOCKLIST_DIR = os.path.join(CONF_DIR, "aegisgate-blocklists")
BLOCKLIST_CONF = os.path.join(CONF_DIR, "aegisgate-blocklist.conf")
LOCAL_CONF = os.path.join(CONF_DIR, "aegisgate-local.conf")
CLIENTS_CONF = os.path.join(CONF_DIR, "aegisgate-clients.conf")
UPSTREAM_CONF = os.path.join(CONF_DIR, "aegisgate-upstream.conf")
DHCP_CONF = os.path.join(CONF_DIR, "aegisgate-dhcp.conf")

BACKUP_DIR = "/etc/dnsmasq.d/aegisgate-backup"

DNSMASQ_BIN = "/usr/sbin/dnsmasq"


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def _backup_configs():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = str(int(time.time()))
    for f in [MAIN_CONF, BLOCKLIST_CONF, LOCAL_CONF, CLIENTS_CONF, UPSTREAM_CONF, DHCP_CONF]:
        if os.path.exists(f):
            shutil.copy2(f, os.path.join(BACKUP_DIR, f"{os.path.basename(f)}.{ts}"))


def _write_conf(path, content):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    ok, out, err = _run(["dnsmasq", "--conf-file=" + tmp, "--test"], timeout=10)
    if ok:
        os.rename(tmp, path)
        return True, f"Config written: {path}"
    else:
        os.unlink(tmp)
        return False, f"Config validation failed: {err}"


def _validate_syntax(content):
    tmp = "/tmp/aegisgate-dnsmasq-test.conf"
    try:
        with open(tmp, "w") as f:
            f.write(content)
        ok, out, err = _run(["dnsmasq", "--conf-file=" + tmp, "--test"], timeout=10)
        return ok, err
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def generate_main_config(settings):
    lines = ["# AegisGate DNS - generated config - DO NOT EDIT MANUALLY", ""]
    lines.append(f"cache-size={settings.get('cache_size', 5000)}")
    lines.append(f"local=/{settings.get('local_domain', 'lan')}/")
    lines.append("domain=lan")
    lines.append("expand-hosts")
    lan_iface = settings.get("lan_interface", "")
    if not lan_iface:
        scopes = settings.get("dhcp_scopes", [])
        if scopes:
            lan_iface = scopes[0].get("interface", "")
    if not lan_iface:
        from modules.dns_db import get_db
        try:
            conn = get_db()
            row = conn.execute("SELECT interface FROM dhcp_scopes WHERE enabled=1 LIMIT 1").fetchone()
            if row:
                lan_iface = row["interface"]
            conn.close()
        except Exception:
            pass
    if lan_iface:
        lines.append(f"interface={lan_iface}")

    listen_addr = settings.get("dns_listen_addr", "")
    listen_port = settings.get("dns_listen_port", 53)
    if listen_addr and listen_addr != "0.0.0.0":
        lines.append(f"listen-address={listen_addr}")
    lines.append(f"port={listen_port}")

    lines.append("no-resolv")
    lines.append("no-poll")
    lines.append("stop-dns-rebind")
    lines.append(f"dns-forward-max={settings.get('ratelimit_qps', 20) * 50}")

    lines.append(f"min-cache-ttl={settings.get('blocked_response_ttl', 60)}")

    log_path = "/var/log/ram/dnsmasq-queries.log"
    lines.append("log-queries=extra")
    lines.append(f"log-facility={log_path}")
    if settings.get("dhcp_enabled", False):
        lines.append("log-dhcp")
        lease_dir = "/var/lib/misc"
        os.makedirs(lease_dir, exist_ok=True)
        lines.append(f"dhcp-leasefile={lease_dir}/dnsmasq.leases")

    lines.append("")
    lines.append(f"conf-file={BLOCKLIST_CONF}")
    lines.append(f"conf-dir={BLOCKLIST_DIR}")
    lines.append(f"conf-file={LOCAL_CONF}")
    lines.append(f"conf-file={UPSTREAM_CONF}")
    if settings.get("dhcp_enabled", False):
        lines.append(f"conf-file={DHCP_CONF}")
    lines.append(f"conf-file={CLIENTS_CONF}")

    return "\n".join(lines) + "\n"


def _dnsmasq_domain(value, rtype):
    """Convert rule value to dnsmasq domain format.

    dnsmasq supports:
    - exact: address=/example.com/0.0.0.0
    - wildcard: address=/*.example.com/0.0.0.0 (matches subdomains)
    - adblock_domain: address=/example.com/0.0.0.0 (matches domain + subdomains)

    dnsmasq does NOT support regex. Regex rules are skipped with a warning.
    Invalid entries (CSS selectors, URL paths) are also skipped.
    """
    if rtype == "regex":
        return None
    v = value.strip()
    if "##" in v or "[" in v or "]" in v:
        return None
    if v.startswith("/"):
        return None
    dot_pos = v.find(".")
    if dot_pos > 0 and "/" in v[dot_pos + 1:]:
        return None
    if rtype == "wildcard":
        if v.startswith(".*"):
            v = v[2:]
        if v.startswith("\\."):
            v = v[2:]
        v = v.replace("\\.", ".")
        if not v.startswith("*"):
            v = "*" + v
        return v
    return v


def _write_blocklists_streaming(settings, extra_rules=None):
    """Write blocklist rules directly from DB to per-list files.
    
    Instead of loading 2M+ rules into memory, we stream them from SQLite
    directly into per-source config files. Manual/allow/rewrite rules go
    into the main blocklist file, list-sourced rules go into separate files
    under aegisgate-blocklists/ directory.
    
    extra_rules: list of dicts with 'value', 'action', 'type', 'category' keys
                 (e.g. from _with_service_rules()) that are merged with DB rules.
    """
    from modules.dns_db import get_db
    block_mode = settings.get("block_mode", "null_ip")
    block_ipv4 = settings.get("custom_block_ipv4", "0.0.0.0")
    block_ipv6 = settings.get("custom_block_ipv6", "::")
    db_path = "/opt/nft-dashboard/data/dns.db"
    
    os.makedirs(BLOCKLIST_DIR, exist_ok=True)
    
    for f in os.listdir(BLOCKLIST_DIR):
        if f.endswith(".conf"):
            os.unlink(os.path.join(BLOCKLIST_DIR, f))
    
    list_map = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT id, name FROM dns_lists").fetchall():
            list_map[row["id"]] = row["name"]
        conn.close()
    except Exception:
        pass
    
    manual_lines = ["# AegisGate manual/allow/rewrite rules - generated", ""]
    
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT value, action, type, source, modifiers_json FROM dns_rules WHERE enabled=1 ORDER BY priority ASC, id ASC"
    )
    
    batch_size = 50000
    list_files = {}
    list_counts = {}
    skipped_regex = 0
    seen = set()
    
    for row in cursor:
        domain = row[0].strip()
        action = row[1]
        rtype = row[2]
        source = row[3] or "manual"
        modifiers_json = row[4]
        
        dnsmasq_domain = _dnsmasq_domain(domain, rtype)
        if dnsmasq_domain is None:
            skipped_regex += 1
            continue
        if dnsmasq_domain in seen:
            continue
        seen.add(dnsmasq_domain)
        
        line = None
        if action == "allow":
            line = f"local=/{dnsmasq_domain}/"
            manual_lines.append(line)
            continue
        
        if action == "rewrite":
            modifiers = {}
            if modifiers_json:
                try:
                    modifiers = json.loads(modifiers_json)
                except Exception:
                    pass
            rewrite_target = modifiers.get("dnsrewrite", "")
            if rewrite_target and rewrite_target not in ("NXDOMAIN", "REFUSED", "NOERROR"):
                line = f"address=/{dnsmasq_domain}/{rewrite_target}"
            elif rewrite_target in ("NXDOMAIN", "REFUSED"):
                line = f"server=/{dnsmasq_domain}/"
            if line:
                manual_lines.append(line)
            continue
        
        if block_mode == "nxdomain" or block_mode == "refused":
            line = f"server=/{dnsmasq_domain}/"
        elif block_mode == "custom_ip":
            line = f"address=/{dnsmasq_domain}/{block_ipv4}"
            if block_ipv6 and block_ipv6 != "::":
                line += f"\naddress=/{dnsmasq_domain}/{block_ipv6}"
        else:
            line = f"address=/{dnsmasq_domain}/0.0.0.0"
        
        if not line:
            continue
        
        if source == "manual" or source == "test_import":
            manual_lines.append(line)
        else:
            list_id_str = source.replace("list:", "")
            try:
                list_id = int(list_id_str)
            except (ValueError, TypeError):
                list_id = 0
            if list_id not in list_files:
                list_name = list_map.get(list_id, f"list_{list_id}").replace(" ", "_").replace("/", "_")
                safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", list_name)[:48]
                fpath = os.path.join(BLOCKLIST_DIR, f"{safe_name}.conf")
                fh = open(fpath, "w")
                fh.write(f"# AegisGate blocklist: {list_map.get(list_id, source)} - generated\n\n")
                list_files[list_id] = fh
                list_counts[list_id] = 0
            list_files[list_id].write(line + "\n")
            list_counts[list_id] = list_counts.get(list_id, 0) + 1
    
    conn.close()
    
    if extra_rules:
        for rule in extra_rules:
            domain = rule.get("value", "").strip()
            if not domain:
                continue
            action = rule.get("action", "block")
            rtype = rule.get("type", "adblock_domain")
            category = rule.get("category", "service")
            dnsmasq_domain = _dnsmasq_domain(domain, rtype)
            if dnsmasq_domain is None:
                continue
            if dnsmasq_domain in seen:
                continue
            seen.add(dnsmasq_domain)
            if action == "allow":
                manual_lines.append(f"local=/{dnsmasq_domain}/")
                continue
            if action == "rewrite":
                modifiers = rule.get("modifiers", {})
                rewrite_target = modifiers.get("dnsrewrite", "")
                if rewrite_target and rewrite_target not in ("NXDOMAIN", "REFUSED", "NOERROR"):
                    manual_lines.append(f"address=/{dnsmasq_domain}/{rewrite_target}")
                elif rewrite_target in ("NXDOMAIN", "REFUSED"):
                    manual_lines.append(f"server=/{dnsmasq_domain}/")
                continue
            if block_mode == "nxdomain" or block_mode == "refused":
                line = f"server=/{dnsmasq_domain}/"
            elif block_mode == "custom_ip":
                line = f"address=/{dnsmasq_domain}/{block_ipv4}"
            else:
                line = f"address=/{dnsmasq_domain}/0.0.0.0"
            manual_lines.append(line)
    
    for lid, fh in list_files.items():
        fh.close()
    
    if skipped_regex:
        manual_lines.append(f"# Note: {skipped_regex} regex rule(s) skipped (dnsmasq does not support regex)")
    
    manual_content = "\n".join(manual_lines) + "\n"
    ok, msg = _write_conf(BLOCKLIST_CONF, manual_content)
    errors = []
    if not ok:
        errors.append(f"blocklist-manual: {msg}")
    
    for lid, fh in list_files.items():
        fpath = fh.name
        ok2, msg2 = _validate_syntax_file(fpath)
        if not ok2:
            errors.append(f"blocklist-{lid}: {msg2}")
    
    return len(errors) == 0, "; ".join(errors) if errors else "Blocklists written", list_counts


def _validate_syntax_file(path):
    try:
        ok, out, err = _run(["dnsmasq", f"--conf-file={path}", "--test"], timeout=30)
        return ok, err
    except Exception as e:
        return False, str(e)


def generate_local_config(records, rewrites):
    lines = ["# AegisGate local DNS - generated", ""]

    for rec in records:
        if not rec.get("enabled", 1):
            continue
        domain = rec["domain"].strip()
        rtype = rec.get("rtype", "A").upper()
        value = rec["value"].strip()
        ttl = rec.get("ttl", 60)

        if rtype == "A":
            lines.append(f"address=/{domain}/{value}")
        elif rtype == "AAAA":
            lines.append(f"address=/{domain}/{value}")
        elif rtype == "CNAME":
            lines.append(f"cname={domain},{value}")
        elif rtype == "TXT":
            lines.append(f"txt-record={domain},{value}")
        elif rtype == "MX":
            lines.append(f"mx-host={domain},{value}")
        elif rtype == "SRV":
            parts = value.split()
            if len(parts) >= 3:
                lines.append(f"srv-host={domain},{parts[0]},{parts[1]},{parts[2]}")

    lines.append("")
    lines.append("# DNS rewrites")
    for rw in rewrites:
        if not rw.get("enabled", 1):
            continue
        domain = rw["domain"].strip()
        target = rw["target"].strip()
        rwtype = rw.get("rtype", "CNAME").upper()
        if rwtype == "CNAME":
            lines.append(f"cname={domain},{target}")
        else:
            lines.append(f"address=/{domain}/{target}")

    return "\n".join(lines) + "\n"


def generate_upstream_config(upstreams, settings):
    lines = ["# AegisGate upstream DNS - generated", ""]

    if not upstreams:
        lines.append("server=9.9.9.9")
        lines.append("server=1.1.1.1")
        return "\n".join(lines) + "\n"

    for up in upstreams:
        if not up.get("enabled", 1):
            continue
        addr = up["address"].strip()
        proto = up.get("proto", "udp").lower()
        domain = up.get("domain", "").strip()

        if proto in ("doh", "https"):
            lines.append(f"server={addr}")
        elif proto in ("dot", "tls"):
            lines.append(f"server={addr}")
        elif proto == "tcp":
            lines.append(f"server={addr}")
        else:
            if domain:
                lines.append(f"server=/{domain}/{addr}")
            else:
                lines.append(f"server={addr}")

    for fb in settings.get("fallback_dns", []):
        lines.append(f"server={fb}")

    for bs in settings.get("bootstrap_dns", []):
        pass  # bootstrap DNS is for DoH resolution, handled separately

    return "\n".join(lines) + "\n"


def generate_clients_config(clients, policies, static_leases=None):
    lines = ["# AegisGate client policies - generated", ""]
    policies_by_id = {p.get("id"): p for p in policies} if policies else {}

    lease_macs = set()
    lease_ips = set()
    if static_leases:
        for lease in static_leases:
            mac = (lease.get("mac") or "").strip().lower()
            ip = (lease.get("ip") or "").strip()
            if mac:
                lease_macs.add(mac)
            if ip:
                lease_ips.add(ip)

    for client in clients:
        if not client.get("enabled", 1):
            continue
        ip = (client.get("ip") or "").strip()
        mac = (client.get("mac") or "").strip()
        name = (client.get("name") or "").strip()
        client_tags = []
        try:
            client_tags = json.loads(client.get("tags_json", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            pass

        tag = None
        if mac or ip:
            tag = name or (mac.replace(":", "").lower() if mac else ip.replace(".", ""))
            tag = re.sub(r'[^a-zA-Z0-9_-]', '_', tag)[:32]

        if tag:
            client_mac_lower = mac.lower() if mac else ""
            if (client_mac_lower and client_mac_lower in lease_macs) or (ip and ip in lease_ips):
                if client_mac_lower and client_mac_lower in lease_macs:
                    lines.append(f"# {name or mac} managed by static lease")
                elif ip and ip in lease_ips:
                    lines.append(f"# {name or ip} managed by static lease")
            elif mac and ip:
                lines.append(f"dhcp-host={mac},{ip},set:{tag}")
            elif mac:
                lines.append(f"dhcp-host={mac},set:{tag}")
            elif ip:
                lines.append(f"# client {name or ip} at {ip}")

        if tag:
            upstreams = []
            try:
                upstreams = json.loads(client.get("upstreams_json") or "[]")
            except (json.JSONDecodeError, TypeError):
                upstreams = []
            if isinstance(upstreams, list):
                dns_servers = []
                for item in upstreams:
                    if isinstance(item, dict):
                        addr = item.get("address") or item.get("ip") or ""
                    else:
                        addr = str(item)
                    addr = addr.strip()
                    if not addr:
                        continue
                    if "://" in addr:
                        lines.append(f"server=tag:{tag},{addr}")
                    else:
                        dns_servers.append(addr.split(":", 1)[0])
                if dns_servers:
                    lines.append(f"dhcp-option=tag:{tag},option:dns-server,{','.join(dns_servers)}")

        policy_id = client.get("policy_id")
        policy = policies_by_id.get(policy_id) if policy_id else None
        if policy and not policy.get("enabled", 1):
            policy = None

        if policy and tag:
            p_upstreams = []
            try:
                p_upstreams = json.loads(policy.get("upstreams_json") or "[]")
            except (json.JSONDecodeError, TypeError):
                p_upstreams = []
            if isinstance(p_upstreams, list) and p_upstreams:
                for item in p_upstreams:
                    if isinstance(item, dict):
                        addr = item.get("address") or item.get("ip") or ""
                    else:
                        addr = str(item)
                    addr = addr.strip()
                    if addr:
                        lines.append(f"server=tag:{tag},{addr}")

            if policy and tag:
                pass

    return "\n".join(lines) + "\n"


def write_all_configs(settings, rules=None, records=None, rewrites=None,
                       upstreams=None, clients=None, policies=None, static_leases=None):
    _backup_configs()
    errors = []

    main_conf = generate_main_config(settings)
    ok, msg = _write_conf(MAIN_CONF, main_conf)
    if not ok:
        errors.append(f"main: {msg}")

    ok, msg, list_counts = _write_blocklists_streaming(settings, extra_rules=rules)
    if not ok:
        errors.append(f"blocklist: {msg}")

    if records is None:
        records = []
    if rewrites is None:
        rewrites = []
    local_conf = generate_local_config(records, rewrites)
    ok, msg = _write_conf(LOCAL_CONF, local_conf)
    if not ok:
        errors.append(f"local: {msg}")

    if upstreams is None:
        upstreams = []
    up_conf = generate_upstream_config(upstreams, settings)
    ok, msg = _write_conf(UPSTREAM_CONF, up_conf)
    if not ok:
        errors.append(f"upstream: {msg}")

    if clients is None:
        clients = []
    if policies is None:
        policies = []
    cl_conf = generate_clients_config(clients, policies, static_leases=static_leases)
    ok, msg = _write_conf(CLIENTS_CONF, cl_conf)
    if not ok:
        errors.append(f"clients: {msg}")

    if errors:
        return False, "; ".join(errors)
    return True, "All configs written"


def reload_dnsmasq():
    ok, out, err = _run(["systemctl", "restart", "dnsmasq"], timeout=15)
    if ok:
        return True, "dnsmasq restarted"
    ok2, _, _ = _run(["killall", "-HUP", "dnsmasq"], timeout=5)
    if ok2:
        return True, "dnsmasq SIGHUP sent"
    return False, f"dnsmasq restart failed: {err}"


def start_dnsmasq():
    ok, out, err = _run(["systemctl", "start", "dnsmasq"], timeout=15)
    if ok:
        return True, "dnsmasq started"
    ok2, out2, err2 = _run(["/usr/sbin/dnsmasq"], timeout=5)
    if ok2:
        return True, "dnsmasq started (direct)"
    return False, f"dnsmasq start failed: {err}"


def stop_dnsmasq():
    ok, out, err = _run(["systemctl", "stop", "dnsmasq"], timeout=10)
    if ok:
        return True, "dnsmasq stopped"
    ok2, _, _ = _run(["killall", "dnsmasq"], timeout=5)
    if ok2:
        return True, "dnsmasq killed"
    return False, f"dnsmasq stop failed: {err}"


def get_dnsmasq_status():
    ok, out, err = _run(["systemctl", "is-active", "dnsmasq"], timeout=5)
    if ok and "active" in out:
        ok2, out2, _ = _run(["systemctl", "show", "dnsmasq", "--property=ActiveEnterTimestamp"], timeout=5)
        uptime = out2.strip().split("=", 1)[-1] if ok2 else "unknown"
        return {"running": True, "uptime": uptime}
    ok3, pid_out, _ = _run(["pgrep", "-x", "dnsmasq"], timeout=5)
    if ok3 and pid_out:
        return {"running": True, "uptime": "unknown"}
    return {"running": False, "uptime": None}


def rollback_configs():
    if not os.path.isdir(BACKUP_DIR):
        return False, "No backup directory"
    backups = sorted(os.listdir(BACKUP_DIR))
    if not backups:
        return False, "No backups found"
    latest = {}
    for f in backups:
        parts = f.rsplit(".", 1)
        if len(parts) == 2:
            base, ts = parts
            if base not in latest or ts > latest[base][1]:
                latest[base] = (f, ts)
    errors = []
    for base, (fname, _) in latest.items():
        src = os.path.join(BACKUP_DIR, fname)
        dst = os.path.join(CONF_DIR, base)
        try:
            shutil.copy2(src, dst)
        except Exception as e:
            errors.append(f"{base}: {e}")
    if errors:
        return False, "; ".join(errors)
    return True, "Configs rolled back"


def ensure_dnsmasq_dir():
    os.makedirs(CONF_DIR, exist_ok=True)
    main_dnsmasq_conf = "/etc/dnsmasq.conf"
    if os.path.exists(main_dnsmasq_conf):
        with open(main_dnsmasq_conf) as f:
            content = f.read()
        if "aegisgate.conf" not in content:
            with open(main_dnsmasq_conf, "w") as f:
                f.write("# AegisGate dnsmasq main config\nconf-file=/etc/dnsmasq.d/aegisgate.conf\n")
    for f in [MAIN_CONF, BLOCKLIST_CONF, LOCAL_CONF, UPSTREAM_CONF, CLIENTS_CONF]:
        if not os.path.exists(f):
            with open(f, "w") as fh:
                fh.write("# AegisGate DNS - placeholder\n")
