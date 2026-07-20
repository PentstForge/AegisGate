#!/usr/bin/env python3
"""AegisGate IP Blocklists — download, parse, and apply IP/CIDR blocklists via nft sets.

Design principles:
  - Streaming: never load all IPs into Python memory at once.
  - Per-list nft set files written to disk, then applied with `nft -f`.
  - DB stores list metadata + custom entries only (preset list IPs live in files).
  - nft sets: ipbl_ipv4 / ipbl_ipv6 with interval+auto-merge flags.
  - Full CRUD: add/edit/delete lists, enable/disable, manual add/remove IPs.
"""
import gzip
import ipaddress
import json
import os
import re
import subprocess
import time
import urllib.request

NFT = "/usr/sbin/nft"
DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(DASHBOARD_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "ip_blocklists.db")
LISTS_DIR = os.path.join(DATA_DIR, "ip-blocklists")
NFT_DIR = os.path.join(DATA_DIR, "ip-blocklists-nft")

PRESET_LISTS = [
    {
        "name": "Firehol Level 1",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset",
        "fmt": "netset",
        "category": "firehol,malware,c&c,spam",
        "description": "Firehol Level 1 — most aggressive threats (botnets, C&C, spam) ~4K entries",
    },
    {
        "name": "Spamhaus DROP",
        "url": "https://www.spamhaus.org/drop/drop.txt",
        "fmt": "spamhaus",
        "category": "spamhaus,spam,hijacked",
        "description": "Spamhaus DROP — known hijacked/spam IP ranges ~1.6K entries",
    },
    {
        "name": "Firehol Level 2",
        "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level2.netset",
        "fmt": "netset",
        "category": "firehol,attacks,spam",
        "description": "Firehol Level 2 — attacks and spam ~15K entries",
    },
    {
        "name": "blocklist.net.ua",
        "url": "https://blocklist.net.ua/blocklist.csv",
        "fmt": "netua_csv",
        "category": "ukraine,ddos,flood,abuse",
        "description": "blocklist.net.ua — Ukrainian DDoS/HTTP flood blocklist ~93K entries",
    },
]

