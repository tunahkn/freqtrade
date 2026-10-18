import json
import time

import numpy as np

from botnet.brain.decision import DEFAULT_WEIGHTS
from botnet.common.bus import Bus, Signal
from botnet.learner import learner


def _simulate(bus: Bus, days: int = 10):
    """'good' botu geleceği doğru, 'bad' botu ters tahmin ediyor."""
    rng = np.random.default_rng(0)
    start = time.time() - days * 86400
    step = 900
    n = days * 86400 // step
    prices = 60000 * np.exp(np.cumsum(rng.normal(0, 0.004, n + 40)))
    horizon_steps = int(learner.HORIZON_SEC // step)
    for i in range(n):
        ts = start + i * step
        future = prices[i + horizon_steps] if i + horizon_steps < len(prices) else prices[i]
        direction = 1.0 if future > prices[i] else -1.0
        bus.publish(
            Signal(
                bot="trend", score=0.6 * direction, confidence=0.8, price=prices[i], ts=ts, ttl=900
            )
        )
        bus.publish(
            Signal(
                bot="orderflow",
                score=-0.6 * direction,
                confidence=0.8,
                price=prices[i],
                ts=ts,
                ttl=900,
            )
        )


def test_learner_rewards_good_and_punishes_bad(tmp_path):
    bus = Bus(tmp_path / "s.db")
    _simulate(bus)
    stats = learner.evaluate(bus.history())
    assert stats["trend"].hit_rate > 0.9
    assert stats["orderflow"].hit_rate < 0.1

    wpath = tmp_path / "weights.json"
    new = learner.run_once(bus, wpath)
    assert new["trend"] > DEFAULT_WEIGHTS["trend"]
    assert new["orderflow"] < DEFAULT_WEIGHTS["orderflow"]
    assert abs(sum(new.values()) - 1) < 0.01
    assert max(new.values()) <= learner.MAX_WEIGHT + 1e-6
    assert min(new.values()) >= learner.MIN_WEIGHT - 1e-6
    saved = json.loads(wpath.read_text())
    assert saved["weights"] == new


def test_learner_step_limit():
    stats = {"trend": learner.BotStats("trend", 100, 1.0, 0.01)}
    new = learner.update_weights(dict(DEFAULT_WEIGHTS), stats)
    # Normalize öncesi en fazla +0.10 adım
    assert new["trend"] <= DEFAULT_WEIGHTS["trend"] + learner.MAX_STEP + 1e-6


def test_learner_ignores_small_samples():
    stats = {"trend": learner.BotStats("trend", 5, 1.0, 0.01)}
    assert learner.update_weights(dict(DEFAULT_WEIGHTS), stats) == {
        k: round(v, 4) for k, v in DEFAULT_WEIGHTS.items()
    }
