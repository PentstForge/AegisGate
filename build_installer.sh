#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_SOURCE="$SCRIPT_DIR/install.sh"
INSTALL_OUT="$SCRIPT_DIR/install.sh"
TMP_DIR="$(mktemp -d)"
BASE_INSTALL="$TMP_DIR/install.base.sh"
STAGE_DIR="$TMP_DIR/payload"
ARCHIVE="$TMP_DIR/aegisgate-app.tar.gz"
B64="$TMP_DIR/aegisgate-app.b64"

cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

for command_name in python3 tar gzip base64 sha256sum awk; do
    command -v "$command_name" >/dev/null 2>&1 || die "Required build command not found: $command_name"
done
[[ -s "$INSTALL_SOURCE" ]] || die "Installer source is missing: $INSTALL_SOURCE"

# install.sh is both the checked-in output and the source for its non-payload logic.
# Remove the previous payload and the legacy generated nft restore body before rebuilding.
INSTALL_SOURCE="$INSTALL_SOURCE" BASE_INSTALL="$BASE_INSTALL" python3 <<'PY'
import os
import re

source = os.environ["INSTALL_SOURCE"]
destination = os.environ["BASE_INSTALL"]
with open(source, "r", encoding="utf-8") as handle:
    content = handle.read()

content, payload_count = re.subn(
    r"\ndeploy_app_files\(\) \{.*?\n\}",
    "",
    content,
    count=1,
    flags=re.DOTALL,
)
if payload_count != 1:
    raise SystemExit("existing deploy_app_files payload was not found exactly once")

legacy_restore = re.compile(
    r"\n    cat > \"\$\{APP_DIR\}/scripts/safe-nft-restore\.sh\" <<'RESTORE_EOF'.*?"
    r"\nRESTORE_EOF\n    chmod \+x \"\$\{APP_DIR\}/scripts/safe-nft-restore\.sh\"",
    re.DOTALL,
)
replacement = (
    '\n    [[ -x "${APP_DIR}/scripts/safe-nft-restore.sh" ]] || '
    'fail "Missing safe nft restore payload"\n'
    '    chmod +x "${APP_DIR}/scripts/safe-nft-restore.sh"'
)
content, restore_count = legacy_restore.subn(replacement, content, count=1)
if restore_count not in (0, 1):
    raise SystemExit("legacy safe nft restore block occurred more than once")

content, marker_spacing_count = re.subn(
    r"\n{2,}(?=setup_directories\(\) \{)",
    "\n\n",
    content,
    count=1,
)
if marker_spacing_count != 1:
    raise SystemExit("setup_directories spacing marker was not found exactly once")

lines = content.splitlines()
deploy_calls = [index for index, line in enumerate(lines) if line.strip() == "deploy_app_files"]
if len(deploy_calls) > 1:
    first = deploy_calls[0]
    lines = [
        line for index, line in enumerate(lines)
        if line.strip() != "deploy_app_files" or index == first
    ]

with open(destination, "w", encoding="utf-8", newline="\n") as handle:
    handle.write("\n".join(lines) + "\n")
PY

PAYLOAD_PATHS=(
    modules
    templates
    static
    scripts
    app.py
    wsgi.py
    auto-ban.py
    restore-state.py
    timeline_updater.py
    ingest_events.py
    log-truncate.sh
    uninstall.sh
    config.example.json
    README.md
    LICENSE
)

for path in "${PAYLOAD_PATHS[@]}"; do
    [[ -e "$SCRIPT_DIR/$path" ]] || die "Required payload path is missing: $path"
done

mkdir -p "$STAGE_DIR"
tar -C "$SCRIPT_DIR" -cf - \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*.db' \
    --exclude='*.db-*' \
    --exclude='*.log' \
    --exclude='.env' \
    --exclude='.env.*' \
    --exclude='data' \
    --exclude='backups' \
    --exclude='backup_*' \
    "${PAYLOAD_PATHS[@]}" | tar -C "$STAGE_DIR" -xf -

(
    cd "$STAGE_DIR"
    find . -type f ! -name MANIFEST.sha256 -print0 \
        | LC_ALL=C sort -z \
        | xargs -0 sha256sum > MANIFEST.sha256
)

tar --sort=name \
    --mtime='UTC 2020-01-01' \
    --owner=0 --group=0 --numeric-owner \
    --use-compress-program='gzip -n' \
    -C "$STAGE_DIR" -cf "$ARCHIVE" .

