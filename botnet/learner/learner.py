"""
learner-bot: kendini geliştirme.

Her gün her sensörün son N gündeki sinyallerini, sinyalden HORIZON sonra fiyatın gittiği
yönle karşılaştırır:
    isabet = sinyal yönü == fiyat yönü (sadece |score| >= 0.2 olanlar)
    kenar  = ortalama(sign(score) * getiri * confidence)

İsabetli botların ağırlığı artar, kötüler azalır. Güvenlik sınırları:
- tek bot en fazla MAX_WEIGHT, en az MIN_WEIGHT
- bir günde en fazla MAX_STEP mutlak değişim
- MIN_SAMPLES'tan az örneği olan bot dokunulmadan kalır
Sonuç weights.json'a yazılır, rapor Telegram'a gönderilir (token varsa).
"""

from __future__ import annotations

import bisect
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from botnet.brain.decision import DEFAULT_WEIGHTS, WEIGHTS_PATH, load_weights
from botnet.common.bus import Bus, Signal
from botnet.common.notify import send_telegram


log = logging.getLogger("botnet.learner")

HORIZON_SEC = float(os.environ.get("BOTNET_LEARN_HORIZON", str(4 * 3600)))
LOOKBACK_DAYS = float(os.environ.get("BOTNET_LEARN_DAYS", "14"))
MIN_SAMPLES = 30
MAX_WEIGHT = 0.35
MIN_WEIGHT = 0.03
MAX_STEP = 0.10


@dataclass
class BotStats:
    bot: str
    samples: int
    hit_rate: float
    edge: float


def price_series(signals: list[Signal]) -> tuple[list[float], list[float]]:
    pts = sorted((s.ts, s.price) for s in signals if s.price)
    return [t for t, _ in pts], [p for _, p in pts]


def price_at(times: list[float], prices: list[float], ts: float, tol: float = 900) -> float | None:
    i = bisect.bisect_left(times, ts)
    best = None
    for j in (i - 1, i):
        if not (0 <= j < len(times)) or abs(times[j] - ts) > tol:
            continue
        if best is None or abs(times[j] - ts) < abs(times[best] - ts):
            best = j
    return prices[best] if best is not None else None


def evaluate(signals: list[Signal], horizon: float = HORIZON_SEC) -> dict[str, BotStats]:
    times, prices = price_series(signals)
    by_bot: dict[str, list[tuple[float, float]]] = {}
    # Aynı bot için ardışık sinyalleri seyrelt (her horizon/4'te bir örnek) - aşırı sayımı önler
    last_ts: dict[str, float] = {}
    for s in sorted(signals, key=lambda x: x.ts):
        if abs(s.score) < 0.2:
            continue
        if s.ts - last_ts.get(s.bot, 0) < horizon / 4:
            continue
        p0 = s.price or price_at(times, prices, s.ts)
        p1 = price_at(times, prices, s.ts + horizon)
        if not p0 or not p1:
            continue
        last_ts[s.bot] = s.ts
        ret = (p1 - p0) / p0
        by_bot.setdefault(s.bot, []).append((s.score * s.confidence, ret))

    stats = {}
    for bot, rows in by_bot.items():
        hits = sum(1 for sc, r in rows if (sc > 0) == (r > 0) and r != 0)
        edge = sum((1 if sc > 0 else -1) * r * abs(sc) for sc, r in rows) / len(rows)
        stats[bot] = BotStats(bot, len(rows), hits / len(rows), edge)
    return stats


def update_weights(current: dict[str, float], stats: dict[str, BotStats]) -> dict[str, float]:
    new = dict(current)
    for bot, st in stats.items():
        if st.samples < MIN_SAMPLES or bot not in new:
            continue
        # %50 isabet = nötr. Her +%10 isabet -> ağırlık * 1.5 hedefi
        factor = 1 + 5 * (st.hit_rate - 0.5)
        target = max(MIN_WEIGHT, min(MAX_WEIGHT, current[bot] * factor))
        step = max(-MAX_STEP, min(MAX_STEP, target - current[bot]))
        new[bot] = current[bot] + step

    # Normalize et (toplam 1), sınırları koruyarak birkaç tur
    for _ in range(10):
        total = sum(new.values())
        new = {b: w / total for b, w in new.items()}
        clipped = {b: max(MIN_WEIGHT, min(MAX_WEIGHT, w)) for b, w in new.items()}
        if clipped == new:
            break
        new = clipped
    return {b: round(w, 4) for b, w in new.items()}


def format_report(stats: dict[str, BotStats], old: dict, new: dict) -> str:
    lines = ["🧠 Günlük öğrenme raporu", f"Ufuk: {HORIZON_SEC / 3600:.0f} saat"]
    for bot in sorted(new, key=lambda b: -new[b]):
        st = stats.get(bot)
        perf = f"isabet %{st.hit_rate * 100:.0f} ({st.samples})" if st else "veri yok"
        lines.append(f"• {bot}: {old.get(bot, 0):.2f} → {new[bot]:.2f} | {perf}")
    return "\n".join(lines)


def run_once(bus: Bus, weights_path: Path = WEIGHTS_PATH) -> dict[str, float]:
    since = time.time() - LOOKBACK_DAYS * 86400
    signals = bus.history(since=since)
    stats = evaluate(signals)
    old = load_weights(weights_path)
    for bot in DEFAULT_WEIGHTS:
        old.setdefault(bot, DEFAULT_WEIGHTS[bot])
    new = update_weights(old, stats)
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = weights_path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "weights": new,
                "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "stats": {b: s.__dict__ for b, s in stats.items()},
            },
            indent=2,
        )
    )
    tmp.replace(weights_path)
    report = format_report(stats, old, new)
    log.info("\n%s", report)
    send_telegram(report)
    bus.prune(older_than_days=120)
    return new


def main() -> None:
    from botnet.common.runner import setup_logging

    setup_logging("botnet.learner")
    bus = Bus()
    interval = float(os.environ.get("BOTNET_LEARN_INTERVAL", "86400"))
    while True:
        try:
            run_once(bus)
        except Exception:
            log.exception("öğrenme turu başarısız")
        time.sleep(interval)


if __name__ == "__main__":
    main()
