#!/usr/bin/env python3
import os
import sqlite3
import time
import json

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "dns.db"
)
LISTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "dns-lists"
)

DB_CREATE = """
CREATE TABLE IF NOT EXISTS dns_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dns_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    value TEXT NOT NULL,
    action TEXT NOT NULL,
    category TEXT,
    comment TEXT,
    enabled INTEGER DEFAULT 1,
    source TEXT DEFAULT 'manual',
    source_list_id INTEGER,
    priority INTEGER DEFAULT 100,
    modifiers_json TEXT,
    raw_rule TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER,
    expires_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_dns_rules_action ON dns_rules(action);
CREATE INDEX IF NOT EXISTS idx_dns_rules_value ON dns_rules(value);
CREATE INDEX IF NOT EXISTS idx_dns_rules_source ON dns_rules(source);

CREATE TABLE IF NOT EXISTS dns_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT,
    format TEXT DEFAULT 'hosts',
    category TEXT,
    enabled INTEGER DEFAULT 1,
    rule_count INTEGER DEFAULT 0,
    last_updated INTEGER,
    last_error TEXT,
    etag TEXT,
    sha1 TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS dns_clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    ip TEXT,
    cidr TEXT,
    mac TEXT,
    client_id TEXT,
    tags_json TEXT,
    policy_id INTEGER,
    policy TEXT,
    enabled INTEGER DEFAULT 1,
    use_global_settings INTEGER DEFAULT 1,
    filtering_enabled INTEGER DEFAULT 1,
    safesearch_enabled INTEGER,
    safebrowsing_enabled INTEGER,
    parental_enabled INTEGER,
    blocked_services_json TEXT,
    upstreams_json TEXT,
    ignore_querylog INTEGER DEFAULT 0,
    ignore_statistics INTEGER DEFAULT 0,
    first_seen_ts INTEGER,
    last_seen_ts INTEGER,
    total_queries INTEGER DEFAULT 0,
    blocked_queries INTEGER DEFAULT 0,
    risk_score REAL DEFAULT 0,
    source TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_dns_clients_ip ON dns_clients(ip);
CREATE INDEX IF NOT EXISTS idx_dns_clients_mac ON dns_clients(mac);

CREATE TABLE IF NOT EXISTS dns_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    block_categories TEXT NOT NULL,
    allow_categories TEXT,
    custom_allow TEXT,
    custom_block TEXT,
    safe_search_json TEXT,
    blocked_services_json TEXT,
    schedule_json TEXT,
    block_doh_bypass INTEGER DEFAULT 0,
    block_external_dns INTEGER DEFAULT 0,
    default_action TEXT DEFAULT 'allow',
    blocking_mode TEXT DEFAULT 'null_ip',
    blocked_response_ttl INTEGER DEFAULT 60,
    upstreams_json TEXT,
    rate_limit_qps INTEGER,
    enabled INTEGER DEFAULT 1,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS dns_local_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    rtype TEXT DEFAULT 'A',
    value TEXT NOT NULL,
    ttl INTEGER DEFAULT 60,
    comment TEXT,
    enabled INTEGER DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_dns_local_domain ON dns_local_records(domain);

CREATE TABLE IF NOT EXISTS dns_rewrites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    target TEXT NOT NULL,
    rtype TEXT DEFAULT 'CNAME',
    comment TEXT,
    enabled INTEGER DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_dns_rewrites_domain ON dns_rewrites(domain);

CREATE TABLE IF NOT EXISTS dns_upstream (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    proto TEXT DEFAULT 'udp',
    domain TEXT,
    enabled INTEGER DEFAULT 1,
    priority INTEGER DEFAULT 100,
    comment TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS dns_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    client_ip TEXT,
    client_mac TEXT,
    client_name TEXT,
    domain TEXT,
    qtype TEXT DEFAULT 'A',
    action TEXT,
    reason TEXT,
    policy TEXT,
    rule_id INTEGER,
    list_id INTEGER,
    upstream TEXT,
    response TEXT,
    rcode TEXT,
    latency_ms REAL,
    blocked_categories TEXT,
    cname_chain TEXT
);
CREATE INDEX IF NOT EXISTS idx_dns_queries_ts ON dns_queries(ts);
CREATE INDEX IF NOT EXISTS idx_dns_queries_client ON dns_queries(client_ip);
CREATE INDEX IF NOT EXISTS idx_dns_queries_domain ON dns_queries(domain);
CREATE INDEX IF NOT EXISTS idx_dns_queries_action ON dns_queries(action);

CREATE TABLE IF NOT EXISTS dns_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT DEFAULT 'info',
    source TEXT,
    message TEXT,
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_dns_events_ts ON dns_events(ts);

CREATE TABLE IF NOT EXISTS dns_decision_cache (
    key TEXT PRIMARY KEY,
    client_ref TEXT,
    domain TEXT NOT NULL,
    qtype TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    rule_id INTEGER,
    list_id INTEGER,
    response_json TEXT,
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dns_decision_cache_expires ON dns_decision_cache(expires_at);

CREATE TABLE IF NOT EXISTS dns_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    enabled INTEGER DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS dns_client_groups (
    client_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    PRIMARY KEY (client_id, group_id)
);

CREATE TABLE IF NOT EXISTS dns_rule_groups (
    rule_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    PRIMARY KEY (rule_id, group_id)
);

CREATE TABLE IF NOT EXISTS dns_list_groups (
    list_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    PRIMARY KEY (list_id, group_id)
);

CREATE TABLE IF NOT EXISTS dns_access_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    value TEXT NOT NULL,
    comment TEXT,
    enabled INTEGER DEFAULT 1,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dns_custom_services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    domains_json TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS dhcp_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dhcp_scopes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    interface TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    protocol TEXT DEFAULT 'ipv4',
    subnet TEXT NOT NULL,
    range_start TEXT,
    range_end TEXT,
    router TEXT,
    dns_servers TEXT,
    domain TEXT DEFAULT 'lan',
    lease_time INTEGER DEFAULT 86400,
    authoritative INTEGER DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS dhcp_leases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id INTEGER,
    ip TEXT NOT NULL,
    mac TEXT,
    hostname TEXT,
    client_id TEXT,
    lease_start INTEGER,
    lease_end INTEGER,
    state TEXT DEFAULT 'active',
    source TEXT DEFAULT 'dnsmasq',
    first_seen_ts INTEGER,
    last_seen_ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_dhcp_leases_ip ON dhcp_leases(ip);
CREATE INDEX IF NOT EXISTS idx_dhcp_leases_mac ON dhcp_leases(mac);

CREATE TABLE IF NOT EXISTS dhcp_static_leases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id INTEGER,
    mac TEXT NOT NULL,
    ip TEXT NOT NULL,
    hostname TEXT,
    client_name TEXT,
    policy_id INTEGER,
    enabled INTEGER DEFAULT 1,
    comment TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS dhcp_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id INTEGER,
    option_code INTEGER NOT NULL,
    option_name TEXT,
    option_type TEXT DEFAULT 'text',
    option_value TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    comment TEXT
);

CREATE TABLE IF NOT EXISTS dhcp_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    ip TEXT,
    mac TEXT,
    hostname TEXT,
    scope_id INTEGER,
    severity TEXT DEFAULT 'low',
    message TEXT,
    raw_json TEXT
);
"""


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(LISTS_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(DB_CREATE)
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn):
    policy_cols = {r[1] for r in conn.execute("PRAGMA table_info(dns_policies)").fetchall()}
    if "updated_at" not in policy_cols:
        conn.execute("ALTER TABLE dns_policies ADD COLUMN updated_at INTEGER")
        conn.commit()


