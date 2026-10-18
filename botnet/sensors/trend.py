"""
trend-bot: 15m / 1h / 4h trend ve piyasa yapısı.

Ağırlıklar: 15m %25, 1h %35, 4h %40. Büyük zaman dilimi yönü daha önemli.
Güven = zaman dilimlerinin aynı yönü gösterme derecesi.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from botnet.common.bus import Signal
from botnet.common.indicators import market_structure, trend_score_frame
from botnet.common.okx import OkxClient


NAME = "trend"
TF_WEIGHTS = {"15m": 0.25, "1H": 0.35, "4H": 0.40}


def analyze(frames: dict[str, pd.DataFrame]) -> Signal:
    per_tf: dict[str, float] = {}
    structure: dict[str, int] = {}
    for tf, df in frames.items():
        if df is None or len(df) < 120:
            continue
        ts = float(trend_score_frame(df).iloc[-1])
        ms = market_structure(df)
        # Piyasa yapısı trend skorunu %25 etkiler
        per_tf[tf] = float(np.clip(0.75 * ts + 0.25 * ms, -1, 1))
        structure[tf] = ms

    if not per_tf:
        return Signal(bot=NAME, score=0.0, confidence=0.0, tags=["no_data"])

    total_w = sum(TF_WEIGHTS[tf] for tf in per_tf)
    score = sum(TF_WEIGHTS[tf] * s for tf, s in per_tf.items()) / total_w
    signs = [np.sign(s) for s in per_tf.values() if abs(s) > 0.15]
    agreement = abs(sum(signs)) / len(per_tf) if signs else 0.0
    confidence = 0.3 + 0.6 * agreement

    tags = []
    htf = per_tf.get("4H", 0.0)
    if htf > 0.4:
        tags.append("htf_bull")
    elif htf < -0.4:
        tags.append("htf_bear")
    if agreement == 1.0 and len(per_tf) == len(TF_WEIGHTS):
        tags.append("tf_aligned_up" if score > 0 else "tf_aligned_down")
    for tf, ms in structure.items():
        if ms == 1:
            tags.append(f"hh_hl_{tf}")
        elif ms == -1:
            tags.append(f"lh_ll_{tf}")

    last_price = None
    for tf in ("15m", "1H", "4H"):
        if tf in frames and frames[tf] is not None and len(frames[tf]):
            last_price = float(frames[tf]["close"].iloc[-1])
            break

    return Signal(
        bot=NAME,
        score=score,
        confidence=confidence,
        price=last_price,
        tags=tags,
        details={"per_tf": per_tf, "structure": structure, "htf_trend": htf},
    )


class TrendSensor:
    name = NAME
    interval = 60.0

    def __init__(self, client: OkxClient | None = None):
        self.client = client or OkxClient()

    def step(self) -> Signal:
        frames = {tf: self.client.candles(tf, 300) for tf in TF_WEIGHTS}
        return analyze(frames)


def main() -> None:
    from botnet.common.runner import run_forever

    run_forever(TrendSensor())


if __name__ == "__main__":
    main()
