#!/usr/bin/env python3
"""Trade autopsy for APEX SNIPER v8: per-trade log + diagnostic report.

Replays the current strategy defaults (struct gate, TP1 50% @1R + BE,
time-stop 20, stop 2xATR, TP2 2.5R) and records for every trade:
direction, entry score, active components, regime, exit reason, net R,
MFE/MAE in R, bars held, and a counterfactual outcome with no TP1/BE
management (fixed SL to TP2) from the same entry.

Usage: python3 trade_autopsy.py <4h csv> [--csv out.csv]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from apex_backtest import compute_features, scores, load, FEE_PCT

TH = 14
WINDOW = 5
STOP_MULT = 2.0
PAYOFF = 2.5
TP1_R = 1.0
TP1_PCT = 0.5
TIME_STOP = 20


def counterfactual(f, i, d, dist):
    """No-management outcome from entry bar i: fixed SL vs TP2, no BE/TP1."""
    h, l, c, n = f["h"], f["l"], f["c"], f["n"]
    e = c[i]
    sl, tp = e - d * dist, e + d * dist * PAYOFF
    for j in range(i + 1, min(i + 200, n)):
        hit_sl = l[j] <= sl if d == 1 else h[j] >= sl
        hit_tp = h[j] >= tp if d == 1 else l[j] <= tp
        if hit_sl:
            return "CF_SL"
        if hit_tp:
            return "CF_TP2"
    return "CF_OPEN"


def simulate(f, ls, ss):
    h, l, c, atr, n = f["h"], f["l"], f["c"], f["atr"], f["n"]
    gate_l = f["bs_struct_l"] <= WINDOW
    gate_s = f["bs_struct_s"] <= WINDOW
    sig_l = (ls >= TH) & (ls > ss) & gate_l
    sig_s = (ss >= TH) & (ss > ls) & gate_s

    comps = lambda i, side: "".join([
        "B" if (f["bias_long"][i] if side > 0 else f["bias_short"][i]) else "-",
        "V" if (f["vwap_l"][i] if side > 0 else f["vwap_s"][i]) else "-",
        "S" if ((f["bs_ssl"][i] if side > 0 else f["bs_bsl"][i]) <= WINDOW) else "-",
        "T" if ((f["bs_struct_l"][i] if side > 0 else f["bs_struct_s"][i]) <= WINDOW) else "-",
        "F" if ((f["bs_fvg_l"][i] if side > 0 else f["bs_fvg_s"][i]) <= WINDOW) else "-",
        "P" if (f["pd_l"][i] if side > 0 else f["pd_s"][i]) else "-",
        "C" if (f["cvd_l"][i] if side > 0 else f["cvd_s"][i]) else "-",
        "O" if ((f["bs_poc_up"][i] if side > 0 else f["bs_poc_dn"][i]) <= WINDOW) else "-",
    ])

    trades = []
    pos = 0
    qty = entry = stop = tp1 = tp2 = dist = 0.0
    tp1_done = False
    tp1_cash = 0.0
    ei = 0
    meta = {}
    hi_ext = lo_ext = 0.0

    def fee(px, q):
        return FEE_PCT * q * (entry + px)

    def record(px, reason, q):
        pnl = q * (px - entry) * pos - fee(px, q) + tp1_cash
        risk = meta["qty0"] * dist  # risk cash at entry = full qty * stop distance
        mfe = (hi_ext - entry) / dist if pos == 1 else (entry - lo_ext) / dist
        mae = (entry - lo_ext) / dist if pos == 1 else (hi_ext - entry) / dist
        trades.append(dict(meta, exit_reason=reason, r=pnl / risk,
                           mfe_r=mfe, mae_r=mae, bars=0))

    for i in range(1, n):
        if pos != 0:
            hi_ext = max(hi_ext, h[i])
            lo_ext = min(lo_ext, l[i])
            done = False
            if not tp1_done:
                hit_stop = l[i] <= stop if pos == 1 else h[i] >= stop
                hit_tp1 = h[i] >= tp1 if pos == 1 else l[i] <= tp1
                if hit_stop:
                    record(stop, "SL", qty)
                    trades[-1]["bars"] = i - ei
                    pos = 0
                    done = True
                elif hit_tp1:
                    part = qty * TP1_PCT
                    tp1_cash = part * (tp1 - entry) * pos - fee(tp1, part)
                    qty -= part
                    tp1_done = True
                    stop = entry  # BE
                elif i - ei >= TIME_STOP:
                    record(c[i], "TIME", qty)
                    trades[-1]["bars"] = i - ei
                    pos = 0
                    done = True
            if pos != 0 and tp1_done and not done:
                hit_stop = l[i] <= stop if pos == 1 else h[i] >= stop
                hit_tp2 = h[i] >= tp2 if pos == 1 else l[i] <= tp2
                if hit_stop:
                    record(stop, "BE", qty)
                    trades[-1]["bars"] = i - ei
                    pos = 0
                elif hit_tp2:
                    record(tp2, "TP2", qty)
                    trades[-1]["bars"] = i - ei
                    pos = 0
        if np.isnan(atr[i]) or atr[i] <= 0:
            continue
        want = 1 if sig_l[i] else (-1 if sig_s[i] else 0)
        if want != 0 and want != pos:
            if pos != 0:
                record(c[i], "REVERSE", qty)
                trades[-1]["bars"] = i - ei
            pos = want
            dist = atr[i] * STOP_MULT
            qty = 1.0 / dist  # normalized: risk cash = 1
            entry = c[i]
            stop = entry - pos * dist
            tp1 = entry + pos * dist * TP1_R
            tp2 = entry + pos * dist * PAYOFF
            tp1_done = False
            tp1_cash = 0.0
            ei = i
            hi_ext = h[i]
            lo_ext = l[i]
            score = ls[i] if pos == 1 else ss[i]
            meta = dict(i=i, dir="L" if pos == 1 else "S", score=int(score),
                        comps=comps(i, pos), regime="MOM" if f["momentum"][i] else "MR",
                        qty0=qty, cf=counterfactual(f, i, pos, dist))
    if pos != 0:
        record(c[-1], "EOD", qty)
        trades[-1]["bars"] = n - 1 - ei
    return trades


def pf(rs):
    w = sum(r for r in rs if r > 0)
    lo = -sum(r for r in rs if r <= 0)
    return w / lo if lo > 0 else float("inf")


def table(trades, key):
    groups = {}
    for t in trades:
        groups.setdefault(key(t), []).append(t["r"])
    print(f"{'group':12s} {'n':>4s} {'PF':>6s} {'sumR':>7s} {'avgR':>6s} {'wr%':>5s}")
    for g, rs in sorted(groups.items()):
        wr = 100 * sum(1 for r in rs if r > 0) / len(rs)
        print(f"{str(g):12s} {len(rs):4d} {pf(rs):6.2f} {sum(rs):7.2f} {np.mean(rs):6.2f} {wr:5.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    df = load(args.csv)
    f = compute_features(df)
    ls, ss = scores(f, WINDOW)
    trades = simulate(f, ls, ss)
    dts = df["dt"].to_numpy()

    if args.out:
        import csv as csvmod
        with open(args.out, "w", newline="") as fh:
            wr = csvmod.DictWriter(fh, fieldnames=["i", "dt", "dir", "score", "comps",
                                                   "regime", "exit_reason", "r", "mfe_r",
                                                   "mae_r", "bars", "cf"])
            wr.writeheader()
            for t in trades:
                row = {k: t[k] for k in ("i", "dir", "score", "comps", "regime",
                                         "exit_reason", "r", "mfe_r", "mae_r", "bars", "cf")}
                row["dt"] = str(dts[t["i"]])[:16]
                wr.writerow(row)
        print(f"wrote {args.out} ({len(trades)} trades)", file=sys.stderr)

    rs = [t["r"] for t in trades]
    print(f"\nTOTAL: n={len(trades)} PF={pf(rs):.2f} sumR={sum(rs):.2f} "
          f"wr={100*sum(1 for r in rs if r>0)/len(rs):.1f}%")

    print("\n── Q1 exit reason distribution ──")
    table(trades, lambda t: t["exit_reason"])
    be = [t for t in trades if t["exit_reason"] == "BE"]
    if be:
        cf_tp2 = sum(1 for t in be if t["cf"] == "CF_TP2")
        cf_sl = sum(1 for t in be if t["cf"] == "CF_SL")
        print(f"BE deaths: {len(be)} | without BE management: {cf_tp2} would hit TP2, "
              f"{cf_sl} would hit SL, {len(be)-cf_tp2-cf_sl} neither (200 bars)")

    print("\n── Q3 regime ──")
    table(trades, lambda t: t["regime"])
    print("\n── Q4 direction ──")
    table(trades, lambda t: t["dir"])
    print("\n── Q5 score tier ──")
    table(trades, lambda t: "14-15" if t["score"] <= 15 else "16-17" if t["score"] <= 17 else "18+")

    print("\n── Q6 MAE/MFE ──")
    win = [t for t in trades if t["r"] > 0]
    lose = [t for t in trades if t["r"] <= 0]
    if win:
        maes = [t["mae_r"] for t in win]
        print(f"winners n={len(win)}: MAE mean={np.mean(maes):.2f}R median={np.median(maes):.2f}R "
              f"p75={np.percentile(maes,75):.2f}R p90={np.percentile(maes,90):.2f}R (stop at 1.0R)")
    if lose:
        mfes = [t["mfe_r"] for t in lose]
        near = sum(1 for m in mfes if m >= 0.8)
        print(f"losers  n={len(lose)}: MFE mean={np.mean(mfes):.2f}R median={np.median(mfes):.2f}R; "
              f"{near} losers ({100*near/len(lose):.0f}%) reached >=0.8R favorable first")


if __name__ == "__main__":
    main()
