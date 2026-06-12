#!/usr/bin/env python3
"""Signal follow-through test for APEX SNIPER v8 mandatory-gate variants.

Measures, for each gate design, the probability that price reaches +1R
before -1R after a sniper signal ("no immediate reversal"), plus PF with
the hit-and-run execution model. Result: G1 (structure break mandatory)
improved follow-through on 3 of 4 datasets and is now the Pine default
(require_struct input).

Usage: python3 gate_test.py <eth 4h csv> <btc 4h csv>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from apex_backtest import compute_features, scores, run, load
from bottleneck_analysis import resample_1d

STOP_MULT = 2.0


def one_r_first(f, sig_l, sig_s):
    """For each signal bar: does +1R print before -1R? (entry at close)"""
    h, l, c, atr, n = f["h"], f["l"], f["c"], f["atr"], f["n"]
    hits = total = 0
    for i in range(n - 1):
        d = 1 if sig_l[i] else (-1 if sig_s[i] else 0)
        if d == 0 or np.isnan(atr[i]) or atr[i] <= 0:
            continue
        r = atr[i] * STOP_MULT
        tp, st = c[i] + d * r, c[i] - d * r
        for j in range(i + 1, min(i + 80, n)):
            hit_tp = h[j] >= tp if d == 1 else l[j] <= tp
            hit_st = l[j] <= st if d == 1 else h[j] >= st
            if hit_st:  # conservative: stop wins ties
                total += 1
                break
            if hit_tp:
                hits += 1
                total += 1
                break
    return hits, total


def test(name, df, th, window=5):
    f = compute_features(df)
    ls, ss = scores(f, window)
    years = (df["dt"].iloc[-1] - df["dt"].iloc[0]).days / 365.25
    gates = {
        "G0 score only": (np.ones(f["n"], bool), np.ones(f["n"], bool)),
        "G1 +struct mandatory": (f["bs_struct_l"] <= window, f["bs_struct_s"] <= window),
        "G2 +sweep|fvg mandatory": (
            (f["bs_ssl"] <= window) | (f["bs_fvg_l"] <= window),
            (f["bs_bsl"] <= window) | (f["bs_fvg_s"] <= window)),
        "G3 struct AND (sweep|fvg)": (
            (f["bs_struct_l"] <= window) & ((f["bs_ssl"] <= window) | (f["bs_fvg_l"] <= window)),
            (f["bs_struct_s"] <= window) & ((f["bs_bsl"] <= window) | (f["bs_fvg_s"] <= window))),
    }
    print(f"\n════ {name} (th={th}, w={window}) ════")
    print(f"{'gate':28s} {'sig/yr':>7s} {'1R-first':>9s} {'trades':>7s} {'wr%':>5s} {'PF':>6s} {'ret%':>7s} {'DD%':>5s}")
    for gname, (gl, gs) in gates.items():
        sl = (ls >= th) & (ls > ss) & gl
        sh = (ss >= th) & (ss > ls) & gs
        hits, total = one_r_first(f, sl, sh)
        p = 100 * hits / total if total else float("nan")
        r = run(f, np.where(sl, ls, 0), np.where(sh, ss, 0), th, STOP_MULT, 2.5, tp1_r=1.0, time_stop=20)
        print(f"{gname:28s} {(sl|sh).sum()/years:7.1f} {p:8.1f}% {r['trades']:7d} {r['winrate']:5.1f} {r['pf']:6.2f} {r['ret']:7.2f} {r['maxdd']:5.2f}")


if __name__ == "__main__":
    eth4 = load(sys.argv[1])
    btc4 = load(sys.argv[2])
    test("ETH 4h", eth4, 14)
    test("BTC 4h", btc4, 14)
    test("ETH 1D", resample_1d(eth4), 12)
    test("BTC 1D", resample_1d(btc4), 12)