DB_CREATE = """
CREATE TABLE IF NOT EXISTS ip_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    url TEXT,
    fmt TEXT DEFAULT 'netset',
    category TEXT,
    description TEXT,
    enabled INTEGER DEFAULT 1,
    entry_count INTEGER DEFAULT 0,
    last_updated INTEGER,
    last_error TEXT,
    etag TEXT,
    sha1 TEXT,
    is_preset INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS ip_custom_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_or_cidr TEXT NOT NULL,
    comment TEXT DEFAULT '',
    added_by TEXT DEFAULT 'admin',
    created_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ip_custom_entry ON ip_custom_entries(ip_or_cidr);

CREATE TABLE IF NOT EXISTS ip_blocklist_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = __import__("sqlite3").connect(DB_PATH)
    conn.row_factory = __import__("sqlite3").Row
    conn.executescript(DB_CREATE)
    return conn


def init_db():
    conn = get_db()
    try:
        count = conn.execute("SELECT COUNT(*) FROM ip_lists WHERE is_preset=1").fetchone()[0]
        if count == 0:
            for preset in PRESET_LISTS:
                conn.execute(
                    "INSERT OR IGNORE INTO ip_lists (name, url, fmt, category, description, enabled, is_preset, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?)",
                    (preset["name"], preset["url"], preset["fmt"], preset["category"],
                     preset["description"], int(time.time()), int(time.time())),
                )
            conn.commit()
    finally:
        conn.close()


def get_lists():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM ip_lists ORDER BY id ASC").fetchall()]
    finally:
        conn.close()


def get_list_by_id(list_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM ip_lists WHERE id=?", (list_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def add_list(name, url=None, fmt="netset", category=None, description=None, enabled=1):
    now = int(time.time())
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO ip_lists (name, url, fmt, category, description, enabled, is_preset, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (name, url, fmt, category, description, enabled, now, now),
        )
        conn.commit()
        return True, cur.lastrowid
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
            if key in ("name", "url", "fmt", "category", "description", "enabled"):
                sets.append(f"{key}=?")
                vals.append(value)
        if not sets:
            return False, "No fields to update"
        sets.append("updated_at=?")
        vals.append(int(time.time()))
        vals.append(list_id)
        conn.execute(f"UPDATE ip_lists SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        return True, f"List {list_id} updated"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_list(list_id):
    conn = get_db()
    try:
        lst = get_list_by_id(list_id)
        if lst and lst.get("url"):
            for ext in (".nft", ".txt"):
                path = os.path.join(LISTS_DIR, f"list_{list_id}{ext}")
                if os.path.exists(path):
                    os.remove(path)
        conn.execute("DELETE FROM ip_lists WHERE id=?", (list_id,))
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
            row = conn.execute("SELECT enabled FROM ip_lists WHERE id=?", (list_id,)).fetchone()
            if not row:
                return False, "List not found"
            enabled = 0 if row["enabled"] else 1
        conn.execute("UPDATE ip_lists SET enabled=?, updated_at=? WHERE id=?", (enabled, int(time.time()), list_id))
        conn.commit()
        return True, f"List {'enabled' if enabled else 'disabled'}"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def _parse_ips_from_content(content, fmt):
    ips = set()
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";") or line.startswith("//"):
            continue
        if fmt == "spamhaus":
            parts = line.split(";")
            addr = parts[0].strip()
        elif fmt == "netua_csv":
            if line.startswith("IP;"):
                continue
            parts = line.split(";")
            addr = parts[0].strip()
        else:
            addr = line.split()[0].strip()
        try:
            if "/" in addr:
                net = ipaddress.ip_network(addr, strict=False)
                ips.add(str(net))
            else:
                ipaddress.ip_address(addr)
                ips.add(addr)
        except ValueError:
            continue
    return ips


def _fetch_url(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "AegisGate/1.0", "Accept-Encoding": "gzip"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip" or url.endswith(".gz"):
            try:
                data = gzip.decompress(data)
            except Exception:
                pass
        content = data.decode("utf-8", errors="replace")
        etag = resp.headers.get("ETag")
        return content, etag, None
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, None, "not_modified"
        return None, None, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return None, None, str(e)[:200]


def _write_nft_file(list_id, ips, is_ipv6=False):
    family = "ip6" if is_ipv6 else "ip"
    set_name = "ipbl_ipv6" if is_ipv6 else "ipbl_ipv4"
    os.makedirs(NFT_DIR, exist_ok=True)
    path = os.path.join(NFT_DIR, f"list_{list_id}_{'v6' if is_ipv6 else 'v4'}.nft")
    with open(path, "w") as f:
        f.write(f"# AegisGate IP blocklist: list_id={list_id}\n")
        if ips:
            elements = ", ".join(ips)
            f.write(f"add element inet filter {set_name} {{ {elements} }}\n")
    return path


def _apply_nft_file(path):
    try:
        r = subprocess.run([NFT, "-f", path], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return False, r.stderr[:500]
        return True, None
    except Exception as e:
        return False, str(e)[:200]


def _remove_nft_file(list_id, is_ipv6=False):
    set_name = "ipbl_ipv6" if is_ipv6 else "ipbl_ipv4"
    path = os.path.join(NFT_DIR, f"list_{list_id}_{'v6' if is_ipv6 else 'v4'}.nft")
    nft_path = os.path.join(NFT_DIR, f"list_{list_id}_{'v6' if is_ipv6 else 'v4'}.nft")
    empty_path = os.path.join(NFT_DIR, f"empty_{list_id}_{'v6' if is_ipv6 else 'v4'}.nft")
    with open(empty_path, "w") as f:
        f.write(f"# empty flush for list {list_id}\n")
    result = _apply_nft_file(empty_path)
    if os.path.exists(nft_path):
        os.remove(nft_path)
    if os.path.exists(empty_path):
        os.remove(empty_path)
    return result


def download_list(list_id):
    lst = get_list_by_id(list_id)
    if not lst:
        return False, "List not found"
    url = lst.get("url")
    if not url:
        return False, "No URL for list"

    content, etag, error = _fetch_url(url)
    if error == "not_modified":
        return True, "List not modified"
    if error:
        conn = get_db()
        try:
            conn.execute("UPDATE ip_lists SET last_error=?, updated_at=? WHERE id=?", (error, int(time.time()), list_id))
            conn.commit()
        finally:
            conn.close()
        return False, error
    if content is None:
        return True, "Not modified"

    import hashlib
    sha1 = hashlib.sha1(content.encode()).hexdigest()

    os.makedirs(LISTS_DIR, exist_ok=True)
    raw_path = os.path.join(LISTS_DIR, f"list_{list_id}.txt")
    with open(raw_path, "w") as f:
        f.write(content)

    ips = _parse_ips_from_content(content, lst.get("fmt", "netset"))
    _rfc1918_v4 = {ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
                   ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("127.0.0.0/8")}
    _rfc1918_v6 = {ipaddress.ip_network("::1/128"), ipaddress.ip_network("fc00::/7")}
    ipv4 = set()
    ipv6 = set()
    for addr in ips:
        try:
            if "/" in addr:
                net = ipaddress.ip_network(addr, strict=False)
                if net.version == 4:
                    if any(net.overlaps(r) for r in _rfc1918_v4):
                        continue
                    ipv4.add(str(net))
                else:
                    if any(net.overlaps(r) for r in _rfc1918_v6):
                        continue
                    ipv6.add(str(net))
            else:
                ip = ipaddress.ip_address(addr)
                if ip.version == 4:
                    if any(ip in r for r in _rfc1918_v4):
                        continue
                    ipv4.add(str(ip))
                else:
                    if any(ip in r for r in _rfc1918_v6):
                        continue
                    ipv6.add(str(ip))
        except ValueError:
            continue

    os.makedirs(NFT_DIR, exist_ok=True)
    ensure_nft_sets()

    flush_list_from_nft(list_id)

    count = 0
    for is_v6, ip_set in [(False, ipv4), (True, ipv6)]:
        if not ip_set:
            continue
        batch_size = 500
        ip_list = sorted(ip_set)
        for i in range(0, len(ip_list), batch_size):
            batch = ip_list[i:i + batch_size]
            suffix = "v6" if is_v6 else "v4"
            file_id = f"{list_id}_{i//batch_size}_{suffix}" if i > 0 else f"{list_id}_{suffix}"
            path = _write_nft_file(file_id, batch, is_v6)
            ok, err = _apply_nft_file(path)
            if ok:
                count += len(batch)

    conn = get_db()
    try:
        conn.execute(
            "UPDATE ip_lists SET entry_count=?, last_updated=?, last_error=NULL, etag=?, sha1=?, updated_at=? WHERE id=?",
            (len(ips), int(time.time()), etag, sha1, int(time.time()), list_id),
        )
        conn.commit()
    finally:
        conn.close()

    return True, f"Downloaded: {len(ipv4)} IPv4 + {len(ipv6)} IPv6 entries"


def download_all_lists():
    lists = get_lists()
    results = []
    for lst in lists:
        if not lst.get("enabled") or not lst.get("url"):
            continue
        ok, msg = download_list(lst["id"])
        results.append({"id": lst["id"], "name": lst["name"], "ok": ok, "msg": msg})
    return results


def get_custom_entries():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM ip_custom_entries ORDER BY id ASC").fetchall()]
    finally:
        conn.close()


def add_custom_entry(ip_or_cidr, comment=""):
    try:
        if "/" in ip_or_cidr:
            ipaddress.ip_network(ip_or_cidr, strict=False)
        else:
            ipaddress.ip_address(ip_or_cidr)
    except ValueError:
        return False, f"Invalid IP/CIDR: {ip_or_cidr}"

    conn = get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO ip_custom_entries (ip_or_cidr, comment, added_by, created_at) VALUES (?, ?, 'admin', ?)",
            (ip_or_cidr, comment, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()

    set_name = "ipbl_ipv6" if ":" in ip_or_cidr else "ipbl_ipv4"
    try:
        subprocess.run([NFT, "add", "element", "inet", "filter", set_name, f"{{ {ip_or_cidr} }}"],
                       capture_output=True, text=True, timeout=10)
    except Exception:
        pass
    return True, f"Added {ip_or_cidr}"


def remove_custom_entry(ip_or_cidr):
    conn = get_db()
    try:
        before = conn.execute("SELECT COUNT(*) FROM ip_custom_entries WHERE ip_or_cidr=?", (ip_or_cidr,)).fetchone()[0]
        if not before:
            return False, f"{ip_or_cidr} not found"
        conn.execute("DELETE FROM ip_custom_entries WHERE ip_or_cidr=?", (ip_or_cidr,))
        conn.commit()
    finally:
        conn.close()

    set_name = "ipbl_ipv6" if ":" in ip_or_cidr else "ipbl_ipv4"
    try:
        subprocess.run([NFT, "delete", "element", "inet", "filter", set_name, f"{{ {ip_or_cidr} }}"],
                       capture_output=True, text=True, timeout=10)
    except Exception:
        pass
    return True, f"Removed {ip_or_cidr}"


def ensure_nft_sets():
    for set_name in ("ipbl_ipv4", "ipbl_ipv6"):
        try:
            subprocess.run([NFT, "add", "set", "inet", "filter", set_name,
                           "{ type ipv4_addr ; flags interval, timeout ; auto-merge ; }" if set_name == "ipbl_ipv4"
                           else "{ type ipv6_addr ; flags interval, timeout ; auto-merge ; }"],
                          capture_output=True, text=True, timeout=10)
        except Exception:
            pass


def restore_ipbl():
    ensure_nft_sets()
    errors = []
    for set_name in ("ipbl_ipv4", "ipbl_ipv6"):
        result = subprocess.run(
            [NFT, "list", "set", "inet", "filter", set_name],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            errors.append(f"{set_name}: {result.stderr.strip()}")
    if not os.path.isdir(NFT_DIR):
        if errors:
            raise RuntimeError("; ".join(errors))
        return 0
    nft_files = sorted(f for f in os.listdir(NFT_DIR) if f.endswith(".nft"))
    count = 0
    for fname in nft_files:
        path = os.path.join(NFT_DIR, fname)
        try:
            r = subprocess.run([NFT, "-f", path], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                count += 1
            else:
                errors.append(f"{fname}: {r.stderr.strip()}")
        except Exception as exc:
            errors.append(f"{fname}: {exc}")
    entries = get_custom_entries()
    for e in entries:
        set_name = "ipbl_ipv6" if ":" in e["ip_or_cidr"] else "ipbl_ipv4"
        try:
            result = subprocess.run([NFT, "add", "element", "inet", "filter", set_name, f"{{ {e['ip_or_cidr']} }}"],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode != 0 and "File exists" not in result.stderr:
                errors.append(f"{e['ip_or_cidr']}: {result.stderr.strip()}")
        except Exception as exc:
            errors.append(f"{e['ip_or_cidr']}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))
    return count


def get_nft_set_stats():
    from .nft_utils import nft_set_count
    stats = {
        "blacklist_ipv4_count": nft_set_count("filter", "blacklist_ipv4"),
        "crowdsec_count": nft_set_count("filter", "crowdsec-blacklists"),
    }
    for set_name in ("ipbl_ipv4", "ipbl_ipv6"):
        try:
            r = subprocess.run([NFT, "-j", "list", "set", "inet", "filter", set_name],
                              capture_output=True, text=True, timeout=60)
            count = 0
            if r.returncode == 0 and r.stdout:
                data = json.loads(r.stdout)
                for item in data.get("nftables", []):
                    s = item.get("set", {})
                    if s.get("name") == set_name and "elem" in s:
                        elems = s["elem"]
                        count = len(elems) if isinstance(elems, list) else 0
                        break
            stats[f"{set_name}_count"] = count
        except Exception:
            stats[f"{set_name}_count"] = 0
    return stats


def _find_anchor_handle(chain):
    try:
        r = subprocess.run([NFT, "-a", "list", "chain", "inet", "filter", chain],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None
        for line in r.stdout.splitlines():
            if "lan_trusted accept" in line and "daddr" not in line:
                m = re.search(r"handle\s+(\d+)", line)
                if m:
                    return m.group(1)
        for line in r.stdout.splitlines():
            if "udp sport 68 udp dport 67 accept" in line:
                m = re.search(r"handle\s+(\d+)", line)
                if m:
                    return m.group(1)
        for line in r.stdout.splitlines():
            if "ct state established,related accept" in line:
                m = re.search(r"handle\s+(\d+)", line)
                if m:
                    return m.group(1)
        for line in r.stdout.splitlines():
            if "ct state established accept" in line:
                m = re.search(r"handle\s+(\d+)", line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None


def ensure_nft_rules():
    ensure_nft_sets()
    for chain in ("input", "forward"):
        try:
            r = subprocess.run([NFT, "-j", "list", "chain", "inet", "filter", chain],
                               capture_output=True, text=True, timeout=10)
            stdout = r.stdout or ""
            directions = ("saddr", "daddr") if chain == "forward" else ("saddr",)
            for proto, set_name in [("ip", "ipbl_ipv4"), ("ip6", "ipbl_ipv6")]:
                for direction in directions:
                    rule_tag = f"DROP_IPBL_{direction.upper()}_{chain.upper()}:"
                    if rule_tag in stdout:
                        continue
                    prefix = f"DROP_IPBL_{direction.upper()}_{chain.upper()}: "
                    anchor_line = _find_anchor_handle(chain)
                    if anchor_line:
                        subprocess.run([NFT, "insert", "rule", "inet", "filter", chain,
                                        "handle", anchor_line,
                                        proto, direction, f"@{set_name}", "ct", "state", "new",
                                        "log", "prefix", f'"{prefix}"', "drop"],
                                       capture_output=True, text=True, timeout=10)
                    else:
                        subprocess.run([NFT, "add", "rule", "inet", "filter", chain,
                                        proto, direction, f"@{set_name}", "ct", "state", "new",
                                        "log", "prefix", f'"{prefix}"', "drop"],
                                       capture_output=True, text=True, timeout=10)
        except Exception:
            pass


def apply_all_enabled():
    ensure_nft_sets()
    ensure_nft_rules()
    lists = get_lists()
    total = 0
    for lst in lists:
        if lst.get("enabled") and lst.get("url"):
            ok, msg = download_list(lst["id"])
            if ok:
                try:
                    count = int(msg.split("+")[0].split(": ")[1].strip().split()[0])
                    total += count
                except Exception:
                    pass
    entries = get_custom_entries()
    for e in entries:
        set_name = "ipbl_ipv6" if ":" in e["ip_or_cidr"] else "ipbl_ipv4"
        try:
            subprocess.run([NFT, "add", "element", "inet", "filter", set_name, f"{{ {e['ip_or_cidr']} }}"],
                           capture_output=True, text=True, timeout=10)
        except Exception:
            pass
    return total


def flush_list_from_nft(list_id):
    for is_v6 in (False, True):
        suffix = "v6" if is_v6 else "v4"
        set_name = "ipbl_ipv6" if is_v6 else "ipbl_ipv4"
        for i in range(200):
            path = os.path.join(NFT_DIR, f"list_{list_id}_{i}_{suffix}.nft")
            if os.path.exists(path):
                os.remove(path)
        path = os.path.join(NFT_DIR, f"list_{list_id}_{suffix}.nft")
        if os.path.exists(path):
            os.remove(path)
    return True, "Flushed"


def get_settings():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM ip_blocklist_settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def update_setting(key, value):
    conn = get_db()
    try:
        conn.execute("INSERT OR REPLACE INTO ip_blocklist_settings (key, value, updated_at) VALUES (?, ?, ?)",
                     (key, str(value), int(time.time())))
        conn.commit()
        return True, f"Setting {key} updated"
    finally:
        conn.close()


init_db()
