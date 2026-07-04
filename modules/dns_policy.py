#!/usr/bin/env python3
import json
import time

from .dns_db import get_db, add_event

DEFAULT_POLICIES = [
    {
        "name": "Default",
        "description": "Normal clients: block ads, trackers, malware, phishing",
        "block_categories": "ads,trackers,malware,phishing",
        "allow_categories": "",
        "custom_allow": "",
        "custom_block": "",
        "safe_search_json": json.dumps({"enabled": False}),
        "blocked_services_json": json.dumps([]),
        "schedule_json": json.dumps({}),
        "block_doh_bypass": 0,
        "block_external_dns": 0,
        "default_action": "allow",
        "blocking_mode": "null_ip",
        "blocked_response_ttl": 60,
        "upstreams_json": json.dumps([]),
        "rate_limit_qps": None,
        "enabled": 1,
    },
    {
        "name": "Admin",
        "description": "Developer/admin devices: block only malware and phishing",
        "block_categories": "malware,phishing",
        "allow_categories": "",
        "custom_allow": "",
        "custom_block": "",
        "safe_search_json": json.dumps({"enabled": False}),
        "blocked_services_json": json.dumps([]),
        "schedule_json": json.dumps({}),
        "block_doh_bypass": 0,
        "block_external_dns": 0,
        "default_action": "allow",
        "blocking_mode": "null_ip",
        "blocked_response_ttl": 60,
        "upstreams_json": json.dumps([]),
        "rate_limit_qps": None,
        "enabled": 1,
    },
    {
        "name": "Kids",
        "description": "Children devices: strict filtering with Safe Search",
        "block_categories": "ads,trackers,malware,phishing,adult,gambling,social",
        "allow_categories": "",
        "custom_allow": "",
        "custom_block": "",
        "safe_search_json": json.dumps({"enabled": True, "google": True, "youtube": True, "bing": True, "duckduckgo": True}),
        "blocked_services_json": json.dumps(["youtube", "tiktok", "instagram", "facebook"]),
        "schedule_json": json.dumps({}),
        "block_doh_bypass": 1,
        "block_external_dns": 1,
        "default_action": "block",
        "blocking_mode": "null_ip",
        "blocked_response_ttl": 300,
        "upstreams_json": json.dumps([]),
        "rate_limit_qps": None,
        "enabled": 1,
    },
    {
        "name": "IoT",
        "description": "IoT devices: block telemetry and malware, allow only necessary",
        "block_categories": "telemetry,malware,phishing",
        "allow_categories": "",
        "custom_allow": "",
        "custom_block": "",
        "safe_search_json": json.dumps({"enabled": False}),
        "blocked_services_json": json.dumps([]),
        "schedule_json": json.dumps({}),
        "block_doh_bypass": 1,
        "block_external_dns": 1,
        "default_action": "block",
        "blocking_mode": "null_ip",
        "blocked_response_ttl": 60,
        "upstreams_json": json.dumps([]),
        "rate_limit_qps": 10,
        "enabled": 1,
    },
    {
        "name": "Guest",
        "description": "Guest network: basic protection, DNS redirect enforced",
        "block_categories": "malware,phishing,adult,gambling",
        "allow_categories": "",
        "custom_allow": "",
        "custom_block": "",
        "safe_search_json": json.dumps({"enabled": False}),
        "blocked_services_json": json.dumps([]),
        "schedule_json": json.dumps({}),
        "block_doh_bypass": 1,
        "block_external_dns": 1,
        "default_action": "allow",
        "blocking_mode": "null_ip",
        "blocked_response_ttl": 60,
        "upstreams_json": json.dumps([]),
        "rate_limit_qps": 30,
        "enabled": 1,
    },
    {
        "name": "Family",
        "description": "Family profile: Safe Search, adult/gambling/social blocks and night internet schedule",
        "block_categories": "ads,trackers,malware,phishing,adult,gambling,social",
        "allow_categories": "",
        "custom_allow": "",
        "custom_block": "",
        "safe_search_json": json.dumps({"enabled": True, "google": True, "youtube": True, "bing": True, "duckduckgo": True}),
        "blocked_services_json": json.dumps(["adult", "gambling", "youtube", "tiktok", "instagram", "facebook", "roblox"]),
        "schedule_json": json.dumps({"enabled": True, "timezone": "Europe/Kiev", "offline_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "offline_start": "22:00", "offline_end": "07:00"}),
        "block_doh_bypass": 1,
        "block_external_dns": 1,
        "default_action": "allow",
        "blocking_mode": "null_ip",
        "blocked_response_ttl": 300,
        "upstreams_json": json.dumps([]),
        "rate_limit_qps": 20,
        "enabled": 1,
    },
]

BLOCKED_SERVICES = {
    "youtube": ["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "youtube-nocookie.com",
                "youtubei.googleapis.com", "youtube.googleapis.com", "yt3.ggpht.com", "i.ytimg.com", "ytimg.com",
                "googlevideo.com", "video.google.com", "youtube-ui.l.google.com"],
    "tiktok": ["tiktok.com", "www.tiktok.com", "api.tiktok.com", "muscdn.com", "musical.ly"],
    "facebook": ["facebook.com", "www.facebook.com", "fbcdn.net", "fb.com", "messenger.com", "instagram.com", "www.instagram.com"],
    "instagram": ["instagram.com", "www.instagram.com", "cdninstagram.com"],
    "twitter": ["twitter.com", "x.com", "twimg.com", "abs.twimg.com", "t.co"],
    "whatsapp": ["whatsapp.com", "web.whatsapp.com", "cdn.whatsapp.net", "wa.me"],
    "telegram": ["telegram.org", "t.me", "telegram.me", "web.telegram.org"],
    "discord": ["discord.com", "discordapp.com", "discord.gg", "cdn.discordapp.com"],
    "roblox": ["roblox.com", "www.roblox.com", "rbxcdn.com", "roblox.gg"],
    "steam": ["store.steampowered.com", "steamcommunity.com", "steamcdn-a.akamaihd.net"],
    "netflix": ["netflix.com", "www.netflix.com", "nflxvideo.net", "nflximg.net", "nflxext.com"],
    "onlyfans": ["onlyfans.com", "www.onlyfans.com"],
    "gambling": ["bet365.com", "pokerstars.com", "888casino.com", "williamhill.com", "bwin.com"],
    "crypto": ["binance.com", "coinbase.com", "kraken.com", "bitfinex.com"],
    "doh_vpn": ["cloudflare-dns.com", "dns.google", "dns.quad9.net", "doh.opendns.com", "doh.cleanbrowsing.org",
                "adblock.dcompass.org", "doh.libredns.gr", "dns.adguard.com", "dah4r2b3mn3r3vw7g5xrxd6rv4a3j7rvue2gvjfg4kll7i4j3aa7ad.onion"],
}

BLOCKED_SERVICES.update({
    "snapchat": ["snapchat.com", "www.snapchat.com", "app.snapchat.com", "sc-cdn.net", "snapkit.com"],
    "reddit": ["reddit.com", "www.reddit.com", "redd.it", "redditmedia.com", "redditstatic.com"],
    "pinterest": ["pinterest.com", "www.pinterest.com", "pinimg.com"],
    "twitch": ["twitch.tv", "www.twitch.tv", "ttvnw.net", "jtvnw.net", "twitchcdn.net"],
    "spotify": ["spotify.com", "open.spotify.com", "scdn.co", "spotifycdn.com"],
    "epic_games": ["epicgames.com", "store.epicgames.com", "unrealengine.com", "ol.epicgames.com"],
    "minecraft": ["minecraft.net", "mojang.com", "api.minecraftservices.com", "xboxlive.com"],
    "xbox": ["xbox.com", "xboxlive.com", "xboxservices.com", "microsoft.com"],
    "playstation": ["playstation.com", "sonyentertainmentnetwork.com", "playstation.net"],
    "amazon_video": ["primevideo.com", "amazonvideo.com", "aiv-cdn.net", "media-amazon.com"],
    "disney_plus": ["disneyplus.com", "bamgrid.com", "disney.demdex.net"],
    "hulu": ["hulu.com", "www.hulu.com", "huluim.com"],
    "adult": ["pornhub.com", "xvideos.com", "xnxx.com", "redtube.com", "youporn.com", "xhamster.com"],
    "dating": ["tinder.com", "bumble.com", "badoo.com", "okcupid.com", "grindr.com"],
    "shopping": ["amazon.com", "ebay.com", "aliexpress.com", "temu.com", "shein.com"],
    "ai_chat": ["chat.openai.com", "chatgpt.com", "claude.ai", "gemini.google.com", "copilot.microsoft.com"],
    "cloud_storage": ["dropbox.com", "drive.google.com", "onedrive.live.com", "icloud.com", "mega.nz"],
    "proxy_vpn": ["nordvpn.com", "expressvpn.com", "surfshark.com", "protonvpn.com", "windscribe.com", "torproject.org"],
})

SAFE_SEARCH_DOMAINS = {
    "google": [("forcesafesearch.google.com", "google.com"), ("forcesafesearch.google.com", "www.google.com")],
    "youtube": [("restrict.youtube.com", "youtube.com"), ("restrict.youtube.com", "www.youtube.com"), ("restrict.youtube.com", "m.youtube.com")],
    "bing": [("strict.bing.com", "bing.com"), ("strict.bing.com", "www.bing.com")],
    "duckduckgo": [("safe.duckduckgo.com", "duckduckgo.com")],
    "yandex": [("family.yandex.com", "yandex.com")],
    "ecosia": [("safe.ecosia.org", "ecosia.org")],
    "pixabay": [("pixabay.com", "pixabay.com")],
}


def ensure_default_policies():
    conn = get_db()
    try:
        existing = conn.execute("SELECT COUNT(*) as c FROM dns_policies").fetchone()["c"]
        if existing == 0:
            now = int(time.time())
            for p in DEFAULT_POLICIES:
                conn.execute(
                    "INSERT INTO dns_policies (name, description, block_categories, allow_categories, custom_allow, custom_block, safe_search_json, blocked_services_json, schedule_json, block_doh_bypass, block_external_dns, default_action, blocking_mode, blocked_response_ttl, upstreams_json, rate_limit_qps, enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (p["name"], p["description"], p["block_categories"], p["allow_categories"],
                     p["custom_allow"], p["custom_block"], p["safe_search_json"],
                     p["blocked_services_json"], p["schedule_json"], p["block_doh_bypass"],
                     p["block_external_dns"], p["default_action"], p["blocking_mode"],
                     p["blocked_response_ttl"], p["upstreams_json"], p["rate_limit_qps"], p["enabled"]),
                )
            conn.commit()
            add_event("policies_created", "info", "dns_policy", "Default policies created")
            return True
        names = {r["name"] for r in conn.execute("SELECT name FROM dns_policies").fetchall()}
        if "Family" not in names:
            schedule = {
                "enabled": True,
                "timezone": "Europe/Kiev",
                "offline_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                "offline_start": "22:00",
                "offline_end": "07:00",
            }
            conn.execute(
                "INSERT INTO dns_policies (name, description, block_categories, allow_categories, custom_allow, custom_block, safe_search_json, blocked_services_json, schedule_json, block_doh_bypass, block_external_dns, default_action, blocking_mode, blocked_response_ttl, upstreams_json, rate_limit_qps, enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("Family", "Family profile: Safe Search, adult/gambling/social blocks and night internet schedule",
                 "ads,trackers,malware,phishing,adult,gambling,social", "", "", "",
                 json.dumps({"enabled": True, "google": True, "youtube": True, "bing": True, "duckduckgo": True}),
                 json.dumps(["adult", "gambling", "youtube", "tiktok", "instagram", "facebook", "roblox"]),
                 json.dumps(schedule), 1, 1, "allow", "null_ip", 300, json.dumps([]), 20, 1),
            )
            conn.commit()
            add_event("policy_created", "info", "dns_policy", "Family policy created")
            return True
        return False
    finally:
        conn.close()


