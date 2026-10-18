"""
Ortak sinyal veriyolu ("sinir sistemi").

Her sensör bir ``Signal`` yayınlar, ana beyin ve learner okur. Depolama SQLite (WAL modu) -
tek VPS üzerinde birden çok süreç güvenle yazıp okuyabilir.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_DB = os.environ.get("BOTNET_DB", "user_data/botnet/signals.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bot         TEXT    NOT NULL,
    ts          REAL    NOT NULL,
    ttl         REAL    NOT NULL,
    score       REAL    NOT NULL,
    confidence  REAL    NOT NULL,
    price       REAL,
    tags        TEXT    NOT NULL DEFAULT '[]',
    details     TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_signals_bot_ts ON signals (bot, ts);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals (ts);
"""


def clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass
class Signal:
    """
    Tek bir sensör çıktısı.

    score:      -1 (güçlü SHORT) ... 0 (nötr) ... +1 (güçlü LONG)
    confidence: 0 (emin değil) ... 1 (çok emin)
    ttl:        saniye; ts + ttl geçtiyse sinyal bayat sayılır ve beyin tarafından yok sayılır.
                0 verilirse çalıştırıcı (runner) sensör aralığının 2 katını atar.
    """

    bot: str
    score: float
    confidence: float
    price: float | None = None
    tags: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    ttl: float = 0.0

    def __post_init__(self) -> None:
        self.score = clamp(self.score)
        self.confidence = clamp(self.confidence, 0.0, 1.0)

    def is_fresh(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return self.ts + self.ttl >= now

    @property
    def strength(self) -> float:
        return self.score * self.confidence


class Bus:
    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path or DEFAULT_DB)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if str(self.path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def publish(self, signal: Signal) -> None:
        self._conn.execute(
            "INSERT INTO signals (bot, ts, ttl, score, confidence, price, tags, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                signal.bot,
                signal.ts,
                signal.ttl,
                signal.score,
                signal.confidence,
                signal.price,
                json.dumps(signal.tags),
                json.dumps(signal.details, default=str),
            ),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_signal(row: sqlite3.Row) -> Signal:
        return Signal(
            bot=row["bot"],
            score=row["score"],
            confidence=row["confidence"],
            price=row["price"],
            tags=json.loads(row["tags"]),
            details=json.loads(row["details"]),
            ts=row["ts"],
            ttl=row["ttl"],
        )

    def latest(self, now: float | None = None, fresh_only: bool = True) -> dict[str, Signal]:
        """Her bot için en son sinyal. ``fresh_only`` ise bayat olanlar atlanır."""
        rows = self._conn.execute(
            "SELECT s.* FROM signals s "
            "JOIN (SELECT bot, MAX(ts) AS mts FROM signals GROUP BY bot) m "
            "ON s.bot = m.bot AND s.ts = m.mts"
        ).fetchall()
        result = {}
        for row in rows:
            sig = self._row_to_signal(row)
            if not fresh_only or sig.is_fresh(now):
                result[sig.bot] = sig
        return result

    def history(
        self, bot: str | None = None, since: float = 0.0, until: float | None = None
    ) -> list[Signal]:
        query = "SELECT * FROM signals WHERE ts >= ?"
        params: list = [since]
        if until is not None:
            query += " AND ts <= ?"
            params.append(until)
        if bot is not None:
            query += " AND bot = ?"
            params.append(bot)
        query += " ORDER BY ts ASC"
        return [self._row_to_signal(r) for r in self._conn.execute(query, params).fetchall()]

    def bots(self) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT DISTINCT bot FROM signals").fetchall()]

    def prune(self, older_than_days: float = 120) -> int:
        cutoff = time.time() - older_than_days * 86400
        cur = self._conn.execute("DELETE FROM signals WHERE ts < ?", (cutoff,))
        self._conn.commit()
        return cur.rowcount

    def publish_many(self, signals: Iterable[Signal]) -> None:
        for sig in signals:
            self.publish(sig)
