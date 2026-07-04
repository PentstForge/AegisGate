#!/usr/bin/env python3
import json
import os
import time

from .dns_db import get_db, get_setting, set_setting, get_all_settings, ensure_settings, add_event, DEFAULT_SETTINGS
from .dns_config import (
    write_all_configs, reload_dnsmasq, start_dnsmasq, stop_dnsmasq,
    get_dnsmasq_status, rollback_configs, ensure_dnsmasq_dir, BLOCKLIST_CONF,
    LOCAL_CONF, UPSTREAM_CONF, MAIN_CONF,
)
from .dns_rules import get_rules_for_config, add_rule, delete_rule, toggle_rule, get_rules, check_host, import_rules_from_text
from .dns_lists import add_list, delete_list, toggle_list, get_lists, get_list_by_id, download_list, update_all_lists, purge_list_rules
from .dns_policy import ensure_default_policies, get_all_policies, get_policy, add_policy, update_policy, delete_policy, add_client, update_client, delete_client, discover_clients_from_arp, get_safe_search_rewrites, get_blocked_service_domains, update_client_stats, resolve_client_name, resolve_client_mac, BLOCKED_SERVICES, SAFE_SEARCH_DOMAINS, parse_json_list, services_to_domains
from .dns_cache import get_decision, set_decision, invalidate_cache, cleanup_cache, cache_stats
from .dns_nft import get_dns_redirect_status
from .dhcp import get_static_leases


def init_dns():
    ensure_settings()
    ensure_dnsmasq_dir()
    ensure_default_policies()
    settings = get_all_settings()
    rules = get_rules_for_config()
    records = get_local_records()
    rewrites = get_rewrites()
    upstreams = get_upstreams()
    clients = get_clients()
    policies = get_policies()
    static_leases = get_static_leases() if settings.get("dhcp_enabled") else []
    rules = _with_service_rules(rules, settings, clients, policies)
    return write_all_configs(settings, rules=rules, records=records, rewrites=rewrites,
                             upstreams=upstreams, clients=clients, policies=policies,
                             static_leases=static_leases)


def toggle_dns(enabled=None):
    settings = get_all_settings()
    if enabled is None:
        enabled = not settings.get("dns_enabled", False)
    settings["dns_enabled"] = enabled
    set_setting("dns_enabled", enabled)
    if enabled:
        init_dns()
        ok, msg = start_dnsmasq()
        add_event("dns_toggled", "info", "dns", f"DNS {'enabled' if enabled else 'disabled'}")
        return ok, msg
    else:
        ok, msg = stop_dnsmasq()
        add_event("dns_toggled", "info", "dns", f"DNS disabled")
        return ok, msg


