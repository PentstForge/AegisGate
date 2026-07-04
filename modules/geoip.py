import json
import os
import time
import ipaddress
import urllib.request
import threading
import maxminddb

GEOIP_DIR = "/opt/nft-dashboard/data/geoip"
CITY_DB = os.path.join(GEOIP_DIR, "GeoLite2-City.mmdb")
ASN_DB = os.path.join(GEOIP_DIR, "GeoLite2-ASN.mmdb")
CACHE_FILE = "/var/log/ram/geoip_cache.json"
CACHE_TTL = 86400
_IP_API_BATCH_URL = "http://ip-api.com/batch"
_IP_API_TTL = 604800

_city_reader = None
_asn_reader = None
_geo_cache = {}
_cache_mtime = 0
_dirty = False
_last_save = 0
_SAVE_INTERVAL = 60


def _get_city_reader():
    global _city_reader
    if _city_reader is None or not _city_reader:
        try:
            _city_reader = maxminddb.open_database(CITY_DB)
        except Exception:
            _city_reader = None
    return _city_reader


def _get_asn_reader():
    global _asn_reader
    if _asn_reader is None or not _asn_reader:
        try:
            _asn_reader = maxminddb.open_database(ASN_DB)
        except Exception:
            _asn_reader = None
    return _asn_reader


def _load_cache():
    global _geo_cache, _cache_mtime, _last_load
    if _geo_cache and time.time() - getattr(_load_cache, '_last_load', 0) < 5:
        return
    try:
        mt = os.path.getmtime(CACHE_FILE)
        if mt != _cache_mtime:
            with open(CACHE_FILE, "r") as f:
                _geo_cache = json.load(f)
            _cache_mtime = mt
    except (json.JSONDecodeError, ValueError):
        _geo_cache = {}
        _cache_mtime = 0
        try:
            os.remove(CACHE_FILE)
        except Exception:
            pass
    except Exception:
        _geo_cache = {}
        _cache_mtime = 0
    _load_cache._last_load = time.time()


def _save_cache():
    global _cache_mtime, _dirty, _last_save
    now = time.time()
    if not _dirty:
        return
    if _last_save > 0 and now - _last_save < _SAVE_INTERVAL:
        return
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_geo_cache, f)
        os.replace(tmp, CACHE_FILE)
        _cache_mtime = os.path.getmtime(CACHE_FILE)
        _dirty = False
        _last_save = now
    except Exception:
        pass


def _flush_cache():
    global _cache_mtime, _dirty, _last_save
    if not _dirty:
        return
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_geo_cache, f)
        os.replace(tmp, CACHE_FILE)
        _cache_mtime = os.path.getmtime(CACHE_FILE)
        _dirty = False
        _last_save = time.time()
    except Exception:
        pass


