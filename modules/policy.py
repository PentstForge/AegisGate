import json
import os
import subprocess
import logging
import re
from datetime import datetime

NFT = "/usr/sbin/nft"
POLICY_FILE = "/opt/nft-dashboard/data/policy.json"
AUTO_BAN_CONFIG = "/opt/nft-dashboard/data/auto-ban-config.json"
CROWDSEC_BOUNCER_CFG = "/etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml"
SURICATA_LOCAL_RULES = "/etc/suricata/rules/local-bridge.rules"
SURICATA_LOCAL_RULES_DISABLED = "/etc/suricata/rules/local-bridge.rules.disabled"

log = logging.getLogger("policy")

POLICIES = {
    "monitor": {
        "ssh_rate_limit": "10/minute",
        "ssh_rate_burst": 10,
        "newconn_rate": "200/second",
        "newconn_burst": 50,
        "ssh_ban_threshold": 0,
        "port_scan_threshold": 0,
        "syn_flood_threshold": 0,
        "blacklist_ttl": "0",
        "crowdsec_mode": "log",
        "suricata_local_rules": "alert",
        "bogon_mode": "log",
        "emergency_bypass": True,
        "description": "Observe only. No auto-bans. All thresholds disabled.",
    },
    "balanced": {
        "ssh_rate_limit": "3/minute",
        "ssh_rate_burst": 5,
        "newconn_rate": "100/second",
        "newconn_burst": 30,
        "ssh_ban_threshold": 5,
        "port_scan_threshold": 8,
        "syn_flood_threshold": 10,
        "blacklist_ttl": "24h",
        "crowdsec_mode": "ban",
        "suricata_local_rules": "drop_critical",
        "bogon_mode": "drop",
        "emergency_bypass": True,
        "description": "Default balanced security. Ban SSH brute-force (5+ fails), port scans (8+ ports).",
    },
    "strict": {
        "ssh_rate_limit": "1/minute",
        "ssh_rate_burst": 3,
        "newconn_rate": "50/second",
        "newconn_burst": 15,
        "ssh_ban_threshold": 3,
        "port_scan_threshold": 5,
        "syn_flood_threshold": 5,
        "blacklist_ttl": "48h",
        "crowdsec_mode": "ban",
        "suricata_local_rules": "drop_all",
        "bogon_mode": "drop",
        "emergency_bypass": True,
        "description": "Aggressive. Lower thresholds, longer bans, Suricata drops all local rules.",
    },
    "paranoid": {
        "ssh_rate_limit": "1/minute",
        "ssh_rate_burst": 2,
        "newconn_rate": "30/second",
        "newconn_burst": 10,
        "ssh_ban_threshold": 2,
        "port_scan_threshold": 3,
        "syn_flood_threshold": 3,
        "blacklist_ttl": "168h",
        "crowdsec_mode": "ban_aggressive",
        "suricata_local_rules": "drop_all",
        "bogon_mode": "drop",
        "emergency_bypass": False,
        "description": "Maximum security. Ban at 2 SSH fails, 3 port scans, 7-day blacklists. No emergency bypass.",
    },
}