ARCHIVE_SHA256="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
base64 -w76 "$ARCHIVE" > "$B64"

BASE_INSTALL="$BASE_INSTALL" B64="$B64" ARCHIVE_SHA256="$ARCHIVE_SHA256" python3 <<'PY'
import os

base_path = os.environ["BASE_INSTALL"]
b64_path = os.environ["B64"]
archive_sha256 = os.environ["ARCHIVE_SHA256"]

with open(base_path, "r", encoding="utf-8") as handle:
    content = handle.read()
with open(b64_path, "r", encoding="ascii") as handle:
    archive_content = handle.read().rstrip("\n")

marker = "setup_directories() {"
if content.count(marker) != 1:
    raise SystemExit("setup_directories marker was not found exactly once")

deploy_block = f'''deploy_app_files() {{
    step "Deploying application files"
    local archive_file
    archive_file="$(mktemp /tmp/aegisgate-payload.XXXXXX.tar.gz)"
    base64 -d > "$archive_file" <<'ARCHIVE_EOF'
{archive_content}
ARCHIVE_EOF
    printf '%s  %s\\n' '{archive_sha256}' "$archive_file" | sha256sum -c - >/dev/null || {{
        rm -f "$archive_file"
        fail "Embedded application archive checksum failed"
    }}
    mkdir -p "$APP_DIR"
    rm -rf "$APP_DIR/modules" "$APP_DIR/templates" "$APP_DIR/static" "$APP_DIR/scripts"
    rm -f \
        "$APP_DIR/app.py" "$APP_DIR/wsgi.py" "$APP_DIR/auto-ban.py" \
        "$APP_DIR/restore-state.py" "$APP_DIR/timeline_updater.py" \
        "$APP_DIR/ingest_events.py" "$APP_DIR/log-truncate.sh" \
        "$APP_DIR/uninstall.sh" "$APP_DIR/config.example.json" \
        "$APP_DIR/README.md" "$APP_DIR/LICENSE" "$APP_DIR/MANIFEST.sha256"
    tar xzf "$archive_file" -C "$APP_DIR"
    rm -f "$archive_file"
    (cd "$APP_DIR" && sha256sum -c MANIFEST.sha256 >/dev/null) || fail "Deployed file checksum failed"
    chmod +x "$APP_DIR"/*.py "$APP_DIR"/*.sh "$APP_DIR/scripts"/*.sh "$APP_DIR/scripts"/*.py 2>/dev/null || true
}}
'''
content = content.replace(marker, deploy_block + "\n" + marker)

if "\n    deploy_app_files\n" not in content:
    call_marker = "\n    setup_auth_and_data\n"
    if content.count(call_marker) != 1:
        raise SystemExit("setup_auth_and_data call marker was not found exactly once")
    content = content.replace(call_marker, "\n    deploy_app_files" + call_marker)

with open(base_path, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(content)
PY

mv "$BASE_INSTALL" "$INSTALL_OUT"
chmod +x "$INSTALL_OUT"

bash -n "$INSTALL_OUT"
grep -q '^deploy_app_files() {' "$INSTALL_OUT" || die "Generated installer has no deploy function"
[[ "$(grep -c '^    deploy_app_files$' "$INSTALL_OUT")" -eq 1 ]] || die "Generated installer has an invalid deploy call count"
grep -q 'scripts/health-monitor.py' "$STAGE_DIR/MANIFEST.sha256" || die "Health monitor is missing from payload"
grep -q 'scripts/safe-nft-restore.sh' "$STAGE_DIR/MANIFEST.sha256" || die "Safe nft restore is missing from payload"
grep -q 'uninstall.sh' "$STAGE_DIR/MANIFEST.sha256" || die "Uninstaller is missing from payload"
if grep -Eq '(^|/)(auth\.json|sessions\.json|health\.env|data/env|\.env)$' "$STAGE_DIR/MANIFEST.sha256"; then
    die "Runtime secret/state file entered the payload"
fi

printf 'Built installer: %s\n' "$INSTALL_OUT"
printf 'Embedded files: %s\n' "$(wc -l < "$STAGE_DIR/MANIFEST.sha256")"
printf 'Archive SHA-256: %s\n' "$ARCHIVE_SHA256"
printf 'Archive size: %s bytes\n' "$(stat -c %s "$ARCHIVE")"
printf 'Installer size: %s bytes\n' "$(stat -c %s "$INSTALL_OUT")"
