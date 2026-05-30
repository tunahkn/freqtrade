"""
APEX SNIPER engine — pure pandas/numpy, no freqtrade / no TA-Lib dependency.

This is a faithful port of the APEX SNIPER v9 Pine logic (SMC/ICT confluence
scoring). It is deliberately framework-free so it can be reused by:

  * the freqtrade strategy  (user_data/strategies/ApexSniper.py)
  * the standalone backtest (scripts/apex_backtest.py)

compute(df, params) -> df with extra columns, most importantly:
  atr, score_long, score_short, cats_long, cats_short,
  enter_long, enter_short   (bool, confirmed-bar signals)

The engine mirrors Pine's bar-by-bar semantics for the stateful parts
(FVG tracking, CVD divergence, rolling POC, sequence memory, debounce),
so signals line up with the chart version.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


DEFAULTS = dict(
    ma_len=50, ma_type="EMA", swing_len=5,
    fvg_mult=0.7, fvg_max=8, liq_lb=20, wick_rej=True,
    vp_lb=200, vp_bins=24, vp_every=2, cvd_reset=True,
    adx_len=14, adx_th=20.0, rvol_len=20, rvol_min=1.3,
    use_regime_w=False, sniper_th=14, watch_th=10,
    min_cats=3, setup_win=10,
    atr_len=14,
    # kill-zone hours in EXCHANGE/UTC time (start,end) inclusive-exclusive, 24h
    killzones=((2, 5), (8, 11), (13, 16)),
)


# --------------------------------------------------------------------------- #
# vectorizable indicators
# --------------------------------------------------------------------------- #
def _ma(s: pd.Series, n: int, kind: str) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean() if kind == "EMA" else s.rolling(n).mean()


def _atr(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    # Wilder smoothing
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def _adx(df: pd.DataFrame, n: int) -> pd.Series:
    h, l = df["high"], df["low"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = df["close"].shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1.0 / n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1.0 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / n, adjust=False).mean()


def _pivots(arr: np.ndarray, left: int, right: int, find_high: bool) -> np.ndarray:
    """Causal pivots: value recorded at the CONFIRMATION bar (right bars later),
    matching Pine's ta.pivothigh/low. No lookahead."""
    n = len(arr)
    out = np.full(n, np.nan)
    for i in range(left + right, n):
        c = i - right
        w = arr[c - left: c + right + 1]
        if find_high and arr[c] == w.max():
            out[i] = arr[c]
        elif (not find_high) and arr[c] == w.min():
            out[i] = arr[c]
    return out


