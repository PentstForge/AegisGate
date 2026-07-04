import os
from datetime import datetime
from flask import render_template_string

REPORTS_DIR = "/opt/nft-dashboard/data/reports"

ALL_SECTIONS = [
    ("summary", "Summary"),
    ("attackers", "Top Attackers"),
    ("ssh", "SSH Brute-Force"),
    ("suricata", "Suricata Alerts"),
    ("risk", "Risk Assessment"),
    ("geoip", "GeoIP / Countries"),
    ("qos", "QoS Status"),
    ("bandwidth", "Bandwidth"),
    ("policy", "Policy"),
    ("health", "System Health"),
    ("conntrack", "Conntrack"),
]


def _fmt_bytes(b):
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b / 1024 / 1024:.1f} MB"
    else:
        return f"{b / 1024 / 1024 / 1024:.2f} GB"


def _fmt_bps(bps):
    if bps < 1000:
        return f"{bps:.0f} bps"
    elif bps < 1_000_000:
        return f"{bps / 1000:.1f} Kbps"
    elif bps < 1_000_000_000:
        return f"{bps / 1_000_000:.1f} Mbps"
    else:
        return f"{bps / 1_000_000_000:.2f} Gbps"


def generate_html_report(period="all", sections=None):
    if sections is None:
        sections = [s[0] for s in ALL_SECTIONS]
    os.makedirs(REPORTS_DIR, exist_ok=True)

    from modules.health import get_health
    from modules.parsers import parse_log, parse_auth_log, LOG_FILE
    from modules.nft_utils import nft_set_ips, cscli_json
    from modules.suricata import get_suricata_rules, get_suricata_mode, get_suricata_alerts
    from modules.policy import get_policy
    from modules.qos import get_qos_state, get_qos_stats, get_cake_tin_stats, get_manual_rules_stats, _detect_ifaces
    from modules.bandwidth import get_bandwidth_summary_all, format_bytes, format_bits_per_sec
    from modules.risk import get_all_risks
    from modules.geoip import get_country_stats, get_asn_stats
    from modules.timeline import get_timeline_summary
    from modules.network import get_conntrack_stats
    from collections import Counter

    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    entries = parse_log(lines)
    src_counter = Counter(e["src"] for e in entries if e["src"])
    rule_counter = Counter(e["rule"] for e in entries)
    ssh_fail, _ = parse_auth_log()
    bl_list = nft_set_ips("filter", "blacklist_ipv4")
    cs_list = nft_set_ips("filter", "crowdsec-blacklists")
    health = get_health()
    policy = get_policy()
    suricata_rules = get_suricata_rules()
    suricata_mode = get_suricata_mode()
    suricata_alerts = get_suricata_alerts(n=50)

    all_ips = list(set(
        [e["src"] for e in entries if e.get("src")]
        + [e["ip"] for e in bl_list]
        + [e["ip"] for e in cs_list]
        + list(ssh_fail.keys())
    ))

    now = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    filename = f"security-report-{now}.html"
    filepath = os.path.join(REPORTS_DIR, filename)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Security Report {now}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', monospace; background: #0d1117; color: #e6edf3; padding: 20px; max-width: 1200px; margin: 0 auto; }}
