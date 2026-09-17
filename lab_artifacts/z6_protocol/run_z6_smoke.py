#!/usr/bin/env python3
"""Canonical MNQ M5 Z6 smoke and independent OB parity audit."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backtest.history import Bar
from src.backtest.smc_ob import _SmcObSignal, _atr
from src.detectors.pivots import confirmed_swing
from src.zones.levels import SrPivot, SupportResistanceClusterer
from src.zones.liquidity import liquidity_tolerance
from src.zones.order_blocks import OrderBlockBuilder

ZIP_PATH = Path("E:/FARS-LAB/databento.zip")
MEMBER = "databento/MNQ_M5.csv"
OUT = Path(__file__).with_name("smoke.json")


def load_streaming() -> tuple[list[Bar], str]:
    bars: list[Bar] = []
    digest = hashlib.sha256()
    with zipfile.ZipFile(ZIP_PATH) as archive, archive.open(MEMBER) as raw:
        buffered = io.BufferedReader(raw)
        class HashReader(io.RawIOBase):
            def readable(self): return True
            def readinto(self, target):
                data = buffered.read(len(target))
                digest.update(data)
                target[:len(data)] = data
                return len(data)
        with io.TextIOWrapper(io.BufferedReader(HashReader()), encoding="utf-8", newline="") as text:
            for row in csv.DictReader(text):
                if row["timestamp"] < "2019-05-06":
                    continue
                ts = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                bars.append(Bar(ts, float(row["open"]), float(row["high"]),
                                float(row["low"]), float(row["close"]),
                                float(row.get("volume") or 0.0)))
    return bars, digest.hexdigest()


def main() -> None:
    bars, data_hash = load_streaming()
    h = np.asarray([b.high for b in bars]); l = np.asarray([b.low for b in bars])
    c = np.asarray([b.close for b in bars])
    atr = _atr(h, l, c)
    # Same default fallback used by ZoneEngine without an external ATR series.
    tolerance = liquidity_tolerance(0.25, 0.25 * 10)
    sr = {
        side: SupportResistanceClusterer(symbol="MNQ", timeframe="M5", side=side,
              tolerance=tolerance, min_samples=2, lookback_bars=4000)
        for side in ("high", "low")
    }
    extracted = OrderBlockBuilder(symbol="MNQ", timeframe="M5")
    reference = _SmcObSignal(10, 3, True, 60)
    # Suppress its full-array parse: these arrays are the exact causal ATR transform.
    hv = (h - l) >= 2 * atr
    reference._p_hi = np.where(hv, l, h)
    reference._p_lo = np.where(hv, h, l)
    reference._plen = len(c)

    ob_expected: set[tuple] = set()
    ob_actual: set[tuple] = set()
    sr_seen: dict[str, tuple] = {}
    backdating = 0
    for t, bar in enumerate(bars):
        reference.update_pivots(t, h, l, c)
        spec = reference.structure_and_arm(t, h, l, c, False) if reference.ready() else None
        if spec is not None:
            if spec["side"] == 1:
                a = max(reference.sh_i, t - reference.oblook)
                j = a + int(np.argmin(reference._p_lo[a:t+1]))
            else:
                a = max(reference.sl_i, t - reference.oblook)
                j = a + int(np.argmax(reference._p_hi[a:t+1]))
            ob_expected.add((bars[j].timestamp.isoformat(), float(reference._p_lo[j]),
                             float(reference._p_hi[j]), int(spec["side"])))
        for zone in extracted.on_bar(bar, t):
            ob_actual.add((zone.pattern_time.isoformat(), zone.lower, zone.upper,
                           int(zone.metadata["side"])))
            backdating += int(zone.available_at > bar.timestamp)

        ph, pl = confirmed_swing(h, l, t, 3)
        for pivot in (ph, pl):
            if pivot is None:
                continue
            side = pivot.kind
            sr[side].add(SrPivot(
                pivot.index, pivot.level, float(l[pivot.index]), float(h[pivot.index]),
                side, t, bars[pivot.index].timestamp, bar.timestamp,
            ), t)
            for zone in sr[side].zones():
                old = sr_seen.get(zone.zone_id)
                if old and (old[0] != zone.pattern_time.isoformat() or old[1] != zone.available_at.isoformat()):
                    backdating += 1
                sr_seen[zone.zone_id] = (
                    zone.pattern_time.isoformat(), zone.available_at.isoformat(),
                    zone.lower, zone.upper, zone.strength, zone.zone_type,
                )
                backdating += int(zone.available_at > bar.timestamp)

    only_expected = sorted(ob_expected - ob_actual)
    only_actual = sorted(ob_actual - ob_expected)
    payload = {
        "protocol": "Z6 S/R + order blocks",
        "source": f"{ZIP_PATH}::{MEMBER}",
        "data_sha256": data_hash,
        "bars": len(bars),
        "start": bars[0].timestamp.isoformat(),
        "end": bars[-1].timestamp.isoformat(),
        "timezone": "UTC (source timestamps)",
        "dataset_filter": "timestamp >= 2019-05-06 (canonical C1 convention)",
        "duplicate_handling": "none; filtered rows consumed in archive order",
        "contract_rollover": "canonical continuous MNQ member; no transformation in Z6",
        "sr_tolerance": tolerance,
        "sr_unique_zones": len(sr_seen),
        "sr_support_zones": sum(1 for z in sr_seen.values() if z[-1] == "support"),
        "sr_resistance_zones": sum(1 for z in sr_seen.values() if z[-1] == "resistance"),
        "ob_port_zones": len(ob_expected),
        "ob_extracted_zones": len(ob_actual),
        "ob_only_port": len(only_expected),
        "ob_only_extracted": len(only_actual),
        "ob_parity_differences": len(only_expected) + len(only_actual),
        "causality_violations": backdating,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload["artifact_payload_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    if payload["ob_parity_differences"] or backdating:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
