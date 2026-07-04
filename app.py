#!/usr/bin/env python3
import os
import sys
import json
import time
import threading
from datetime import datetime, timedelta
from collections import Counter
from flask import Flask, render_template, request, redirect, send_from_directory, make_response, url_for, jsonify

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules.parsers import parse_log, parse_auth_log, port_name, PORT_NAMES, LOG_FILE, is_excluded
from modules.nft_utils import nft_set_ips, nft_set_count, cscli_json, svc_status, svc_uptime, count_nft_drop_rules
from modules.suricata import get_suricata_alerts, get_suricata_rules, get_suricata_mode, get_suricata_ips_stats
from modules.health import get_health, get_cpu_usage, get_load_avg, get_cpu_temp, get_memory, get_disk, get_network, get_conntrack, get_uptime, get_services
from modules.risk import get_all_risks, get_risk_for_ip, risk_badge
from modules.timeline import build_timeline, get_timeline_summary
from modules.allowlist import load_allowlist, get_all_entries, add_ip, add_network, remove_ip, remove_network, toggle_emergency_bypass, get_emergency_bypass
from modules.policy import get_policy, set_policy, get_policies
from modules.rules_ui import get_rules_state, toggle_rule, get_rules_categories, get_rules_categories_with_status
from modules.export import generate_html_report, get_previous_reports, delete_report, ALL_SECTIONS
from modules.geoip import lookup as geoip_lookup, get_country_stats, get_asn_stats, get_flag, COUNTRY_FLAGS, _load_cache
from modules.auth import init_auth, authenticate, create_session, verify_session, destroy_session, change_password
from modules.wg_manager import (get_server_status, get_server_config, init_server, update_server,
    start_server, stop_server, restart_server, add_peer, remove_peer, toggle_peer,
    update_peer, update_peer_acl, get_all_peers_with_status, get_peer_config,
    get_peer_qr_base64, get_bandwidth_summary, get_connection_events, get_events,
    format_bytes as wg_format_bytes, _get_dmz_ip as _get_lan_ip)
from modules.bandwidth import (get_bandwidth_data, get_protocol_breakdown,
    get_bandwidth_summary_all, get_live_protocol_breakdown, get_sample_count,
    format_bits_per_sec, format_bytes as bw_format_bytes, format_rate,
    INTERFACES)
from modules.multiwan import (ensure_schema as mw_ensure_schema, get_status as mw_get_status,
    get_wan_interfaces as mw_get_interfaces, get_wan_interface as mw_get_interface,
    add_wan_interface as mw_add, delete_wan_interface as mw_delete,
    update_wan_interface as mw_update, get_all_settings as mw_get_settings,
    set_setting as mw_set_setting, log_event as mw_log_event,
    get_events as mw_get_events, run_health_checks as mw_check_now,
    apply_routing as mw_apply_routing, start_health_monitor as mw_start_daemon,
    get_db as mw_get_db)
from modules.firewall import (get_nat_rules, get_dnat_rules, get_masquerade_rules,
    get_filter_rules, get_filter_chain, get_firewall_overview, add_dnat_rule,
    remove_dnat_rule, update_dnat_rule, add_masquerade_rule, remove_masquerade_rule,
    update_masquerade_rule, add_input_rule, add_forward_rule, remove_filter_rule,
    format_bytes as fw_format_bytes)
from modules.qos import (get_qos_state, apply_profile, update_profile,
    toggle_qos, add_manual_rule, remove_manual_rule, toggle_manual_rule,
    reset_profile, get_qos_stats, run_speedtest, get_current_qdisc,
    ALGORITHMS, CAKE_DIFFSERV, CAKE_FLOWMODE, PRIORITY_CLASSES, PRIORITY_LEVELS,
    add_priority_class, remove_priority_class, get_all_priority_classes,
    get_manual_rules_stats)
from modules.network import (get_network_state, add_route, delete_route,
    set_interface, set_ip_address, set_mtu)
from modules.ifaces import (get_all as get_ifaces, set_role, create_vlan, delete_vlan,
    get_vlans, get_iface_select_options, ROLES, persist_vlans)
from modules.dns import (get_status, toggle_dns, apply_config, get_rules as get_dns_rules,
    add_rule as add_dns_rule, delete_rule as delete_dns_rule, toggle_rule as toggle_dns_rule,
    import_rules_from_text as dns_import_rules, get_local_records, add_local_record,
    delete_local_record, get_rewrites, add_rewrite, delete_rewrite,
    get_upstreams as get_dns_upstreams, add_upstream, delete_upstream,
    get_lists as get_dns_lists, add_list as add_dns_list, delete_list as delete_dns_list,
    toggle_list as toggle_dns_list, download_list, update_all_lists,
    update_settings as update_dns_settings, init_dns, get_clients as get_dns_clients)
from modules.dns_logs import parse_log_file, batch_insert_queries, cleanup_old_queries, get_query_stats, get_recent_queries
from modules.dns_rules import count_rules as count_dns_rules
from modules.dns_policy import (ensure_default_policies, get_all_policies, get_policy as get_dns_policy, add_policy, update_policy, delete_policy,
    add_client, update_client, delete_client, discover_clients_from_arp,
    get_safe_search_rewrites, get_blocked_service_domains, BLOCKED_SERVICES, SAFE_SEARCH_DOMAINS,
    get_all_groups, add_group, update_group, delete_group,
    add_client_to_group, remove_client_from_group,
    add_rule_to_group, remove_rule_from_group,
    add_list_to_group, remove_list_from_group, get_group_members,
    get_all_access_rules, add_access_rule, update_access_rule, delete_access_rule,
    get_custom_services, add_custom_service, update_custom_service, delete_custom_service,
    add_service_domain, remove_service_domain, get_all_services)
from modules.dns_cache import get_decision, set_decision, invalidate_cache, cleanup_cache, cache_stats
from modules.dns_nft import get_dns_redirect_status
from modules.dhcp import (get_dhcp_status, add_scope, update_scope, delete_scope, toggle_scope,
    add_static_lease, delete_static_lease, update_static_lease, get_leases, make_lease_static,
    detect_active_dhcp, validate_scope_config, generate_dhcp_config, write_dhcp_config,
    sync_leases_to_db, parse_lease_file,
    get_dhcp_options, add_dhcp_option, update_dhcp_option, delete_dhcp_option,
    setup_dhcp_nft_rules, remove_dhcp_nft_rules)
from modules.dhcp_leases import get_static_leases, cleanup_expired_leases
from modules.ip_blocklists import (init_db as ipbl_init_db, get_lists as ipbl_get_lists, get_list_by_id as ipbl_get_list,
    add_list as ipbl_add_list, update_list as ipbl_update_list, delete_list as ipbl_delete_list,
    toggle_list as ipbl_toggle_list, download_list as ipbl_download_list, download_all_lists as ipbl_download_all,
    get_custom_entries as ipbl_get_custom, add_custom_entry as ipbl_add_custom, remove_custom_entry as ipbl_remove_custom,
    get_nft_set_stats as ipbl_get_stats, ensure_nft_sets as ipbl_ensure_sets, ensure_nft_rules as ipbl_ensure_rules,
    flush_list_from_nft as ipbl_flush_list, get_settings as ipbl_get_settings, update_setting as ipbl_update_setting,
    restore_ipbl)
app = Flask(__name__)
_first_run_pass = init_auth()
mw_ensure_schema()
ipbl_init_db()
try:
    import threading
    def _restore_ipbl_bg():
        try:
            restore_ipbl()
        except Exception:
            pass
    threading.Thread(target=_restore_ipbl_bg, daemon=True).start()
except Exception:
    pass

try:
    _load_cache()
except Exception:
    pass

from modules.wg_manager import _load_state, _write_server_config, _sync_all_peers, _apply_all_firewall_rules, get_server_status, _open_wg_port, _save_state as _wg_save_state
try:
    _wg_state = _load_state()
    if _wg_state.get("server"):
        is_running = get_server_status().get("running", False)
        _wg_state["server"]["running"] = is_running
        _wg_save_state(_wg_state)
        if is_running:
            _write_server_config(_wg_state["server"])
            _sync_all_peers()
            _apply_all_firewall_rules()
            _open_wg_port(_wg_state["server"].get("listen_port", 51820))
except Exception:
    pass
app.template_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
app.static_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.jinja_env.auto_reload = True

@app.template_filter('fmt_int')
def fmt_int_filter(value):
    try:
        return f"{int(value):,}"
    except (ValueError, TypeError):
        return str(value)

PORT_NAMES_TPL = {k: v for k, v in PORT_NAMES.items()}

_cache = {}
_CACHE_TTL = {
    "dashboard": 60,
    "health": 30,
    "risk": 120,
    "geoip": 120,
    "allowlist": 30,
    "policy": 60,
    "rules": 60,
}


def _cached(key, func, ttl=None):
    if ttl is None:
        ttl = _CACHE_TTL.get(key, 30)
    now = time.time()
    if key in _cache:
        entry = _cache[key]
        if now - entry["ts"] < ttl:
            return entry["data"]
    data = func()
    _cache[key] = {"ts": now, "data": data}
    return data


def get_theme():
    return request.cookies.get("theme", "light")


def json_response(data):
    resp = make_response(json.dumps(data, default=str))
    resp.mimetype = "application/json"
    return resp


def is_ajax():
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _parse_duration(dur):
    if not dur or dur.startswith("-"):
        return 0
    try:
        total = 0
        for part in dur.split():
            if part.endswith("h"):
                total += int(part[:-1]) * 3600
            elif part.endswith("m"):
                total += int(part[:-1]) * 60
            elif part.endswith("s"):
                total += int(part[:-1])
            else:
                total += int(part)
        return total
    except (ValueError, IndexError):
        return 0


def _parse_json_list(val):
    if isinstance(val, list):
        return val
    if not val:
        return []
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return [s.strip() for s in str(val).replace('[', '').replace(']', '').replace('"', '').replace("'", "").split(',') if s.strip()]


app.jinja_env.filters['parse_json_list'] = lambda v: _parse_json_list(v)
app.jinja_env.filters['strftime'] = lambda ts: datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if isinstance(ts, (int, float)) and ts > 0 else ""


def ajax_ok(message, **extra):
    d = {"ok": True, "message": message}
    d.update(extra)
    return json_response(d)


def ajax_error(message, **extra):
    d = {"ok": False, "error": message}
    d.update(extra)
    return json_response(d)


def ajax_or_redirect(message, section="", error=False):
    if is_ajax():
        if error:
            return ajax_error(message)
        return ajax_ok(message)
    suffix = f"#{section}" if section else ""
    prefix = "/dns?message="
    if error:
        return redirect(f"{prefix}{message}&error=1{suffix}")
    return redirect(f"{prefix}{message}{suffix}")


AUTH_ENABLED = os.environ.get("NFT_DASHBOARD_AUTH", "1") != "0"


@app.after_request
def add_cache_headers(response):
    if request.path.startswith("/static/"):
        response.cache_control.max_age = 3600
        response.cache_control.public = True
    if request.method == "POST" and request.headers.get("X-Requested-With") == "XMLHttpRequest":
        if response.status_code in (301, 302, 303, 307, 308):
            loc = response.headers.get("Location", "")
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(loc)
            params = parse_qs(parsed.query)
            msg = params.get("message", [""])[0]
            is_err = "error=1" in loc
            if is_err:
                return json_response({"ok": False, "error": msg or "Error"})
            return json_response({"ok": True, "message": msg or "OK"})
    return response


def _get_token():
    token = request.cookies.get("session_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    return token


@app.before_request
def check_auth():
    global _first_run_pass
    if request.path.startswith(("/static/", "/login")):
        return None
    if request.path == "/logout":
        return None
    if not AUTH_ENABLED:
        return None
    token = _get_token()
    user = verify_session(token) if token else None
    if not user:
        return redirect("/login")
    return None


@app.route("/login", methods=["GET", "POST"])
def login_page():
    global _first_run_pass
    if request.method == "GET":
        if not AUTH_ENABLED:
            return redirect("/")
        info = None
        if _first_run_pass:
            info = f"First-time password: {_first_run_pass}"
            _first_run_pass = None
        return render_template("login.html", error=None, info=info, theme=get_theme())
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if authenticate(username, password):
        token = create_session(username)
        resp = redirect("/timeline")
        resp.set_cookie("session_token", token, max_age=86400, httponly=True)
        return resp
    return render_template("login.html", error="Invalid username or password", info=None, theme=get_theme())


@app.route("/logout")
def logout():
    token = _get_token()
    if token:
        destroy_session(token)
    resp = redirect("/login")
    resp.set_cookie("session_token", "", max_age=0)
    return resp


@app.route("/change-password", methods=["GET", "POST"])
def change_password_page():
    if not AUTH_ENABLED:
        return redirect("/")
    token = _get_token()
    user = verify_session(token) if token else None
    if not user:
        return redirect("/login")
    error = None
    success = None
    if request.method == "POST":
        old_pass = request.form.get("old_password", "")
        new_pass = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not old_pass or not new_pass:
            error = "All fields are required"
        elif new_pass != confirm:
            error = "New passwords do not match"
        elif len(new_pass) < 8:
            error = "New password must be at least 8 characters"
        else:
            ok, msg = change_password(user, old_pass, new_pass)
            if ok:
                success = msg
            else:
                error = msg
    return render_template("change_password.html", tab="change_password", now=datetime.now(),
                           theme=get_theme(), theme_icon="&#x1f319;", error=error, success=success)


def _load_full_dashboard():
    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    entries = parse_log(lines)

    src_counter = Counter(e["src"] for e in entries if e["src"])
    rule_counter = Counter(e["rule"] for e in entries)
    dpt_counter = Counter(e["dpt"] for e in entries if e["dpt"])

    bl_list = nft_set_ips("filter", "blacklist_ipv4")
    bl_ips = {e["ip"] for e in bl_list}

    ssh_fail, ssh_success = parse_auth_log()

    cs_decisions = []
    cs_ips = set()
    cs_ip_latest = {}
    try:
        for alert in cscli_json(["decisions", "list"]):
            if not alert:
                continue
            for d in (alert.get("decisions") or []):
                dur = d.get("duration", "")
                if dur and dur.startswith("-"):
                    continue
                ip = d.get("value", "")
                if not ip:
                    continue
                scenario = d.get("scenario", alert.get("scenario", ""))
                events = alert.get("events_count", alert.get("capacity", ""))
                origin = d.get("origin", alert.get("kind", ""))
                dur_sec = _parse_duration(dur)
                if ip not in cs_ip_latest or dur_sec > cs_ip_latest[ip]["_dur_sec"]:
                    cs_ip_latest[ip] = {
                        "value": ip,
                        "scenario": scenario,
                        "type": d.get("type", ""),
                        "duration": dur,
                        "events": events,
                        "origin": origin,
                        "_dur_sec": dur_sec,
                    }
                cs_ips.add(ip)
        cs_nft_ips = nft_set_ips("filter", "blacklist_ipv4")
        if not cs_nft_ips:
            cs_nft_ips = nft_set_ips("filter", "crowdsec-blacklists")
        cs_nft_count = len(cs_nft_ips)
        for e in cs_nft_ips:
            ip = e.get("ip", "")
            if ip and ip not in cs_ip_latest:
                cs_ip_latest[ip] = {
                    "value": ip,
                    "scenario": "CAPI/community",
                    "type": "ban",
                    "duration": e.get("timeout", ""),
                    "events": "",
                    "origin": "CAPI",
                    "_dur_sec": 0,
                }
                cs_ips.add(ip)
    except Exception:
        pass
    for ip, info in sorted(cs_ip_latest.items()):
        cs_decisions.append({k: v for k, v in info.items() if not k.startswith("_")})

    cs_list = []
    cs_nft_ips = set()
    for d in cs_decisions:
        ip = d.get("value", "")
        if ip and ip not in cs_nft_ips:
            cs_list.append({"ip": ip, "scenario": d.get("scenario", ""), "timeout": d.get("duration", "")})
            cs_nft_ips.add(ip)

    cs_nft_list = nft_set_ips("filter", "blacklist_ipv4")
    if not cs_nft_list:
        cs_nft_list = nft_set_ips("filter", "crowdsec-blacklists")
    cs_nft_total = len(cs_nft_list)
    cs_nft_show = sorted(cs_nft_list, key=lambda x: x.get("_expires_s", 0), reverse=True)[:200]
    cs_nft_show = [{"ip": e["ip"], "timeout": e.get("timeout", ""), "expires": e.get("expires", "")} for e in cs_nft_show]

    suricata_alerts = get_suricata_alerts(30)
    suricata_rules = get_suricata_rules()
    suricata_ips_mode = get_suricata_mode()
    suricata_stats = dict(get_suricata_ips_stats() or {})
    suricata_stats["latest_shown"] = len(suricata_alerts)

    svc_names = ["nftables", "crowdsec", "crowdsec-firewall-bouncer", "suricata", "ssh"]
    services = []
    for s in svc_names:
        st = svc_status(s)
        up = svc_uptime(s) if st == "active" else ""
        services.append({"name": s, "status": st, "uptime": up})

    return {
        "entries": entries, "src_counter": src_counter, "rule_counter": rule_counter,
        "dpt_counter": dpt_counter, "bl_list": bl_list, "cs_list": cs_list,
        "bl_ips": bl_ips, "cs_ips": cs_ips, "ssh_fail": ssh_fail,
        "cs_decisions": cs_decisions, "suricata_alerts": suricata_alerts,
        "suricata_rules": suricata_rules, "suricata_ips_mode": suricata_ips_mode,
        "suricata_stats": suricata_stats,
        "services": services, "cs_nft_show": cs_nft_show, "cs_nft_total": cs_nft_total,
    }


def _filter_suricata_alerts_by_period(alerts, period, limit):
    if period == "all":
        return alerts[:limit]
    now = datetime.now()
    delta = {"5m": timedelta(minutes=5), "1h": timedelta(hours=1), "6h": timedelta(hours=6),
             "12h": timedelta(hours=12), "24h": timedelta(hours=24), "7d": timedelta(days=7)}.get(period, timedelta(days=9999))
    cutoff = now - delta
    filtered = []
    for a in alerts:
        ts_str = a.get("time_iso") or a.get("time", "")
        try:
            if "T" in ts_str:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                ts = datetime.strptime(ts_str, "%m-%d %H:%M").replace(year=now.year)
            if ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)
            if ts >= cutoff:
                filtered.append(a)
        except (ValueError, TypeError):
            pass
    return filtered[:limit]


