"""Frozen Kai OB defaults, C2 OOS 36/6/6 and conditional E1 account scoring.

Run --preregister once, then run without arguments. Outputs are checkpointed
after each market. No fitting, parameter search or canonical artifact writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lab_artifacts.e7_protocol.run_e7_multimercado import load_market_csv, build_maes
from lab_artifacts.run_c1_walkforward import (
    CALIBRATION_BARS_COUNT, MIN_FOLD_TRADES_GATE, ZIP_PATH, M5_MEMBER,
    compute_file_sha256, load_canonical_m5, run_bootstrap_ci,
)
from lab_artifacts.run_d_protocol import (
    extract_m1_bars_and_compute_maes, AccountEngineConfig, apex_profile,
    run_account_monte_carlo,
)
from src.account_policy import SizingPolicyConfig
from src.backtest.executor import run_backtest
from src.backtest.markets import MNQ, MYM, MGC
from src.backtest.smc_ob import SmcObStrategy, smc_ob_config
from src.hypothesis_registry import WalkForwardPlan

BASE = Path(__file__).resolve().parent
KAI = Path('C:/Users/yo/Documents/GitHub/kai-backtesting/src/kai_bt')
MARKETS = {'MYM': MYM, 'MNQ': MNQ, 'MGC': MGC}
PARAMS = dict(swing_w=10, target_rr=3., choch_only=True, ob_lookback=60,
              f=.5, wait=36, cooldown=0, min_risk_pts=0.)
SEED = 20260729


def save(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str, allow_nan=False)+'\n', encoding='utf-8')


def preregister():
    path = BASE / 'preregistro.json'
    if path.exists():
        raise SystemExit('Preregistration already exists; it must not be overwritten.')
    sources = [KAI/'strategies/strat_smc_ob_signal.py', KAI/'strategies/strat_smc.py',
               KAI/'live/live_smc_bridge.py', ROOT/'src/backtest/smc_ob.py',
               Path(__file__), ROOT/'lab_artifacts/e7_protocol/run_e7_multimercado.py']
    save(path, {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
        'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'source_sha256': {str(p): compute_file_sha256(p) for p in sources},
        'grid': {'markets': list(MARKETS), 'configurations': {'kai_defaults': PARAMS}, 'scenarios': ['por_tramo']},
        'selection': 'strat_smc.DEFAULTS; NOT bridge FROZEN target_rr=2.0. No tuning after OOS.',
        'walk_forward': {'train_months': 36, 'test_months': 6, 'step_months': 6,
                         'calibration_bars': CALIBRATION_BARS_COUNT, 'fitting': False,
                         'purge': 'No fitted train labels; calibration strictly precedes OOS. Reset per fold; unresolved end positions reported and excluded, as C2.'},
        'execution': {'initial_balance': 50000., 'risk_per_trade': .01, 'discrete_partials': True,
                      'signal_session_filter': 'none, matching Kai; MarketSpec passed for all markets',
                      'fill': 'FARS E5 pending LIMIT; M5 stop-first; prices rounded to market tick',
                      'costs': 'C2 reference path commission=2/side, zero slip; reprice net at .62/side plus .25 points*dpp on STOP/BE/MARKET remaining contracts; LIMIT/TP no slip',
                      'r_denominator': 'budgeted_risk_dollars, as C2; effective R additionally reported'},
        'metrics': ['n', 'WR', 'E[R]', 'PF_dollars', 'DD_R', 'DD_dollars', 'CBB_CI95', 'positive_folds'],
        'bootstrap': {'method': 'existing C1 CircularBlockBootstrap percentile', 'reps': 2000,
                      'seed': 42, 'block_size': 'max(2,min(20,int(n**(1/3))))'},
        'blocking_gates': {'expectancy': '>0', 'CI95_lower': '>0', 'positive_folds': '>=.75',
                           'C2_concentration': '<60% of total net R', 'C2_min_trades_each_fold': MIN_FOLD_TRADES_GATE},
        'gate5': 'DD<5% and DD<12R: hygiene only, does not block',
        'data': {'MNQ': str(ZIP_PATH)+'::'+M5_MEMBER,
                 'MYM': 'D:/fars move/FARS/MYM_M5.csv', 'MGC': 'D:/fars move/FARS/MGC_M5.csv',
                 'cut': '2019-05-06 UTC', 'loaders': 'reuse C1 MNQ and E7 MYM/MGC unchanged',
                 'duplicates': 'E7 discards timestamps <= previous retained; MNQ input validated strictly increasing',
                 'rollover': 'use supplied OHLC unadjusted by this runner; source continuous-contract/roll policy undocumented; no canonical promotion'},
        'account': {'conditional': 'only if market passes all C2 blocking gates',
                    'profile': 'Apex25k intraday_event', 'horizon_calendar_days': 30,
                    'seed': SEED, 'n_simulations': 2000, 'risk_pct': .004671,
                    'policies': ['fixed', 'buffer_prop k=.10'],
                    'pools': 'approved OB market alone; MNQ OB + each approved non-MNQ OB market, sorted chronologically',
                    'mae': 'MNQ existing extract_m1_bars_and_compute_maes; MYM/MGC E7 build_maes',
                    'limitation': 'Existing account MC resamples individual trades IID, not joint days/blocks. This is a model score, not proof of independence or diversification.'},
    })


def reprice(trade, market):
    partial = trade.exit_reason == 'break_even_stop' or (
        trade.exit_reason in {'stop_loss', 'time_exit'} and trade.stop_price == trade.entry_price)
    remaining = trade.quantity - int(trade.quantity*.5) if partial else trade.quantity
    slipping = remaining if trade.exit_reason in {'stop_loss', 'break_even_stop', 'time_exit'} else 0
    slip = .25 * market.dollar_per_point * slipping
    commission = .62 * 2 * trade.quantity
    net = trade.gross_pnl - commission - slip
    r = net / trade.budgeted_risk_dollars
    return replace(trade, commission=commission, slippage_cost=slip, net_pnl=net,
                   r_result=r, budgeted_r=r, effective_r=net/trade.effective_risk_dollars)


def metrics(trades):
    rs = [t.r_result for t in trades]
    pnl = [t.net_pnl for t in trades]
    loss = -sum(p for p in pnl if p < 0)
    dd = []
    for values in (rs, pnl):
        total = peak = worst = 0.
        for value in values:
            total += value
            peak = max(peak, total)
            worst = max(worst, peak-total)
        dd.append(worst)
    return {'n': len(trades), 'win_rate': sum(p > 0 for p in pnl)/len(pnl) if pnl else 0.,
            'expectancy_r': sum(rs)/len(rs) if rs else 0., 'net_r': sum(rs),
            'profit_factor': sum(p for p in pnl if p > 0)/loss if loss else None,
            'max_drawdown_r': dd[0], 'max_drawdown_dollars': dd[1],
            'max_drawdown_pct_initial': dd[1]/50000*100,
            'effective_expectancy_r': sum(t.effective_r for t in trades)/len(trades) if trades else 0.,
            'commission': sum(t.commission for t in trades), 'slippage': sum(t.slippage_cost for t in trades)}


def gen_trades(bars, market, min_risk=0.):
    """Adapt E7 gen_trades: pass market to OB strategy, retain folds and C2 repricing."""
    plan = WalkForwardPlan.create_calendar_rolling(bars, train_months=36, test_months=6,
                                                  step_months=6, warmup_bars=CALIBRATION_BARS_COUNT)
    cfg = smc_ob_config(market=market, commission_per_side=2.)
    trades, folds = [], []
    for fold in plan.folds:
        cal = bars[max(0, fold.test_start_idx-CALIBRATION_BARS_COUNT):fold.test_start_idx]
        test = bars[fold.test_start_idx:fold.test_end_idx]
        params = dict(PARAMS, min_risk_pts=min_risk)
        res = run_backtest(test, SmcObStrategy(market=market, log_decisions=False, **params), cfg, calibration_bars=cal)
        ft = [replace(reprice(t, market), trade_id=f'{market.symbol}_fold_{fold.fold_id}_{t.trade_id}') for t in res.trades]
        assert all(test[0].timestamp <= t.entry_time <= t.exit_time <= test[-1].timestamp for t in ft)
        row = {'fold_id': fold.fold_id, 'train_start': fold.train_start, 'train_end': fold.train_end,
               'test_start': fold.test_start, 'test_end': fold.test_end, 'test_bars': len(test),
               'unresolved_positions': res.unresolved_positions, 'open_position': res.open_position, **metrics(ft)}
        folds.append(row)
        trades.extend(ft)
        print(f"{market.symbol} fold {fold.fold_id}: n={len(ft)} E[R]={row['expectancy_r']:+.5f}", flush=True)
    return trades, folds


def evaluate(bars, market):
    trades, folds = gen_trades(bars, market)
    agg = metrics(trades)
    low, high = run_bootstrap_ci([t.r_result for t in trades])
    agg['bootstrap_cbb_ci95'] = [low, high]
    agg['positive_folds'] = sum(f['net_r'] > 0 for f in folds)
    agg['n_folds'] = len(folds)
    agg['positive_folds_ratio'] = agg['positive_folds']/len(folds) if folds else 0.
    concentration = max((f['net_r'] for f in folds), default=0)/agg['net_r'] if agg['net_r'] > 0 else None
    agg['max_fold_concentration'] = concentration
    gates = {'expectancy_positive': agg['expectancy_r'] > 0, 'CI_excludes_zero_positive': low is not None and low > 0,
             'folds_ge_75pct': agg['positive_folds_ratio'] >= .75,
             'C2_concentration_lt_60pct': concentration is not None and concentration < .60,
             'C2_sample_sufficient': bool(folds) and all(f['n'] >= MIN_FOLD_TRADES_GATE for f in folds)}
    return trades, {'aggregate': agg, 'folds': folds, 'blocking_gates': gates,
                    'gate5_hygiene_pass': agg['max_drawdown_r'] < 12 and agg['max_drawdown_pct_initial'] < 5,
                    'verdict': 'PASS' if all(gates.values()) else 'FAIL'}


def summarize(result):
    lines = ['# SMC-OB: Kai defaults / por_tramo', '',
             'Resultados mecánicos pendientes de revisión independiente de Hermes. No promoción ni commits.', '',
             '| Mercado | n | WR | E[R] | PF | DD R | IC95 CBB | Folds+ | Veredicto |',
             '|---|---:|---:|---:|---:|---:|---|---:|---|']
    for symbol, row in result['markets'].items():
        a = row['aggregate']
        lo, hi = a['bootstrap_cbb_ci95']
        ci = f'[{lo:.4f}, {hi:.4f}]' if lo is not None else 'no disponible'
        pf = f"{a['profit_factor']:.3f}" if a['profit_factor'] is not None else 'sin pérdidas'
        lines.append(f"| {symbol} | {a['n']} | {a['win_rate']:.1%} | {a['expectancy_r']:+.4f} | {pf} | {a['max_drawdown_r']:.2f} | {ci} | {a['positive_folds']}/{a['n_folds']} | {row['verdict']} |")
    lines += ['', 'Configuración: swing=10, RR=3, CHoCH=True, lookback=60, f=.5, wait=36, cooldown=0, min_risk=0. Sin filtro horario añadido.',
              '36/6/6 calendario; 500 barras previas de calibración; sin ajuste de parámetros. E[R] usa riesgo presupuestado, PF usa dólares netos. Costes C2 por tramo: .62 USD/pata y .25 puntos × dpp por contrato restante en STOP/BE/MARKET; sin slippage LIMIT/TP.',
              'Gates: E[R]>0, límite inferior IC95>0, folds positivos>=75%, concentración<60% y >=15 trades/fold (C2). DD es solo higiene E3.',
              'Fuentes, hashes, fechas, posiciones sin resolver y métricas por fold: resultados.json. Se reutilizan los loaders existentes; no se cambia ni declara canónico ningún dataset.', '', '## Cuenta E1', '']
    if result['account']:
        lines += ['| Pool | Política | Pase | Quema | Bloqueadas | Timeout |', '|---|---|---:|---:|---:|---:|']
        for row in result['account']:
            lines.append(f"| {row['pool']} | {row['policy']} | {row['pass']:.2%} | {row['blown']:.2%} | {row['blocked']:.2%} | {row['timeout']:.2%} |")
    else:
        lines.append(result.get('account_status', 'Pendiente de completar gates de los tres mercados.'))
    lines += ['', 'El motor solicitado usa remuestreo IID de trades; sus probabilidades son puntuaciones condicionadas a ese modelo, no evidencia de independencia temporal ni de diversificación del pool. MAE M1/M5 y sus limitaciones se auditan si se activa E1.',
              'Ambigüedades y preguntas exactas en BLOCKERS.md.']
    (BASE/'SUMMARY.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preregister', action='store_true')
    args = parser.parse_args()
    if args.preregister:
        preregister()
        return
    prereg = json.loads((BASE/'preregistro.json').read_text(encoding='utf-8'))
    for path, expected in prereg['source_sha256'].items():
        if compute_file_sha256(Path(path)) != expected:
            raise RuntimeError(f'Frozen source changed: {path}')
    result = {'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'preregistro_sha256': compute_file_sha256(BASE/'preregistro.json'), 'markets': {}, 'account': []}
    pools, barsets = {}, {}
    for symbol, market in MARKETS.items():
        print(f'Loading {symbol}', flush=True)
        if symbol == 'MNQ':
            bars, digest = load_canonical_m5(ZIP_PATH)
            source = str(ZIP_PATH)+'::'+M5_MEMBER
        else:
            path = Path(f'D:/fars move/FARS/{symbol}_M5.csv')
            bars = load_market_csv(symbol, path)
            digest, source = compute_file_sha256(path), str(path)
        assert all(a.timestamp < b.timestamp for a, b in zip(bars, bars[1:]))
        assert all(b.timestamp.tzinfo is not None for b in bars)
        trades, row = evaluate(bars, market)
        row['data'] = {'source': source, 'sha256': digest, 'bars': len(bars),
                       'start': bars[0].timestamp, 'end': bars[-1].timestamp,
                       'timezone': 'aware source timestamps, UTC cut; MarketSpec ET',
                       'contract_rollover': 'source policy not documented; no transformations added',
                       'comparison_previous': 'same source path and existing loader as C1/E7; no dataset change or canonical promotion'}
        result['markets'][symbol] = row
        pools[symbol], barsets[symbol] = trades, bars
        save(BASE/f'trades_{symbol}.json', [asdict(t) for t in trades])
        save(BASE/'resultados.json', result)
        summarize(result)
        print(f"{symbol}: {row['verdict']} {row['aggregate']}", flush=True)
    approved = [s for s, row in result['markets'].items() if row['verdict'] == 'PASS']
    if not approved:
        result['account_status'] = 'No ejecutada: ningún mercado pasa los gates; E1 es condicional por encargo.'
    else:
        maes = {}
        needed = set(approved) | {'MNQ'}
        for symbol in needed:
            if symbol == 'MNQ':
                maes.update(extract_m1_bars_and_compute_maes(pools[symbol], ZIP_PATH))
            else:
                prefixed = build_maes(pools[symbol], barsets[symbol], MARKETS[symbol].dollar_per_point, symbol)
                maes.update({k[len(symbol)+1:]: v for k, v in prefixed.items()})
        save(BASE/'maes.json', {k: asdict(v) for k, v in maes.items()})
        result['mae_modes'] = dict(Counter(m.resolution_mode for m in maes.values()))
        variants = {s: pools[s] for s in approved}
        variants.update({f'MNQ+{s}': sorted(pools['MNQ']+pools[s], key=lambda t: (t.entry_time, t.exit_time, t.trade_id)) for s in approved if s != 'MNQ'})
        for label, pool in variants.items():
            for policy in (SizingPolicyConfig(kind='fixed'), SizingPolicyConfig(kind='buffer_prop', param=.10)):
                cfg = AccountEngineConfig(risk_pct=.004671, trailing_mode='intraday_event', horizon_calendar_days=30, policy=policy)
                mc = run_account_monte_carlo(apex_profile('25k', cadence='intraday_event'), pool, cfg,
                                            n_simulations=2000, seed=SEED, precomputed_maes=maes)
                result['account'].append({'pool': label, 'policy': policy.kind, 'pass': mc.pass_rate,
                                          'blown': mc.blown_rate, 'blocked': mc.blocked_rate,
                                          'timeout': mc.timeout_rate, 'trades_per_day': mc.trades_per_day})
                save(BASE/'resultados.json', result)
                summarize(result)
    result['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    save(BASE/'resultados.json', result)
    summarize(result)


if __name__ == '__main__':
    main()