def get_setting(key, default=None):
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM dns_settings WHERE key=?", (key,)).fetchone()
        if row:
            try:
                return json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                return row["value"]
        return default
    finally:
        conn.close()


def set_setting(key, value):
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO dns_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value) if not isinstance(value, str) else value, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_settings():
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM dns_settings").fetchall()
        result = {}
        for r in rows:
            try:
                result[r["key"]] = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                result[r["key"]] = r["value"]
        return result
    finally:
        conn.close()


DEFAULT_SETTINGS = {
    "dns_enabled": False,
    "dns_listen_addr": "0.0.0.0",
    "dns_listen_port": 53,
    "cache_size": 5000,
    "block_mode": "null_ip",
    "custom_block_ipv4": "0.0.0.0",
    "custom_block_ipv6": "::",
    "blocked_response_ttl": 60,
    "query_log_enabled": True,
    "query_log_retention_days": 30,
    "query_log_ignore_clients": [],
    "upstream_mode": "load_balance",
    "upstream_timeout": 3,
    "bootstrap_dns": ["9.9.9.9", "1.1.1.1"],
    "fallback_dns": ["8.8.8.8", "1.0.0.1"],
    "ratelimit_qps": 20,
    "ratelimit_subnet_len_ipv4": 24,
    "ratelimit_subnet_len_ipv6": 56,
    "ratelimit_whitelist": [],
    "refuse_any": True,
    "redirect_external_dns": False,
    "block_doh_providers": False,
    "local_domain": "lan",
    "dhcp_enabled": False,
    "telegram_alerts_enabled": False,
    "telegram_chat_id": "",
    "decision_cache_ttl": 300,
    "decision_cache_enabled": True,
    "global_blocked_services": [],
}


def ensure_settings():
    conn = get_db()
    try:
        existing = {r["key"] for r in conn.execute("SELECT key FROM dns_settings").fetchall()}
        now = int(time.time())
        for key, val in DEFAULT_SETTINGS.items():
            if key not in existing:
                conn.execute(
                    "INSERT INTO dns_settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, json.dumps(val), now),
                )
        conn.commit()
    finally:
        conn.close()


def add_event(event_type, severity="info", source=None, message=None, data=None):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO dns_events (ts, event_type, severity, source, message, data_json) VALUES (?, ?, ?, ?, ?, ?)",
            (int(time.time()), event_type, severity, source, message, json.dumps(data) if data else None),
        )
        conn.commit()
    finally:
        conn.close()
