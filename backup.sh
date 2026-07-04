#!/bin/bash
set -euo pipefail

APP_DIR="/opt/nft-dashboard"
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="${1:-$APP_DIR/backups/backup_${TS}}"
SYSTEM_BACKUP_DIR="${BACKUP_DIR}/system"

mkdir -p "$BACKUP_DIR/system" "$BACKUP_DIR/modules" "$BACKUP_DIR/templates" \
    "$BACKUP_DIR/static" "$BACKUP_DIR/scripts" "$BACKUP_DIR/data/wireguard" \
    "$BACKUP_DIR/data/geoip" "$BACKUP_DIR/data/reports"

echo "[*] Creating full backup: $BACKUP_DIR"

# --- App root files ---
for f in app.py wsgi.py auto-ban.py restore-state.py timeline_updater.py log-truncate.sh; do
    cp "$APP_DIR/$f" "$BACKUP_DIR/" 2>/dev/null || true
done

# --- Modules ---
for f in "$APP_DIR"/modules/*.py; do
    [[ -f "$f" ]] && cp "$f" "$BACKUP_DIR/modules/"
done

# --- Templates ---
for f in "$APP_DIR"/templates/*.html; do
    [[ -f "$f" ]] && cp "$f" "$BACKUP_DIR/templates/"
done

# --- Static ---
for f in "$APP_DIR"/static/*; do
    [[ -f "$f" ]] && cp "$f" "$BACKUP_DIR/static/"
done

# --- Scripts ---
for f in "$APP_DIR"/scripts/*; do
    [[ -f "$f" ]] && cp "$f" "$BACKUP_DIR/scripts/"
done

# --- Data (except large DB and GeoIP) ---
for f in allowlist.json auth.json auto-ban-config.json config.json ifaces.json policy.json qos.json rules_state.json env; do
    cp "$APP_DIR/data/$f" "$BACKUP_DIR/data/" 2>/dev/null || true
done
cp "$APP_DIR/data/wireguard/"*.json "$BACKUP_DIR/data/wireguard/" 2>/dev/null || true
cp "$APP_DIR/data/geoip/"*.mmdb "$BACKUP_DIR/data/geoip/" 2>/dev/null || true
cp "$APP_DIR/data/reports/"*.html "$BACKUP_DIR/data/reports/" 2>/dev/null || true
cp "$APP_DIR/data/bandwidth.db" "$BACKUP_DIR/data/" 2>/dev/null || true

# --- System config files ---
for path in \
    /etc/nftables.conf \
    /etc/fstab \
    /etc/rsyslog.d/99-nft-drops.conf \
    /etc/sysctl.d/99-aegisgate.conf \
    /etc/systemd/system/nft-dashboard.service \
    /etc/systemd/system/aegisgate-restore.service \
    /etc/systemd/system/nft-dashboard-restore.service \
    /etc/systemd/system/nftables.service.d/override.conf \
    /etc/suricata/suricata.yaml \
    /etc/suricata/threshold.config \
    /etc/suricata/rules/local-bridge.rules \
    /etc/modules-load.d/aegisgate.conf; do
    cp "$path" "$SYSTEM_BACKUP_DIR/" 2>/dev/null || true
done
crontab -l > "$SYSTEM_BACKUP_DIR/crontab.root" 2>/dev/null || true

# --- Generate rollback script ---
cat > "$BACKUP_DIR/rollback.sh" <<'ROLLBACK_EOF'
#!/bin/bash
set -euo pipefail
APP_DIR="/opt/nft-dashboard"
BACKUP_DIR="$(cd "$(dirname "$0")" && pwd)"
SYSTEM_BACKUP_DIR="${BACKUP_DIR}/system"

echo "[*] Rolling back from: $BACKUP_DIR"

# Stop service
systemctl stop nft-dashboard 2>/dev/null || true

# Restore app root files
for f in app.py wsgi.py auto-ban.py restore-state.py timeline_updater.py log-truncate.sh; do
    if [[ -f "$BACKUP_DIR/$f" ]]; then
        cp "$BACKUP_DIR/$f" "$APP_DIR/$f"
    fi
done

# Restore modules
if [[ -d "$BACKUP_DIR/modules" ]]; then
    for f in "$BACKUP_DIR/modules/"*.py; do
        [[ -f "$f" ]] && cp "$f" "$APP_DIR/modules/"
    done
fi

# Restore templates
if [[ -d "$BACKUP_DIR/templates" ]]; then
    for f in "$BACKUP_DIR/templates/"*.html; do
        [[ -f "$f" ]] && cp "$f" "$APP_DIR/templates/"
    done
fi

# Restore static
if [[ -d "$BACKUP_DIR/static" ]]; then
    for f in "$BACKUP_DIR/static/"*; do
        [[ -f "$f" ]] && cp "$f" "$APP_DIR/static/"
    done
fi

# Restore scripts
if [[ -d "$BACKUP_DIR/scripts" ]]; then
    for f in "$BACKUP_DIR/scripts/"*; do
        [[ -f "$f" ]] && cp "$f" "$APP_DIR/scripts/"
    done
fi

# Restore data
if [[ -d "$BACKUP_DIR/data" ]]; then
    for f in allowlist.json auth.json auto-ban-config.json config.json ifaces.json policy.json qos.json rules_state.json env bandwidth.db; do
        if [[ -f "$BACKUP_DIR/data/$f" ]]; then
            cp "$BACKUP_DIR/data/$f" "$APP_DIR/data/$f"
        fi
    done
    cp "$BACKUP_DIR/data/wireguard/"*.json "$APP_DIR/data/wireguard/" 2>/dev/null || true
    cp "$BACKUP_DIR/data/geoip/"*.mmdb "$APP_DIR/data/geoip/" 2>/dev/null || true
    cp "$BACKUP_DIR/data/reports/"*.html "$APP_DIR/data/reports/" 2>/dev/null || true
fi

# Restore system config
if [[ -d "$SYSTEM_BACKUP_DIR" ]]; then
    for path in \
        /etc/nftables.conf \
        /etc/fstab \
        /etc/rsyslog.d/99-nft-drops.conf \
        /etc/sysctl.d/99-aegisgate.conf \
        /etc/systemd/system/nft-dashboard.service \
        /etc/systemd/system/aegisgate-restore.service \
        /etc/systemd/system/nft-dashboard-restore.service \
        /etc/suricata/suricata.yaml \
        /etc/suricata/threshold.config \
        /etc/suricata/rules/local-bridge.rules \
        /etc/modules-load.d/aegisgate.conf; do
        fname="$(basename "$path")"
        if [[ -f "$SYSTEM_BACKUP_DIR/$fname" ]]; then
            mkdir -p "$(dirname "$path")"
            cp "$SYSTEM_BACKUP_DIR/$fname" "$path"
        fi
    done

    # Restore nftables override
    if [[ -f "$SYSTEM_BACKUP_DIR/override.conf" ]]; then
        mkdir -p /etc/systemd/system/nftables.service.d/
        cp "$SYSTEM_BACKUP_DIR/override.conf" /etc/systemd/system/nftables.service.d/override.conf
    fi

    # Restore crontab
    if [[ -f "$SYSTEM_BACKUP_DIR/crontab.root" ]]; then
        crontab "$SYSTEM_BACKUP_DIR/crontab.root"
    fi

    systemctl daemon-reload
    sysctl --system >/dev/null 2>&1 || true
fi

rm -rf "$APP_DIR/modules/__pycache__"
systemctl restart nft-dashboard
echo "[OK] Rollback complete. Dashboard restarted."
ROLLBACK_EOF

chmod +x "$BACKUP_DIR/rollback.sh"

# --- Summary ---
echo ""
echo "========================================="
echo "  Backup complete"
echo "========================================="
echo "  Location:  $BACKUP_DIR"
echo "  Rollback:  bash $BACKUP_DIR/rollback.sh"
echo ""
echo "  Contents:"
echo "    App files:   $(find "$BACKUP_DIR" -maxdepth 1 -type f | wc -l) root files"
echo "    Modules:     $(find "$BACKUP_DIR/modules" -type f 2>/dev/null | wc -l) files"
echo "    Templates:   $(find "$BACKUP_DIR/templates" -type f 2>/dev/null | wc -l) files"
echo "    Static:      $(find "$BACKUP_DIR/static" -type f 2>/dev/null | wc -l) files"
echo "    Scripts:     $(find "$BACKUP_DIR/scripts" -type f 2>/dev/null | wc -l) files"
echo "    Data:        $(find "$BACKUP_DIR/data" -type f 2>/dev/null | wc -l) files"
echo "    System:      $(find "$SYSTEM_BACKUP_DIR" -type f 2>/dev/null | wc -l) files"
echo "========================================="