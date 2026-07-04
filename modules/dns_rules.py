#!/usr/bin/env python3
import json
import re
import time
from .dns_db import get_db

WILDCARD_RE = re.compile(r'^\*\.')


def parse_adblock_rule(line):
    line = line.strip()
    if not line or line.startswith(("!", "#", ";")):
        return None

    is_allow = line.startswith("@@")
    if is_allow:
        line = line[2:]

    modifiers = {}
    modifier_str = ""
    if "$" in line:
        line, modifier_str = line.rsplit("$", 1)
        for mod in modifier_str.split(","):
            mod = mod.strip()
            if "=" in mod:
                k, v = mod.split("=", 1)
                modifiers[k.lower()] = v
            else:
                modifiers[mod.lower()] = True

    if "badfilter" in modifiers:
        return {"type": "badfilter", "value": line.strip(), "action": "badfilter",
                "modifiers": modifiers, "raw_rule": line.strip()}

    important = "important" in modifiers
    action = "allow" if is_allow else "block"
    if important:
        action = "important_allow" if is_allow else "important_block"

    rtype = "exact"
    value = line.strip()

    if value.startswith("||") and value.endswith("^"):
        value = value[2:-1]
        rtype = "adblock_domain"
    elif value.startswith("||"):
        value = value[2:]
        if value.endswith("^"):
            value = value[:-1]
        rtype = "adblock_domain"
    elif value.startswith("/") and value.endswith("/"):
        value = value[1:-1]
        rtype = "regex"
    elif "*" in value:
        rtype = "wildcard"
        value = value.replace(".", r"\.").replace("*", ".*")
    elif value.startswith("|"):
        value = value.lstrip("|")
        rtype = "adblock_anchor"
    else:
        rtype = "exact"

    if not value:
        return None

    dnstype = modifiers.get("dnstype")
    client = modifiers.get("client")
    dnsrewrite = modifiers.get("dnsrewrite")
    denyallow = modifiers.get("denyallow")
    ctag = modifiers.get("ctag")

    if dnsrewrite:
        action = "rewrite"
    elif action == "block" and not is_allow:
        pass

    return {
        "type": rtype,
        "value": value,
        "action": action,
        "modifiers": modifiers,
        "important": important,
        "dnstype": dnstype,
        "client": client,
        "dnsrewrite": dnsrewrite,
        "denyallow": denyallow,
        "ctag": ctag,
        "raw_rule": line,
    }


def parse_hosts_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split()
    if len(parts) < 2:
        return None
    ip = parts[0]
    domain = parts[1]
    if ip in ("0.0.0.0", "127.0.0.1", "::", "::1", "0.0.0.0"):
        return {"type": "hosts", "value": domain, "action": "block", "ip": ip}
    return {"type": "hosts", "value": domain, "action": "rewrite", "ip": ip}


def parse_rule(text):
    result = parse_hosts_line(text)
    if result is not None:
        return {"ok": True, **result}
    result = parse_adblock_rule(text)
    if result is not None:
        return {"ok": True, **result}
    return {"ok": False, "error": "Unrecognized rule format"}