def _nft_rule(rule_str):
    try:
        r = subprocess.run([NFT, "-f", "-"], input=rule_str + "\n",
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            log.warning("nft -f rule failed: %s | %s", rule_str, r.stderr.strip())
        return r.returncode == 0
    except Exception as e:
        log.error("nft -f error: %s", e)
        return False


def _nft(cmd):
    try:
        r = subprocess.run([NFT] + cmd, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            log.warning("nft %s failed: %s", cmd, r.stderr.strip())
        return r.returncode == 0
    except Exception as e:
        log.error("nft error: %s", e)
        return False


def _svc(action, service):
    timeout = 60 if action in ("restart", "reload") else 30
    try:
        r = subprocess.run(["systemctl", action, service], capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except Exception as e:
        log.error("systemctl %s %s error: %s", action, service, e)
        return False


def _get_chain_text(chain_name, table="filter", family="inet"):
    try:
        r = subprocess.run([NFT, "-a", "list", "chain", family, table, chain_name],
                           capture_output=True, text=True, timeout=10)
        return r.stdout
    except Exception:
        return ""


def _find_handle(chain_text, keyword):
    for line in reversed(chain_text.splitlines()):
        line = line.strip()
        if keyword in line and "handle" in line:
            parts = line.rsplit("handle", 1)
            try:
                return int(parts[-1].strip().split()[0])
            except (ValueError, IndexError):
                pass
    return None


def _find_all_handles(chain_text, keyword):
    handles = []
    for line in chain_text.splitlines():
        line = line.strip()
        if keyword in line and "handle" in line:
            parts = line.rsplit("handle", 1)
            try:
                h = int(parts[-1].strip().split()[0])
                handles.append(h)
            except (ValueError, IndexError):
                pass
    return sorted(handles, reverse=True)


def _parse_rate(rate_str):
    parts = rate_str.split("/")
    if len(parts) != 2:
        return None, None
    return int(parts[0]), parts[1]


def _apply_ssh_rate_limit(policy):
    rate_str = policy.get("ssh_rate_limit", "3/minute")
    burst = policy.get("ssh_rate_burst", 5)
    rate_val, rate_unit = _parse_rate(rate_str)
    if rate_val is None:
        return False

    chain_text = _get_chain_text("input")
    h = _find_handle(chain_text, "tcp dport 22 limit rate")
    if h is not None:
        _nft(["delete", "rule", "inet", "filter", "input", "handle", str(h)])

    drop_handle = _find_handle(chain_text, "DROP_DEFAULT_IN")
    rule = f"tcp dport 22 limit rate over {rate_val}/{rate_unit} burst {burst} packets accept"
    if drop_handle is not None:
        _nft_rule(f"insert rule inet filter input handle {drop_handle} {rule}")
    else:
        _nft_rule(f"add rule inet filter input {rule}")
    return True


def _apply_newconn_rate(policy):
    rate_str = policy.get("newconn_rate", "100/second")
    burst = policy.get("newconn_burst", 30)
    rate_val, rate_unit = _parse_rate(rate_str)
    if rate_val is None:
        return False

    chain_text = _get_chain_text("input")
    h = _find_handle(chain_text, "DROP_NEWCONN_IN")
    if h is not None:
        _nft(["delete", "rule", "inet", "filter", "input", "handle", str(h)])

    drop_handle = _find_handle(chain_text, "DROP_DEFAULT_IN")
    rule = f"meta l4proto {{ tcp, udp }} ct state new limit rate over {rate_val}/{rate_unit} burst {burst} packets log prefix \"DROP_NEWCONN_IN: \" drop"
    if drop_handle is not None:
        _nft_rule(f"insert rule inet filter input handle {drop_handle} {rule}")
    else:
        _nft_rule(f"add rule inet filter input {rule}")
    return True


def _apply_bogon_mode(policy):
    mode = policy.get("bogon_mode", "drop")
    action = "accept" if mode == "log" else "drop"
    prefix = "LOG" if mode == "log" else "DROP"

    chain_text = _get_chain_text("forward_antispoof")
    handles_to_remove = []
    for line in chain_text.splitlines():
        line = line.strip()
        if "handle" not in line:
            continue
        parts = line.rsplit("handle", 1)
        try:
            h = int(parts[-1].strip().split()[0])
        except (ValueError, IndexError):
            continue
        if any(kw in line for kw in ["BOGON", "SPOOF", "LOOPBACK", "MCAST", "BCAST", "ULA_SPOOF"]):
            handles_to_remove.append(h)

    for h in sorted(handles_to_remove, reverse=True):
        _nft(["delete", "rule", "inet", "filter", "forward_antispoof", "handle", str(h)])

    _nft_rule(f'add rule inet filter forward_antispoof ip saddr @bogon_ipv4 log prefix "{prefix}_BOGON_SRC: " {action}')
    _nft_rule(f'add rule inet filter forward_antispoof ip daddr @bogon_ipv4 log prefix "{prefix}_BOGON_DST: " {action}')
    _nft_rule(f'add rule inet filter forward_antispoof ip6 saddr @bogon_ipv6 log prefix "{prefix}_BOGON6_SRC: " {action}')
    _nft_rule(f'add rule inet filter forward_antispoof ip6 daddr @bogon_ipv6 log prefix "{prefix}_BOGON6_DST: " {action}')
    _nft_rule(f'add rule inet filter forward_antispoof ip saddr @rfc1918_ipv4 ip daddr != @rfc1918_ipv4 ip saddr != @lan_trusted log prefix "{prefix}_SPOOF_RFC1918: " {action}')
    _nft_rule(f'add rule inet filter forward_antispoof ip saddr 127.0.0.0/8 log prefix "{prefix}_LOOPBACK_SRC: " {action}')
    _nft_rule(f'add rule inet filter forward_antispoof ip daddr 127.0.0.0/8 log prefix "{prefix}_LOOPBACK_DST: " {action}')
    _nft_rule(f'add rule inet filter forward_antispoof ip daddr 224.0.0.0/4 log prefix "{prefix}_MCAST_DST: " {action}')
    _nft_rule(f'add rule inet filter forward_antispoof ip daddr 255.255.255.255 log prefix "{prefix}_BCAST_DST: " {action}')
    _nft_rule(f'add rule inet filter forward_antispoof ip6 saddr fc00::/7 ip6 daddr != fc00::/7 log prefix "{prefix}_ULA_SPOOF: " {action}')

    input_text = _get_chain_text("input")
    input_bogon_handles = []
    for kw in ["BOGON_IN", "LOG_BOGON_IN"]:
        for h in _find_all_handles(input_text, kw):
            if h not in input_bogon_handles:
                input_bogon_handles.append(h)
    for h in sorted(input_bogon_handles, reverse=True):
        _nft(["delete", "rule", "inet", "filter", "input", "handle", str(h)])

    drop_default_handle = _find_handle(input_text, "DROP_DEFAULT_IN")
    if mode == "log":
        if drop_default_handle is not None:
            _nft_rule(f'insert rule inet filter input handle {drop_default_handle} ip saddr @bogon_ipv4 ct state new log prefix "LOG_BOGON_IN: "')
        else:
            _nft_rule('add rule inet filter input ip saddr @bogon_ipv4 ct state new log prefix "LOG_BOGON_IN: "')
    else:
        if drop_default_handle is not None:
            _nft_rule(f'insert rule inet filter input handle {drop_default_handle} ip saddr @bogon_ipv4 ct state new log prefix "DROP_BOGON_IN: " drop')
        else:
            _nft_rule('add rule inet filter input ip saddr @bogon_ipv4 ct state new log prefix "DROP_BOGON_IN: " drop')
    return True


def _apply_crowdsec_mode(policy):
    mode = policy.get("crowdsec_mode", "ban")

    if not os.path.exists(CROWDSEC_BOUNCER_CFG):
        return True

    with open(CROWDSEC_BOUNCER_CFG, "r") as f:
        content = f.read()

    if mode == "log":
        content = re.sub(r'^deny_action:\s*\S+', 'deny_action: LOG', content, flags=re.MULTILINE)
        content = re.sub(r'^deny_log:\s*\S+', 'deny_log: true', content, flags=re.MULTILINE)
    else:
        content = re.sub(r'^deny_action:\s*\S+', 'deny_action: DROP', content, flags=re.MULTILINE)
        content = re.sub(r'^deny_log:\s*\S+', 'deny_log: false', content, flags=re.MULTILINE)

    with open(CROWDSEC_BOUNCER_CFG, "w") as f:
        f.write(content)

    _svc("restart", "crowdsec-firewall-bouncer")

    return True


SURICATA_RULES = {
    "drop_critical": [
        ('drop', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', '22', '[BRIDGE] SSH brute force attempt', 'flow:to_server; threshold:type both, track by_src, count 5, seconds 60; classtype:attempted-admin', 9000010),
        ('drop', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] Known malicious outbound C2 pattern', 'flow:established; content:"User-Agent|3a| "; content:"sqlmap"; nocase; classtype:trojan-activity', 9000040),
        ('drop', 'http', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] Dir traversal attempt', 'http.uri; content:"../"; nocase; classtype:web-application-attack', 9000050),
        ('drop', 'http', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] SQL injection attempt', 'http.uri; content:"\' OR "; nocase; classtype:web-application-attack', 9000051),
        ('drop', 'http', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] XSS attempt', 'http.uri; content:"<script"; nocase; classtype:web-application-attack', 9000052),
        ('alert', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] TCP SYN flood detected', 'flags:S; threshold:type both, track by_src, count 100, seconds 10; classtype:attempted-dos', 9000001),
        ('alert', 'icmp', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] ICMP flood detected', 'threshold:type both, track by_src, count 50, seconds 5; classtype:attempted-dos', 9000002),
        ('alert', 'udp', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] UDP flood detected', 'threshold:type both, track by_src, count 200, seconds 10; classtype:attempted-dos', 9000003),
        ('alert', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', '80', '[BRIDGE] HTTP scan/flood', 'flow:to_server; threshold:type both, track by_src, count 50, seconds 10; classtype:attempted-recon', 9000011),
        ('alert', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', '443', '[BRIDGE] HTTPS scan/flood', 'flow:to_server; threshold:type both, track by_src, count 50, seconds 10; classtype:attempted-recon', 9000012),
        ('alert', 'dns', '$EXTERNAL_NET', 'any', '$HOME_NET', '53', '[BRIDGE] DNS amplification attempt', 'threshold:type both, track by_src, count 100, seconds 10; classtype:attempted-dos', 9000020),
        ('alert', 'tcp', 'any', 'any', '$HOME_NET', 'any', '[BRIDGE] Suspicious port scan SYN', 'flags:S; threshold:type both, track by_src, count 30, seconds 60; classtype:attempted-recon', 9000030),
    ],
    "alert": [
        ('alert', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', '22', '[BRIDGE] SSH brute force attempt', 'flow:to_server; threshold:type both, track by_src, count 5, seconds 60; classtype:attempted-admin', 9000010),
        ('alert', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] Known malicious outbound C2 pattern', 'flow:established; content:"User-Agent|3a| "; content:"sqlmap"; nocase; classtype:trojan-activity', 9000040),
        ('alert', 'http', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] Dir traversal attempt', 'http.uri; content:"../"; nocase; classtype:web-application-attack', 9000050),
        ('alert', 'http', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] SQL injection attempt', 'http.uri; content:"\' OR "; nocase; classtype:web-application-attack', 9000051),
        ('alert', 'http', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] XSS attempt', 'http.uri; content:"<script"; nocase; classtype:web-application-attack', 9000052),
        ('alert', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] TCP SYN flood detected', 'flags:S; threshold:type both, track by_src, count 100, seconds 10; classtype:attempted-dos', 9000001),
        ('alert', 'icmp', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] ICMP flood detected', 'threshold:type both, track by_src, count 50, seconds 5; classtype:attempted-dos', 9000002),
        ('alert', 'udp', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] UDP flood detected', 'threshold:type both, track by_src, count 200, seconds 10; classtype:attempted-dos', 9000003),
        ('alert', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', '80', '[BRIDGE] HTTP scan/flood', 'flow:to_server; threshold:type both, track by_src, count 50, seconds 10; classtype:attempted-recon', 9000011),
        ('alert', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', '443', '[BRIDGE] HTTPS scan/flood', 'flow:to_server; threshold:type both, track by_src, count 50, seconds 10; classtype:attempted-recon', 9000012),
        ('alert', 'dns', '$EXTERNAL_NET', 'any', '$HOME_NET', '53', '[BRIDGE] DNS amplification attempt', 'threshold:type both, track by_src, count 100, seconds 10; classtype:attempted-dos', 9000020),
        ('alert', 'tcp', 'any', 'any', '$HOME_NET', 'any', '[BRIDGE] Suspicious port scan SYN', 'flags:S; threshold:type both, track by_src, count 30, seconds 60; classtype:attempted-recon', 9000030),
    ],
    "drop_all": [
        ('drop', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', '22', '[BRIDGE] SSH brute force attempt', 'flow:to_server; threshold:type both, track by_src, count 5, seconds 60; classtype:attempted-admin', 9000010),
        ('drop', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] Known malicious outbound C2 pattern', 'flow:established; content:"User-Agent|3a| "; content:"sqlmap"; nocase; classtype:trojan-activity', 9000040),
        ('drop', 'http', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] Dir traversal attempt', 'http.uri; content:"../"; nocase; classtype:web-application-attack', 9000050),
        ('drop', 'http', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] SQL injection attempt', 'http.uri; content:"\' OR "; nocase; classtype:web-application-attack', 9000051),
        ('drop', 'http', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] XSS attempt', 'http.uri; content:"<script"; nocase; classtype:web-application-attack', 9000052),
        ('drop', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] TCP SYN flood detected', 'flags:S; threshold:type both, track by_src, count 100, seconds 10; classtype:attempted-dos', 9000001),
        ('drop', 'icmp', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] ICMP flood detected', 'threshold:type both, track by_src, count 50, seconds 5; classtype:attempted-dos', 9000002),
        ('drop', 'udp', '$EXTERNAL_NET', 'any', '$HOME_NET', 'any', '[BRIDGE] UDP flood detected', 'threshold:type both, track by_src, count 200, seconds 10; classtype:attempted-dos', 9000003),
        ('drop', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', '80', '[BRIDGE] HTTP scan/flood', 'flow:to_server; threshold:type both, track by_src, count 50, seconds 10; classtype:attempted-recon', 9000011),
        ('drop', 'tcp', '$EXTERNAL_NET', 'any', '$HOME_NET', '443', '[BRIDGE] HTTPS scan/flood', 'flow:to_server; threshold:type both, track by_src, count 50, seconds 10; classtype:attempted-recon', 9000012),
        ('drop', 'dns', '$EXTERNAL_NET', 'any', '$HOME_NET', '53', '[BRIDGE] DNS amplification attempt', 'threshold:type both, track by_src, count 100, seconds 10; classtype:attempted-dos', 9000020),
        ('drop', 'tcp', 'any', 'any', '$HOME_NET', 'any', '[BRIDGE] Suspicious port scan SYN', 'flags:S; threshold:type both, track by_src, count 30, seconds 60; classtype:attempted-recon', 9000030),
    ],
}


def _apply_suricata_rules(policy):
    mode = policy.get("suricata_local_rules", "drop_critical")

    if os.path.exists(SURICATA_LOCAL_RULES_DISABLED) and not os.path.exists(SURICATA_LOCAL_RULES):
        os.rename(SURICATA_LOCAL_RULES_DISABLED, SURICATA_LOCAL_RULES)

    rules_list = SURICATA_RULES.get(mode, SURICATA_RULES["drop_critical"])
    lines = []
    for action, proto, src, sport, dst, dport, msg, opts, sid in rules_list:
        line = f'{action} {proto} {src} {sport} -> {dst} {dport} (msg:"{msg}"; {opts}; sid:{sid}; rev:2;)'
        lines.append(line)

    with open(SURICATA_LOCAL_RULES, "w") as f:
        f.write("\n".join(lines) + "\n")

    _svc("reload", "suricata")
    return True


def _apply_emergency_bypass(policy):
    bypass = policy.get("emergency_bypass", True)

    chain_text = _get_chain_text("input")
    bypass_handles = []
    for kw in ["EMERGENCY_BYPASS"]:
        bypass_handles.extend(_find_all_handles(chain_text, kw))
    for h in sorted(bypass_handles, reverse=True):
        _nft(["delete", "rule", "inet", "filter", "input", "handle", str(h)])

    if bypass:
        drop_handle = _find_handle(chain_text, "DROP_DEFAULT_IN")
        if drop_handle is not None:
            _nft_rule(f"insert rule inet filter input handle {drop_handle} ip saddr @lan_trusted accept comment \"EMERGENCY_BYPASS\"")

    forward_text = _get_chain_text("forward")
    fwd_bypass_handles = []
    for kw in ["EMERGENCY_BYPASS"]:
        fwd_bypass_handles.extend(_find_all_handles(forward_text, kw))
    for h in sorted(fwd_bypass_handles, reverse=True):
        _nft(["delete", "rule", "inet", "filter", "forward", "handle", str(h)])

    if bypass:
        accept_handle = _find_handle(forward_text, "ct state established,related accept")
        if accept_handle is not None:
            _nft_rule(f"insert rule inet filter forward handle {accept_handle} ip saddr @lan_trusted accept comment \"EMERGENCY_BYPASS\"")

    return True


def _apply_auto_ban_config(policy):
    config = {
        "ssh_threshold": policy.get("ssh_ban_threshold", 5),
        "port_scan_threshold": policy.get("port_scan_threshold", 8),
        "syn_flood_threshold": policy.get("syn_flood_threshold", 10),
        "blacklist_ttl": policy.get("blacklist_ttl", "24h"),
    }
    os.makedirs(os.path.dirname(AUTO_BAN_CONFIG), exist_ok=True)
    with open(AUTO_BAN_CONFIG, "w") as f:
        json.dump(config, f, indent=2)


def get_policy():
    try:
        with open(POLICY_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        data = {"mode": "balanced", "changed_at": datetime.now().isoformat(), "changed_by": "system"}
    mode = data.get("mode", "balanced")
    policy = POLICIES.get(mode, POLICIES["balanced"]).copy()
    policy["mode"] = mode
    policy["changed_at"] = data.get("changed_at", "")
    policy["changed_by"] = data.get("changed_by", "")
    return policy


def set_policy(mode, changed_by="admin"):
    if mode not in POLICIES:
        return False, f"Unknown policy: {mode}"

    old_policy = get_policy()
    policy = POLICIES[mode]

    data = {"mode": mode, "changed_at": datetime.now().isoformat(), "changed_by": changed_by}
    os.makedirs(os.path.dirname(POLICY_FILE), exist_ok=True)
    with open(POLICY_FILE, "w") as f:
        json.dump(data, f, indent=2)

    _apply_auto_ban_config(policy)

    if policy.get("ssh_rate_limit") != old_policy.get("ssh_rate_limit") or policy.get("ssh_rate_burst") != old_policy.get("ssh_rate_burst"):
        try:
            _apply_ssh_rate_limit(policy)
        except Exception as e:
            log.error("Failed to apply SSH rate limit: %s", e)

    if policy.get("newconn_rate") != old_policy.get("newconn_rate") or policy.get("newconn_burst") != old_policy.get("newconn_burst"):
        try:
            _apply_newconn_rate(policy)
        except Exception as e:
            log.error("Failed to apply newconn rate: %s", e)

    if policy.get("bogon_mode") != old_policy.get("bogon_mode"):
        try:
            _apply_bogon_mode(policy)
        except Exception as e:
            log.error("Failed to apply bogon mode: %s", e)

    if policy.get("crowdsec_mode") != old_policy.get("crowdsec_mode"):
        try:
            _apply_crowdsec_mode(policy)
        except Exception as e:
            log.error("Failed to apply CrowdSec mode: %s", e)

    if policy.get("suricata_local_rules") != old_policy.get("suricata_local_rules"):
        try:
            _apply_suricata_rules(policy)
        except Exception as e:
            log.error("Failed to apply Suricata rules: %s", e)

    if policy.get("emergency_bypass") != old_policy.get("emergency_bypass"):
        try:
            _apply_emergency_bypass(policy)
        except Exception as e:
            log.error("Failed to apply emergency bypass: %s", e)

    from modules.firewall import _full_config_sync
    try:
        _full_config_sync()
    except Exception as e:
        log.error("Config sync failed: %s", e)

    return True, f"Policy set to {mode}"


def get_auto_ban_config():
    try:
        with open(AUTO_BAN_CONFIG, "r") as f:
            return json.load(f)
    except Exception:
        return {
            "ssh_threshold": 5,
            "port_scan_threshold": 8,
            "syn_flood_threshold": 10,
            "blacklist_ttl": "24h",
        }


def get_policies():
    return POLICIES