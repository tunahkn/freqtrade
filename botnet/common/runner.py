"""
Sensörleri sonsuz döngüde çalıştıran ortak çalıştırıcı.

Her sensör ``step() -> Signal | None`` sağlar. Hata olursa loglanır, artan bekleme ile
tekrar denenir; süreç ölmez (Docker yine de ``restart: unless-stopped`` ile korur).
"""

from __future__ import annotations

import logging
import os
import signal as _signal
import time
from typing import Protocol

from botnet.common.bus import Bus, Signal


class Sensor(Protocol):
    name: str
    interval: float

    def step(self) -> Signal | None: ...


def setup_logging(name: str) -> logging.Logger:
    logging.basicConfig(
        level=os.environ.get("BOTNET_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    return logging.getLogger(name)


def run_forever(sensor: Sensor, bus: Bus | None = None, max_steps: int | None = None) -> None:
    log = setup_logging(f"botnet.{sensor.name}")
    bus = bus or Bus()
    stop = {"flag": False}

    def _handle(*_):
        stop["flag"] = True

    _signal.signal(_signal.SIGTERM, _handle)
    _signal.signal(_signal.SIGINT, _handle)

    failures = 0
    steps = 0
    log.info("başladı, aralık=%ss db=%s", sensor.interval, bus.path)
    while not stop["flag"]:
        started = time.time()
        try:
            sig = sensor.step()
            if sig is not None:
                if sig.ttl <= 0:
                    sig.ttl = max(2 * sensor.interval, 120)
                bus.publish(sig)
                log.info(
                    "score=%+.2f conf=%.2f tags=%s", sig.score, sig.confidence, ",".join(sig.tags)
                )
            failures = 0
        except Exception:
            failures += 1
            log.exception("adım başarısız (%d. kez)", failures)
        steps += 1
        if max_steps is not None and steps >= max_steps:
            break
        backoff = min(300, sensor.interval * (2 ** min(failures, 4))) if failures else 0
        sleep_for = max(0.0, sensor.interval - (time.time() - started)) + backoff
        # Kısa parçalarla uyu ki SIGTERM'e hızlı cevap verelim
        end = time.time() + sleep_for
        while not stop["flag"] and time.time() < end:
            time.sleep(min(1.0, end - time.time()))
    log.info("durdu")