def add_rule(rule_type, value, action="block", category=None, comment=None,
             source="manual", priority=100, modifiers_json=None, raw_rule=None):
    now = int(time.time())
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO dns_rules (type, value, action, category, comment, enabled, source, priority, modifiers_json, raw_rule, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
            (rule_type, value, action, category, comment, source, priority,
             modifiers_json, raw_rule, now, now),
        )
        conn.commit()
        return True, cur.lastrowid
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_rule(rule_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dns_rules WHERE id=?", (rule_id,))
        conn.commit()
        return True, f"Rule {rule_id} deleted"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def toggle_rule(rule_id, enabled=None):
    conn = get_db()
    try:
        if enabled is None:
            row = conn.execute("SELECT enabled FROM dns_rules WHERE id=?", (rule_id,)).fetchone()
            if not row:
                return False, "Rule not found"
            enabled = 0 if row["enabled"] else 1
        conn.execute("UPDATE dns_rules SET enabled=?, updated_at=? WHERE id=?", (enabled, int(time.time()), rule_id))
        conn.commit()
        return True, f"Rule {rule_id} {'enabled' if enabled else 'disabled'}"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def _rules_where(rule_type=None, action=None, source=None, enabled_only=False, search=None):
    query = " WHERE 1=1"
    params = []
    if rule_type:
        query += " AND type=?"
        params.append(rule_type)
    if action:
        query += " AND action=?"
        params.append(action)
    if source:
        query += " AND source=?"
        params.append(source)
    if enabled_only:
        query += " AND enabled=1"
    if search:
        like = f"%{search.strip()}%"
        query += " AND (value LIKE ? OR category LIKE ? OR comment LIKE ? OR source LIKE ?)"
        params.extend([like, like, like, like])
    return query, params


def count_rules(rule_type=None, action=None, source=None, enabled_only=False, search=None):
    conn = get_db()
    try:
        where, params = _rules_where(rule_type, action, source, enabled_only, search)
        return conn.execute("SELECT COUNT(*) as c FROM dns_rules" + where, params).fetchone()["c"]
    finally:
        conn.close()


def get_rules(rule_type=None, action=None, source=None, enabled_only=False, search=None, limit=None, offset=0):
    conn = get_db()
    try:
        where, params = _rules_where(rule_type, action, source, enabled_only, search)
        query = "SELECT * FROM dns_rules" + where
        query += " ORDER BY priority ASC, id ASC"
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset or 0)])
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def get_rules_for_config():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, type, value, action, category, enabled, modifiers_json FROM dns_rules WHERE enabled=1 ORDER BY priority ASC, id ASC"
        ).fetchall()
        result = []
        for r in rows:
            rule = dict(r)
            if rule.get("modifiers_json"):
                try:
                    import json
                    rule["modifiers"] = json.loads(rule["modifiers_json"])
                except Exception:
                    rule["modifiers"] = {}
            else:
                rule["modifiers"] = {}
            result.append(rule)
        return result
    finally:
        conn.close()


def check_host(domain):
    domain = domain.strip().lower()
    conn = get_db()
    try:
        exact_rows = conn.execute(
            "SELECT * FROM dns_rules WHERE value=? AND enabled=1 ORDER BY priority ASC, id ASC",
            (domain,),
        ).fetchall()
        if exact_rows:
            return [dict(r) for r in exact_rows]

        parts = domain.split(".")
        wildcard_matches = []
        for i in range(len(parts)):
            wildcard = ".".join(["*"] + parts[i + 1:]) if i < len(parts) - 1 else None
            if wildcard:
                wrows = conn.execute(
                    "SELECT * FROM dns_rules WHERE value=? AND enabled=1",
                    (wildcard,),
                ).fetchall()
                wildcard_matches.extend([dict(r) for r in wrows])

        suffix_matches = []
        for i in range(1, len(parts)):
            suffix = ".".join(parts[i:])
            srows = conn.execute(
                "SELECT * FROM dns_rules WHERE value=? AND enabled=1 AND type='adblock_domain'",
                (suffix,),
            ).fetchall()
            suffix_matches.extend([dict(r) for r in srows])

        all_matches = wildcard_matches + suffix_matches

        seen = set()
        unique = []
        for m in all_matches:
            if m["id"] not in seen:
                seen.add(m["id"])
                unique.append(m)
        return unique
    finally:
        conn.close()


def import_rules_from_text(text, source="manual", category=None):
    imported = 0
    skipped = 0
    errors = 0
    rows = []
    now = int(time.time())
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "!", ";")):
            skipped += 1
            continue

        if line.startswith("||") or line.startswith("@@") or line.startswith("/") or "$" in line:
            parsed = parse_adblock_rule(line)
            if parsed:
                rows.append((
                    parsed["type"], parsed["value"], parsed.get("action", "block"),
                    category, None, 1, source, 100,
                    json.dumps(parsed.get("modifiers", {})) if parsed.get("modifiers") else None,
                    parsed.get("raw_rule"), now, now,
                ))
                imported += 1
            else:
                skipped += 1
        elif re.match(r'^[0-9a-fA-F.:]+\s+\S+', line):
            parsed = parse_hosts_line(line)
            if parsed:
                rows.append((
                    parsed["type"], parsed["value"], parsed["action"],
                    category, None, 1, source, 100, None, line, now, now,
                ))
                imported += 1
            else:
                skipped += 1
        elif re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$', line):
            rows.append(("exact", line, "block", category, None, 1, source, 100, None, line, now, now))
            imported += 1
        else:
            skipped += 1

    if rows:
        conn = get_db()
        try:
            conn.executemany(
                "INSERT INTO dns_rules (type, value, action, category, comment, enabled, source, priority, modifiers_json, raw_rule, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        except Exception:
            errors += imported
            imported = 0
        finally:
            conn.close()

    return imported, skipped, errors
