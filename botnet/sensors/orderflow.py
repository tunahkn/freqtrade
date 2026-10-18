"""
orderflow-bot: balina işlemleri, CVD (kümülatif delta), absorption.

- Delta = agresif alış hacmi - agresif satış hacmi.
- Balina = tek işlemde >= WHALE_BTC.
- Absorption: güçlü satış baskısına rağmen fiyat düşmüyorsa (alıcı emir duvarı yutuyor)
  -> boğa işareti. Tersi -> ayı işareti.
"""

from __future__ import annotations

import math
import os
import time
from collections import deque

import pandas as pd

from botnet.common.bus import Signal
from botnet.common.okx import OkxClient


NAME = "orderflow"
WHALE_BTC = float(os.environ.get("BOTNET_WHALE_BTC", "10"))
WINDOW_SEC = float(os.environ.get("BOTNET_ORDERFLOW_WINDOW", "900"))


def analyze(trades: pd.DataFrame, whale_btc: float = WHALE_BTC) -> Signal:
    if trades is None or len(trades) < 20:
        return Signal(bot=NAME, score=0.0, confidence=0.0, tags=["no_data"])

    buys = trades[trades["side"] == "buy"]
    sells = trades[trades["side"] == "sell"]
    buy_vol, sell_vol = float(buys["btc"].sum()), float(sells["btc"].sum())
    total = buy_vol + sell_vol
    delta = buy_vol - sell_vol
    delta_ratio = delta / total if total else 0.0

    whale_buys = buys[buys["btc"] >= whale_btc]
    whale_sells = sells[sells["btc"] >= whale_btc]
    wb, ws = float(whale_buys["btc"].sum()), float(whale_sells["btc"].sum())
    whale_ratio = (wb - ws) / (wb + ws) if (wb + ws) else 0.0

    first, last = float(trades["price"].iloc[0]), float(trades["price"].iloc[-1])
    price_change = (last - first) / first if first else 0.0

    tags: list[str] = []
    absorption = 0.0
    # Delta güçlü negatif ama fiyat düşmedi -> alıcılar satışı emiyor
    if delta_ratio < -0.25 and price_change >= -0.0005:
        absorption = 0.6
        tags.append("bull_absorption")
    elif delta_ratio > 0.25 and price_change <= 0.0005:
        absorption = -0.6
        tags.append("bear_absorption")

    if wb >= 3 * whale_btc and whale_ratio > 0.3:
        tags.append("whale_buy")
    if ws >= 3 * whale_btc and whale_ratio < -0.3:
        tags.append("whale_sell")

    # Absorption varsa delta yanıltıcıdır, ağırlığı azalt
    delta_w = 0.2 if absorption else 0.45
    score = delta_w * math.tanh(2.5 * delta_ratio) + 0.35 * whale_ratio + absorption
    # Hacim ve balina katılımı arttıkça güven artar
    whale_share = (wb + ws) / total if total else 0.0
    confidence = min(1.0, 0.3 + 0.4 * min(1.0, total / (50 * whale_btc)) + 0.3 * whale_share)

    return Signal(
        bot=NAME,
        score=score,
        confidence=confidence,
        price=last,
        tags=tags,
        details={
            "buy_btc": round(buy_vol, 3),
            "sell_btc": round(sell_vol, 3),
            "delta_btc": round(delta, 3),
            "delta_ratio": round(delta_ratio, 4),
            "whale_buy_btc": round(wb, 3),
            "whale_sell_btc": round(ws, 3),
            "price_change": round(price_change, 5),
            "n_trades": len(trades),
        },
    )


class OrderflowSensor:
    """
    OKX /market/trades son 500 işlemi verir; her adımda çekip trade_id ile tekrarları ayıklar
    ve WINDOW_SEC'lik kayan pencere tutar.
    """

    name = NAME
    interval = 5.0
    publish_every = 30.0

    def __init__(self, client: OkxClient | None = None):
        self.client = client or OkxClient()
        self.seen: deque[str] = deque(maxlen=20000)
        self.seen_set: set[str] = set()
        self.window: list[dict] = []
        self.last_publish = 0.0

    def ingest(self, trades: pd.DataFrame) -> None:
        for row in trades.itertuples(index=False):
            if row.trade_id in self.seen_set:
                continue
            if len(self.seen) == self.seen.maxlen:
                self.seen_set.discard(self.seen[0])
            self.seen.append(row.trade_id)
            self.seen_set.add(row.trade_id)
            self.window.append(row._asdict())
        cutoff = time.time() - WINDOW_SEC
        self.window = [t for t in self.window if t["ts"] >= cutoff]

    def step(self) -> Signal | None:
        self.ingest(self.client.trades(500))
        if time.time() - self.last_publish < self.publish_every:
            return None
        self.last_publish = time.time()
        sig = analyze(pd.DataFrame(self.window))
        sig.ttl = 120
        return sig


def main() -> None:
    from botnet.common.runner import run_forever

    run_forever(OrderflowSensor())


if __name__ == "__main__":
    main()
