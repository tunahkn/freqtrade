"""
orderbook-bot: emir defteri dengesizliği, büyük duvarlar ve spoofing.

- Dengesizlik: fiyatın ±%0.5 ve ±%1.5 bandındaki alış/satış emir hacmi farkı.
- Duvar: medyan seviyenin WALL_MULT katından büyük tek seviye.
- Spoofing: önceki görüntüde olan büyük duvar, fiyat ona ulaşmadan kaybolduysa
  (sahte emir çekildi). Çekilen alış duvarı -> ayı işareti, çekilen satış duvarı -> boğa.
"""

from __future__ import annotations

import math
import os

import numpy as np

from botnet.common.bus import Signal
from botnet.common.okx import OkxClient


NAME = "orderbook"
WALL_MULT = float(os.environ.get("BOTNET_WALL_MULT", "8"))

Levels = list[tuple[float, float]]


def band_volume(levels: Levels, mid: float, pct: float) -> float:
    lo, hi = mid * (1 - pct), mid * (1 + pct)
    return float(sum(s for p, s in levels if lo <= p <= hi))


def find_walls(levels: Levels, mid: float, pct: float = 0.02, mult: float = WALL_MULT) -> Levels:
    near = [(p, s) for p, s in levels if abs(p - mid) / mid <= pct]
    if len(near) < 10:
        return []
    med = float(np.median([s for _, s in near]))
    return [(p, s) for p, s in near if med > 0 and s >= mult * med]


def detect_pulled(prev: Levels, curr: Levels, mid: float, is_bid: bool) -> list[float]:
    """Önceki duvarlardan, fiyat o seviyeye değmeden kaybolanlar."""
    curr_prices = {round(p, 1) for p, _ in curr}
    pulled = []
    for p, _ in prev:
        if round(p, 1) in curr_prices:
            continue
        # Fiyat duvara ulaştıysa (yendiyse) spoofing değildir
        touched = mid <= p if is_bid else mid >= p
        if not touched:
            pulled.append(p)
    return pulled


def analyze(
    bids: Levels,
    asks: Levels,
    prev_bid_walls: Levels | None = None,
    prev_ask_walls: Levels | None = None,
) -> tuple[Signal, Levels, Levels]:
    if not bids or not asks:
        return Signal(bot=NAME, score=0.0, confidence=0.0, tags=["no_data"]), [], []

    mid = (bids[0][0] + asks[0][0]) / 2
    imbalances = {}
    for pct in (0.005, 0.015):
        b, a = band_volume(bids, mid, pct), band_volume(asks, mid, pct)
        imbalances[pct] = (b - a) / (b + a) if (b + a) else 0.0

    bid_walls = find_walls(bids, mid)
    ask_walls = find_walls(asks, mid)
    tags: list[str] = []
    spoof = 0.0
    pulled_bids = detect_pulled(prev_bid_walls or [], bid_walls, mid, is_bid=True)
    pulled_asks = detect_pulled(prev_ask_walls or [], ask_walls, mid, is_bid=False)
    if pulled_bids:
        spoof -= 0.3
        tags.append("bid_wall_pulled")
    if pulled_asks:
        spoof += 0.3
        tags.append("ask_wall_pulled")
    if bid_walls:
        tags.append("bid_wall")
    if ask_walls:
        tags.append("ask_wall")

    score = 0.4 * math.tanh(2 * imbalances[0.005]) + 0.3 * math.tanh(2 * imbalances[0.015]) + spoof
    # Emir defteri gürültülüdür; güveni sınırlı tut
    agree = np.sign(imbalances[0.005]) == np.sign(imbalances[0.015])
    confidence = 0.35 + (0.15 if agree else 0.0) + (0.1 if spoof else 0.0)

    sig = Signal(
        bot=NAME,
        score=score,
        confidence=confidence,
        price=mid,
        tags=tags,
        details={
            "imb_0.5pct": round(imbalances[0.005], 4),
            "imb_1.5pct": round(imbalances[0.015], 4),
            "bid_walls": bid_walls[:5],
            "ask_walls": ask_walls[:5],
            "pulled_bids": pulled_bids[:5],
            "pulled_asks": pulled_asks[:5],
        },
    )
    return sig, bid_walls, ask_walls


class OrderbookSensor:
    name = NAME
    interval = 10.0

    def __init__(self, client: OkxClient | None = None):
        self.client = client or OkxClient()
        self.prev_bid_walls: Levels = []
        self.prev_ask_walls: Levels = []

    def step(self) -> Signal:
        bids, asks = self.client.orderbook(400)
        sig, self.prev_bid_walls, self.prev_ask_walls = analyze(
            bids, asks, self.prev_bid_walls, self.prev_ask_walls
        )
        return sig


def main() -> None:
    from botnet.common.runner import run_forever

    run_forever(OrderbookSensor())


if __name__ == "__main__":
    main()
