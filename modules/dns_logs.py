#!/usr/bin/env python3
import os
import re
import time
import json

from .dns_db import get_db, add_event

LOG_FILE = "/var/log/ram/dnsmasq-queries.log"

DNSLOG_RE = re.compile(
    r'^(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(.*))?$'
)

DNSMASQ_LOG_RE = re.compile(
    r'^(\S+)\s+(\S+)\s+(\S+)\s+\[?(\S*?)\]?\s+([A-Z]+)\s+(\S+)\s+is\s+(\S+)\s+(.*)$'
)

DNSMASQ_REPLY_RE = re.compile(
    r'^(\S+)\s+(\S+)\s+(\S+)\s+\[?(\S*?)\]?\s+([A-Z]+)\s+(\S+)\s+reply\s+(\S+)\s+is\s+(.*)$'
)

RE_QUERY = re.compile(
    r'^(\w+\s+\d+\s+\d+:\d+:\d+)\s+\S+\[\d+\]:\s+(\d+)\s+(\S+)/(\d+)\s+query\[(\w+)\]\s+(\S+)\s+from\s+(\S+)'
)
RE_CONFIG = re.compile(
    r'^(\w+\s+\d+\s+\d+:\d+:\d+)\s+\S+\[\d+\]:\s+(\d+)\s+(\S+)/(\d+)\s+config\s+(\S+)\s+is\s+(\S+)'
)
RE_FORWARDED = re.compile(
    r'^(\w+\s+\d+\s+\d+:\d+:\d+)\s+\S+\[\d+\]:\s+(\d+)\s+(\S+)/(\d+)\s+forwarded\s+(\S+)\s+to\s+(\S+)'
)
RE_REPLY = re.compile(
    r'^(\w+\s+\d+\s+\d+:\d+:\d+)\s+\S+\[\d+\]:\s+(\d+)\s+(\S+)/(\d+)\s+reply\s+(\S+)\s+is\s+(.+)'
)
RE_BLOCKED = re.compile(
    r'^(\w+\s+\d+\s+\d+:\d+:\d+)\s+\S+\[\d+\]:\s+(\d+)\s+(\S+)/(\d+)\s+(?:config|blocked)\s+(\S+)\s+is\s+(0\.0\.0\.0|NXDOMAIN|refused|\S+)'
)


def parse_timestamp(ts_str):
    try:
        import datetime
        dt = datetime.datetime.strptime(ts_str, "%b %d %H:%M:%S")
        dt = dt.replace(year=time.localtime().tm_year)
        return int(dt.timestamp())
    except Exception:
        return int(time.time())


def parse_dnsmasq_log_line(line):
    line = line.strip()
    if not line:
        return None
    if line.startswith("dnsmasq[") or "started" in line or "compile" in line or "read " in line or "using " in line or "cache" in line.lower():
        return None

    m = RE_QUERY.match(line)
    if m:
        ts = parse_timestamp(m.group(1))
        client_ip = m.group(7).split("/")[0] if "/" in m.group(7) else m.group(7)
        domain = m.group(6)
        qtype = m.group(5)
        return {"ts": ts, "client_ip": client_ip, "domain": domain, "qtype": qtype, "action": "query", "reason": "", "response": "", "raw": line}

    m = RE_CONFIG.match(line)
    if m:
        ts = parse_timestamp(m.group(1))
        client_ip = m.group(3).split("/")[0] if "/" in m.group(3) else m.group(3)
        domain = m.group(5)
        result = m.group(6)
        action = "block" if result in ("0.0.0.0", "NXDOMAIN", "refused", "::") else "allow"
        reason = result if action == "block" else "local"
        qtype = "A"
        return {"ts": ts, "client_ip": client_ip, "domain": domain, "qtype": qtype, "action": action, "reason": reason, "response": result, "raw": line}

    m = RE_BLOCKED.match(line)
    if m:
        ts = parse_timestamp(m.group(1))
        client_ip = m.group(3).split("/")[0] if "/" in m.group(3) else m.group(3)
        domain = m.group(5)
        result = m.group(6)
        action = "block" if result in ("0.0.0.0", "NXDOMAIN", "refused", "::") else "allow"
        reason = result if action == "block" else "local"
        qtype = "A"
        return {"ts": ts, "client_ip": client_ip, "domain": domain, "qtype": qtype, "action": action, "reason": reason, "response": result, "raw": line}

    m = RE_FORWARDED.match(line)
    if m:
        ts = parse_timestamp(m.group(1))
        client_ip = m.group(3).split("/")[0] if "/" in m.group(3) else m.group(3)
        domain = m.group(5)
        upstream = m.group(6)
        return {"ts": ts, "client_ip": client_ip, "domain": domain, "qtype": "A", "action": "forwarded", "reason": "", "response": upstream, "raw": line}

    m = RE_REPLY.match(line)
    if m:
        ts = parse_timestamp(m.group(1))
        client_ip = m.group(3).split("/")[0] if "/" in m.group(3) else m.group(3)
        domain = m.group(5)
        response = m.group(6).strip()
        return {"ts": ts, "client_ip": client_ip, "domain": domain, "qtype": "A", "action": "reply", "reason": "", "response": response, "raw": line}

    ts_match = re.search(r'^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})', line)
    if ts_match:
        ts_str = ts_match.group(1)
        rest = line[len(ts_match.group(0)):].strip()
    else:
        ts_str = None
        rest = line

    m = DNSMASQ_LOG_RE.match(rest)
    if m:
        client_ip = m.group(4) or m.group(2)
        qtype = m.group(5)
        domain = m.group(6)
        result = m.group(7)
        detail = m.group(8) if m.group(8) else ""
        action = "allow"
        reason = ""
        response = detail
        if result.lower() in ("nxdomain", "nxdomain"):
            action = "block"
            reason = "nxdomain"
        elif "0.0.0.0" in result or result == "NXDOMAIN" or result == "refused":
            action = "block"
            reason = result
        elif domain.endswith(".in-addr.arpa") or domain.endswith(".ip6.arpa"):
            action = "allow"
            reason = "ptr"
        return {"ts": ts_str or str(int(time.time())), "client_ip": client_ip, "domain": domain, "qtype": qtype, "action": action, "reason": reason, "response": response, "raw": line}

    m2 = DNSMASQ_REPLY_RE.match(rest)
    if m2:
        client_ip = m2.group(4) or m2.group(2)
        qtype = m2.group(5)
        domain = m2.group(6)
        response_ip = m2.group(8)
        return {"ts": ts_str or str(int(time.time())), "client_ip": client_ip, "domain": domain, "qtype": qtype, "action": "reply", "reason": "", "response": response_ip, "raw": line}

    return None


