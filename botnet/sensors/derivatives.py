"""
derivatives-bot: boğa/ayı kalabalığının konumu.

1. Funding + long/short oranı (kalabalığa karşı / contrarian):
   herkes long ve funding yüksekse -> long squeeze riski -> ayı eğilimi. Tersi -> boğa.
2. Open interest + fiyat (trend sağlığı):
   OI↑ fiyat↑ = yeni long para, sağlıklı yükseliş (+)
   OI↑ fiyat↓ = yeni short para, düşüş (−)
   OI↓ fiyat↑ = short kapatma, zayıf yükseliş (+ zayıf)
   OI↓ fiyat↓ = long tasfiyesi, düşüş yorgunluğu (− zayıf)
3. Taker alış/satış oranı: agresif taraf.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from botnet.common.bus import Signal
from botnet.common.okx import OkxClient


NAME = "derivatives"
# 8 saatlik funding için "yüksek" eşik (%0.03)
FUNDING_HOT = 0.0003


def analyze(
    funding: float,
    oi: pd.DataFrame,
    ls_ratio: pd.DataFrame,
    taker: pd.DataFrame,
    price_change: float,
    lookback: int = 12,
) -> Signal:
    tags: list[str] = []

    # 1) Kalabalık konumu (contrarian)
    ratio = float(ls_ratio["ratio"].iloc[-1]) if len(ls_ratio) else 1.0
    crowd = 0.0
    crowd -= float(np.clip(funding / FUNDING_HOT, -1.5, 1.5)) * 0.5
    crowd -= float(np.clip((ratio - 1.0) / 1.5, -1, 1)) * 0.5
    if funding > FUNDING_HOT and ratio > 1.8:
        tags.append("crowd_long_squeeze_risk")
    if funding < -FUNDING_HOT / 2 and ratio < 0.8:
        tags.append("crowd_short_squeeze_risk")

    # 2) OI + fiyat
    oi_change = 0.0
    if len(oi) > lookback:
        prev = float(oi["oi"].iloc[-lookback - 1])
        oi_change = (float(oi["oi"].iloc[-1]) - prev) / prev if prev else 0.0
    oi_up, px_up = oi_change > 0.005, price_change > 0.002
    oi_dn, px_dn = oi_change < -0.005, price_change < -0.002
    regime = 0.0
    if oi_up and px_up:
        regime, tag = 0.7, "new_longs"
    elif oi_up and px_dn:
        regime, tag = -0.7, "new_shorts"
    elif oi_dn and px_up:
        regime, tag = 0.3, "short_covering"
    elif oi_dn and px_dn:
        regime, tag = -0.3, "long_liquidation"
    else:
        tag = None
    if tag:
        tags.append(tag)

    # 3) Taker baskısı
    taker_ratio = 0.0
    if len(taker):
        recent = taker.tail(lookback)
        b, s = float(recent["buy"].sum()), float(recent["sell"].sum())
        taker_ratio = (b - s) / (b + s) if (b + s) else 0.0
    if taker_ratio > 0.1:
        tags.append("taker_buy_pressure")
    elif taker_ratio < -0.1:
        tags.append("taker_sell_pressure")

    score = 0.35 * crowd + 0.40 * regime + 0.25 * math.tanh(4 * taker_ratio)
    extremes = int(abs(funding) > FUNDING_HOT) + int(ratio > 1.8 or ratio < 0.8)
    confidence = min(1.0, 0.35 + 0.15 * extremes + (0.15 if tag else 0.0))

    return Signal(
        bot=NAME,
        score=score,
        confidence=confidence,
        tags=tags,
        details={
            "funding": funding,
            "ls_ratio": ratio,
            "oi_change": round(oi_change, 5),
            "price_change": round(price_change, 5),
            "taker_ratio": round(taker_ratio, 4),
        },
    )


class DerivativesSensor:
    name = NAME
    interval = 300.0

    def __init__(self, client: OkxClient | None = None):
        self.client = client or OkxClient()

    def step(self) -> Signal:
        candles = self.client.candles("5m", 20)
        price_change = 0.0
        if len(candles) > 12:
            p0, p1 = float(candles["close"].iloc[-13]), float(candles["close"].iloc[-1])
            price_change = (p1 - p0) / p0
        sig = analyze(
            funding=self.client.funding_rate(),
            oi=self.client.open_interest_history("5m"),
            ls_ratio=self.client.long_short_ratio("5m"),
            taker=self.client.taker_volume("5m"),
            price_change=price_change,
        )
        sig.price = float(candles["close"].iloc[-1]) if len(candles) else None
        return sig


def main() -> None:
    from botnet.common.runner import run_forever

    run_forever(DerivativesSensor())


if __name__ == "__main__":
    main()
