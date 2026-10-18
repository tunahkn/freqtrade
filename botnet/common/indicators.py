"""
Saf pandas/numpy indikatörleri. TA-Lib gerektirmez; sensörler hafif kalır ve
ana beyin de aynı hesapları kullanır (backtest ile canlı tutarlı olsun diye).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # Hiç kayıp yoksa RSI 100
    return out.where(avg_loss != 0, 100.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    macd = ema(close, fast) - ema(close, slow)
    return macd - ema(macd, signal)


def trend_score_frame(df: pd.DataFrame) -> pd.Series:
    """
    Tek zaman dilimi için vektörel trend skoru (-1..+1).
    Hem trend-bot hem BrainStrategy (backtest) bunu kullanır.

    Bileşenler:
      0.35 * EMA20 > EMA50 yönü
      0.25 * fiyat EMA100 üstünde/altında
      0.20 * RSI sapması (50 etrafı, ±20'de doyar)
      0.20 * MACD histogram yönü
    """
    close = df["close"]
    e20, e50, e100 = ema(close, 20), ema(close, 50), ema(close, 100)
    r = rsi(close, 14)
    h = macd_hist(close)
    score = (
        0.35 * np.sign(e20 - e50)
        + 0.25 * np.sign(close - e100)
        + 0.20 * ((r - 50) / 20).clip(-1, 1)
        + 0.20 * np.sign(h)
    )
    return score.fillna(0.0)


def swing_points(df: pd.DataFrame, left: int = 3, right: int = 3) -> tuple[pd.Series, pd.Series]:
    """
    Fraktal tepe/dip noktaları. Bir mum ancak sağında ``right`` mum kapandıktan sonra
    onaylanır; bu yüzden sonuç ``right`` kadar geriye kaydırılmış biçimde döner
    (gelecek verisi sızdırmaz).
    """
    win = left + right + 1
    is_high = df["high"] == df["high"].rolling(win, center=True).max()
    is_low = df["low"] == df["low"].rolling(win, center=True).min()
    highs = df["high"].where(is_high).shift(right)
    lows = df["low"].where(is_low).shift(right)
    return highs, lows


def market_structure(df: pd.DataFrame) -> int:
    """
    Son iki onaylı swing'e göre piyasa yapısı:
    +1 = HH + HL (yükselen), -1 = LH + LL (düşen), 0 = karışık.
    """
    highs, lows = swing_points(df)
    h = highs.dropna().tail(2).tolist()
    lo = lows.dropna().tail(2).tolist()
    if len(h) < 2 or len(lo) < 2:
        return 0
    if h[1] > h[0] and lo[1] > lo[0]:
        return 1
    if h[1] < h[0] and lo[1] < lo[0]:
        return -1
    return 0
