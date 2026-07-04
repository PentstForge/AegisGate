#!/usr/bin/env python3
import json
import os
import time

from .dns_db import get_db, add_event
from .dns_rules import add_rule


SURICATA_DNS_ALERTS_FILE = "/var/log/ram/dns-suricata-alerts.json"
CROWDSEC_BANS_FILE = "/var/log/ram/crowdsec-bans.json"


def process_suricata_dns_alerts(limit=100):
    alerts = []
    if not os.path.exists(SURICATA_DNS_ALERTS_FILE):
        return alerts
    try:
        with open(SURICATA_DNS_ALERTS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    alert = json.loads(line)
                    if alert.get("alert", {}).get("category") == "dns" or "dns" in alert.get("alert", {}).get("signature", "").lower():
                        alerts.append(alert)
                        if len(alerts) >= limit:
                            break
                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception:
        pass
    return alerts


def auto_block_from_suricata(domain=None, ip=None, reason="Suricata DNS alert"):
    if domain:
        ok, rid = add_rule("exact", domain, "block", category="suricata", source="suricata", comment=reason)
        if ok:
            add_event("suricata_auto_block", "warning", "dns_suricata",
                       f"Auto-blocked domain {domain}: {reason}",
                       data={"domain": domain, "reason": reason})
            return True, f"Auto-blocked {domain}"
    if ip:
        from .allowlist import add_ip as add_allowlist_ip
        pass
    return False, "No domain or IP provided"


def process_crowdsec_bans():
    banned = []
    if not os.path.exists(CROWDSEC_BANS_FILE):
        return banned
    try:
        with open(CROWDSEC_BANS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ban = json.loads(line)
                    banned.append(ban)
                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception:
        pass
    return banned


def add_dns_event_from_suricata(alert):
    add_event(
        "suricata_dns_alert",
        "warning",
        "dns_suricata",
        f"DNS alert: {alert.get('alert', {}).get('signature', 'unknown')}",
        data=alert,
    )


def get_dns_events(limit=50, event_type=None, severity=None):
    conn = get_db()
    try:
        query = "SELECT * FROM dns_events WHERE 1=1"
        params = []
        if event_type:
            query += " AND event_type=?"
            params.append(event_type)
        if severity:
            query += " AND severity=?"
            params.append(severity)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()