def _ip_api_batch_lookup(ips):
    global _geo_cache, _dirty
    public_ips = []
    for ip in ips:
        try:
            a = ipaddress.ip_address(ip)
            if a.is_private or a.is_loopback or a.is_reserved:
                continue
        except ValueError:
            continue
        public_ips.append(ip)
    if not public_ips:
        return
    batch = [{"query": ip, "fields": "status,country,countryCode,city,lat,lon,as,asname"} for ip in public_ips[:100]]
    try:
        data = json.dumps(batch).encode()
        req = urllib.request.Request(_IP_API_BATCH_URL, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            results = json.loads(resp.read().decode())
        now = time.time()
        for r in results:
            if r.get("status") != "success":
                continue
            ip = r.get("query", "")
            if not ip:
                continue
            entry = {
                "ip": ip,
                "country": r.get("countryCode"),
                "country_name": r.get("country"),
                "city": r.get("city"),
                "lat": r.get("lat"),
                "lon": r.get("lon"),
                "asn": None,
                "asn_org": r.get("asname"),
                "ts": now,
            }
            as_str = r.get("as", "")
            if as_str and as_str.startswith("AS"):
                try:
                    entry["asn"] = int(as_str.split()[0].replace("AS", ""))
                except Exception:
                    pass
            _geo_cache[ip] = entry
            _dirty = True
    except Exception:
        pass


def _bulk_lookup_batch(ips):
    global _geo_cache, _dirty, _cache_mtime
    cr = _get_city_reader()
    ar = _get_asn_reader()
    uncached = []
    for ip in ips:
        if ip in _geo_cache:
            entry = _geo_cache[ip]
            if time.time() - entry.get("ts", 0) < CACHE_TTL:
                continue
        uncached.append(ip)

    for ip in uncached:
        result = {
            "ip": ip,
            "country": None,
            "country_name": None,
            "city": None,
            "lat": None,
            "lon": None,
            "asn": None,
            "asn_org": None,
            "ts": time.time(),
        }
        try:
            ipaddr = ipaddress.ip_address(ip)
            if ipaddr.is_private or ipaddr.is_loopback or ipaddr.is_reserved:
                result["country"] = "LOCAL"
                result["country_name"] = "Private/Local"
                result["city"] = "LAN"
                _geo_cache[ip] = result
                continue
        except ValueError:
            _geo_cache[ip] = result
            continue

        if cr:
            try:
                city = cr.get(ip)
                if city:
                    c = city.get("country", {})
                    result["country"] = c.get("iso_code")
                    result["country_name"] = c.get("names", {}).get("en")
                    ct = city.get("city", {})
                    result["city"] = ct.get("names", {}).get("en")
                    loc = city.get("location", {})
                    result["lat"] = loc.get("latitude")
                    result["lon"] = loc.get("longitude")
            except Exception:
                pass

        if ar:
            try:
                asn = ar.get(ip)
                if asn:
                    result["asn"] = asn.get("autonomous_system_number")
                    result["asn_org"] = asn.get("autonomous_system_organization")
            except Exception:
                pass

        _geo_cache[ip] = result

    if uncached:
        need_api = False
        for ip in uncached:
            entry = _geo_cache.get(ip, {})
            if entry.get("country") is None and entry.get("country_name") != "Private/Local":
                need_api = True
                break
        if need_api and not cr:
            _ip_api_batch_lookup(uncached)
        _dirty = True
    if len(_geo_cache) > 20000:
        _geo_cache = dict(sorted(_geo_cache.items(), key=lambda x: x[1].get("ts", 0), reverse=True)[:15000])
        _dirty = True
    _flush_cache()


def lookup(ip):
    global _geo_cache, _dirty
    if ip in _geo_cache:
        entry = _geo_cache[ip]
        if time.time() - entry.get("ts", 0) < CACHE_TTL:
            return entry

    result = {
        "ip": ip,
        "country": None,
        "country_name": None,
        "city": None,
        "lat": None,
        "lon": None,
        "asn": None,
        "asn_org": None,
        "ts": time.time(),
    }

    try:
        ipaddr = ipaddress.ip_address(ip)
        if ipaddr.is_private or ipaddr.is_loopback or ipaddr.is_reserved:
            result["country"] = "LOCAL"
            result["country_name"] = "Private/Local"
            result["city"] = "LAN"
            _geo_cache[ip] = result
            _dirty = True
            _save_cache()
            return result
    except ValueError:
        return result

    cr = _get_city_reader()
    if cr:
        try:
            city = cr.get(ip)
            if city:
                c = city.get("country", {})
                result["country"] = c.get("iso_code")
                result["country_name"] = c.get("names", {}).get("en")
                ct = city.get("city", {})
                result["city"] = ct.get("names", {}).get("en")
                loc = city.get("location", {})
                result["lat"] = loc.get("latitude")
                result["lon"] = loc.get("longitude")
        except Exception:
            pass

    ar = _get_asn_reader()
    if ar:
        try:
            asn = ar.get(ip)
            if asn:
                result["asn"] = asn.get("autonomous_system_number")
                result["asn_org"] = asn.get("autonomous_system_organization")
        except Exception:
            pass

    _geo_cache[ip] = result
    _dirty = True
    if len(_geo_cache) > 20000:
        _geo_cache = dict(sorted(_geo_cache.items(), key=lambda x: x[1].get("ts", 0), reverse=True)[:15000])
    _save_cache()
    return result


def bulk_lookup(ips, limit=100):
    _load_cache()
    _bulk_lookup_batch(list(ips)[:limit])
    _flush_cache()
    results = []
    for ip in list(ips)[:limit]:
        if ip in _geo_cache:
            results.append(_geo_cache[ip])
        else:
            results.append(lookup(ip))
    return results


_stats_cache = {"data": None, "ts": 0, "ips_hash": None}
_STATS_CACHE_TTL = 60


def _compute_stats(ips):
    global _stats_cache
    ips_hash = hash(tuple(sorted(ips)[:2000]))
    now = time.time()
    if _stats_cache["data"] is not None and _stats_cache["ips_hash"] == ips_hash and now - _stats_cache["ts"] < _STATS_CACHE_TTL:
        return _stats_cache["data"]

    _load_cache()
    _bulk_lookup_batch(ips)
    _flush_cache()

    country_stats = {}
    asn_stats = {}
    for ip in ips:
        if ip in _geo_cache:
            info = _geo_cache[ip]
        else:
            info = lookup(ip)
        cc = info.get("country") or "XX"
        if cc not in country_stats:
            country_stats[cc] = {"count": 0, "name": info.get("country_name", cc)}
        country_stats[cc]["count"] += 1
        asn = info.get("asn")
        if asn is None:
            key = "Unknown"
        else:
            key = f"AS{asn} ({info.get('asn_org', 'Unknown')})"
        if key not in asn_stats:
            asn_stats[key] = {"count": 0, "asn": asn, "org": info.get("asn_org"), "ips": []}
        asn_stats[key]["count"] += 1
        asn_stats[key]["ips"].append(ip)

    countries = dict(sorted(country_stats.items(), key=lambda x: x[1]["count"], reverse=True))
    asns = dict(sorted(asn_stats.items(), key=lambda x: x[1]["count"], reverse=True))
    _stats_cache["data"] = {"countries": countries, "asns": asns}
    _stats_cache["ts"] = now
    _stats_cache["ips_hash"] = ips_hash
    return _stats_cache["data"]


def get_country_stats(ips):
    data = _compute_stats(ips)
    return data.get("countries", {})


def get_asn_stats(ips):
    data = _compute_stats(ips)
    return data.get("asns", {})


COUNTRY_FLAGS = {
    "AD": "🇦🇩", "AE": "🇦🇪", "AF": "🇦🇫", "AG": "🇦🇬", "AI": "🇦🇮", "AL": "🇦🇱", "AM": "🇦🇲",
    "AO": "🇦🇴", "AR": "🇦🇷", "AS": "🇦🇸", "AT": "🇦🇹", "AU": "🇦🇺", "AW": "🇦🇼", "AX": "🇦🇽",
    "AZ": "🇦🇿", "BA": "🇧🇦", "BB": "🇧🇧", "BD": "🇧🇩", "BE": "🇧🇪", "BF": "🇧🇫", "BG": "🇧🇬",
    "BH": "🇧🇭", "BI": "🇧🇮", "BJ": "🇧🇯", "BL": "🇧🇱", "BM": "🇧🇲", "BN": "🇧🇳", "BO": "🇧🇴",
    "BQ": "🇧🇶", "BR": "🇧🇷", "BS": "🇧🇸", "BT": "🇧🇹", "BV": "🇧🇻", "BW": "🇧🇼", "BY": "🇧🇾",
    "BZ": "🇧🇿", "CA": "🇨🇦", "CC": "🇨🇨", "CD": "🇨🇩", "CF": "🇨🇫", "CG": "🇨🇬", "CH": "🇨🇭",
    "CI": "🇨🇮", "CK": "🇨🇰", "CL": "🇨🇱", "CM": "🇨🇲", "CN": "🇨🇳", "CO": "🇨🇴", "CR": "🇨🇷",
    "CU": "🇨🇺", "CV": "🇨🇻", "CW": "🇨🇼", "CX": "🇨🇽", "CY": "🇨🇾", "CZ": "🇨🇿", "DE": "🇩🇪",
    "DJ": "🇩🇯", "DK": "🇩🇰", "DM": "🇩🇲", "DO": "🇩🇴", "DZ": "🇩🇿", "EC": "🇪🇨", "EE": "🇪🇪",
    "EG": "🇪🇬", "EH": "🇪🇭", "ER": "🇪🇷", "ES": "🇪🇸", "ET": "🇪🇹", "FI": "🇫🇮", "FJ": "🇫🇯",
    "FK": "🇫🇰", "FM": "🇫🇲", "FO": "🇫🇴", "FR": "🇫🇷", "GA": "🇬🇦", "GB": "🇬🇧", "GD": "🇬🇩",
    "GE": "🇬🇪", "GF": "🇬🇫", "GG": "🇬🇬", "GH": "🇬🇭", "GI": "🇬🇮", "GL": "🇬🇱", "GM": "🇬🇲",
    "GN": "🇬🇳", "GP": "🇬🇵", "GQ": "🇬🇶", "GR": "🇬🇷", "GS": "🇬🇸", "GT": "🇬🇹", "GU": "🇬🇺",
    "GW": "🇬🇼", "GY": "🇬🇾", "HK": "🇭🇰", "HM": "🇭🇲", "HN": "🇭🇳", "HR": "🇭🇷", "HT": "🇭🇹",
    "HU": "🇭🇺", "ID": "🇮🇩", "IE": "🇮🇪", "IL": "🇮🇱", "IM": "🇮🇲", "IN": "🇮🇳", "IO": "🇮🇴",
    "IQ": "🇮🇶", "IR": "🇮🇷", "IS": "🇮🇸", "IT": "🇮🇹", "JE": "🇯🇪", "JM": "🇯🇲", "JO": "🇯🇴",
    "JP": "🇯🇵", "KE": "🇰🇪", "KG": "🇰🇬", "KH": "🇰🇭", "KI": "🇰🇮", "KM": "🇰🇲", "KN": "🇰🇳",
    "KP": "🇰🇵", "KR": "🇰🇷", "KW": "🇰🇼", "KY": "🇰🇾", "KZ": "🇰🇿", "LA": "🇱🇦", "LB": "🇱🇧",
    "LC": "🇱🇨", "LI": "🇱🇮", "LK": "🇱🇰", "LR": "🇱🇷", "LS": "🇱🇸", "LT": "🇱🇹", "LU": "🇱🇺",
    "LV": "🇱🇻", "LY": "🇱🇾", "MA": "🇲🇦", "MC": "🇲🇨", "MD": "🇲🇩", "ME": "🇲🇪", "MF": "🇲🇫",
    "MG": "🇲🇬", "MH": "🇲🇭", "MK": "🇲🇰", "ML": "🇲🇱", "MM": "🇲🇲", "MN": "🇲🇳", "MO": "🇲🇴",
    "MP": "🇲🇵", "MQ": "🇲🇶", "MR": "🇲🇷", "MS": "🇲🇸", "MT": "🇲🇹", "MU": "🇲🇺", "MV": "🇲🇻",
    "MW": "🇲🇼", "MX": "🇲🇽", "MY": "🇲🇾", "MZ": "🇲🇿", "NA": "🇳🇦", "NC": "🇳🇨", "NE": "🇳🇪",
    "NF": "🇳🇫", "NG": "🇳🇬", "NI": "🇳🇮", "NL": "🇳🇱", "NO": "🇳🇴", "NP": "🇳🇵", "NR": "🇳🇷",
    "NU": "🇳🇺", "NZ": "🇳🇿", "OM": "🇴🇲", "PA": "🇵🇦", "PE": "🇵🇪", "PF": "🇵🇫", "PG": "🇵🇬",
    "PH": "🇵🇭", "PK": "🇵🇰", "PL": "🇵🇱", "PM": "🇵🇲", "PN": "🇵🇳", "PR": "🇵🇷", "PS": "🇵🇸",
    "PT": "🇵🇹", "PW": "🇵🇼", "PY": "🇵🇾", "QA": "🇶🇦", "RE": "🇷🇪", "RO": "🇷🇴", "RS": "🇷🇸",
    "RU": "🇷🇺", "RW": "🇷🇼", "SA": "🇸🇦", "SB": "🇸🇧", "SC": "🇸🇨", "SD": "🇸🇩", "SE": "🇸🇪",
    "SG": "🇸🇬", "SH": "🇸🇭", "SI": "🇸🇮", "SJ": "🇸🇯", "SK": "🇸🇰", "SL": "🇸🇱", "SM": "🇸🇲",
    "SN": "🇸🇳", "SO": "🇸🇴", "SR": "🇸🇷", "SS": "🇸🇸", "ST": "🇸🇹", "SV": "🇸🇻", "SX": "🇸🇽",
    "SY": "🇸🇾", "SZ": "🇸🇿", "TC": "🇹🇨", "TD": "🇹🇩", "TF": "🇹🇫", "TG": "🇹🇬", "TH": "🇹🇭",
    "TJ": "🇹🇯", "TK": "🇹🇰", "TL": "🇹🇱", "TM": "🇹🇲", "TN": "🇹🇳", "TO": "🇹🇴", "TR": "🇹🇷",
    "TT": "🇹🇹", "TV": "🇹🇻", "TW": "🇹🇼", "TZ": "🇹🇿", "UA": "🇺🇦", "UG": "🇺🇬", "UM": "🇺🇲",
    "US": "🇺🇸", "UY": "🇺🇾", "UZ": "🇺🇿", "VA": "🇻🇦", "VC": "🇻🇨", "VE": "🇻🇪", "VG": "🇻🇬",
    "VI": "🇻🇮", "VN": "🇻🇳", "VU": "🇻🇺", "WF": "🇼🇫", "WS": "🇼🇸", "YE": "🇾🇪", "YT": "🇾🇹",
    "ZA": "🇿🇦", "ZM": "🇿🇲", "ZW": "🇿🇼",
}


def get_flag(country_code):
    if not country_code or country_code == "LOCAL":
        return "\U0001f3e0"
    return COUNTRY_FLAGS.get(country_code.upper(), "\U0001f3f3")


def close():
    global _city_reader, _asn_reader
    if _city_reader:
        try:
            _city_reader.close()
        except Exception:
            pass
        _city_reader = None
    if _asn_reader:
        try:
            _asn_reader.close()
        except Exception:
            pass
        _asn_reader = None