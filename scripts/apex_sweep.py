#!/usr/bin/env python3
"""
APEX golden-parameter sweep — robustness-first, anti-overfit.

Grids engine + exit params on REAL 2017-2019 Bitfinex data (BTC/ETH/LTC 4H),
costs included. Does NOT pick the single highest in-sample peak (that is
curve-fitting); instead ranks combos by how CONSISTENTLY they work across all
three assets, so the embedded default is a robust plateau, not a lucky spike.
2019-2026 is deliberately left untouched as the OOS confirmation set.

Objective per combo (aggregated over the 3 assets):
  hard gate : every asset PF > 1.0   (must generalize)
  rank key  : (# assets PF>1.2, mean net %, -mean maxDD)
"""
import sys, itertools
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, "scripts"); sys.path.insert(0, "user_data/strategies")
import apex_backtest as bt, apex_engine

ASSETS = {a: pd.read_feather(f"/tmp/apexdata/{a}_USD-4h.feather")
          for a in ("BTC", "ETH", "LTC")}

ENG = list(itertools.product([10, 11, 12], [6, 8, 10], [2, 3]))   # sniper, swing, mincats
EXIT = list(itertools.product([2.0, 2.5, 3.0], [3.0, 4.0], [150, 200]))  # atr_m, trail, ema

# cache engine computes (expensive) keyed by (asset, sniper, swing, mincats)
cache = {}
for a, raw in ASSETS.items():
    for s, sw, mc in ENG:
        cache[(a, s, sw, mc)] = apex_engine.compute(
            raw.copy(), {"sniper_th": s, "min_cats": mc, "swing_len": sw})


def metrics(df, atr_m, trail, ema_len):
    e = df.copy()
    ema = e["close"].ewm(span=ema_len, adjust=False).mean()
    e["enter_long"] = e["enter_long"] & (e["close"] > ema) & (ema.diff() > 0)
    tr, eq = bt.simulate(e, atr_m=atr_m, rr=2.0, risk_pct=1.0,
                         allow_long=True, allow_short=False, trail_atr_m=trail)
    if not tr:
        return None
    p = np.array([t["pnl"] for t in tr])
    gw, gl = p[p > 0].sum(), -p[p <= 0].sum()
    pf = gw / gl if gl > 0 else 99.0
    e_eq = 10000.0 + np.cumsum(p); pk = np.maximum.accumulate(e_eq)
    dd = ((e_eq - pk) / pk).min() * 100
    return pf, len(p), (eq - 10000) / 100.0, dd


rows = []
for (s, sw, mc) in ENG:
    for (atr_m, trail, ema_len) in EXIT:
        per = {}
        ok = True
        for a in ASSETS:
            m = metrics(cache[(a, s, sw, mc)], atr_m, trail, ema_len)
            if m is None:
                ok = False; break
            per[a] = m
        if not ok:
            continue
        pfs = [per[a][0] for a in ASSETS]
        nets = [per[a][2] for a in ASSETS]
        dds = [per[a][3] for a in ASSETS]
        n_tot = sum(per[a][1] for a in ASSETS)
        gate = all(pf > 1.0 for pf in pfs)
        n_strong = sum(1 for pf in pfs if pf > 1.2)
        rows.append(dict(sniper=s, swing=sw, mincats=mc, atr_m=atr_m, trail=trail,
                         ema=ema_len, n=n_tot, gate=gate, n_strong=n_strong,
                         mean_pf=np.mean(pfs), mean_net=np.mean(nets),
                         mean_dd=np.mean(dds), min_pf=min(pfs),
                         pf_btc=pfs[0], pf_eth=pfs[1], pf_ltc=pfs[2]))

df = pd.DataFrame(rows)
gated = df[df.gate].copy()
print(f"Total combos: {len(df)}   passing 'all 3 PF>1.0' gate: {len(gated)}")
gated = gated.sort_values(["n_strong", "mean_net", "mean_dd"],
                          ascending=[False, False, True])
print("\n=== TOP 12 ROBUST COMBOS (real 2017-2019, costs in) ===")
print(f"{'snip':>4} {'sw':>3} {'mc':>3} {'atr':>4} {'trl':>4} {'ema':>4} | "
      f"{'mPF':>5} {'mNet%':>7} {'mDD%':>6} {'strong':>6} {'n':>4} | "
      f"{'BTC':>5} {'ETH':>5} {'LTC':>5}")
for _, r in gated.head(12).iterrows():
    print(f"{r.sniper:>4.0f} {r.swing:>3.0f} {r.mincats:>3.0f} {r.atr_m:>4.1f} "
          f"{r.trail:>4.1f} {r.ema:>4.0f} | {r.mean_pf:>5.2f} {r.mean_net:>7.1f} "
          f"{r.mean_dd:>6.1f} {r.n_strong:>6.0f} {r.n:>4.0f} | "
          f"{r.pf_btc:>5.2f} {r.pf_eth:>5.2f} {r.pf_ltc:>5.2f}")
