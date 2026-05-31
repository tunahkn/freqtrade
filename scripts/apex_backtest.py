#!/usr/bin/env python3
"""
Standalone APEX SNIPER backtest — no freqtrade install required.

Replays the apex_engine signals on an OHLCV feather file and simulates the
v9 exit model (tiered TP1/TP2/TP3 thirds, ATR stop, fee-aware breakeven after
TP1), then prints honest performance stats.

Usage:
  python scripts/apex_backtest.py <feather> [--short] [--fee 0.05] [--slip 0.02]
  python scripts/apex_backtest.py tests/testdata/UNITTEST_BTC-5m.feather

Notes / honesty:
  * Intrabar fills are approximated bar-by-bar with a conservative
    "stop-checked-before-target" rule.
  * Test datasets here are tiny/synthetic — stats demonstrate the PIPELINE,
    not a tradeable edge. Run on real, large data + out-of-sample to judge.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "user_data" / "strategies"))
import apex_engine  # noqa: E402


def simulate(df, fee=0.05, slip=0.02, atr_m=1.5, rr=2.0, risk_pct=1.0,
             fee_buf=0.12, allow_long=True, allow_short=True,
             scaleout=False, trail=True, trail_atr_m=2.5, trail_start=1.0):
    """Bar-by-bar simulation. Risk-based sizing: each trade risks risk_pct of equity.

    Exit model (v9.1):
      scaleout=True  -> original tiered thirds at TP1/TP2/TP3
      scaleout=False -> single position; trailing ATR stop (trail=True) lets
                        winners run, otherwise fixed stop + TP3.
    """
    fee_f = fee / 100.0
    slip_f = slip / 100.0
    equity = 10000.0
    trades = []
    pos = None  # dict

    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    atr = df["atr"].values
    el, es = df["enter_long"].values, df["enter_short"].values

    for i in range(len(df)):
        # ---- manage open position on this bar ----
        if pos is not None:
            long = pos["long"]
            sd = pos["sd"]
            # update running extreme + ATR trailing stop (ratchet only)
            pos["run_hi"] = max(pos["run_hi"], h[i])
            pos["run_lo"] = min(pos["run_lo"], l[i])
            r_now = (c[i] - pos["entry"]) / sd if long else (pos["entry"] - c[i]) / sd
            if trail and r_now >= trail_start and not np.isnan(atr[i]):
                cand = (pos["run_hi"] - atr[i] * trail_atr_m) if long \
                    else (pos["run_lo"] + atr[i] * trail_atr_m)
                pos["stop"] = max(pos["stop"], cand) if long else min(pos["stop"], cand)

            # STOP first (conservative)
            stop_hit = (l[i] <= pos["stop"]) if long else (h[i] >= pos["stop"])
            if stop_hit:
                px = pos["stop"] * (1 - slip_f if long else 1 + slip_f)
                _book(pos, px, pos["qty_rem"], fee_f, trades, i)
                equity += pos["realized"]
                pos = None
                continue

            if scaleout:
                for k, tp in enumerate(("tp1", "tp2", "tp3")):
                    if pos["done"][k]:
                        continue
                    hit = (h[i] >= pos[tp]) if long else (l[i] <= pos[tp])
                    if not hit:
                        break
                    qty = min(pos["qty0"] * pos["frac"][k], pos["qty_rem"])
                    px = pos[tp] * (1 - slip_f if long else 1 + slip_f)
                    _partial(pos, px, qty, fee_f, long)
                    pos["done"][k] = True
                    pos["qty_rem"] -= qty
                    if k == 0:
                        buf = pos["entry"] * fee_buf / 100.0
                        pos["stop"] = pos["entry"] + buf if long else pos["entry"] - buf
                    if pos["qty_rem"] <= 1e-12:
                        break
            elif not trail:
                # fixed TP3 target for whole position
                hit = (h[i] >= pos["tp3"]) if long else (l[i] <= pos["tp3"])
                if hit:
                    px = pos["tp3"] * (1 - slip_f if long else 1 + slip_f)
                    _book(pos, px, pos["qty_rem"], fee_f, trades, i)
                    equity += pos["realized"]
                    pos = None
                    continue

            if pos is not None and pos["qty_rem"] <= 1e-12:
                equity += pos["realized"]
                trades.append(_finalize(pos, i))
                pos = None
                continue

        # ---- new entry (only flat) ----
        if pos is None and i + 1 < len(df) and not np.isnan(atr[i]) and atr[i] > 0:
            go_long = el[i] and allow_long
            go_short = es[i] and allow_short
            if go_long or go_short:
                long = bool(go_long and (not go_short))
                if go_long and go_short:
                    long = df["score_long"].values[i] >= df["score_short"].values[i]
                # fill at next bar open (no lookahead on signal bar close)
                entry = o[i + 1] * (1 + slip_f if long else 1 - slip_f)
                sd = atr[i] * atr_m
                risk_cap = equity * risk_pct / 100.0
                qty0 = risk_cap / sd
                stop = entry - sd if long else entry + sd
                tp1 = entry + sd * rr if long else entry - sd * rr
                tp2 = entry + sd * rr * 2 if long else entry - sd * rr * 2
                tp3 = entry + sd * rr * 3 if long else entry - sd * rr * 3
                pos = dict(long=long, entry=entry, stop=stop, sd=sd, tp1=tp1, tp2=tp2, tp3=tp3,
                           qty0=qty0, qty_rem=qty0, frac=[0.3333, 0.3333, 0.3334],
                           done=[False, False, False], realized=0.0, ebar=i + 1,
                           run_hi=o[i + 1], run_lo=o[i + 1],
                           entry_fee=entry * qty0 * fee_f)
                pos["realized"] -= pos["entry_fee"]

    # close any dangling position at last close
    if pos is not None:
        px = c[-1]
        _partial(pos, px, pos["qty_rem"], fee_f, pos["long"])
        pos["qty_rem"] = 0
        equity += pos["realized"]
        trades.append(_finalize(pos, len(df) - 1))

    return trades, equity


def _partial(pos, px, qty, fee_f, long):
    pnl = (px - pos["entry"]) * qty if long else (pos["entry"] - px) * qty
    pos["realized"] += pnl - px * qty * fee_f


def _book(pos, px, qty, fee_f, trades, i):
    _partial(pos, px, qty, fee_f, pos["long"])
    pos["qty_rem"] = 0
    trades.append(_finalize(pos, i))


def _finalize(pos, i):
    return dict(long=pos["long"], entry=pos["entry"], pnl=pos["realized"],
                bars=i - pos["ebar"])


def stats(trades, final_eq, start=10000.0):
    n = len(trades)
    if n == 0:
        return "No trades generated."
    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    gross_win = wins.sum()
    gross_loss = -losses.sum()
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    win_rate = len(wins) / n * 100

    # equity curve & max drawdown
    eq = start + np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak)
    max_dd = dd.min()
    max_dd_pct = (dd / peak).min() * 100

    avg_r = pnls.mean()
    expectancy = avg_r
    ret_pct = (final_eq - start) / start * 100

    longs = sum(1 for t in trades if t["long"])
    out = []
    out.append(f"  Trades            : {n}  (long {longs} / short {n-longs})")
    out.append(f"  Win rate          : {win_rate:.1f}%")
    out.append(f"  Profit factor     : {pf:.2f}")
    out.append(f"  Net PnL           : {pnls.sum():+.2f}  ({ret_pct:+.2f}%)")
    out.append(f"  Avg trade (exp.)  : {expectancy:+.2f}")
    out.append(f"  Best / Worst      : {pnls.max():+.2f} / {pnls.min():+.2f}")
    out.append(f"  Max drawdown      : {max_dd:+.2f}  ({max_dd_pct:.2f}%)")
    out.append(f"  Final equity      : {final_eq:.2f}")
    out.append(f"  Avg bars in trade : {np.mean([t['bars'] for t in trades]):.1f}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("feather")
    ap.add_argument("--fee", type=float, default=0.05)
    ap.add_argument("--slip", type=float, default=0.02)
    ap.add_argument("--atr-m", type=float, default=1.5)
    ap.add_argument("--rr", type=float, default=1.5)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--no-long", action="store_true")
    ap.add_argument("--no-short", action="store_true")
    ap.add_argument("--sniper-th", type=int, default=14)
    args = ap.parse_args()

    df = pd.read_feather(args.feather)
    df = apex_engine.compute(df, {"sniper_th": args.sniper_th})

    print(f"Data: {Path(args.feather).name}  bars={len(df)}  "
          f"{df['date'].min()} -> {df['date'].max()}")
    print(f"Signals: long={int(df['enter_long'].sum())}  short={int(df['enter_short'].sum())}")
    trades, final_eq = simulate(
        df, fee=args.fee, slip=args.slip, atr_m=args.atr_m, rr=args.rr,
        risk_pct=args.risk, allow_long=not args.no_long, allow_short=not args.no_short)
    print("-" * 50)
    print(stats(trades, final_eq))


if __name__ == "__main__":
    main()