def parse_log_file(log_file=None, max_lines=50000):
    if log_file is None:
        log_file = LOG_FILE
    if not os.path.exists(log_file):
        return []

    entries = []
    try:
        with open(log_file, "r") as f:
            lines = f.readlines()
        start = max(0, len(lines) - max_lines)
        for line in lines[start:]:
            entry = parse_dnsmasq_log_line(line)
            if entry:
                entries.append(entry)
    except Exception:
        pass
    return entries


def batch_insert_queries(entries, batch_size=100):
    if not entries:
        return 0
    from .dns_policy import resolve_client_name, resolve_client_mac
    conn = get_db()
    inserted = 0
    try:
        now = int(time.time())
        min_ts = min((int(e.get("ts", now)) for e in entries if e.get("ts")), default=now)
        seen = set()
        for row in conn.execute(
            "SELECT ts, client_ip, domain, action FROM dns_queries WHERE ts >= ?",
            (min_ts - 1,),
        ).fetchall():
            seen.add((row[0], row[1], row[2], row[3]))
        client_cache = {}
        for i in range(0, len(entries), batch_size):
            batch = entries[i:i + batch_size]
            rows = []
            for e in batch:
                ts = e.get("ts", str(now))
                try:
                    ts_int = int(ts)
                except (ValueError, TypeError):
                    ts_int = now
                client_ip = e.get("client_ip", "")
                domain = e.get("domain", "")
                action = e.get("action", "allow")
                key = (ts_int, client_ip, domain, action)
                if key in seen:
                    continue
                seen.add(key)
                if client_ip and client_ip not in client_cache:
                    client_cache[client_ip] = {
                        "name": resolve_client_name(client_ip),
                        "mac": resolve_client_mac(client_ip),
                    }
                ci = client_cache.get(client_ip, {})
                rows.append((
                    ts_int,
                    client_ip,
                    e.get("client_mac", "") or ci.get("mac", ""),
                    e.get("client_name", "") or ci.get("name", ""),
                    domain,
                    e.get("qtype", "A"),
                    action,
                    e.get("reason", ""),
                    "",  # policy
                    None,  # rule_id
                    None,  # list_id
                    e.get("upstream", "") or e.get("response", "") if e.get("action") == "forwarded" else "",
                    e.get("response", ""),
                    "",  # rcode
                    None,  # latency_ms
                    None,  # blocked_categories
                    None,  # cname_chain
                ))
            if rows:
                conn.executemany(
                    "INSERT INTO dns_queries (ts, client_ip, client_mac, client_name, domain, qtype, action, reason, policy, rule_id, list_id, upstream, response, rcode, latency_ms, blocked_categories, cname_chain) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                inserted += len(rows)
        conn.commit()
    finally:
        conn.close()
    return inserted


def cleanup_old_queries(days=30):
    cutoff = int(time.time()) - (days * 86400)
    conn = get_db()
    try:
        deleted = conn.execute("DELETE FROM dns_queries WHERE ts < ?", (cutoff,)).rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def get_query_stats(hours=24):
    cutoff = int(time.time()) - (hours * 3600)
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) as c FROM dns_queries WHERE ts > ?", (cutoff,)).fetchone()["c"]
        blocked = conn.execute("SELECT COUNT(*) as c FROM dns_queries WHERE ts > ? AND action='block'", (cutoff,)).fetchone()["c"]
        top_domains = [dict(r) for r in conn.execute(
            "SELECT domain, COUNT(*) as cnt FROM dns_queries WHERE ts > ? AND domain NOT LIKE '%.in-addr.arpa' AND domain NOT LIKE '%_dns-sd._udp.%' GROUP BY domain ORDER BY cnt DESC LIMIT 20",
            (cutoff,),
        ).fetchall()]
        top_clients = [dict(r) for r in conn.execute(
            "SELECT client_ip, COUNT(*) as cnt, SUM(CASE WHEN action='block' THEN 1 ELSE 0 END) as blocked FROM dns_queries WHERE ts > ? GROUP BY client_ip ORDER BY cnt DESC LIMIT 20",
            (cutoff,),
        ).fetchall()]
        top_blocked = [dict(r) for r in conn.execute(
            "SELECT domain, COUNT(*) as cnt FROM dns_queries WHERE ts > ? AND action='block' GROUP BY domain ORDER BY cnt DESC LIMIT 20",
            (cutoff,),
        ).fetchall()]
        qtype_breakdown = [dict(r) for r in conn.execute(
            "SELECT qtype, COUNT(*) as cnt FROM dns_queries WHERE ts > ? GROUP BY qtype ORDER BY cnt DESC LIMIT 10",
            (cutoff,),
        ).fetchall()]
        upstream_breakdown = [dict(r) for r in conn.execute(
            "SELECT upstream, COUNT(*) as cnt FROM dns_queries WHERE ts > ? AND upstream IS NOT NULL AND upstream != '' GROUP BY upstream ORDER BY cnt DESC LIMIT 10",
            (cutoff,),
        ).fetchall()]
        hourly = [dict(r) for r in conn.execute(
            "SELECT (ts/3600)*3600 as hour, COUNT(*) as total, SUM(CASE WHEN action='block' THEN 1 ELSE 0 END) as blocked FROM dns_queries WHERE ts > ? GROUP BY hour ORDER BY hour ASC",
            (cutoff,),
        ).fetchall()]
        return {
            "total": total,
            "blocked": blocked,
            "block_pct": round(blocked / total * 100, 1) if total > 0 else 0,
            "top_domains": top_domains,
            "top_clients": top_clients,
            "top_blocked": top_blocked,
            "qtype_breakdown": qtype_breakdown,
            "upstream_breakdown": upstream_breakdown,
            "hourly": hourly,
        }
    finally:
        conn.close()


