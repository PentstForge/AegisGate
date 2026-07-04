import json
import os
import subprocess
import logging

NFT = "/usr/sbin/nft"
RULES_STATE_FILE = "/opt/nft-dashboard/data/rules_state.json"

log = logging.getLogger("rules_ui")

RULES = {
    "ssh_bruteforce": {
        "enabled": True,
        "category": "input",
        "description": "SSH rate limiting (3/min burst 5)",
        "type": "nft_rate",
        "editable": True,
        "params": {"port": 22, "rate": "3/minute", "burst": 5},
        "param_schema": [
            {"key": "port", "label": "Port", "type": "number", "min": 1, "max": 65535},
            {"key": "rate", "label": "Rate limit", "type": "text", "placeholder": "3/minute"},
            {"key": "burst", "label": "Burst", "type": "number", "min": 1, "max": 100},
        ],
    },
    "http_https": {
        "enabled": True,
        "category": "input",
        "description": "HTTP/HTTPS inbound",
        "type": "nft_accept",
        "editable": True,
        "params": {"ports": [80, 443]},
        "param_schema": [
            {"key": "ports", "label": "Ports (comma separated)", "type": "text", "placeholder": "80, 443"},
        ],
    },
    "dns": {
        "enabled": True,
        "category": "input",
        "description": "DNS inbound (UDP 53, rate-limited 50/sec)",
        "type": "nft_rate",
        "editable": True,
        "params": {"port": 53, "rate": "50/second", "burst": 5},
        "param_schema": [
            {"key": "port", "label": "Port", "type": "number", "min": 1, "max": 65535},
            {"key": "rate", "label": "Rate limit", "type": "text", "placeholder": "50/second"},
            {"key": "burst", "label": "Burst", "type": "number", "min": 1, "max": 100},
        ],
    },
    "ssh_alt": {
        "enabled": True,
        "category": "input",
        "description": "SSH alt port",
        "type": "nft_accept",
        "editable": True,
        "params": {"port": 222},
        "param_schema": [
            {"key": "port", "label": "Port", "type": "number", "min": 1, "max": 65535},
        ],
    },
    "scan_ban": {
        "enabled": True,
        "category": "auto-ban",
        "description": "Port scan auto-ban",
        "type": "auto_ban",
        "editable": True,
        "params": {"threshold": 8, "ban_time": 3600},
        "param_schema": [
            {"key": "threshold", "label": "Port threshold", "type": "number", "min": 1, "max": 100},
            {"key": "ban_time", "label": "Ban time (sec)", "type": "number", "min": 60, "max": 86400},
        ],
    },
    "syn_flood": {
        "enabled": True,
        "category": "forward",
        "description": "SYN flood protection",
        "type": "nft_chain",
    },
    "icmp_flood": {
        "enabled": True,
        "category": "forward",
        "description": "ICMP flood rate limiting",
        "type": "nft_chain",
    },
    "bogon": {
        "enabled": True,
        "category": "forward",
        "description": "Bogon/RFC1918 source drops",
        "type": "nft_chain",
    },
    "bad_tcp": {
        "enabled": True,
        "category": "forward",
        "description": "Bad TCP flags (XMAS/NULL/SYNFIN)",
        "type": "nft_chain",
    },
    "suricata_drops": {
        "enabled": True,
        "category": "suricata",
        "description": "Suricata local drop rules (5 rules)",
        "type": "suricata",
    },
    "crowdsec": {
        "enabled": True,
        "category": "service",
        "description": "CrowdSec firewall bouncer",
        "type": "service",
    },
    "rate_limit_abuse": {
        "enabled": True,
        "category": "forward",
        "description": "Abuse rate limiting (50/sec for flagged IPs)",
        "type": "nft_chain",
    },
}

def _get_rule_params(rule_id, state=None):
    if state is None:
        state = get_rules_state()
    rule = state.get(rule_id, RULES.get(rule_id, {}))
    defaults = RULES.get(rule_id, {}).get("params", {})
    saved = rule.get("params", {}) if isinstance(rule.get("params"), dict) else {}
    merged = {**defaults, **saved}
    return merged


