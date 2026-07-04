#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_INSTALL="$SCRIPT_DIR/install.base.sh"
INSTALL_OUT="$SCRIPT_DIR/install.sh"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

# Step 1: Create base install.sh (without deploy_app_files) from current install.sh
# Remove any existing deploy_app_files function block
python3 -c "
import re
with open('$SCRIPT_DIR/install.sh', 'r') as f:
    content = f.read()

# Remove deploy_app_files() { ... } block
content = re.sub(
    r'\ndeploy_app_files\(\) \{.*?\n\}',
    '',
    content,
    flags=re.DOTALL
)

# Remove duplicate deploy_app_files call in main() if exists
lines = content.split('\n')
new_lines = []
seen_deploy = False
for line in lines:
    stripped = line.strip()
    if stripped == 'deploy_app_files':
        if seen_deploy:
            continue
        seen_deploy = True
    new_lines.append(line)

with open('$BASE_INSTALL', 'w') as f:
    f.write('\n'.join(new_lines))
print(f'Base install.sh: {len(new_lines)} lines')
"

# Step 2: Create tar.gz archive of project files
ARCHIVE="$TMPDIR/app.tar.gz"
B64="$TMPDIR/app.b64"

tar czf "$ARCHIVE" \
    -C "$SCRIPT_DIR" \
    --exclude='data' \
    --exclude='backups' \
    --exclude='backup_*' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='*.db' \
    --exclude='install.sh' \
    --exclude='install.base.sh' \
    --exclude='build_installer.sh' \
    --exclude='serve-demo.py' \
    --exclude='docker-compose.demo.yml' \
    --exclude='AegisDocs' \
    --exclude='README.md' \
    --exclude='backup.sh' \
    modules/*.py \
    templates/*.html \
    static \
    scripts \
    app.py \
    wsgi.py \
    auto-ban.py \
    restore-state.py \
    timeline_updater.py \
    ingest_events.py \
    log-truncate.sh

# Build separate GeoIP archive (bundled alongside install.sh)
GEOIP_ARCHIVE="$SCRIPT_DIR/aegisgate-geoip.tar.gz"
tar czf "$GEOIP_ARCHIVE" \
    -C "$SCRIPT_DIR" \
    data/geoip/GeoLite2-City.mmdb \
    data/geoip/GeoLite2-ASN.mmdb 2>/dev/null || true
GEOIP_SIZE="0"
if [[ -f "$GEOIP_ARCHIVE" ]]; then
    GEOIP_SIZE=$(du -h "$GEOIP_ARCHIVE" | cut -f1)
fi

base64 -w76 "$ARCHIVE" > "$B64"

ARCHIVE_CONTENT="$(cat "$B64")"

# Step 3: Build deploy_app_files function
DEPLOY_BLOCK="deploy_app_files() {
    step \"Deploying application files\"
    mkdir -p \"\$APP_DIR\"
    base64 -d << 'ARCHIVE_EOF' | tar xzf - -C \"\$APP_DIR\"
${ARCHIVE_CONTENT}
ARCHIVE_EOF
    chmod +x \"\$APP_DIR\"/*.py \"\$APP_DIR\"/*.sh \"\$APP_DIR/scripts\"/*.sh \"\$APP_DIR/scripts\"/*.py 2>/dev/null || true
}"

# Step 4: Insert deploy_app_files before setup_directories() in base install.sh
BEFORE_MARKER='setup_directories() {'
BEFORE_LINE=$(grep -n "^${BEFORE_MARKER}" "$BASE_INSTALL" | head -1 | cut -d: -f1)

if [[ -z "$BEFORE_LINE" ]]; then
    echo "ERROR: Could not find setup_directories() in base install.sh" >&2
    exit 1
fi

head -n $((BEFORE_LINE - 1)) "$BASE_INSTALL" > "$TMPDIR/part1"
printf "\n%s\n\n" "$DEPLOY_BLOCK" >> "$TMPDIR/part1"
tail -n +"$BEFORE_LINE" "$BASE_INSTALL" >> "$TMPDIR/part1"

# Step 5: Add deploy_app_files call in main() before setup_auth_and_data
cp "$TMPDIR/part1" "$INSTALL_OUT"

# Check if deploy_app_files call already exists in main()
if ! grep -q "^    deploy_app_files$" "$INSTALL_OUT"; then
    AFTER_CALL_LINE=$(grep -n "^    setup_auth_and_data$" "$INSTALL_OUT" | head -1 | cut -d: -f1)
    if [[ -n "$AFTER_CALL_LINE" ]]; then
        sed -i "${AFTER_CALL_LINE}i\\    deploy_app_files" "$INSTALL_OUT"
    fi
fi

chmod +x "$INSTALL_OUT"

echo "Built installer: $INSTALL_OUT"
echo "Archive size: $(du -h "$ARCHIVE" | cut -f1)"
echo "Base64 size: $(du -h "$B64" | cut -f1)"
echo "GeoIP archive: $GEOIP_ARCHIVE ($GEOIP_SIZE)"
echo "Total lines: $(wc -l < "$INSTALL_OUT")"

# Cleanup
rm -f "$BASE_INSTALL"