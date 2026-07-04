<p align="center">
  <img src="static/img/logo.png" alt="AegisGate" width="128" height="128">
</p>

<h1 align="center">AegisGate</h1>

<p align="center"><strong>Smart Network Security</strong></p>

<p align="center">
  All-in-one network security gateway for Linux — firewall, DNS filtering, intrusion prevention, VPN, QoS, and real-time threat intelligence in a single self-hosted appliance.
</p>

<p align="center">
  <a href="https://aegisgate.net">Website</a> &middot;
  <a href="https://aegisgate.net/demo/">Live Demo</a> &middot;
  <a href="https://aegisgate.net/docs.html">Docs</a>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="Platform: Linux" src="https://img.shields.io/badge/platform-Linux%20%7C%20Raspberry%20Pi-red">
  <img alt="Language: Python" src="https://img.shields.io/badge/language-Python%203-yellow">
  <img alt="Stack: Flask" src="https://img.shields.io/badge/stack-Flask%20%2B%20nftables%20%2B%20dnsmasq-green">
  <img alt="Status: Production" src="https://img.shields.io/badge/status-production-brightgreen">
</p>

<p align="center">
  Built and maintained by <a href="https://pentest-forge.com"><strong>PentestForge</strong></a> — AI-powered offensive security platform.
</p>

---

### What it does

AegisGate turns a single Linux box (bare-metal, Raspberry Pi, or VM) into a **complete perimeter security gateway**. Instead of stitching together Pi-hole + pfSense + OpenVPN + Suricata + CrowdSec by hand, AegisGate ships all of it — unified under one dashboard, one policy surface, one install command.

| Capability | Engine | What you get |
|------------|--------|--------------|
| 🌐 **DNS Firewall & AdBlock** | dnsmasq + RPZ blocklists | 680k+ domains blocked, per-client groups, service bundles (YouTube, TikTok, social), DoH bypass protection, SafeSearch, query log |
| 🧱 **Firewall & NAT** | nftables | Stateful filter/NAT/DNAT, IP blocklists, allowlist, CrowdSec drops, hostname-based rules, port forwarding, GeoIP filtering |
| 🛡️ **Intrusion Prevention** | Suricata (NFQ mode) | Inline IPS — 5 drop rules (SSH brute, C2, dir traversal, SQLi, XSS) + alert rules for floods/scans, period-filtered timeline |
| 🥊 **Auto-Ban & Threat Intel** | CrowdSec bouncer | Real-time IP banning from CrowdSec decisions, abuse signal ingestion, manual decision management from UI |
| 🔒 **WireGuard VPN** | WireGuard | Per-peer ACL (Internet / LAN / DMZ / custom), QR provisioning, bandwidth tracking, DNAT mirroring for wg0, anti-spoofing |
| 🌍 **GeoIP Filtering** | MaxMind GeoLite2 | Country-level IP allow/drop, GeoIP lookup for any IP, automatic mmdb download |
| ⚡ **QoS / Traffic Shaping** | tc (CAKE / fq_codel / HTB / HFSC) | Gaming / Streaming / Office / IoT / Custom profiles, built-in speed test with SVG gauges |
| 📡 **DHCP Server** | dnsmasq | Scopes, static leases, hostname tracking, per-client DNS policy tags, duplicate-IP protection |
| 📊 **Monitoring & Timeline** | Flask + SQLite + Chart.js | Real-time dashboard: drops, blocked IPs, CrowdSec, Suricata stats, SSH attempts, bandwidth, health score, risk scoring |
| 🕒 **Unified Event Timeline** | SQLite + cron ingest | All security events (firewall/DNS/IPS/CrowdSec) in one searchable, filterable, exportable timeline |
| 🌐 **Network Management** | ip / ifupdown | Interface roles, VLAN CRUD, static routes, conntrack viewer, Multi-WAN with failover and policy routing |

### Who it's for

- 🏠 **Home labs & homelabbers** — replace Pi-hole + router + VPN box with one Pi
- 🏢 **Small offices** — enterprise-grade perimeter defense without subscriptions
- 🔬 **Security researchers** — real Suricata IPS + full event timeline for analysis
- 🥧 **Raspberry Pi owners** — purpose-built for Pi 4/5, ARM-optimized
- 🛠️ **Self-hosters** — your network, your rules, no cloud, no telemetry

### Class of application

**Network security appliance / edge gateway** — AegisGate sits at the network edge between WAN and LAN, inspecting and filtering all traffic in and out. It is **not** a DNS-only blocker (like Pi-hole) or a router-only firewall (like OpenWrt). It is a **full perimeter stack** combining:

- DNS filtering + DHCP (Pi-hole-class)
- Stateful firewall + NAT (pfSense-class)
- Inline IPS (Suricata-class)
- Auto-remediation threat intel (CrowdSec-class)
- WireGuard VPN gateway (VPN appliance-class)
- QoS / traffic shaping (SQM-class)
- Real-time monitoring + timeline (SIEM-lite)

