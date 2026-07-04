#!/usr/bin/env python3
import json
import os
import re
import time
import subprocess

from .dns_db import get_db, add_event

DHCP_LEASE_FILE = "/var/lib/misc/dnsmasq.leases"
DHCP_CONF = "/etc/dnsmasq.d/aegisgate-dhcp.conf"


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def get_dhcp_status():
    conn = get_db()
    try:
        scopes = [dict(r) for r in conn.execute("SELECT * FROM dhcp_scopes ORDER BY id ASC").fetchall()]
        static_leases = [dict(r) for r in conn.execute("SELECT * FROM dhcp_static_leases ORDER BY id ASC").fetchall()]
        active_leases_count = conn.execute("SELECT COUNT(*) as c FROM dhcp_leases WHERE state='active'").fetchone()["c"]
        total_leases = conn.execute("SELECT COUNT(*) as c FROM dhcp_leases").fetchone()["c"]
    finally:
        conn.close()

    dhcp_enabled = False
    try:
        from .dns_db import get_setting
        dhcp_enabled = get_setting("dhcp_enabled", False)
    except Exception:
        pass

    return {
        "enabled": dhcp_enabled,
        "scopes": scopes,
        "static_leases": static_leases,
        "active_leases": active_leases_count,
        "total_leases": total_leases,
    }


