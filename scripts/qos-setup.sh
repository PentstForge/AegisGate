#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${AEGIS_APP_DIR:-/opt/nft-dashboard}"
cd "$APP_DIR"
exec /usr/bin/python3 - <<'PY'
from modules.qos import _load, apply_profile

data = _load()
if not data.get("enabled", True):
    from modules.qos import _disable_qos_runtime
    ok, message = _disable_qos_runtime(data)
    print(message)
    raise SystemExit(0 if ok else 1)
ok, message = apply_profile(data.get("active_profile", "gaming"))
print(message)
raise SystemExit(0 if ok else 1)
PY