def _build_nft_rules(rule_id, params=None):
    if params is None:
        params = _get_rule_params(rule_id)
    rtype = RULES.get(rule_id, {}).get("type", "")
    rules = []
    if rule_id == "ssh_bruteforce":
        port = params.get("port", 22)
        rate = params.get("rate", "3/minute")
        burst = params.get("burst", 5)
        rules.append(f"add rule inet filter input tcp dport {port} limit rate over {rate} burst {burst} packets accept")
    elif rule_id == "http_https":
        ports = params.get("ports", [80, 443])
        if isinstance(ports, str):
            ports = [int(p.strip()) for p in ports.split(",") if p.strip()]
        port_str = ", ".join(str(p) for p in ports)
        rules.append(f"add rule inet filter input tcp dport {{ {port_str} }} accept")
    elif rule_id == "dns":
        port = params.get("port", 53)
        rate = params.get("rate", "50/second")
        burst = params.get("burst", 5)
        rules.append(f"add rule inet filter input udp dport {port} limit rate over {rate} burst {burst} packets accept")
    elif rule_id == "ssh_alt":
        port = params.get("port", 222)
        rules.append(f"add rule inet filter input tcp dport {port} accept")
    return rules


def _build_detect_keywords(rule_id, params=None):
    if params is None:
        params = _get_rule_params(rule_id)
    keywords = []
    if rule_id == "ssh_bruteforce":
        port = params.get("port", 22)
        keywords.append(f"tcp dport {port} limit rate")
    elif rule_id == "http_https":
        ports = params.get("ports", [80, 443])
        if isinstance(ports, str):
            ports = [int(p.strip()) for p in ports.split(",") if p.strip()]
        port_str = ", ".join(str(p) for p in ports)
        keywords.append(f"tcp dport {{ {port_str} }} accept")
        port_str_nosp = ", ".join(str(p) for p in ports)
        keywords.append(f"tcp dport {{{port_str_nosp}}} accept")
    elif rule_id == "dns":
        port = params.get("port", 53)
        keywords.append(f"udp dport {port} limit rate")
    elif rule_id == "ssh_alt":
        port = params.get("port", 222)
        keywords.append(f"tcp dport {port} accept")
    return keywords

NFT_RULES_REMOVE_BEFORE = {
    "ssh_bruteforce": "DROP_DEFAULT_IN",
    "http_https": "DROP_DEFAULT_IN",
    "dns": "DROP_DEFAULT_IN",
    "ssh_alt": "DROP_DEFAULT_IN",
}

NFT_CHAIN_JUMPS = {
    "bogon": {
        "jump": 'add rule inet filter forward jump forward_antispoof',
        "chain": "forward_antispoof",
        "parent": "forward",
    },
    "bad_tcp": {
        "jump": 'add rule inet filter forward jump forward_badtcp',
        "chain": "forward_badtcp",
        "parent": "forward",
    },
}

NFT_SUBCHAIN_RULES = {
    "syn_flood": {
        "parent": "forward_ratelimit",
        "detect_keywords": ["jump mark_syn_flood"],
        "add_rules": [
            'add rule inet filter forward_ratelimit tcp flags syn ct state new limit rate over 500/second burst 100 packets jump mark_syn_flood',
        ],
    },
    "icmp_flood": {
        "parent": "forward_ratelimit",
        "detect_keywords": ["jump mark_icmp_flood"],
        "add_rules": [
            'add rule inet filter forward_ratelimit ip protocol icmp icmp type echo-request limit rate over 20/second burst 10 packets jump mark_icmp_flood',
        ],
    },
    "rate_limit_abuse": {
        "parent": "forward_ratelimit",
        "detect_keywords": ["@rate_limit_abuse"],
        "add_rules": [
            'add rule inet filter forward_ratelimit meta l4proto { tcp, udp } ct state new ip saddr @rate_limit_abuse limit rate over 50/second burst 5 packets log prefix "DROP_ABUSE_FWD: " drop',
            'add rule inet filter forward_ratelimit ip protocol icmp icmp type echo-request ip saddr @rate_limit_abuse limit rate over 5/second burst 5 packets log prefix "DROP_ABUSE_ICMP: " drop',
        ],
    },
}

