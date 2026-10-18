"""
Ana beynin karar mantığı (saf Python, Freqtrade'den bağımsız test edilebilir).

    S = Σ w_i * score_i * confidence_i / Σ w_i   (sadece taze sinyaller)

Sonra teknik çekirdek (trend) ile harmanlanır ve LONG / SHORT / bekle kararı çıkar.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from botnet.common.bus import Signal


WEIGHTS_PATH = Path(os.environ.get("BOTNET_WEIGHTS", "user_data/botnet/weights.json"))

DEFAULT_WEIGHTS: dict[str, float] = {
    "trend": 0.25,
    "orderflow": 0.20,
    "derivatives": 0.18,
    "liquidity": 0.17,
    "orderbook": 0.12,
    "sentiment": 0.08,
}


def load_weights(path: Path = WEIGHTS_PATH) -> dict[str, float]:
    try:
        data = json.loads(Path(path).read_text())
        weights = {k: float(v) for k, v in data.get("weights", data).items()}
        return weights or dict(DEFAULT_WEIGHTS)
    except (FileNotFoundError, ValueError, AttributeError):
        return dict(DEFAULT_WEIGHTS)


@dataclass
class Fusion:
    score: float = 0.0
    coverage: float = 0.0  # taze sinyali olan ağırlık oranı (0..1)
    contributions: dict[str, float] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


def fuse(
    signals: dict[str, Signal],
    weights: dict[str, float] | None = None,
    now: float | None = None,
    exclude: tuple[str, ...] = (),
) -> Fusion:
    weights = weights or DEFAULT_WEIGHTS
    now = time.time() if now is None else now
    total_w = sum(w for b, w in weights.items() if b not in exclude)
    used_w = 0.0
    acc = 0.0
    contrib: dict[str, float] = {}
    tags: list[str] = []
    for bot, sig in signals.items():
        if bot in exclude or bot not in weights or not sig.is_fresh(now):
            continue
        w = weights[bot]
        used_w += w
        acc += w * sig.strength
        contrib[bot] = round(w * sig.strength, 4)
        tags.extend(sig.tags)
    if used_w == 0:
        return Fusion()
    return Fusion(
        score=acc / used_w,
        coverage=used_w / total_w if total_w else 0.0,
        contributions=contrib,
        tags=tags,
    )


@dataclass
class Decision:
    side: str | None  # "long" | "short" | None
    score: float
    tag: str = ""
    reason: dict = field(default_factory=dict)


# Etiketleri kısa entry_tag'e çevirmek için (Freqtrade entry_tag 255 karakter sınırı)
_TAG_SHORT = {
    "whale_buy": "whaleB",
    "whale_sell": "whaleS",
    "bull_absorption": "absB",
    "bear_absorption": "absS",
    "sweep_low": "sweepL",
    "sweep_high": "sweepH",
    "stop_hunt_reversal": "hunt",
    "long_liq_wave": "liqL",
    "short_liq_wave": "liqS",
    "crowd_long_squeeze_risk": "sqzL",
    "crowd_short_squeeze_risk": "sqzS",
    "new_longs": "oiL",
    "new_shorts": "oiS",
    "extreme_fear": "fear",
    "extreme_greed": "greed",
    "bid_wall_pulled": "spoofB",
    "ask_wall_pulled": "spoofA",
}


def decide(
    tech_score: float,
    htf_trend: float,
    fusion: Fusion | None,
    threshold: float = 0.35,
    tech_weight: float = 0.4,
    min_coverage: float = 0.4,
) -> Decision:
    """
    tech_score: teknik çekirdek (-1..1)
    htf_trend:  4 saatlik trend (-1..1); güçlü ters trende karşı işlem açılmaz
    fusion:     sensör füzyonu; yoksa ya da kapsama düşükse sadece teknik çekirdek kullanılır
    """
    sensors_ok = fusion is not None and fusion.coverage >= min_coverage
    if sensors_ok:
        final = tech_weight * tech_score + (1 - tech_weight) * fusion.score
    else:
        final = tech_score

    tags = set(fusion.tags) if sensors_ok else set()
    # Likidite avı + orderflow teyidi: trend filtresini aşabilen dönüş girişi
    reversal_long = "sweep_low" in tags and ({"bull_absorption", "whale_buy"} & tags)
    reversal_short = "sweep_high" in tags and ({"bear_absorption", "whale_sell"} & tags)

    side = None
    kind = ""
    if reversal_long and final > 0:
        side, kind = "long", "rev"
    elif reversal_short and final < 0:
        side, kind = "short", "rev"
    elif final > threshold and htf_trend > -0.4:
        side, kind = "long", "trend"
    elif final < -threshold and htf_trend < 0.4:
        side, kind = "short", "trend"

    if side is None:
        return Decision(side=None, score=final)

    short_tags = sorted({_TAG_SHORT[t] for t in tags if t in _TAG_SHORT})
    tag = f"{'L' if side == 'long' else 'S'}:{kind}:{final:+.2f}"
    if short_tags:
        tag += ":" + "+".join(short_tags)
    return Decision(
        side=side,
        score=final,
        tag=tag[:250],
        reason={
            "tech": round(tech_score, 3),
            "htf": round(htf_trend, 3),
            "fusion": round(fusion.score, 3) if fusion else None,
            "coverage": round(fusion.coverage, 2) if fusion else 0,
            "contrib": fusion.contributions if fusion else {},
        },
    )