h1 {{ color: #58a6ff; }} h2 {{ color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 4px; margin-top: 30px; }}
table {{ border-collapse: collapse; width: 100%; margin: 8px 0; }}
th, td {{ border: 1px solid #30363d; padding: 6px 10px; text-align: left; }}
th {{ background: #161b22; }}
.ok {{ color: #3fb950; }} .danger {{ color: #f85149; }} .warn {{ color: #d29922; }} .info {{ color: #58a6ff; }}
.stat {{ display: inline-block; background: #1c2333; border: 1px solid #30363d; border-radius: 6px; padding: 10px 16px; margin: 4px; text-align: center; }}
.stat .num {{ font-size: 24px; font-weight: 700; }} .stat .lbl {{ font-size: 11px; color: #8b949e; }}
.section {{ margin-bottom: 20px; }}
.bar {{ background: #1c2333; border-radius: 3px; height: 8px; overflow: hidden; }}
.bar-fill {{ height: 100%; border-radius: 3px; }}
.tin-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; }}
.tin-card {{ background: #1c2333; border: 1px solid #30363d; border-radius: 6px; padding: 8px 12px; }}
footer {{ color: #484f58; font-size: 11px; margin-top: 30px; border-top: 1px solid #30363d; padding-top: 8px; }}
</style></head><body>
<h1>Security Report</h1>
<p>Generated: {now} | Period: {period} | Policy: {policy.get('mode','unknown')}</p>
"""

    if "summary" in sections:
        tl = get_timeline_summary(period if period != "all" else "7d")
        html += f"""
<div class="section">
<h2>Summary</h2>
<div style="display:flex;flex-wrap:wrap;">
<div class="stat"><div class="num">{len(entries)}</div><div class="lbl">Total Drops</div></div>
<div class="stat"><div class="num">{len(src_counter)}</div><div class="lbl">Unique Sources</div></div>
<div class="stat"><div class="num danger">{len(bl_list)}</div><div class="lbl">Blacklist</div></div>
<div class="stat"><div class="num warn">{len(cs_list)}</div><div class="lbl">CrowdSec</div></div>
<div class="stat"><div class="num">{sum(ssh_fail.values())}</div><div class="lbl">SSH Fails</div></div>
<div class="stat"><div class="num">{suricata_rules}</div><div class="lbl">Suricata Rules</div></div>
<div class="stat"><div class="num">{tl.get('total_suricata', 0)}</div><div class="lbl">Suricata Alerts</div></div>
<div class="stat"><div class="num">{tl.get('total_cs_bans', 0)}</div><div class="lbl">CS Bans</div></div>
</div></div>
"""

    if "attackers" in sections and src_counter:
        html += f"""
<div class="section">
<h2>Top 20 Attackers</h2>
<table><tr><th>#</th><th>IP</th><th>Hits</th><th>In BL</th><th>In CS</th></tr>
"""
        for i, (ip, count) in enumerate(src_counter.most_common(20)):
            bl_flag = '<span class="danger">YES</span>' if any(e["ip"] == ip for e in bl_list) else '&mdash;'
            cs_flag = '<span class="warn">YES</span>' if any(e["ip"] == ip for e in cs_list) else '&mdash;'
            html += f"<tr><td>{i+1}</td><td>{ip}</td><td>{count}</td><td>{bl_flag}</td><td>{cs_flag}</td></tr>\n"
        html += "</table></div>\n"

    if "ssh" in sections and ssh_fail:
        html += f"""
<div class="section">
<h2>SSH Brute-Force Top 20</h2>
<table><tr><th>#</th><th>IP</th><th>Failed Attempts</th></tr>
"""
        for i, (ip, count) in enumerate(ssh_fail.most_common(20)):
            html += f"<tr><td>{i+1}</td><td>{ip}</td><td>{count}</td></tr>\n"
        html += "</table></div>\n"

    if "suricata" in sections and suricata_alerts:
        html += f"""
<div class="section">
<h2>Suricata Alerts (Top 50)</h2>
<p>Mode: <strong>{suricata_mode}</strong> | Rules loaded: {suricata_rules}</p>
<table><tr><th>Time</th><th>Source</th><th>Dest</th><th>Proto</th><th>Port</th><th>Signature</th><th>SID</th></tr>
"""
        for a in suricata_alerts:
            html += f'<tr><td>{a["time"]}</td><td>{a["src"]}</td><td>{a["dst"]}</td><td>{a["proto"]}</td><td>{a["dpt"]}</td><td>{a["msg"]}</td><td>{a["sid"]}</td></tr>\n'
        html += "</table></div>\n"

    if "risk" in sections:
        try:
            risks = get_all_risks(limit=30)
            if risks:
                html += """
<div class="section">
<h2>Risk Assessment (Top 30)</h2>
<table><tr><th>#</th><th>IP</th><th>Score</th><th>Hits</th><th>Ports</th><th>Flags</th></tr>
"""
                for i, r in enumerate(risks):
                    score_class = "danger" if r.get("score", 0) >= 70 else ("warn" if r.get("score", 0) >= 40 else "ok")
                    ports = ", ".join(str(p) for p in r.get("ports_scanned", [])[:5])
                    flags = ", ".join(r.get("flags", []))
                    html += f'<tr><td>{i+1}</td><td>{r.get("ip","")}</td><td class="{score_class}">{r.get("score",0)}</td><td>{r.get("hits",0)}</td><td>{ports}</td><td>{flags}</td></tr>\n'
                html += "</table></div>\n"
        except Exception:
            pass

    if "geoip" in sections and all_ips:
        try:
            countries = get_country_stats(all_ips)
            asns = get_asn_stats(all_ips)
            if countries:
                total = sum(countries.values()) or 1
                html += """
<div class="section">
<h2>GeoIP &mdash; Top Countries</h2>
<table><tr><th>#</th><th>Country</th><th>IPs</th><th>%</th></tr>
"""
                for i, (cc, cnt) in enumerate(list(countries.items())[:20]):
                    pct = round(cnt / total * 100, 1)
                    html += f"<tr><td>{i+1}</td><td>{cc}</td><td>{cnt}</td><td>{pct}%</td></tr>\n"
                html += "</table></div>\n"
            if asns:
                total_asn = sum(asns.values()) or 1
                html += """
<div class="section">
<h2>GeoIP &mdash; Top ASNs</h2>
<table><tr><th>#</th><th>ASN</th><th>IPs</th><th>%</th></tr>
"""
                for i, (asn, cnt) in enumerate(list(asns.items())[:15]):
                    pct = round(cnt / total_asn * 100, 1)
                    html += f"<tr><td>{i+1}</td><td>{asn}</td><td>{cnt}</td><td>{pct}%</td></tr>\n"
                html += "</table></div>\n"
        except Exception:
            pass

    if "qos" in sections:
        try:
            from modules.qos import DSCP_MAP, PRIORITY_LEVELS, get_all_priority_classes
            qos = get_qos_state()
            if qos.get("enabled"):
                profile = qos.get("profiles", {}).get(qos.get("active_profile", ""), {})
                html += f"""
<div class="section">
<h2>QoS Status</h2>
<p>Active profile: <strong>{qos.get("active_profile","unknown")}</strong> | Algorithm: <strong>{profile.get("algorithm","N/A").upper()}</strong> | Download: <strong>{profile.get("download_mbit","N/A")} Mbit/s</strong> | Upload: <strong>{profile.get("upload_mbit","N/A")} Mbit/s</strong></p>
<table><tr><th>Interface</th><th>Algorithm</th><th>Bandwidth</th><th>Sent</th><th>Dropped</th></tr>
"""
                wan, lan, vpn_ifaces = _detect_ifaces()
                for iface in [wan, lan] + vpn_ifaces:
                    if not iface:
                        continue
                    stats = get_qos_stats(iface)
                    is_wan = (iface == wan)
                    bw_val = f"{profile.get('upload_mbit', 'N/A')} Mbit/s ↑" if is_wan else f"{profile.get('download_mbit', 'N/A')} Mbit/s ↓"
                    algo = "CAKE" if "cake" in str(stats.get("raw", "")) else "fq_codel"
                    html += f'<tr><td>{iface}</td><td>{algo}</td><td>{bw_val}</td><td>{_fmt_bytes(stats.get("sent_bytes", 0))}</td><td class="danger">{stats.get("dropped", 0)}</td></tr>\n'
                html += "</table>"

                priorities = profile.get("priorities", {})
                all_classes = get_all_priority_classes()
                TIN_MAP = {"cs7": "Voice", "cs6": "Voice", "cs5": "Voice", "ef": "Voice",
                           "cs4": "Video", "cs3": "Video", "af41": "Video", "af42": "Video", "af43": "Video",
                           "cs0": "Best Effort", "cs1": "Bulk", "cs2": "Bulk"}
                tin_order = ["Voice", "Best Effort", "Video", "Bulk"]
                tin_groups = {t: [] for t in tin_order}
                for cls_key, prio_level in priorities.items():
                    cls = all_classes.get(cls_key, {})
                    dscp = DSCP_MAP.get(prio_level, "cs0")
                    tin = TIN_MAP.get(dscp, "Best Effort")
                    tin_groups[tin].append({"key": cls_key, "label": cls.get("label", cls_key),
                        "ports": cls.get("ports", ""), "networks": cls.get("networks", ""),
                        "protocols": cls.get("protocols", ""), "priority": prio_level,
                        "priority_label": PRIORITY_LEVELS.get(prio_level, {}).get("label", prio_level),
                        "dscp": dscp, "cake_tin": tin, "icon": cls.get("icon", "")})

                html += '<h3>Priority → CAKE Tin Mapping</h3>'
                tin_colors = {"Voice": "#8b5cf6", "Best Effort": "#3fb950", "Video": "#58a6ff", "Bulk": "#f85149"}
                for tin in tin_order:
                    items = tin_groups[tin]
                    if not items:
                        continue
                    c = tin_colors.get(tin, "#8b949e")
                    html += f'<h4 style="color:{c};margin-top:12px">{tin} Tin</h4>'
                    html += '<table><tr><th>Class</th><th>Ports</th><th>Networks</th><th>Proto</th><th>Priority</th><th>DSCP</th></tr>'
                    for it in items:
                        html += f'<tr><td>{it["icon"]} {it["label"]}</td><td>{it["ports"] or "&mdash;"}</td><td>{it["networks"] or "&mdash;"}</td><td>{it["protocols"].upper() if it["protocols"] else "Any"}</td><td><span class="{it["priority"]}">{it["priority_label"]}</span></td><td>{it["dscp"]}</td></tr>\n'
                    html += '</table>'

                for iface in [wan, lan] + vpn_ifaces:
                    if not iface:
                        continue
                    tin_data = get_cake_tin_stats(iface)
                    tins = tin_data.get("tins", [])
                    if not tins:
                        continue
                    html += f'<h3>CAKE Tins — {iface}</h3>'
                    html += '<table><tr><th>Tin</th><th>Packets</th><th>Bytes</th><th>Drops</th><th>Overlimits</th><th>Flows</th></tr>'
                    total = tin_data.get("total", {})
                    html += f'<tr style="font-weight:700"><td>Total</td><td>{total.get("pkts",0)}</td><td>{_fmt_bytes(total.get("bytes",0))}</td><td class="danger">{total.get("drops",0)}</td><td>{total.get("overlimits",0)}</td><td>&mdash;</td></tr>'
                    for t in tins:
                        html += f'<tr><td style="color:{tin_colors.get(t["name"],"")}">{t["name"]}</td><td>{t.get("pkts",0)}</td><td>{_fmt_bytes(t.get("bytes",0))}</td><td class="danger">{t.get("drops",0)}</td><td>{t.get("overlimits",0)}</td><td>{t.get("sp_flows",0)}/{t.get("bk_flows",0)}</td></tr>\n'
                    html += '</table>'

                manual = get_manual_rules_stats()
                if manual:
                    html += """
<h3>Manual QoS Rules</h3>
<table><tr><th>Interface</th><th>Match</th><th>Packets</th><th>Bytes</th><th>Drops</th></tr>
"""
                    for r in manual[:15]:
                        html += f'<tr><td>{r.get("iface","")}</td><td>{r.get("match","")}</td><td>{r.get("pkts",0)}</td><td>{_fmt_bytes(r.get("bytes",0))}</td><td class="danger">{r.get("drops",0)}</td></tr>\n'
                    html += "</table>"
                html += "</div>\n"
            else:
                html += '<div class="section"><h2>QoS Status</h2><p>QoS is <span class="warn">DISABLED</span></p></div>\n'
        except Exception:
            pass

    if "bandwidth" in sections:
        try:
            bw = get_bandwidth_summary_all()
            if bw:
                html += """
<div class="section">
<h2>Bandwidth Summary</h2>
<table><tr><th>Channel</th><th>Interface</th><th>RX</th><th>TX</th><th>RX Packets</th><th>TX Packets</th></tr>
"""
                for ch, d in bw.items():
                    html += f'<tr><td>{ch}</td><td>{d.get("interface","")}</td><td>{_fmt_bytes(d.get("rx_bytes",0))}</td><td>{_fmt_bytes(d.get("tx_bytes",0))}</td><td>{d.get("rx_packets",0)}</td><td>{d.get("tx_packets",0)}</td></tr>\n'
                html += "</table></div>\n"
        except Exception:
            pass

    if "policy" in sections:
        html += f"""
<div class="section">
<h2>Policy: {policy.get('mode', 'unknown').capitalize()}</h2>
<table><tr><th>Setting</th><th>Value</th></tr>
<tr><td>SSH rate limit</td><td>{policy.get('ssh_rate_limit', 'N/A')}</td></tr>
<tr><td>SSH ban threshold</td><td>{policy.get('ssh_ban_threshold', 'N/A')}</td></tr>
<tr><td>Port scan threshold</td><td>{policy.get('port_scan_threshold', 'N/A')}</td></tr>
<tr><td>Blacklist TTL</td><td>{policy.get('blacklist_ttl', 'N/A')}</td></tr>
</table></div>
"""

    if "health" in sections:
        html += f"""
<div class="section">
<h2>System Health</h2>
<table><tr><th>Metric</th><th>Value</th></tr>
<tr><td>CPU Temp</td><td>{health['cpu_temp']}&deg;C</td></tr>
<tr><td>CPU Usage</td><td>{health['cpu_usage']}%</td></tr>
<tr><td>Memory</td><td>{health['memory']['pct']}% ({health['memory']['used_mb']}/{health['memory']['total_mb']} MB)</td></tr>
<tr><td>Uptime</td><td>{health['uptime']}</td></tr>
</table>

<h3>Services</h3>
<table><tr><th>Service</th><th>Status</th></tr>
"""
        for svc in health.get('services', []):
            status_class = 'ok' if svc['status'] == 'active' else 'danger'
            html += f"<tr><td>{svc['name']}</td><td class='{status_class}'>{svc['status']}</td></tr>\n"
        html += "</table></div>\n"

    if "conntrack" in sections:
        try:
            ct = get_conntrack_stats()
            pct = ct.get("pct", 0)
            pct_class = "danger" if pct > 80 else ("warn" if pct > 60 else "ok")
            html += f"""
<div class="section">
<h2>Conntrack</h2>
<div class="stat"><div class="num {pct_class}">{pct:.1f}%</div><div class="lbl">Usage</div></div>
<div class="stat"><div class="num">{ct.get('count', 0)}</div><div class="lbl">Active</div></div>
<div class="stat"><div class="num">{ct.get('limit', 0)}</div><div class="lbl">Limit</div></div>
</div>
"""
        except Exception:
            pass

    html += f"""
<footer>Generated by RPiGwSec Security Dashboard | {now}</footer>
</body></html>"""

    with open(filepath, "w") as f:
        f.write(html)
    return filename


def get_previous_reports(max_reports=20):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    reports = []
    for f in sorted(os.listdir(REPORTS_DIR), reverse=True):
        if f.endswith(".html"):
            filepath = os.path.join(REPORTS_DIR, f)
            size = os.path.getsize(filepath)
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d %H:%M")
            reports.append({"filename": f, "size": f"{size/1024:.1f} KB", "url": f"/export/download/{f}", "mtime": mtime})
            if len(reports) >= max_reports:
                break
    return reports


def delete_report(filename):
    filepath = os.path.join(REPORTS_DIR, filename)
    if os.path.isfile(filepath) and filename.endswith(".html"):
        os.remove(filepath)
        return True
    return False


def cleanup_old_reports(max_age_days=30):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    now = datetime.now().timestamp()
    for f in os.listdir(REPORTS_DIR):
        filepath = os.path.join(REPORTS_DIR, f)
        if os.path.isfile(filepath) and now - os.path.getmtime(filepath) > max_age_days * 86400:
            os.remove(filepath)