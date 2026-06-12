#!/usr/bin/env python3
"""Confluence-chain bottleneck analysis for APEX SNIPER v8.

For each component: how often it is active, and how often it is the missing
piece on "near-miss" bars (score within 3 points below the threshold).
Also prints signal frequency per threshold and a first-half/second-half
walk-forward split at the current execution defaults.

Usage: python3 bottleneck_analysis.py <4h csv> [more csvs...]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from apex_backtest import compute_features, scores, run, load

TH = 14
WINDOW = 5


def resample_1d(df: pd.DataFrame) -> pd.DataFrame:
    df = df.set_index("dt")
    out = pd.DataFrame({
        "open": df["open"].resample("1D").first(),
        "high": df["high"].resample("1D").max(),
        "low": df["low"].resample("1D").min(),
        "close": df["close"].resample("1D").last(),
        "volume": df["volume"].resample("1D").sum(),
    }).dropna().reset_index()
    out["ts"] = out["dt"].astype("int64") // 10**9
    return out


def components(f: dict, window: int):
    """Per-side component activity (pts > 0); mirrors scores()."""
    def side(bias, vwap, sweep, struct, fvg, pdz, cvd, poc):
        return {
            "HTF bias (3p)": bias, "Anch. VWAP (2p)": vwap,
            "Liq sweep (3p)": sweep, "Structure (2-4p)": struct,
            "FVG tap (2p)": fvg, "Prem/Disc+OTE (1-3p)": pdz,
            "CVD div (1p)": cvd, "POC reclaim (1p)": poc,
        }
    long_c = side(f["bias_long"], f["vwap_l"], f["bs_ssl"] <= window,
                  f["bs_struct_l"] <= window, f["bs_fvg_l"] <= window,
                  f["pd_l"], f["cvd_l"], f["bs_poc_up"] <= window)
    short_c = side(f["bias_short"], f["vwap_s"], f["bs_bsl"] <= window,
                   f["bs_struct_s"] <= window, f["bs_fvg_s"] <= window,
                   f["pd_s"], f["cvd_s"], f["bs_poc_dn"] <= window)
    return long_c, short_c


def analyze(name: str, df: pd.DataFrame, th: int = TH, window: int = WINDOW):
    f = compute_features(df)
    ls, ss = scores(f, window)
    years = (df["dt"].iloc[-1] - df["dt"].iloc[0]).days / 365.25
    comps_l, comps_s = components(f, window)

    print(f"\n════ {name}: {len(df)} bars, {years:.1f}y ════")
    print(f"{'component':24s} {'long%':>6s} {'short%':>7s}  near-miss absence L/S")
    near_l = (ls >= th - 3) & (ls < th)
    near_s = (ss >= th - 3) & (ss < th)
    for k in comps_l:
        al, ash = comps_l[k], comps_s[k]
        miss_l = 100 * (~al[near_l]).mean() if near_l.any() else float("nan")
        miss_s = 100 * (~ash[near_s]).mean() if near_s.any() else float("nan")
        print(f"{k:24s} {100*al.mean():5.1f}% {100*ash.mean():6.1f}%   {miss_l:5.1f}% / {miss_s:5.1f}%")
    print(f"near-miss bars (th-3..th-1): L {near_l.sum()}, S {near_s.sum()}")

    print(f"\n{'th':>3s} {'sig/yr':>7s} {'trades':>7s} {'PF':>5s} {'ret%':>7s} {'DD%':>5s}")
    for t in (11, 12, 13, 14, 15):
        sig = ((ls >= t) & (ls > ss)) | ((ss >= t) & (ss > ls))
        r = run(f, ls, ss, t, 2.0, 2.5, tp1_r=1.0, time_stop=20)
        print(f"{t:3d} {sig.sum()/years:7.1f} {r['trades']:7d} {r['pf']:5.2f} {r['ret']:7.2f} {r['maxdd']:5.2f}")

    half = len(df) // 2
    for tag, sl in (("first half", slice(0, half)), ("second half", slice(half, None))):
        d2 = df.iloc[sl].reset_index(drop=True)
        f2 = compute_features(d2)
        l2, s2 = scores(f2, window)
        r = run(f2, l2, s2, th, 2.0, 2.5, tp1_r=1.0, time_stop=20)
        print(f"WF {tag:12s} trades={r['trades']:3d} wr={r['winrate']:.0f}% PF={r['pf']:.2f} ret={r['ret']:.2f}% DD={r['maxdd']:.2f}%")


if __name__ == "__main__":
    for path in sys.argv[1:]:
        df4 = load(path)
        base = os.path.basename(path)
        analyze(f"{base} (4h)", df4)
        analyze(f"{base} (1D resampled, th=12)", resample_1d(df4), th=12)