def _daily_vwap(df: pd.DataFrame, day_id: np.ndarray) -> np.ndarray:
    hlc3 = (df["high"] + df["low"] + df["close"]).values / 3.0
    vol = df["volume"].fillna(0).values
    out = np.full(len(df), np.nan)
    cum_pv = 0.0
    cum_v = 0.0
    cur = None
    for i in range(len(df)):
        if day_id[i] != cur:
            cur = day_id[i]
            cum_pv = 0.0
            cum_v = 0.0
        cum_pv += hlc3[i] * vol[i]
        cum_v += vol[i]
        out[i] = cum_pv / cum_v if cum_v > 0 else np.nan
    return out


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def compute(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    p = {**DEFAULTS, **(params or {})}
    df = df.copy().reset_index(drop=True)

    # time helpers
    dt = pd.to_datetime(df["date"], utc=True)
    day_id = (dt.dt.floor("D")).astype("int64").values
    hour = dt.dt.hour.values
    new_day = np.concatenate([[True], day_id[1:] != day_id[:-1]])

    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    v = df["volume"].fillna(0).values
    n = len(df)

    atr = _atr(df, p["atr_len"]).values
    ma = _ma(df["close"], p["ma_len"], p["ma_type"]).values  # proxy HTF bias on same tf
    adx = _adx(df, p["adx_len"]).values
    volbase = df["volume"].rolling(p["rvol_len"]).mean().shift(1).values
    rvol = np.where((volbase > 0) & ~np.isnan(volbase), v / np.where(volbase == 0, np.nan, volbase), 1.0)
    disp = (np.isnan(volbase)) | (rvol >= p["rvol_min"])
    vwap = _daily_vwap(df, day_id)

    ph = _pivots(h, p["swing_len"], p["swing_len"], True)
    pl = _pivots(l, p["swing_len"], p["swing_len"], False)
    ph5 = _pivots(h, 5, 5, True)
    pl5 = _pivots(l, 5, 5, False)

    bsl = pd.Series(h).rolling(p["liq_lb"]).max().shift(1).values
    ssl = pd.Series(l).rolling(p["liq_lb"]).min().shift(1).values
    rH = pd.Series(h).rolling(50).max().values
    rL = pd.Series(l).rolling(50).min().values

    # outputs
    score_long = np.zeros(n, int)
    score_short = np.zeros(n, int)
    cats_long = np.zeros(n, int)
    cats_short = np.zeros(n, int)
    enter_long = np.zeros(n, bool)
    enter_short = np.zeros(n, bool)

    # stateful vars
    lastSH = np.nan
    lastSL = np.nan
    fvgs: list[dict] = []          # {top,bot,bar,bull}
    cvd = 0.0
    lastPL = lastCvdPL = np.nan
    lastPH = lastCvdPH = np.nan
    bull_div = bear_div = 0
    poc = np.nan
    # sequence memory counters
    ssl_c = bsl_c = bos_up_c = bos_dn_c = poc_up_c = poc_dn_c = 0
    long_active = short_active = False
    bsl_raw_prev = ssl_raw_prev = False

    wrw = p["wick_rej"]
    sw = p["setup_win"]

    for i in range(n):
        # ---- HTF bias (same-tf MA proxy) ----
        bias_long = (not np.isnan(c[i])) and (not np.isnan(ma[i])) and c[i] > ma[i]
        bias_short = (not np.isnan(ma[i])) and c[i] < ma[i]

        # ---- structure ----
        if not np.isnan(ph[i]):
            lastSH = ph[i]
        if not np.isnan(pl[i]):
            lastSL = pl[i]
        broke_high = (not np.isnan(lastSH)) and i > 0 and c[i] > lastSH and c[i - 1] <= lastSH
        broke_low = (not np.isnan(lastSL)) and i > 0 and c[i] < lastSL and c[i - 1] >= lastSL
        bos_up = broke_high and bias_long
        bos_dn = broke_low and bias_short
        choch_up = broke_high and bias_short
        choch_dn = broke_low and bias_long

        # ---- FVG create ----
        if i >= 2 and not np.isnan(atr[i]):
            if l[i] > h[i - 2] and (l[i] - h[i - 2]) >= atr[i] * p["fvg_mult"] and disp[i]:
                fvgs.append(dict(top=l[i], bot=h[i - 2], bar=i, bull=True))
            if h[i] < l[i - 2] and (l[i - 2] - h[i]) >= atr[i] * p["fvg_mult"] and disp[i]:
                fvgs.append(dict(top=l[i - 2], bot=h[i], bar=i, bull=False))
        # CE-tap before mitigation
        bull_tap = bear_tap = False
        for f in fvgs:
            ce = (f["top"] + f["bot"]) / 2.0
            if l[i] <= ce <= h[i] and (i - f["bar"]) < 100:
                if f["bull"]:
                    bull_tap = True
                else:
                    bear_tap = True
        # mitigation removal
        fvgs = [f for f in fvgs
                if not ((f["bull"] and l[i] <= f["bot"]) or ((not f["bull"]) and h[i] >= f["top"]))]
        if len(fvgs) > p["fvg_max"]:
            fvgs = fvgs[-p["fvg_max"]:]

        # ---- liquidity sweep ----
        bsl_raw = (not np.isnan(bsl[i])) and h[i] > bsl[i] and (c[i] < bsl[i] if wrw else True)
        ssl_raw = (not np.isnan(ssl[i])) and l[i] < ssl[i] and (c[i] > ssl[i] if wrw else True)
        bsl_swept = bsl_raw and not bsl_raw_prev
        ssl_swept = ssl_raw and not ssl_raw_prev
        bsl_raw_prev, ssl_raw_prev = bsl_raw, ssl_raw

        # ---- premium / discount + OTE ----
        in_disc = in_prem = ote_long = ote_short = False
        if not np.isnan(rH[i]) and not np.isnan(rL[i]):
            rng = rH[i] - rL[i]
            mid = (rH[i] + rL[i]) / 2.0
            in_disc = c[i] < mid
            in_prem = c[i] > mid
            if rng > 0:
                ote_long = (rH[i] - rng * 0.79) <= c[i] <= (rH[i] - rng * 0.62)
                ote_short = (rL[i] + rng * 0.62) <= c[i] <= (rL[i] + rng * 0.79)

        # ---- CVD + divergence ----
        rng_hl = max(h[i] - l[i], 1e-12)
        body = abs(c[i] - o[i]) / rng_hl
        buyv = v[i] * (body if c[i] >= o[i] else 1 - body)
        sellv = v[i] * (body if c[i] < o[i] else 1 - body)
        delta = buyv - sellv
        cvd = delta if (p["cvd_reset"] and new_day[i]) else cvd + delta
        if not np.isnan(pl5[i]):
            catp = cvd  # cvd at this confirmation bar approximates Pine cvd[5] alignment
            if not np.isnan(lastPL) and not np.isnan(lastCvdPL) and pl5[i] < lastPL and catp > lastCvdPL:
                bull_div = 6
            lastPL, lastCvdPL = pl5[i], catp
        if not np.isnan(ph5[i]):
            catp = cvd
            if not np.isnan(lastPH) and not np.isnan(lastCvdPH) and ph5[i] > lastPH and catp < lastCvdPH:
                bear_div = 6
            lastPH, lastCvdPH = ph5[i], catp
        bull_div = max(0, bull_div - 1)
        bear_div = max(0, bear_div - 1)

        # ---- POC (rolling) ----
        if i >= p["vp_lb"] and (i % p["vp_every"] == 0):
            lo = l[i - p["vp_lb"] + 1: i + 1].min()
            hi = h[i - p["vp_lb"] + 1: i + 1].max()
            rngv = hi - lo
            if rngv > 0:
                binw = rngv / p["vp_bins"]
                bins = np.zeros(p["vp_bins"])
                seg_price = (h[i - p["vp_lb"] + 1: i + 1] + l[i - p["vp_lb"] + 1: i + 1]
                             + c[i - p["vp_lb"] + 1: i + 1]) / 3.0
                seg_vol = v[i - p["vp_lb"] + 1: i + 1]
                idx = np.clip(((seg_price - lo) / max(binw, 1e-12)).astype(int), 0, p["vp_bins"] - 1)
                for j in range(len(idx)):
                    bins[idx[j]] += seg_vol[j]
                poc = lo + (int(bins.argmax()) + 0.5) * binw
        poc_up = (not np.isnan(poc)) and i > 0 and c[i] > poc and c[i - 1] <= poc
        poc_dn = (not np.isnan(poc)) and i > 0 and c[i] < poc and c[i - 1] >= poc

        # ---- regime / kill zone ----
        is_mom = (not np.isnan(adx[i])) and adx[i] > p["adx_th"]
        in_kz = any(a <= hour[i] < b for a, b in p["killzones"])

        # ---- sequence memory ----
        ssl_c = sw if ssl_swept else max(0, ssl_c - 1)
        bsl_c = sw if bsl_swept else max(0, bsl_c - 1)
        bos_up_c = sw if (bos_up or choch_up) else max(0, bos_up_c - 1)
        bos_dn_c = sw if (bos_dn or choch_dn) else max(0, bos_dn_c - 1)
        poc_up_c = sw if poc_up else max(0, poc_up_c - 1)
        poc_dn_c = sw if poc_dn else max(0, poc_dn_c - 1)

        # ---- scoring ----
        wT = (12 if is_mom else 8) if p["use_regime_w"] else 10
        wS = (13 if is_mom else 9) if p["use_regime_w"] else 11
        wL = (9 if is_mom else 12) if p["use_regime_w"] else 10
        wO = (8 if is_mom else 13) if p["use_regime_w"] else 10

        lv = vwap[i] is not np.nan and (not np.isnan(vwap[i])) and c[i] > vwap[i] and bias_long
        sv = (not np.isnan(vwap[i])) and c[i] < vwap[i] and bias_short

        lc_t = min(5, (3 if bias_long else 0) + (2 if lv else 0))
        sc_t = min(5, (3 if bias_short else 0) + (2 if sv else 0))
        lc_l = min(5, (3 if ssl_c > 0 else 0) + (1 if bull_div > 0 else 0))
        sc_l = min(5, (3 if bsl_c > 0 else 0) + (1 if bear_div > 0 else 0))
        lc_s = min(5, (3 if bos_up_c > 0 else 0) + (2 if bull_tap else 0))
        sc_s = min(5, (3 if bos_dn_c > 0 else 0) + (2 if bear_tap else 0))
        lc_o = min(4, (2 if (in_disc or ote_long) else 0) + (1 if poc_up_c > 0 else 0) + (1 if in_kz else 0))
        sc_o = min(4, (2 if (in_prem or ote_short) else 0) + (1 if poc_dn_c > 0 else 0) + (1 if in_kz else 0))

        maxraw = 5 * wT + 5 * wL + 5 * wS + 4 * wO
        lraw = lc_t * wT + lc_l * wL + lc_s * wS + lc_o * wO
        sraw = sc_t * wT + sc_l * wL + sc_s * wS + sc_o * wO
        sl = int(round(lraw / maxraw * 20))
        ss = int(round(sraw / maxraw * 20))
        lcat = (lc_t > 0) + (lc_l > 0) + (lc_s > 0) + (lc_o > 0)
        scat = (sc_t > 0) + (sc_l > 0) + (sc_s > 0) + (sc_o > 0)

        score_long[i] = sl
        score_short[i] = ss
        cats_long[i] = lcat
        cats_short[i] = scat

        qual_long = sl >= p["sniper_th"] and sl > ss and lcat >= p["min_cats"]
        qual_short = ss >= p["sniper_th"] and ss > sl and scat >= p["min_cats"]

        # debounce
        e_long = qual_long and not long_active
        e_short = qual_short and not short_active
        if qual_long:
            long_active = True
        if sl < p["watch_th"]:
            long_active = False
        if qual_short:
            short_active = True
        if ss < p["watch_th"]:
            short_active = False

        enter_long[i] = e_long
        enter_short[i] = e_short

    df["atr"] = atr
    df["score_long"] = score_long
    df["score_short"] = score_short
    df["cats_long"] = cats_long
    df["cats_short"] = cats_short
    df["enter_long"] = enter_long
    df["enter_short"] = enter_short
    return df
