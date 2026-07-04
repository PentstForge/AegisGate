#!/usr/bin/env python3
import os
import sqlite3
import time
import json
import subprocess
import re
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = "/opt/nft-dashboard/data/bandwidth.db"


def _get_interfaces():
    try:
        from modules.ifaces import get_wan, get_lan, get_vpn
        return {
            "internet": get_wan(),
            "lan": get_lan(),
            "vpn": get_vpn(),
        }
    except Exception:
        return {"internet": "eth0", "lan": "eth1", "vpn": "wg0"}


INTERFACES = _get_interfaces()

PORT_PROTOCOL_MAP = {
    80: "HTTP",
    443: "HTTPS",
    8080: "HTTP",
    8443: "HTTPS",
    20: "FTP",
    21: "FTP",
    25: "SMTP",
    465: "SMTP",
    587: "SMTP",
    53: "DNS",
    22: "SSH",
    51820: "VPN",
    123: "NTP",
    110: "POP3",
    143: "IMAP",
    993: "IMAPS",
    995: "POP3S",
    3389: "RDP",
    3306: "MySQL",
    5432: "PostgreSQL",
    6379: "Redis",
    8080: "HTTP-Alt",
}

LAN_NETS = ["192.168.1.", "10.0.0."]
VPN_NET = "10.0.0."

DB_CREATE = """
CREATE TABLE IF NOT EXISTS traffic_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    iface TEXT NOT NULL,
    rx_bytes INTEGER NOT NULL DEFAULT 0,
    tx_bytes INTEGER NOT NULL DEFAULT 0,
    rx_packets INTEGER NOT NULL DEFAULT 0,
    tx_packets INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ts_iface ON traffic_samples(ts, iface);

CREATE TABLE IF NOT EXISTS protocol_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    iface TEXT NOT NULL,
    protocol TEXT NOT NULL,
    connections INTEGER NOT NULL DEFAULT 0,
    rx_bytes INTEGER NOT NULL DEFAULT 0,
    tx_bytes INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_proto_ts_iface ON protocol_samples(ts, iface, protocol);
"""


def _get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(DB_CREATE)
    return conn


def read_proc_net_dev():
    ifaces = {}
    try:
        with open("/proc/net/dev", "r") as f:
            for line in f:
                line = line.strip()
                if ":" not in line:
                    continue
                iface, data = line.split(":", 1)
                iface = iface.strip()
                parts = data.split()
                if len(parts) >= 10:
                    ifaces[iface] = {
                        "rx_bytes": int(parts[0]),
                        "tx_bytes": int(parts[8]),
                        "rx_packets": int(parts[1]),
                        "tx_packets": int(parts[9]),
                    }
    except Exception:
        pass
    return ifaces


def _classify_conntrack_connections():
    proto_counts = defaultdict(lambda: {"connections": 0})
    try:
        r = subprocess.run(
            ["conntrack", "-L"],
            capture_output=True, text=True, timeout=10,
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            dport = None
            proto = None
            if "dport=" in line:
                m = re.search(r'dport=(\d+)', line)
                if m:
                    dport = int(m.group(1))
            if line.startswith("tcp"):
                proto = "tcp"
            elif line.startswith("udp"):
                proto = "udp"
            elif line.startswith("icmp"):
                proto = "icmp"
            if proto == "icmp":
                proto_counts["ICMP"]["connections"] += 1
                continue
            if dport and dport in PORT_PROTOCOL_MAP:
                label = PORT_PROTOCOL_MAP[dport]
            elif dport:
                label = "Other"
            else:
                label = "Other"
            proto_counts[label]["connections"] += 1
    except Exception:
        pass
    return dict(proto_counts)


def collect_sample():
    proc_data = read_proc_net_dev()
    conntrack_protos = _classify_conntrack_connections()
    conn = _get_db()
    now = time.time()
    try:
        for channel, iface in INTERFACES.items():
            if iface in proc_data:
                d = proc_data[iface]
                conn.execute(
                    "INSERT INTO traffic_samples (ts, iface, rx_bytes, tx_bytes, rx_packets, tx_packets) VALUES (?,?,?,?,?,?)",
                    (now, channel, d["rx_bytes"], d["tx_bytes"], d["rx_packets"], d["tx_packets"]),
                )
        for proto_name, vals in conntrack_protos.items():
            conn.execute(
                "INSERT INTO protocol_samples (ts, iface, protocol, connections, rx_bytes, tx_bytes) VALUES (?,?,?,?,?,?)",
                (now, "internet", proto_name, vals["connections"], 0, 0),
            )
        conn.commit()
    finally:
        conn.close()


def cleanup_old_samples(days=90):
    conn = _get_db()
    cutoff = time.time() - days * 86400
    try:
        conn.execute("DELETE FROM traffic_samples WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM protocol_samples WHERE ts < ?", (cutoff,))
        conn.commit()
    finally:
        conn.close()


def _delta_series(rows):
    if len(rows) < 2:
        return [], [], []
    timestamps = []
    rx_deltas = []
    tx_deltas = []
    prev = rows[0]
    for row in rows[1:]:
        dt = row["ts"] - prev["ts"]
        if dt <= 0:
            prev = row
            continue
        rx_delta = max(0, row["rx_bytes"] - prev["rx_bytes"])
        tx_delta = max(0, row["tx_bytes"] - prev["tx_bytes"])
        timestamps.append(row["ts"])
        rx_deltas.append(rx_delta / dt if dt > 0 else 0)
        tx_deltas.append(tx_delta / dt if dt > 0 else 0)
        prev = row
    return timestamps, rx_deltas, tx_deltas


def get_bandwidth_data(channel, period="1h"):
    period_map = {
        "1h": 3600, "12h": 43200, "24h": 86400,
        "7d": 604800, "30d": 2592000, "1y": 31536000,
    }
    period_sec = period_map.get(period, 3600)
    cutoff = time.time() - period_sec
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT ts, rx_bytes, tx_bytes, rx_packets, tx_packets FROM traffic_samples WHERE iface=? AND ts>? ORDER BY ts ASC",
            (channel, cutoff),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"labels": [], "rx": [], "tx": [], "total_rx": 0, "total_tx": 0,
                "avg_rx_bps": 0, "avg_tx_bps": 0, "peak_rx_bps": 0, "peak_tx_bps": 0}

    timestamps, rx_rates, tx_rates = _delta_series(rows)

    total_rx = 0
    total_tx = 0
    prev = rows[0]
    for row in rows[1:]:
        rx_delta = row["rx_bytes"] - prev["rx_bytes"]
        tx_delta = row["tx_bytes"] - prev["tx_bytes"]
        if rx_delta > 0:
            total_rx += rx_delta
        if tx_delta > 0:
            total_tx += tx_delta
        prev = row
    total_time = rows[-1]["ts"] - rows[0]["ts"] if len(rows) >= 2 else 1

    labels = []
    for ts in timestamps:
        dt = datetime.fromtimestamp(ts)
        if period_sec <= 86400:
            labels.append(dt.strftime("%H:%M"))
        elif period_sec <= 604800:
            labels.append(dt.strftime("%m/%d %H:%M"))
        elif period_sec <= 2592000:
            labels.append(dt.strftime("%m/%d"))
        else:
            labels.append(dt.strftime("%Y/%m"))

    max_points = 300
    if len(labels) > max_points:
        step = len(labels) // max_points
        labels = labels[::step]
        rx_rates = rx_rates[::step]
        tx_rates = tx_rates[::step]

    return {
        "labels": labels,
        "rx": rx_rates,
        "tx": tx_rates,
        "total_rx": total_rx,
        "total_tx": total_tx,
        "avg_rx_bps": total_rx / total_time if total_time > 0 else 0,
        "avg_tx_bps": total_tx / total_time if total_time > 0 else 0,
        "peak_rx_bps": max(rx_rates) if rx_rates else 0,
        "peak_tx_bps": max(tx_rates) if tx_rates else 0,
    }