def add_scope(name, interface, subnet, range_start=None, range_end=None,
              router=None, dns_servers=None, domain="lan", lease_time=86400, authoritative=1):
    now = int(time.time())
    conn = get_db()
    try:
        if dns_servers:
            if isinstance(dns_servers, str):
                dns_servers = json.dumps([s.strip() for s in dns_servers.split(",") if s.strip()])
            elif isinstance(dns_servers, list):
                dns_servers = json.dumps(dns_servers)
        cur = conn.execute(
            "INSERT INTO dhcp_scopes (name, interface, subnet, range_start, range_end, router, dns_servers, domain, lease_time, authoritative, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (name, interface, subnet, range_start, range_end, router,
             dns_servers if dns_servers else None,
             domain, lease_time, authoritative, now, now),
        )
        conn.commit()
        return True, cur.lastrowid
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def update_scope(scope_id, **kwargs):
    conn = get_db()
    try:
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in ("name", "interface", "subnet", "range_start", "range_end",
                      "router", "dns_servers", "domain", "lease_time", "authoritative", "enabled"):
                sets.append(f"{k}=?")
                if k == "dns_servers":
                    if isinstance(v, str):
                        v = json.dumps([s.strip() for s in v.split(",") if s.strip()])
                    elif isinstance(v, list):
                        v = json.dumps(v)
                else:
                    v = json.dumps(v) if isinstance(v, (dict, list)) else v
                vals.append(v)
        if not sets:
            return False, "No fields to update"
        sets.append("updated_at=?")
        vals.append(int(time.time()))
        vals.append(scope_id)
        conn.execute(f"UPDATE dhcp_scopes SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        return True, f"Scope {scope_id} updated"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_scope(scope_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dhcp_options WHERE scope_id=?", (scope_id,))
        conn.execute("DELETE FROM dhcp_static_leases WHERE scope_id=?", (scope_id,))
        conn.execute("DELETE FROM dhcp_scopes WHERE id=?", (scope_id,))
        conn.commit()
        return True, f"Scope {scope_id} deleted"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def toggle_scope(scope_id, enabled=None):
    conn = get_db()
    try:
        if enabled is None:
            row = conn.execute("SELECT enabled FROM dhcp_scopes WHERE id=?", (scope_id,)).fetchone()
            if not row:
                return False, "Scope not found"
            enabled = 0 if row["enabled"] else 1
        conn.execute("UPDATE dhcp_scopes SET enabled=?, updated_at=? WHERE id=?", (enabled, int(time.time()), scope_id))
        conn.commit()
        return True, f"Scope {'enabled' if enabled else 'disabled'}"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def add_static_lease(scope_id, mac, ip, hostname=None, client_name=None, policy_id=None, comment=None):
    now = int(time.time())
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO dhcp_static_leases (scope_id, mac, ip, hostname, client_name, policy_id, enabled, comment, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (scope_id, mac, ip, hostname, client_name, policy_id, comment, now, now),
        )
        conn.commit()
        return True, cur.lastrowid
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_static_lease(lease_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dhcp_static_leases WHERE id=?", (lease_id,))
        conn.commit()
        return True, f"Static lease {lease_id} deleted"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def update_static_lease(lease_id, mac=None, ip=None, hostname=None, comment=None):
    conn = get_db()
    try:
        sets = []
        vals = []
        if mac is not None:
            sets.append("mac=?")
            vals.append(mac)
        if ip is not None:
            sets.append("ip=?")
            vals.append(ip)
        if hostname is not None:
            sets.append("hostname=?")
            vals.append(hostname if hostname else None)
        if comment is not None:
            sets.append("comment=?")
            vals.append(comment if comment else None)
        if not sets:
            return False, "No fields to update"
        sets.append("updated_at=?")
        vals.append(int(time.time()))
        vals.append(lease_id)
        conn.execute(f"UPDATE dhcp_static_leases SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        return True, f"Static lease {lease_id} updated"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def get_dhcp_options(scope_id=None):
    conn = get_db()
    try:
        if scope_id:
            return [dict(r) for r in conn.execute("SELECT * FROM dhcp_options WHERE scope_id=? ORDER BY option_code ASC", (scope_id,)).fetchall()]
        return [dict(r) for r in conn.execute("SELECT * FROM dhcp_options ORDER BY scope_id, option_code ASC").fetchall()]
    finally:
        conn.close()


def add_dhcp_option(scope_id=None, option_code=6, option_name=None, option_type="text", option_value="", comment=None):
    conn = get_db()
    try:
        common_options = {1: "Subnet Mask", 3: "Router", 6: "DNS Servers", 12: "Hostname",
                         15: "Domain Name", 28: "Broadcast Address", 42: "NTP Servers",
                         66: "TFTP Server", 67: "Bootfile", 119: "Domain Search", 121: "Static Routes"}
        if not option_name:
            option_name = common_options.get(option_code, f"Option {option_code}")
        cur = conn.execute("INSERT INTO dhcp_options (scope_id, option_code, option_name, option_type, option_value, enabled, comment) VALUES (?, ?, ?, ?, ?, 1, ?)",
                          (scope_id, option_code, option_name, option_type, option_value, comment))
        conn.commit()
        return True, cur.lastrowid
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def update_dhcp_option(option_id, **kwargs):
    conn = get_db()
    try:
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in ("scope_id", "option_code", "option_name", "option_type", "option_value", "enabled", "comment"):
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return False, "No fields to update"
        vals.append(option_id)
        conn.execute(f"UPDATE dhcp_options SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        return True, f"Option {option_id} updated"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_dhcp_option(option_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dhcp_options WHERE id=?", (option_id,))
        conn.commit()
        return True, f"Option {option_id} deleted"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def get_static_leases(scope_id=None):
    conn = get_db()
    try:
        if scope_id:
            return [dict(r) for r in conn.execute("SELECT * FROM dhcp_static_leases WHERE scope_id=? ORDER BY ip ASC", (scope_id,)).fetchall()]
        return [dict(r) for r in conn.execute("SELECT * FROM dhcp_static_leases ORDER BY ip ASC").fetchall()]
    finally:
        conn.close()


def get_leases(scope_id=None, state=None, limit=100):
    sync_leases_to_db()
    conn = get_db()
    try:
        query = "SELECT * FROM dhcp_leases WHERE 1=1"
        params = []
        if scope_id:
            query += " AND scope_id=?"
            params.append(scope_id)
        if state:
            query += " AND state=?"
            params.append(state)
        query += " ORDER BY lease_end DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def make_lease_static(lease_id):
    conn = get_db()
    try:
        lease = conn.execute("SELECT * FROM dhcp_leases WHERE id=?", (lease_id,)).fetchone()
        if not lease:
            return False, "Lease not found"
        lease = dict(lease)
        now = int(time.time())
        conn.execute(
            "INSERT INTO dhcp_static_leases (scope_id, mac, ip, hostname, client_name, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (lease.get("scope_id"), lease["mac"], lease["ip"], lease.get("hostname"), None, now, now),
        )
        conn.execute("UPDATE dhcp_leases SET state='static' WHERE id=?", (lease_id,))
        conn.commit()
        return True, f"Lease {lease_id} converted to static"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def detect_active_dhcp(interface):
    ok, out, err = _run(["nmap", "--script", "broadcast-dhcp-discover", "-e", interface], timeout=15)
    if ok and out:
        return True, f"DHCP server detected on {interface}: {out[:500]}"
    return False, f"No DHCP server detected on {interface}"


def validate_scope_config(scope_id=None, interface=None, subnet=None, range_start=None, range_end=None, router=None):
    errors = []
    if not interface:
        errors.append("Interface is required")

    try:
        ok, out, err = _run(["ip", "addr", "show", interface], timeout=5)
        if not ok:
            errors.append(f"Interface {interface} does not exist")
    except Exception:
        errors.append(f"Cannot check interface {interface}")

    if subnet:
        import ipaddress
        try:
            net = ipaddress.ip_network(subnet, strict=False)
            if range_start:
                start = ipaddress.ip_address(range_start)
                if start not in net:
                    errors.append(f"Range start {range_start} not in subnet {subnet}")
            if range_end:
                end = ipaddress.ip_address(range_end)
                if end not in net:
                    errors.append(f"Range end {range_end} not in subnet {subnet}")
            if router:
                gw = ipaddress.ip_address(router)
                if gw not in net:
                    errors.append(f"Gateway {router} not in subnet {subnet}")
        except ValueError as e:
            errors.append(f"Invalid subnet: {e}")

    from .dns_db import get_db
    conn = get_db()
    try:
        if scope_id:
            existing = conn.execute("SELECT id FROM dhcp_scopes WHERE interface=? AND id!=?", (interface, scope_id)).fetchone()
        else:
            existing = conn.execute("SELECT id FROM dhcp_scopes WHERE interface=?", (interface,)).fetchone()
        if existing:
            errors.append(f"Scope already exists for interface {interface}")
    finally:
        conn.close()

    return errors


def generate_dhcp_config():
    from .dns_db import get_db
    conn = get_db()
    try:
        scopes = [dict(r) for r in conn.execute("SELECT * FROM dhcp_scopes WHERE enabled=1").fetchall()]
        static_leases = [dict(r) for r in conn.execute("SELECT * FROM dhcp_static_leases WHERE enabled=1").fetchall()]
        options = [dict(r) for r in conn.execute("SELECT * FROM dhcp_options WHERE enabled=1").fetchall()]
    finally:
        conn.close()

    if not scopes:
        return "# AegisGate DHCP - no scopes configured\n"

    lines = ["# AegisGate DHCP - generated", ""]

    for scope in scopes:
        lines.append(f"# Scope: {scope['name']}")
        lines.append(f"interface={scope['interface']}")

        if scope.get("authoritative", 1):
            lines.append("dhcp-authoritative")

        if scope.get("range_start") and scope.get("range_end"):
            netmask = ""
            if scope.get("subnet"):
                import ipaddress
                try:
                    net = ipaddress.ip_network(scope["subnet"], strict=False)
                    netmask = str(net.netmask)
                except Exception:
                    pass
            lease_time = scope.get("lease_time", 86400)
            try:
                lease_time = int(lease_time)
            except (ValueError, TypeError):
                lease_time = 86400
            if lease_time >= 3600:
                lease_str = f"{lease_time // 3600}h"
            elif lease_time >= 60:
                lease_str = f"{lease_time // 60}m"
            else:
                lease_str = f"{lease_time}s"
            lines.append(f"dhcp-range={scope['interface']},{scope['range_start']},{scope['range_end']},{netmask},{lease_str}")

        if scope.get("router"):
            lines.append(f"dhcp-option={scope['interface']},option:router,{scope['router']}")

        if scope.get("dns_servers"):
            try:
                dns_list = json.loads(scope["dns_servers"]) if isinstance(scope["dns_servers"], str) else scope["dns_servers"]
                if isinstance(dns_list, str):
                    dns_list = [dns_list]
                if dns_list:
                    lines.append(f"dhcp-option=option:dns-server,{','.join(dns_list)}")
            except (json.JSONDecodeError, TypeError):
                dns_raw = str(scope["dns_servers"]).strip()
                if dns_raw:
                    lines.append(f"dhcp-option=option:dns-server,{dns_raw}")

        lines.append("")

    client_tag_map = {}
    try:
        from .dns_db import get_db as get_dns_db
        _conn = get_dns_db()
        for row in _conn.execute("SELECT mac, name FROM dns_clients WHERE mac IS NOT NULL AND mac != '' AND enabled=1").fetchall():
            cmac = (row[0] or "").strip().lower()
            cname = (row[1] or "").strip()
            if cmac and cname:
                import re as _re
                tag = _re.sub(r'[^a-zA-Z0-9_-]', '_', cname)[:32]
                client_tag_map[cmac] = tag
        _conn.close()
    except Exception:
        pass

    seen_ips = {}
    for lease in static_leases:
        mac = lease["mac"]
        ip = lease["ip"]
        hostname = lease.get("hostname", "") or ""
        lease_time = "24h"
        if ip in seen_ips:
            continue
        seen_ips[ip] = mac
        tags = []
        if lease.get("policy_id"):
            tags.append(f"policy_{lease['policy_id']}")
        mac_lower = mac.strip().lower()
        if mac_lower in client_tag_map:
            tags.insert(0, client_tag_map[mac_lower])
        tag_str = ",".join(tags)
        set_str = f",set:{tag_str}" if tag_str else ""
        if hostname:
            lines.append(f"dhcp-host={mac},{ip},{hostname},{lease_time}{set_str}")
        else:
            lines.append(f"dhcp-host={mac},{ip},{lease_time}{set_str}")

    for opt in options:
        scope_iface = ""
        if opt.get("scope_id"):
            conn = get_db()
            try:
                scope = conn.execute("SELECT interface FROM dhcp_scopes WHERE id=?", (opt["scope_id"],)).fetchone()
                if scope:
                    scope_iface = f"{scope['interface']},"
            finally:
                conn.close()
        lines.append(f"dhcp-option={scope_iface}{opt['option_code']},{opt['option_value']}")

    return "\n".join(lines) + "\n"


def write_dhcp_config():
    from .dns_config import _write_conf, DHCP_CONF
    content = generate_dhcp_config()
    return _write_conf(DHCP_CONF, content)


def parse_lease_file():
    if not os.path.exists(DHCP_LEASE_FILE):
        return []
    leases = []
    try:
        with open(DHCP_LEASE_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 4:
                    leases.append({
                        "expiry": parts[0],
                        "mac": parts[1],
                        "ip": parts[2],
                        "hostname": parts[3] if len(parts) > 3 else "",
                        "client_id": parts[4] if len(parts) > 4 else "",
                    })
    except Exception:
        pass
    return leases


def sync_leases_to_db():
     from .dhcp_leases import import_leases
     leases = parse_lease_file()
     if leases:
         count = import_leases(leases)
         return True, f"Synced {count} leases"
     return True, "No leases to sync"


NFT = "/usr/sbin/nft"


def _run_cmd(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def _get_lan_interface():
    try:
        from modules.dns_db import get_db
        conn = get_db()
        row = conn.execute("SELECT interface FROM dhcp_scopes WHERE enabled=1 LIMIT 1").fetchone()
        conn.close()
        if row:
            return row["interface"]
    except Exception:
        pass
    return "eth1"


def setup_dhcp_nft_rules(lan_iface=None):
    if not lan_iface:
        lan_iface = _get_lan_interface()
    ok, out, err = _run_cmd([NFT, "list", "chain", "inet", "filter", "input"])
    if f'iifname "{lan_iface}" udp dport 67 accept' in (out if ok else ""):
        return True, "DHCP nft rules already exist"
    gateway_ip = _get_gateway_ip(lan_iface)
    _run_cmd([NFT, "insert", "rule", "inet", "filter", "input", "iifname", lan_iface, "udp", "dport", "68", "accept"])
    _run_cmd([NFT, "insert", "rule", "inet", "filter", "input", "iifname", lan_iface, "udp", "dport", "67", "accept"])
    _run_cmd([NFT, "insert", "rule", "inet", "filter", "input", "iifname", lan_iface, "udp", "dport", "53", "accept"])
    _run_cmd([NFT, "insert", "rule", "inet", "filter", "input", "iifname", lan_iface, "tcp", "dport", "53", "accept"])
    if gateway_ip:
        _run_cmd([NFT, "add", "rule", "inet", "filter", "input", "iifname", lan_iface, "udp", "sport", "67", "ip", "saddr", "!=", gateway_ip, "drop"])
    try:
        with open("/proc/sys/net/ipv4/ip_forward", "w") as f:
            f.write("1\n")
    except Exception:
        pass
    return True, "DHCP+DNS nft rules added"


def _get_gateway_ip(iface):
    try:
        result = subprocess.run(["ip", "-4", "addr", "show", iface], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if "inet " in line:
                return line.strip().split()[1].split("/")[0]
    except Exception:
        pass
    return None


def remove_dhcp_nft_rules(lan_iface=None):
    if not lan_iface:
        lan_iface = _get_lan_interface()
    ok, out, err = _run_cmd([NFT, "-a", "list", "chain", "inet", "filter", "input"])
    if not ok or "udp dport 67" not in out:
        return True, "No DHCP rules to remove"
    handles = []
    for line in out.splitlines():
        if ("udp dport 67 accept" in line or "udp dport 68 accept" in line
                or "udp dport 53 accept" in line or "tcp dport 53 accept" in line
                or ("udp sport 67" in line and "saddr" in line and "drop" in line)):
            if f'iifname "{lan_iface}"' in line or "saddr" in line:
                parts = line.strip().split()
                for i, p in enumerate(parts):
                    if p == "handle" and i + 1 < len(parts):
                        handles.append(parts[i + 1])
    if not handles:
        return True, "No DHCP/DNS rule handles found"
    for h in handles:
        _run_cmd([NFT, "delete", "rule", "inet", "filter", "input", "handle", h])
    return True, f"Removed {len(handles)} DHCP/DNS nft rules"