def get_recent_queries(limit=100, offset=0, action=None, client=None, domain=None, qtype=None):
    conn = get_db()
    try:
        query = "SELECT * FROM dns_queries WHERE 1=1"
        params = []
        if action:
            query += " AND action=?"
            params.append(action)
        if client:
            query += " AND client_ip LIKE ?"
            params.append(f"%{client}%")
        if domain:
            query += " AND domain LIKE ?"
            params.append(f"%{domain}%")
        if qtype:
            query += " AND qtype=?"
            params.append(qtype)
        query += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def clear_queries(before_ts=None):
    conn = get_db()
    try:
        if before_ts:
            result = conn.execute("DELETE FROM dns_queries WHERE ts < ?", (before_ts,))
        else:
            result = conn.execute("DELETE FROM dns_queries")
        conn.commit()
        return result.rowcount
    finally:
        conn.close()


def export_queries(fmt="json", limit=10000):
    conn = get_db()
    try:
        queries = [dict(r) for r in conn.execute(
            "SELECT * FROM dns_queries ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()]
        if fmt == "csv":
            lines = ["ts,client_ip,domain,qtype,action,reason,upstream,latency_ms"]
            for q in queries:
                lines.append(f"{q.get('ts','')},{q.get('client_ip','')},{q.get('domain','')},{q.get('qtype','')},{q.get('action','')},{q.get('reason','')},{q.get('upstream','')},{q.get('latency_ms','')}")
            return {"format": "csv", "data": "\n".join(lines), "count": len(queries)}
        return {"format": "json", "data": queries, "count": len(queries)}
    finally:
        conn.close()