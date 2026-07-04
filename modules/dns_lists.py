#!/usr/bin/env python3
import hashlib
import json
import os
import time
import urllib.request

from .dns_db import get_db, LISTS_DIR, add_event


HAGEZI_MAX_LISTS = [
    {
        "name": "HaGeZi Ultimate",
        "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/ultimate.txt",
        "fmt": "domains",
        "category": "hagezi,ads,tracking,malware,phishing,telemetry",
    },
    {
        "name": "HaGeZi TIF",
        "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/tif.txt",
        "fmt": "domains",
        "category": "hagezi,malware,phishing,threat-intel",
    },
    {
        "name": "HaGeZi DoH Bypass",
        "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/doh.txt",
        "fmt": "domains",
        "category": "hagezi,doh,bypass",
    },
    {
        "name": "HaGeZi Native Windows/Office",
        "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/native.winoffice.txt",
        "fmt": "domains",
        "category": "hagezi,native,telemetry,windows,office",
    },
    {
        "name": "HaGeZi Native Samsung",
        "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/native.samsung.txt",
        "fmt": "domains",
        "category": "hagezi,native,telemetry,samsung",
    },
    {
        "name": "HaGeZi Native TikTok Extended",
        "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/domains/native.tiktok.extended.txt",
        "fmt": "domains",
        "category": "hagezi,native,tiktok,tracking",
    },
]


def add_list(name, url=None, fmt="hosts", category=None, enabled=1):
    now = int(time.time())
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO dns_lists (name, url, format, category, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, url, fmt, category, enabled, now, now),
        )
        conn.commit()
        lid = cur.lastrowid
        return True, lid
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def update_list(list_id, **kwargs):
    conn = get_db()
    try:
        sets = []
        vals = []
        for key, value in kwargs.items():
            if key in ("name", "url", "format", "category", "enabled"):
                sets.append(f"{key}=?")
                vals.append(value)
        if not sets:
            return False, "No fields to update"
        sets.append("updated_at=?")
        vals.append(int(time.time()))
        vals.append(list_id)
        conn.execute(f"UPDATE dns_lists SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        return True, f"List {list_id} updated"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_list(list_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dns_rules WHERE source_list_id=?", (list_id,))
        conn.execute("DELETE FROM dns_lists WHERE id=?", (list_id,))
        conn.commit()
        return True, f"List {list_id} deleted"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def toggle_list(list_id, enabled=None):
    conn = get_db()
    try:
        if enabled is None:
            row = conn.execute("SELECT enabled FROM dns_lists WHERE id=?", (list_id,)).fetchone()
            if not row:
                return False, "List not found"
            enabled = 0 if row["enabled"] else 1
        conn.execute("UPDATE dns_lists SET enabled=?, updated_at=? WHERE id=?", (enabled, int(time.time()), list_id))
        conn.commit()
        return True, f"List {'enabled' if enabled else 'disabled'}"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def get_lists():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM dns_lists ORDER BY id ASC").fetchall()]
    finally:
        conn.close()


def get_list_by_id(list_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM dns_lists WHERE id=?", (list_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def download_list(list_id):
    lst = get_list_by_id(list_id)
    if not lst:
        return False, "List not found"
    url = lst.get("url")
    if not url:
        return False, "No URL for list"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AegisDNS/1.0"})
        etag = lst.get("etag")
        sha1 = lst.get("sha1")
        if etag:
            req.add_header("If-None-Match", etag)

        resp = urllib.request.urlopen(req, timeout=30)
        if resp.status == 304:
            return True, "List not modified"

        data = resp.read()
        content = data.decode("utf-8", errors="replace")

        new_sha1 = hashlib.sha1(data).hexdigest()
        new_etag = resp.headers.get("ETag")

        path = os.path.join(LISTS_DIR, f"list_{list_id}.txt")
        with open(path, "w") as f:
            f.write(content)

        from .dns_rules import import_rules_from_text
        fmt = lst.get("format", "hosts")
        category = lst.get("category") or lst.get("name", "unknown")
        conn = get_db()
        try:
            conn.execute("DELETE FROM dns_rules WHERE source=? OR source_list_id=?", (f"list:{list_id}", list_id))
            conn.commit()
        finally:
            conn.close()
        imported, skipped, errors = import_rules_from_text(
            content, source=f"list:{list_id}", category=category
        )

        conn = get_db()
        try:
            conn.execute(
                "UPDATE dns_rules SET source_list_id=? WHERE source=?",
                (list_id, f"list:{list_id}"),
            )
            conn.execute(
                "UPDATE dns_lists SET rule_count=?, last_updated=?, last_error=?, etag=?, sha1=?, updated_at=? WHERE id=?",
                (imported, int(time.time()), None, new_etag, new_sha1, int(time.time()), list_id),
            )
            conn.commit()
        finally:
            conn.close()

        add_event("list_updated", "info", "dns_lists", f"List '{lst['name']}' updated: {imported} rules imported")
        return True, f"Downloaded: {imported} rules imported, {skipped} skipped, {errors} errors"
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return True, "List not modified"
        err_msg = f"HTTP {e.code}: {e.reason}"
        conn = get_db()
        try:
            conn.execute("UPDATE dns_lists SET last_error=?, updated_at=? WHERE id=?",
                         (err_msg, int(time.time()), list_id))
            conn.commit()
        finally:
            conn.close()
        return False, err_msg
    except Exception as e:
        err_msg = str(e)[:200]
        conn = get_db()
        try:
            conn.execute("UPDATE dns_lists SET last_error=?, updated_at=? WHERE id=?",
                         (err_msg, int(time.time()), list_id))
            conn.commit()
        finally:
            conn.close()
        return False, err_msg


def update_all_lists():
    lists = get_lists()
    results = []
    for lst in lists:
        if not lst.get("enabled", 1) or not lst.get("url"):
            continue
        ok, msg = download_list(lst["id"])
        results.append({"id": lst["id"], "name": lst["name"], "ok": ok, "msg": msg})
    return results


def ensure_hagezi_max_lists(enabled=1):
    conn = get_db()
    created = 0
    existing = set()
    try:
        rows = conn.execute("SELECT url FROM dns_lists WHERE url IS NOT NULL").fetchall()
        existing = {r["url"] for r in rows}
    finally:
        conn.close()

    results = []
    for preset in HAGEZI_MAX_LISTS:
        if preset["url"] in existing:
            results.append({"name": preset["name"], "ok": True, "msg": "Already installed"})
            continue
        ok, result = add_list(
            preset["name"],
            url=preset["url"],
            fmt=preset["fmt"],
            category=preset["category"],
            enabled=enabled,
        )
        if ok:
            created += 1
        results.append({"name": preset["name"], "ok": ok, "msg": result})
    return {"created": created, "results": results}


def purge_list_rules(list_id):
    conn = get_db()
    try:
        deleted = conn.execute("DELETE FROM dns_rules WHERE source_list_id=?", (list_id,)).rowcount
        conn.commit()
        return True, f"Purged {deleted} rules"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()
