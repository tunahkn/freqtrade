#!/usr/bin/env python3
"""
APEX institutional validator — turns "I think it's good" into an evidence table.

A trading desk does not green-light a strategy on profit factor alone. It asks:
  * Risk-adjusted: Sharpe / Sortino (return per unit of risk, not raw return).
  * Robust: would it survive a different ordering of the same trades? (Monte Carlo)
  * Alpha: does it beat buy & hold, or did the market just trend?
  * Significant: >= 100 trades, positive expectancy after costs.

This runs the apex_engine on one or many feather files and prints a PASS/FAIL
scorecard against those gates. No gate is fudged — a small/synthetic dataset
will (correctly) FAIL the significance gate.

Usage:
  python scripts/apex_validate.py <feather> [<feather> ...] [--rr 2 --risk 1]
  python scripts/apex_validate.py tests/testdata/*-5m.feather
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apex_backtest as bt  # reuse the honest simulator


def institutional_metrics(trades, final_eq, df, start=10000.0, bars_per_year=None):
    """Compute the metrics a desk actually looks at."""
    n = len(trades)
    if n == 0:
        return None
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    rets = pnls / start  # per-trade return on starting equity (approx)

    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    gross_win = wins.sum()
    gross_loss = -losses.sum()
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    win_rate = len(wins) / n * 100.0
    expectancy = pnls.mean()

    # risk-adjusted (per-trade Sharpe/Sortino, annualized by trade count proxy)
    sd = rets.std(ddof=1) if n > 1 else 0.0
    downside = rets[rets < 0]
    dsd = downside.std(ddof=1) if len(downside) > 1 else 0.0
    sharpe = (rets.mean() / sd * np.sqrt(n)) if sd > 0 else 0.0
    sortino = (rets.mean() / dsd * np.sqrt(n)) if dsd > 0 else 0.0

    # equity curve & max drawdown
    eq = start + np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    max_dd_pct = ((eq - peak) / peak).min() * 100.0

    # buy & hold alpha
    c = df["close"].values
    bh = (c[-1] / c[0] - 1) * 100.0
    strat = (final_eq - start) / start * 100.0
    alpha = strat - bh

    # Monte Carlo: reshuffle trade order 2000x -> distribution of outcomes.
    # Tests path-robustness: a real edge survives reordering; a lucky streak doesn't.
    rng = np.random.default_rng(42)
    mc_finals = []
    mc_dds = []
    for _ in range(2000):
        perm = rng.permutation(pnls)
        e = start + np.cumsum(perm)
        pk = np.maximum.accumulate(e)
        mc_finals.append(e[-1])
        mc_dds.append(((e - pk) / pk).min() * 100.0)
    mc_finals = np.array(mc_finals)
    mc_dds = np.array(mc_dds)
    mc_profit_prob = (mc_finals > start).mean() * 100.0
    mc_dd_p95 = np.percentile(mc_dds, 5)  # worst 5% drawdown

    return dict(n=n, win_rate=win_rate, pf=pf, expectancy=expectancy,
                sharpe=sharpe, sortino=sortino, max_dd=max_dd_pct,
                strat=strat, bh=bh, alpha=alpha,
                mc_profit_prob=mc_profit_prob, mc_dd_p95=mc_dd_p95)


def scorecard(name, m):
    if m is None:
        return f"  {name:<22} : NO TRADES"
    # gates a desk would apply
    g_n     = m["n"] >= 100
    g_pf    = m["pf"] > 1.3
    g_sharpe= m["sharpe"] > 1.0
    g_alpha = m["alpha"] > 0
    g_mc    = m["mc_profit_prob"] > 95.0
    passed = sum([g_n, g_pf, g_sharpe, g_alpha, g_mc])

    def mark(b):
        return "✅" if b else "❌"

    lines = [
        f"  ── {name} " + "─" * max(0, 40 - len(name)),
        f"     Trades        {m['n']:>8}      {mark(g_n)} (>=100)",
        f"     Profit factor {m['pf']:>8.2f}      {mark(g_pf)} (>1.3)",
        f"     Win rate      {m['win_rate']:>7.1f}%",
        f"     Expectancy    {m['expectancy']:>8.2f}",
        f"     Sharpe        {m['sharpe']:>8.2f}      {mark(g_sharpe)} (>1.0)",
        f"     Sortino       {m['sortino']:>8.2f}",
        f"     Max DD        {m['max_dd']:>7.1f}%",
        f"     Strategy ret  {m['strat']:>7.1f}%",
        f"     Buy & hold    {m['bh']:>7.1f}%",
        f"     ALPHA         {m['alpha']:>+7.1f}%      {mark(g_alpha)} (>0)",
        f"     MC profit p.  {m['mc_profit_prob']:>7.1f}%      {mark(g_mc)} (>95%)",
        f"     MC DD (p95)   {m['mc_dd_p95']:>7.1f}%",
        f"     VERDICT       {passed}/5 gates  "
        + ("🏆 INSTITUTIONAL-GRADE" if passed == 5
           else "🟡 PROMISING" if passed >= 3
           else "🔴 NOT TRADEABLE"),
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("feathers", nargs="+")
    ap.add_argument("--fee", type=float, default=0.05)
    ap.add_argument("--slip", type=float, default=0.02)
    ap.add_argument("--atr-m", type=float, default=1.0)
    ap.add_argument("--rr", type=float, default=2.0)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--no-short", action="store_true")
    ap.add_argument("--long-regime", action="store_true",
                    help="gate longs: close > rising EMA200 (bear guard)")
    ap.add_argument("--trail-atr-m", type=float, default=2.5)
    ap.add_argument("--sniper-th", type=int, default=14)
    ap.add_argument("--min-cats", type=int, default=2)
    ap.add_argument("--swing-len", type=int, default=8)
    args = ap.parse_args()

    import apex_engine
    print("=" * 56)
    print(" APEX INSTITUTIONAL VALIDATOR")
    print("=" * 56)
    agg = []
    for f in args.feathers:
        df = pd.read_feather(f)
        df = apex_engine.compute(df, {"sniper_th": args.sniper_th,
                                      "min_cats": args.min_cats,
                                      "swing_len": args.swing_len})
        if args.long_regime:
            ema = df["close"].ewm(span=200, adjust=False).mean()
            df["enter_long"] = df["enter_long"] & (df["close"] > ema) & (ema.diff() > 0)
        trades, final_eq = bt.simulate(
            df, fee=args.fee, slip=args.slip, atr_m=args.atr_m, rr=args.rr,
            risk_pct=args.risk, allow_long=True, allow_short=not args.no_short,
            trail_atr_m=args.trail_atr_m)
        m = institutional_metrics(trades, final_eq, df)
        print(scorecard(Path(f).stem, m))
        if m:
            agg.append(m)
    if len(agg) > 1:
        print("\n  " + "=" * 44)
        avg_alpha = np.mean([m["alpha"] for m in agg])
        n_pos_alpha = sum(1 for m in agg if m["alpha"] > 0)
        tot_trades = sum(m["n"] for m in agg)
        print(f"  PORTFOLIO  : {len(agg)} assets, {tot_trades} trades")
        print(f"  Avg alpha  : {avg_alpha:+.1f}%   "
              f"({n_pos_alpha}/{len(agg)} assets beat buy&hold)")
        print(f"  CROSS-ASSET VERDICT: "
              + ("✅ generalizes" if n_pos_alpha == len(agg)
                 else "⚠️ asset-specific" if n_pos_alpha >= len(agg) / 2
                 else "❌ does not generalize"))
    print()
    print("  Note: small/synthetic data SHOULD fail the >=100 trade gate.")
    print("  Run on real BTC/ETH/SOL 4H exports to judge the edge.")


if __name__ == "__main__":
    main()