def get_status(period_seconds=None):
    if period_seconds is None:
        period_seconds = 86400
    settings = get_all_settings()
    dm_status = get_dnsmasq_status()
    conn = get_db()
    try:
        total_rules = conn.execute("SELECT COUNT(*) as c FROM dns_rules WHERE enabled=1").fetchone()["c"]
        block_rules = conn.execute("SELECT COUNT(*) as c FROM dns_rules WHERE action='block' AND enabled=1").fetchone()["c"]
        allow_rules = conn.execute("SELECT COUNT(*) as c FROM dns_rules WHERE action='allow' AND enabled=1").fetchone()["c"]
        rewrite_rules = conn.execute("SELECT COUNT(*) as c FROM dns_rules WHERE action='rewrite' AND enabled=1").fetchone()["c"]
        total_lists = conn.execute("SELECT COUNT(*) as c FROM dns_lists").fetchone()["c"]
        enabled_lists = conn.execute("SELECT COUNT(*) as c FROM dns_lists WHERE enabled=1").fetchone()["c"]
        total_clients = conn.execute("SELECT COUNT(*) as c FROM dns_clients").fetchone()["c"]
        total_policies = conn.execute("SELECT COUNT(*) as c FROM dns_policies").fetchone()["c"]
        total_queries = conn.execute("SELECT COUNT(*) as c FROM dns_queries").fetchone()["c"]
        blocked_queries = conn.execute("SELECT COUNT(*) as c FROM dns_queries WHERE action='block'").fetchone()["c"]
        local_records = conn.execute("SELECT COUNT(*) as c FROM dns_local_records WHERE enabled=1").fetchone()["c"]
        rewrites = conn.execute("SELECT COUNT(*) as c FROM dns_rewrites WHERE enabled=1").fetchone()["c"]
        upstreams = conn.execute("SELECT COUNT(*) as c FROM dns_upstream WHERE enabled=1").fetchone()["c"]
        queries_24h = conn.execute(
            "SELECT COUNT(*) as c FROM dns_queries WHERE ts>?",
            (int(time.time()) - period_seconds,),
        ).fetchone()["c"]
        blocked_24h = conn.execute(
            "SELECT COUNT(*) as c FROM dns_queries WHERE action='block' AND ts>?",
            (int(time.time()) - period_seconds,),
        ).fetchone()["c"]
        queries_1h = conn.execute(
            "SELECT COUNT(*) as c FROM dns_queries WHERE ts>?",
            (int(time.time()) - min(period_seconds, 3600),),
        ).fetchone()["c"]
        blocked_1h = conn.execute(
            "SELECT COUNT(*) as c FROM dns_queries WHERE action='block' AND ts>?",
            (int(time.time()) - min(period_seconds, 3600),),
        ).fetchone()["c"]
        top_domains = [dict(r) for r in conn.execute(
            "SELECT domain, COUNT(*) as cnt FROM dns_queries WHERE ts>? AND domain NOT LIKE '%.in-addr.arpa' AND domain NOT LIKE '%_dns-sd._udp.%' GROUP BY domain ORDER BY cnt DESC LIMIT 10",
            (int(time.time()) - period_seconds,),
        ).fetchall()]
        lan_subnets = []
        try:
            from .dhcp import get_dhcp_status
            for scope in get_dhcp_status().get("scopes", []):
                sn = (scope.get("subnet") or "").strip()
                if sn:
                    net = sn.split("/")[0]
                    lan_subnets.append(net.rsplit(".", 1)[0] + ".%")
        except Exception:
            pass
        if not lan_subnets:
            lan_subnets = ["172.24.%", "10.%", "192.168.%", "127.%"]
        lan_where = " OR ".join(["client_ip LIKE ?" for _ in lan_subnets])
        lan_params = lan_subnets[:]
        top_clients = [dict(r) for r in conn.execute(
            f"SELECT client_ip, COUNT(*) as cnt FROM dns_queries WHERE ts>? AND ({lan_where}) GROUP BY client_ip ORDER BY cnt DESC LIMIT 10",
            (int(time.time()) - period_seconds,) + tuple(lan_params),
        ).fetchall()]
        from .dns_policy import resolve_client_name
        for c in top_clients:
            c["hostname"] = resolve_client_name(c["client_ip"], "")
        top_blocked = [dict(r) for r in conn.execute(
            "SELECT domain, COUNT(*) as cnt FROM dns_queries WHERE action='block' AND ts>? GROUP BY domain ORDER BY cnt DESC LIMIT 10",
            (int(time.time()) - period_seconds,),
        ).fetchall()]
        qtype_breakdown = [dict(r) for r in conn.execute(
            "SELECT qtype, COUNT(*) as cnt FROM dns_queries WHERE ts>? GROUP BY qtype ORDER BY cnt DESC LIMIT 10",
            (int(time.time()) - period_seconds,),
        ).fetchall()]
        upstream_breakdown = [dict(r) for r in conn.execute(
            "SELECT upstream, COUNT(*) as cnt FROM dns_queries WHERE ts>? AND upstream IS NOT NULL AND upstream != '' GROUP BY upstream ORDER BY cnt DESC LIMIT 10",
            (int(time.time()) - period_seconds,),
        ).fetchall()]
        top_blocked_clients = [dict(r) for r in conn.execute(
            f"SELECT client_ip, COUNT(*) as cnt FROM dns_queries WHERE action='block' AND ts>? AND ({lan_where}) GROUP BY client_ip ORDER BY cnt DESC LIMIT 10",
            (int(time.time()) - period_seconds,) + tuple(lan_params),
        ).fetchall()]
    finally:
        conn.close()

    return {
        "period_seconds": period_seconds,
        "enabled": settings.get("dns_enabled", False),
        "running": dm_status.get("running", False),
        "uptime": dm_status.get("uptime"),
        "listen_addr": settings.get("dns_listen_addr", "0.0.0.0"),
        "listen_port": settings.get("dns_listen_port", 53),
        "cache_size": settings.get("cache_size", 5000),
        "block_mode": settings.get("block_mode", "null_ip"),
        "local_domain": settings.get("local_domain", "lan"),
        "rules": {
            "total": total_rules,
            "block": block_rules,
            "allow": allow_rules,
            "rewrite": rewrite_rules,
        },
        "lists": {"total": total_lists, "enabled": enabled_lists},
        "clients": total_clients,
        "policies": total_policies,
        "queries": {
            "total": total_queries,
            "blocked": blocked_queries,
            "last_24h": queries_24h,
            "blocked_24h": blocked_24h,
            "last_1h": queries_1h,
            "blocked_1h": blocked_1h,
        },
        "local_records": local_records,
        "rewrites": rewrites,
        "upstreams": upstreams,
        "top_domains": top_domains,
        "top_clients": top_clients,
        "top_blocked": top_blocked,
        "top_blocked_clients": top_blocked_clients,
        "qtype_breakdown": qtype_breakdown,
        "upstream_breakdown": upstream_breakdown,
    }


