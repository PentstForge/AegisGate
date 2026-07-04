#!/usr/bin/env bash
set -euo pipefail

NFT=${NFT:-/usr/sbin/nft}
CONF=/tmp/aegisgate-safe-inet-filter.nft
NAT_CONF=/tmp/aegisgate-safe-ip-nat.nft

sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true

if ! "$NFT" list table ip nat >/dev/null 2>&1; then
cat > "$NAT_CONF" <<'NATCONF'
table ip nat {
    chain PREROUTING {
        type nat hook prerouting priority dstnat; policy accept;
    }

    chain INPUT {
        type nat hook input priority srcnat; policy accept;
    }

    chain OUTPUT {
        type nat hook output priority dstnat; policy accept;
    }

    chain POSTROUTING {
        type nat hook postrouting priority srcnat; policy accept;
        oifname "eth0" masquerade
    }
}
NATCONF
    "$NFT" -c -f "$NAT_CONF"
    "$NFT" -f "$NAT_CONF"
fi

if ! "$NFT" list table ip filter >/dev/null 2>&1; then
"$NFT" -f - <<'FILTERCONF'
table ip filter {
    chain INPUT {
        type filter hook input priority filter; policy accept;
    }

    chain FORWARD {
        type filter hook forward priority filter; policy accept;
        iifname "eth1" oifname "eth0" accept
        iifname "eth0" oifname "eth1" ct state related,established accept
        iifname "eth1" oifname "eth1" accept
    }

    chain OUTPUT {
        type filter hook output priority filter; policy accept;
    }
}
FILTERCONF
fi

