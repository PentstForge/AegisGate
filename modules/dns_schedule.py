#!/usr/bin/env python3
import ipaddress
import json
import subprocess
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from .dns_db import get_db, add_event


TABLE = "aegis_dns_schedule"
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_time(value, default):
    try:
        hour, minute = str(value).split(":", 1)
        return dtime(int(hour), int(minute[:2]))
    except (ValueError, TypeError):
        return default


def schedule_is_active(schedule, now=None):
    if not schedule or not schedule.get("enabled"):
        return False
    tz_name = schedule.get("timezone") or "Europe/Kiev"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now = now.astimezone(tz) if now else datetime.now(tz)
    day = WEEKDAYS[now.weekday()]
    offline_days = schedule.get("offline_days") or []
    if day not in offline_days:
        return False

    start = _parse_time(schedule.get("offline_start"), dtime(22, 0))
    end = _parse_time(schedule.get("offline_end"), dtime(7, 0))
    current = now.time().replace(second=0, microsecond=0)
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def get_scheduled_blocks(now=None):
    conn = get_db()
    try:
        policies = [dict(r) for r in conn.execute("SELECT * FROM dns_policies WHERE enabled=1").fetchall()]
        active_policy_ids = []
        for policy in policies:
            try:
                schedule = json.loads(policy.get("schedule_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                schedule = {}
            if schedule_is_active(schedule, now=now):
                active_policy_ids.append(policy["id"])

        if not active_policy_ids:
            return []

        placeholders = ",".join("?" for _ in active_policy_ids)
        rows = conn.execute(
            f"SELECT id, name, ip, cidr, policy_id FROM dns_clients WHERE enabled=1 AND policy_id IN ({placeholders})",
            active_policy_ids,
        ).fetchall()
        blocks = []
        for row in rows:
            client = dict(row)
            for key in ("ip", "cidr"):
                value = (client.get(key) or "").strip()
                if not value:
                    continue
                try:
                    net = ipaddress.ip_network(value, strict=False)
                except ValueError:
                    continue
                blocks.append({"client_id": client["id"], "client_name": client.get("name"), "network": str(net), "version": net.version})
        return blocks
    finally:
        conn.close()


def _run_nft(content):
    return subprocess.run(["nft", "-f", "-"], input=content, text=True, capture_output=True, timeout=10)


def apply_schedules(now=None):
    blocks = get_scheduled_blocks(now=now)
    subprocess.run(["nft", "delete", "table", "inet", TABLE], capture_output=True, text=True, timeout=5)
    if not blocks:
        add_event("schedule_applied", "info", "dns_schedule", "No active DNS family schedule blocks")
        return True, "No active schedule blocks", []

    ipv4 = sorted({b["network"] for b in blocks if b["version"] == 4})
    ipv6 = sorted({b["network"] for b in blocks if b["version"] == 6})
    lines = [
        f"table inet {TABLE} {{",
        "  chain forward {",
        "    type filter hook forward priority -5; policy accept;",
    ]
    if ipv4:
        lines.append(f"    ip saddr {{ {', '.join(ipv4)} }} drop comment \"AegisDNS scheduled internet block\"")
    if ipv6:
        lines.append(f"    ip6 saddr {{ {', '.join(ipv6)} }} drop comment \"AegisDNS scheduled internet block\"")
    lines.extend(["  }", "}", ""])
    result = _run_nft("\n".join(lines))
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "nft failed").strip()
        add_event("schedule_apply_failed", "high", "dns_schedule", msg, {"blocks": blocks})
        return False, msg, blocks
    add_event("schedule_applied", "info", "dns_schedule", f"Applied scheduled internet block for {len(blocks)} clients", {"blocks": blocks})
    return True, f"Applied scheduled internet block for {len(blocks)} clients", blocks
