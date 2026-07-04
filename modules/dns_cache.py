#!/usr/bin/env python3
import hashlib
import json
import time

from .dns_db import get_db

CACHE_TTL = 300


def _cache_key(domain, client_ip=None, qtype="A", policy_version=0):
    raw = f"{domain}|{client_ip or ''}|{qtype}|{policy_version}"
    return hashlib.md5(raw.encode()).hexdigest()


def get_decision(domain, client_ip=None, qtype="A", policy_version=0):
    key = _cache_key(domain, client_ip, qtype, policy_version)
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM dns_decision_cache WHERE key=? AND expires_at>?",
            (key, int(time.time())),
        ).fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


def set_decision(domain, action, reason="", client_ip=None, qtype="A",
                 policy_version=0, rule_id=None, list_id=None,
                 response_json=None, ttl=None):
    key = _cache_key(domain, client_ip, qtype, policy_version)
    now = int(time.time())
    expires = now + (ttl or CACHE_TTL)
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO dns_decision_cache (key, client_ref, domain, qtype, action, reason, rule_id, list_id, response_json, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, client_ip, domain, qtype, action, reason, rule_id, list_id,
             json.dumps(response_json) if response_json else None, expires, now),
        )
        conn.commit()
    finally:
        conn.close()


def invalidate_cache():
    conn = get_db()
    try:
        conn.execute("DELETE FROM dns_decision_cache")
        conn.commit()
        return True
    finally:
        conn.close()


def cleanup_cache():
    now = int(time.time())
    conn = get_db()
    try:
        deleted = conn.execute("DELETE FROM dns_decision_cache WHERE expires_at<?", (now,)).rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def cache_stats():
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) as c FROM dns_decision_cache").fetchone()["c"]
        expired = conn.execute("SELECT COUNT(*) as c FROM dns_decision_cache WHERE expires_at<?", (int(time.time()),)).fetchone()["c"]
        return {"total": total, "expired": expired, "active": total - expired}
    finally:
        conn.close()