NFT_CHAIN_JUMP_KEYWORDS = {
    "bogon": "forward_antispoof",
    "bad_tcp": "forward_badtcp",
}


def _nft(cmd):
    try:
        r = subprocess.run([NFT] + cmd, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            log.warning("nft %s failed: %s", cmd, r.stderr.strip())
        return r.returncode == 0
    except Exception as e:
        log.error("nft error: %s", e)
        return False


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


def _svc(action, service):
    try:
        r = subprocess.run(["systemctl", action, service], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            log.warning("systemctl %s %s failed: %s", action, service, r.stderr.strip())
        return r.returncode == 0
    except Exception as e:
        log.error("systemctl error: %s", e)
        return False


def _is_service_active(service):
    try:
        r = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def _get_cron_lines():
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return []
        return r.stdout.strip().splitlines()
    except Exception:
        return []


def _set_cron_lines(lines):
    try:
        p = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
        p.communicate("\n".join(lines) + "\n", timeout=10)
        return p.returncode == 0
    except Exception as e:
        log.error("crontab error: %s", e)
        return False


def _get_nft_chain_handles(chain_name, table="filter", family="inet"):
    try:
        r = subprocess.run([NFT, "-a", "list", "chain", family, table, chain_name],
                           capture_output=True, text=True, timeout=10)
        handles = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if "handle" in line:
                parts = line.split("handle")
                h = parts[-1].strip().split()[0]
                try:
                    handles.append((int(h), line))
                except ValueError:
                    pass
        return handles
    except Exception:
        return []


def _get_chain_rules(chain_name, table="filter", family="inet"):
    try:
        r = subprocess.run([NFT, "-a", "list", "chain", family, table, chain_name],
                           capture_output=True, text=True, timeout=10)
        return r.stdout
    except Exception:
        return ""


def _get_input_chain_rules():
    return _get_chain_rules("input")


def _get_forward_chain_rules():
    return _get_chain_rules("forward")


def _find_handle_by_keyword(chain_text, keyword):
    for line in reversed(chain_text.splitlines()):
        line = line.strip()
        if keyword in line and "handle" in line:
            parts = line.rsplit("handle", 1)
            try:
                h = int(parts[-1].strip().split()[0])
                return h
            except (ValueError, IndexError):
                pass
    return None


def _insert_rule_before(chain, keyword, rule_body, family="inet", table="filter"):
    chain_text = _get_chain_rules(chain)
    h = _find_handle_by_keyword(chain_text, keyword)
    if h is not None:
        return _nft_rule(f"insert rule {family} {table} {chain} handle {h} {rule_body}")
    else:
        return _nft_rule(f"add rule {family} {table} {chain} {rule_body}")


def _enable_nft_accept(rule_id):
    rules = _build_nft_rules(rule_id)
    anchor = NFT_RULES_REMOVE_BEFORE.get(rule_id)
    for rule_cmd in rules:
        rule_body = rule_cmd.replace("add rule inet filter input ", "")
        if anchor:
            _insert_rule_before("input", anchor, rule_body)
        else:
            _nft_rule(f"add rule inet filter input {rule_body}")


def _disable_nft_accept(rule_id):
    keywords = _build_detect_keywords(rule_id)
    chain_text = _get_input_chain_rules()
    for kw in keywords:
        h = _find_handle_by_keyword(chain_text, kw)
        if h is not None:
            _nft(["delete", "rule", "inet", "filter", "input", "handle", str(h)])


SUBCHAIN_DEPS = {
    "syn_flood": ["mark_syn_flood"],
    "icmp_flood": ["mark_icmp_flood"],
}


def _ensure_chain(chain_name, family="inet", table="filter"):
    r = subprocess.run([NFT, "list", "chain", family, table, chain_name],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        log.info("Creating missing nft chain: %s", chain_name)
        _nft_rule(f"add chain {family} {table} {chain_name}")
    return True


def _ensure_jump_from_forward(sub_chain_name):
    fwd = _get_chain_rules("forward")
    if f"jump {sub_chain_name}" in fwd:
        return True
    anchor_kw = "accept"
    _insert_rule_before("forward", anchor_kw, f"jump {sub_chain_name}")
    return True


def _enable_nft_chain(rule_id):
    for dep in SUBCHAIN_DEPS.get(rule_id, []):
        _ensure_chain(dep)

    info = NFT_CHAIN_JUMPS.get(rule_id)
    if info:
        chain_name = info["chain"]
        _ensure_chain(chain_name)
        _ensure_jump_from_forward(chain_name)
        return True

    sub = NFT_SUBCHAIN_RULES.get(rule_id)
    if not sub:
        return False
    parent = sub["parent"]
    _ensure_chain(parent)
    if parent.startswith("forward_"):
        _ensure_jump_from_forward(parent)
    parent_text = _get_chain_rules(parent)
    all_present = all(kw in parent_text for kw in sub["detect_keywords"])
    if all_present:
        return True
    for rule_spec in sub["add_rules"]:
        rule_body = rule_spec.replace(f"add rule inet filter {parent} ", "")
        _nft_rule(f"add rule inet filter {parent} {rule_body}")
    return True


def _disable_nft_chain(rule_id):
    info = NFT_CHAIN_JUMPS.get(rule_id)
    if info:
        parent = info["parent"]
        parent_text = _get_chain_rules(parent)
        chain_kw = info["chain"]
        h = _find_handle_by_keyword(parent_text, chain_kw)
        if h is not None:
            _nft(["delete", "rule", "inet", "filter", parent, "handle", str(h)])
        return True

    sub = NFT_SUBCHAIN_RULES.get(rule_id)
    if not sub:
        return False
    parent = sub["parent"]
    parent_text = _get_chain_rules(parent)
    for kw in sub["detect_keywords"]:
        while True:
            parent_text = _get_chain_rules(parent)
            h = _find_handle_by_keyword(parent_text, kw)
            if h is not None:
                _nft(["delete", "rule", "inet", "filter", parent, "handle", str(h)])
            else:
                break
    return True


def _enable_auto_ban():
    lines = _get_cron_lines()
    autoban_lines = [l for l in lines if "auto-ban.py" in l and l.strip() and not l.strip().startswith("#")]
    if not autoban_lines:
        new_line = "*/5 * * * * /opt/nft-dashboard/auto-ban.py >> /var/log/ram/auto-ban.log 2>&1"
        lines.append(new_line)
        _set_cron_lines(lines)


def _disable_auto_ban():
    lines = _get_cron_lines()
    lines = [l for l in lines if "auto-ban.py" not in l]
    _set_cron_lines(lines)


def _enable_suricata():
    rules_file = "/etc/suricata/rules/local-bridge.rules"
    backup = rules_file + ".disabled"
    if os.path.exists(backup):
        os.rename(backup, rules_file)
    _svc("restart", "suricata")
    _ensure_suricata_nfq_rules()


def _disable_suricata():
    _remove_suricata_nfq_rules()
    rules_file = "/etc/suricata/rules/local-bridge.rules"
    backup = rules_file + ".disabled"
    if os.path.exists(rules_file):
        os.rename(rules_file, backup)
    _svc("restart", "suricata")


def _has_suricata_nfq_rules():
    input_text = _get_chain_rules("input")
    forward_text = _get_chain_rules("forward")
    return "queue" in input_text and "queue" in forward_text


def _ensure_suricata_nfq_rules():
    input_text = _get_chain_rules("input")
    if "iifname \"eth0\" ct state new tcp dport" not in input_text or "queue" not in input_text:
        _nft_rule('insert rule inet filter input position 37 iifname "eth0" ct state new tcp dport { 22, 80, 222, 443, 3000, 3331, 5194 } queue num 0 bypass')
    forward_text = _get_chain_rules("forward")
    if "ct state new ct status dnat queue" not in forward_text:
        _nft_rule("insert rule inet filter forward position 84 ct state new ct status dnat queue num 0 bypass")


def _remove_suricata_nfq_rules():
    for chain, keyword in (("input", "queue"), ("forward", "ct status dnat queue")):
        while True:
            chain_text = _get_chain_rules(chain)
            h = _find_handle_by_keyword(chain_text, keyword)
            if h is None:
                break
            _nft(["delete", "rule", "inet", "filter", chain, "handle", str(h)])


def _enable_crowdsec():
    _svc("start", "crowdsec-firewall-bouncer")
    _svc("enable", "crowdsec-firewall-bouncer")


def _disable_crowdsec():
    _svc("stop", "crowdsec-firewall-bouncer")
    _svc("disable", "crowdsec-firewall-bouncer")


APPLY_ENABLE = {
    "nft_accept": lambda rid: _enable_nft_accept(rid),
    "nft_rate": lambda rid: _enable_nft_accept(rid),
    "nft_chain": lambda rid: _enable_nft_chain(rid),
    "auto_ban": lambda rid: _enable_auto_ban(),
    "suricata": lambda rid: _enable_suricata(),
    "service": lambda rid: _enable_crowdsec(),
}

APPLY_DISABLE = {
    "nft_accept": lambda rid: _disable_nft_accept(rid),
    "nft_rate": lambda rid: _disable_nft_accept(rid),
    "nft_chain": lambda rid: _disable_nft_chain(rid),
    "auto_ban": lambda rid: _disable_auto_ban(),
    "suricata": lambda rid: _disable_suricata(),
    "service": lambda rid: _disable_crowdsec(),
}


def get_rules_state():
    state = {}
    try:
        with open(RULES_STATE_FILE, "r") as f:
            state = json.load(f)
    except Exception:
        pass
    result = {}
    for rule_id, rule_def in RULES.items():
        if rule_id in state and isinstance(state[rule_id], dict):
            result[rule_id] = {**rule_def, **state[rule_id]}
        else:
            result[rule_id] = rule_def.copy()
            result[rule_id]["enabled"] = rule_def.get("enabled", True)
    return result


def _detect_real_status(rule_id, rule_def):
    rtype = rule_def.get("type")
    if rtype == "service":
        return _is_service_active("crowdsec-firewall-bouncer")
    if rtype == "auto_ban":
        lines = _get_cron_lines()
        return any("auto-ban.py" in l and not l.strip().startswith("#") for l in lines)
    if rtype == "suricata":
        return os.path.exists("/etc/suricata/rules/local-bridge.rules") and _has_suricata_nfq_rules()
    if rtype in ("nft_accept", "nft_rate"):
        keywords = _build_detect_keywords(rule_id, rule_def.get("params"))
        chain_text = _get_input_chain_rules()
        for kw in keywords:
            if kw in chain_text:
                return True
        return False
    if rtype == "nft_chain":
        info = NFT_CHAIN_JUMPS.get(rule_id)
        if info:
            parent = info["parent"]
            parent_text = _get_chain_rules(parent)
            return info["chain"] in parent_text
        sub = NFT_SUBCHAIN_RULES.get(rule_id)
        if sub:
            parent = sub["parent"]
            parent_text = _get_chain_rules(parent)
            for kw in sub["detect_keywords"]:
                if kw in parent_text:
                    return True
            return False
        return rule_def.get("enabled", True)
    return rule_def.get("enabled", True)


def get_rules_state_with_status():
    state = get_rules_state()
    for rid, rdef in state.items():
        rdef["real_status"] = _detect_real_status(rid, rdef)
    return state


def toggle_rule(rule_id, enabled):
    if rule_id not in RULES:
        return False, f"Unknown rule: {rule_id}"
    state = get_rules_state()
    state[rule_id]["enabled"] = enabled
    _save_state(state)

    rule_type = RULES[rule_id]["type"]
    try:
        if enabled:
            fn = APPLY_ENABLE.get(rule_type)
            if fn:
                fn(rule_id)
        else:
            fn = APPLY_DISABLE.get(rule_type)
            if fn:
                fn(rule_id)
    except Exception as e:
        log.error("Failed to %s rule %s: %s", "enable" if enabled else "disable", rule_id, e)
        return False, f"Failed to {'enable' if enabled else 'disable'} {rule_id}: {e}"

    return True, f"Rule {rule_id} {'enabled' if enabled else 'disabled'}"


def apply_all_enabled():
    state = get_rules_state()
    for rule_id, rule_def in RULES.items():
        is_enabled = state.get(rule_id, {}).get("enabled", True)
        if is_enabled:
            rule_type = rule_def["type"]
            fn = APPLY_ENABLE.get(rule_type)
            if fn:
                try:
                    fn(rule_id)
                    log.info("Applied enabled rule: %s", rule_id)
                except Exception as e:
                    log.error("Failed to apply rule %s: %s", rule_id, e)


def restore_all_rules():
    apply_all_enabled()


def _save_state(state):
    os.makedirs(os.path.dirname(RULES_STATE_FILE), exist_ok=True)
    with open(RULES_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_rules_categories():
    cats = {}
    for rule_id, rule in get_rules_state().items():
        cat = rule.get("category", "other")
        if cat not in cats:
            cats[cat] = []
        cats[cat].append({**rule, "id": rule_id})
    return cats


def get_rules_categories_with_status():
    cats = {}
    state = get_rules_state_with_status()
    for rule_id, rule in state.items():
        cat = rule.get("category", "other")
        if cat not in cats:
            cats[cat] = []
        cats[cat].append({**rule, "id": rule_id})
    return cats


def update_rule_params(rule_id, params):
    if rule_id not in RULES:
        return False, f"Unknown rule: {rule_id}"
    if not RULES[rule_id].get("editable"):
        return False, f"Rule {rule_id} is not editable"
    schema = RULES[rule_id].get("param_schema", [])
    allowed_keys = {s["key"] for s in schema}
    parsed = {}
    for key, value in params.items():
        if key not in allowed_keys:
            continue
        schema_item = next((s for s in schema if s["key"] == key), None)
        if not schema_item:
            continue
        if schema_item["type"] == "number":
            try:
                parsed[key] = int(value)
            except (ValueError, TypeError):
                parsed[key] = value
        elif schema_item["type"] == "text" and key == "ports":
            parsed[key] = [int(p.strip()) for p in str(value).split(",") if p.strip()]
        else:
            parsed[key] = value
    state = get_rules_state()
    if rule_id not in state:
        state[rule_id] = RULES[rule_id].copy()
    state[rule_id]["params"] = parsed
    state[rule_id]["description"] = _build_description(rule_id, parsed)
    _save_state(state)
    if state[rule_id].get("enabled", True):
        try:
            fn_disable = APPLY_DISABLE.get(RULES[rule_id]["type"])
            if fn_disable:
                fn_disable(rule_id)
            fn_enable = APPLY_ENABLE.get(RULES[rule_id]["type"])
            if fn_enable:
                fn_enable(rule_id)
        except Exception as e:
            log.error("Failed to reapply rule %s: %s", rule_id, e)
    return True, f"Rule {rule_id} updated"


def _build_description(rule_id, params):
    if rule_id == "ssh_bruteforce":
        port = params.get("port", 22)
        rate = params.get("rate", "3/minute")
        burst = params.get("burst", 5)
        return f"SSH rate limiting port {port} ({rate} burst {burst})"
    elif rule_id == "http_https":
        ports = params.get("ports", [80, 443])
        if isinstance(ports, str):
            ports = [int(p.strip()) for p in ports.split(",") if p.strip()]
        port_str = ", ".join(str(p) for p in ports)
        return f"TCP inbound ports {port_str}"
    elif rule_id == "dns":
        port = params.get("port", 53)
        rate = params.get("rate", "50/second")
        burst = params.get("burst", 5)
        return f"DNS UDP port {port} (rate {rate} burst {burst})"
    elif rule_id == "ssh_alt":
        port = params.get("port", 222)
        return f"SSH alt port {port}"
    elif rule_id == "scan_ban":
        threshold = params.get("threshold", 8)
        ban_time = params.get("ban_time", 3600)
        return f"Port scan auto-ban ({threshold}+ ports, {ban_time}s ban)"
    return RULES.get(rule_id, {}).get("description", rule_id)
