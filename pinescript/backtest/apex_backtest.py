#!/usr/bin/env python3
"""Parameter sweep backtester for APEX SNIPER v8 (Pine Script v6 port).

Faithfully replicates pinescript/apex_sniper_v8_strategy.pine bar-by-bar:
signals are evaluated on bar close, entries fill at the signal bar close,
ATR stop and R-multiple take-profit fill intrabar on later bars (stop checked
before target on bars that touch both — conservative).

Data: Binance OHLCV CSV (klines format), e.g.
https://raw.githubusercontent.com/lth-elm/Backtrading-Python-Binance/main/data/ETHUSDT-2017-2020-4h.csv

Usage: python3 apex_backtest.py <csv path> [--grid | --th 14 --window 8 --stop 1.5 --payoff 1.5]
"""
import argparse
import itertools
import sys

import numpy as np
import pandas as pd

# ── fixed inputs (mirror the Pine defaults) ─────────────────────────────────
HTF_MA_LEN = 50
SWING_LEN = 5
FVG_ATR_MULT = 0.5
FVG_MAX = 20
LIQ_LB = 20
VP_LB = 200
VP_BINS = 24
ADX_LEN = 14
ADX_TREND_TH = 20.0
RVOL_LEN = 20
RVOL_MIN = 1.3
RISK_PCT = 0.5          # % of equity risked per trade
FEE_PCT = 0.0005        # 0.05% taker per side (matches TV strategy setting)
START_EQUITY = 10_000.0


def wilder(series: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(series), np.nan)
    if len(series) < length:
        return out
    acc = np.nanmean(series[:length])
    out[length - 1] = acc
    for i in range(length, len(series)):
        acc = (acc * (length - 1) + series[i]) / length
        out[i] = acc
    return out


def bars_since(events: np.ndarray) -> np.ndarray:
    """Pine ta.barssince: NaN until first event, then bars elapsed."""
    out = np.full(len(events), np.inf)
    last = -1
    for i, e in enumerate(events):
        if e:
            last = i
        if last >= 0:
            out[i] = i - last
    return out


def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, header=None).iloc[:, :6]
    df.columns = ["ts", "open", "high", "low", "close", "volume"]
    df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    return df.reset_index(drop=True)


