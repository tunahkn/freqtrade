#!/usr/bin/env python3
"""Diagnostic report generator for APEX SNIPER v8 strategy backtests.

Generates comprehensive analysis reports from trade autopsy CSV files, comparing
against sample backtests to identify performance drivers and parameter impacts.

Usage: python3 diagnostic_report.py <user_trades.csv> [--compare sample_file.csv]
"""
import argparse
import os
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trade_autopsy import pf


def load_autopsy(path):
    """Load trade autopsy CSV, handling both Python-generated and Pine-logged formats."""
    df = pd.read_csv(path)
    # Pine Logs uses "dt" (datetime), Python autopsy uses "dt" + other format
    # Ensure all expected columns exist
    expected = {'dir', 'score', 'comps', 'regime', 'exit_reason', 'r', 'mfe_r', 'mae_r', 'bars'}
    if not expected.issubset(df.columns):
        raise ValueError(f"Missing columns. Have: {set(df.columns)}, need: {expected}")
    return df.astype({'r': float, 'mfe_r': float, 'mae_r': float, 'bars': int})


def generate_report(df, label="Backtest"):
    """Generate detailed diagnostic report from trade autopsy."""
    print(f"\n{'='*70}")
    print(f"DIAGNOSTIC REPORT: {label}")
    print(f"{'='*70}")

    # Basic stats
    rs = df['r'].values
    n_trades = len(df)
    win_count = (rs > 0).sum()
    lose_count = (rs <= 0).sum()
    wr = 100 * win_count / n_trades if n_trades > 0 else 0
    pf_val = pf(rs)
    sum_r = rs.sum()
    avg_r = np.mean(rs)
    median_r = np.median(rs)

    print(f"\nOVERALL PERFORMANCE")
    print(f"  Trades: {n_trades}")
    print(f"  Win rate: {wr:.1f}% ({win_count}W / {lose_count}L)")
    print(f"  Profit factor: {pf_val:.2f}")
    print(f"  Sum R: {sum_r:.2f}")
    print(f"  Mean R: {avg_r:.2f}")
    print(f"  Median R: {median_r:.2f}")
    print(f"  Std Dev: {np.std(rs):.2f}")

    # Exit distribution
    print(f"\nEXIT REASON DISTRIBUTION")
    exit_groups = df.groupby('exit_reason')['r'].agg(['count', 'sum', 'mean', 'std'])
    for reason, row in exit_groups.iterrows():
        pf_reason = pf(df[df['exit_reason'] == reason]['r'].values)
        print(f"  {reason:6s}: n={int(row['count']):3d} sumR={row['sum']:7.2f} avgR={row['mean']:6.2f} PF={pf_reason:6.2f}")

    # BE-related analysis
    be_trades = df[df['exit_reason'] == 'BE']
    if len(be_trades) > 0:
        print(f"\nBREAKEVEN (BE) ANALYSIS")
        print(f"  Total BE exits: {len(be_trades)} ({100*len(be_trades)/n_trades:.0f}%)")
        print(f"  BE avg R: {be_trades['r'].mean():.3f}")
        print(f"  CF win potential: ", end="")
        cf_tp2 = (be_trades['cf'] == 'CF_TP2').sum()
        cf_sl = (be_trades['cf'] == 'CF_SL').sum()
        cf_open = (be_trades['cf'] == 'CF_OPEN').sum()
        print(f"{cf_tp2} CF_TP2 (would have won) + {cf_sl} CF_SL + {cf_open} CF_OPEN")
        print(f"  Insight: {cf_tp2} of {len(be_trades)} BE trades ({100*cf_tp2/len(be_trades):.0f}%) would have hit TP2 without BE management")

    # Regime performance
    print(f"\nREGIME PERFORMANCE")
    for regime in ['MOM', 'MR']:
        regime_df = df[df['regime'] == regime]
        if len(regime_df) > 0:
            regime_rs = regime_df['r'].values
            wr_regime = 100 * (regime_rs > 0).sum() / len(regime_df)
            pf_regime = pf(regime_rs)
            print(f"  {regime}: n={len(regime_df):3d} wr={wr_regime:5.1f}% PF={pf_regime:6.2f} avgR={np.mean(regime_rs):6.2f}")

    # Direction analysis
    print(f"\nDIRECTION PERFORMANCE")
    for direction in ['L', 'S']:
        dir_df = df[df['dir'] == direction]
        if len(dir_df) > 0:
            dir_rs = dir_df['r'].values
            wr_dir = 100 * (dir_rs > 0).sum() / len(dir_df)
            pf_dir = pf(dir_rs)
            print(f"  {direction}: n={len(dir_df):3d} wr={wr_dir:5.1f}% PF={pf_dir:6.2f} avgR={np.mean(dir_rs):6.2f}")

    # Score tier analysis
    print(f"\nSCORE TIER PERFORMANCE")
    for tier_label, max_score in [("14-15", 15), ("16-17", 17), ("18+", float('inf'))]:
        tier_df = df[(df['score'] > max_score - 2) & (df['score'] <= max_score)]
        if len(tier_df) > 0:
            tier_rs = tier_df['r'].values
            wr_tier = 100 * (tier_rs > 0).sum() / len(tier_df)
            pf_tier = pf(tier_rs)
            print(f"  {tier_label:5s}: n={len(tier_df):3d} wr={wr_tier:5.1f}% PF={pf_tier:6.2f} avgR={np.mean(tier_rs):6.2f}")

    # MAE/MFE analysis
    print(f"\nMAE/MFE STATISTICS (R units)")
    win_df = df[df['r'] > 0]
    lose_df = df[df['r'] <= 0]

    if len(win_df) > 0:
        maes = win_df['mae_r'].values
        print(f"  Winners (n={len(win_df)})")
        print(f"    MAE: mean={np.mean(maes):6.2f} median={np.median(maes):6.2f} " +
              f"p75={np.percentile(maes, 75):6.2f} p90={np.percentile(maes, 90):6.2f}")
        print(f"    Critical: {(maes >= 0.8).sum()} winners ({100*(maes >= 0.8).sum()/len(win_df):.0f}%) approached ≥0.8R MAE")

    if len(lose_df) > 0:
        mfes = lose_df['mfe_r'].values
        print(f"  Losers (n={len(lose_df)})")
        print(f"    MFE: mean={np.mean(mfes):6.2f} median={np.median(mfes):6.2f} " +
              f"p75={np.percentile(mfes, 75):6.2f} p90={np.percentile(mfes, 90):6.2f}")
        near_winners = (mfes >= 0.8).sum()
        print(f"    Critical: {near_winners} losers ({100*near_winners/len(lose_df):.0f}%) touched ≥0.8R favorable first")

    # Bars held analysis
    print(f"\nHOLDING TIME")
    bars_held = df['bars'].values
    print(f"  Mean: {np.mean(bars_held):.1f} bars")
    print(f"  Median: {np.median(bars_held):.0f} bars")
    print(f"  Mode (time-stop): {(bars_held == 20).sum()} trades at 20-bar limit")

    # Component activity (if available)
    if 'comps' in df.columns:
        print(f"\nCOMPONENT ACTIVITY")
        components = {}
        for comp_str in df['comps'].values:
            for i, letter in enumerate(comp_str):
                if letter != '-':
                    components[i] = components.get(i, 0) + 1
        comp_names = ['Bias', 'VWAP', 'Sweep', 'Struct', 'FVG', 'PDZ', 'CVD', 'POC']
        for i, name in enumerate(comp_names):
            if i in components:
                print(f"  {name:8s}: {components[i]:3d} trades ({100*components[i]/n_trades:5.1f}%)")

    return {
        'n': n_trades,
        'wr': wr,
        'pf': pf_val,
        'sum_r': sum_r,
        'avg_r': avg_r,
        'median_r': median_r,
    }