cat > "$CONF" <<'NFTCONF'
table inet filter {
    set blacklist_ipv4 {
        type ipv4_addr
        flags interval,timeout
        auto-merge
    }

    set crowdsec-blacklists {
        type ipv4_addr
        flags interval,timeout
        auto-merge
    }

    set blacklist_ipv6 {
        type ipv6_addr
        flags interval,timeout
        auto-merge
    }

    set crowdsec6-blacklists {
        type ipv6_addr
        flags interval,timeout
        auto-merge
    }

    set allowlist_ipv4 {
        type ipv4_addr
        flags interval
        auto-merge
    }

    set allowlist_ipv6 {
        type ipv6_addr
        flags interval
        auto-merge
    }

    set lan_trusted {
        type ipv4_addr
        flags interval
        auto-merge
        elements = { 203.0.113.0/24, 192.168.1.0/24, 10.0.0.0/24 }
    }

    set ipbl_ipv4 {
        type ipv4_addr
        flags interval,timeout
        auto-merge
    }

    set ipbl_ipv6 {
        type ipv6_addr
        flags interval,timeout
        auto-merge
    }

    set rate_limit_abuse {
        type ipv4_addr
        flags interval
        auto-merge
    }

    set rfc1918_ipv4 {
        type ipv4_addr
        flags interval
        auto-merge
        elements = { 10.0.0.0/8, 100.64.0.0/10, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16, 192.0.0.0/24, 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 224.0.0.0/4, 240.0.0.0/4 }
    }

    chain input {
        type filter hook input priority filter; policy drop;
        ct state established,related accept
        iifname "lo" accept
        ip saddr 192.168.1.0/24 ip daddr 192.168.1.1 tcp dport 8080 accept
        ip saddr @lan_trusted accept
        udp sport 68 udp dport 67 accept
        ip saddr @ipbl_ipv4 ct state new log prefix "DROP_IPBL_SADDR_INPUT: " drop
        ip6 saddr @ipbl_ipv6 ct state new log prefix "DROP_IPBL_SADDR_INPUT: " drop
        ip saddr @blacklist_ipv4 ct state new log prefix "DROP_CROWDSEC_INPUT: " drop
        ip6 saddr @blacklist_ipv6 ct state new log prefix "DROP_CROWDSEC_INPUT6: " drop
        ip saddr @crowdsec-blacklists ct state new log prefix "DROP_CROWDSEC_INPUT: " drop
        ip6 saddr @crowdsec6-blacklists ct state new log prefix "DROP_CROWDSEC_INPUT6: " drop
        ip daddr 192.168.1.1 tcp dport 8080 accept
        ip saddr @allowlist_ipv4 accept
        ip6 saddr @allowlist_ipv6 accept
        ip saddr @lan_trusted accept
        iifname "eth1" accept
        icmp type { echo-request, echo-reply, destination-unreachable, time-exceeded } accept
        ip protocol igmp accept
        udp dport { 53, 67, 68, 51820 } accept
        tcp dport 22 limit rate over 3/minute burst 5 packets log prefix "DROP_SSH_BRUTE: " drop
        iifname "eth0" ct state new tcp dport { 22, 80, 443, 222, 3000, 3331, 5194 } queue flags bypass to 0
        tcp dport { 22, 80, 443, 222, 3000, 3331, 5194 } accept
        ct state invalid log prefix "DROP_INVALID_INPUT: " drop
        ct state new log prefix "DROP_DEFAULT_IN: " drop
    }

    chain wg_acl {
    }

    chain forward {
        type filter hook forward priority filter; policy drop;
        ct state established,related accept
        iifname "eth1" oifname "eth0" accept
        iifname "eth0" oifname "eth1" ct state established,related accept
        iifname "wg0" jump wg_acl
        iifname "wg0" accept
        oifname "wg0" accept
        ct state invalid log prefix "DROP_INVALID_FWD: " drop

        ip saddr @lan_trusted accept

        ip saddr @ipbl_ipv4 ct state new log prefix "DROP_IPBL_SADDR_FORWARD: " drop
        ip6 saddr @ipbl_ipv6 ct state new log prefix "DROP_IPBL_SADDR_FORWARD: " drop
        ip saddr @blacklist_ipv4 ct state new log prefix "DROP_CROWDSEC_FWD: " drop
        ip6 saddr @blacklist_ipv6 ct state new log prefix "DROP_CROWDSEC_FWD6: " drop
        ip saddr @crowdsec-blacklists ct state new log prefix "DROP_CROWDSEC_FWD: " drop
        ip6 saddr @crowdsec6-blacklists ct state new log prefix "DROP_CROWDSEC_FWD6: " drop
        ct state new ct status dnat queue flags bypass to 0
        ct status dnat accept
        ip daddr @ipbl_ipv4 ct state new log prefix "DROP_IPBL_DADDR_FORWARD: " drop
        ip6 daddr @ipbl_ipv6 ct state new log prefix "DROP_IPBL_DADDR_FORWARD: " drop

        meta l4proto { tcp, udp } ct state new ip saddr @rate_limit_abuse limit rate over 50/second burst 5 packets log prefix "DROP_ABUSE_FWD: " drop
        ip protocol icmp icmp type echo-request ip saddr @rate_limit_abuse limit rate over 5/second burst 5 packets log prefix "DROP_ABUSE_ICMP: " drop
        tcp flags & (fin|syn|rst|psh|ack|urg) == 0x0 ct state new log prefix "DROP_NULL_FLAGS: " drop
        tcp flags syn ct state new limit rate over 500/second burst 100 packets log prefix "DROP_SYN_FLOOD: " drop
        ip protocol icmp icmp type echo-request limit rate over 20/second burst 10 packets log prefix "DROP_ICMP_FLOOD: " drop
        ip saddr @rfc1918_ipv4 ip daddr != @rfc1918_ipv4 ip saddr != @lan_trusted log prefix "DROP_SPOOF_RFC1918: " drop

        jump forward_ratelimit
        jump forward_antispoof
        jump forward_badtcp

        ip saddr @allowlist_ipv4 accept
        ip daddr @allowlist_ipv4 accept
        ip6 saddr @allowlist_ipv6 accept
        ip6 daddr @allowlist_ipv6 accept
        ip saddr @lan_trusted accept
        ip daddr @lan_trusted accept
        ip protocol igmp accept
        icmp type { echo-request, echo-reply, destination-unreachable, time-exceeded } accept
        ct state new log prefix "DROP_DEFAULT_FWD: " drop
    }

    chain forward_ratelimit {
        tcp flags syn ct state new limit rate over 500/second burst 100 packets jump mark_syn_flood
        ip protocol icmp icmp type echo-request limit rate over 20/second burst 10 packets jump mark_icmp_flood
        meta l4proto { tcp, udp } ct state new ip saddr @rate_limit_abuse limit rate over 50/second burst 5 packets log prefix "DROP_ABUSE_FWD: " drop
        ip protocol icmp icmp type echo-request ip saddr @rate_limit_abuse limit rate over 5/second burst 5 packets log prefix "DROP_ABUSE_ICMP: " drop
    }

    chain forward_antispoof {
        ip saddr @rfc1918_ipv4 ip daddr != @rfc1918_ipv4 ip saddr != @lan_trusted log prefix "DROP_SPOOF_RFC1918: " drop
    }

    chain forward_badtcp {
        tcp flags & (fin|syn|rst|psh|ack|urg) == 0x0 ct state new log prefix "DROP_NULL_FLAGS: " drop
    }

    chain mark_syn_flood {
        log prefix "DROP_SYN_FLOOD: " drop
    }

    chain mark_icmp_flood {
        log prefix "DROP_ICMP_FLOOD: " drop
    }

    chain output {
        type filter hook output priority filter; policy accept;
    }
}
NFTCONF

"$NFT" -c -f "$CONF"
if "$NFT" list table inet filter >/dev/null 2>&1; then
    "$NFT" delete table inet filter
fi
"$NFT" -f "$CONF"

# Restore IP blocklists from nft files
cd /opt/nft-dashboard 2>/dev/null && python3 -c "from modules.ip_blocklists import restore_ipbl; restore_ipbl()" 2>/dev/null || true

# Restore aegis_dns_services nft table
python3 /opt/nft-dashboard/scripts/dns_apply_service_blocks.py 2>/dev/null || true

ping -c 1 -W 2 google.com >/dev/null
printf 'AegisGate safe nft restore applied, internet OK\n'
