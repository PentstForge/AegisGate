#!/usr/bin/env python3
"""Multi-WAN Gateway: health checks, failover, load balancing, policy routing."""

import json
import os
import sqlite3
import subprocess
import time
import threading
import logging

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "multiwan.db")

log = logging.getLogger("multiwan")

SCHEMA = """
CREATE TABLE IF NOT EXISTS wan_interfaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT DEFAULT '',
    interface TEXT NOT NULL,
    gateway TEXT NOT NULL,
    weight INTEGER DEFAULT 100,
    role TEXT DEFAULT 'active',
    priority INTEGER DEFAULT 100,
    health_check_ip TEXT DEFAULT '8.8.8.8',
    health_check_method TEXT DEFAULT 'ping',
    health_check_interval INTEGER DEFAULT 5,
    health_check_timeout INTEGER DEFAULT 3,
    health_check_fail_count INTEGER DEFAULT 3,
    metric_base INTEGER DEFAULT 100,
    enabled INTEGER DEFAULT 1,
    status TEXT DEFAULT 'unknown',
    last_check REAL DEFAULT 0,
    last_up REAL DEFAULT 0,
    last_down REAL DEFAULT 0,
    consecutive_fails INTEGER DEFAULT 0,
    consecutive_oks INTEGER DEFAULT 0,
    current_metric INTEGER DEFAULT 100,
    download_speed REAL DEFAULT 0,
    upload_speed REAL DEFAULT 0,
    notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS wan_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    interface TEXT NOT NULL,
    event TEXT NOT NULL,
    old_status TEXT DEFAULT '',
    new_status TEXT DEFAULT '',
    details TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS wan_settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
"""

DEFAULT_SETTINGS = {
    "failover_mode": "auto",
    "lb_algorithm": "weighted",
    "check_interval": "5",
    "check_timeout": "3",
    "check_fail_count": "3",
    "check_ok_count": "2",
    "sticky_sessions": "1",
    "nat_masquerade": "1",
    "default_route_metric_base": "100",
    "enabled": "0",
}


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_schema():
    conn = get_db()
    try:
        conn.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO wan_settings (key, value) VALUES (?, ?)", (k, v))
        conn.commit()
    finally:
        conn.close()


def get_all_settings():
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM wan_settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def get_setting(key, default=""):
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM wan_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key, value):
    conn = get_db()
    try:
        conn.execute("INSERT OR REPLACE INTO wan_settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
    finally:
        conn.close()


def add_wan_interface(name, display_name, interface, gateway, weight=100, role="active",
                      priority=100, health_check_ip="8.8.8.8", metric_base=100, enabled=1, notes=""):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO wan_interfaces (name, display_name, interface, gateway, weight, role, priority, "
            "health_check_ip, metric_base, enabled, status, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, display_name, interface, gateway, weight, role, priority,
             health_check_ip, metric_base, enabled, "unknown", notes)
        )
        conn.commit()
        return True, f"WAN interface '{name}' added (id={cur.lastrowid})"
    except sqlite3.IntegrityError:
        return False, f"WAN interface '{name}' already exists"
    finally:
        conn.close()