def get_protocol_breakdown(channel, period="1h"):
    period_map = {
        "1h": 3600, "12h": 43200, "24h": 86400,
        "7d": 604800, "30d": 2592000, "1y": 31536000,
    }
    period_sec = period_map.get(period, 3600)
    cutoff = time.time() - period_sec
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT ts, protocol, connections, rx_bytes, tx_bytes FROM protocol_samples WHERE iface=? AND ts>? ORDER BY ts ASC",
            (channel, cutoff),
        ).fetchall()
    finally:
        conn.close()

    protos = defaultdict(lambda: {"connections": 0, "rx_bytes": 0, "tx_bytes": 0})
    for row in rows:
        p = row["protocol"]
        protos[p]["connections"] += row["connections"]
        protos[p]["rx_bytes"] += row["rx_bytes"]
        protos[p]["tx_bytes"] += row["tx_bytes"]

    total_conn = sum(v["connections"] for v in protos.values()) or 1
    result = []
    for pname, vals in sorted(protos.items(), key=lambda x: x[1]["connections"], reverse=True):
        result.append({
            "protocol": pname,
            "connections": vals["connections"],
            "rx_bytes": vals["rx_bytes"],
            "tx_bytes": vals["tx_bytes"],
            "total": vals["rx_bytes"] + vals["tx_bytes"],
            "pct": round(vals["connections"] / total_conn * 100, 1),
        })
    return result


def get_live_protocol_breakdown(channel):
    conntrack_protos = _classify_conntrack_connections()
    total_conn = sum(v["connections"] for v in conntrack_protos.values()) or 1
    result = []
    for pname, vals in sorted(conntrack_protos.items(), key=lambda x: x[1]["connections"], reverse=True):
        result.append({
            "protocol": pname,
            "connections": vals["connections"],
            "pct": round(vals["connections"] / total_conn * 100, 1),
        })
    return result


def get_bandwidth_summary_all():
    result = {}
    proc_data = read_proc_net_dev()
    for channel, iface in INTERFACES.items():
        if iface in proc_data:
            d = proc_data[iface]
            result[channel] = {
                "interface": iface,
                "rx_bytes": d["rx_bytes"],
                "tx_bytes": d["tx_bytes"],
                "rx_packets": d["rx_packets"],
                "tx_packets": d["tx_packets"],
            }
        else:
            result[channel] = {"interface": iface, "rx_bytes": 0, "tx_bytes": 0, "rx_packets": 0, "tx_packets": 0}
    return result


def format_bits_per_sec(bps):
    if bps < 1000:
        return f"{bps:.0f} bps"
    elif bps < 1_000_000:
        return f"{bps/1000:.1f} Kbps"
    elif bps < 1_000_000_000:
        return f"{bps/1_000_000:.1f} Mbps"
    else:
        return f"{bps/1_000_000_000:.2f} Gbps"


def format_bytes(b):
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b/1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b/1024/1024:.1f} MB"
    else:
        return f"{b/1024/1024/1024:.2f} GB"


def format_rate(bps):
    return format_bits_per_sec(bps * 8)


def get_sample_count(period="1h"):
    period_map = {
        "1h": 3600, "12h": 43200, "24h": 86400,
        "7d": 604800, "30d": 2592000, "1y": 31536000,
    }
    cutoff = time.time() - period_map.get(period, 3600)
    conn = _get_db()
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM traffic_samples WHERE ts > ?", (cutoff,)).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()