"""Reconcile saved ledgers, CBB intervals and frozen source hashes."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from lab_artifacts.smcob_protocol.run_smcob_multimercado import metrics
from lab_artifacts.run_c1_walkforward import compute_file_sha256, run_bootstrap_ci
from src.backtest.executor import ExecutedTrade

BASE = Path(__file__).resolve().parent


if __name__ == '__main__':
    result = json.loads((BASE/'resultados.json').read_text(encoding='utf-8'))
    prereg = json.loads((BASE/'preregistro.json').read_text(encoding='utf-8'))
    assert result['preregistro_sha256'] == compute_file_sha256(BASE/'preregistro.json')
    assert prereg['created_at_utc'] < result['started_at_utc']
    for source, digest in prereg['source_sha256'].items():
        assert compute_file_sha256(Path(source)) == digest, source
    evidence = {'frozen_sources_unchanged': True, 'preregistered_before_execution': True, 'markets': {}}
    for symbol, row in result['markets'].items():
        trades = [ExecutedTrade(**r) for r in json.loads((BASE/f'trades_{symbol}.json').read_text(encoding='utf-8'))]
        assert len({t.trade_id for t in trades}) == len(trades)
        for key, value in metrics(trades).items():
            assert value == row['aggregate'][key], (symbol, key)
        ci = run_bootstrap_ci([t.r_result for t in trades])
        assert list(ci) == row['aggregate']['bootstrap_cbb_ci95']
        assert sum(f['n'] for f in row['folds']) == len(trades)
        assert row['verdict'] == ('PASS' if all(row['blocking_gates'].values()) else 'FAIL')
        evidence['markets'][symbol] = {'ledger_reconciled': True, 'CBB_reproduced_exactly': True,
                                       'unique_trade_ids': True, 'verdict_consistent': True}
    maes = json.loads((BASE/'maes.json').read_text(encoding='utf-8'))
    mnq = json.loads((BASE/'trades_MNQ.json').read_text(encoding='utf-8'))
    assert set(maes) == {t['trade_id'] for t in mnq}
    assert all(v['resolution_mode'] == 'm1_causal' for v in maes.values())
    evidence['MNQ_all_maes_m1_causal'] = len(maes)
    for row in result['account']:
        assert abs(sum(row[k] for k in ('pass', 'blown', 'blocked', 'timeout'))-1) < 1e-12
    evidence['account_probabilities_sum_to_one'] = True
    (BASE/'verification.json').write_text(json.dumps(evidence, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(evidence, indent=2))
