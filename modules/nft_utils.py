import json
import subprocess
import ipaddress as _ip
from datetime import timedelta
from time import time

NFT = "/usr/sbin/nft"
CSCLI = "/usr/bin/cscli"

_nft_set_cache = {}
_nft_set_cache_ttl = 60


def _parse_set_elements(stdout):
    data = json.loads(stdout)
    elems = []
    for item in data.get("nftables", []):
        s = item.get("set", {})
        for e in s.get("elem", []):
            elem_data = e.get("elem", e) if isinstance(e, dict) else e
            if isinstance(elem_data, dict):
                val = elem_data.get("val", elem_data)
                if isinstance(val, dict):
                    if "prefix" in val:
                        prefix = val["prefix"]
                        ip_str = prefix["addr"]
                        prefix_len = prefix.get("len", 32)
                    elif "range" in val:
                        rng = val["range"]
                        start_ip = _ip.ip_address(rng[0])
                        end_ip = _ip.ip_address(rng[1])
                        ip_str = None
                        prefix_len = None
                        cur = start_ip
                        while cur <= end_ip:
                            t = e.get("elem", {}).get("timeout", e.get("timeout", ""))
                            if isinstance(t, int):
                                t = str(timedelta(seconds=t))
                            er = e.get("elem", {}).get("expires", e.get("expires", 0))
                            if not isinstance(er, int):
                                er = 0
                            elems.append({"ip": str(cur), "timeout": str(t) if t else "", "expires": str(timedelta(seconds=er)) if er else "", "_expires_s": er})
                            cur = cur + 1
                        continue
                    else:
                        ip_str = str(val)
                        prefix_len = 32
                else:
                    ip_str = str(val)
                    prefix_len = 32
                if "/" in ip_str:
                    parts = ip_str.split("/")
                    ip_str = parts[0]
                    if prefix_len == 32:
                        prefix_len = int(parts[1])
                else:
                    if isinstance(val, dict) and "prefix" not in val and "range" not in val:
                        ip_str = str(val)
            else:
                ip_str = str(elem_data)
                prefix_len = 32
            if ip_str is None:
                continue
            timeout = e.get("elem", {}).get("timeout", e.get("timeout", ""))
            if isinstance(timeout, int):
                timeout = str(timedelta(seconds=timeout))
            expires_raw = e.get("elem", {}).get("expires", e.get("expires", 0))
            if not isinstance(expires_raw, int):
                expires_raw = 0
            expires = str(timedelta(seconds=expires_raw)) if expires_raw else ""
            if prefix_len is not None and prefix_len >= 24 and prefix_len < 32:
                try:
                    for addr in _ip.ip_network(f"{ip_str}/{prefix_len}", strict=False):
                        elems.append({"ip": str(addr), "timeout": str(timeout), "expires": expires, "_expires_s": expires_raw})
                except Exception:
                    elems.append({"ip": ip_str, "timeout": str(timeout), "expires": expires, "_expires_s": expires_raw})
            else:
                if prefix_len is None or prefix_len == 32 or "/" not in ip_str:
                    elems.append({"ip": ip_str, "timeout": str(timeout), "expires": expires, "_expires_s": expires_raw})
                else:
                    elems.append({"ip": ip_str, "timeout": str(timeout), "expires": expires, "_expires_s": expires_raw})
    return elems


def _nft_list_set(family, table, name):
    try:
        r = subprocess.run(
            [NFT, "-j", "list", "set", family, table, name],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode != 0:
            return []
        return _parse_set_elements(r.stdout)
    except Exception:
        return []


def nft_set_ips(table, name):
    global _nft_set_cache
    cache_key = f"{table}/{name}"
    now = time()
    if cache_key in _nft_set_cache and now - _nft_set_cache[cache_key]["ts"] < _nft_set_cache_ttl:
        return _nft_set_cache[cache_key]["data"]

    inet_elems = _nft_list_set("inet", table, name)

    seen = set()
    merged = []
    for e in inet_elems:
        if e["ip"] not in seen:
            seen.add(e["ip"])
            merged.append(e)

    merged.sort(key=lambda x: x.get("_expires_s", 0), reverse=True)
    _nft_set_cache[cache_key] = {"data": merged, "ts": now}
    return merged


def nft_set_count(table, name):
    return len(nft_set_ips(table, name))


def cscli_json(args):
    try:
        r = subprocess.run([CSCLI] + args + ["-o", "json"],
                           capture_output=True, text=True, timeout=10)
        return json.loads(r.stdout)
    except Exception:
        return []


def svc_status(name):
    try:
        r = subprocess.run(["systemctl", "is-active", name],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def svc_uptime(name):
    try:
        r = subprocess.run(["systemctl", "show", name, "--property=ActiveEnterTimestamp"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip().replace("ActiveEnterTimestamp=", "")
    except Exception:
        return ""


def unban_ip(ip_str):
    removed = []
    for family in ("inet",):
        try:
            r = subprocess.run(
                [NFT, "delete", "element", family, "filter", "blacklist_ipv4", f"{{ {ip_str} }}"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                removed.append(f"{family}/filter/blacklist_ipv4")
        except Exception:
            pass
        try:
            r = subprocess.run(
                [NFT, "delete", "element", family, "filter", "crowdsec-blacklists", f"{{ {ip_str} }}"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                removed.append(f"{family}/filter/crowdsec-blacklists")
        except Exception:
            pass
    _nft_set_cache.clear()
    return removed


def unban_network(network_str):
    try:
        net = _ip.ip_network(network_str, strict=False)
    except ValueError:
        return []
    removed_from = []
    all_ips = []
    bl = nft_set_ips("filter", "blacklist_ipv4")
    cs = nft_set_ips("filter", "crowdsec-blacklists")
    for entry in bl + cs:
        try:
            if _ip.ip_address(entry["ip"]) in net:
                all_ips.append(entry["ip"])
        except ValueError:
            continue
    for ip in set(all_ips):
        r = unban_ip(ip)
        if r:
            removed_from.extend(r)
    return list(set(removed_from))


def unban_cs_decision(ip_str):
    try:
        decisions = cscli_json(["decisions", "list", "-o", "json"])
        for d in decisions:
            val = d.get("value", "") or d.get("target", "")
            if val == ip_str:
                subprocess.run(
                    [CSCLI, "decisions", "delete", "--ip", ip_str],
                    capture_output=True, text=True, timeout=10
                )
                break
    except Exception:
        pass


def count_nft_drop_rules(table="inet filter"):
    try:
        parts = table.split()
        r = subprocess.run([NFT, "-j", "list", "table", parts[0], parts[1]],
                           capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout)
        count = 0
        for item in data.get("nftables", []):
            rule = item.get("rule")
            if not rule:
                continue
            for e in rule.get("expr", []):
                if isinstance(e, dict) and "drop" in e:
                    count += 1
                    break
        return count
    except Exception:
        return 0