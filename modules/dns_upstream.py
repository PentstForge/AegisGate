#!/usr/bin/env python3
import json
import time
import subprocess

from .dns_db import get_db


def get_upstreams():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM dns_upstream ORDER BY priority ASC, id ASC").fetchall()]
    finally:
        conn.close()


def add_upstream(address, proto="udp", domain=None, enabled=1, priority=100, comment=None):
    now = int(time.time())
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO dns_upstream (address, proto, domain, enabled, priority, comment, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (address, proto, domain, enabled, priority, comment, now, now),
        )
        conn.commit()
        return True, cur.lastrowid
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_upstream(upstream_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dns_upstream WHERE id=?", (upstream_id,))
        conn.commit()
        return True, f"Upstream {upstream_id} deleted"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def update_upstream(upstream_id, **kwargs):
    conn = get_db()
    try:
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in ("address", "proto", "domain", "enabled", "priority", "comment"):
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return False, "No fields to update"
        sets.append("updated_at=?")
        vals.append(int(time.time()))
        vals.append(upstream_id)
        conn.execute(f"UPDATE dns_upstream SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        return True, f"Upstream {upstream_id} updated"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def test_upstream(address, proto="udp", timeout=3):
    if proto in ("doh", "https"):
        url = address if address.startswith("http") else f"https://{address}/dns-query"
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"Content-Type": "application/dns-message", "User-Agent": "AegisDNS/1.0"})
            urllib.request.urlopen(req, timeout=timeout)
            return True, f"DoH {address}: reachable"
        except Exception as e:
            return False, f"DoH {address}: {str(e)[:100]}"

    if proto in ("dot", "tls"):
        import socket
        import ssl
        host = address.split(":")[0]
        port = int(address.split(":")[1]) if ":" in address else 853
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, port), timeout=timeout) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                    return True, f"DoT {address}: reachable ({ssock.version()})"
        except Exception as e:
            return False, f"DoT {address}: {str(e)[:100]}"

    host = address.split(":")[0]
    port = int(address.split(":")[1]) if ":" in address else 53
    try:
        import socket
        with socket.create_connection((host, port), timeout=timeout) as sock:
            return True, f"{proto.upper()} {address}: reachable"
    except Exception as e:
        return False, f"{proto.upper()} {address}: {str(e)[:100]}"


def test_all_upstreams():
    upstreams = get_upstreams()
    results = []
    for up in upstreams:
        if not up.get("enabled", 1):
            continue
        ok, msg = test_upstream(up["address"], up.get("proto", "udp"))
        results.append({"id": up["id"], "address": up["address"], "proto": up["proto"], "ok": ok, "msg": msg})
    return results