def compare_reports(user_stats, sample_stats, user_label, sample_label):
    """Compare user results against sample backtest."""
    print(f"\n{'='*70}")
    print(f"COMPARATIVE ANALYSIS")
    print(f"{'='*70}\n")

    metrics = [
        ('Win Rate', 'wr', '%'),
        ('Profit Factor', 'pf', 'x'),
        ('Avg R per Trade', 'avg_r', 'R'),
    ]

    print(f"{'Metric':20s} {user_label:20s} {sample_label:20s} {'Diff':>10s}")
    print("-" * 75)

    for label, key, unit in metrics:
        u_val = user_stats[key]
        s_val = sample_stats[key]
        if key == 'wr':
            diff = u_val - s_val
            diff_str = f"{diff:+.1f}%"
        elif key == 'pf':
            if s_val > 0:
                diff_pct = 100 * (u_val - s_val) / s_val
                diff_str = f"{diff_pct:+.0f}%"
            else:
                diff_str = "N/A"
        else:
            diff = u_val - s_val
            diff_str = f"{diff:+.3f}R"

        print(f"{label:20s} {u_val:7.2f}{unit:3s}  {s_val:7.2f}{unit:3s}  {diff_str:>10s}")


def main():
    ap = argparse.ArgumentParser(
        description="Generate diagnostic report from trade autopsy CSV"
    )
    ap.add_argument("csv", help="User trade autopsy CSV (from Pine Logs or Python backtest)")
    ap.add_argument("--compare", default=None, help="Sample backtest CSV to compare against")
    ap.add_argument("--sample-dir", default="results", help="Directory containing sample autopsy files")
    args = ap.parse_args()

    # Load user CSV
    user_df = load_autopsy(args.csv)
    user_label = Path(args.csv).stem.replace('autopsy_', '').replace('.csv', '')
    user_stats = generate_report(user_df, f"YOUR BACKTEST ({user_label})")

    # Load and compare against sample if provided
    if args.compare:
        sample_df = load_autopsy(args.compare)
        sample_label = Path(args.compare).stem.replace('autopsy_', '').replace('.csv', '')
        sample_stats = generate_report(sample_df, f"REFERENCE ({sample_label})")
        compare_reports(user_stats, sample_stats, user_label, sample_label)
    elif len(user_df) < 100:
        # Auto-detect reference sample (prefer BTC, then ETH, close to user trade count)
        sample_dir = Path(args.sample_dir)
        candidates = list(sample_dir.glob("autopsy_*.csv"))
        if candidates:
            sample_file = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]
            sample_df = load_autopsy(str(sample_file))
            sample_label = sample_file.stem.replace('autopsy_', '').replace('.csv', '')
            sample_stats = generate_report(sample_df, f"REFERENCE ({sample_label})")
            compare_reports(user_stats, sample_stats, user_label, sample_label)


if __name__ == "__main__":
    main()