…all in one process, one dashboard, one policy surface — deployed with a single `curl | bash`.

---

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Screenshots](#screenshots)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgements](#acknowledgements)

---

## Features

### 🧱 Firewall (nftables)
- Dynamic NAT/filter/DNAT rules with per-service chains
- Source IP blocklists with automatic cron updates
- Rate limiting and SYN-flood protection
- GeoIP-based filtering
- DMZ / port-forwarding management from the UI

### 🌐 DNS & DHCP (dnsmasq)
- DNS blocklists (RPZ-style) with scheduled updates
- Per-client / per-group DNS policies
- SafeSearch enforcement
- Local DNS records and rewrites
- Upstream DNS configuration with health checks
- DHCP scopes, static leases, options, tags
- DNS access rules and schedules

### 🛡️ Intrusion Prevention (Suricata)
- NFQ-mode IPS integrated with nftables
- Alert ingestion and timeline correlation
- Period-based alert filtering (5m / 1h / 6h / 12h / 24h / 7d / all)

### 🥊 CrowdSec Integration
- Firewall bouncer for real-time IP banning
- Decision management from the dashboard
- Abuse signal ingestion into timeline

### 🌍 GeoIP (MaxMind)
- Country-level IP filtering
- GeoIP lookup for any IP address
- Automatic mmdb download (bundled archive or online)

### 📊 Bandwidth & QoS
- Real-time bandwidth collection (per-interface, per-VLAN)
- tc cake / fq_codel QoS management
- Built-in speed test
- Historical bandwidth charts

### 🔒 WireGuard VPN
- Peer management with QR codes
- Per-peer ACL (LAN / DMZ / Internet)
- DNAT mirroring for wg0 interface
- Anti-spoofing with wg0 bypass

### 🕒 Timeline & Forensics
- Unified event timeline (firewall drops, DNS blocks, IPS alerts, CrowdSec)
- Searchable, filterable, exportable
- Risk scoring and health monitoring

### 🎨 Dashboard
- Light / Dark theme toggle (persisted)
- Responsive Jinja2 templates
- Live-updating widgets (polling)
- Export to JSON / CSV

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    AegisGate Appliance               │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐    │
│  │ nftables │  │ dnsmasq  │  │    Suricata IPS  │    │
│  │ firewall │  │ DNS+DHCP │  │   (NFQ mode)     │    │
│  └────┬─────┘  └────┬─────┘  └────────┬─────────┘    │
│       │              │                  │             │
│  ┌────┴──────────────┴──────────────────┴──────┐      │
│  │              Flask Dashboard (app.py)       │      │
│  │   Gunicorn + 38 Python modules + Jinja2 UI │      │
│  └────┬──────────────┬──────────────────┬──────┘      │
│       │              │                  │             │
│  ┌────┴────┐   ┌─────┴─────┐  ┌────────┴────────┐    │
│  │ SQLite  │   │ CrowdSec │  │   WireGuard VPN │    │
│  │  (5 DBs)│   │ bouncer  │  │   (wg_manager)  │    │
│  └─────────┘   └──────────┘  └─────────────────┘    │
│                                                      │
│  ┌──────────────────────────────────────────────┐    │
│  │     systemd + cron + tmpfs ram-logs          │    │
│  └──────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────┘
         WAN (eth0)                LAN (eth1)
```

**Stack:** Python 3 / Flask / Gunicorn / Jinja2 / SQLite / nftables / dnsmasq / Suricata / CrowdSec / WireGuard / MaxMind GeoIP / systemd + cron

**Databases:**
- `dns.db` — DNS policies, lists, clients, groups, services, rewrites, upstreams, schedules, DHCP
- `timeline.db` — unified event timeline
- `bandwidth.db` — bandwidth samples
- `ip_blocklists.db` — IP blocklist sources and entries
- `multiwan.db` — Multi-WAN configuration

## Screenshots

Dashboard, firewall rules, DNS policies, VPN peers, bandwidth charts, and more — all manageable from the web UI. See the `static/img/` directory for the logo.

## Requirements

### Supported OS
- **Debian** 11 / 12
- **Ubuntu** 22.04 / 24.04
- **Raspbian** (Raspberry Pi 4 / 5)
- **RHEL family** — Rocky Linux 9, AlmaLinux 9, Fedora 39+

### Hardware
- **Minimum:** 2 CPU, 2 GB RAM, 8 GB storage, 2 NICs (WAN + LAN)
- **Recommended:** 4 CPU, 4 GB RAM, 32 GB SSD, 2+ NICs
- **ARM:** Raspberry Pi 4 (4 GB) or Pi 5

### Software (installed automatically)
- Python 3.9+
- nftables 0.9.6+
- dnsmasq 2.86+
- Suricata 6.0+
- CrowdSec + firewall-bouncer
- WireGuard tools
- Gunicorn
- MaxMind GeoLite2 mmdb (auto-downloaded)

## Installation

### Quick start

```bash
# 1. Clone the repository
git clone https://github.com/PentestForge/AegisGate.git
cd AegisGate

# 2. Copy and edit the configuration
cp config.example.json data/config.json
nano data/config.json  # set your WAN/LAN interfaces and IPs

# 3. Make sure GeoIP archive is present (optional — will download if absent)
# aegisgate-geoip.tar.gz with data/geoip/GeoLite2-City.mmdb + GeoLite2-ASN.mmdb

# 4. Run the installer (as root)
sudo bash install.sh
```

### Configuration

Edit `data/config.json` before running the installer:

```json
{
  "wan_interface": "eth0",
  "lan_interface": "eth1",
  "wan_ip": "203.0.113.10",
  "lan_ip": "192.168.1.1",
  "listen_addr": "192.168.1.1",
  "listen_port": 8080,
  "protected_ips": ["203.0.113.10", "1.1.1.1"],
  "protected_nets": ["192.168.1.0/24", "10.0.0.0/8"]
}
```

| Field | Description |
|-------|-------------|
| `wan_interface` | WAN NIC name (e.g. `eth0`) |
| `lan_interface` | LAN NIC name (e.g. `eth1`) |
| `wan_ip` | WAN static IP |
| `lan_ip` | LAN gateway IP |
| `listen_addr` | Dashboard bind address |
| `listen_port` | Dashboard port (default 8080) |
| `protected_ips` | IPs that should never be blocked |
| `protected_nets` | Networks that should never be blocked |

### Uninstall

```bash
sudo bash uninstall.sh
```

## Usage

After installation, the dashboard is available at:

```
http://<lan_ip>:8080/
```

Default credentials are generated during installation and stored in `data/auth.json`. **Change them immediately** from the dashboard (`Change Password` menu).

### Services

```bash
systemctl status nft-dashboard       # Flask dashboard (gunicorn)
systemctl status dnsmasq             # DNS + DHCP
systemctl status suricata            # IPS
systemctl status crowdsec            # threat intel
systemctl status aegisgate-restore   # post-boot state restore
systemctl status aegisgate-net-setup # NIC bring-up + fallback IP
```

### Cron jobs (auto-configured)

| Schedule | Job |
|----------|-----|
| every 1 min | `ingest_events.py` — firewall/DNS/CrowdSec event ingestion |
| every 1 min | `collect_bandwidth.py` — bandwidth sampling |
| every 2 min | `dns_log_import.py` — DNS query log import |
| every 5 min | `timeline_updater.py` — timeline aggregation |
| every 5 min | `auto-ban.py` — automatic IP banning |
| every 30 min | `ram-log-rotate.sh` — tmpfs log rotation |
| daily 04:17 | `dns_update_lists.py` — DNS blocklist refresh |
| daily 23:00 | `ip_blocklist_cron.py` — IP blocklist refresh |

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

## Acknowledgements

### PentestForge

**AegisGate** is developed and maintained by [**PentestForge**](https://pentest-forge.com) — an AI-powered offensive security platform that maps your attack surface, validates real risk, and delivers evidence-backed reports.

> *Stop guessing. Start knowing.* — [pentest-forge.com](https://pentest-forge.com)

PentestForge combines three layers of AI:
- **AI Report Analysis** — false-positive reduction and severity re-assessment
- **AI Security Chat** — 24/7 security consultant with context-aware answers
- **Deep Pentest AI** — auto-exploit validation and independent vulnerability discovery

### Third-party projects

AegisGate builds on outstanding open-source projects:

- [nftables](https://netfilter.org/projects/nftables/) — Linux firewall
- [dnsmasq](http://www.thekelleys.org.uk/dnsmasq/doc.html) — DNS forwarder + DHCP
- [Suricata](https://suricata.io/) — IDS/IPS engine
- [CrowdSec](https://www.crowdsec.net/) — collaborative threat intelligence
- [WireGuard](https://www.wireguard.com/) — fast modern VPN
- [MaxMind GeoLite2](https://dev.maxmind.com/geoip/geolite2-free-geolocation-data) — IP geolocation
- [Flask](https://flask.palletsprojects.com/) — Python web framework
- [Gunicorn](https://gunicorn.org/) — Python WSGI HTTP server
- [Chart.js](https://www.chartjs.org/) — JavaScript charting
- [P3TERX/GeoLite.mmdb](https://github.com/P3TERX/GeoLite.mmdb) — GeoLite2 release mirror

---

<p align="center">
  <strong>AegisGate</strong> — self-hosted network defense.<br>
  Built with ❤️ by <a href="https://pentest-forge.com">PentestForge</a>
</p>