def apply_config():
    settings = get_all_settings()
    rules = get_rules_for_config()
    records = get_local_records()
    rewrites = get_rewrites()
    upstreams = get_upstreams()
    clients = get_clients()
    policies = get_policies()
    static_leases = get_static_leases() if settings.get("dhcp_enabled") else []
    rules = _with_service_rules(rules, settings, clients, policies)
    ok, msg = write_all_configs(settings, rules=rules, records=records,
                                rewrites=rewrites, upstreams=upstreams,
                                clients=clients, policies=policies,
                                static_leases=static_leases)
    if not ok:
        rollback_configs()
        return False, msg
    from .dhcp import write_dhcp_config
    write_dhcp_config()
    ok2, msg2 = reload_dnsmasq()
    if not ok2:
        return False, msg2
    from .dns_service_nft import apply_service_blocks
    apply_service_blocks()
    add_event("config_applied", "info", "dns", "DNS config applied and dnsmasq reloaded")
    return True, msg2


def _with_service_rules(rules, settings, clients, policies):
    from .dns_policy import get_blocked_service_domains
    result = list(rules or [])
    service_names = set(parse_json_list(settings.get("global_blocked_services")))

    for domain in get_blocked_service_domains(json.dumps(list(service_names))):
        result.append({
            "id": 0,
            "type": "adblock_domain",
            "value": domain,
            "action": "block",
            "category": "service",
            "enabled": 1,
            "modifiers": {},
        })
    return result


def get_local_records():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM dns_local_records ORDER BY domain ASC").fetchall()]
    finally:
        conn.close()


def add_local_record(domain, rtype="A", value="", ttl=60, comment=None, enabled=1):
    now = int(time.time())
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO dns_local_records (domain, rtype, value, ttl, comment, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (domain, rtype, value, ttl, comment, enabled, now, now),
        )
        conn.commit()
        return True, cur.lastrowid
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_local_record(record_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dns_local_records WHERE id=?", (record_id,))
        conn.commit()
        return True, f"Record {record_id} deleted"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def get_rewrites():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM dns_rewrites ORDER BY domain ASC").fetchall()]
    finally:
        conn.close()


def add_rewrite(domain, target, rtype="CNAME", comment=None, enabled=1):
    now = int(time.time())
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO dns_rewrites (domain, target, rtype, comment, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (domain, target, rtype, comment, enabled, now, now),
        )
        conn.commit()
        return True, cur.lastrowid
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_rewrite(rewrite_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dns_rewrites WHERE id=?", (rewrite_id,))
        conn.commit()
        return True, f"Rewrite {rewrite_id} deleted"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


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


def get_clients():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM dns_clients ORDER BY id ASC").fetchall()]
    finally:
        conn.close()


def get_policies():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM dns_policies ORDER BY id ASC").fetchall()]
    finally:
        conn.close()


def update_settings(updates):
    for key, value in updates.items():
        if key in DEFAULT_SETTINGS:
            set_setting(key, value)
    return True, "Settings updated"
