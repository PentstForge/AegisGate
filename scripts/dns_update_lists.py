#!/usr/bin/env python3
import fcntl
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules.dns import apply_config
from modules.dns_db import ensure_settings, add_event
from modules.dns_lists import update_all_lists


LOCK_PATH = "/tmp/aegisgate-dns-update-lists.lock"


def main():
    ensure_settings()
    with open(LOCK_PATH, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("DNS list update already running")
            return 0

        started = time.time()
        results = update_all_lists()
        ok_count = sum(1 for r in results if r.get("ok"))
        fail_count = sum(1 for r in results if not r.get("ok"))
        if ok_count:
            ok, msg = apply_config()
        else:
            ok, msg = True, "No lists updated"
        add_event(
            "lists_auto_updated",
            "info" if ok and fail_count == 0 else "medium",
            "dns_lists",
            f"Auto update complete: {ok_count} ok, {fail_count} failed, apply={msg}",
            {"results": results, "duration_sec": round(time.time() - started, 1)},
        )
        print(f"updated={ok_count} failed={fail_count} apply_ok={ok} message={msg}")
        for r in results:
            print(f"{r.get('name')}: {'OK' if r.get('ok') else 'FAIL'} {r.get('msg')}")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
