"""
sentiment-bot: Fear & Greed endeksi (contrarian).

Aşırı korku (<25) -> herkes satmış, boğa eğilimi. Aşırı açgözlülük (>75) -> ayı eğilimi.
Yavaş değişen bir veridir; güveni düşük tutulur ve sadece arka plan rüzgârı sayılır.
"""

from __future__ import annotations

from botnet.common.bus import Signal
from botnet.common.okx import fear_greed_index


NAME = "sentiment"


def analyze(fng: int) -> Signal:
    score = (50 - fng) / 50
    tags = []
    if fng <= 25:
        tags.append("extreme_fear")
    elif fng >= 75:
        tags.append("extreme_greed")
    confidence = 0.15 + 0.25 * min(1.0, abs(fng - 50) / 40)
    return Signal(bot=NAME, score=score, confidence=confidence, tags=tags, details={"fng": fng})


class SentimentSensor:
    name = NAME
    interval = 3600.0

    def step(self) -> Signal:
        return analyze(fear_greed_index())


def main() -> None:
    from botnet.common.runner import run_forever

    run_forever(SentimentSensor())


if __name__ == "__main__":
    main()
