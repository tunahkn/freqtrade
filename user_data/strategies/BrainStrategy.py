"""
BrainStrategy - BTC/USDT ana beyin (OKX Futures, long + short).

İki katman:
1. Teknik çekirdek (her zaman, backtest edilebilir): 15m / 1h / 4h trend skoru.
2. Sensör füzyonu (sadece canlı/dry-run): botnet sensörlerinin signals.db'ye yazdığı
   orderflow, orderbook, derivatives, liquidity, sentiment sinyalleri. Ağırlıklar learner
   botunun güncellediği weights.json'dan okunur.

Karar mantığı: botnet/brain/decision.py
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from math import isnan
from pathlib import Path

from pandas import DataFrame

from freqtrade.enums import RunMode
from freqtrade.persistence import Trade
from freqtrade.strategy import (
    DecimalParameter,
    IStrategy,
    informative,
    stoploss_from_absolute,
)


# botnet paketini bul (repo kökü veya Docker'da /freqtrade)
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from botnet.brain.decision import decide, fuse, load_weights  # noqa: E402
from botnet.common.bus import Bus  # noqa: E402
from botnet.common.indicators import atr, trend_score_frame  # noqa: E402


logger = logging.getLogger(__name__)

MAX_LEVERAGE = 2.0
RISK_PER_TRADE = 0.01  # sermayenin %1'i


class BrainStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short = True
    process_only_new_candles = True
    startup_candle_count = 900  # 4h EMA100 için ~ 150 saat + pay

    # Kaldıraçlı getiri (2x'te %8 = fiyatta %4)
    minimal_roi = {"0": 0.08, "360": 0.04, "1440": 0.015}
    # Kesin taban: 2x'te %6 = fiyatta %3. Asıl stop custom_stoploss (ATR).
    stoploss = -0.06
    use_custom_stoploss = True
    use_exit_signal = True
    exit_profit_only = False

    entry_threshold = DecimalParameter(0.2, 0.6, default=0.35, decimals=2, space="buy")
    tech_weight = DecimalParameter(0.2, 0.8, default=0.4, decimals=2, space="buy")
    atr_mult = DecimalParameter(1.5, 3.5, default=2.0, decimals=1, space="sell")

    _bus: Bus | None = None

    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 2},
            {
                # Art arda 3 stop -> 6 saat dur
                "method": "StoplossGuard",
                "lookback_period_candles": 96,
                "trade_limit": 3,
                "stop_duration_candles": 24,
                "only_per_pair": False,
            },
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 672,
                "trade_limit": 5,
                "stop_duration_candles": 96,
                "max_allowed_drawdown": 0.15,
            },
        ]

    # ------------------------------------------------------------------ indikatörler

    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["tscore"] = trend_score_frame(dataframe)
        # Stop ve pozisyon boyutu 1 saatlik ATR ile: 15m ATR gürültüye fazla yakın
        dataframe["atr"] = atr(dataframe, 14)
        return dataframe

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["tscore"] = trend_score_frame(dataframe)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["tscore"] = trend_score_frame(dataframe)
        dataframe["atr"] = dataframe["atr_1h"]
        dataframe["tech"] = (
            0.25 * dataframe["tscore"]
            + 0.35 * dataframe["tscore_1h"].fillna(0)
            + 0.40 * dataframe["tscore_4h"].fillna(0)
        )
        dataframe["htf"] = dataframe["tscore_4h"].fillna(0)
        return dataframe

    # ------------------------------------------------------------------ sensörler

    def _is_live(self) -> bool:
        return self.dp is not None and self.dp.runmode in (RunMode.LIVE, RunMode.DRY_RUN)

    def _live_decision(self, dataframe: DataFrame):
        if self._bus is None:
            self._bus = Bus()
        signals = self._bus.latest()
        weights = load_weights()
        last = dataframe.iloc[-1]
        trend_sig = signals.get("trend")
        # trend-bot taze ise onun zengin analizini (piyasa yapısı dahil) kullan
        tech = trend_sig.score if trend_sig else float(last["tech"])
        htf = (
            float(trend_sig.details.get("htf_trend", last["htf"]))
            if trend_sig
            else float(last["htf"])
        )
        fusion = fuse(signals, weights, exclude=("trend",))
        return decide(
            tech,
            htf,
            fusion,
            threshold=self.entry_threshold.value,
            tech_weight=self.tech_weight.value,
        )

    # ------------------------------------------------------------------ giriş / çıkış

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        thr = self.entry_threshold.value
        has_vol = dataframe["volume"] > 0
        prev = dataframe["tech"].shift(1)
        # Sadece eşiğin yeni kesildiği mumda gir; trend boyunca her mumda tekrar girme
        long_cond = (dataframe["tech"] > thr) & (prev <= thr) & (dataframe["htf"] > -0.4) & has_vol
        short_cond = (
            (dataframe["tech"] < -thr) & (prev >= -thr) & (dataframe["htf"] < 0.4) & has_vol
        )
        dataframe.loc[long_cond, ["enter_long", "enter_tag"]] = (1, "L:tech")
        dataframe.loc[short_cond, ["enter_short", "enter_tag"]] = (1, "S:tech")

        if self._is_live() and len(dataframe):
            idx = dataframe.index[-1]
            dataframe.loc[idx, ["enter_long", "enter_short"]] = 0
            dataframe.loc[idx, "enter_tag"] = None
            try:
                d = self._live_decision(dataframe)
                dataframe.loc[idx, "brain_score"] = d.score
                if d.side == "long":
                    dataframe.loc[idx, ["enter_long", "enter_tag"]] = (1, d.tag)
                elif d.side == "short":
                    dataframe.loc[idx, ["enter_short", "enter_tag"]] = (1, d.tag)
                if d.side:
                    logger.info("BEYİN KARARI %s %s %s", d.side.upper(), d.tag, d.reason)
            except Exception:  # sensör hatası işlemi durdurmasın, teknik çekirdeğe dön
                logger.exception("Sensör füzyonu başarısız, teknik çekirdek kullanılıyor")
                if long_cond.iloc[-1]:
                    dataframe.loc[idx, ["enter_long", "enter_tag"]] = (1, "L:tech")
                elif short_cond.iloc[-1]:
                    dataframe.loc[idx, ["enter_short", "enter_tag"]] = (1, "S:tech")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        half = self.entry_threshold.value / 2
        score = dataframe["tech"]
        if "brain_score" in dataframe.columns:
            score = dataframe["brain_score"].fillna(dataframe["tech"])
        dataframe.loc[score < -half, "exit_long"] = 1
        dataframe.loc[score > half, "exit_short"] = 1
        return dataframe

    # ------------------------------------------------------------------ risk

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        return min(MAX_LEVERAGE, max_leverage)

    def _last_atr(self, pair: str) -> float | None:
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or df.empty or "atr" not in df.columns:
            return None
        val = df["atr"].iloc[-1]
        return None if isnan(val) else float(val)

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        """Stop mesafesine göre pozisyon: stop olursa kayıp ≈ sermayenin %1'i."""
        a = self._last_atr(pair)
        if not a or not current_rate:
            return min(proposed_stake, max_stake)
        stop_pct = (self.atr_mult.value * a) / current_rate
        equity = self.wallets.get_total_stake_amount() if self.wallets else proposed_stake
        stake = equity * RISK_PER_TRADE / (stop_pct * max(leverage, 1.0))
        if min_stake:
            stake = max(stake, min_stake)
        return min(stake, max_stake)

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        """
        Başlangıçta giriş fiyatından atr_mult x ATR uzakta sabit stop. Fiyat 1 ATR kâra
        geçince stop fiyatı takip eder. Freqtrade stopu sadece sıkılaştırır, asla gevşetmez.
        """
        a = self._last_atr(pair)
        if not a:
            return None
        dist = self.atr_mult.value * a
        gain = (
            (trade.open_rate - current_rate) if trade.is_short else (current_rate - trade.open_rate)
        )
        anchor = current_rate if gain >= a else trade.open_rate
        stop_rate = anchor + dist if trade.is_short else anchor - dist
        return stoploss_from_absolute(
            stop_rate, current_rate, is_short=trade.is_short, leverage=trade.leverage
        )
