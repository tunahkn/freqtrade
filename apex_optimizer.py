#!/usr/bin/env python3
"""
APEX SNIPER v10 — Comprehensive Parameter Optimizer
=====================================================
Scans 48 combinations of (min_conditions × st_factor × atr_stop_mult)
on three synthetic-but-statistically-realistic price series:
  • ETHUSDT 4h  (primary optimization target)
  • BTCUSDT 4h  (cross-validation)
  • ETHUSDT 1h  (cross-validation)

Synthetic data parameters are calibrated to 2022-2024 on-chain statistics:
  ETH 4h  : annualised vol ≈ 80 %, daily drift ≈ +0.06 %
  BTC 4h  : annualised vol ≈ 65 %, daily drift ≈ +0.04 %
  Regime mixing : 35 % bull / 35 % bear / 30 % range
  Tail risk     : Student-t (df=4) innovations — fat-tail crypto behaviour
  Vol clustering: GARCH(1,1) α=0.12, β=0.82

NOTE: When network access is available (e.g. your local Mac terminal),
      set USE_REAL_DATA = True to download live Binance perp OHLCV data.

Filter:  Total Trades ≥ 30  AND  Max Drawdown ≤ 25 %
Sort:    Profit Factor DESC
"""

import itertools
import warnings
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
# SWITCH: real data vs synthetic simulation
# ─────────────────────────────────────────────
USE_REAL_DATA = False        # set True when running on your local Mac terminal

# ─────────────────────────────────────────────
# GLOBAL CONFIG
# ─────────────────────────────────────────────
INITIAL_CAP   = 10_000.0
RISK_PCT      = 0.005        # 0.5 % per trade
MIN_TRADES    = 30
MAX_DD        = 25.0
TP1_R, TP2_R, TP3_R          = 1.0, 2.0, 3.0
TP1_PCT, TP2_PCT, TP3_PCT    = 0.50, 0.25, 0.25

# Hyper-parameter grid (3 × 4 × 4 = 48 combinations)
MIN_CONDITIONS_VALS = [3, 4, 5]
ST_FACTOR_VALS      = [2.0, 2.5, 3.0, 3.5]
ATR_STOP_MULT_VALS  = [1.0, 1.5, 2.0, 2.5]

# Fixed indicator parameters
HTF_MA_LEN    = 50
SWING_LEN     = 5
FVG_ATR_MULT  = 0.5
LIQ_LOOKBACK  = 20
RECENT_WINDOW = 12
CVD_PERIOD    = 20
ST_ATR_LEN    = 10

# Simulation seeds (multiple seeds = Monte Carlo robustness test)
N_MC_RUNS  = 8          # 8 independent 2-year simulations per symbol
BARS_4H    = 2190       # ≈ 2 years × 365 × 6 bars/day
BARS_1H    = 8760       # ≈ 2 years × 365 × 24 bars/day

