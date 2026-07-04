#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/nft-dashboard}"
SERVICE_NAME="${SERVICE_NAME:-nft-dashboard}"
RESTORE_SERVICE_NAME="${RESTORE_SERVICE_NAME:-aegisgate-restore}"
NET_SETUP_SERVICE="${NET_SETUP_SERVICE:-aegisgate-net-setup}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info() { printf "%b[INFO]%b %s\n" "$GREEN" "$NC" "$*"; }
warn() { printf "%b[WARN]%b %s\n" "$YELLOW" "$NC" "$*"; }
step() { printf "\n%b[STEP]%b %s\n" "$CYAN" "$NC" "$*"; }

confirm() {
    printf "%bWARNING: This will remove ALL AegisGate data, services, configs, and firewall rules.%b\n" "$RED" "$NC"
    printf "%bType 'yes' to proceed: %b" "$YELLOW" "$NC"
    read -r answer
    [[ "$answer" == "yes" ]] || { info "Aborted"; exit 0; }
}

confirm

step "Stopping services"
for svc in "$SERVICE_NAME" "$RESTORE_SERVICE_NAME" "$NET_SETUP_SERVICE" dnsmasq suricata; do
    systemctl stop "$svc" 2>/dev/null || true
    systemctl disable "$svc" 2>/dev/null || true
done

step "Removing systemd service files"
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
rm -f "/etc/systemd/system/${RESTORE_SERVICE_NAME}.service"
rm -f "/etc/systemd/system/${NET_SETUP_SERVICE}.service"
rm -f /etc/systemd/system/dnsmasq.service
rm -rf /etc/systemd/system/nftables.service.d
rm -rf /etc/systemd/system/suricata.service.d
systemctl daemon-reload

step "Removing nftables firewall rules"
nft delete table inet filter 2>/dev/null || true
nft delete table ip nat 2>/dev/null || true
rm -f /etc/nftables.conf
if command -v nft >/dev/null 2>&1; then
    nft -f /etc/nftables.conf 2>/dev/null || true
fi

step "Removing dnsmasq configs"
rm -f /etc/dnsmasq.conf
rm -f /etc/dnsmasq.d/aegisgate.conf
rm -f /etc/dnsmasq.d/aegisgate-local.conf
rm -f /etc/dnsmasq.d/aegisgate-upstream.conf
rm -f /etc/dnsmasq.d/aegisgate-dhcp.conf
rm -f /etc/dnsmasq.d/aegisgate-clients.conf
rm -f /etc/dnsmasq.d/aegisgate-blocklist.conf
rm -rf /etc/dnsmasq.d/aegisgate-blocklists
rm -rf /etc/dnsmasq.d/aegisgate-backup

step "Removing suricata configs (AegisGate-specific)"
rm -f /etc/suricata/rules/local-bridge.rules
rm -f /etc/suricata/threshold.config

step "Removing CrowdSec API port fix (revert 8180 -> 8080 if changed)"
if [[ -f /etc/crowdsec/config.yaml ]] && grep -q 'listen_uri:.*:8180' /etc/crowdsec/config.yaml 2>/dev/null; then
    sed -i 's/listen_uri:.*:8180/listen_uri: 127.0.0.1:8080/' /etc/crowdsec/config.yaml
    for f in /etc/crowdsec/local_api_credentials.yaml /etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml; do
        [[ -f "$f" ]] && sed -i 's|http://127.0.0.1:8180|http://127.0.0.1:8080|g' "$f"
    done
    info "CrowdSec API port reverted to 8080"
fi

step "Removing sysctl, modules, rsyslog configs"
rm -f /etc/sysctl.d/99-aegisgate.conf
rm -f /etc/modules-load.d/aegisgate.conf
rm -f /etc/rsyslog.d/99-nft-drops.conf
sysctl --system 2>/dev/null || true

step "Removing crontab entries"
tmp=$(mktemp)
crontab -l 2>/dev/null | sed '/# AEGISGATE BEGIN/,/# AEGISGATE END/d' > "$tmp" 2>/dev/null || true
crontab "$tmp" 2>/dev/null || true
rm -f "$tmp"

step "Removing tmpfs /var/log/ram from fstab"
sed -i '/\/var\/log\/ram.*tmpfs/d' /etc/fstab
systemctl stop rsyslog 2>/dev/null || true
umount /var/log/ram 2>/dev/null || true
rm -rf /var/log/ram
systemctl start rsyslog 2>/dev/null || true

step "Removing AegisGate app directory"
rm -rf "$APP_DIR"

step "Removing WireGuard config if created by AegisGate"
if [[ -f /etc/wireguard/wg0.conf ]] && grep -q 'AegisGate\|aegisgate' /etc/wireguard/wg0.conf 2>/dev/null; then
    rm -f /etc/wireguard/wg0.conf
    info "Removed wg0.conf (AegisGate-managed)"
else
    warn "Keeping /etc/wireguard/wg0.conf (not AegisGate-managed or not found)"
fi

step "Removing network config backups"
rm -f /etc/network/interfaces.aegisgate-backup 2>/dev/null || true

step "Restarting remaining services"
systemctl restart rsyslog 2>/dev/null || true
systemctl restart crowdsec 2>/dev/null || true
systemctl restart crowdsec-firewall-bouncer 2>/dev/null || true

printf "\n%bAegisGate uninstalled successfully.%b\n" "$GREEN" "$NC"
warn "Note: CrowdSec, Suricata, dnsmasq packages remain installed (use apt/dnf to remove)"
warn "Note: Network interfaces config was NOT reverted (manual restore needed)"
warn "Note: WireGuard peers may need manual cleanup"