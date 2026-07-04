#!/usr/bin/env python3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.bandwidth import collect_sample, cleanup_old_samples

if __name__ == "__main__":
    collect_sample()
    cleanup_old_samples(days=90)