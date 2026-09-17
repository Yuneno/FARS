import json
from pathlib import Path
import sys
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

M8_DIR = Path(__file__).resolve().parent
MARKETS = ["MNQ", "MES", "MYM", "MGC"]
ARMS = [
    "control_m7",
    "atr_k050",
    "atr_k100",
    "atr_k050_ote_band",
    "atr_k050_trend_kai",
    "atr_k050_premium_discount",
    "wrapper_trivial",
]
SCENARIOS = ["canonico", "canonico_mas_1tick", "realista"]

def main():
    data = {}
    for m in MARKETS:
        data[m] = {}
        for a in ARMS:
            data[m][a] = {}
            for s in SCENARIOS:
                fpath = M8_DIR / f"metrics_{m}_{a}_{s}.json"
                cell = json.loads(fpath.read_text(encoding="utf-8"))
                data[m][a][s] = cell

    # Print markdown tables per market
    for m in MARKETS:
        print(f"\n## Mercado: {m}\n")
        for s in SCENARIOS:
            print(f"### Escenario: `{s}`\n")
            print("| Arm | n | E[R] | d E[R] (vs k0.5) | PF | WR (%) | Net R | Max DD (R) | Folds+ | IC95 CBB |")
            print("|---|---|---|---|---|---|---|---|---|---|")
            ref_cell = data[m]["atr_k050"][s]["aggregate"]
            ref_er = ref_cell["mean_er"]

            for a in ARMS:
                agg = data[m][a][s]["aggregate"]
                delta_er = agg["mean_er"] - ref_er
                delta_str = f"{delta_er:+.4f}" if a != "atr_k050" else "-"
                ci = agg["cbb_bootstrap_ci95"]
                ci_str = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "[N/A]"
                wr_str = f"{agg['win_rate']*100:.1f}%"
                print(
                    f"| `{a}` | {agg['n_trades']} | {agg['mean_er']:+.4f} | {delta_str} | "
                    f"{agg['profit_factor']:.3f} | {wr_str} | {agg['net_r']:+.1f} | "
                    f"{agg['max_drawdown_r']:.1f}R | {agg['positive_folds']}/{agg['total_folds']} | {ci_str} |"
                )
            print()

if __name__ == "__main__":
    main()
