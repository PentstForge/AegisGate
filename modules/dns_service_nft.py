#!/usr/bin/env python3
import ipaddress
import json
import socket
import subprocess

from .dns_db import get_db, get_setting, add_event
from .dns_policy import BLOCKED_SERVICES, parse_json_list, get_all_services


TABLE = "aegis_dns_services"
NFT = "/usr/sbin/nft"


def _run(args, input_text=None, timeout=20):
    try:
        r = subprocess.run([NFT] + args, input=input_text, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def _normalize_services(value):
    return [str(s).strip().lower() for s in parse_json_list(value) if str(s).strip()]


def _resolve_domains(domains):
    ipv4 = set()
    ipv6 = set()
    for domain in domains:
        try:
            infos = socket.getaddrinfo(domain, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            continue
        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if ip.version == 4:
                ipv4.add(str(ip))
            else:
                ipv6.add(str(ip))
    return sorted(ipv4), sorted(ipv6)


def _get_category_domains(categories_str):
    all_svcs = get_all_services()
    cats = [c.strip().lower() for c in (categories_str or "").split(",") if c.strip()]
    domains = set()
    for cat in cats:
        if cat in all_svcs:
            domains.update(all_svcs[cat])
    return sorted(domains)


def get_client_policies():
    global_services = set(_normalize_services(get_setting("global_blocked_services", [])))
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.ip, c.cidr, c.blocked_services_json, c.enabled,
                   p.id AS policy_id, p.name AS policy_name, p.blocked_services_json AS policy_services,
                   p.enabled AS policy_enabled, p.default_action, p.block_categories,
                   p.block_doh_bypass, p.block_external_dns,
                   p.safe_search_json, p.blocking_mode, p.blocked_response_ttl
            FROM dns_clients c
            LEFT JOIN dns_policies p ON c.policy_id=p.id
            WHERE c.enabled=1
            """
        ).fetchall()

        client_groups = {}
        for row in conn.execute(
            """
            SELECT cg.client_id, g.id AS group_id
            FROM dns_client_groups cg
            JOIN dns_groups g ON cg.group_id=g.id
            WHERE g.enabled=1
            """
        ).fetchall():
            cid = row["client_id"]
            if cid not in client_groups:
                client_groups[cid] = []
            client_groups[cid].append(row["group_id"])

        results = []
        for row in rows:
            client = dict(row)
            networks = []
            for key in ("ip", "cidr"):
                value = (client.get(key) or "").strip()
                if not value:
                    continue
                try:
                    networks.append(ipaddress.ip_network(value, strict=False))
                except ValueError:
                    continue
            if not networks:
                continue

            has_policy = client.get("policy_id") is not None and client.get("policy_enabled", 1)
            policy = {
                "id": client.get("policy_id"),
                "name": client.get("policy_name"),
                "default_action": (client.get("default_action") or "allow"),
                "blocked_services_json": client.get("policy_services") or "[]",
                "block_categories": client.get("block_categories") or "",
                "block_doh_bypass": client.get("block_doh_bypass", 0),
                "block_external_dns": client.get("block_external_dns", 0),
            }

            services = set(global_services)
            services.update(_normalize_services(client.get("blocked_services_json")))
            if has_policy:
                services.update(_normalize_services(policy["blocked_services_json"]))
            services = sorted(s for s in services if s in get_all_services())

            results.append({
                "client_id": client["id"],
                "client_name": client.get("name"),
                "networks": networks,
                "policy": policy if has_policy else None,
                "services": services,
                "groups": client_groups.get(client["id"], []),
            })
        return results
    finally:
        conn.close()


def get_client_service_blocks():
    all_services = get_all_services()
    global_services = set(_normalize_services(get_setting("global_blocked_services", [])))
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.ip, c.cidr, c.blocked_services_json, c.enabled,
                   p.blocked_services_json AS policy_services, p.enabled AS policy_enabled
            FROM dns_clients c
            LEFT JOIN dns_policies p ON c.policy_id=p.id
            WHERE c.enabled=1
            """
        ).fetchall()

        blocks = []
        for row in rows:
            client = dict(row)
            services = set(global_services)
            services.update(_normalize_services(client.get("blocked_services_json")))
            if client.get("policy_enabled", 1):
                services.update(_normalize_services(client.get("policy_services")))
            services = sorted(s for s in services if s in all_services)
            if not services:
                continue
            networks = []
            for key in ("ip", "cidr"):
                value = (client.get(key) or "").strip()
                if not value:
                    continue
                try:
                    networks.append(ipaddress.ip_network(value, strict=False))
                except ValueError:
                    continue
            if networks:
                domains = []
                for service in services:
                    domains.extend(all_services.get(service, []))
                blocks.append({
                    "client_id": client["id"],
                    "client_name": client.get("name"),
                    "networks": networks,
                    "services": services,
                    "domains": sorted(set(domains)),
                })
        return blocks
    finally:
        conn.close()


def apply_service_blocks():
    return apply_policy_nft()


def apply_policy_nft():
    clients = get_client_policies()
    _run(["delete", "table", "inet", TABLE], timeout=5)

    if not clients:
        add_event("service_blocks_applied", "info", "dns_service_nft", "No client policies active")
        return True, "No client policies active", []

    GATEWAY_V4 = "192.168.1.1"
    VPN_GW_V4 = "10.0.0.1"

    forward_rules = []
    input_rules = []
    applied = []

    for client in clients:
        nets_v4 = []
        nets_v6 = []
        for net in client["networks"]:
            if net.version == 4:
                nets_v4.append(str(net))
            else:
                nets_v6.append(str(net))
        if not nets_v4 and not nets_v6:
            continue

        policy = client.get("policy")
        cid = client["client_id"]
        cname = client.get("client_name") or f"client{cid}"

        # 1. Service blocks (from blocked_services + global)
        if client["services"]:
            all_svcs = get_all_services()
            domains = set()
            for svc in client["services"]:
                domains.update(all_svcs.get(svc, []))
            if domains:
                ipv4, ipv6 = _resolve_domains(sorted(domains))
                if ipv4:
                    for net in nets_v4:
                        forward_rules.append(
                            f"ip saddr {net} ip daddr {{ {', '.join(ipv4)} }} drop comment \"AegisDNS svc-block {cname}\""
                        )
                if ipv6:
                    for net in nets_v6:
                        forward_rules.append(
                            f"ip6 saddr {net} ip6 daddr {{ {', '.join(ipv6)} }} drop comment \"AegisDNS svc-block {cname}\""
                        )
                applied.append({"client_id": cid, "client_name": cname, "type": "service_block", "services": client["services"], "domains": len(domains)})

        if not policy:
            continue

        # 2. block_external_dns: drop DNS to non-gateway IPs
        if policy.get("block_external_dns"):
            for net in nets_v4:
                forward_rules.append(
                    f"ip saddr {net} udp dport 53 ip daddr != {{ {GATEWAY_V4}, {VPN_GW_V4} }} drop comment \"AegisDNS ext-dns-block {cname}\""
                )
                forward_rules.append(
                    f"ip saddr {net} tcp dport 53 ip daddr != {{ {GATEWAY_V4}, {VPN_GW_V4} }} drop comment \"AegisDNS ext-dns-block {cname}\""
                )
            applied.append({"client_id": cid, "client_name": cname, "type": "ext_dns_block"})

        # 3. block_doh_bypass: drop DoH (443) and DoT (853) to known providers
        if policy.get("block_doh_bypass"):
            doh_domains = ["dns.google", "dns.google.com", "cloudflare-dns.com", "dns.quad9.net",
                           "doh.opendns.com", "dns.adguard.com", "security.cloudflare-dns.com",
                           "family.cloudflare-dns.com", "doh.cleanbrowsing.org", "doh.mullvad.net"]
            doh_ipv4, doh_ipv6 = _resolve_domains(doh_domains)
            if doh_ipv4:
                for net in nets_v4:
                    forward_rules.append(
                        f"ip saddr {net} ip daddr {{ {', '.join(doh_ipv4)} }} tcp dport {{ 443, 853 }} drop comment \"AegisDNS doh-block {cname}\""
                    )
                for dip in doh_ipv4:
                    input_rules.append(
                        f"ip saddr {net} ip daddr {dip} tcp dport {{ 443, 853 }} drop comment \"AegisDNS doh-input {cname}\""
                    )
            applied.append({"client_id": cid, "client_name": cname, "type": "doh_block"})

    # 4. Access rules: deny disallowed clients (global, not per-client)
    try:
        conn = get_db()
        for ar in conn.execute("SELECT value FROM dns_access_rules WHERE type='disallowed_client' AND enabled=1").fetchall():
            addr = (ar["value"] or "").strip()
            if not addr:
                continue
            try:
                net = ipaddress.ip_network(addr, strict=False)
                if net.version == 4:
                    forward_rules.append(f"ip saddr {net} drop comment \"AegisDNS access-deny\"")
                    input_rules.append(f"ip saddr {net} drop comment \"AegisDNS access-deny\"")
            except ValueError:
                pass
        conn.close()
    except Exception:
        pass

    if not forward_rules and not input_rules:
        add_event("service_blocks_applied", "info", "dns_service_nft", "No nft rules needed")
        return True, "No nft rules needed", []

    lines = [
        f"table inet {TABLE} {{",
        "  chain forward {",
        "    type filter hook forward priority -7; policy accept;",
    ]
    for rule in forward_rules:
        lines.append(f"    {rule}")
    lines.append("  }")
    lines.append("  chain input {")
    lines.append("    type filter hook input priority -7; policy accept;")
    for rule in input_rules:
        lines.append(f"    {rule}")
    lines.append("  }")
    lines.append("}")
    lines.append("")

    ok, out, err = _run(["-f", "-"], input_text="\n".join(lines), timeout=30)
    if not ok:
        add_event("service_blocks_failed", "high", "dns_service_nft", err or out, {"applied": applied})
        return False, err or out or "nft failed", applied
    add_event("service_blocks_applied", "info", "dns_service_nft", f"Applied policy rules for {len(applied)} entries", {"applied": applied})
    return True, f"Applied policy rules for {len(applied)} entries", applied