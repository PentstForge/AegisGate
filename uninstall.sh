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

[[ "${EUID}" -eq 0 ]] || { warn "Run as root"; exit 1; }
printf "%bWARNING: This removes all AegisGate services, configuration, and runtime data.%b\n" "$RED" "$NC"
printf "%bType 'yes' to proceed: %b" "$YELLOW" "$NC"
read -r answer
[[ "$answer" == "yes" ]] || { info "Aborted"; exit 0; }

step "Stopping AegisGate services"
for service in \
    aegisgate-health qos-setup vlan-setup nftables-restore-wg \
    "$SERVICE_NAME" "$RESTORE_SERVICE_NAME" "$NET_SETUP_SERVICE" dnsmasq wg-quick@wg0; do
    systemctl disable --now "$service" >/dev/null 2>&1 || true
done

step "Restoring pre-install system configuration"
shopt -s nullglob
backups=("$APP_DIR"/backups/install_*)
if [[ "${#backups[@]}" -gt 0 ]]; then
    oldest_backup="${backups[0]}"
    if [[ -f "$oldest_backup/system/missing-before-install.txt" ]]; then
        while IFS= read -r path; do
            [[ -n "$path" ]] && rm -rf "$path"
        done < "$oldest_backup/system/missing-before-install.txt"
    fi
    cp -a "$oldest_backup/system/etc/." /etc/ 2>/dev/null || true
    if [[ -s "$oldest_backup/system/crontab.root" ]]; then
        crontab "$oldest_backup/system/crontab.root"
    else
        current_cron="$(mktemp)"
        crontab -l 2>/dev/null | sed '/# AEGISGATE BEGIN/,/# AEGISGATE END/d' > "$current_cron" || true
        crontab "$current_cron" 2>/dev/null || true
        rm -f "$current_cron"
    fi
    systemctl daemon-reload
    if [[ -f "$oldest_backup/system/service-state.txt" ]]; then
        while IFS='|' read -r service_name enabled_state active_state; do
            [[ -n "$service_name" ]] || continue
            if [[ "$enabled_state" == "enabled" ]]; then
                systemctl enable "$service_name" >/dev/null 2>&1 || true
            else
                systemctl disable "$service_name" >/dev/null 2>&1 || true
            fi
            if [[ "$active_state" == "active" ]]; then
                systemctl restart "$service_name" >/dev/null 2>&1 || true
            else
                systemctl stop "$service_name" >/dev/null 2>&1 || true
            fi
        done < "$oldest_backup/system/service-state.txt"
    fi
    if command -v nmcli >/dev/null 2>&1; then
        nmcli connection reload >/dev/null 2>&1 || true
    fi
    info "Restored original files from $oldest_backup"
else
    warn "No installer backup found; removing only known AegisGate files"
    rm -f \
        "/etc/systemd/system/${SERVICE_NAME}.service" \
        "/etc/systemd/system/${RESTORE_SERVICE_NAME}.service" \
        /etc/systemd/system/nft-dashboard-restore.service \
        "/etc/systemd/system/${NET_SETUP_SERVICE}.service" \
        /etc/systemd/system/aegisgate-health.service \
        /etc/systemd/system/qos-setup.service \
        /etc/systemd/system/vlan-setup.service \
        /etc/systemd/system/nftables-restore-wg.service \
        /etc/systemd/system/nftables.service.d/override.conf \
        /etc/systemd/system/suricata.service.d/override.conf \
        /etc/systemd/system/wg-quick@wg0.service.d/aegisgate.conf \
        /etc/NetworkManager/conf.d/90-aegisgate.conf \
        /etc/NetworkManager/system-connections/aegisgate-wan.nmconnection \
        /etc/NetworkManager/system-connections/aegisgate-lan.nmconnection \
        /etc/crowdsec/acquis.d/aegisgate.yaml \
        /etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml.local \
        /etc/rsyslog.d/30-nft-drops.conf \
        /etc/logrotate.d/nft-drops \
        /etc/logrotate.d/ram-logs \
        /etc/sysctl.d/99-aegisgate.conf \
        /etc/sysctl.d/99-aegisgate-memory.conf \
        /etc/modules-load.d/aegisgate.conf \
        /etc/udev/rules.d/99-realtek-lan.rules
    current_cron="$(mktemp)"
    crontab -l 2>/dev/null | sed '/# AEGISGATE BEGIN/,/# AEGISGATE END/d' > "$current_cron" || true
    crontab "$current_cron" 2>/dev/null || true
    rm -f "$current_cron"
    sed -i '/\/var\/log\/ram.*tmpfs/d' /etc/fstab
    if command -v nmcli >/dev/null 2>&1; then
        nmcli connection delete aegisgate-wan >/dev/null 2>&1 || true
        nmcli connection delete aegisgate-lan >/dev/null 2>&1 || true
    fi
fi

step "Removing AegisGate runtime state"
if command -v nft >/dev/null 2>&1; then
    nft delete table inet filter >/dev/null 2>&1 || true
    nft delete table ip nat >/dev/null 2>&1 || true
fi
rm -rf /etc/aegisgate
umount /var/log/ram >/dev/null 2>&1 || true
rm -rf /var/log/ram
rm -rf "$APP_DIR"

systemctl daemon-reload
sysctl --system >/dev/null 2>&1 || true

printf "\n%bAegisGate uninstalled successfully.%b\n" "$GREEN" "$NC"
warn "Installed OS packages were intentionally retained."