@app.route("/")
def index():
    query = request.args.get("q", "").strip()
    period = request.args.get("period", "1h")
    tab = request.args.get("tab", "drops")

    data = _cached("dashboard", _load_full_dashboard)

    if period != "all":
        now = datetime.now()
        delta = {"5m": timedelta(minutes=5), "1h": timedelta(hours=1), "6h": timedelta(hours=6),
                 "24h": timedelta(hours=24), "7d": timedelta(days=7)}.get(period, timedelta(days=9999))
        cutoff = now - delta
        filtered = []
        for e in data["entries"]:
            ts_iso = e.get("time_iso", "")
            try:
                ts = datetime.fromisoformat(ts_iso)
                if ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)
                if ts >= cutoff:
                    filtered.append(e)
            except (ValueError, TypeError):
                pass
        entries = filtered
    else:
        entries = data["entries"]

    if query:
        q_lower = query.lower()
        entries = [e for e in entries if q_lower in e["src"].lower() or q_lower in e["rule"].lower() or q_lower in e["iface"].lower() or q_lower in e["dst"].lower() or q_lower in e["dpt"]]

    src_counter = Counter(e["src"] for e in entries if e["src"])
    rule_counter = Counter(e["rule"] for e in entries)
    dpt_counter = Counter(e["dpt"] for e in entries if e["dpt"])

    return render_template("dashboard.html",
        now=datetime.now(),
        total_drops=len(entries),
        unique_src=len(src_counter),
        bl_count=len(data["bl_ips"]),
        cs_count=len(data["cs_ips"]),
        ssh_attempts=sum(data["ssh_fail"].values()),
        unique_rules=len(rule_counter),
        nft_drop_rules=count_nft_drop_rules(),
        top_ips=src_counter.most_common(20),
        top_rules=rule_counter.most_common(20),
        top_dpts=dpt_counter.most_common(20),
        last_entries=entries[-50:],
        port_names=PORT_NAMES_TPL,
        bl_list=sorted(data["bl_list"], key=lambda x: x["ip"]),
        cs_list=sorted(data["cs_list"], key=lambda x: x["ip"]),
        cs_nft_list=data.get("cs_nft_show", []),
        cs_nft_total=data.get("cs_nft_total", 0),
        bl_ips=data["bl_ips"],
        cs_ips=data["cs_ips"],
        ssh_top=data["ssh_fail"].most_common(30),
        cs_decisions=data["cs_decisions"],
        suricata_alerts=_filter_suricata_alerts_by_period(data["suricata_alerts"], period, 30),
        suricata_rules=data["suricata_rules"],
        suricata_ips=data["suricata_ips_mode"],
        suricata_stats=data["suricata_stats"],
        cs_alerts_total=len(data["cs_decisions"]),
        services=data["services"],
        query=query,
        period=period,
        tab=tab,
        theme=get_theme(),
        theme_icon="&#x1f319;",
    )


@app.route("/health")
def health():
    health_data = _cached("health", get_health)
    return render_template("health.html", tab="health", now=datetime.now(), theme=get_theme(), theme_icon="&#x1f319;", health=health_data)


@app.route("/api/health")
def api_health():
    health_data = _cached("health", get_health)
    def _serialize(obj):
        if isinstance(obj, dict):
            return {k: _serialize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_serialize(i) for i in obj]
        return obj
    return json.dumps(_serialize(health_data), default=str)


@app.route("/risk")
def risk_page():
    query = request.args.get("q", "").strip()
    level_filter = request.args.get("level", "")
    refresh = request.args.get("refresh", "")
    risks = get_all_risks(force=bool(refresh))
    if query:
        risks = [r for r in risks if query in r["ip"] or any(query in t for t in r["tags"])]
    if level_filter:
        risks = [r for r in risks if r["level"] == level_filter]
    critical_count = sum(1 for r in risks if r["level"] == "critical")
    high_count = sum(1 for r in risks if r["level"] == "high")
    medium_count = sum(1 for r in risks if r["level"] == "medium")
    low_count = sum(1 for r in risks if r["level"] == "low")
    return render_template("risk.html", tab="risk", now=datetime.now(), theme=get_theme(), theme_icon="&#x26a0;",
        risks=risks, query=query, level_filter=level_filter,
        critical_count=critical_count, high_count=high_count, medium_count=medium_count, low_count=low_count)


@app.route("/api/risk")
def api_risk():
    risks = get_all_risks()
    return json.dumps(risks, default=str)


@app.route("/api/risk/<ip>")
def api_risk_ip(ip):
    risk = get_risk_for_ip(ip)
    if risk:
        return json.dumps(risk, default=str)
    return json.dumps({"error": "not found"}), 404


@app.route("/timeline")
def timeline_page():
    zoom = request.args.get("zoom", "24h")
    tl = build_timeline(zoom)
    summary = get_timeline_summary(zoom)
    buckets = tl.get("buckets", [])
    max_points = 300
    if len(buckets) > max_points:
        step = len(buckets) // max_points
        buckets = buckets[::step]
    labels = [b["ts"][-8:] if len(b["ts"]) > 16 else b["ts"] for b in buckets]
    drops = [b.get("drops", 0) for b in buckets]
    ssh_fail = [b.get("ssh_fail", 0) for b in buckets]
    suricata = [b.get("suricata", 0) for b in buckets]
    cs_bans = [b.get("cs_bans", 0) for b in buckets]
    chart_data = json.dumps({
        "labels": labels,
        "drops": drops,
        "ssh_fail": ssh_fail,
        "suricata": suricata,
        "cs_bans": cs_bans,
    })
    return render_template("timeline.html", tab="timeline", now=datetime.now(), theme=get_theme(), theme_icon="&#x1f4c8;",
        zoom=zoom, summary=summary, chart_data=chart_data)


@app.route("/api/timeline")
def api_timeline():
    zoom = request.args.get("zoom", "24h")
    tl = build_timeline(zoom)
    tl["summary"] = get_timeline_summary(zoom)
    return json_response(tl)


@app.route("/api/port-stats")
def api_port_stats():
    from modules.port_stats import get_port_stats
    period = request.args.get("period", "24h")
    return json_response(get_port_stats(period))


@app.route("/allowlist")
def allowlist_page():
    data = load_allowlist()
    entries = get_all_entries()
    emergency = get_emergency_bypass()
    emergency_since = data.get("emergency_bypass_since")
    message = request.args.get("message", "")
    message_error = request.args.get("error", "0") == "1"
    return render_template("allowlist.html", tab="allowlist", now=datetime.now(), theme=get_theme(), theme_icon="&#x2705;",
        entries=entries, emergency=emergency, emergency_since=emergency_since,
        message=message, message_error=message_error)


@app.route("/allowlist/add", methods=["POST"])
def allowlist_add():
    value = request.form.get("value", "").strip()
    comment = request.form.get("comment", "").strip()
    entry_type = request.form.get("type", "ip")
    if "/" in value:
        entry_type = "network"
    if entry_type == "network":
        ok, msg = add_network(value, comment)
    else:
        ok, msg = add_ip(value, comment)
    return redirect(f"/allowlist?message={msg}&error={0 if ok else 1}")


@app.route("/allowlist/remove", methods=["POST"])
def allowlist_remove():
    value = request.form.get("value", "").strip()
    entry_type = request.form.get("type", "ip")
    if entry_type == "network":
        ok, msg = remove_network(value)
    else:
        ok, msg = remove_ip(value)
    return redirect(f"/allowlist?message={msg}&error={0 if ok else 1}")


@app.route("/allowlist/emergency", methods=["POST"])
def allowlist_emergency():
    action = request.form.get("action", "")
    enable = action == "enable"
    ok, msg = toggle_emergency_bypass(enable)
    return redirect(f"/allowlist?message={msg}&error={0 if ok else 1}")


@app.route("/api/allowlist")
def api_allowlist():
    return json.dumps(get_all_entries(), default=str)


@app.route("/policy")
def policy_page():
    policy = get_policy()
    policies = get_policies()
    current_mode = (policy or {}).get("mode", "balanced")
    message = request.args.get("message", "")
    message_error = request.args.get("error", "0") == "1"
    return render_template("policy.html", tab="policy", now=datetime.now(), theme=get_theme(), theme_icon="&#x1f6e1;",
        policy=policy, policies=policies, current_mode=current_mode,
        message=message, message_error=message_error)


@app.route("/policy/set", methods=["POST"])
def policy_set():
    mode = request.form.get("mode", "balanced")
    ok, msg = set_policy(mode)
    if ok:
        return redirect(f"/policy?message={msg}")
    return redirect(f"/policy?message={msg}&error=1")


@app.route("/api/policy")
def api_policy():
    return json.dumps(get_policy(), default=str)


@app.route("/rules")
def rules_page():
    categories = get_rules_categories_with_status()
    rules = []
    for cat_rules in categories.values():
        for r in cat_rules:
            r["mismatched"] = bool(r.get("real_status")) != bool(r.get("enabled"))
            rules.append(r)
    ipbl_lists = ipbl_get_lists()
    ipbl_custom = ipbl_get_custom()
    ipbl_stats = ipbl_get_stats()
    active_tab = request.args.get("tab", "rules")
    return render_template("rules.html", tab="rules", now=datetime.now(), theme=get_theme(), theme_icon="&#x2699;",
        categories=categories, rules=rules, active_tab=active_tab,
        ipbl_lists=ipbl_lists, ipbl_custom=ipbl_custom, ipbl_stats=ipbl_stats)


@app.route("/rules/toggle", methods=["POST"])
def rules_toggle():
    rule_id = request.form.get("rule_id", "")
    enabled = request.form.get("enabled", "1") == "1"
    toggle_rule(rule_id, enabled)
    if is_ajax():
        return ajax_ok(f"Rule {rule_id} {'enabled' if enabled else 'disabled'}")
    return redirect("/rules")


@app.route("/rules/update", methods=["POST"])
def rules_update():
    from modules.rules_ui import update_rule_params
    rule_id = request.form.get("rule_id", "")
    params = {k: v for k, v in request.form.items() if k != "rule_id"}
    ok, msg = update_rule_params(rule_id, params)
    if is_ajax():
        return ajax_ok(msg) if ok else ajax_error(msg)
    return redirect(f"/rules?message={msg}")


@app.route("/api/rules")
def api_rules():
    return json.dumps(get_rules_state(), default=str)


@app.route("/export")
def export_page():
    reports = get_previous_reports()
    return render_template("export.html", tab="export", now=datetime.now(), theme=get_theme(), theme_icon="&#x1f4e5;",
        reports=reports, sections=ALL_SECTIONS)


@app.route("/export/generate", methods=["POST"])
def export_generate():
    period = request.form.get("period", "all")
    selected = request.form.getlist("sections")
    if not selected:
        selected = [s[0] for s in ALL_SECTIONS]
    filename = generate_html_report(period=period, sections=selected)
    return redirect(f"/export/download/{filename}")


@app.route("/export/delete/<filename>", methods=["POST"])
def export_delete(filename):
    delete_report(filename)
    return redirect(url_for("export_page"))


@app.route("/export/download/<filename>")
def export_download(filename):
    return send_from_directory("/opt/nft-dashboard/data/reports", filename, as_attachment=True)


