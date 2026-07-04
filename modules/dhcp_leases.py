#!/usr/bin/env python3
import time
import socket
import subprocess

from .dns_db import get_db, add_event


def _resolve_hostname(ip, mac=""):
    try:
        name = socket.gethostbyaddr(ip)[0]
        if name and name != ip:
            return name
    except (socket.herror, socket.gaierror, OSError):
        pass
    if mac:
        try:
            conn = get_db()
            row = conn.execute("SELECT name FROM dns_clients WHERE mac=? OR mac=? OR ip=?", (mac, mac.upper(), ip)).fetchone()
            conn.close()
            if row and row["name"]:
                return row["name"]
        except Exception:
            pass
    try:
        r = subprocess.run(["ip", "neigh", "show", ip], capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                if ip in line:
                    return ""
    except Exception:
        pass
    return ""


def import_leases(leases):
    conn = get_db()
    imported = 0
    now = int(time.time())
    try:
        for lease in leases:
            expiry = int(lease.get("expiry", 0)) if lease.get("expiry", "0").isdigit() else 0
            mac = lease.get("mac", "")
            ip = lease.get("ip", "")
            hostname = lease.get("hostname", "")
            client_id = lease.get("client_id", "")

            if not mac or not ip:
                continue

            if not hostname or hostname == "*":
                resolved = _resolve_hostname(ip, mac)
                if resolved:
                    hostname = resolved
                else:
                    hostname = ""

            existing = conn.execute("SELECT id, state FROM dhcp_leases WHERE mac=? AND ip=?", (mac, ip)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE dhcp_leases SET lease_end=?, hostname=?, client_id=?, state='active', last_seen_ts=? WHERE id=?",
                    (expiry, hostname, client_id, now, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO dhcp_leases (scope_id, ip, mac, hostname, client_id, lease_start, lease_end, state, source, first_seen_ts, last_seen_ts) VALUES (NULL, ?, ?, ?, ?, ?, ?, 'active', 'dnsmasq', ?, ?)",
                    (ip, mac, hostname, client_id, now, expiry, now, now),
                )
            imported += 1

            if hostname and ip:
                try:
                    conn.execute(
                        "UPDATE dns_clients SET name=COALESCE(NULLIF(name,''),?), mac=COALESCE(NULLIF(mac,''),?) WHERE ip=?",
                        (hostname, mac, ip),
                    )
                except Exception:
                    pass
        conn.commit()
    finally:
        conn.close()
    return imported


def get_leases(scope_id=None, state=None, limit=100):
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


def get_static_leases(scope_id=None):
    conn = get_db()
    try:
        if scope_id:
            return [dict(r) for r in conn.execute("SELECT * FROM dhcp_static_leases WHERE scope_id=? ORDER BY id ASC", (scope_id,)).fetchall()]
        return [dict(r) for r in conn.execute("SELECT * FROM dhcp_static_leases ORDER BY id ASC").fetchall()]
    finally:
        conn.close()


def cleanup_expired_leases():
    now = int(time.time())
    conn = get_db()
    try:
        expired = conn.execute("SELECT * FROM dhcp_leases WHERE state='active' AND lease_end < ?", (now,)).fetchall()
        for lease in expired:
            add_event("lease_expired", "info", "dhcp_leases",
                      f"DHCP lease expired: {lease['ip']} ({lease['mac']})",
                      data={"ip": lease["ip"], "mac": lease["mac"], "hostname": lease.get("hostname")})
        conn.execute("UPDATE dhcp_leases SET state='expired' WHERE state='active' AND lease_end < ?", (now,))
        deleted = conn.execute("DELETE FROM dhcp_leases WHERE state='expired' AND lease_end < ?", (now - 86400 * 7,)).rowcount
        conn.commit()
        return len(expired), deleted
    finally:
        conn.close()


def register_lease_in_dns(ip, hostname, mac=None):
    if not hostname or not ip:
        return
    from .dns import add_local_record
    add_local_record(hostname, rtype="A", value=ip, comment=f"DHCP lease {mac}" if mac else "DHCP lease")