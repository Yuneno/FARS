#!/usr/bin/env python3
"""Canonical Dataset Verification Script for FARS.

Verifies the canonical dataset convention:
- Archive: E:/FARS-LAB/databento.zip
- Member: databento/MNQ_M5.csv
- Expected raw member SHA-256: fbed6061205b8299af140f85e36b472f5f1d88084977ad9c4ca9aa1f817b8a96
- Filter: timestamp >= 2019-05-06
- Expected bar count: 518,237 bars
"""

from __future__ import annotations

import csv
import hashlib
import io
import sys
import zipfile
from pathlib import Path

ZIP_PATH = Path("E:/FARS-LAB/databento.zip")
MEMBER_NAME = "databento/MNQ_M5.csv"
EXPECTED_MEMBER_SHA256 = "fbed6061205b8299af140f85e36b472f5f1d88084977ad9c4ca9aa1f817b8a96"
EXPECTED_BAR_COUNT = 518_237
CUTOFF_TIMESTAMP = "2019-05-06"


def verify_canonical_dataset() -> bool:
    if not ZIP_PATH.exists():
        print(f"[FAIL] Archive not found at {ZIP_PATH}")
        return False

    print(f"Opening archive: {ZIP_PATH}")
    with zipfile.ZipFile(ZIP_PATH) as zf:
        if MEMBER_NAME not in zf.namelist():
            print(f"[FAIL] Member {MEMBER_NAME} not found in zip archive.")
            return False

        print(f"Reading member: {MEMBER_NAME}...")
        raw_bytes = zf.read(MEMBER_NAME)
        actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        print(f"Member SHA-256: {actual_sha256}")

        if actual_sha256 != EXPECTED_MEMBER_SHA256:
            print(f"[MISMATCH] Expected SHA-256: {EXPECTED_MEMBER_SHA256}")
            print(f"           Actual SHA-256:   {actual_sha256}")
            return False
        else:
            print(f"[OK] Member SHA-256 matches expected convention ({EXPECTED_MEMBER_SHA256}).")

        # Check rows post-2019-05-06
        reader = csv.DictReader(io.StringIO(raw_bytes.decode("utf-8")))
        bar_count = 0
        first_ts = None
        last_ts = None
        for row in reader:
            ts = row.get("timestamp", "")
            if ts >= CUTOFF_TIMESTAMP:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
                bar_count += 1

        print(f"Filtered bars (timestamp >= {CUTOFF_TIMESTAMP}): {bar_count:,}")
        print(f"First timestamp: {first_ts}")
        print(f"Last timestamp:  {last_ts}")

        if bar_count != EXPECTED_BAR_COUNT:
            print(f"[MISMATCH] Expected {EXPECTED_BAR_COUNT:,} bars, found {bar_count:,}")
            return False
        else:
            print(f"[OK] Bar count matches expected {EXPECTED_BAR_COUNT:,}.")

    print("\nVERIFICATION RESULT: OK")
    return True


if __name__ == "__main__":
    success = verify_canonical_dataset()
    sys.exit(0 if success else 1)
