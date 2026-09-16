"""One-time mechanical extraction; retained to audit the source-body port."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path('C:/Users/yo/Documents/GitHub/kai-backtesting/src/kai_bt/strategies/strat_smc_ob_signal.py')

def extract(source, node):
    return '\n'.join(source.splitlines()[node.lineno - 1:node.end_lineno])

def main():
    kai = SOURCE.read_text(encoding='utf-8')
    tree = ast.parse(kai)
    core = '\n\n'.join(extract(kai, n) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)))
    core = core.replace('class SmcObSignal:', 'class _SmcObSignal:').replace('        from kai_bt.strategies.strat_smc import MAX_WAIT\n', '')
    fvg = (ROOT / 'src/backtest/smc_fvg.py').read_text(encoding='utf-8')
    wrapper = fvg[fvg.index('SmcFvgReason ='):].replace('SmcFvg', 'SmcOb').replace('smc_fvg', 'smc_ob').replace('FVG', 'OB')
    wrapper = wrapper.replace('        swing_w: int = DEFAULT_SWING_W,', '        swing_w: int = DEFAULT_SWING_W,\n        choch_only: bool = True,\n        ob_lookback: int = 60,')
    wrapper = wrapper.replace('        self.market = market', '        if ob_lookback < 1:\n            raise ValueError("ob_lookback must be >= 1")\n        self.choch_only = bool(choch_only)\n        self.ob_lookback = int(ob_lookback)\n        self._detector = _SmcObSignal(swing_w, target_rr, choch_only, ob_lookback)\n        self._arrays = np.empty((5, 1024), dtype=float)\n        self._atr_acc = 0.0\n        self._execution_blocked = False\n        self.market = market')
    start = wrapper.index('        self._sh_p:')
    end = wrapper.index('        self._pending_active', start)
    wrapper = wrapper[:start] + wrapper[end:]
    wrapper = wrapper.replace('        del position, cooldown', '        self._execution_blocked = position or cooldown > 0')
    start = wrapper.index('    def _update_pivots(')
    end = wrapper.index('        decision: SmcObReason', start)
    wrapper = wrapper[:start] + '''    def _update_pivots(self, t: int) -> None:
        # Populate the reference cache with the SAME causal recurrence as _atr.
        # Capacity doubles; prefix views avoid quadratic full-history parsing.
        if t == self._arrays.shape[1]:
            grown = np.empty((5, 2 * self._arrays.shape[1]), dtype=float)
            grown[:, :t] = self._arrays
            self._arrays = grown
        h, l, c = self._high[t], self._low[t], self._close[t]
        tr = h - l if t == 0 else max(h - l, abs(h - self._close[t-1]), abs(l - self._close[t-1]))
        self._atr_acc += (tr - self._atr_acc) / 200 if t else tr
        hv = h - l >= 2 * self._atr_acc
        self._arrays[:, t] = h, l, c, l if hv else h, h if hv else l
        h5, l5, c5, phi, plo = self._arrays[:, :t + 1]
        detector = self._detector
        detector._p_hi, detector._p_lo, detector._plen = phi, plo, t + 1
        detector.update_pivots(t, h5, l5, c5)

    def _structure_and_signal(self, t: int, *, emit: bool) -> Signal | None:
        if not emit or self._pending_active or self._execution_blocked or not self._detector.ready():
            return None
        h5, l5, c5 = self._arrays[:3, :t + 1]
        spec = self._detector.structure_and_arm(t, h5, l5, c5, False)
        if spec is None:
            return None
        direction = "long" if spec["side"] == 1 else "short"
        entry, stop, target, risk = spec["entry"], spec["sl"], spec["tp"], spec["risk"]

''' + wrapper[end:]
    wrapper = wrapper.replace('if not pivots_only and t >= 2 * self.swing_w + 2:', 'if not pivots_only:')
    wrapper = wrapper.replace('            "f": self.f,', '            "choch_only": self.choch_only,\n            "ob_lookback": self.ob_lookback,\n            "f": self.f,')
    wrapper = wrapper.replace('            f=self.f,', '            choch_only=self.choch_only,\n            ob_lookback=self.ob_lookback,\n            f=self.f,')
    wrapper = wrapper.replace('discrete_partial_contracts: bool = False', 'discrete_partial_contracts: bool = True')
    header = '''"""Kai SMC-OB detector and causal FARS execution adapter.

Detector bodies preserve strat_smc_ob_signal.py; only its MAX_WAIT import is
resolved locally. Integration follows smc_fvg.py, with Kai OB defaults, no
cooldown and no added minimum-risk/session filter. Parsed prices are cached
incrementally with the exact ATR recurrence; the detector remains unchanged.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

import numpy as np

from src.backtest.executor import BacktestConfig
from src.backtest.history import Bar
from src.backtest.markets import MNQ, MarketSpec
from src.backtest.strategy import Signal

DEFAULT_FRACTION = 0.5
DEFAULT_SWING_W = 10
DEFAULT_TARGET_RR = 3.0
MAX_WAIT = DEFAULT_WAIT = 36
DEFAULT_MIN_RISK_PTS = 0.0
DEFAULT_COOLDOWN = 0

'''
    (ROOT / 'src/backtest/smc_ob.py').write_text(header + core + '\n\n\n' + wrapper, encoding='utf-8')

if __name__ == '__main__':
    main()