def get_policy(policy_id=None, name=None):
    conn = get_db()
    try:
        if policy_id:
            row = conn.execute("SELECT * FROM dns_policies WHERE id=?", (policy_id,)).fetchone()
            return dict(row) if row else None
        if name:
            row = conn.execute("SELECT * FROM dns_policies WHERE name=?", (name,)).fetchone()
            return dict(row) if row else None
        return None
    finally:
        conn.close()


def get_all_policies():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM dns_policies ORDER BY id ASC").fetchall()]
    finally:
        conn.close()


def add_policy(name, description="", block_categories="ads,trackers,malware,phishing",
               allow_categories="", custom_allow="", custom_block="",
               safe_search_json=None, blocked_services_json=None, schedule_json=None,
               block_doh_bypass=0, block_external_dns=0, default_action="allow",
               blocking_mode="null_ip", blocked_response_ttl=60,
               upstreams_json=None, rate_limit_qps=None, enabled=1):
    now = int(time.time())
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO dns_policies (name, description, block_categories, allow_categories, custom_allow, custom_block, safe_search_json, blocked_services_json, schedule_json, block_doh_bypass, block_external_dns, default_action, blocking_mode, blocked_response_ttl, upstreams_json, rate_limit_qps, enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, description, block_categories, allow_categories, custom_allow, custom_block,
             safe_search_json or '{"enabled": false}', blocked_services_json or '[]',
             schedule_json or '{}', block_doh_bypass, block_external_dns,
             default_action, blocking_mode, blocked_response_ttl,
             upstreams_json or '[]', rate_limit_qps, enabled),
        )
        conn.commit()
        return True, cur.lastrowid
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def update_policy(policy_id, **kwargs):
    conn = get_db()
    try:
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in ("name", "description", "block_categories", "allow_categories",
                      "custom_allow", "custom_block", "safe_search_json",
                      "blocked_services_json", "schedule_json", "block_doh_bypass",
                      "block_external_dns", "default_action", "blocking_mode",
                      "blocked_response_ttl", "upstreams_json", "rate_limit_qps", "enabled"):
                sets.append(f"{k}=?")
                vals.append(json.dumps(v) if isinstance(v, (dict, list)) else v)
        if not sets:
            return False, "No fields to update"
        sets.append("updated_at=?")
        vals.append(int(time.time()))
        vals.append(policy_id)
        conn.execute(f"UPDATE dns_policies SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        return True, f"Policy {policy_id} updated"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_policy(policy_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dns_policies WHERE id=?", (policy_id,))
        conn.commit()
        return True, f"Policy {policy_id} deleted"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def add_client(name=None, ip=None, cidr=None, mac=None, client_id=None,
                tags=None, policy_id=None, source="manual", notes=None):
    now = int(time.time())
    conn = get_db()
    try:
        existing = None
        if mac:
            existing = conn.execute("SELECT id FROM dns_clients WHERE mac=? COLLATE NOCASE", (mac,)).fetchone()
        if not existing and ip:
            existing = conn.execute("SELECT id FROM dns_clients WHERE ip=?", (ip,)).fetchone()
        if existing:
            cid = existing["id"]
            updates = {}
            if name:
                updates["name"] = name
            if ip:
                updates["ip"] = ip
            if mac:
                updates["mac"] = mac
            if policy_id is not None:
                updates["policy_id"] = policy_id
            if notes:
                updates["notes"] = notes
            updates["last_seen_ts"] = now
            sets = []
            vals = []
            for k, v in updates.items():
                sets.append(f"{k}=?")
                vals.append(json.dumps(v) if isinstance(v, (dict, list)) else v)
            vals.append(cid)
            conn.execute(f"UPDATE dns_clients SET {', '.join(sets)} WHERE id=?", vals)
            conn.commit()
            return True, cid
        cur = conn.execute(
            "INSERT INTO dns_clients (name, ip, cidr, mac, client_id, tags_json, policy_id, enabled, use_global_settings, filtering_enabled, first_seen_ts, last_seen_ts, total_queries, blocked_queries, risk_score, source, notes) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 1, ?, ?, 0, 0, 0, ?, ?)",
            (name, ip, cidr, mac, client_id,
             json.dumps(tags) if tags else "[]",
             policy_id, now, now, source, notes),
        )
        conn.commit()
        return True, cur.lastrowid
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def update_client(client_id, **kwargs):
    conn = get_db()
    try:
        sets = []
        vals = []
        for k, v in kwargs.items():
            valid_cols = ("name", "ip", "cidr", "mac", "client_id", "tags_json", "policy_id",
                          "enabled", "use_global_settings", "filtering_enabled",
                          "safesearch_enabled", "safebrowsing_enabled", "parental_enabled",
                          "blocked_services_json", "upstreams_json", "ignore_querylog",
                          "ignore_statistics", "notes")
            if k in valid_cols:
                sets.append(f"{k}=?")
                vals.append(json.dumps(v) if isinstance(v, (dict, list)) else v)
        if not sets:
            return False, "No fields to update"
        sets.append("last_seen_ts=?")
        vals.append(int(time.time()))
        vals.append(client_id)
        conn.execute(f"UPDATE dns_clients SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        return True, f"Client {client_id} updated"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def set_client_policy(client_id, policy_id):
    return update_client(client_id, policy_id=policy_id)


def set_client_services(client_id, services):
    return update_client(client_id, blocked_services_json=services)


def set_client_upstreams(client_id, upstreams):
    return update_client(client_id, upstreams_json=upstreams)


def parse_json_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        if value.startswith("["):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                pass
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def services_to_domains(services):
    domains = []
    for svc in services:
        domains.extend(BLOCKED_SERVICES.get(str(svc).strip().lower(), []))
    return list(dict.fromkeys(domains))


def update_client_stats():
    conn = get_db()
    try:
        clients = conn.execute("SELECT id, ip, mac, name FROM dns_clients").fetchall()
        if not clients:
            return
        now = int(time.time())
        cutoff_24h = now - 86400
        for c in clients:
            cip = c["ip"] or ""
            cmac = c["mac"] or ""
            if not cip and not cmac:
                continue
            if cip:
                total = conn.execute(
                    "SELECT COUNT(*) as c FROM dns_queries WHERE client_ip=? AND ts>?",
                    (cip, cutoff_24h),
                ).fetchone()["c"]
                blocked = conn.execute(
                    "SELECT COUNT(*) as c FROM dns_queries WHERE client_ip=? AND ts>? AND action='block'",
                    (cip, cutoff_24h),
                ).fetchone()["c"]
            else:
                total = conn.execute(
                    "SELECT COUNT(*) as c FROM dns_queries WHERE client_mac=? AND ts>?",
                    (cmac, cutoff_24h),
                ).fetchone()["c"]
                blocked = conn.execute(
                    "SELECT COUNT(*) as c FROM dns_queries WHERE client_mac=? AND ts>? AND action='block'",
                    (cmac, cutoff_24h),
                ).fetchone()["c"]
            risk = min(1.0, blocked / total) if total > 0 else 0
            conn.execute(
                "UPDATE dns_clients SET total_queries=?, blocked_queries=?, risk_score=?, last_seen_ts=? WHERE id=?",
                (total, blocked, risk, now, c["id"]),
            )
        conn.commit()
    finally:
        conn.close()


def resolve_client_name(client_ip, client_mac=""):
    conn = get_db()
    try:
        if client_ip:
            row = conn.execute("SELECT name FROM dns_clients WHERE ip=?", (client_ip,)).fetchone()
            if row and row["name"]:
                return row["name"]
        if client_mac:
            row = conn.execute("SELECT name FROM dns_clients WHERE mac=?", (client_mac,)).fetchone()
            if row and row["name"]:
                return row["name"]
        return ""
    finally:
        conn.close()


def resolve_client_mac(client_ip):
    conn = get_db()
    try:
        row = conn.execute("SELECT mac FROM dns_clients WHERE ip=?", (client_ip,)).fetchone()
        return row["mac"] if row and row["mac"] else ""
    finally:
        conn.close()


def delete_client(client_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dns_clients WHERE id=?", (client_id,))
        conn.commit()
        return True, f"Client {client_id} deleted"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def discover_clients_from_arp():
    import subprocess
    clients_added = 0
    try:
        r = subprocess.run(["ip", "neigh", "show"], capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return 0
        conn = get_db()
        try:
            for line in r.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) < 4:
                    continue
                ip = parts[0]
                mac_idx = None
                for i, p in enumerate(parts):
                    if p == "lladdr" and i + 1 < len(parts):
                        mac_idx = i + 1
                        break
                mac = parts[mac_idx] if mac_idx and mac_idx < len(parts) else None
                if not mac or mac == "00:00:00:00:00:00":
                    continue
                existing = conn.execute("SELECT id FROM dns_clients WHERE ip=? OR mac=?", (ip, mac)).fetchone()
                if not existing:
                    now = int(time.time())
                    conn.execute(
                        "INSERT INTO dns_clients (ip, mac, source, first_seen_ts, last_seen_ts, enabled, use_global_settings, filtering_enabled, total_queries, blocked_queries, risk_score, tags_json, blocked_services_json, upstreams_json) VALUES (?, ?, 'arp', ?, ?, 1, 1, 1, 0, 0, 0, '[]', '[]', '[]')",
                        (ip, mac, now, now),
                    )
                    clients_added += 1
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
    return clients_added


def get_safe_search_rewrites(policy_id=None, safe_search_json=None):
    rewrites = []
    if safe_search_json:
        try:
            ss = json.loads(safe_search_json) if isinstance(safe_search_json, str) else safe_search_json
        except (json.JSONDecodeError, TypeError):
            return rewrites
    else:
        return rewrites

    if not ss.get("enabled"):
        return rewrites

    for engine, enabled in ss.items():
        if engine == "enabled" or not enabled:
            continue
        engine_domains = SAFE_SEARCH_DOMAINS.get(engine, [])
        for safe_domain, orig_domain in engine_domains:
            rewrites.append({"domain": orig_domain, "target": safe_domain, "rtype": "CNAME"})

    return rewrites


def get_blocked_service_domains(services_json):
    if not services_json:
        return []
    try:
        services = json.loads(services_json) if isinstance(services_json, str) else services_json
    except (json.JSONDecodeError, TypeError):
        return []

    all_services = get_all_services()
    domains = []
    for svc in services:
        if svc in all_services:
            domains.extend(all_services[svc])
    return domains


def get_all_groups():
    conn = get_db()
    try:
        groups = [dict(r) for r in conn.execute("SELECT * FROM dns_groups ORDER BY id ASC").fetchall()]
        for g in groups:
            g["client_count"] = conn.execute("SELECT COUNT(*) as c FROM dns_client_groups WHERE group_id=?", (g["id"],)).fetchone()["c"]
            g["rule_count"] = conn.execute("SELECT COUNT(*) as c FROM dns_rule_groups WHERE group_id=?", (g["id"],)).fetchone()["c"]
            g["list_count"] = conn.execute("SELECT COUNT(*) as c FROM dns_list_groups WHERE group_id=?", (g["id"],)).fetchone()["c"]
        return groups
    finally:
        conn.close()


def add_group(name, description=""):
    now = int(time.time())
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO dns_groups (name, description, enabled, created_at) VALUES (?, ?, 1, ?)", (name, description, now))
        conn.commit()
        return True, cur.lastrowid
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def update_group(group_id, **kwargs):
    conn = get_db()
    try:
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in ("name", "description", "enabled"):
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return False, "No fields to update"
        sets.append("updated_at=?")
        vals.append(int(time.time()))
        vals.append(group_id)
        conn.execute(f"UPDATE dns_groups SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        return True, f"Group {group_id} updated"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_group(group_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dns_client_groups WHERE group_id=?", (group_id,))
        conn.execute("DELETE FROM dns_rule_groups WHERE group_id=?", (group_id,))
        conn.execute("DELETE FROM dns_list_groups WHERE group_id=?", (group_id,))
        conn.execute("DELETE FROM dns_groups WHERE id=?", (group_id,))
        conn.commit()
        return True, f"Group {group_id} deleted"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def add_client_to_group(client_id, group_id):
    conn = get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO dns_client_groups (client_id, group_id) VALUES (?, ?)", (client_id, group_id))
        conn.commit()
        return True, "Client added to group"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def remove_client_from_group(client_id, group_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dns_client_groups WHERE client_id=? AND group_id=?", (client_id, group_id))
        conn.commit()
        return True, "Client removed from group"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def add_rule_to_group(rule_id, group_id):
    conn = get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO dns_rule_groups (rule_id, group_id) VALUES (?, ?)", (rule_id, group_id))
        conn.commit()
        return True, "Rule added to group"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def remove_rule_from_group(rule_id, group_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dns_rule_groups WHERE rule_id=? AND group_id=?", (rule_id, group_id))
        conn.commit()
        return True, "Rule removed from group"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def add_list_to_group(list_id, group_id):
    conn = get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO dns_list_groups (list_id, group_id) VALUES (?, ?)", (list_id, group_id))
        conn.commit()
        return True, "List added to group"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def remove_list_from_group(list_id, group_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dns_list_groups WHERE list_id=? AND group_id=?", (list_id, group_id))
        conn.commit()
        return True, "List removed from group"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def get_group_members(group_id):
    conn = get_db()
    try:
        clients = [dict(r) for r in conn.execute(
            "SELECT c.* FROM dns_clients c JOIN dns_client_groups cg ON c.id=cg.client_id WHERE cg.group_id=?", (group_id,)).fetchall()]
        rules = [dict(r) for r in conn.execute(
            "SELECT r.id, r.value, r.type, r.action FROM dns_rules r JOIN dns_rule_groups rg ON r.id=rg.rule_id WHERE rg.group_id=?", (group_id,)).fetchall()]
        lists = [dict(r) for r in conn.execute(
            "SELECT l.id, l.name, l.url FROM dns_lists l JOIN dns_list_groups lg ON l.id=lg.list_id WHERE lg.group_id=?", (group_id,)).fetchall()]
        return {"clients": clients, "rules": rules, "lists": lists}
    finally:
        conn.close()


def get_all_access_rules():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM dns_access_rules ORDER BY id ASC").fetchall()]
    finally:
        conn.close()


def add_access_rule(rule_type, value, comment=""):
    now = int(time.time())
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO dns_access_rules (type, value, comment, enabled, created_at) VALUES (?, ?, ?, 1, ?)",
                          (rule_type, value, comment, now))
        conn.commit()
        return True, cur.lastrowid
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def update_access_rule(rule_id, **kwargs):
    conn = get_db()
    try:
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in ("type", "value", "comment", "enabled"):
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return False, "No fields to update"
        vals.append(rule_id)
        conn.execute(f"UPDATE dns_access_rules SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        return True, f"Access rule {rule_id} updated"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_access_rule(rule_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dns_access_rules WHERE id=?", (rule_id,))
        conn.commit()
        return True, f"Access rule {rule_id} deleted"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def get_custom_services():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM dns_custom_services ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_custom_service(name, domains=None):
    now = int(time.time())
    domains = domains or []
    domains_json = json.dumps(domains)
    conn = get_db()
    try:
        conn.execute("INSERT INTO dns_custom_services (name, domains_json, enabled, created_at) VALUES (?, ?, 1, ?)",
                     (name.lower().replace(" ", "_"), domains_json, now))
        conn.commit()
        return True, f"Service '{name}' added"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def update_custom_service(service_id, name=None, domains=None, enabled=None):
    conn = get_db()
    try:
        sets = []
        vals = []
        if name is not None:
            sets.append("name=?")
            vals.append(name.lower().replace(" ", "_"))
        if domains is not None:
            sets.append("domains_json=?")
            vals.append(json.dumps(domains))
        if enabled is not None:
            sets.append("enabled=?")
            vals.append(1 if enabled else 0)
        if not sets:
            return False, "No fields to update"
        sets.append("updated_at=?")
        vals.append(int(time.time()))
        vals.append(service_id)
        conn.execute(f"UPDATE dns_custom_services SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        return True, f"Service {service_id} updated"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def delete_custom_service(service_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM dns_custom_services WHERE id=?", (service_id,))
        conn.commit()
        return True, f"Service {service_id} deleted"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def add_service_domain(service_id, domain):
    conn = get_db()
    try:
        row = conn.execute("SELECT domains_json FROM dns_custom_services WHERE id=?", (service_id,)).fetchone()
        if not row:
            return False, "Service not found"
        domains = json.loads(row["domains_json"])
        domain = domain.strip().lower()
        if domain in domains:
            return False, "Domain already exists"
        domains.append(domain)
        conn.execute("UPDATE dns_custom_services SET domains_json=?, updated_at=? WHERE id=?",
                     (json.dumps(domains), int(time.time()), service_id))
        conn.commit()
        return True, f"Domain '{domain}' added"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def remove_service_domain(service_id, domain):
    conn = get_db()
    try:
        row = conn.execute("SELECT domains_json FROM dns_custom_services WHERE id=?", (service_id,)).fetchone()
        if not row:
            return False, "Service not found"
        domains = json.loads(row["domains_json"])
        domain = domain.strip().lower()
        if domain not in domains:
            return False, "Domain not found"
        domains.remove(domain)
        conn.execute("UPDATE dns_custom_services SET domains_json=?, updated_at=? WHERE id=?",
                     (json.dumps(domains), int(time.time()), service_id))
        conn.commit()
        return True, f"Domain '{domain}' removed"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def get_all_services():
    merged = dict(BLOCKED_SERVICES)
    for svc in get_custom_services():
        merged[svc["name"]] = json.loads(svc["domains_json"])
    return merged
