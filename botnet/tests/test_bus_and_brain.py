import json
import time

from botnet.brain.decision import DEFAULT_WEIGHTS, decide, fuse, load_weights
from botnet.common.bus import Bus, Signal


def test_signal_clamps():
    s = Signal(bot="x", score=3, confidence=-1)
    assert s.score == 1.0 and s.confidence == 0.0


def test_bus_publish_latest_and_staleness(tmp_path):
    bus = Bus(tmp_path / "s.db")
    now = time.time()
    bus.publish(Signal(bot="a", score=0.1, confidence=0.5, ts=now - 100, ttl=60))
    bus.publish(Signal(bot="a", score=0.7, confidence=0.5, ts=now - 10, ttl=60, tags=["t"]))
    bus.publish(Signal(bot="b", score=-0.5, confidence=0.5, ts=now - 1000, ttl=60))
    latest = bus.latest(now=now)
    assert set(latest) == {"a"}  # b bayat
    assert latest["a"].score == 0.7 and latest["a"].tags == ["t"]
    assert set(bus.latest(now=now, fresh_only=False)) == {"a", "b"}
    assert len(bus.history(bot="a")) == 2


def test_fuse_weighted_and_coverage():
    now = time.time()
    sigs = {
        "orderflow": Signal(bot="orderflow", score=1.0, confidence=1.0, ts=now, ttl=60),
        "derivatives": Signal(bot="derivatives", score=-1.0, confidence=0.5, ts=now, ttl=60),
        "old": Signal(bot="liquidity", score=1.0, confidence=1.0, ts=now - 999, ttl=60),
    }
    sigs = {s.bot: s for s in sigs.values()}
    f = fuse(sigs, DEFAULT_WEIGHTS, now=now, exclude=("trend",))
    w_of, w_de = DEFAULT_WEIGHTS["orderflow"], DEFAULT_WEIGHTS["derivatives"]
    expected = (w_of * 1.0 + w_de * -0.5) / (w_of + w_de)
    assert abs(f.score - expected) < 1e-9
    assert "liquidity" not in f.contributions
    assert 0 < f.coverage < 1


def test_decide_trend_filter_blocks_counter_trend_long():
    d = decide(tech_score=0.8, htf_trend=-0.8, fusion=None)
    assert d.side is None


def test_decide_long_and_short_without_sensors():
    assert decide(0.6, 0.5, None).side == "long"
    assert decide(-0.6, -0.5, None).side == "short"
    assert decide(0.1, 0.0, None).side is None


def test_decide_reversal_after_liquidity_sweep_overrides_trend_filter():
    from botnet.brain.decision import Fusion

    fusion = Fusion(score=0.3, coverage=0.8, tags=["sweep_low", "bull_absorption"])
    d = decide(tech_score=-0.1, htf_trend=-0.7, fusion=fusion)
    assert d.side == "long"
    assert d.tag.startswith("L:rev") and "sweepL" in d.tag and "absB" in d.tag


def test_load_weights_fallback_and_file(tmp_path):
    assert load_weights(tmp_path / "none.json") == DEFAULT_WEIGHTS
    p = tmp_path / "w.json"
    p.write_text(json.dumps({"weights": {"trend": 0.5, "orderflow": 0.5}}))
    assert load_weights(p) == {"trend": 0.5, "orderflow": 0.5}


def test_runner_publishes_with_default_ttl(tmp_path):
    from botnet.common.runner import run_forever

    class Fake:
        name = "fake"
        interval = 0.01

        def step(self):
            return Signal(bot="fake", score=0.5, confidence=0.5, price=1.0)

    bus = Bus(tmp_path / "s.db")
    run_forever(Fake(), bus=bus, max_steps=2)
    hist = bus.history(bot="fake")
    assert len(hist) == 2 and hist[0].ttl == 120