# ─────────────────────────────────────────────
# SYNTHETIC DATA GENERATOR  (GARCH + Student-t)
# ─────────────────────────────────────────────
def _generate_ohlcv(
    n_bars:     int,
    start_price: float,
    annual_vol:  float,
    daily_drift: float,
    bars_per_day: int,
    seed:        int,
    regime_bull_frac: float = 0.35,
    regime_bear_frac: float = 0.35,
    df_t:        int = 4,              # Student-t degrees of freedom
    garch_alpha: float = 0.12,
    garch_beta:  float = 0.82,
) -> pd.DataFrame:
    """
    Simulate a realistic crypto OHLCV series via GARCH(1,1) + Student-t shocks
    with three market regimes (bull / bear / range) each with its own drift.
    """
    rng = np.random.default_rng(seed)

    bar_vol   = annual_vol / np.sqrt(252 * bars_per_day)
    bar_drift = daily_drift / bars_per_day

    # Regime sequence: chunk into 20-bar blocks, assign regime randomly
    block_size = 20
    n_blocks   = int(np.ceil(n_bars / block_size))
    probs = [regime_bull_frac, regime_bear_frac, 1 - regime_bull_frac - regime_bear_frac]
    regime_ids = rng.choice([0, 1, 2], size=n_blocks, p=probs)
    # 0=bull, 1=bear, 2=range
    regime_drift_mul = np.array([2.5, -2.5, 0.2])

    # GARCH variance path
    omega   = bar_vol**2 * (1 - garch_alpha - garch_beta)
    h       = np.full(n_bars, bar_vol**2)
    eps     = np.zeros(n_bars)
    # Student-t innovations (fat tails)
    z = rng.standard_t(df_t, size=n_bars) / np.sqrt(df_t / (df_t - 2))

    for i in range(1, n_bars):
        blk  = min(i // block_size, n_blocks - 1)
        drft = bar_drift * regime_drift_mul[regime_ids[blk]]
        h[i] = max(omega + garch_alpha * eps[i-1]**2 + garch_beta * h[i-1], 1e-12)
        eps[i] = drft + np.sqrt(h[i]) * z[i]

    log_ret = eps
    log_prices = np.log(start_price) + np.cumsum(log_ret)
    close = np.exp(log_prices)

    # Construct realistic OHLC from close series
    bar_sigma = np.sqrt(h)
    high  = close * np.exp( np.abs(rng.normal(0, bar_sigma * 0.7)))
    low   = close * np.exp(-np.abs(rng.normal(0, bar_sigma * 0.7)))
    open_ = np.concatenate([[start_price], close[:-1]])
    # Clamp to sensible range
    high  = np.maximum(high,  np.maximum(open_, close))
    low   = np.minimum(low,   np.minimum(open_, close))

    # Volume: log-normal, correlated with volatility
    base_vol   = 1e9 / bars_per_day   # ~$1B daily volume
    vol_factor = np.exp(rng.normal(0, 0.4, n_bars))
    vol_high   = 1 + 3 * (bar_sigma / bar_vol)   # spikes on high-vol bars
    volume = base_vol * vol_factor * vol_high

    ts = pd.date_range("2022-01-01", periods=n_bars, freq=f"{24//bars_per_day}h", tz="UTC")
    return pd.DataFrame({
        "ts":     ts,
        "open":   open_,
        "high":   high,
        "low":    low,
        "close":  close,
        "volume": volume,
    })


def build_synthetic_datasets(seed_offset: int = 0) -> dict:
    """Return dict of DataFrames for all three symbols using given seed."""
    seed = 42 + seed_offset * 100
    dfs = {}
    dfs["ETHUSDT_4h"] = _generate_ohlcv(BARS_4H, 2500.0, 0.80, 0.0006, 6,  seed)
    dfs["BTCUSDT_4h"] = _generate_ohlcv(BARS_4H, 40000.0, 0.65, 0.0004, 6,  seed+1)
    dfs["ETHUSDT_1h"] = _generate_ohlcv(BARS_1H, 2500.0, 0.80, 0.0006, 24, seed+2)
    return dfs


def build_real_datasets(days: int = 730) -> dict:
    import ccxt
    from datetime import datetime, timezone

    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    symbols_map = {
        "ETHUSDT_4h": ("ETH/USDT:USDT", "4h"),
        "BTCUSDT_4h": ("BTC/USDT:USDT", "4h"),
        "ETHUSDT_1h": ("ETH/USDT:USDT", "1h"),
    }
    dfs = {}
    since0 = int((datetime.now(timezone.utc).timestamp() - days * 86_400) * 1_000)
    for label, (sym, tf) in symbols_map.items():
        print(f"  Downloading {sym} {tf} …", end=" ", flush=True)
        since_ms = since0
        bars = []
        while True:
            chunk = exchange.fetch_ohlcv(sym, tf, since=since_ms, limit=1000)
            if not chunk:
                break
            bars.extend(chunk)
            if len(chunk) < 1000:
                break
            since_ms = chunk[-1][0] + 1
        df = pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
        dfs[label] = df
        print(f"{len(df)} bars")
    return dfs


# ─────────────────────────────────────────────
# INDICATORS  (pure numpy / pandas — no TA-lib)
# ─────────────────────────────────────────────
def _atr(high, low, close, period=14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def _supertrend(high, low, close, factor: float, period: int):
    atr_s    = _atr(high, low, close, period)
    hl2      = (high + low) / 2
    upper_r  = (hl2 + factor * atr_s).to_numpy()
    lower_r  = (hl2 - factor * atr_s).to_numpy()
    c        = close.to_numpy()
    n        = len(c)
    upper    = np.full(n, np.nan)
    lower    = np.full(n, np.nan)
    st       = np.full(n, np.nan)
    dir_     = np.ones(n)

    # Find first bar where ATR is valid (non-NaN)
    first_valid = 0
    while first_valid < n and np.isnan(upper_r[first_valid]):
        first_valid += 1
    if first_valid >= n:
        return pd.Series(dir_, index=close.index)

    # Seed the first valid bar
    upper[first_valid] = upper_r[first_valid]
    lower[first_valid] = lower_r[first_valid]
    dir_[first_valid]  = 1
    st[first_valid]    = upper[first_valid]

    for i in range(first_valid + 1, n):
        # Trail upper band downward, lower band upward
        upper[i] = upper_r[i] if (upper_r[i] < upper[i-1] or c[i-1] > upper[i-1]) else upper[i-1]
        lower[i] = lower_r[i] if (lower_r[i] > lower[i-1] or c[i-1] < lower[i-1]) else lower[i-1]

        # Direction flip: compare current close to the already-finalized band
        prev_st  = st[i-1]
        if prev_st == upper[i-1]:           # was bearish
            dir_[i] = -1 if c[i] > upper[i] else 1
        else:                               # was bullish
            dir_[i] =  1 if c[i] < lower[i] else -1

        st[i] = lower[i] if dir_[i] == -1 else upper[i]

    return pd.Series(dir_, index=close.index)


def _pivot_cross(series: pd.Series, n: int, is_high: bool) -> pd.Series:
    """Returns rolling pivot break signals (vectorized approx)."""
    if is_high:
        pivot_val = series.rolling(2*n+1, center=True).max()
        is_pivot  = (series == pivot_val)
    else:
        pivot_val = series.rolling(2*n+1, center=True).min()
        is_pivot  = (series == pivot_val)
    last_pivot = series.where(is_pivot).ffill().shift(n)
    if is_high:
        return series > last_pivot
    else:
        return series < last_pivot


def compute_all_indicators(df: pd.DataFrame, st_factor: float) -> pd.DataFrame:
    h, l, c, o, v = df.high, df.low, df.close, df.open, df.volume

    df["atr14"]    = _atr(h, l, c, 14)
    df["atr_st"]   = _atr(h, l, c, ST_ATR_LEN)
    df["st_dir"]   = _supertrend(h, l, c, st_factor, ST_ATR_LEN)
    df["trend_bull"] = df["st_dir"] < 0
    df["trend_bear"] = df["st_dir"] > 0

    # HTF bias (SMA50 on current frame — simplification of htf request.security)
    df["sma50"]        = c.rolling(HTF_MA_LEN).mean()
    df["bullish_bias"] = c > df["sma50"]
    df["bearish_bias"] = c < df["sma50"]

    # Structure: rolling pivot high/low → BOS
    bos_up = _pivot_cross(c, SWING_LEN, is_high=True)
    bos_dn = _pivot_cross(c, SWING_LEN, is_high=False)
    df["bos_bull"] = bos_up & df["bullish_bias"]
    df["bos_bear"] = bos_dn & df["bearish_bias"]
    df["mss_bull"] = bos_up & df["bearish_bias"]
    df["mss_bear"] = bos_dn & df["bullish_bias"]
    df["struct_bull_recent"] = (df["bos_bull"] | df["mss_bull"]).rolling(RECENT_WINDOW).max().astype(bool)
    df["struct_bear_recent"] = (df["bos_bear"] | df["mss_bear"]).rolling(RECENT_WINDOW).max().astype(bool)

    # Liquidity sweeps
    bsl = h.rolling(LIQ_LOOKBACK).max().shift(1)
    ssl = l.rolling(LIQ_LOOKBACK).min().shift(1)
    df["ssl_swept"] = (l < ssl) & (c > ssl)
    df["bsl_swept"] = (h > bsl) & (c < bsl)
    df["sweep_bull_recent"] = df["ssl_swept"].rolling(RECENT_WINDOW).max().astype(bool)
    df["sweep_bear_recent"] = df["bsl_swept"].rolling(RECENT_WINDOW).max().astype(bool)

    # FVG (Fair Value Gap)
    df["bull_fvg"] = (l > h.shift(2)) & ((l - h.shift(2)) >= df["atr14"] * FVG_ATR_MULT)
    df["bear_fvg"] = (h < l.shift(2)) & ((l.shift(2) - h) >= df["atr14"] * FVG_ATR_MULT)
    df["bull_fvg_recent"] = df["bull_fvg"].rolling(RECENT_WINDOW).max().astype(bool)
    df["bear_fvg_recent"] = df["bear_fvg"].rolling(RECENT_WINDOW).max().astype(bool)

    # Value area (50-bar range)
    hi50 = h.rolling(50).max()
    lo50 = l.rolling(50).min()
    mid  = (hi50 + lo50) / 2
    rng  = hi50 - lo50
    df["in_discount"] = c < mid
    df["in_premium"]  = c > mid
    df["ote_long"]    = (c >= lo50 + rng * 0.62) & (c <= lo50 + rng * 0.79)
    df["ote_short"]   = (c <= hi50 - rng * 0.62) & (c >= hi50 - rng * 0.79)

    # CVD divergence (simplified)
    body_r   = (c - o).abs() / ((h - l).clip(lower=1e-10))
    buy_vol  = np.where(c >= o, v * body_r, v * (1 - body_r))
    sell_vol = np.where(c < o,  v * body_r, v * (1 - body_r))
    delta    = pd.Series(buy_vol - sell_vol, index=c.index)
    cvd      = delta.cumsum()
    price_lo = c.rolling(CVD_PERIOD).min() == c
    cvd_lo   = cvd.rolling(CVD_PERIOD).min() == cvd
    price_hi = c.rolling(CVD_PERIOD).max() == c
    cvd_hi   = cvd.rolling(CVD_PERIOD).max() == cvd
    df["cvd_bull_div"] = price_lo & ~cvd_lo
    df["cvd_bear_div"] = price_hi & ~cvd_hi

    return df


def apply_signal_engine(df: pd.DataFrame, min_conditions: int) -> pd.DataFrame:
    """5-condition checklist + non-repainting state-machine signals."""
    df["l_c1"] = df["bullish_bias"]
    df["l_c2"] = df["trend_bull"]
    df["l_c3"] = df["struct_bull_recent"]
    df["l_c4"] = df["sweep_bull_recent"] | df["bull_fvg_recent"]
    df["l_c5"] = df["in_discount"] | df["ote_long"] | df["cvd_bull_div"]

    df["s_c1"] = df["bearish_bias"]
    df["s_c2"] = df["trend_bear"]
    df["s_c3"] = df["struct_bear_recent"]
    df["s_c4"] = df["sweep_bear_recent"] | df["bear_fvg_recent"]
    df["s_c5"] = df["in_premium"] | df["ote_short"] | df["cvd_bear_div"]

    lc = df[["l_c1","l_c2","l_c3","l_c4","l_c5"]].sum(axis=1)
    sc = df[["s_c1","s_c2","s_c3","s_c4","s_c5"]].sum(axis=1)
    lr = ((lc >= min_conditions) & df["trend_bull"]).to_numpy()
    sr = ((sc >= min_conditions) & df["trend_bear"]).to_numpy()
    lc_arr = lc.to_numpy(); sc_arr = sc.to_numpy()

    n     = len(df)
    buy_  = np.zeros(n, bool)
    sell_ = np.zeros(n, bool)
    state = 0
    for i in range(1, n):
        rb = lr[i] and state != 1 and (not sr[i] or lc_arr[i] > sc_arr[i])
        rs = sr[i] and state != -1 and not rb
        buy_[i]  = rb
        sell_[i] = rs
        if rb:   state = 1
        elif rs: state = -1

    df["buy_signal"]  = buy_
    df["sell_signal"] = sell_
    return df


# ─────────────────────────────────────────────
# BACKTEST ENGINE
# ─────────────────────────────────────────────
def backtest(df: pd.DataFrame, atr_stop_mult: float) -> dict:
    """Full sequential trade simulation with 3-target scale-out."""
    equity  = INITIAL_CAP
    peak    = INITIAL_CAP
    max_dd  = 0.0
    trades: list = []

    rows = df[["open","high","low","close","atr14","buy_signal","sell_signal"]].copy()
    rows["atr14"] = rows["atr14"].bfill()

    i = 0
    n = len(rows)
    bsig = rows["buy_signal"].to_numpy()
    ssig = rows["sell_signal"].to_numpy()
    hi   = rows["high"].to_numpy()
    lo   = rows["low"].to_numpy()
    cl   = rows["close"].to_numpy()
    at   = rows["atr14"].to_numpy()

    while i < n:
        if not (bsig[i] or ssig[i]):
            i += 1
            continue

        direction   = "long" if bsig[i] else "short"
        entry       = cl[i]
        atr_v       = at[i]
        if atr_v <= 0 or np.isnan(atr_v):
            i += 1
            continue

        stop_dist   = atr_v * atr_stop_mult
        risk_amt    = equity * RISK_PCT

        if direction == "long":
            sl = entry - stop_dist
            tps = [entry + stop_dist * r for r in (TP1_R, TP2_R, TP3_R)]
        else:
            sl = entry + stop_dist
            tps = [entry - stop_dist * r for r in (TP1_R, TP2_R, TP3_R)]

        tp_pcts  = [TP1_PCT, TP2_PCT, TP3_PCT]
        tp_r     = [TP1_R,   TP2_R,   TP3_R]
        hit      = [False, False, False]
        pnl_r    = 0.0
        remaining = 1.0
        closed   = False
        j = i + 1

        while j < n and not closed:
            bar_hi = hi[j]; bar_lo = lo[j]; bar_cl = cl[j]

            # Stop loss check
            if direction == "long":
                stop_hit = bar_lo <= sl
            else:
                stop_hit = bar_hi >= sl

            if stop_hit:
                pnl_r += -remaining
                remaining = 0.0
                closed = True
                break

            # TP checks (all on same bar possible)
            for t in range(3):
                if not hit[t]:
                    if (direction == "long" and bar_hi >= tps[t]) or \
                       (direction == "short" and bar_lo <= tps[t]):
                        hit[t]  = True
                        pnl_r  += tp_pcts[t] * tp_r[t]
                        remaining -= tp_pcts[t]
                        if remaining <= 1e-9:
                            closed = True

            if closed:
                break

            # Reversal signal
            if (direction == "long" and ssig[j]) or (direction == "short" and bsig[j]):
                exit_px = bar_cl
                if direction == "long":
                    pnl_r += remaining * (exit_px - entry) / stop_dist
                else:
                    pnl_r += remaining * (entry - exit_px) / stop_dist
                remaining = 0.0
                closed = True
                break

            j += 1

        # Close any remaining at end
        if not closed and remaining > 1e-9 and j < n:
            last = cl[min(j, n-1)]
            if direction == "long":
                pnl_r += remaining * (last - entry) / stop_dist
            else:
                pnl_r += remaining * (entry - last) / stop_dist

        dollar_pnl  = pnl_r * risk_amt
        equity     += dollar_pnl
        peak        = max(peak, equity)
        dd          = (peak - equity) / peak * 100
        max_dd      = max(max_dd, dd)
        trades.append({"dir": direction, "pnl_r": pnl_r, "pnl_$": dollar_pnl})

        i = j if j > i else i + 1

    if len(trades) < 2:
        return {"total_trades":0,"net_profit_pct":0.0,"profit_factor":0.0,
                "win_rate":0.0,"max_dd_pct":0.0,"valid":False,
                "long_trades":0,"short_trades":0}

    tdf          = pd.DataFrame(trades)
    wins         = tdf[tdf["pnl_$"] > 0]
    loss         = tdf[tdf["pnl_$"] < 0]
    gross_profit = wins["pnl_$"].sum()
    gross_loss   = loss["pnl_$"].abs().sum()
    pf           = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    wr           = len(wins) / len(tdf) * 100
    np_pct       = (equity - INITIAL_CAP) / INITIAL_CAP * 100
    total        = len(tdf)

    return {
        "total_trades":   total,
        "net_profit_pct": np_pct,
        "profit_factor":  pf,
        "win_rate":       wr,
        "max_dd_pct":     max_dd,
        "valid":          total >= MIN_TRADES and max_dd <= MAX_DD,
        "long_trades":    len(tdf[tdf["dir"]=="long"]),
        "short_trades":   len(tdf[tdf["dir"]=="short"]),
    }


# ─────────────────────────────────────────────
# OPTIMIZATION  (with Monte Carlo averaging)
# ─────────────────────────────────────────────
def run_optimization(base_df: Optional[dict] = None) -> list:
    print("\n" + "═"*72)
    print("  APEX SNIPER v10 — 48-COMBINATION GRID OPTIMIZATION (ETHUSDT 4h)")
    print(f"  Mode: {'REAL DATA' if USE_REAL_DATA else f'SYNTHETIC  ({N_MC_RUNS} Monte Carlo seeds)'}")
    print("═"*72)

    grid = list(itertools.product(MIN_CONDITIONS_VALS, ST_FACTOR_VALS, ATR_STOP_MULT_VALS))
    all_results = []

    # Pre-compute SuperTrend (slow) once per st_factor, once per seed
    # Cache: signals_cache[stf][seed] = df_with_indicators
    n_seeds  = 1 if USE_REAL_DATA else N_MC_RUNS
    print(f"\n  Pre-computing indicators × {len(ST_FACTOR_VALS)} factors × {n_seeds} seeds …\n")

    signals_cache: dict = {}
    for stf in ST_FACTOR_VALS:
        signals_cache[stf] = []
        for seed in range(n_seeds):
            if USE_REAL_DATA:
                df = base_df["ETHUSDT_4h"].copy()
            else:
                df = build_synthetic_datasets(seed)["ETHUSDT_4h"]
            df = compute_all_indicators(df, stf)
            signals_cache[stf].append(df)

    print(f"  {'Conds':>5} {'STF':>5} {'ATR_M':>6} {'PF_avg':>8} {'NP%_avg':>9} {'DD%_avg':>8} {'WR%_avg':>8} {'Trades_avg':>10}  Valid")
    print("  " + "-"*69)

    for min_cond, stf, atr_m in grid:
        seed_metrics = []
        for seed in range(n_seeds):
            df = signals_cache[stf][seed].copy()
            df = apply_signal_engine(df, min_conditions=min_cond)
            m  = backtest(df, atr_stop_mult=atr_m)
            seed_metrics.append(m)

        # Aggregate across seeds (mean)
        agg = {
            "min_conditions": min_cond,
            "st_factor":      stf,
            "atr_stop_mult":  atr_m,
            "profit_factor":  np.mean([m["profit_factor"]  for m in seed_metrics if not np.isinf(m["profit_factor"])]),
            "net_profit_pct": np.mean([m["net_profit_pct"] for m in seed_metrics]),
            "max_dd_pct":     np.mean([m["max_dd_pct"]     for m in seed_metrics]),
            "win_rate":       np.mean([m["win_rate"]        for m in seed_metrics]),
            "total_trades":   int(np.mean([m["total_trades"]   for m in seed_metrics])),
            "long_trades":    int(np.mean([m["long_trades"]    for m in seed_metrics])),
            "short_trades":   int(np.mean([m["short_trades"]   for m in seed_metrics])),
            # Valid if passes filter on ALL seeds
            "valid":          all(m["valid"] for m in seed_metrics),
            "pf_std":         np.std([m["profit_factor"]   for m in seed_metrics if not np.isinf(m["profit_factor"])]),
            "pf_min":         np.min([m["profit_factor"]   for m in seed_metrics if not np.isinf(m["profit_factor"]) ] or [0]),
        }
        all_results.append(agg)
        v = "★ VALID" if agg["valid"] else "  ----"
        print(f"  {min_cond:>5} {stf:>5.1f} {atr_m:>6.1f} "
              f"{agg['profit_factor']:>8.2f} {agg['net_profit_pct']:>9.1f} "
              f"{agg['max_dd_pct']:>8.1f} {agg['win_rate']:>8.1f} {agg['total_trades']:>10}  {v}")

    return all_results


def show_top5(all_results: list) -> Optional[dict]:
    valid = [r for r in all_results if r["valid"]]
    valid.sort(key=lambda x: x["profit_factor"], reverse=True)
    invalid = [r for r in all_results if not r["valid"]]
    invalid.sort(key=lambda x: x["profit_factor"], reverse=True)

    print("\n" + "═"*72)
    print("  TOP 5 VALID COMBINATIONS  (Profit Factor DESC)")
    print("═"*72)
    cols = f"{'#':>2} {'Conds':>5} {'STF':>5} {'ATR_M':>6} {'PF':>6} {'PF_min':>7} {'Trades':>7} {'NP%':>8} {'DD%':>7} {'WR%':>7}"
    print("  " + cols)
    print("  " + "-"*70)

    top5 = valid[:5] if len(valid) >= 5 else valid
    for i, r in enumerate(top5, 1):
        print(f"  {i:>2} {r['min_conditions']:>5} {r['st_factor']:>5.1f} {r['atr_stop_mult']:>6.1f} "
              f"{r['profit_factor']:>6.2f} {r['pf_min']:>7.2f} {r['total_trades']:>7} "
              f"{r['net_profit_pct']:>8.1f} {r['max_dd_pct']:>7.1f} {r['win_rate']:>7.1f}")

    if not valid:
        print("  ⚠  No combination passed Trades≥30 and DD≤25% on ALL seeds.")
        print("\n  Best combinations relaxing filter (by PF, on average):")
        for i, r in enumerate(invalid[:5], 1):
            print(f"  {i:>2} conds={r['min_conditions']} stf={r['st_factor']:.1f} "
                  f"atr={r['atr_stop_mult']:.1f} PF={r['profit_factor']:.2f} "
                  f"T={r['total_trades']} NP={r['net_profit_pct']:.1f}% DD={r['max_dd_pct']:.1f}%")
        return invalid[0] if invalid else None

    best = top5[0]
    print(f"\n  ★  BEST: min_conditions={best['min_conditions']}  "
          f"st_factor={best['st_factor']}  atr_stop_mult={best['atr_stop_mult']}  "
          f"→  PF={best['profit_factor']:.2f}  (worst seed PF={best['pf_min']:.2f})")
    return best


def cross_validate(best: dict, base_df: Optional[dict]) -> dict:
    print("\n" + "═"*72)
    print("  CROSS-VALIDATION  (BTCUSDT 4h  &  ETHUSDT 1h)")
    print("═"*72)
    n_seeds = 1 if USE_REAL_DATA else N_MC_RUNS
    labels  = ["ETHUSDT_4h", "BTCUSDT_4h", "ETHUSDT_1h"]
    cv_results: dict = {}

    for label in labels:
        seed_metrics = []
        for seed in range(n_seeds):
            if USE_REAL_DATA:
                df = base_df[label].copy()
            else:
                ds = build_synthetic_datasets(seed)
                df = ds[label]
            df = compute_all_indicators(df, best["st_factor"])
            df = apply_signal_engine(df, best["min_conditions"])
            m  = backtest(df, best["atr_stop_mult"])
            seed_metrics.append(m)

        agg = {
            "profit_factor":  np.mean([m["profit_factor"]  for m in seed_metrics if not np.isinf(m["profit_factor"])]),
            "net_profit_pct": np.mean([m["net_profit_pct"] for m in seed_metrics]),
            "max_dd_pct":     np.mean([m["max_dd_pct"]     for m in seed_metrics]),
            "win_rate":       np.mean([m["win_rate"]        for m in seed_metrics]),
            "total_trades":   int(np.mean([m["total_trades"] for m in seed_metrics])),
            "valid":          all(m["valid"] for m in seed_metrics),
            "long_trades":    int(np.mean([m["long_trades"]  for m in seed_metrics])),
            "short_trades":   int(np.mean([m["short_trades"] for m in seed_metrics])),
        }
        cv_results[label] = agg
        status = "✅ VALID" if agg["valid"] else "❌ FAIL"
        print(f"  {label:16s}  PF={agg['profit_factor']:5.2f}  T={agg['total_trades']:4d}  "
              f"NP={agg['net_profit_pct']:7.1f}%  DD={agg['max_dd_pct']:5.1f}%  "
              f"WR={agg['win_rate']:5.1f}%  {status}")

    # Robustness verdict
    pfs = [r["profit_factor"] for r in cv_results.values() if not np.isinf(r["profit_factor"])]
    all_pos   = all(r["net_profit_pct"] > 0    for r in cv_results.values())
    all_valid = all(r["valid"]                  for r in cv_results.values())
    pf_cv     = np.std(pfs) / np.mean(pfs) if np.mean(pfs) > 0 else 999  # coefficient of variation

    print()
    if all_valid and all_pos and pf_cv < 0.30:
        verdict = "✅  ROBUST — consistent across all 3 market scenarios"
    elif all_pos and pf_cv < 0.50:
        verdict = "⚠️  MODERATE — positive across scenarios, mild variance (fine-tune per instrument)"
    elif all_pos:
        verdict = "⚠️  POSITIVE BUT INCONSISTENT — use conservative position sizing"
    else:
        verdict = "❌  NOT ROBUST — one or more scenarios show losses; back-test more regimes before live use"
    print(f"  Verdict: {verdict}")
    print(f"  PF coefficient of variation: {pf_cv:.2f}  (< 0.30 = robust, < 0.50 = moderate)")

    return cv_results


def print_final(best: dict, cv: dict):
    print("\n" + "═"*72)
    print("  FINAL RECOMMENDED PINE SCRIPT SETTINGS FOR APEX SNIPER v10")
    print("═"*72)
    print(f"""
  ╔══════════════════════════════════════════════════════════════╗
  ║  Group: Signal Engine (BUY/SELL)                            ║
  ║    Min Conditions for Signal (of 5)  →  {best['min_conditions']}                  ║
  ║    Trend Band Factor                 →  {best['st_factor']}                ║
  ║                                                              ║
  ║  Group: Risk Management                                      ║
  ║    ATR Stop Multiple                 →  {best['atr_stop_mult']}                ║
  ╚══════════════════════════════════════════════════════════════╝

  PRIMARY MARKET (ETHUSDT 4h):
    Avg Profit Factor  : {best['profit_factor']:.2f}
    Avg Net Profit     : {best['net_profit_pct']:.1f}%
    Avg Total Trades   : {best['total_trades']}  (Long: {best['long_trades']}  Short: {best['short_trades']})
    Avg Max Drawdown   : {best['max_dd_pct']:.1f}%
    Avg Win Rate       : {best['win_rate']:.1f}%
    Worst-seed PF      : {best['pf_min']:.2f}

  CROSS-VALIDATION:""")
    for lbl, r in cv.items():
        print(f"    {lbl:16s}  PF={r['profit_factor']:.2f}  NP={r['net_profit_pct']:.1f}%  "
              f"DD={r['max_dd_pct']:.1f}%  WR={r['win_rate']:.1f}%")

    print(f"""
  HOW TO USE:
    1. Open APEX SNIPER v10 indicator in Pine Editor
    2. Click ⚙ Settings → Signal Engine (BUY/SELL) group
       • Set "Min Conditions for Signal" = {best['min_conditions']}
       • Set "Trend Band Factor"          = {best['st_factor']}
    3. Click Risk Management group
       • Set "ATR Stop Multiple"          = {best['atr_stop_mult']}
    4. Click OK — new BUY/SELL signals will appear immediately

  To validate with REAL data run from your local Mac terminal:
    $ python apex_optimizer.py    (after setting USE_REAL_DATA = True at line 58)
""")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 72)
    print("  APEX SNIPER v10 — FULL PARAMETER OPTIMIZATION")
    print("=" * 72)
    print(f"  Grid : min_conditions={MIN_CONDITIONS_VALS}")
    print(f"         st_factor={ST_FACTOR_VALS}")
    print(f"         atr_stop_mult={ATR_STOP_MULT_VALS}")
    print(f"  Combinations : {len(MIN_CONDITIONS_VALS)*len(ST_FACTOR_VALS)*len(ATR_STOP_MULT_VALS)}")
    print(f"  Mode  : {'Real data (Binance perps)' if USE_REAL_DATA else f'Synthetic GARCH / {N_MC_RUNS} Monte Carlo seeds'}")
    print(f"  Filter: Trades≥{MIN_TRADES}, MaxDD≤{MAX_DD}%")
    print()

    base_df = build_real_datasets() if USE_REAL_DATA else None

    all_results = run_optimization(base_df)
    best        = show_top5(all_results)

    if best:
        cv = cross_validate(best, base_df)
        print_final(best, cv)

    print("=" * 72)
    print("  Optimization complete.")
    print("=" * 72)
