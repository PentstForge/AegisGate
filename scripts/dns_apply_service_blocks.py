#!/usr/bin/env python3
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules.dns_service_nft import apply_service_blocks


def main():
    ok, msg, blocks = apply_service_blocks()
    print(f"ok={ok} clients={len(blocks)} message={msg}")
    for block in blocks:
        print(block)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
