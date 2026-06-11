#!/usr/bin/env python3
"""
APEX regime research — diagnoses WHY the engine lost 13% while BTC did +647%.

Hypothesis: the SMC long edge only exists in established uptrends; the 2018
bear chops the tight ATR stops to death. If true, one principled, a-priori
regime gate (price above a rising EMA200 — the most standard institutional
trend filter, NOT tuned on this data) should rescue the full cycle by
sitting out the bear.

Also runs the humbling control: a 3-line "dumb" EMA200-cross entry with the
SAME exit model. If dumb beats smart, the SMC machinery is negative value.

Usage:
  python scripts/apex_regime_research.py /tmp/apexdata/*-4h.feather
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "user_data" / "strategies"))
import apex_backtest as bt  # noqa: E402
import apex_engine  # noqa: E402


def pf_of(trades):
    if not trades:
        return float("nan"), 0, 0.0
    pnls = np.array([t["pnl"] for t in trades])
    gw = pnls[pnls > 0].sum()
    gl = -pnls[pnls <= 0].sum()
    pf = gw / gl if gl > 0 else float("inf")
    return pf, len(pnls), pnls.sum()


def run(df, label, results):
    trades, eq = bt.simulate(df, atr_m=1.0, rr=2.0, risk_pct=1.0,
                             allow_long=True, allow_short=False)
    pf, n, _ = pf_of(trades)
    results.append((label, n, pf, (eq - 10000.0) / 100.0))


def main():
    for f in sys.argv[1:]:
        raw = pd.read_feather(f)
        df = apex_engine.compute(raw, {"sniper_th": 11, "min_cats": 2,
                                       "swing_len": 8})
        ema = df["close"].ewm(span=200, adjust=False).mean()
        rising = ema.diff() > 0
        bh = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
        results = []

        run(df, "A apex no-gate", results)

        dB = df.copy()
        dB["enter_long"] = dB["enter_long"] & (dB["close"] > ema)
        run(dB, "B apex >EMA200", results)

        dC = df.copy()
        dC["enter_long"] = dC["enter_long"] & (dC["close"] > ema) & rising
        run(dC, "C apex >EMA200+rising", results)

        dD = df.copy()
        x = (dD["close"] > ema) & (dD["close"].shift(1) <= ema.shift(1))
        dD["enter_long"] = x.fillna(False).values
        dD["enter_short"] = np.zeros(len(dD), dtype=bool)
        run(dD, "D dumb EMA200-cross", results)

        print(f"\n=== {Path(f).stem}   (B&H {bh:+.0f}%) ===")
        for label, n, pf, ret in results:
            print(f"  {label:<24} n={n:<4} PF={pf:5.2f}  net={ret:+7.1f}%")

        # per-year diagnosis of the gated variant (C)
        dC["year"] = pd.to_datetime(dC["date"]).dt.year
        for y in sorted(dC["year"].unique()):
            sl = dC[dC["year"] == y].reset_index(drop=True)
            if len(sl) < 250:
                continue
            tr, eq = bt.simulate(sl, atr_m=1.0, rr=2.0, risk_pct=1.0,
                                 allow_long=True, allow_short=False)
            pf, n, _ = pf_of(tr)
            yb = (sl["close"].iloc[-1] / sl["close"].iloc[0] - 1) * 100
            print(f"    {y}: C-gated  n={n:<3} PF={pf:5.2f} "
                  f"net={(eq-10000)/100:+6.1f}%   (yil B&H {yb:+.0f}%)")


if __name__ == "__main__":
    main()