@app.route("/geoip")
def geoip_page():
    def _geo_data():
        seen = set()
        all_ips = []
        bl_entries = nft_set_ips("filter", "blacklist_ipv4")
        for e in bl_entries:
            if e["ip"] not in seen:
                seen.add(e["ip"])
                all_ips.append(e["ip"])
        try:
            with open(LOG_FILE, "r") as f:
                lines = f.readlines()
            for e in parse_log(lines):
                src = e.get("src", "")
                if src and src not in seen:
                    seen.add(src)
                    all_ips.append(src)
        except Exception:
            pass
        country_stats = get_country_stats(all_ips)
        asn_stats = get_asn_stats(all_ips)
        return all_ips, country_stats, asn_stats

    all_ips, country_stats, asn_stats = _cached("geoip", _geo_data)
    query = request.args.get("q", "").strip()
    page = max(1, int(request.args.get("page", "1")))
    cpage = max(1, int(request.args.get("cpage", "1")))
    apage = max(1, int(request.args.get("apage", "1")))
    per_page = 50
    if query:
        filtered = [ip for ip in all_ips if query in ip]
    else:
        filtered = all_ips
    total_ips = len(filtered)
    total_pages = max(1, (total_ips + per_page - 1) // per_page)
    page = min(page, total_pages)
    page_ips = filtered[(page - 1) * per_page : page * per_page]
    ip_details = [geoip_lookup(ip) for ip in page_ips]
    sorted_countries = sorted(country_stats.items(), key=lambda x: x[1]['count'], reverse=True)
    sorted_asns = sorted(asn_stats.items(), key=lambda x: x[1]['count'], reverse=True)
    cpage_total = max(1, (len(sorted_countries) + per_page - 1) // per_page)
    cpage = min(cpage, cpage_total)
    page_countries = sorted_countries[(cpage - 1) * per_page : cpage * per_page]
    apage_total = max(1, (len(sorted_asns) + per_page - 1) // per_page)
    apage = min(apage, apage_total)
    page_asns = sorted_asns[(apage - 1) * per_page : apage * per_page]
    all_flags = {code: get_flag(code) for code in country_stats.keys()}
    all_flags["LOCAL"] = get_flag("LOCAL")
    all_flags["XX"] = get_flag("XX")
    return render_template("geoip.html", tab="geoip", now=datetime.now(), theme=get_theme(), theme_icon="&#x1f30d;",
        ips=filtered, country_stats=page_countries, asn_stats=page_asns, ip_details=ip_details, flags=all_flags, query=query,
        page=page, total_pages=total_pages, per_page=per_page,
        cpage=cpage, cpage_total=cpage_total, total_countries=len(sorted_countries),
        apage=apage, apage_total=apage_total, total_asns=len(sorted_asns))


@app.route("/api/geoip")
def api_geoip():
    source = request.args.get("source", "all")
    limit = int(request.args.get("limit", "200"))
    ips = []
    if source in ("all", "blacklist"):
        ips.extend(e["ip"] for e in nft_set_ips("filter", "blacklist_ipv4"))
    if source in ("all", "crowdsec"):
        cs_ips = nft_set_ips("filter", "blacklist_ipv4")
        if not cs_ips:
            cs_ips = nft_set_ips("filter", "crowdsec-blacklists")
        ips.extend(e["ip"] for e in cs_ips)
    if source in ("all", "drops"):
        from modules.parsers import parse_log, LOG_FILE
        try:
            with open(LOG_FILE, "r") as f:
                drop_lines = f.readlines()[:limit]
            for e in parse_log(drop_lines):
                ip = e.get("src")
                if ip and ip not in ips:
                    ips.append(ip)
        except Exception:
            pass
    ips = list(dict.fromkeys(ips))[:limit]
    results = [geoip_lookup(ip) for ip in ips]
    return json.dumps({"ips": results, "countries": get_country_stats(ips), "asns": get_asn_stats(ips)}, default=str)


@app.route("/vpn")
def vpn_page():
    server_status = get_server_status()
    server_config = get_server_config()
    peers = get_all_peers_with_status()
    online_count = sum(1 for p in peers if p.get("connected"))
    bw = get_bandwidth_summary()
    events_raw = get_connection_events()
    events = []
    for ev in events_raw:
        ts = ev.get("ts", 0)
        try:
            ts_fmt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts_fmt = str(ts)
        events.append({**ev, "ts_formatted": ts_fmt})
    return render_template("vpn.html", tab="vpn", now=datetime.now(), theme=get_theme(), theme_icon="&#x1f310;",
        server_status=server_status, server_config=server_config, peers=peers, online_count=online_count,
        bw=bw, events=events, format_bytes=wg_format_bytes, lan_ip=_get_lan_ip())


@app.route("/vpn/init", methods=["POST"])
def vpn_init():
    address = request.form.get("address", "10.0.0.1/24")
    listen_port = int(request.form.get("listen_port", 51820))
    dns = request.form.get("dns", "1.1.1.1, 8.8.8.8")
    mtu = int(request.form.get("mtu", 1420))
    ok, msg = init_server(address=address, listen_port=listen_port, dns=dns, mtu=mtu)
    if ok:
        start_ok, start_msg = start_server()
        return redirect(f"/vpn?message={msg} {' - '+start_msg if start_ok else ' - '+start_msg}")
    return redirect(f"/vpn?message={msg}&error=1")


@app.route("/vpn/start", methods=["POST"])
def vpn_start():
    ok, msg = start_server()
    if ok:
        return redirect(f"/vpn?message={msg}")
    return redirect(f"/vpn?message={msg}&error=1")


@app.route("/vpn/stop", methods=["POST"])
def vpn_stop():
    ok, msg = stop_server()
    if ok:
        return redirect(f"/vpn?message={msg}")
    return redirect(f"/vpn?message={msg}&error=1")


@app.route("/vpn/restart", methods=["POST"])
def vpn_restart_route():
    ok, msg = restart_server()
    if ok:
        return redirect(f"/vpn?message={msg}")
    return redirect(f"/vpn?message={msg}&error=1")


@app.route("/vpn/settings", methods=["POST"])
def vpn_settings():
    address = request.form.get("address")
    listen_port = int(request.form.get("listen_port", 51820))
    dns = request.form.get("dns")
    mtu = int(request.form.get("mtu", 1420))
    ok, msg = update_server(address=address, listen_port=listen_port, dns=dns, mtu=mtu)
    if ok:
        return redirect(f"/vpn?message={msg}")
    return redirect(f"/vpn?message={msg}&error=1")


@app.route("/vpn/add-peer", methods=["POST"])
def vpn_add_peer():
    name = request.form.get("name", "").strip()
    keepalive = int(request.form.get("keepalive", 25))
    notes = request.form.get("notes", "").strip()
    internet = request.form.get("internet") == "1"
    lan = request.form.get("lan") == "1"
    dmz = request.form.get("dmz") == "1"
    custom_networks = request.form.get("custom_networks", "").strip()
    parts = []
    if internet:
        parts.append("0.0.0.0/0")
    else:
        if lan:
            parts.append("192.168.1.0/24")
            parts.append("10.0.0.0/24")
        if dmz:
            parts.append("192.168.1.204/32")
    if custom_networks:
        for cn in custom_networks.split(","):
            cn = cn.strip()
            if cn and cn not in parts:
                parts.append(cn)
    if not parts:
        parts = None
    else:
        parts = ",".join(parts)
    peer, msg = add_peer(name=name, allowed_ips=parts, persistent_keepalive=keepalive, notes=notes)
    if peer:
        return redirect(f"/vpn?message={msg}")
    return redirect(f"/vpn?message={msg}&error=1")


@app.route("/vpn/toggle/<peer_id>", methods=["POST"])
def vpn_toggle_peer(peer_id):
    ok, msg = toggle_peer(peer_id)
    if ok:
        return redirect(f"/vpn?message={msg}")
    return redirect(f"/vpn?message={msg}&error=1")


@app.route("/vpn/remove/<peer_id>", methods=["POST"])
def vpn_remove_peer(peer_id):
    ok, msg = remove_peer(peer_id)
    if ok:
        return redirect(f"/vpn?message={msg}")
    return redirect(f"/vpn?message={msg}&error=1")


@app.route("/vpn/acl", methods=["POST"])
def vpn_acl():
    peer_id = request.form.get("peer_id", "").strip()
    internet = request.form.get("internet") == "1"
    lan = request.form.get("lan") == "1"
    dmz = request.form.get("dmz") == "1"
    custom_networks = request.form.get("custom_networks", "").strip()
    ok, msg = update_peer_acl(peer_id, internet=internet, lan=lan, dmz=dmz, custom_networks=custom_networks.split(",") if custom_networks else None)
    if ok:
        return redirect(f"/vpn?message={msg}")
    return redirect(f"/vpn?message={msg}&error=1")


@app.route("/vpn/peer-config/<peer_id>")
def vpn_peer_config(peer_id):
    config = get_peer_config(peer_id)
    if not config:
        return json_response({"error": "Peer not found"})
    state = _load_wg_state()
    name = state.get("peers", {}).get(peer_id, {}).get("name", peer_id)
    return json_response({"config": config, "name": name})


@app.route("/vpn/peer-qr/<peer_id>")
def vpn_peer_qr(peer_id):
    qr = get_peer_qr_base64(peer_id)
    state = _load_wg_state()
    name = state.get("peers", {}).get(peer_id, {}).get("name", peer_id)
    return json_response({"qr_base64": qr, "name": name})


@app.route("/api/vpn/status")
def api_vpn_status():
    server_status = get_server_status()
    server_config = get_server_config()
    peers = get_all_peers_with_status()
    online_count = sum(1 for p in peers if p.get("connected"))
    events = get_events(limit=20)
    result = {
        "server_running": server_status.get("running", False),
        "server_address": server_config.get("address", "") if server_config else "",
        "server_port": server_config.get("listen_port", 0) if server_config else 0,
        "online_count": online_count,
        "total_peers": len(peers),
        "peers": [{
            "id": p["id"],
            "name": p.get("name", ""),
            "address": p.get("address", ""),
            "connected": p.get("connected", False),
            "enabled": p.get("enabled", True),
            "endpoint": p.get("endpoint"),
            "last_handshake": p.get("last_handshake"),
            "transfer_rx": p.get("transfer_rx", 0),
            "transfer_tx": p.get("transfer_tx", 0),
            "allowed_ips": p.get("allowed_ips", ""),
            "acl": p.get("acl", {}),
        } for p in peers],
        "events": events[:10],
    }
    return json_response(result)


def _load_wg_state():
    from modules.wg_manager import _load_state
    return _load_state()


@app.route("/bandwidth")
def bandwidth_page():
    channel = request.args.get("channel", "internet")
    period = request.args.get("period", "1h")
    if channel not in INTERFACES:
        channel = "internet"
    if period not in ("1h", "12h", "24h", "7d", "30d", "1y"):
        period = "1h"
    data = get_bandwidth_data(channel, period)
    proto_breakdown = get_live_protocol_breakdown(channel)
    summary = get_bandwidth_summary_all()
    sample_count = get_sample_count(period)
    return render_template("bandwidth.html", tab="bandwidth", now=datetime.now(), theme=get_theme(), theme_icon="&#x1F4CA;",
        channel=channel, period=period, data=data, proto_breakdown=proto_breakdown,
        summary=summary, sample_count=sample_count, interfaces=INTERFACES,
        format_bytes=bw_format_bytes, format_rate=format_rate, format_bits=format_bits_per_sec)


@app.route("/api/bandwidth/<channel>")
def api_bandwidth(channel):
    period = request.args.get("period", "1h")
    if channel not in INTERFACES:
        return json.dumps({"error": "invalid channel"}), 400
    data = get_bandwidth_data(channel, period)
    proto_breakdown = get_live_protocol_breakdown(channel)
    summary = get_bandwidth_summary_all()
    return json.dumps({"channel": channel, "period": period, "data": data,
        "proto_breakdown": proto_breakdown, "summary": summary}, default=str)


@app.route("/api/bandwidth-summary")
def api_bandwidth_summary():
    return json.dumps(get_bandwidth_summary_all(), default=str)


@app.route("/firewall")
def firewall_page():
    tab = request.args.get("tab", "overview")
    message = request.args.get("message", "")
    message_error = request.args.get("error", "0") == "1"
    overview = get_firewall_overview()
    dnat_rules = get_dnat_rules()
    masq_rules = get_masquerade_rules()
    input_rules = get_filter_rules()
    forward_rules = get_filter_chain("forward")
    wg_rules = get_filter_chain("wg_acl")
    nat_all = get_nat_rules()
    from modules.ifaces import get_iface_select_options
    iface_opts = get_iface_select_options()
    return render_template("firewall.html", tab="firewall", now=datetime.now(), theme=get_theme(), theme_icon="&#x1F6E1;",
        active_tab=tab, overview=overview, dnat_rules=dnat_rules, masq_rules=masq_rules,
        input_rules=input_rules, forward_rules=forward_rules, wg_rules=wg_rules,
        nat_all=nat_all, message=message, message_error=message_error,
        format_bytes=fw_format_bytes, iface_opts=iface_opts)


@app.route("/firewall/add-dnat", methods=["POST"])
def firewall_add_dnat():
    proto = request.form.get("proto", "tcp")
    dport = request.form.get("dport", "").strip()
    target_ip = request.form.get("target_ip", "").strip()
    target_port = request.form.get("target_port", "").strip()
    iface = request.form.get("iface", "eth0")
    dest_ip = request.form.get("dest_ip", "").strip() or None
    if not dport or not target_ip:
        return redirect("/firewall?tab=dnat&error=1&message=Port and target IP are required")
    try:
        int(dport)
    except ValueError:
        return redirect("/firewall?tab=dnat&error=1&message=Port must be a number")
    tp = target_port if target_port else dport
    ok, msg = add_dnat_rule(proto, dport, target_ip, tp, iface, dest_ip)
    if ok:
        return redirect(f"/firewall?tab=dnat&message={msg}")
    return redirect(f"/firewall?tab=dnat&error=1&message={msg}")


@app.route("/firewall/remove-dnat/<handle>", methods=["POST"])
def firewall_remove_dnat(handle):
    ok, msg = remove_dnat_rule(handle)
    if ok:
        return redirect(f"/firewall?tab=dnat&message={msg}")
    return redirect(f"/firewall?tab=dnat&error=1&message={msg}")


@app.route("/firewall/edit-dnat/<handle>", methods=["POST"])
def firewall_edit_dnat(handle):
    proto = request.form.get("proto", "tcp")
    dport = request.form.get("dport", "").strip()
    target_ip = request.form.get("target_ip", "").strip()
    target_port = request.form.get("target_port", "").strip()
    iface = request.form.get("iface", "eth0")
    dest_ip = request.form.get("dest_ip", "").strip() or None
    if not dport or not target_ip:
        return redirect("/firewall?tab=dnat&error=1&message=Port and target IP are required")
    tp = target_port if target_port else dport
    ok, msg = update_dnat_rule(handle, proto, dport, target_ip, tp, iface, dest_ip)
    if ok:
        return redirect(f"/firewall?tab=dnat&message={msg}")
    return redirect(f"/firewall?tab=dnat&error=1&message={msg}")


@app.route("/firewall/add-masq", methods=["POST"])
def firewall_add_masq():
    source_net = request.form.get("source_net", "").strip()
    iface = request.form.get("iface", "eth0")
    if not source_net:
        return redirect("/firewall?tab=nat&error=1&message=Source network is required")
    ok, msg = add_masquerade_rule(source_net, iface)
    if ok:
        return redirect(f"/firewall?tab=nat&message={msg}")
    return redirect(f"/firewall?tab=nat&error=1&message={msg}")


@app.route("/firewall/remove-masq/<handle>", methods=["POST"])
def firewall_remove_masq(handle):
    ok, msg = remove_masquerade_rule(handle)
    if ok:
        return redirect(f"/firewall?tab=nat&message={msg}")
    return redirect(f"/firewall?tab=nat&error=1&message={msg}")


@app.route("/firewall/edit-masq/<handle>", methods=["POST"])
def firewall_edit_masq(handle):
    source_net = request.form.get("source_net", "").strip()
    iface = request.form.get("iface", "eth0")
    if not source_net:
        return redirect("/firewall?tab=nat&error=1&message=Source network is required")
    ok, msg = update_masquerade_rule(handle, source_net, iface)
    if ok:
        return redirect(f"/firewall?tab=nat&message={msg}")
    return redirect(f"/firewall?tab=nat&error=1&message={msg}")


@app.route("/firewall/add-input", methods=["POST"])
def firewall_add_input():
    action = request.form.get("action_input", "accept")
    proto = request.form.get("proto", "tcp")
    port = request.form.get("port", "").strip()
    saddr = request.form.get("saddr", "").strip()
    if not port and proto != "icmp":
        return redirect("/firewall?tab=input&error=1&message=Port is required for TCP/UDP")
    try:
        if port:
            int(port)
    except ValueError:
        return redirect("/firewall?tab=input&error=1&message=Port must be a number")
    ok, msg = add_input_rule(action, proto, port=port, saddr=saddr)
    if ok:
        return redirect(f"/firewall?tab=input&message={msg}")
    return redirect(f"/firewall?tab=input&error=1&message={msg}")


@app.route("/firewall/add-forward", methods=["POST"])
def firewall_add_forward():
    action = request.form.get("action", "accept")
    iifname = request.form.get("iifname", "").strip()
    oifname = request.form.get("oifname", "").strip()
    proto = request.form.get("proto", "")
    saddr = request.form.get("saddr", "").strip()
    daddr = request.form.get("daddr", "").strip()
    dport = request.form.get("dport", "").strip()
    sport = request.form.get("sport", "").strip()
    comment = request.form.get("comment", "").strip()
    if not action:
        return redirect("/firewall?tab=forward&error=1&message=Action is required")
    ok, msg = add_forward_rule(action, iifname=iifname, oifname=oifname, proto=proto, saddr=saddr, daddr=daddr, dport=dport, sport=sport, comment=comment)
    if ok:
        return redirect(f"/firewall?tab=forward&message={msg}")
    return redirect(f"/firewall?tab=forward&error=1&message={msg}")


@app.route("/firewall/remove-rule/<chain>/<handle>", methods=["POST"])
def firewall_remove_rule(chain, handle):
    tab = request.form.get("tab", "input")
    ok, msg = remove_filter_rule(chain, handle)
    if ok:
        return redirect(f"/firewall?tab={tab}&message={msg}")
    return redirect(f"/firewall?tab={tab}&error=1&message={msg}")


@app.route("/api/firewall/overview")
def api_firewall_overview():
    return json.dumps(get_firewall_overview(), default=str)


@app.route("/api/firewall/dnat")
def api_firewall_dnat():
    return json.dumps(get_dnat_rules(), default=str)


@app.route("/api/firewall/nat")
def api_firewall_nat():
    return json.dumps(get_nat_rules(), default=str)


@app.route("/ip-blocklists")
def ip_blocklists_page():
    return redirect("/rules?tab=ip-blocklists")


@app.route("/api/ip-blocklists/lists")
def api_ipbl_lists():
    return jsonify(ipbl_get_lists())


@app.route("/api/ip-blocklists/lists/<int:list_id>", methods=["GET"])
def api_ipbl_list_detail(list_id):
    lst = ipbl_get_list(list_id)
    if not lst:
        return jsonify({"error": "not found"}), 404
    return jsonify(lst)


@app.route("/api/ip-blocklists/lists", methods=["POST"])
def api_ipbl_add_list():
    name = request.form.get("name", "").strip() or (request.get_json(silent=True) or {}).get("name", "").strip()
    url = request.form.get("url", "").strip() or (request.get_json(silent=True) or {}).get("url", "").strip()
    fmt = request.form.get("fmt", "netset") or (request.get_json(silent=True) or {}).get("fmt", "netset")
    category = request.form.get("category", "") or (request.get_json(silent=True) or {}).get("category", "")
    description = request.form.get("description", "") or (request.get_json(silent=True) or {}).get("description", "")
    ok, result = ipbl_add_list(name, url=url, fmt=fmt, category=category, description=description)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.content_type == "application/json":
        return jsonify({"ok": ok, "result": result})
    return redirect(f"/ip-blocklists?tab=lists&message={'List added' if ok else result}&error={'1' if not ok else '0'}")


@app.route("/api/ip-blocklists/lists/<int:list_id>", methods=["POST"])
def api_ipbl_update_list(list_id):
    data = request.get_json(silent=True) or dict(request.form)
    ok, msg = ipbl_update_list(list_id, **data)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/ip-blocklists/lists/<int:list_id>/delete", methods=["POST"])
def api_ipbl_delete_list(list_id):
    ok, msg = ipbl_delete_list(list_id)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/ip-blocklists/lists/<int:list_id>/toggle", methods=["POST"])
def api_ipbl_toggle_list(list_id):
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    ok, msg = ipbl_toggle_list(list_id, enabled=enabled)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/ip-blocklists/lists/<int:list_id>/download", methods=["POST"])
def api_ipbl_download_list(list_id):
    ok, msg = ipbl_download_list(list_id)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/ip-blocklists/download-all", methods=["POST"])
def api_ipbl_download_all():
    results = ipbl_download_all()
    return jsonify({"results": results})


@app.route("/api/ip-blocklists/lists/<int:list_id>/flush", methods=["POST"])
def api_ipbl_flush_list(list_id):
    ok, msg = ipbl_flush_list(list_id)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/ip-blocklists/custom", methods=["GET"])
def api_ipbl_custom():
    return jsonify(ipbl_get_custom())


@app.route("/api/ip-blocklists/custom", methods=["POST"])
def api_ipbl_add_custom():
    data = request.get_json(silent=True) or {}
    ip_or_cidr = data.get("ip_or_cidr", "").strip()
    comment = data.get("comment", "").strip()
    if not ip_or_cidr:
        return jsonify({"ok": False, "msg": "IP/CIDR required"}), 400
    ok, msg = ipbl_add_custom(ip_or_cidr, comment)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/ip-blocklists/custom/<path:ip_or_cidr>", methods=["DELETE"])
def api_ipbl_remove_custom(ip_or_cidr):
    ok, msg = ipbl_remove_custom(ip_or_cidr)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/ip-blocklists/stats")
def api_ipbl_stats():
    return jsonify(ipbl_get_stats())


@app.route("/api/ip-blocklists/settings", methods=["GET"])
def api_ipbl_settings():
    return jsonify(ipbl_get_settings())


@app.route("/api/ip-blocklists/settings", methods=["POST"])
def api_ipbl_update_settings():
    data = request.get_json(silent=True) or {}
    results = []
    for k, v in data.items():
        ok, msg = ipbl_update_setting(k, v)
        results.append({"key": k, "ok": ok})
    return jsonify({"results": results})


@app.route("/qos")
def qos_page():
    qos = get_qos_state()
    wan_iface = qos.get("wan_iface", "eth0")
    lan_iface = qos.get("lan_iface", "eth1")
    vpn_ifaces = qos.get("vpn_ifaces", [])
    wan_qdisc = get_current_qdisc(wan_iface)
    lan_qdisc = get_current_qdisc(lan_iface)
    wan_stats = get_qos_stats(wan_iface)
    lan_stats = get_qos_stats(lan_iface)
    from modules.network import get_conntrack_stats
    conntrack = get_conntrack_stats()
    return render_template("qos.html", tab="qos", now=datetime.now(), theme=get_theme(), theme_icon="&#x2699;",
        qos=qos, wan_iface=wan_iface, lan_iface=lan_iface, vpn_ifaces=vpn_ifaces,
        wan_qdisc=wan_qdisc, lan_qdisc=lan_qdisc,
        wan_stats=wan_stats, lan_stats=lan_stats, conntrack=conntrack)


@app.route("/qos/apply-profile", methods=["POST"])
def qos_apply_profile():
    profile_id = request.form.get("profile_id", "gaming")
    ok, msg = apply_profile(profile_id)
    if ok:
        return redirect(f"/qos?message={msg}")
    return redirect(f"/qos?message={msg}&error=1")


@app.route("/qos/update-profile", methods=["POST"])
def qos_update_profile():
    profile_id = request.form.get("profile_id", "gaming")
    updates = {}
    updates["qos_wan"] = request.form.get("qos_wan") == "1"
    updates["qos_lan"] = request.form.get("qos_lan") == "1"
    vpn_ifaces = ["wg0", "wg1", "wg2"]
    selected_vpns = []
    for v in vpn_ifaces:
        if request.form.get(f"qos_vpn_{v}") == "1":
            selected_vpns.append(v)
    updates["qos_vpns"] = selected_vpns
    for field in ["download_mbit", "upload_mbit", "algorithm", "cake_diffserv", "cake_flowmode",
                   "cake_nat", "cake_overhead", "cake_rtt", "cake_wash", "cake_ack_filter",
                   "fq_codel_target", "fq_codel_interval", "fq_codel_limit", "fq_codel_flows"]:
        val = request.form.get(field)
        if val is not None:
            if field in ("download_mbit", "upload_mbit", "cake_overhead", "fq_codel_limit", "fq_codel_flows"):
                try:
                    updates[field] = int(val)
                except ValueError:
                    pass
            elif field == "cake_nat":
                updates[field] = val == "1"
            elif field == "cake_wash":
                updates[field] = val == "1"
            elif field == "cake_ack_filter":
                updates[field] = val == "1"
            else:
                updates[field] = val
    priorities = {}
    custom_classes = {}
    all_classes = get_all_priority_classes()
    for prio_key in all_classes:
        val = request.form.get(f"prio_{prio_key}")
        if val:
            priorities[prio_key] = val
        ports_val = request.form.get(f"ports_{prio_key}", "").strip()
        nets_val = request.form.get(f"nets_{prio_key}", "").strip()
        proto_val = request.form.get(f"proto_{prio_key}", "").strip()
        if ports_val or nets_val or proto_val:
            cls_override = {}
            if ports_val:
                cls_override["ports"] = ports_val
            if nets_val:
                cls_override["networks"] = nets_val
            if proto_val:
                cls_override["protocols"] = proto_val
            custom_classes[prio_key] = cls_override
    if priorities:
        updates["priorities"] = priorities
    if custom_classes:
        updates["custom_classes"] = custom_classes
    ok, msg = update_profile(profile_id, updates)
    if ok:
        return redirect(f"/qos?message={msg}")
    return redirect(f"/qos?message={msg}&error=1")


@app.route("/qos/toggle", methods=["POST"])
def qos_toggle():
    enabled = request.form.get("enabled", "1") == "1"
    ok, msg = toggle_qos(enabled)
    if ok:
        return redirect(f"/qos?message={msg}")
    return redirect(f"/qos?message={msg}&error=1")


@app.route("/qos/add-rule", methods=["POST"])
def qos_add_rule():
    rule_type = request.form.get("type", "port")
    match_val = request.form.get("match", "").strip()
    bandwidth = request.form.get("bandwidth_kbit", "0")
    priority = request.form.get("priority", "3")
    comment = request.form.get("comment", "").strip()
    if not match_val:
        return redirect("/qos?message=Match value required&error=1")
    ok, msg = add_manual_rule(rule_type, match_val, bandwidth, priority, comment)
    if ok:
        return redirect(f"/qos?message={msg}")
    return redirect(f"/qos?message={msg}&error=1")


@app.route("/qos/remove-rule", methods=["POST"])
def qos_remove_rule():
    rule_id = request.form.get("rule_id", "")
    ok, msg = remove_manual_rule(rule_id)
    return redirect(f"/qos?message={msg}")


@app.route("/qos/toggle-rule", methods=["POST"])
def qos_toggle_rule():
    rule_id = request.form.get("rule_id", "")
    enabled = request.form.get("enabled", "1") == "1"
    ok, msg = toggle_manual_rule(rule_id, enabled)
    return redirect(f"/qos?message={msg}")


@app.route("/qos/reset-profile/<profile_id>", methods=["POST"])
def qos_reset_profile(profile_id):
    ok, msg = reset_profile(profile_id)
    if ok:
        return redirect(f"/qos?message={msg}")
    return redirect(f"/qos?message={msg}&error=1")


@app.route("/qos/add-class", methods=["POST"])
def qos_add_class():
    class_key = request.form.get("class_key", "").strip().lower().replace(" ", "_")
    label = request.form.get("label", "").strip()
    ports = request.form.get("ports", "").strip()
    networks = request.form.get("networks", "").strip()
    protocols = request.form.get("protocols", "").strip()
    icon = request.form.get("icon", "").strip()
    if not class_key or not label:
        return redirect("/qos?message=Key and label required&error=1")
    ok, msg = add_priority_class(class_key, label, ports, networks, protocols, icon)
    if ok:
        return redirect(f"/qos?message={msg}")
    return redirect(f"/qos?message={msg}&error=1")


@app.route("/qos/remove-class", methods=["POST"])
def qos_remove_class():
    class_key = request.form.get("class_key", "")
    ok, msg = remove_priority_class(class_key)
    if ok:
        return redirect(f"/qos?message={msg}")
    return redirect(f"/qos?message={msg}&error=1")


@app.route("/api/qos/state")
def api_qos_state():
    return json_response(get_qos_state())


@app.route("/api/qos/stats/<iface>")
def api_qos_stats(iface):
    return json_response(get_qos_stats(iface))


@app.route("/api/qos/cake/<iface>")
def api_qos_cake(iface):
    from modules.qos import get_cake_tin_stats
    return json_response(get_cake_tin_stats(iface))


@app.route("/api/qos/traffic")
def api_qos_traffic():
    from modules.qos import get_cake_tin_stats, _load, DSCP_MAP, PRIORITY_LEVELS, _detect_ifaces, get_all_priority_classes, get_manual_rules_stats, _get_qos_ifaces
    wan, lan, vpn_ifaces = _detect_ifaces()
    data = _load()
    profile = data.get("profiles", {}).get(data.get("active_profile", "gaming"), {})
    qos_ifaces = _get_qos_ifaces(profile, data)
    priorities = profile.get("priorities", {})
    all_classes = get_all_priority_classes()
    prios = []
    for cls_key, prio_level in priorities.items():
        cls = all_classes.get(cls_key, {})
        dscp = DSCP_MAP.get(prio_level, "cs0")
        lvl = PRIORITY_LEVELS.get(prio_level, {})
        tin = "Best Effort"
        if dscp in ("cs5", "cs6", "cs7", "ef"):
            tin = "Voice"
        elif dscp in ("cs3", "cs4", "af41", "af42", "af43"):
            tin = "Video"
        elif dscp in ("cs1", "cs2"):
            tin = "Bulk"
        prios.append({
            "key": cls_key,
            "label": cls.get("label", cls_key),
            "icon": cls.get("icon", ""),
            "ports": cls.get("ports", ""),
            "networks": cls.get("networks", ""),
            "protocols": cls.get("protocols", ""),
            "priority": prio_level,
            "priority_label": lvl.get("label", ""),
            "priority_value": lvl.get("value", 0),
            "dscp": dscp,
            "cake_tin": tin,
        })
    manual_rules = data.get("manual_rules", [])
    manual_rules_stats = get_manual_rules_stats()
    iface_data = {}
    iface_labels = {"wan": "WAN", "lan": "LAN"}
    for iface in qos_ifaces:
        if iface == wan:
            key = "wan"
        elif iface == lan:
            key = "lan"
        else:
            key = iface
        iface_data[key] = get_cake_tin_stats(iface) if iface else {}
        iface_data[key]["iface"] = iface
        iface_data[key]["label"] = iface_labels.get(key, iface.upper())
    return json_response({
        "qos_ifaces": qos_ifaces,
        "wan_iface": wan,
        "lan_iface": lan,
        "priorities": prios,
        "manual_rules": manual_rules,
        "manual_rules_stats": manual_rules_stats,
        "ifaces": iface_data,
        "enabled": data.get("enabled", True),
        "active_profile": data.get("active_profile", ""),
    })


@app.route("/api/suricata/alerts")
def api_suricata_alerts():
    period = request.args.get("period", "1h")
    n = int(request.args.get("n", "30"))
    alerts = get_suricata_alerts(n * 3)
    return json_response(_filter_suricata_alerts_by_period(alerts, period, n))


@app.route("/api/dashboard-stats")
def api_dashboard_stats():
    data = _cached("dashboard", _load_full_dashboard)
    period = request.args.get("period", "1h")
    if period != "all":
        now = datetime.now()
        delta = {"5m": timedelta(minutes=5), "1h": timedelta(hours=1), "6h": timedelta(hours=6),
                 "24h": timedelta(hours=24), "7d": timedelta(days=7)}.get(period, timedelta(days=9999))
        cutoff = now - delta
        filtered = []
        for e in data.get("entries", []):
            ts_iso = e.get("time_iso", "")
            try:
                ts = datetime.fromisoformat(ts_iso)
                if ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)
                if ts >= cutoff:
                    filtered.append(e)
            except (ValueError, TypeError):
                pass
        entries = filtered
    else:
        entries = data.get("entries", [])
    src_counter = Counter(e["src"] for e in entries if e["src"])
    return json_response({
        "total_drops": len(entries),
        "unique_src": len(src_counter),
        "bl_count": len(data.get("bl_ips", set())),
        "cs_count": len(data.get("cs_ips", set())),
        "ssh_attempts": sum(data.get("ssh_fail", Counter()).values()),
        "cs_alerts_total": len(data.get("cs_decisions", [])),
        "suricata_rules": data.get("suricata_rules", 0),
        "suricata_alerts_24h": data.get("suricata_stats", {}).get("historical_alerts_24h", 0),
        "suricata_alerts_7d": data.get("suricata_stats", {}).get("historical_alerts_7d", 0),
        "nft_drop_rules": count_nft_drop_rules(),
    })


@app.route("/api/qos/speedtest")
def api_qos_speedtest():
    return json_response(run_speedtest())


@app.route("/network")
def network_page():
    net = get_network_state()
    all_ifaces, ifaces_cfg = get_ifaces()
    vlans = get_vlans()
    iface_opts = get_iface_select_options()
    mw_status = mw_get_status()
    mw_interfaces = mw_get_interfaces()
    mw_settings = mw_get_settings()
    mw_events = mw_get_events(limit=50)
    return render_template("network.html", tab="network", now=datetime.now(), theme=get_theme(), theme_icon="&#x1f5a7;",
        net=net, all_ifaces=all_ifaces, ifaces_cfg=ifaces_cfg, vlans=vlans,
        iface_opts=iface_opts, roles=ROLES,
        mw_status=mw_status, mw_interfaces=mw_interfaces, mw_settings=mw_settings, mw_events=mw_events)


@app.route("/network/add-route", methods=["POST"])
def network_add_route():
    dst = request.form.get("dst", "").strip()
    via = request.form.get("via", "").strip()
    dev = request.form.get("dev", "").strip()
    metric = int(request.form.get("metric", "0") or "0")
    if not dst:
        return redirect("/network?message=Destination required&error=1")
    ok, msg = add_route(dst, via, dev, metric)
    if ok:
        return redirect(f"/network?message={msg}")
    return redirect(f"/network?message={msg}&error=1")


@app.route("/network/del-route", methods=["POST"])
def network_del_route():
    dst = request.form.get("dst", "").strip()
    via = request.form.get("via", "").strip()
    dev = request.form.get("dev", "").strip()
    ok, msg = delete_route(dst, via, dev)
    if ok:
        return redirect(f"/network?message={msg}")
    return redirect(f"/network?message={msg}&error=1")


@app.route("/network/set-mtu", methods=["POST"])
def network_set_mtu():
    iface = request.form.get("iface", "").strip()
    mtu = request.form.get("mtu", "1500").strip()
    if not iface:
        return redirect("/network?message=Interface required&error=1")
    ok, msg = set_mtu(iface, int(mtu))
    if ok:
        return redirect(f"/network?message={msg}")
    return redirect(f"/network?message={msg}&error=1")


@app.route("/network/toggle-iface", methods=["POST"])
def network_toggle_iface():
    iface = request.form.get("iface", "").strip()
    action = request.form.get("action", "up")
    if not iface:
        return redirect("/network?message=Interface required&error=1")
    ok, msg = set_interface(iface, action)
    if ok:
        return redirect(f"/network?message={msg}")
    return redirect(f"/network?message={msg}&error=1")


@app.route("/api/network/state")
def api_network_state():
    return json.dumps(get_network_state(), default=str)


@app.route("/network/set-role", methods=["POST"])
def network_set_role():
    iface = request.form.get("iface", "").strip()
    role = request.form.get("role", "unused").strip()
    label = request.form.get("label", "").strip() or None
    if not iface:
        return redirect("/network?message=Interface required&error=1")
    ok, msg = set_role(iface, role, label)
    if ok:
        return redirect(f"/network?message={msg}")
    return redirect(f"/network?message={msg}&error=1")


@app.route("/network/create-vlan", methods=["POST"])
def network_create_vlan():
    parent = request.form.get("parent", "").strip()
    vlan_id = request.form.get("vlan_id", "").strip()
    ip_cidr = request.form.get("ip_cidr", "").strip()
    role = request.form.get("role", "lan").strip()
    label = request.form.get("label", "").strip() or None
    if not parent or not vlan_id:
        return redirect("/network?message=Parent interface and VLAN ID required&error=1")
    ok, msg = create_vlan(parent, vlan_id, ip_cidr, role, label)
    persist_vlans()
    if ok:
        return redirect(f"/network?message={msg}")
    return redirect(f"/network?message={msg}&error=1")


@app.route("/network/delete-vlan", methods=["POST"])
def network_delete_vlan():
    vlan_name = request.form.get("vlan_name", "").strip()
    if not vlan_name:
        return redirect("/network?message=VLAN name required&error=1")
    ok, msg = delete_vlan(vlan_name)
    persist_vlans()
    if ok:
        return redirect(f"/network?message={msg}")
    return redirect(f"/network?message={msg}&error=1")


def get_hostname_rules_data():
    try:
        from modules.hostname_resolver import get_hostname_rules
        rules = get_hostname_rules()
        from modules.dns_policy import get_all_policies
        policy_map = {p["id"]: p["name"] for p in get_all_policies()}
        for r in rules:
            r["dns_policy_name"] = policy_map.get(r.get("dns_policy_id"))
        return rules
    except Exception:
        return []


def get_tracked_clients_data():
    try:
        from modules.hostname_resolver import get_tracked_clients
        return get_tracked_clients()
    except Exception:
        return []


@app.route("/dns")
def dns_page():
    try:
        from modules.dns_logs import parse_log_file, batch_insert_queries
        _entries = parse_log_file()
        if _entries:
            batch_insert_queries(_entries)
    except Exception:
        pass
    try:
        from modules.dns_policy import update_client_stats
        update_client_stats()
    except Exception:
        pass
    status = get_status()
    rule_search = request.args.get("rule_search", "").strip()
    try:
        rule_limit = int(request.args.get("rule_limit", 100))
    except (ValueError, TypeError):
        rule_limit = 100
    if rule_limit not in (50, 100, 250, 500, 1000):
        rule_limit = 100
    try:
        rule_page = int(request.args.get("rule_page", 1))
    except (ValueError, TypeError):
        rule_page = 1
    rule_page = max(1, rule_page)
    rule_total = count_dns_rules(search=rule_search or None)
    rule_pages = max(1, (rule_total + rule_limit - 1) // rule_limit)
    if rule_page > rule_pages:
        rule_page = rule_pages
    rule_offset = (rule_page - 1) * rule_limit
    rules = get_dns_rules(search=rule_search or None, limit=rule_limit, offset=rule_offset)
    lists = get_dns_lists()
    local_records = get_local_records()
    rewrites = get_rewrites()
    upstreams = get_dns_upstreams()
    clients = get_dns_clients()
    policies = get_all_policies()
    for p in policies:
        sched = {}
        try:
            sched = json.loads(p.get("schedule_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        p["schedule_parsed"] = sched
    policy_names = {p.get("id"): p.get("name") for p in policies}
    for client in clients:
        client["policy_name"] = policy_names.get(client.get("policy_id"), "Global")
    groups = get_all_groups()
    for group in groups:
        group["members"] = get_group_members(group["id"])
    access_rules = get_all_access_rules()
    from modules.dns_db import get_all_settings
    settings = get_all_settings()
    from modules.dns_policy import BLOCKED_SERVICES, SAFE_SEARCH_DOMAINS, parse_json_list
    from modules.dns_lists import HAGEZI_MAX_LISTS
    dhcp_status = get_dhcp_status()
    dhcp_scopes = dhcp_status.get("scopes", [])
    for _sc in dhcp_scopes:
        ds = _sc.get("dns_servers", "")
        if isinstance(ds, str):
            try:
                parsed = json.loads(ds)
                if isinstance(parsed, list):
                    _sc["dns_servers_display"] = ", ".join(parsed)
                else:
                    _sc["dns_servers_display"] = str(parsed)
            except (json.JSONDecodeError, TypeError):
                _sc["dns_servers_display"] = ds
        elif isinstance(ds, list):
            _sc["dns_servers_display"] = ", ".join(ds)
        else:
            _sc["dns_servers_display"] = str(ds) if ds else ""
    dhcp_static_leases = dhcp_status.get("static_leases", [])
    dhcp_bound_macs = {s["mac"].lower() for s in dhcp_static_leases}
    for client in clients:
        client_mac = (client.get("mac") or "").lower()
        client["dhcp_bound"] = client_mac in dhcp_bound_macs if client_mac else False
    dhcp_active_leases = get_leases(limit=200)
    from modules.dns_policy import resolve_client_name
    for lease in dhcp_active_leases:
        if not lease.get("hostname"):
            name = resolve_client_name(lease.get("ip", ""), lease.get("mac", ""))
            if name:
                lease["hostname"] = name
    dhcp_options_list = get_dhcp_options()
    global_blocked_services = parse_json_list(settings.get("global_blocked_services"))
    family_policy = next((p for p in policies if p.get("name") == "Family"), None)
    family_schedule = {}
    if family_policy and family_policy.get("schedule_json"):
        try:
            family_schedule = json.loads(family_policy.get("schedule_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            family_schedule = {}
    return render_template("dns.html", tab="dns", now=datetime.now(),
                           theme=get_theme(), theme_icon="&#x1f319;",
                           status=status, rules=rules, lists=lists,
                           local_records=local_records, rewrites=rewrites,
                           upstreams=upstreams, settings=settings,
                           clients=clients, policies=policies,
                           groups=groups, access_rules=access_rules,
                           blocked_services=get_all_services(),
                            custom_services=get_custom_services(),
                            custom_service_names=[cs["name"] for cs in get_custom_services()],
                           safe_search=SAFE_SEARCH_DOMAINS,
                            hagezi_presets=HAGEZI_MAX_LISTS,
                            global_blocked_services=global_blocked_services,
                            family_policy=family_policy,
                            family_schedule=family_schedule,
                             rule_search=rule_search,
                             rule_limit=rule_limit,
                             rule_page=rule_page,
                             rule_pages=rule_pages,
                             rule_total=rule_total,
                             dhcp_status=dhcp_status,
                             dhcp_scopes=dhcp_scopes,
                             dhcp_static_leases=dhcp_static_leases,
                              dhcp_active_leases=dhcp_active_leases,
                               dhcp_options_list=dhcp_options_list,
                               hostname_rules=get_hostname_rules_data(),
                               tracked_clients=get_tracked_clients_data())


@app.route("/dns/dhcp/toggle", methods=["POST"])
def dns_dhcp_toggle():
    enabled = request.form.get("enabled", "").lower() in ("1", "true", "on", "yes")
    from modules.dns_db import set_setting
    set_setting("dhcp_enabled", enabled)
    if enabled:
        ok, msg = write_dhcp_config()
        from modules.dns_config import reload_dnsmasq
        reload_dnsmasq()
        from modules.dhcp import setup_dhcp_nft_rules
        setup_dhcp_nft_rules()
        return json_response({"ok": True, "message": "DHCP enabled"})
    else:
        ok, msg = write_dhcp_config()
        from modules.dns_config import reload_dnsmasq
        reload_dnsmasq()
        from modules.dhcp import remove_dhcp_nft_rules
        remove_dhcp_nft_rules()
        return json_response({"ok": True, "message": "DHCP disabled"})


@app.route("/dns/dhcp/add-scope", methods=["POST"])
def dns_dhcp_add_scope():
    name = request.form.get("name", "Default").strip()
    interface = request.form.get("interface", "eth0").strip()
    subnet = request.form.get("subnet", "").strip()
    range_start = request.form.get("range_start", "").strip() or None
    range_end = request.form.get("range_end", "").strip() or None
    router = request.form.get("router", "").strip() or None
    dns_servers = request.form.get("dns_servers", "").strip() or None
    domain = request.form.get("domain", "lan").strip()
    lease_time = int(request.form.get("lease_time", 86400))
    ok, msg = add_scope(name=name, interface=interface, subnet=subnet,
                        range_start=range_start, range_end=range_end,
                        router=router, dns_servers=dns_servers,
                        domain=domain, lease_time=lease_time)
    if ok:
        from modules.dns_db import set_setting
        set_setting("dhcp_enabled", True)
        write_dhcp_config()
        return ajax_or_redirect(f"Scope added: {name}", "dhcp")
    return ajax_or_redirect(msg, "dhcp", error=True)


@app.route("/dns/dhcp/delete-scope/<int:scope_id>", methods=["POST"])
def dns_dhcp_delete_scope(scope_id):
    ok, msg = delete_scope(scope_id)
    if ok:
        write_dhcp_config()
        from modules.dns_config import reload_dnsmasq
        reload_dnsmasq()
    return ajax_or_redirect(msg if ok else msg, "dhcp", error=not ok)


@app.route("/dns/dhcp/update-scope/<int:scope_id>", methods=["POST"])
def dns_dhcp_update_scope(scope_id):
    fields = {}
    for f in ("name", "interface", "subnet", "range_start", "range_end", "router", "dns_servers", "domain"):
        v = request.form.get(f, "").strip()
        if v:
            fields[f] = v
        elif f in ("name", "interface", "subnet"):
            return ajax_or_redirect(f"{f} is required", "dhcp", error=True)
    lease_time = request.form.get("lease_time", "").strip()
    if lease_time:
        fields["lease_time"] = int(lease_time)
    ok, msg = update_scope(scope_id, **fields)
    if ok:
        write_dhcp_config()
        from modules.dns_config import reload_dnsmasq
        reload_dnsmasq()
    return ajax_or_redirect(msg if ok else msg, "dhcp", error=not ok)


@app.route("/dns/dhcp/toggle-scope/<int:scope_id>", methods=["POST"])
def dns_dhcp_toggle_scope(scope_id):
    ok, msg = toggle_scope(scope_id)
    write_dhcp_config()
    return ajax_or_redirect(msg, "dhcp")


@app.route("/dns/dhcp/refresh-leases", methods=["POST"])
def dns_dhcp_refresh_leases():
    ok, msg = sync_leases_to_db()
    return ajax_or_redirect(msg, "dhcp")


@app.route("/dns/dhcp/make-static/<int:lease_id>", methods=["POST"])
def dns_dhcp_make_static(lease_id):
    ok, msg = make_lease_static(lease_id)
    return ajax_or_redirect(msg, "dhcp")


@app.route("/dns/dhcp/add-static-lease", methods=["POST"])
def dns_dhcp_add_static_lease():
    mac = request.form.get("mac", "").strip()
    ip_addr = request.form.get("ip", "").strip()
    hostname = request.form.get("hostname", "").strip() or None
    comment = request.form.get("comment", "").strip() or None
    if not mac or not ip_addr:
        return ajax_or_redirect("MAC and IP required", "dhcp", error=True)
    ok, msg = add_static_lease(scope_id=None, mac=mac, ip=ip_addr,
                                hostname=hostname, comment=comment)
    if ok:
        write_dhcp_config()
        from modules.dns_config import reload_dnsmasq
        reload_dnsmasq()
        return ajax_or_redirect(f"Static lease added: {mac} → {ip_addr}", "dhcp")
    return ajax_or_redirect(msg, "dhcp", error=True)


@app.route("/dns/dhcp/delete-static-lease/<int:lease_id>", methods=["POST"])
def dns_dhcp_delete_static_lease(lease_id):
    ok, msg = delete_static_lease(lease_id)
    if ok:
        write_dhcp_config()
        from modules.dns_config import reload_dnsmasq
        reload_dnsmasq()
    return ajax_or_redirect(msg, "dhcp")


@app.route("/dns/dhcp/update-static-lease/<int:lease_id>", methods=["POST"])
def dns_dhcp_update_static_lease(lease_id):
    mac = request.form.get("mac", "").strip()
    ip = request.form.get("ip", "").strip()
    hostname = request.form.get("hostname", "").strip()
    comment = request.form.get("comment", "").strip()
    if not mac or not ip:
        return ajax_or_redirect("MAC and IP are required", "dhcp", error=True)
    ok, msg = update_static_lease(lease_id, mac=mac, ip=ip, hostname=hostname, comment=comment)
    if ok:
        write_dhcp_config()
        from modules.dns_config import reload_dnsmasq
        reload_dnsmasq()
    return ajax_or_redirect(msg if ok else msg, "dhcp", error=not ok)


@app.route("/dns/dhcp/add-option", methods=["POST"])
def dns_dhcp_add_option():
    scope_id = request.form.get("scope_id", type=int) or None
    option_code = int(request.form.get("option_code", 6))
    option_name = request.form.get("option_name", "").strip() or None
    option_type = request.form.get("option_type", "text").strip()
    option_value = request.form.get("option_value", "").strip()
    comment = request.form.get("comment", "").strip() or None
    ok, result = add_dhcp_option(scope_id=scope_id, option_code=option_code,
                                   option_name=option_name, option_type=option_type,
                                   option_value=option_value, comment=comment)
    if ok:
        write_dhcp_config()
        return ajax_or_redirect(f"DHCP option {option_code} added", "dhcp")
    return ajax_or_redirect(result, "dhcp", error=True)


@app.route("/dns/dhcp/delete-option/<int:option_id>", methods=["POST"])
def dns_dhcp_delete_option(option_id):
    ok, msg = delete_dhcp_option(option_id)
    write_dhcp_config()
    return ajax_or_redirect(msg, "dhcp")


@app.route("/dns/dhcp/detect-active", methods=["POST"])
def dns_dhcp_detect_active():
    interface = request.form.get("interface", "eth0").strip()
    ok, msg = detect_active_dhcp(interface)
    if is_ajax():
        return ajax_ok(msg)
    return ajax_or_redirect(msg, "dhcp")


@app.route("/dns/dhcp/validate-config", methods=["POST"])
def dns_dhcp_validate_config():
    interface = request.form.get("interface", "").strip()
    subnet = request.form.get("subnet", "").strip()
    range_start = request.form.get("range_start", "").strip() or None
    range_end = request.form.get("range_end", "").strip() or None
    router = request.form.get("router", "").strip() or None
    errors = validate_scope_config(interface=interface, subnet=subnet,
                                    range_start=range_start, range_end=range_end,
                                    router=router)
    if is_ajax():
        if errors:
            return json_response({"ok": False, "error": "; ".join(errors)})
        return ajax_ok("Configuration is valid")
    if errors:
        return ajax_or_redirect(f"Validation failed: {'; '.join(errors)}", "dhcp", error=True)
    return ajax_or_redirect("Configuration is valid", "dhcp")


@app.route("/dns/dhcp/reload", methods=["POST"])
def dns_dhcp_reload():
    from modules.dns_db import set_setting
    set_setting("dhcp_enabled", True)
    ok, msg = write_dhcp_config()
    if ok:
        from modules.dns_config import reload_dnsmasq
        ok2, msg2 = reload_dnsmasq()
        return ajax_or_redirect(msg2, "dhcp")
    return ajax_or_redirect(msg, "dhcp", error=True)


@app.route("/dns/dhcp/bind-client/<int:client_id>", methods=["POST"])
def dns_dhcp_bind_client(client_id):
    clients = get_dns_clients()
    client = next((c for c in clients if c["id"] == client_id), None)
    if not client:
        return ajax_or_redirect("Client not found", "clients", error=True)
    mac = (client.get("mac") or "").strip()
    ip = (client.get("ip") or "").strip()
    name = (client.get("name") or "").strip()
    if not mac:
        return ajax_or_redirect("Client has no MAC address", "clients", error=True)
    if not ip:
        return ajax_or_redirect("Client has no IP address", "clients", error=True)
    from modules.dhcp import add_static_lease, get_dhcp_status
    status = get_dhcp_status()
    scopes = status.get("scopes", [])
    scope_id = scopes[0]["id"] if scopes else None
    existing = [s for s in status.get("static_leases", []) if s["mac"].lower() == mac.lower()]
    if existing:
        return ajax_or_redirect(f"Already bound: {mac} → {existing[0]['ip']}", "clients")
    ok, result = add_static_lease(scope_id=scope_id, mac=mac, ip=ip, hostname=name)
    if ok:
        try:
            write_dhcp_config()
            from modules.dns_config import reload_dnsmasq
            reload_dnsmasq()
        except Exception:
            pass
        return ajax_or_redirect(f"DHCP bound: {mac} → {ip}", "clients")
    return ajax_or_redirect(str(result), "clients", error=True)


@app.route("/dns/dhcp/unbind-client/<int:client_id>", methods=["POST"])
def dns_dhcp_unbind_client(client_id):
    clients = get_dns_clients()
    client = next((c for c in clients if c["id"] == client_id), None)
    if not client:
        return ajax_or_redirect("Client not found", "clients", error=True)
    mac = (client.get("mac") or "").strip()
    if not mac:
        return ajax_or_redirect("Client has no MAC address", "clients", error=True)
    from modules.dhcp import delete_static_lease, get_dhcp_status
    status = get_dhcp_status()
    existing = [s for s in status.get("static_leases", []) if s["mac"].lower() == mac.lower()]
    if not existing:
        return ajax_or_redirect("Not bound in DHCP", "clients")
    ok, msg = delete_static_lease(existing[0]["id"])
    if ok:
        try:
            write_dhcp_config()
            from modules.dns_config import reload_dnsmasq
            reload_dnsmasq()
        except Exception:
            pass
        return ajax_or_redirect(f"DHCP unbound: {mac}", "clients")
    return ajax_or_redirect(msg, "clients", error=True)


@app.route("/dns-log")
def dns_log_page():
    return render_template("dns_log.html", tab="dns-log", now=datetime.now(),
                           theme=get_theme(), theme_icon="&#x1f50d;")


@app.route("/dns/add-rule", methods=["POST"])
def dns_add_rule():
    rule_type = request.form.get("type", "exact")
    value = request.form.get("value", "").strip()
    action = request.form.get("action", "block")
    category = request.form.get("category", "").strip() or None
    comment = request.form.get("comment", "").strip() or None
    priority = int(request.form.get("priority", 100))
    if not value:
        return ajax_or_redirect("Domain required", "rules", error=True)
    ok, msg = add_dns_rule(rule_type, value, action, category=category,
                           comment=comment, priority=priority)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
        return ajax_or_redirect(f"Rule added: {value}", "rules")
    return ajax_or_redirect(msg, "rules", error=True)


@app.route("/dns/delete-rule/<int:rule_id>", methods=["POST"])
def dns_delete_rule(rule_id):
    ok, msg = delete_dns_rule(rule_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "rules")


@app.route("/dns/toggle-rule/<int:rule_id>", methods=["POST"])
def dns_toggle_rule_route(rule_id):
    ok, msg = toggle_dns_rule(rule_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "rules")


@app.route("/dns/import-rules", methods=["POST"])
def dns_import_rules_route():
    text = request.form.get("rules_text", "")
    category = request.form.get("category", "").strip() or None
    if not text:
        return ajax_or_redirect("No rules provided", "rules", error=True)
    imported, skipped, errors = dns_import_rules(text, source="manual", category=category)
    threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(f"Imported {imported}, skipped {skipped}, errors {errors}", "rules")


@app.route("/dns/add-list", methods=["POST"])
def dns_add_list():
    name = request.form.get("name", "").strip()
    url = request.form.get("url", "").strip() or None
    fmt = request.form.get("format", "hosts")
    category = request.form.get("category", "").strip() or None
    if not name:
        return ajax_or_redirect("List name required", "lists", error=True)
    ok, msg = add_dns_list(name, url=url, fmt=fmt, category=category)
    if ok and url:
        download_list(msg)
        threading.Thread(target=apply_config, daemon=True).start()
        return ajax_or_redirect("List added and downloading", "lists")
    if ok:
        return ajax_or_redirect("List added (no URL)", "lists")
    return ajax_or_redirect(msg, "lists", error=True)


@app.route("/dns/delete-list/<int:list_id>", methods=["POST"])
def dns_delete_list_route(list_id):
    from modules.dns_lists import purge_list_rules
    purge_list_rules(list_id)
    ok, msg = delete_dns_list(list_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "lists")


@app.route("/dns/toggle-list/<int:list_id>", methods=["POST"])
def dns_toggle_list_route(list_id):
    ok, msg = toggle_dns_list(list_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "lists")


@app.route("/dns/update-list/<int:list_id>", methods=["POST"])
def dns_update_list(list_id):
    ok, msg = download_list(list_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "lists")


@app.route("/dns/update-all-lists", methods=["POST"])
def dns_update_all_lists():
    results = update_all_lists()
    threading.Thread(target=apply_config, daemon=True).start()
    msgs = "; ".join(f"{r['name']}: {'OK' if r['ok'] else r['msg']}" for r in results)
    return ajax_or_redirect(msgs, "lists")


@app.route("/dns/install-hagezi-max", methods=["POST"])
def dns_install_hagezi_max():
    from modules.dns_lists import ensure_hagezi_max_lists
    result = ensure_hagezi_max_lists(enabled=1)
    return ajax_or_redirect(f"HaGeZi Max installed: {result['created']} new lists", "lists")


@app.route("/dns/add-local", methods=["POST"])
def dns_add_local():
    domain = request.form.get("domain", "").strip()
    rtype = request.form.get("rtype", "A")
    value = request.form.get("value", "").strip()
    ttl = int(request.form.get("ttl", 60))
    comment = request.form.get("comment", "").strip() or None
    if not domain or not value:
        return ajax_or_redirect("Domain and value required", "local", error=True)
    ok, msg = add_local_record(domain, rtype=rtype, value=value, ttl=ttl, comment=comment)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
        return ajax_or_redirect(f"Record added: {domain}", "local")
    return ajax_or_redirect(msg, "local", error=True)


@app.route("/dns/delete-local/<int:record_id>", methods=["POST"])
def dns_delete_local_route(record_id):
    ok, msg = delete_local_record(record_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "local")


@app.route("/dns/add-rewrite", methods=["POST"])
def dns_add_rewrite():
    domain = request.form.get("domain", "").strip()
    target = request.form.get("target", "").strip()
    rtype = request.form.get("rtype", "CNAME")
    comment = request.form.get("comment", "").strip() or None
    if not domain or not target:
        return ajax_or_redirect("Domain and target required", "local", error=True)
    ok, msg = add_rewrite(domain, target, rtype=rtype, comment=comment)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
        return ajax_or_redirect(f"Rewrite added: {domain}", "local")
    return ajax_or_redirect(msg, "local", error=True)


@app.route("/dns/delete-rewrite/<int:rewrite_id>", methods=["POST"])
def dns_delete_rewrite_route(rewrite_id):
    ok, msg = delete_rewrite(rewrite_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "local")


@app.route("/dns/add-upstream", methods=["POST"])
def dns_add_upstream():
    address = request.form.get("address", "").strip()
    proto = request.form.get("proto", "udp")
    domain = request.form.get("domain", "").strip() or None
    priority = int(request.form.get("priority", 100))
    comment = request.form.get("comment", "").strip() or None
    if not address:
        return ajax_or_redirect("Address required", "upstream", error=True)
    ok, msg = add_upstream(address, proto=proto, domain=domain, priority=priority, comment=comment)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
        return ajax_or_redirect(f"Upstream added: {address}", "upstream")
    return ajax_or_redirect(msg, "upstream", error=True)


@app.route("/dns/delete-upstream/<int:upstream_id>", methods=["POST"])
def dns_delete_upstream_route(upstream_id):
    ok, msg = delete_upstream(upstream_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "upstream")


@app.route("/dns/add-client", methods=["POST"])
def dns_add_client():
    name = request.form.get("name", "").strip() or None
    ip = request.form.get("ip", "").strip() or None
    mac = request.form.get("mac", "").strip() or None
    policy_id = request.form.get("policy_id", type=int) or None
    ok, msg = add_client(name=name, ip=ip, mac=mac, policy_id=policy_id)
    if ok:
        return ajax_or_redirect(f"Client added: {msg}", "clients")
    return ajax_or_redirect(msg, "clients", error=True)


@app.route("/dns/delete-client/<int:client_id>", methods=["POST"])
def dns_delete_client(client_id):
    from modules.dhcp import delete_static_lease, get_dhcp_status
    clients = get_dns_clients()
    client = next((c for c in clients if c["id"] == client_id), None)
    if client:
        mac = (client.get("mac") or "").lower()
        if mac:
            status = get_dhcp_status()
            for sl in status.get("static_leases", []):
                if sl["mac"].lower() == mac:
                    delete_static_lease(sl["id"])
            try:
                write_dhcp_config()
                from modules.dns_config import reload_dnsmasq
                reload_dnsmasq()
            except Exception:
                pass
    ok, msg = delete_client(client_id)
    return ajax_or_redirect(msg, "clients")


@app.route("/dns/update-client/<int:client_id>", methods=["POST"])
def dns_update_client_route(client_id):
    policy_id = request.form.get("policy_id", type=int) or None
    services = [s for s in request.form.getlist("blocked_services") if s.strip()]
    services = [s.strip().lower() for s in services]
    if not services:
        services_raw = request.form.get("blocked_services", "")
        services = [s.strip().lower() for s in services_raw.split(",") if s.strip()]
    upstreams = [s.strip() for s in request.form.get("upstreams", "").split(",") if s.strip()]
    updates = {
        "name": request.form.get("name", "").strip() or None,
        "policy_id": policy_id,
        "blocked_services_json": services,
        "upstreams_json": upstreams,
    }
    ok, msg = update_client(client_id, **updates)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
        try:
            from modules.dns_service_nft import apply_service_blocks
            apply_service_blocks()
        except Exception:
            pass
    return ajax_or_redirect(msg, "clients")


@app.route("/dns/discover-clients", methods=["POST"])
def dns_discover_clients():
    count = discover_clients_from_arp()
    return ajax_or_redirect(f"Discovered {count} new clients from ARP", "clients")


@app.route("/dns/add-group", methods=["POST"])
def dns_add_group():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    if not name:
        return ajax_or_redirect("Group name is required", "groups", error=True)
    ok, result = add_group(name=name, description=description)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(f"Group created: {name}" if ok else result, "groups", error=not ok)


@app.route("/dns/toggle-group/<int:group_id>", methods=["POST"])
def dns_toggle_group(group_id):
    from modules.dns_policy import get_db as policy_db
    conn = policy_db()
    row = conn.execute("SELECT enabled FROM dns_groups WHERE id=?", (group_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify(ok=False, message="Group not found"), 404
    new_enabled = 0 if row["enabled"] else 1
    ok, msg = update_group(group_id, enabled=new_enabled)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "groups")


@app.route("/dns/update-group/<int:group_id>", methods=["POST"])
def dns_update_group(group_id):
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    enabled = 1 if request.form.get("enabled") == "1" else 0
    if not name:
        return ajax_or_redirect("Group name is required", "groups", error=True)
    ok, msg = update_group(group_id, name=name, description=description, enabled=enabled)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "groups")


@app.route("/dns/delete-group/<int:group_id>", methods=["POST"])
def dns_delete_group(group_id):
    ok, msg = delete_group(group_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "groups")


@app.route("/dns/group/<int:group_id>/add-client", methods=["POST"])
def dns_group_add_client(group_id):
    client_id = request.form.get("client_id", type=int)
    if not client_id:
        return ajax_or_redirect("Client is required", "groups", error=True)
    ok, msg = add_client_to_group(client_id, group_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "groups")


@app.route("/dns/group/<int:group_id>/remove-client/<int:client_id>", methods=["POST"])
def dns_group_remove_client(group_id, client_id):
    ok, msg = remove_client_from_group(client_id, group_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "groups")


@app.route("/dns/group/<int:group_id>/add-rule", methods=["POST"])
def dns_group_add_rule(group_id):
    rule_id = request.form.get("rule_id", type=int)
    if not rule_id:
        return ajax_or_redirect("Rule ID is required", "groups", error=True)
    ok, msg = add_rule_to_group(rule_id, group_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "groups")


@app.route("/dns/group/<int:group_id>/remove-rule/<int:rule_id>", methods=["POST"])
def dns_group_remove_rule(group_id, rule_id):
    ok, msg = remove_rule_from_group(rule_id, group_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "groups")


@app.route("/dns/group/<int:group_id>/add-list", methods=["POST"])
def dns_group_add_list(group_id):
    list_id = request.form.get("list_id", type=int)
    if not list_id:
        return ajax_or_redirect("List is required", "groups", error=True)
    ok, msg = add_list_to_group(list_id, group_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "groups")


@app.route("/dns/group/<int:group_id>/remove-list/<int:list_id>", methods=["POST"])
def dns_group_remove_list(group_id, list_id):
    ok, msg = remove_list_from_group(list_id, group_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "groups")

@app.route("/dns/add-access-rule", methods=["POST"])
def dns_add_access_rule():
    rule_type = request.form.get("type", "").strip()
    value = request.form.get("value", "").strip()
    comment = request.form.get("comment", "").strip()
    if not rule_type or not value:
        return ajax_or_redirect("Type and value are required", "access", error=True)
    ok, result = add_access_rule(rule_type=rule_type, value=value, comment=comment)
    if ok:
        return ajax_or_redirect("Access rule added", "access")
    return ajax_or_redirect(result, "access", error=True)


@app.route("/dns/delete-access-rule/<int:rule_id>", methods=["POST"])
def dns_delete_access_rule(rule_id):
    ok, msg = delete_access_rule(rule_id)
    return ajax_or_redirect(msg, "access")


@app.route("/dns/update-policy/<int:policy_id>", methods=["POST"])
def dns_update_policy_route(policy_id):
    services = request.form.getlist("blocked_services")
    services = [s.strip().lower() for s in services if s.strip()]
    upstreams = [s.strip() for s in request.form.get("upstreams", "").split(",") if s.strip()]
    schedule = {
        "enabled": request.form.get("schedule_enabled") == "1",
        "timezone": request.form.get("timezone", "Europe/Kiev").strip() or "Europe/Kiev",
        "offline_days": request.form.getlist("offline_days"),
        "offline_start": request.form.get("offline_start", "22:00"),
        "offline_end": request.form.get("offline_end", "07:00"),
    }
    updates = {
        "name": request.form.get("name", "").strip(),
        "description": request.form.get("description", "").strip(),
        "block_categories": request.form.get("block_categories", "").strip(),
        "blocked_services_json": services,
        "schedule_json": schedule,
        "upstreams_json": upstreams,
        "block_doh_bypass": 1 if request.form.get("block_doh_bypass") == "1" else 0,
        "block_external_dns": 1 if request.form.get("block_external_dns") == "1" else 0,
        "default_action": request.form.get("default_action", "allow"),
        "blocking_mode": request.form.get("blocking_mode", "null_ip"),
        "rate_limit_qps": request.form.get("rate_limit_qps", type=int),
        "enabled": 1 if request.form.get("enabled") == "1" else 0,
    }
    ok, msg = update_policy(policy_id, **updates)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
        try:
            from modules.dns_service_nft import apply_service_blocks
            apply_service_blocks()
        except Exception:
            pass
    return ajax_or_redirect(msg, "policies")


@app.route("/dns/add-policy", methods=["POST"])
def dns_add_policy_route():
    name = request.form.get("name", "").strip()
    if not name:
        return ajax_or_redirect("Policy name required", "policies", error=True)
    ok, result = add_policy(name=name, description=request.form.get("description", "").strip())
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    if is_ajax() and ok:
        return ajax_ok(f"Policy created: {name}", new_id=result)
    return ajax_or_redirect(f"Policy created: {name}" if ok else str(result), "policies", error=not ok)


@app.route("/dns/toggle-policy/<int:policy_id>", methods=["POST"])
def dns_toggle_policy(policy_id):
    from modules.dns_policy import get_db as policy_db
    conn = policy_db()
    row = conn.execute("SELECT enabled FROM dns_policies WHERE id=?", (policy_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify(ok=False, message="Policy not found"), 404
    new_enabled = 0 if row["enabled"] else 1
    ok, msg = update_policy(policy_id, enabled=new_enabled)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "policies")


@app.route("/dns/delete-policy/<int:policy_id>", methods=["POST"])
def dns_delete_policy(policy_id):
    ok, msg = delete_policy(policy_id)
    if ok:
        threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect(msg, "policies")


@app.route("/dns/global-services", methods=["POST"])
def dns_global_services_route():
    from modules.dns_db import set_setting
    services = request.form.getlist("services")
    set_setting("global_blocked_services", services)
    threading.Thread(target=apply_config, daemon=True).start()
    try:
        from modules.dns_service_nft import apply_service_blocks
        threading.Thread(target=apply_service_blocks, daemon=True).start()
    except Exception:
        pass
    return ajax_or_redirect(f"Global services updated: {len(services)} enabled", "services")


@app.route("/dns/toggle-global-service", methods=["POST"])
def dns_toggle_global_service():
    from modules.dns_db import set_setting, get_setting
    svc_name = request.form.get("service", "").strip().lower()
    enable = request.form.get("enabled", "").lower() in ("1", "true", "on")
    if not svc_name:
        return ajax_or_redirect("Service name required", "services", error=True)
    current = get_setting("global_blocked_services", [])
    if isinstance(current, str):
        current = json.loads(current) if current else []
    if enable:
        if svc_name not in current:
            current.append(svc_name)
    else:
        current = [s for s in current if s != svc_name]
    set_setting("global_blocked_services", current)
    threading.Thread(target=apply_config, daemon=True).start()
    try:
        from modules.dns_service_nft import apply_service_blocks
        threading.Thread(target=apply_service_blocks, daemon=True).start()
    except Exception:
        pass
    msg = f"{'Enabled' if enable else 'Disabled'} {svc_name}"
    if is_ajax():
        return ajax_ok(msg)
    return ajax_or_redirect(msg, "services")


@app.route("/dns/add-custom-service", methods=["POST"])
def dns_add_custom_service():
    from modules.dns_policy import add_custom_service
    name = request.form.get("name", "").strip()
    domains = [d.strip().lower() for d in request.form.get("domains", "").split(",") if d.strip()]
    if not name:
        return ajax_or_redirect("Service name required", "services", error=True)
    ok, msg = add_custom_service(name, domains)
    return ajax_or_redirect(msg, "services", error=not ok)


@app.route("/dns/update-custom-service/<int:service_id>", methods=["POST"])
def dns_update_custom_service(service_id):
    from modules.dns_policy import update_custom_service
    name = request.form.get("name", "").strip() or None
    domains = [d.strip().lower() for d in request.form.get("domains", "").split(",") if d.strip()] if "domains" in request.form else None
    enabled = 1 if request.form.get("enabled") == "1" else 0 if "enabled" in request.form else None
    ok, msg = update_custom_service(service_id, name=name, domains=domains, enabled=enabled)
    return ajax_or_redirect(msg, "services")


@app.route("/dns/delete-custom-service/<int:service_id>", methods=["POST"])
def dns_delete_custom_service(service_id):
    from modules.dns_policy import delete_custom_service
    ok, msg = delete_custom_service(service_id)
    return ajax_or_redirect(msg, "services")


@app.route("/dns/service/<int:service_id>/add-domain", methods=["POST"])
def dns_service_add_domain(service_id):
    from modules.dns_policy import add_service_domain
    domain = request.form.get("domain", "").strip()
    if not domain:
        return ajax_or_redirect("Domain required", "services", error=True)
    ok, msg = add_service_domain(service_id, domain)
    return ajax_or_redirect(msg, "services")


@app.route("/dns/service/<int:service_id>/remove-domain", methods=["POST"])
def dns_service_remove_domain(service_id):
    from modules.dns_policy import remove_service_domain
    domain = request.form.get("domain", "").strip()
    if not domain:
        return ajax_or_redirect("Domain required", "services", error=True)
    ok, msg = remove_service_domain(service_id, domain)
    return ajax_or_redirect(msg, "services")


@app.route("/dns/family-schedule", methods=["POST"])
def dns_family_schedule_route():
    policy = get_dns_policy(name="Family")
    if not policy:
        return ajax_or_redirect("Family policy not found", "policies", error=True)
    schedule = {
        "enabled": request.form.get("enabled") == "1",
        "timezone": request.form.get("timezone", "Europe/Kiev").strip() or "Europe/Kiev",
        "offline_days": request.form.getlist("offline_days"),
        "offline_start": request.form.get("offline_start", "22:00"),
        "offline_end": request.form.get("offline_end", "07:00"),
    }
    ok, msg = update_policy(policy["id"], schedule_json=schedule)
    if ok:
        try:
            from modules.dns_schedule import apply_schedules
            threading.Thread(target=apply_schedules, daemon=True).start()
        except Exception:
            pass
    if ok:
        return ajax_or_redirect("Family schedule updated", "policies")
    return ajax_or_redirect(msg, "policies", error=True)


@app.route("/dns/settings", methods=["POST"])
def dns_settings():
    from modules.dns_db import DEFAULT_SETTINGS
    updates = {}
    for key in DEFAULT_SETTINGS:
        val = request.form.get(key)
        if val is not None:
            if key in ("dns_enabled", "query_log_enabled", "refuse_any",
                       "redirect_external_dns", "block_doh_providers",
                       "decision_cache_enabled", "dhcp_enabled"):
                updates[key] = val.lower() in ("true", "1", "on", "yes")
            elif key in ("dns_listen_port", "cache_size", "blocked_response_ttl",
                         "query_log_retention_days", "ratelimit_qps",
                         "ratelimit_subnet_len_ipv4", "ratelimit_subnet_len_ipv6",
                         "upstream_timeout", "decision_cache_ttl"):
                try:
                    updates[key] = int(val)
                except (ValueError, TypeError):
                    pass
            else:
                updates[key] = val
    update_dns_settings(updates)
    threading.Thread(target=apply_config, daemon=True).start()
    return ajax_or_redirect("Settings saved and config applied", "settings")


@app.route("/api/dns/status")
def api_dns_status():
    return json_response(get_status())


@app.route("/api/dns/toggle", methods=["POST"])
def api_dns_toggle():
    enabled = request.form.get("enabled")
    if enabled is not None:
        enabled = enabled.lower() in ("true", "1", "on", "yes")
    ok, msg = toggle_dns(enabled)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/apply", methods=["POST"])
def api_dns_apply():
    threading.Thread(target=apply_config, daemon=True).start()
    return json_response({"ok": True, "message": "Config apply started in background"})


@app.route("/api/dns/rules")
def api_dns_rules():
    search = request.args.get("search", "").strip() or None
    limit = request.args.get("limit", type=int)
    offset = request.args.get("offset", type=int) or 0
    if limit:
        limit = min(max(limit, 1), 5000)
        rules = get_dns_rules(search=search, limit=limit, offset=offset)
        total = count_dns_rules(search=search)
        return json_response({"rules": rules, "total": total, "limit": limit, "offset": offset})
    return json_response(get_dns_rules(search=search))


@app.route("/api/dns/rules", methods=["POST"])
def api_dns_add_rule():
    data = request.get_json(silent=True) or request.form
    from modules.dns_rules import add_rule as add_dns_rule
    rule_type = data.get("type", "exact")
    value = data.get("value", "").strip()
    action = data.get("action", "allow")
    category = data.get("category", "")
    comment = data.get("comment", "")
    ok, result = add_dns_rule(rule_type, value, action=action, category=category, comment=comment)
    return json_response({"ok": ok, "id" if ok else "error": result})


@app.route("/api/dns/rules/<int:rule_id>", methods=["PUT"])
def api_dns_update_rule(rule_id):
    data = request.get_json(silent=True) or request.form
    from modules.dns_rules import toggle_rule as _toggle_dns_rule
    enabled = data.get("enabled")
    if enabled is not None:
        ok, msg = _toggle_dns_rule(rule_id, int(enabled))
        return json_response({"ok": ok, "message": msg})
    return json_response({"ok": False, "error": "No fields to update"})


@app.route("/api/dns/rules/<int:rule_id>", methods=["DELETE"])
def api_dns_delete_rule(rule_id):
    ok, msg = delete_dns_rule(rule_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/rules/parse", methods=["POST"])
def api_dns_parse_rule():
    data = request.get_json(silent=True) or request.form
    from modules.dns_rules import parse_rule
    rule_text = data.get("rule", "")
    parsed = parse_rule(rule_text)
    return json_response(parsed)


@app.route("/api/dns/lists", methods=["POST"])
def api_dns_add_list():
    data = request.get_json(silent=True) or request.form
    ok, result = add_dns_list(name=data.get("name", ""), url=data.get("url", ""),
                              fmt=data.get("format", data.get("fmt", "hosts")), category=data.get("category", ""))
    return json_response({"ok": ok, "id" if ok else "error": result})


@app.route("/api/dns/lists/<int:list_id>", methods=["PUT"])
def api_dns_update_list(list_id):
    data = request.get_json(silent=True) or request.form
    from modules.dns_lists import update_list
    ok, msg = update_list(list_id, **data)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/lists/<int:list_id>", methods=["DELETE"])
def api_dns_delete_list(list_id):
    ok, msg = delete_dns_list(list_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/lists/<int:list_id>/update", methods=["POST"])
def api_dns_update_single_list(list_id):
    ok, msg = download_list(list_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/lists/update-all", methods=["POST"])
def api_dns_update_all_lists():
    results = update_all_lists()
    return json_response({"ok": True, "results": results})


@app.route("/api/dns/lists/install-hagezi-max", methods=["POST"])
def api_dns_install_hagezi_max():
    from modules.dns_lists import ensure_hagezi_max_lists
    result = ensure_hagezi_max_lists(enabled=1)
    return json_response({"ok": True, **result})


@app.route("/api/dns/dashboard")
def api_dns_dashboard():
    try:
        from modules.dns_logs import parse_log_file, batch_insert_queries
        _entries = parse_log_file()
        if _entries:
            batch_insert_queries(_entries)
    except Exception:
        pass
    status = get_status()
    if not status["top_domains"] and not status["top_blocked"]:
        status = get_status(period_seconds=86400)
    return json_response(status)


@app.route("/api/dns/queries/stats")
def api_dns_query_stats():
    from modules.dns_logs import get_query_stats
    return json_response(get_query_stats())


@app.route("/api/dns/queries/export", methods=["POST"])
def api_dns_query_export():
    from modules.dns_logs import export_queries
    data = request.get_json(silent=True) or {}
    fmt = data.get("format", "json")
    result = export_queries(fmt=fmt)
    return json_response(result)


@app.route("/api/dns/queries/clear", methods=["POST"])
def api_dns_query_clear():
    from modules.dns_logs import clear_queries
    cleared = clear_queries()
    return json_response({"ok": True, "cleared": cleared})


@app.route("/api/dns/local")
def api_dns_local_records():
    return json_response(get_local_records())


@app.route("/api/dns/local", methods=["POST"])
def api_dns_add_local_record():
    data = request.get_json(silent=True) or request.form
    ok, result = add_local_record(domain=data.get("domain", data.get("name", "")), rtype=data.get("rtype", "A"),
                                   value=data.get("value", data.get("target", "")), comment=data.get("comment", ""))
    return json_response({"ok": ok, "id" if ok else "error": result})


@app.route("/api/dns/local/<int:record_id>", methods=["PUT"])
def api_dns_update_local_record(record_id):
    return json_response({"ok": False, "error": "Use HTML form"})


@app.route("/api/dns/local/<int:record_id>", methods=["DELETE"])
def api_dns_delete_local_record_api(record_id):
    ok, msg = delete_local_record(record_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/rewrites")
def api_dns_rewrites():
    return json_response(get_rewrites())


@app.route("/api/dns/rewrites", methods=["POST"])
def api_dns_add_rewrite():
    data = request.get_json(silent=True) or request.form
    ok, result = add_rewrite(domain=data.get("domain", ""), target=data.get("target", ""),
                             rtype=data.get("rtype", "CNAME"), comment=data.get("comment", ""))
    return json_response({"ok": ok, "id" if ok else "error": result})


@app.route("/api/dns/rewrites/<int:rewrite_id>", methods=["PUT"])
def api_dns_update_rewrite(rewrite_id):
    return json_response({"ok": False, "error": "Use HTML form"})


@app.route("/api/dns/rewrites/<int:rewrite_id>", methods=["DELETE"])
def api_dns_delete_rewrite_api(rewrite_id):
    ok, msg = delete_rewrite(rewrite_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/upstream")
def api_dns_upstream():
    return json_response(get_dns_upstreams())


@app.route("/api/dns/upstream", methods=["POST"])
def api_dns_add_upstream():
    data = request.get_json(silent=True) or request.form
    ok, result = add_upstream(address=data.get("address", ""), proto=data.get("proto", "udp"),
                              domain=data.get("domain", ""))
    return json_response({"ok": ok, "id" if ok else "error": result})


@app.route("/api/dns/upstream/<int:upstream_id>", methods=["PUT"])
def api_dns_update_upstream(upstream_id):
    return json_response({"ok": False, "error": "Use HTML form"})


@app.route("/api/dns/upstream/<int:upstream_id>", methods=["DELETE"])
def api_dns_delete_upstream_api(upstream_id):
    ok, msg = delete_upstream(upstream_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/upstream/test", methods=["POST"])
def api_dns_test_upstream():
    data = request.get_json(silent=True) or request.form
    from modules.dns_upstream import test_upstream
    address = data.get("address", "")
    proto = data.get("proto", "udp")
    result = test_upstream(address, proto)
    return json_response(result)


@app.route("/api/dns/settings")
def api_dns_settings():
    from modules.dns_db import get_all_settings
    return json_response(get_all_settings())


@app.route("/api/dns/settings", methods=["PUT"])
def api_dns_update_settings():
    data = request.get_json(silent=True) or request.form
    from modules.dns_db import set_setting
    for key, value in data.items():
        set_setting(key, value)
    return json_response({"ok": True, "message": "Settings updated"})


@app.route("/api/dns/reload", methods=["POST"])
def api_dns_reload():
    ok, msg = apply_config()
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/toggle-redirect", methods=["POST"])
def api_dns_toggle_redirect():
    from modules.dns_nft import toggle_dns_redirect
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled", True)
    ok, msg = toggle_dns_redirect(enabled)
    return json_response({"ok": ok, "message": msg})

@app.route("/api/dns/lists")
def api_dns_lists():
    return json_response(get_dns_lists())

@app.route("/api/dns/queries")
def api_dns_queries():
    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))
    action = request.args.get("action")
    client = request.args.get("client")
    domain = request.args.get("domain")
    qtype = request.args.get("qtype")
    queries = get_recent_queries(limit=limit, offset=offset, action=action,
                                 client=client, domain=domain, qtype=qtype)
    return json_response(queries)

@app.route("/api/dns/stats")
def api_dns_stats():
    hours = int(request.args.get("hours", 24))
    return json_response(get_query_stats(hours))

@app.route("/api/dns/top-stats")
def api_dns_top_stats():
    try:
        from modules.dns_logs import parse_log_file, batch_insert_queries
        _entries = parse_log_file()
        if _entries:
            batch_insert_queries(_entries)
    except Exception:
        pass
    period_map = {"1m": 60, "5m": 300, "1h": 3600, "24h": 86400}
    period = request.args.get("period", "1h")
    period_seconds = period_map.get(period, 3600)
    status = get_status(period_seconds=period_seconds)
    fallback = False
    if not status["top_domains"] and not status["top_blocked"] and period_seconds < 86400:
        status = get_status(period_seconds=86400)
        fallback = True
    return json_response({
        "period": "24h" if fallback else period,
        "period_seconds": 86400 if fallback else period_seconds,
        "queries": status["queries"],
        "top_domains": status["top_domains"],
        "top_blocked": status["top_blocked"],
        "top_clients": status["top_clients"],
        "top_blocked_clients": status["top_blocked_clients"],
        "qtype_breakdown": status["qtype_breakdown"],
        "upstream_breakdown": status["upstream_breakdown"],
        "fallback": fallback,
    })

@app.route("/api/dns/import-log", methods=["POST"])
def api_dns_import_log():
    entries = parse_log_file()
    if entries:
        inserted = batch_insert_queries(entries)
        return json_response({"ok": True, "imported": inserted, "total": len(entries)})
    return json_response({"ok": True, "imported": 0, "total": 0})

@app.route("/api/dns/cleanup-log", methods=["POST"])
def api_dns_cleanup_log():
    days = int(request.args.get("days", 30))
    deleted = cleanup_old_queries(days)
    return json_response({"ok": True, "deleted": deleted})


@app.route("/api/dns/policies")
def api_dns_policies():
    return json_response(get_all_policies())


@app.route("/api/dns/policies", methods=["POST"])
def api_dns_add_policy():
    data = request.get_json(silent=True) or request.form
    name = data.get("name", "").strip()
    if not name:
        return json_response({"ok": False, "error": "Name required"})
    ok, msg = add_policy(name, description=data.get("description", ""),
                         block_categories=data.get("block_categories", "ads,trackers,malware,phishing"),
                         allow_categories=data.get("allow_categories", ""),
                         blocking_mode=data.get("blocking_mode", "null_ip"),
                         block_doh_bypass=int(data.get("block_doh_bypass", 0)),
                         block_external_dns=int(data.get("block_external_dns", 0)))
    return json_response({"ok": ok, "id" if ok else "error": msg})


@app.route("/api/dns/policies/<int:policy_id>", methods=["PUT"])
def api_dns_update_policy(policy_id):
    data = request.get_json(silent=True) or request.form
    ok, msg = update_policy(policy_id, **data)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/policies/<int:policy_id>", methods=["DELETE"])
def api_dns_delete_policy(policy_id):
    ok, msg = delete_policy(policy_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/clients")
def api_dns_clients():
    from modules.dns import get_clients
    return json_response(get_clients())


@app.route("/api/dns/clients", methods=["POST"])
def api_dns_add_client():
    data = request.get_json(silent=True) or request.form
    ok, msg = add_client(name=data.get("name"), ip=data.get("ip"), mac=data.get("mac"),
                         cidr=data.get("cidr"), policy_id=data.get("policy_id", type=int) if data.get("policy_id") else None,
                         tags=data.get("tags"), notes=data.get("notes"))
    return json_response({"ok": ok, "id" if ok else "error": msg})


@app.route("/api/dns/clients/<int:client_id>", methods=["PUT"])
def api_dns_update_client(client_id):
    data = request.get_json(silent=True) or request.form
    ok, msg = update_client(client_id, **data)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/clients/<int:client_id>", methods=["DELETE"])
def api_dns_delete_client(client_id):
    ok, msg = delete_client(client_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/clients/discover", methods=["POST"])
def api_dns_discover_clients():
    count = discover_clients_from_arp()
    return json_response({"ok": True, "discovered": count})


@app.route("/api/dns/cache/stats")
def api_dns_cache_stats():
    return json_response(cache_stats())


@app.route("/api/dns/cache/invalidate", methods=["POST"])
def api_dns_cache_invalidate():
    invalidate_cache()
    return json_response({"ok": True, "message": "Cache invalidated"})


@app.route("/api/dns/cache/cleanup", methods=["POST"])
def api_dns_cache_cleanup():
    deleted = cleanup_cache()
    return json_response({"ok": True, "deleted": deleted})


@app.route("/api/dns/schedules/apply", methods=["POST"])
def api_dns_apply_schedules():
    from modules.dns_schedule import apply_schedules
    ok, msg, blocks = apply_schedules()
    return json_response({"ok": ok, "message": msg, "blocks": blocks})


@app.route("/api/dns/nft/status")
def api_dns_nft_status():
    return json_response(get_dns_redirect_status())


@app.route("/api/dns/blocked-services")
def api_dns_blocked_services():
    return json_response(get_all_services())


@app.route("/api/dns/blocked-services/global", methods=["PUT", "POST"])
def api_dns_global_blocked_services():
    from modules.dns_db import set_setting
    data = request.get_json(silent=True) or request.form
    services = data.get("services", [])
    if isinstance(services, str):
        services = [s.strip() for s in services.split(",") if s.strip()]
    services = [s for s in services if s in get_all_services()]
    set_setting("global_blocked_services", services)
    ok, msg = apply_config()
    return json_response({"ok": ok, "message": msg, "services": services})


@app.route("/api/dns/safe-search-engines")
def api_dns_safe_search():
    return json_response(SAFE_SEARCH_DOMAINS)


@app.route("/api/dns/check-host", methods=["POST"])
def api_dns_check_host():
    data = request.get_json(silent=True) or request.form
    domain = data.get("domain", "").strip()
    if not domain:
        return json_response({"ok": False, "error": "Domain required"})
    from modules.dns_rules import check_host as _check_host
    matches = _check_host(domain)
    return json_response({"ok": True, "domain": domain, "matches": matches, "count": len(matches)})


@app.route("/api/dns/flush-cache", methods=["POST"])
def api_dns_flush_cache():
    from modules.dns_config import reload_dnsmasq
    ok, msg = reload_dnsmasq()
    invalidate_cache()
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/emergency-bypass", methods=["POST"])
def api_dns_emergency_bypass():
    from modules.dns_config import stop_dnsmasq
    ok, msg = stop_dnsmasq()
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/groups")
def api_dns_groups():
    return json_response(get_all_groups())


@app.route("/api/dns/groups", methods=["POST"])
def api_dns_add_group():
    data = request.get_json(silent=True) or request.form
    ok, result = add_group(name=data.get("name", ""), description=data.get("description", ""))
    return json_response({"ok": ok, "id" if ok else "error": result})


@app.route("/api/dns/groups/<int:group_id>", methods=["PUT"])
def api_dns_update_group(group_id):
    data = request.get_json(silent=True) or request.form
    ok, msg = update_group(group_id, **data)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/groups/<int:group_id>", methods=["DELETE"])
def api_dns_delete_group(group_id):
    ok, msg = delete_group(group_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/groups/<int:group_id>/members")
def api_dns_group_members(group_id):
    return json_response(get_group_members(group_id))


@app.route("/api/dns/groups/<int:group_id>/clients", methods=["POST"])
def api_dns_group_add_client(group_id):
    data = request.get_json(silent=True) or request.form
    try:
        client_id = int(data.get("client_id", 0))
    except (ValueError, TypeError):
        client_id = 0
    ok, msg = add_client_to_group(client_id, group_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/groups/<int:group_id>/clients/<int:client_id>", methods=["DELETE"])
def api_dns_group_remove_client(group_id, client_id):
    ok, msg = remove_client_from_group(client_id, group_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/groups/<int:group_id>/rules", methods=["POST"])
def api_dns_group_add_rule(group_id):
    data = request.get_json(silent=True) or request.form
    rule_id = data.get("rule_id", type=int)
    ok, msg = add_rule_to_group(rule_id, group_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/groups/<int:group_id>/rules/<int:rule_id>", methods=["DELETE"])
def api_dns_group_remove_rule(group_id, rule_id):
    ok, msg = remove_rule_from_group(rule_id, group_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/groups/<int:group_id>/lists", methods=["POST"])
def api_dns_group_add_list(group_id):
    data = request.get_json(silent=True) or request.form
    list_id = data.get("list_id", type=int)
    ok, msg = add_list_to_group(list_id, group_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/groups/<int:group_id>/lists/<int:list_id>", methods=["DELETE"])
def api_dns_group_remove_list(group_id, list_id):
    ok, msg = remove_list_from_group(list_id, group_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/access-rules")
def api_dns_access_rules():
    return json_response(get_all_access_rules())


@app.route("/api/dns/access-rules", methods=["POST"])
def api_dns_add_access_rule():
    data = request.get_json(silent=True) or request.form
    ok, result = add_access_rule(rule_type=data.get("type", ""), value=data.get("value", ""), comment=data.get("comment", ""))
    return json_response({"ok": ok, "id" if ok else "error": result})


@app.route("/api/dns/access-rules/<int:rule_id>", methods=["PUT"])
def api_dns_update_access_rule(rule_id):
    data = request.get_json(silent=True) or request.form
    ok, msg = update_access_rule(rule_id, **data)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dns/access-rules/<int:rule_id>", methods=["DELETE"])
def api_dns_delete_access_rule(rule_id):
    ok, msg = delete_access_rule(rule_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/dhcp")
def dhcp_page():
    status = get_dhcp_status()
    scopes = status.get("scopes", [])
    static_leases = status.get("static_leases", [])
    active_leases = get_leases(limit=200)
    dhcp_options = get_dhcp_options()
    errors = []
    return render_template("dhcp.html", tab="dhcp", now=datetime.now(),
                           theme=get_theme(), theme_icon="&#x1f319;",
                           status=status, scopes=scopes, static_leases=static_leases,
                           active_leases=active_leases, dhcp_options=dhcp_options, errors=errors)


@app.route("/api/dhcp/status")
def api_dhcp_status():
    return json_response(get_dhcp_status())


@app.route("/api/dhcp/scopes")
def api_dhcp_scopes():
    return json_response(get_dhcp_status().get("scopes", []))


@app.route("/api/dhcp/scopes", methods=["POST"])
def api_dhcp_add_scope():
    data = request.get_json(silent=True) or request.form
    ok, msg = add_scope(
        name=data.get("name", "Default"),
        interface=data.get("interface", "eth0"),
        subnet=data.get("subnet", ""),
        range_start=data.get("range_start"),
        range_end=data.get("range_end"),
        router=data.get("router"),
        dns_servers=data.get("dns_servers"),
        domain=data.get("domain", "lan"),
        lease_time=int(data.get("lease_time", 86400)),
        authoritative=int(data.get("authoritative", 1)),
    )
    return json_response({"ok": ok, "id" if ok else "error": msg})


@app.route("/api/dhcp/scopes/<int:scope_id>", methods=["PUT"])
def api_dhcp_update_scope(scope_id):
    data = request.get_json(silent=True) or request.form
    ok, msg = update_scope(scope_id, **data)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dhcp/scopes/<int:scope_id>", methods=["DELETE"])
def api_dhcp_delete_scope(scope_id):
    ok, msg = delete_scope(scope_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dhcp/scopes/<int:scope_id>/enable", methods=["POST"])
def api_dhcp_enable_scope(scope_id):
    ok, msg = toggle_scope(scope_id, enabled=1)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dhcp/scopes/<int:scope_id>/disable", methods=["POST"])
def api_dhcp_disable_scope(scope_id):
    ok, msg = toggle_scope(scope_id, enabled=0)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dhcp/leases")
def api_dhcp_leases():
    scope_id = request.args.get("scope_id", type=int)
    state = request.args.get("state")
    limit = int(request.args.get("limit", 100))
    return json_response(get_leases(scope_id=scope_id, state=state, limit=limit))


@app.route("/api/dhcp/leases/refresh", methods=["POST"])
def api_dhcp_refresh_leases():
    ok, msg = sync_leases_to_db()
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dhcp/leases/<int:lease_id>/make-static", methods=["POST"])
def api_dhcp_make_static(lease_id):
    ok, msg = make_lease_static(lease_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dhcp/static-leases")
def api_dhcp_static_leases():
    scope_id = request.args.get("scope_id", type=int)
    return json_response(get_static_leases(scope_id=scope_id))


@app.route("/api/dhcp/static-leases", methods=["POST"])
def api_dhcp_add_static_lease():
    data = request.get_json(silent=True) or request.form
    ok, msg = add_static_lease(
        scope_id=data.get("scope_id", type=int),
        mac=data.get("mac", ""),
        ip=data.get("ip", ""),
        hostname=data.get("hostname"),
        client_name=data.get("client_name"),
        policy_id=data.get("policy_id", type=int) if data.get("policy_id") else None,
        comment=data.get("comment"),
    )
    return json_response({"ok": ok, "id" if ok else "error": msg})


@app.route("/api/dhcp/static-leases/<int:lease_id>", methods=["DELETE"])
def api_dhcp_delete_static_lease(lease_id):
    ok, msg = delete_static_lease(lease_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dhcp/detect-active", methods=["POST"])
def api_dhcp_detect_active():
    interface = request.args.get("interface", "eth0")
    ok, msg = detect_active_dhcp(interface)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dhcp/validate-config", methods=["POST"])
def api_dhcp_validate_config():
    data = request.get_json(silent=True) or request.form
    errors = validate_scope_config(
        interface=data.get("interface"),
        subnet=data.get("subnet"),
        range_start=data.get("range_start"),
        range_end=data.get("range_end"),
        router=data.get("router"),
    )
    return json_response({"ok": len(errors) == 0, "errors": errors})


@app.route("/api/dhcp/reload", methods=["POST"])
def api_dhcp_reload():
    ok, msg = write_dhcp_config()
    if ok:
        from modules.dns_config import reload_dnsmasq
        ok2, msg2 = reload_dnsmasq()
        return json_response({"ok": ok2, "message": msg2})
    return json_response({"ok": False, "error": msg})


@app.route("/api/dhcp/options")
def api_dhcp_options():
    scope_id = request.args.get("scope_id", type=int)
    return json_response(get_dhcp_options(scope_id=scope_id))


@app.route("/api/dhcp/options", methods=["POST"])
def api_dhcp_add_option():
    data = request.get_json(silent=True) or request.form
    ok, result = add_dhcp_option(
        scope_id=data.get("scope_id", type=int) if data.get("scope_id") else None,
        option_code=int(data.get("option_code", 6)),
        option_name=data.get("option_name"),
        option_type=data.get("option_type", "text"),
        option_value=data.get("option_value", ""),
        comment=data.get("comment"),
    )
    return json_response({"ok": ok, "id" if ok else "error": result})


@app.route("/api/dhcp/options/<int:option_id>", methods=["PUT"])
def api_dhcp_update_option(option_id):
    data = request.get_json(silent=True) or request.form
    ok, msg = update_dhcp_option(option_id, **data)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/dhcp/options/<int:option_id>", methods=["DELETE"])
def api_dhcp_delete_option(option_id):
    ok, msg = delete_dhcp_option(option_id)
    return json_response({"ok": ok, "message": msg})


@app.route("/dhcp/add-scope", methods=["POST"])
def dhcp_add_scope():
    name = request.form.get("name", "Default").strip()
    interface = request.form.get("interface", "eth0").strip()
    subnet = request.form.get("subnet", "").strip()
    range_start = request.form.get("range_start", "").strip() or None
    range_end = request.form.get("range_end", "").strip() or None
    router = request.form.get("router", "").strip() or None
    dns_servers = request.form.get("dns_servers", "").strip() or None
    domain = request.form.get("domain", "lan").strip()
    lease_time = int(request.form.get("lease_time", 86400))
    ok, msg = add_scope(name=name, interface=interface, subnet=subnet,
                        range_start=range_start, range_end=range_end,
                        router=router, dns_servers=dns_servers,
                        domain=domain, lease_time=lease_time)
    if ok:
        from modules.dns_db import set_setting
        set_setting("dhcp_enabled", True)
        write_dhcp_config()
        return redirect(f"/dhcp?message=Scope added")
    return redirect(f"/dhcp?message={msg}&error=1")


@app.route("/dhcp/delete-scope/<int:scope_id>", methods=["POST"])
def dhcp_delete_scope(scope_id):
    ok, msg = delete_scope(scope_id)
    return redirect(f"/dhcp?message={msg}")


@app.route("/dhcp/toggle-scope/<int:scope_id>", methods=["POST"])
def dhcp_toggle_scope(scope_id):
    ok, msg = toggle_scope(scope_id)
    write_dhcp_config()
    return redirect(f"/dhcp?message={msg}")


@app.route("/dhcp/refresh-leases", methods=["POST"])
def dhcp_refresh_leases():
    ok, msg = sync_leases_to_db()
    return redirect(f"/dhcp?message={msg}")


@app.route("/dhcp/make-static/<int:lease_id>", methods=["POST"])
def dhcp_make_static(lease_id):
    ok, msg = make_lease_static(lease_id)
    return redirect(f"/dhcp?message={msg}")


@app.route("/dhcp/add-static-lease", methods=["POST"])
def dhcp_add_static_lease():
    mac = request.form.get("mac", "").strip()
    ip = request.form.get("ip", "").strip()
    hostname = request.form.get("hostname", "").strip() or None
    comment = request.form.get("comment", "").strip() or None
    if not mac or not ip:
        return redirect("/dhcp?message=MAC and IP required&error=1")
    ok, msg = add_static_lease(scope_id=None, mac=mac, ip=ip, hostname=hostname, comment=comment)
    if ok:
        write_dhcp_config()
        return redirect(f"/dhcp?message=Static lease added: {mac} → {ip}")
    return redirect(f"/dhcp?message={msg}&error=1")


@app.route("/dhcp/delete-static-lease/<int:lease_id>", methods=["POST"])
def dhcp_delete_static_lease(lease_id):
    from modules.dhcp import delete_static_lease
    ok, msg = delete_static_lease(lease_id)
    write_dhcp_config()
    return redirect(f"/dhcp?message={msg}")


@app.route("/dhcp/detect-active", methods=["POST"])
def dhcp_detect_active():
    interface = request.form.get("interface", "eth0").strip()
    ok, msg = detect_active_dhcp(interface)
    return redirect(f"/dhcp?message={msg}")


@app.route("/dhcp/validate-config", methods=["POST"])
def dhcp_validate_config():
    interface = request.form.get("interface", "").strip()
    subnet = request.form.get("subnet", "").strip()
    range_start = request.form.get("range_start", "").strip() or None
    range_end = request.form.get("range_end", "").strip() or None
    router = request.form.get("router", "").strip() or None
    errors = validate_scope_config(interface=interface, subnet=subnet,
                                   range_start=range_start, range_end=range_end, router=router)
    if errors:
        return redirect(f"/dhcp?message=Validation failed: {'; '.join(errors)}&error=1")
    return redirect("/dhcp?message=Configuration is valid")


@app.route("/dhcp/reload", methods=["POST"])
def dhcp_reload():
    from modules.dns_db import set_setting
    set_setting("dhcp_enabled", True)
    ok, msg = write_dhcp_config()
    if ok:
        from modules.dns_config import reload_dnsmasq
        ok2, msg2 = reload_dnsmasq()
        return redirect(f"/dhcp?message={msg2}")
    return redirect(f"/dhcp?message={msg}&error=1")


@app.route("/dhcp/add-option", methods=["POST"])
def dhcp_add_option():
    scope_id = request.form.get("scope_id", type=int) or None
    option_code = int(request.form.get("option_code", 6))
    option_name = request.form.get("option_name", "").strip() or None
    option_type = request.form.get("option_type", "text").strip()
    option_value = request.form.get("option_value", "").strip()
    comment = request.form.get("comment", "").strip() or None
    ok, result = add_dhcp_option(scope_id=scope_id, option_code=option_code,
                                  option_name=option_name, option_type=option_type,
                                  option_value=option_value, comment=comment)
    if ok:
        write_dhcp_config()
        return redirect(f"/dhcp?message=DHCP option {option_code} added")
    return redirect(f"/dhcp?message={result}&error=1")


@app.route("/dhcp/delete-option/<int:option_id>", methods=["POST"])
def dhcp_delete_option(option_id):
    ok, msg = delete_dhcp_option(option_id)
    write_dhcp_config()
    return redirect(f"/dhcp?message={msg}")


@app.route("/network/multiwan/add", methods=["POST"])
def multiwan_add():
    name = request.form.get("name", "").strip()
    display_name = request.form.get("display_name", "").strip()
    interface = request.form.get("interface", "").strip()
    gateway = request.form.get("gateway", "").strip()
    role = request.form.get("role", "active").strip()
    priority = int(request.form.get("priority", "100") or 100)
    weight = int(request.form.get("weight", "100") or 100)
    health_check_ip = request.form.get("health_check_ip", "8.8.8.8").strip()
    metric_base = int(request.form.get("metric_base", "100") or 100)
    ok, msg = mw_add(name=name, display_name=display_name, interface=interface,
                     gateway=gateway, weight=weight, role=role, priority=priority,
                     health_check_ip=health_check_ip, metric_base=metric_base)
    if is_ajax():
        return json_response({"ok": ok, "message": msg})
    return redirect(f"/network?message={msg}" + ("" if ok else "&error=1"))


@app.route("/network/multiwan/delete", methods=["POST"])
def multiwan_delete():
    iface_id = int(request.form.get("id", "0"))
    ok, msg = mw_delete(iface_id)
    if is_ajax():
        return json_response({"ok": ok, "message": msg})
    return redirect(f"/network?message={msg}" + ("" if ok else "&error=1"))


@app.route("/network/multiwan/edit", methods=["POST"])
def multiwan_edit():
    iface_id = int(request.form.get("id", "0"))
    fields = {}
    for k in ("name", "display_name", "interface", "gateway", "role", "priority",
              "weight", "health_check_ip", "metric_base", "notes"):
        v = request.form.get(k, "").strip()
        if v:
            if k in ("priority", "weight", "metric_base"):
                fields[k] = int(v)
            else:
                fields[k] = v
    ok, msg = mw_update(iface_id, **fields)
    if is_ajax():
        return json_response({"ok": ok, "message": msg})
    return redirect(f"/network?message={msg}" + ("" if ok else "&error=1"))


@app.route("/network/multiwan/toggle-iface", methods=["POST"])
def multiwan_toggle_iface():
    iface_id = int(request.form.get("id", "0"))
    enabled = int(request.form.get("enabled", "1"))
    ok, msg = mw_update(iface_id, enabled=enabled)
    if ok:
        mw_log_event(mw_get_interface(iface_id)["name"] if mw_get_interface(iface_id) else f"iface-{iface_id}",
                     "toggle", "", "enabled" if enabled else "disabled")
    if is_ajax():
        return json_response({"ok": ok, "message": msg or ("Enabled" if enabled else "Disabled")})
    return redirect(f"/network?message={msg}")


@app.route("/network/multiwan/toggle-service", methods=["POST"])
def multiwan_toggle_service():
    cur = mw_get_settings()
    new_val = "0" if cur.get("enabled", "0") == "1" else "1"
    mw_set_setting("enabled", new_val)
    if new_val == "1":
        mw_start_daemon()
    msg = "Multi-WAN enabled" if new_val == "1" else "Multi-WAN disabled"
    if is_ajax():
        return json_response({"ok": True, "message": msg})
    return redirect(f"/network?message={msg}")


@app.route("/network/multiwan/check-now", methods=["POST"])
def multiwan_check_now():
    try:
        mw_check_now()
        msg = "Health checks executed"
    except Exception as e:
        msg = f"Error: {e}"
    if is_ajax():
        return json_response({"ok": True, "message": msg})
    return redirect(f"/network?message={msg}")


@app.route("/network/multiwan/apply-routing", methods=["POST"])
def multiwan_apply_routing():
    try:
        mw_apply_routing()
        msg = "Routing rules applied"
    except Exception as e:
        msg = f"Error: {e}"
    if is_ajax():
        return json_response({"ok": True, "message": msg})
    return redirect(f"/network?message={msg}")


@app.route("/network/multiwan/save-settings", methods=["POST"])
def multiwan_save_settings():
    for k in ("failover_mode", "lb_algorithm", "check_interval", "check_timeout",
              "check_fail_count", "check_ok_count", "sticky_sessions",
              "nat_masquerade", "default_route_metric_base"):
        v = request.form.get(k, "").strip()
        if v:
            mw_set_setting(k, v)
    if is_ajax():
        return json_response({"ok": True, "message": "Settings saved"})
    return redirect("/network?message=Settings saved")


@app.route("/api/multiwan/interface/<int:iface_id>")
def api_multiwan_interface(iface_id):
    iface = mw_get_interface(iface_id)
    if iface:
        return json_response(iface)
    return json_response({"error": "not found"}), 404


@app.route("/api/hostname-rules")
def api_hostname_rules_list():
    from modules.hostname_resolver import get_hostname_rules
    return json_response(get_hostname_rules())


@app.route("/api/hostname-rules", methods=["POST"])
def api_hostname_rules_add():
    data = request.get_json(silent=True) or request.form
    from modules.hostname_resolver import add_hostname_rule
    hostname = data.get("hostname", "").strip().lower()
    rule_type = data.get("rule_type", "both")
    firewall_action = data.get("firewall_action", "")
    comment = data.get("comment", "")
    dns_policy_id = data.get("dns_policy_id")
    if dns_policy_id:
        try:
            dns_policy_id = int(dns_policy_id)
        except (ValueError, TypeError):
            dns_policy_id = None
    ok, result = add_hostname_rule(hostname, rule_type=rule_type,
                                   firewall_action=firewall_action,
                                   dns_policy_id=dns_policy_id, comment=comment)
    return json_response({"ok": ok, "id" if ok else "error": result})


@app.route("/api/hostname-rules/<path:hostname>", methods=["DELETE"])
def api_hostname_rules_delete(hostname):
    from modules.hostname_resolver import remove_hostname_rule
    ok, msg = remove_hostname_rule(hostname)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/hostname-rules/<path:hostname>/toggle", methods=["POST"])
def api_hostname_rules_toggle(hostname):
    data = request.get_json(silent=True) or request.form
    enabled = data.get("enabled", "1") in ("1", "true", True)
    from modules.hostname_resolver import toggle_hostname_rule
    ok, msg = toggle_hostname_rule(hostname, enabled)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/hostname-rules/resolve", methods=["POST"])
def api_hostname_rules_resolve():
    from modules.hostname_resolver import resolve_all
    results = resolve_all()
    return json_response({"ok": True, "results": results})


@app.route("/api/hostname-rules/clients/<int:client_id>/track", methods=["POST"])
def api_hostname_client_track(client_id):
    data = request.get_json(silent=True) or request.form
    track = data.get("track_hostname", True)
    fw_action = data.get("firewall_action", "")
    from modules.hostname_resolver import set_client_hostname_tracking
    ok, msg = set_client_hostname_tracking(client_id, track, fw_action)
    return json_response({"ok": ok, "message": msg})


@app.route("/api/hostname-rules/tracked-clients")
def api_hostname_tracked_clients():
    from modules.hostname_resolver import get_tracked_clients
    return json_response(get_tracked_clients())


if __name__ == "__main__":
    _cfg = {}
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "config.json")) as _f:
            _cfg = json.load(_f)
    except Exception:
        pass
    _host = os.environ.get("LISTEN_ADDR") or _cfg.get("listen_addr") or "0.0.0.0"
    _port = int(os.environ.get("LISTEN_PORT") or _cfg.get("listen_port") or 8080)
    try:
        if mw_get_settings().get("enabled", "0") == "1":
            mw_start_daemon()
    except Exception:
        pass
    app.run(host=_host, port=_port)
