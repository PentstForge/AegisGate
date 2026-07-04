#!/usr/bin/env python3
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules.dns_schedule import apply_schedules


def main():
    ok, msg, blocks = apply_schedules()
    print(f"ok={ok} blocks={len(blocks)} message={msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
