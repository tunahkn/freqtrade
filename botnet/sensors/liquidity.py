"""
liquidity-bot: likidite avı (stop hunt) ve likidasyon dalgaları.

- Sweep: son kapanan mum, önceki N mumun en düşüğünün altına iğne atıp tekrar üstünde
  kapattıysa -> altındaki stoplar toplandı, yukarı dönüş beklenir (sweep_low, boğa).
  Tersi sweep_high (ayı).
- Likidasyon dalgası: kısa sürede çok long likide olduysa zayıf eller temizlendi ->
  dip yakın olabilir (boğa, sweep ile birlikteyse güçlü). Short dalgası tersi.
- Likidite havuzları: birbirine çok yakın eşit tepeler/dipler (stop kümeleri), hedef
  olarak raporlanır.
"""

from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd

from botnet.common.bus import Signal
from botnet.common.indicators import atr
from botnet.common.okx import OkxClient


NAME = "liquidity"
LOOKBACK = 20
LIQ_WAVE_BTC = float(os.environ.get("BOTNET_LIQ_WAVE_BTC", "50"))


def detect_sweep(df: pd.DataFrame, lookback: int = LOOKBACK) -> tuple[int, float]:
    """(+1 sweep_low | -1 sweep_high | 0, süpürülen seviye)."""
    if len(df) < lookback + 2:
        return 0, float("nan")
    last = df.iloc[-1]
    prior = df.iloc[-lookback - 1 : -1]
    prior_low, prior_high = float(prior["low"].min()), float(prior["high"].max())
    rng = float(last["high"] - last["low"]) or 1e-9
    if last["low"] < prior_low and last["close"] > prior_low:
        lower_wick = (min(last["open"], last["close"]) - last["low"]) / rng
        if lower_wick >= 0.4:
            return 1, prior_low
    if last["high"] > prior_high and last["close"] < prior_high:
        upper_wick = (last["high"] - max(last["open"], last["close"])) / rng
        if upper_wick >= 0.4:
            return -1, prior_high
    return 0, float("nan")


def liquidity_pools(df: pd.DataFrame, tolerance_atr: float = 0.15) -> dict[str, list[float]]:
    """Eşit tepeler (üstte stop kümesi) ve eşit dipler (altta stop kümesi)."""
    if len(df) < 50:
        return {"above": [], "below": []}
    a = float(atr(df).iloc[-1])
    tol = a * tolerance_atr
    price = float(df["close"].iloc[-1])
    recent = df.tail(100)

    def clusters(values: np.ndarray) -> list[float]:
        values = np.sort(values)
        out, group = [], [values[0]]
        for v in values[1:]:
            if v - group[-1] <= tol:
                group.append(v)
            else:
                if len(group) >= 3:
                    out.append(float(np.mean(group)))
                group = [v]
        if len(group) >= 3:
            out.append(float(np.mean(group)))
        return out

    highs = clusters(recent["high"].to_numpy())
    lows = clusters(recent["low"].to_numpy())
    return {
        "above": sorted([h for h in highs if h > price])[:3],
        "below": sorted([lo for lo in lows if lo < price], reverse=True)[:3],
    }


def analyze(df: pd.DataFrame, liqs: pd.DataFrame, now: float | None = None) -> Signal:
    if df is None or len(df) < LOOKBACK + 2:
        return Signal(bot=NAME, score=0.0, confidence=0.0, tags=["no_data"])
    now = time.time() if now is None else now
    tags: list[str] = []

    sweep, level = detect_sweep(df)
    if sweep == 1:
        tags.append("sweep_low")
    elif sweep == -1:
        tags.append("sweep_high")

    long_liq = short_liq = 0.0
    if liqs is not None and len(liqs):
        recent = liqs[liqs["ts"] >= now - 900]
        long_liq = float(recent.loc[recent["pos_side"] == "long", "btc"].sum())
        short_liq = float(recent.loc[recent["pos_side"] == "short", "btc"].sum())
    wave = 0.0
    if long_liq >= LIQ_WAVE_BTC and long_liq > 2 * short_liq:
        wave = 0.4
        tags.append("long_liq_wave")
    elif short_liq >= LIQ_WAVE_BTC and short_liq > 2 * long_liq:
        wave = -0.4
        tags.append("short_liq_wave")

    score = 0.6 * sweep + wave
    if sweep and np.sign(wave) == sweep:
        tags.append("stop_hunt_reversal")
    confidence = 0.2
    if sweep:
        confidence += 0.4
    if wave:
        confidence += 0.2
    if "stop_hunt_reversal" in tags:
        confidence += 0.2

    return Signal(
        bot=NAME,
        score=score,
        confidence=min(1.0, confidence),
        price=float(df["close"].iloc[-1]),
        tags=tags,
        details={
            "sweep": sweep,
            "swept_level": None if np.isnan(level) else level,
            "long_liq_btc_15m": round(long_liq, 3),
            "short_liq_btc_15m": round(short_liq, 3),
            "pools": liquidity_pools(df),
        },
    )


class LiquiditySensor:
    name = NAME
    interval = 60.0

    def __init__(self, client: OkxClient | None = None):
        self.client = client or OkxClient()

    def step(self) -> Signal:
        sig = analyze(self.client.candles("15m", 200), self.client.liquidations())
        # Sweep sinyali bir mum (15 dk) boyunca geçerli
        sig.ttl = 900
        return sig


def main() -> None:
    from botnet.common.runner import run_forever

    run_forever(LiquiditySensor())


if __name__ == "__main__":
    main()