def delete_wan_interface(iface_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM wan_interfaces WHERE id=?", (iface_id,))
        conn.execute("DELETE FROM wan_events WHERE interface IN (SELECT name FROM wan_interfaces WHERE id=?)", (iface_id,))
        conn.commit()
        return True, "Deleted"
    finally:
        conn.close()


def update_wan_interface(iface_id, **kwargs):
    conn = get_db()
    try:
        iface = conn.execute("SELECT * FROM wan_interfaces WHERE id=?", (iface_id,)).fetchone()
        if not iface:
            return False, "Interface not found"
        allowed = {"display_name", "gateway", "weight", "role", "priority",
                   "health_check_ip", "health_check_method", "health_check_interval",
                   "health_check_timeout", "health_check_fail_count", "metric_base",
                   "enabled", "notes", "name", "interface"}
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return False, "No valid fields to update"
        vals.append(iface_id)
        conn.execute(f"UPDATE wan_interfaces SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        return True, "Updated"
    finally:
        conn.close()


def get_wan_interfaces():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM wan_interfaces ORDER BY priority, id").fetchall()]
    finally:
        conn.close()


def get_wan_interface(iface_id):
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM wan_interfaces WHERE id=?", (iface_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def log_event(interface, event, old_status="", new_status="", details=""):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO wan_events (ts, interface, event, old_status, new_status, details) VALUES (?,?,?,?,?,?)",
            (time.time(), interface, event, old_status, new_status, details)
        )
        conn.commit()
    finally:
        conn.close()


def get_events(limit=100, interface=None):
    conn = get_db()
    try:
        if interface:
            rows = conn.execute(
                "SELECT * FROM wan_events WHERE interface=? ORDER BY ts DESC LIMIT ?",
                (interface, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM wan_events ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_status():
    ifaces = get_wan_interfaces()
    settings = get_all_settings()
    active_count = sum(1 for i in ifaces if i["status"] == "up" and i["enabled"])
    total_count = sum(1 for i in ifaces if i["enabled"])
    return {
        "interfaces": ifaces,
        "settings": settings,
        "active_count": active_count,
        "total_count": total_count,
        "enabled": settings.get("enabled", "0") == "1",
        "failover_mode": settings.get("failover_mode", "auto"),
        "lb_algorithm": settings.get("lb_algorithm", "weighted"),
    }


def health_check_single(iface):
    ip = iface["health_check_ip"] or "8.8.8.8"
    timeout = iface.get("health_check_timeout", 3) or 3
    method = iface.get("health_check_method", "ping") or "ping"
    source_ip = None
    try:
        out = subprocess.run(["ip", "addr", "show", iface["interface"]],
                             capture_output=True, text=True, timeout=5)
        for line in out.stdout.splitlines():
            line = line.strip()
            if line.startswith("inet ") and "scope global" in line:
                source_ip = line.split()[1].split("/")[0]
                break
    except Exception:
        pass
    if not source_ip:
        return False, "No IP on interface"
    if method == "ping":
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", str(timeout), "-I", iface["interface"], ip],
                capture_output=True, text=True, timeout=timeout + 2
            )
            ok = result.returncode == 0
            return ok, "up" if ok else "ping failed"
        except subprocess.TimeoutExpired:
            return False, "ping timeout"
        except Exception as e:
            return False, str(e)
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), "-I", iface["interface"], ip],
            capture_output=True, text=True, timeout=timeout + 2
        )
        return result.returncode == 0, "up" if result.returncode == 0 else "check failed"
    except Exception as e:
        return False, str(e)


def run_health_checks():
    ifaces = get_wan_interfaces()
    for iface in ifaces:
        if not iface["enabled"]:
            continue
        ok, detail = health_check_single(iface)
        old_status = iface["status"]
        fail_count = int(iface.get("health_check_fail_count", 3) or 3)
        ok_count = int(iface.get("health_check_ok_count", 2) or 2)
        conn = get_db()
        try:
            cur_status = iface["status"]
            cur_fails = iface["consecutive_fails"] or 0
            cur_oks = iface["consecutive_oks"] or 0
            if ok:
                new_oks = cur_oks + 1
                new_fails = 0
                if cur_status != "up" and new_oks >= ok_count:
                    cur_status = "up"
                    log_event(iface["name"], "up", old_status, "up", detail)
                elif cur_status != "up":
                    pass
            else:
                new_fails = cur_fails + 1
                new_oks = 0
                if cur_status != "down" and new_fails >= fail_count:
                    cur_status = "down"
                    log_event(iface["name"], "down", old_status, "down", detail)
                elif cur_status != "down":
                    pass
            conn.execute(
                "UPDATE wan_interfaces SET status=?, consecutive_fails=?, consecutive_oks=?, "
                "last_check=?, current_metric=?, last_up=CASE WHEN ?='up' THEN ? ELSE last_up END, "
                "last_down=CASE WHEN ?='down' THEN ? ELSE last_down END WHERE id=?",
                (cur_status, new_fails, new_oks, time.time(),
                 iface["metric_base"] if cur_status == "up" else 9999,
                 cur_status, time.time(), cur_status, time.time(), iface["id"])
            )
            conn.commit()
        finally:
            conn.close()
    apply_routing()