def compute_features(df: pd.DataFrame) -> dict:
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    v = df["volume"].to_numpy()
    n = len(df)

    # HTF bias (chart TF == 4h == htf "240", so the MA runs on chart data)
    ema = df["close"].ewm(span=HTF_MA_LEN, adjust=False).mean().to_numpy()
    bias_long = c > ema
    bias_short = c < ema

    # daily-anchored VWAP on hlc3
    hlc3 = (h + l + c) / 3.0
    day = df["dt"].dt.floor("D")
    pv = pd.Series(hlc3 * v).groupby(day).cumsum().to_numpy()
    vv = pd.Series(v).groupby(day).cumsum().to_numpy()
    avwap = np.divide(pv, vv, out=np.full(n, np.nan), where=vv > 0)

    # RVOL / displacement
    vol_sma = pd.Series(v).rolling(RVOL_LEN).mean().to_numpy()
    rvol = np.divide(v, vol_sma, out=np.ones(n), where=vol_sma > 0)
    disp = rvol >= RVOL_MIN

    # ATR(14), Wilder — matches ta.atr
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = np.full(n, np.nan)
    atr[1:] = wilder(tr, 14)

    # swing pivots (confirmed SWING_LEN bars later, like ta.pivothigh/low)
    last_sh = np.full(n, np.nan)
    last_sl = np.full(n, np.nan)
    sh = sl = np.nan
    piv_lo_evt = np.zeros(n, bool)
    piv_hi_evt = np.zeros(n, bool)
    for i in range(n):
        j = i - SWING_LEN
        if j >= SWING_LEN:
            win_h = h[j - SWING_LEN : j + SWING_LEN + 1]
            win_l = l[j - SWING_LEN : j + SWING_LEN + 1]
            if h[j] == win_h.max() and (win_h == h[j]).sum() == 1:
                sh = h[j]
                piv_hi_evt[i] = True
            if l[j] == win_l.min() and (win_l == l[j]).sum() == 1:
                sl = l[j]
                piv_lo_evt[i] = True
        last_sh[i] = sh
        last_sl[i] = sl

    c1 = np.roll(c, 1)
    broke_high = (~np.isnan(last_sh)) & (c > last_sh) & (c1 <= last_sh)
    broke_low = (~np.isnan(last_sl)) & (c < last_sl) & (c1 >= last_sl)
    broke_high[0] = broke_low[0] = False
    bos_up = broke_high & bias_long
    bos_dn = broke_low & bias_short
    choch_up = broke_high & bias_short
    choch_dn = broke_low & bias_long

    # liquidity sweeps (wick rejection required, Pine default)
    bsl = pd.Series(h).rolling(LIQ_LB).max().shift(1).to_numpy()
    ssl = pd.Series(l).rolling(LIQ_LB).min().shift(1).to_numpy()
    bsl_swept = (~np.isnan(bsl)) & (h > bsl) & (c < bsl)
    ssl_swept = (~np.isnan(ssl)) & (l < ssl) & (c > ssl)

    # FVG engine: active-gap list, CE tap + mitigation, capped at FVG_MAX
    bull_tap = np.zeros(n, bool)
    bear_tap = np.zeros(n, bool)
    bulls: list = []
    bears: list = []
    for i in range(n):
        for f in bulls:
            if l[i] <= f[2] <= c[i]:
                bull_tap[i] = True
        for f in bears:
            if h[i] >= f[2] >= c[i]:
                bear_tap[i] = True
        bulls = [f for f in bulls if l[i] > f[1]][-FVG_MAX:]
        bears = [f for f in bears if h[i] < f[0]][-FVG_MAX:]
        if i > 1 and not np.isnan(atr[i]):
            if l[i] > h[i - 2] and (l[i] - h[i - 2]) >= atr[i] * FVG_ATR_MULT and disp[i]:
                bulls.append((l[i], h[i - 2], (l[i] + h[i - 2]) / 2))
            if h[i] < l[i - 2] and (l[i - 2] - h[i]) >= atr[i] * FVG_ATR_MULT and disp[i]:
                bears.append((l[i - 2], h[i], (l[i - 2] + h[i]) / 2))

    # premium / discount + OTE on 50-bar range
    r_h = pd.Series(h).rolling(50).max().to_numpy()
    r_l = pd.Series(l).rolling(50).min().to_numpy()
    r_m = (r_h + r_l) / 2
    in_disc = (~np.isnan(r_m)) & (c < r_m)
    in_prem = (~np.isnan(r_m)) & (c > r_m)
    rng = r_h - r_l
    ote_l = (~np.isnan(r_l)) & (c >= r_l + rng * 0.62) & (c <= r_l + rng * 0.79)
    ote_s = (~np.isnan(r_h)) & (c <= r_h - rng * 0.62) & (c >= r_h - rng * 0.79)

    # CVD (body-proportional, daily reset) + pivot divergence state
    body_r = np.abs(c - o) / np.maximum(h - l, 1e-9)
    buy_v = v * np.where(c >= o, body_r, 1 - body_r)
    sell_v = v * np.where(c < o, body_r, 1 - body_r)
    delta = buy_v - sell_v
    new_day = day.ne(day.shift(1)).to_numpy()
    cvd = np.zeros(n)
    for i in range(n):
        cvd[i] = delta[i] if new_day[i] else cvd[i - 1] + delta[i]
    bull_div = np.zeros(n, bool)
    bear_div = np.zeros(n, bool)
    pl1 = pl2 = cl1 = cl2 = np.nan
    ph1 = ph2 = ch1 = ch2 = np.nan
    for i in range(n):
        j = i - SWING_LEN
        if piv_lo_evt[i]:
            pl2, pl1, cl2, cl1 = pl1, l[j], cl1, cvd[j]
        if piv_hi_evt[i]:
            ph2, ph1, ch2, ch1 = ph1, h[j], ch1, cvd[j]
        if not (np.isnan(pl2) or np.isnan(cl2)):
            bull_div[i] = pl1 < pl2 and cl1 > cl2
        if not (np.isnan(ph2) or np.isnan(ch2)):
            bear_div[i] = ph1 > ph2 and ch1 < ch2

    # volume profile POC (200-bar window, 24 bins) + reclaim events
    poc = np.full(n, np.nan)
    for i in range(VP_LB, n):
        w_h = h[i - VP_LB + 1 : i + 1]
        w_l = l[i - VP_LB + 1 : i + 1]
        w_p = hlc3[i - VP_LB + 1 : i + 1]
        w_v = v[i - VP_LB + 1 : i + 1]
        lo, hi = w_l.min(), w_h.max()
        if hi <= lo or w_v.sum() <= 0:
            continue
        hist, edges = np.histogram(w_p, bins=VP_BINS, range=(lo, hi), weights=w_v)
        k = int(hist.argmax())
        poc[i] = (edges[k] + edges[k + 1]) / 2
    poc_up = (~np.isnan(poc)) & (c > poc) & (c1 <= poc)
    poc_dn = (~np.isnan(poc)) & (c < poc) & (c1 >= poc)
    poc_up[0] = poc_dn[0] = False

    # ADX(14) Wilder → regime (gamma flip unknown ⇒ ADX path)
    up = h[1:] - h[:-1]
    dn = l[:-1] - l[1:]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_w = wilder(tr, ADX_LEN)
    pdi = 100 * wilder(plus_dm, ADX_LEN) / np.maximum(atr_w, 1e-9)
    mdi = 100 * wilder(minus_dm, ADX_LEN) / np.maximum(atr_w, 1e-9)
    dx = 100 * np.abs(pdi - mdi) / np.maximum(pdi + mdi, 1e-9)
    adx = np.full(n, np.nan)
    adx[1:] = wilder(dx, ADX_LEN)
    momentum = (~np.isnan(adx)) & (adx > ADX_TREND_TH)

    return dict(
        h=h, l=l, c=c, atr=atr, n=n,
        bias_long=bias_long, bias_short=bias_short,
        vwap_l=(~np.isnan(avwap)) & (c > avwap), vwap_s=(~np.isnan(avwap)) & (c < avwap),
        bs_ssl=bars_since(ssl_swept), bs_bsl=bars_since(bsl_swept),
        bs_struct_l=bars_since(choch_up | bos_up), bs_struct_s=bars_since(choch_dn | bos_dn),
        bs_fvg_l=bars_since(bull_tap), bs_fvg_s=bars_since(bear_tap),
        bs_poc_up=bars_since(poc_up), bs_poc_dn=bars_since(poc_dn),
        pd_l=in_disc | ote_l, pd_s=in_prem | ote_s,
        cvd_l=bull_div, cvd_s=bear_div, momentum=momentum,
    )


