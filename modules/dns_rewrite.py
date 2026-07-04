#!/usr/bin/env python3
import json

from .dns_db import get_db


def get_safe_search_config(policy_id=None):
    if policy_id:
        conn = get_db()
        try:
            row = conn.execute("SELECT safe_search_json FROM dns_policies WHERE id=?", (policy_id,)).fetchone()
            if row and row["safe_search_json"]:
                return json.loads(row["safe_search_json"])
        except (json.JSONDecodeError, TypeError):
            pass
        finally:
            conn.close()
    return {"enabled": False}


def set_safe_search_config(policy_id, config):
    from .dns_policy import update_policy
    return update_policy(policy_id, safe_search_json=json.dumps(config))


def get_blocked_services(policy_id=None):
    if policy_id:
        conn = get_db()
        try:
            row = conn.execute("SELECT blocked_services_json FROM dns_policies WHERE id=?", (policy_id,)).fetchone()
            if row and row["blocked_services_json"]:
                return json.loads(row["blocked_services_json"])
        except (json.JSONDecodeError, TypeError):
            pass
        finally:
            conn.close()
    return []


def set_blocked_services(policy_id, services):
    from .dns_policy import update_policy
    return update_policy(policy_id, blocked_services_json=json.dumps(services))


def generate_safe_search_rewrites(safe_search_config):
    from .dns_policy import SAFE_SEARCH_DOMAINS
    rewrites = []
    if not safe_search_config.get("enabled"):
        return rewrites
    for engine, enabled in safe_search_config.items():
        if engine == "enabled" or not enabled:
            continue
        engine_domains = SAFE_SEARCH_DOMAINS.get(engine, {})
        for safe_domain, orig_domain in engine_domains.items():
            rewrites.append({"domain": orig_domain, "target": safe_domain, "rtype": "CNAME"})
    return rewrites


def generate_blocked_service_rules(services):
    from .dns_policy import BLOCKED_SERVICES
    domains = []
    for svc in services:
        if svc in BLOCKED_SERVICES:
            domains.extend(BLOCKED_SERVICES[svc])
    return domains