def apply_routing():
    ifaces = get_wan_interfaces()
    settings = get_all_settings()
    if settings.get("enabled", "0") != "1":
        return
    nat = settings.get("nat_masquerade", "1") == "1"
    for t in (100, 101, 102):
        try:
            subprocess.run(["ip", "route", "flush", "table", str(t)], capture_output=True, timeout=5)
        except Exception:
            pass
    try:
        result = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5)
        existing_defaults = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
    except Exception:
        existing_defaults = []
    for line in existing_defaults:
        parts = line.split()
        via = dev = metric = None
        for i, p in enumerate(parts):
            if p == "via":
                via = parts[i + 1] if i + 1 < len(parts) else None
            elif p == "dev":
                dev = parts[i + 1] if i + 1 < len(parts) else None
            elif p == "metric":
                metric = parts[i + 1] if i + 1 < len(parts) else None
        if dev:
            try:
                cmd = ["ip", "route", "del", "default", "dev", dev]
                if metric:
                    cmd += ["metric", metric]
                subprocess.run(cmd, capture_output=True, timeout=3)
            except Exception:
                pass
    up_ifaces = [i for i in ifaces if i["enabled"] and i["status"] == "up"]
    if not up_ifaces:
        fallback = [i for i in ifaces if i["enabled"]]
        if fallback:
            f = fallback[0]
            try:
                subprocess.run(["ip", "route", "add", "default", "via", f["gateway"],
                               "dev", f["interface"], "metric", "999"],
                              capture_output=True, timeout=3)
            except Exception:
                pass
        return
    for idx, iface in enumerate(sorted(up_ifaces, key=lambda x: x["priority"])):
        metric = iface["metric_base"] + (idx * 10)
        try:
            subprocess.run(["ip", "route", "add", "default", "via", iface["gateway"],
                           "dev", iface["interface"], "metric", str(metric)],
                          capture_output=True, timeout=3)
        except Exception:
            pass
        try:
            table = 100 + idx
            subprocess.run(["ip", "route", "add", "to", iface["gateway"] + "/32",
                           "dev", iface["interface"], "table", str(table)],
                          capture_output=True, timeout=3)
            subprocess.run(["ip", "route", "add", "default", "via", iface["gateway"],
                           "dev", iface["interface"], "table", str(table)],
                          capture_output=True, timeout=3)
        except Exception:
            pass
    if nat:
        try:
            subprocess.run(["iptables", "-t", "nat", "-F", "POSTROUTING"], capture_output=True, timeout=3)
        except Exception:
            pass
        for iface in up_ifaces:
            try:
                subprocess.run(["iptables", "-t", "nat", "-A", "POSTROUTING",
                               "-o", iface["interface"], "-j", "MASQUERADE"],
                              capture_output=True, timeout=3)
            except Exception:
                pass


_check_thread = None
_check_stop = threading.Event()


def start_health_monitor():
    global _check_thread
    if _check_thread and _check_thread.is_alive():
        return
    _check_stop.clear()

    def _loop():
        while not _check_stop.is_set():
            try:
                settings = get_all_settings()
                if settings.get("enabled", "0") == "1":
                    run_health_checks()
            except Exception as e:
                log.error(f"Health check error: {e}")
            interval = int(get_setting("check_interval", "5") or 5)
            _check_stop.wait(max(interval, 3))

    _check_thread = threading.Thread(target=_loop, daemon=True)
    _check_thread.start()


def stop_health_monitor():
    _check_stop.set()