def scores(f: dict, window: int):
    bos_pts = np.where(f["momentum"], 4, 2)   # 4h chart: regime is never "unknown"
    pd_pts = np.where(f["momentum"], 1, 3)
    long_s = (
        np.where(f["bias_long"], 3, 0) + np.where(f["vwap_l"], 2, 0)
        + np.where(f["bs_ssl"] <= window, 3, 0)
        + np.where(f["bs_struct_l"] <= window, bos_pts, 0)
        + np.where(f["bs_fvg_l"] <= window, 2, 0)
        + np.where(f["pd_l"], pd_pts, 0) + np.where(f["cvd_l"], 1, 0)
        + np.where(f["bs_poc_up"] <= window, 1, 0)
        + 2  # kill zone neutralized on TFs > 1h (kzApplicable false)
        + 0  # sentiment: manual inputs at defaults award nothing
    )
    short_s = (
        np.where(f["bias_short"], 3, 0) + np.where(f["vwap_s"], 2, 0)
        + np.where(f["bs_bsl"] <= window, 3, 0)
        + np.where(f["bs_struct_s"] <= window, bos_pts, 0)
        + np.where(f["bs_fvg_s"] <= window, 2, 0)
        + np.where(f["pd_s"], pd_pts, 0) + np.where(f["cvd_s"], 1, 0)
        + np.where(f["bs_poc_dn"] <= window, 1, 0)
        + 2
    )
    return long_s, short_s


