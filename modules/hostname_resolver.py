#!/usr/bin/env python3
import sqlite3
import time
import socket
import json
import subprocess
import logging
import re

log = logging.getLogger(__name__)

DB_PATH = "/opt/nft-dashboard/data/dns.db"
NFT_TABLE = "inet filter"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS hostname_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT NOT NULL UNIQUE,
    resolved_ip TEXT,
    rule_type TEXT NOT NULL DEFAULT 'both',
    dns_policy_id INTEGER,
    firewall_action TEXT NOT NULL DEFAULT '',
    comment TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_resolved_ts INTEGER,
    last_error TEXT,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_hostname_rules_hostname ON hostname_rules(hostname);
CREATE INDEX IF NOT EXISTS idx_hostname_rules_enabled ON hostname_rules(enabled);
"""

_MIGRATE_DNS_CLIENTS = [
    "ALTER TABLE dns_clients ADD COLUMN hostname TEXT",
    "ALTER TABLE dns_clients ADD COLUMN track_hostname INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE dns_clients ADD COLUMN firewall_action TEXT DEFAULT ''",
]


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = _get_db()
    try:
        conn.executescript(_CREATE_TABLE)
        for sql in _MIGRATE_DNS_CLIENTS:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass
        conn.commit()
    finally:
        conn.close()


def resolve_hostname(hostname, timeout=3):
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
        ips = list(set(r[4][0] for r in results))
        return ips if ips else None
    except (socket.gaierror, socket.herror, OSError):
        pass
    try:
        out = subprocess.run(
            ["dig", "+short", "+timeout=" + str(timeout), "@127.0.0.1", hostname, "A"],
            capture_output=True, text=True, timeout=timeout + 2,
        )
        ips = []
        for line in out.stdout.strip().splitlines():
            line = line.strip()
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", line):
                ips.append(line)
        return ips if ips else None
    except Exception:
        return None


def sanitize_nft_name(name):
    return re.sub(r"[^a-zA-Z0-9_]", "_", name.lower())[:48]


def nft_set_name(hostname):
    return f"hostname_{sanitize_nft_name(hostname)}_ipv4"


def _nft_cmd(args):
    try:
        r = subprocess.run(["nft"] + args, capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            log.warning("nft %s failed: %s", " ".join(args), r.stderr.strip())
        return r.returncode == 0
    except Exception as e:
        log.warning("nft %s error: %s", " ".join(args), e)
        return False


def ensure_nft_set(set_name):
    _nft_cmd(["add", "set", NFT_TABLE, set_name,
              "{ type ipv4_addr; flags interval; }"])


def update_nft_set(set_name, ips):
    _nft_cmd(["flush", "set", NFT_TABLE, set_name])
    if not ips:
        return
    elements = ", ".join(ips)
    _nft_cmd(["add", "element", NFT_TABLE, set_name, "{ " + elements + " }"])


def _add_nft_hostname_rule(set_name, action):
    comment = f"hostname_{set_name}"
    existing = _check_nft_rule_exists(set_name, comment)
    if existing:
        return True
    if action == "block":
        return _nft_cmd(["add", "rule", NFT_TABLE, "forward",
                         "ip", "saddr", "@" + set_name, "drop",
                         "comment", comment])
    elif action == "allow":
        return _nft_cmd(["add", "rule", NFT_TABLE, "forward",
                         "ip", "daddr", "@" + set_name, "accept",
                         "comment", comment])
    return False


def _remove_nft_hostname_rule(set_name):
    comment = f"hostname_{set_name}"
    existing = _check_nft_rule_exists(set_name, comment)
    if existing:
        _nft_cmd(["delete", "rule", NFT_TABLE, "forward",
                   "handle", str(existing)])
    return True


def _check_nft_rule_exists(set_name_part, comment_part):
    try:
        out = subprocess.run(
            ["nft", "-a", "list", "chain", NFT_TABLE, "forward"],
            capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.splitlines():
            if set_name_part in line and comment_part in line:
                match = re.search(r"# handle (\d+)", line)
                if match:
                    return int(match.group(1))
                parts = line.rsplit(" ", 1)
                if parts[-1].isdigit():
                    return int(parts[-1])
        return None
    except Exception:
        return None


def _apply_hostname_firewall(hostname, action, ips):
    if action not in ("block", "allow"):
        return True
    set_name = nft_set_name(hostname)
    ensure_nft_set(set_name)
    if ips:
        update_nft_set(set_name, ips)
    return _add_nft_hostname_rule(set_name, action)


def _remove_hostname_firewall(hostname):
    set_name = nft_set_name(hostname)
    _remove_nft_hostname_rule(set_name)
    _nft_cmd(["delete", "set", NFT_TABLE, set_name])


def add_hostname_rule(hostname, rule_type="both", firewall_action="",
                       dns_policy_id=None, comment=None):
    hostname = hostname.strip().lower()
    if not hostname:
        return False, "Hostname required"
    if firewall_action not in ("", "block", "allow"):
        return False, "firewall_action must be empty, 'block', or 'allow'"
    if rule_type not in ("dns", "firewall", "both"):
        return False, "rule_type must be 'dns', 'firewall', or 'both'"

    ips = resolve_hostname(hostname)
    now = int(time.time())
    resolved_ip = ", ".join(ips) if ips else None
    last_error = None if ips else "Resolution failed"

    conn = _get_db()
    try:
        existing = conn.execute(
            "SELECT id, firewall_action, rule_type, enabled FROM hostname_rules WHERE hostname=?",
            (hostname,),
        ).fetchone()

        if existing:
            old_fw = existing["firewall_action"]
            old_type = existing["rule_type"]
            if old_fw and old_type in ("firewall", "both"):
                _remove_hostname_firewall(hostname)

            conn.execute(
                "UPDATE hostname_rules SET rule_type=?, firewall_action=?, "
                "dns_policy_id=?, comment=?, resolved_ip=?, last_resolved_ts=?, "
                "last_error=?, enabled=1, updated_at=strftime('%s','now') "
                "WHERE hostname=?",
                (rule_type, firewall_action or "", dns_policy_id, comment,
                 resolved_ip, now if ips else None, last_error, hostname),
            )
            conn.commit()
        else:
            conn.execute(
                "INSERT INTO hostname_rules (hostname, rule_type, firewall_action, "
                "dns_policy_id, comment, resolved_ip, last_resolved_ts, last_error, enabled) "
                "VALUES (?,?,?,?,?,?,?,?,1)",
                (hostname, rule_type, firewall_action or "", dns_policy_id,
                 comment, resolved_ip, now if ips else None, last_error),
            )
            conn.commit()

        if rule_type in ("firewall", "both") and firewall_action and ips:
            _apply_hostname_firewall(hostname, firewall_action, ips)

        return True, existing["id"] if existing else conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except Exception as e:
        log.error("add_hostname_rule error: %s", e)
        return False, str(e)
    finally:
        conn.close()


def remove_hostname_rule(hostname):
    hostname = hostname.strip().lower()
    conn = _get_db()
    try:
        rule = conn.execute(
            "SELECT rule_type, firewall_action FROM hostname_rules WHERE hostname=?",
            (hostname,),
        ).fetchone()
        conn.execute("DELETE FROM hostname_rules WHERE hostname=?", (hostname,))
        conn.commit()
    finally:
        conn.close()

    if rule and rule["rule_type"] in ("firewall", "both") and rule["firewall_action"]:
        _remove_hostname_firewall(hostname)
    else:
        set_name = nft_set_name(hostname)
        _nft_cmd(["delete", "set", NFT_TABLE, set_name])

    return True, f"Removed {hostname}"


def toggle_hostname_rule(hostname, enabled):
    hostname = hostname.strip().lower()
    conn = _get_db()
    try:
        rule = conn.execute(
            "SELECT rule_type, firewall_action, resolved_ip FROM hostname_rules WHERE hostname=?",
            (hostname,),
        ).fetchone()
        if not rule:
            return False, f"Rule not found: {hostname}"

        conn.execute(
            "UPDATE hostname_rules SET enabled=?, updated_at=strftime('%s','now') WHERE hostname=?",
            (1 if enabled else 0, hostname),
        )
        conn.commit()

        if not enabled and rule["rule_type"] in ("firewall", "both") and rule["firewall_action"]:
            _remove_hostname_firewall(hostname)
        elif enabled and rule["rule_type"] in ("firewall", "both") and rule["firewall_action"]:
            ips = [ip.strip() for ip in (rule["resolved_ip"] or "").split(",") if ip.strip()]
            if ips:
                _apply_hostname_firewall(hostname, rule["firewall_action"], ips)

        return True, f"{'Enabled' if enabled else 'Disabled'} {hostname}"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def get_hostname_rules():
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM hostname_rules ORDER BY hostname"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def resolve_all():
    conn = _get_db()
    try:
        rules = conn.execute(
            "SELECT * FROM hostname_rules WHERE enabled=1"
        ).fetchall()
    finally:
        conn.close()

    results = []
    for rule in rules:
        hostname = rule["hostname"]
        ips = resolve_hostname(hostname)
        now = int(time.time())
        conn = _get_db()
        try:
            if ips:
                conn.execute(
                    "UPDATE hostname_rules SET resolved_ip=?, last_resolved_ts=?, last_error=NULL, updated_at=? WHERE hostname=?",
                    (", ".join(ips), now, now, hostname),
                )
                conn.commit()
            else:
                conn.execute(
                    "UPDATE hostname_rules SET last_error=?, updated_at=? WHERE hostname=?",
                    ("Resolution failed", now, hostname),
                )
                conn.commit()
        finally:
            conn.close()

        rule_type = rule["rule_type"]
        fw_action = rule["firewall_action"]

        if rule_type in ("firewall", "both") and fw_action:
            if ips:
                set_name = nft_set_name(hostname)
                ensure_nft_set(set_name)
                update_nft_set(set_name, ips)
                _add_nft_hostname_rule(set_name, fw_action)
            else:
                log.warning("Skipping firewall for %s: resolution failed", hostname)

        results.append({
            "hostname": hostname,
            "ips": ips,
            "rule_type": rule_type,
            "firewall_action": fw_action,
            "status": "ok" if ips else "resolve_failed",
        })

    return results


def get_tracked_clients():
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, ip, mac, hostname, track_hostname, firewall_action, "
            "policy_id, total_queries, blocked_queries, last_seen_ts "
            "FROM dns_clients WHERE (track_hostname=1) OR (hostname IS NOT NULL AND hostname != '') "
            "ORDER BY name"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            rule = conn.execute(
                "SELECT id FROM hostname_rules WHERE hostname=?",
                ((d.get("hostname") or "").strip().lower(),)
            ).fetchone()
            d["rule_exists"] = rule is not None
            result.append(d)
        return result
    finally:
        conn.close()


def set_client_hostname_tracking(client_id, track_hostname, firewall_action=""):
    conn = _get_db()
    try:
        client = conn.execute("SELECT id, name, hostname FROM dns_clients WHERE id=?", (client_id,)).fetchone()
        if not client:
            return False, "Client not found"
        if track_hostname and not client["name"] and not client["hostname"]:
            return False, "Client has no hostname"

        hostname = (client["hostname"] or client["name"] or "").strip().lower()
        updates = {"track_hostname": 1 if track_hostname else 0}
        if firewall_action is not None:
            updates["firewall_action"] = firewall_action
        sets = []
        vals = []
        for k, v in updates.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(client_id)
        conn.execute(
            f"UPDATE dns_clients SET {', '.join(sets)}, last_seen_ts=strftime('%s','now') WHERE id=?",
            vals,
        )
        conn.commit()

        if track_hostname and hostname:
            rule_type = "both" if firewall_action in ("block", "allow") else "dns"
            fw = firewall_action if firewall_action in ("block", "allow") else ""
            add_hostname_rule(hostname, rule_type=rule_type, firewall_action=fw)
        elif not track_hostname and hostname:
            remove_hostname_rule(hostname)

        return True, f"Hostname tracking {'enabled' if track_hostname else 'disabled'}"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def sync_dns_client_hostnames():
    try:
        from modules.dns_db import get_db as dns_get_db
        dns_conn = dns_get_db()
        clients = dns_conn.execute(
            "SELECT id, name, ip, hostname FROM dns_clients WHERE hostname IS NOT NULL AND hostname != ''"
        ).fetchall()
        dns_conn.close()
    except Exception:
        return
    for c in clients:
        hostname = (c["hostname"] or "").strip().lower()
        if not hostname:
            continue
        try:
            resolve_hostname(hostname)
        except Exception:
            pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    results = resolve_all()
    for r in results:
        log.info("%s: %s -> %s", r["status"], r["hostname"], r["ips"] or "FAILED")
    sync_dns_client_hostnames()