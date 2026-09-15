import math

from src.cpcv import (
    CpcvObservation,
    compute_cpcv_pbo,
    contiguous_groups,
    split_path_observations,
)


def test_contiguous_groups_cover_once_and_balance_sizes():
    groups = contiguous_groups(25, 6)
    assert groups[0][0] == 0
    assert groups[-1][1] == 25
    assert all(groups[i][1] == groups[i + 1][0] for i in range(5))
    sizes = [end - start for start, end in groups]
    assert max(sizes) - min(sizes) == 1


def test_each_path_purges_overlap_and_embargoes_after_test():
    groups = ((0, 10), (10, 20), (20, 30), (30, 40))
    observations = [
        CpcvObservation(2, 3, 1.0),
        CpcvObservation(8, 11, 2.0),  # train entry, label overlaps test
        CpcvObservation(12, 13, 3.0),  # test
        CpcvObservation(20, 21, 4.0),  # embargo after test
        CpcvObservation(24, 25, 5.0),  # retained after embargo
    ]
    train, test, audit = split_path_observations(
        observations, groups, (1,), embargo_bars=4
    )
    assert train == [1.0, 5.0]
    assert test == [3.0]
    assert audit == {
        "candidate_train_trades": 4,
        "retained_train_trades": 2,
        "test_trades": 1,
        "purged_train_trades": 1,
        "embargoed_train_trades": 1,
    }


def test_pbo_selects_in_sample_winner_for_every_path_and_reports_full_distribution():
    configs = {
        "a": [CpcvObservation(i, i, 1.0 if i < 6 else -1.0) for i in range(12)],
        "b": [CpcvObservation(i, i, -1.0 if i < 6 else 1.0) for i in range(12)],
        "c": [CpcvObservation(i, i, 0.1) for i in range(12)],
    }
    result = compute_cpcv_pbo(
        configs, n_bars=12, n_groups=6, k_test=2, embargo_bars=0
    )
    assert result["parameters"]["n_paths"] == math.comb(6, 2)
    assert len(result["paths"]) == math.comb(6, 2)
    assert len(result["primary_pbo"]["logit_distribution"]["values"]) == math.comb(6, 2)
    for path in result["paths"]:
        winner = path["primary"]["train_winner"]
        train_means = {
            config_id: row["train"]["mean_r"]
            for config_id, row in path["configurations"].items()
        }
        expected = min(train_means, key=lambda config_id: (-train_means[config_id], config_id))
        assert winner == expected