def run(f: dict, long_s, short_s, th: int, stop_mult: float, payoff: float,
        tp1_r: float = 0.0, tp1_pct: float = 0.5, be_after_tp1: bool = True,
        time_stop: int = 0, conviction: bool = False) -> dict:
    """Pine strategy execution model.

    tp1_r > 0 enables a partial take-profit at tp1_r R for tp1_pct of the
    position, optionally moving the stop to breakeven for the runner.
    time_stop > 0 closes a trade that hasn't reached TP1 within N bars.
    conviction scales risk by signal score: 1x at threshold, 1.5x at +2, 2x at +4.
    """
    h, l, c, atr, n = f["h"], f["l"], f["c"], f["atr"], f["n"]
    sniper_l = (long_s >= th) & (long_s > short_s)
    sniper_s = (short_s >= th) & (short_s > long_s)
    equity = START_EQUITY
    peak = equity
    max_dd = 0.0
    pos = 0
    qty = entry = stop = tp = tp1 = 0.0
    tp1_done = True
    entry_i = 0
    trades, wins, gross_w, gross_l = 0, 0, 0.0, 0.0

    def book(pnl):
        nonlocal equity, peak, max_dd
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)

    def fill(px, part_qty):
        return part_qty * (px - entry) * pos - FEE_PCT * part_qty * (entry + px)

    def close_trade(px):
        nonlocal trades, wins, gross_w, gross_l, qty
        pnl = fill(px, qty)
        book(pnl)
        trades += 1
        if pnl > 0:
            wins += 1
            gross_w += pnl
        else:
            gross_l -= pnl
        qty = 0.0

    for i in range(1, n):
        if pos != 0:
            if tp1_r > 0 and not tp1_done:
                hit_tp1 = h[i] >= tp1 if pos == 1 else l[i] <= tp1
                hit_stop = l[i] <= stop if pos == 1 else h[i] >= stop
                if hit_stop:  # conservative: full stop before partial
                    close_trade(stop); pos = 0
                elif hit_tp1:
                    part = qty * tp1_pct
                    book(fill(tp1, part))
                    gross_w += max(fill(tp1, part), 0.0)
                    qty -= part
                    tp1_done = True
                    if be_after_tp1:
                        stop = entry
                elif time_stop > 0 and i - entry_i >= time_stop:
                    close_trade(c[i]); pos = 0
            elif pos != 0:
                if pos == 1 and l[i] <= stop:
                    close_trade(stop); pos = 0
                elif pos == 1 and h[i] >= tp:
                    close_trade(tp); pos = 0
                elif pos == -1 and h[i] >= stop:
                    close_trade(stop); pos = 0
                elif pos == -1 and l[i] <= tp:
                    close_trade(tp); pos = 0
        if np.isnan(atr[i]) or atr[i] <= 0:
            continue
        want = 1 if sniper_l[i] else (-1 if sniper_s[i] else 0)
        if want != 0 and want != pos:
            if pos != 0:
                close_trade(c[i])
            score = long_s[i] if want == 1 else short_s[i]
            mult = 1.0
            if conviction:
                mult = 2.0 if score >= th + 4 else 1.5 if score >= th + 2 else 1.0
            dist = atr[i] * stop_mult
            qty = equity * (RISK_PCT / 100) * mult / dist
            entry = c[i]
            stop = entry - want * dist
            tp = entry + want * dist * payoff
            tp1 = entry + want * dist * tp1_r
            tp1_done = tp1_r <= 0
            entry_i = i
            pos = want
    if pos != 0:
        close_trade(c[-1])
    pf = gross_w / gross_l if gross_l > 0 else float("inf")
    return dict(trades=trades, winrate=100 * wins / max(trades, 1), pf=pf,
                ret=100 * (equity / START_EQUITY - 1), maxdd=100 * max_dd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--exec-grid", action="store_true", help="compare trade-management variants")
    ap.add_argument("--th", type=int, default=14)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--stop", type=float, default=2.0)
    ap.add_argument("--payoff", type=float, default=2.5)
    args = ap.parse_args()

    df = load(args.csv)
    print(f"{args.csv}: {len(df)} bars, {df['dt'].iloc[0]:%Y-%m-%d} → {df['dt'].iloc[-1]:%Y-%m-%d}", file=sys.stderr)
    f = compute_features(df)

    if args.exec_grid:
        ls, ss = scores(f, args.window)
        variants = [
            ("A baseline (full 2.5R exit)", dict()),
            ("B TP1 50% @1R + BE runner", dict(tp1_r=1.0)),
            ("C B + time-stop 20 bars", dict(tp1_r=1.0, time_stop=20)),
            ("D B + conviction sizing", dict(tp1_r=1.0, conviction=True)),
            ("E C + conviction sizing", dict(tp1_r=1.0, time_stop=20, conviction=True)),
        ]
        print("variant,trades,winrate,pf,ret_pct,maxdd_pct")
        for name, kw in variants:
            r = run(f, ls, ss, args.th, args.stop, args.payoff, **kw)
            print(f"\"{name}\",{r['trades']},{r['winrate']:.1f},{r['pf']:.2f},{r['ret']:.2f},{r['maxdd']:.2f}")
        return

    if args.grid:
        print("th,window,stop,payoff,trades,winrate,pf,ret_pct,maxdd_pct")
        for window in (5, 8, 12):
            ls, ss = scores(f, window)
            for th, stop, payoff in itertools.product((12, 13, 14, 15), (1.5, 2.0), (1.5, 2.0, 2.5)):
                r = run(f, ls, ss, th, stop, payoff)
                print(f"{th},{window},{stop},{payoff},{r['trades']},{r['winrate']:.1f},{r['pf']:.2f},{r['ret']:.2f},{r['maxdd']:.2f}")
    else:
        ls, ss = scores(f, args.window)
        r = run(f, ls, ss, args.th, args.stop, args.payoff)
        print(r)


if __name__ == "__main__":
    main()
