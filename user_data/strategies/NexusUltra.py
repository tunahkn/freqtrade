"""
NexusUltra - Advanced Multi-Dimensional Trading Strategy
=========================================================
Next-generation trading strategy combining:
- Smart Money Concepts (Order Blocks, FVG, BOS)
- Adaptive Market Regime Detection
- Multi-Dimensional Confluence Scoring (Nexus Score)
- Volume Intelligence & VWAP Analysis
- TTM Squeeze Momentum
- RSI/MACD Divergence Detection
- Adaptive Parameter Engine (volatility-responsive thresholds)
- Dynamic TP/SL with ATR-based targets

Supports: Spot, Futures (Long & Short), Crypto & Traditional Markets
"""

import logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    BooleanParameter,
    DecimalParameter,
    IStrategy,
    IntParameter,
    informative,
    stoploss_from_open,
)

logger = logging.getLogger(__name__)


class NexusUltra(IStrategy):
    """
    NexusUltra - The most advanced multi-dimensional trading strategy.

    Signal Types:
        AV (Hunt)   -> Enter trade (Long or Short)
        KAPAT (Close) -> Exit trade

    Nexus Score: -100 to +100 confluence score from 5 dimensions:
        1. Trend Score (25%)    - EMA alignment, ADX, slope
        2. Momentum Score (25%) - RSI, MACD, StochRSI, Squeeze
        3. Volume Score (20%)   - Volume ratio, delta, VWAP position
        4. SMC Score (20%)      - Order blocks, FVG, BOS, divergences
        5. Volatility Score (10%) - BB%B, Keltner, ATR regime
    """

    INTERFACE_VERSION = 3

    # --- Core Settings ---
    can_short = True
    timeframe = "5m"
    startup_candle_count = 200
    process_only_new_candles = True

    # --- ROI ---
    minimal_roi = {
        "0": 0.15,
        "30": 0.08,
        "60": 0.05,
        "120": 0.03,
        "240": 0.01,
    }

    # --- Stoploss ---
    stoploss = -0.08
    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    # --- Order Types ---
    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": True,
        "stoploss_on_exchange_interval": 60,
    }

    order_time_in_force = {
        "entry": "GTC",
        "exit": "GTC",
    }

    # ══════════════════════════════════════════════════════════════════
    # HYPEROPT PARAMETERS
    # ══════════════════════════════════════════════════════════════════

    # Sensitivity (1=Aggressive, 5=Conservative)
    sensitivity = IntParameter(1, 5, default=3, space="buy", optimize=True)

    # EMA Lengths
    fast_ema_len = IntParameter(5, 15, default=9, space="buy", optimize=True)
    mid_ema_len = IntParameter(15, 30, default=21, space="buy", optimize=True)
    slow_ema_len = IntParameter(40, 80, default=55, space="buy", optimize=True)
    trend_ema_len = IntParameter(150, 250, default=200, space="buy", optimize=False)

    # RSI
    rsi_length = IntParameter(10, 20, default=14, space="buy", optimize=True)

    # Bollinger Bands
    bb_length = IntParameter(15, 30, default=20, space="buy", optimize=True)
    bb_mult = DecimalParameter(1.5, 3.0, default=2.0, decimals=1, space="buy", optimize=True)

    # Keltner Channel
    kc_length = IntParameter(15, 30, default=20, space="buy", optimize=True)
    kc_mult = DecimalParameter(1.0, 2.5, default=1.5, decimals=1, space="buy", optimize=True)

    # Volume
    vol_ma_len = IntParameter(10, 40, default=20, space="buy", optimize=True)
    vol_spike_multi = DecimalParameter(1.5, 3.5, default=2.0, decimals=1, space="buy", optimize=True)

    # SMC
    smc_length = IntParameter(5, 20, default=10, space="buy", optimize=True)
    fvg_min_size = DecimalParameter(0.05, 0.5, default=0.1, decimals=2, space="buy", optimize=True)

    # Entry/Exit Thresholds (derived from sensitivity but can be optimized)
    nexus_entry_threshold = DecimalParameter(15.0, 60.0, default=35.0, decimals=1, space="buy", optimize=True)
    nexus_exit_threshold = DecimalParameter(-35.0, -10.0, default=-20.0, decimals=1, space="sell", optimize=True)

    # Exit parameters
    exit_rsi_upper = IntParameter(65, 85, default=75, space="sell", optimize=True)
    exit_rsi_lower = IntParameter(15, 35, default=25, space="sell", optimize=True)

    # ══════════════════════════════════════════════════════════════════
    # PLOT CONFIGURATION
    # ══════════════════════════════════════════════════════════════════

    plot_config = {
        "main_plot": {
            "ema_fast": {"color": "#00BCD4"},
            "ema_mid": {"color": "#FF9800"},
            "ema_slow": {"color": "#9C27B0"},
            "ema_trend": {"color": "#607D8B"},
            "bb_upper": {"color": "#E0E0E0"},
            "bb_lower": {"color": "#E0E0E0"},
            "vwap": {"color": "#FFD600"},
        },
        "subplots": {
            "Nexus Score": {
                "nexus_score": {"color": "#00E5FF"},
                "nexus_zero": {"color": "#424242"},
            },
            "RSI": {
                "rsi": {"color": "#FF5252"},
                "rsi_ob": {"color": "#FF174450"},
                "rsi_os": {"color": "#00E67650"},
            },
            "Squeeze Momentum": {
                "sqz_mom": {"color": "#FFD600"},
            },
            "Volume Ratio": {
                "vol_ratio": {"color": "#2196F3"},
            },
        },
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Calculate all indicators for the NexusUltra strategy."""

        # ══════════════════════════════════════════════════════════════
        # BASE INDICATORS
        # ══════════════════════════════════════════════════════════════

        # --- EMA System ---
        for length in [5, 7, 9, 12, 15, 21, 25, 30, 55, 65, 80, 200]:
            dataframe[f"ema_{length}"] = ta.EMA(dataframe, timeperiod=length)

        # --- RSI ---
        for length in [10, 14, 20]:
            dataframe[f"rsi_{length}"] = ta.RSI(dataframe, timeperiod=length)

        # --- MACD ---
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macd"] = macd["macd"]
        dataframe["macd_signal"] = macd["macdsignal"]
        dataframe["macd_hist"] = macd["macdhist"]

        # --- Stochastic RSI ---
        rsi_for_stoch = dataframe[f"rsi_14"]
        dataframe["stoch_k"] = (
            (rsi_for_stoch - rsi_for_stoch.rolling(14).min())
            / (rsi_for_stoch.rolling(14).max() - rsi_for_stoch.rolling(14).min() + 1e-10)
            * 100
        )
        dataframe["stoch_k"] = dataframe["stoch_k"].rolling(3).mean()
        dataframe["stoch_d"] = dataframe["stoch_k"].rolling(3).mean()

        # --- ATR ---
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["atr_percent"] = (dataframe["atr"] / dataframe["close"]) * 100
        dataframe["atr_sma50"] = dataframe["atr_percent"].rolling(50).mean()

        # --- ADX / DI ---
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["di_plus"] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe["di_minus"] = ta.MINUS_DI(dataframe, timeperiod=14)

        # --- Bollinger Bands ---
        for bb_len in [15, 20, 25, 30]:
            for bb_m in [1.5, 2.0, 2.5, 3.0]:
                bb_m_str = str(bb_m).replace(".", "")
                bollinger = self._bollinger_bands(dataframe, bb_len, bb_m)
                dataframe[f"bb_upper_{bb_len}_{bb_m_str}"] = bollinger["upper"]
                dataframe[f"bb_lower_{bb_len}_{bb_m_str}"] = bollinger["lower"]
                dataframe[f"bb_mid_{bb_len}_{bb_m_str}"] = bollinger["mid"]

        # --- Keltner Channel ---
        for kc_len in [15, 20, 25, 30]:
            for kc_m in [1.0, 1.5, 2.0, 2.5]:
                kc_m_str = str(kc_m).replace(".", "")
                kc_basis = ta.EMA(dataframe, timeperiod=kc_len)
                kc_range_val = ta.ATR(dataframe, timeperiod=kc_len) * kc_m
                dataframe[f"kc_upper_{kc_len}_{kc_m_str}"] = kc_basis + kc_range_val
                dataframe[f"kc_lower_{kc_len}_{kc_m_str}"] = kc_basis - kc_range_val

        # --- Volume Analysis ---
        for v_len in [10, 20, 30, 40]:
            dataframe[f"vol_ma_{v_len}"] = dataframe["volume"].rolling(v_len).mean()

        dataframe["vol_delta"] = np.where(
            dataframe["close"] > dataframe["open"],
            dataframe["volume"],
            -dataframe["volume"],
        )
        dataframe["cum_vol_delta"] = dataframe["vol_delta"].rolling(10).mean()

        # --- VWAP (approximation using cumulative within session) ---
        dataframe["vwap"] = self._calculate_vwap(dataframe)

        # ══════════════════════════════════════════════════════════════
        # DERIVED / COMPOSITE INDICATORS
        # ══════════════════════════════════════════════════════════════

        # Will be computed dynamically per hyperopt param combo in signal methods
        # Pre-compute some common ones for plotting
        dataframe["ema_fast"] = dataframe["ema_9"]
        dataframe["ema_mid"] = dataframe["ema_21"]
        dataframe["ema_slow"] = dataframe["ema_55"]
        dataframe["ema_trend"] = dataframe["ema_200"]
        dataframe["rsi"] = dataframe["rsi_14"]
        dataframe["bb_upper"] = dataframe["bb_upper_20_20"]
        dataframe["bb_lower"] = dataframe["bb_lower_20_20"]

        # BB Width & %B
        dataframe["bb_width"] = (
            (dataframe["bb_upper"] - dataframe["bb_lower"]) / dataframe["bb_mid_20_20"] * 100
        )
        bb_range = dataframe["bb_upper"] - dataframe["bb_lower"]
        dataframe["bb_pctb"] = (dataframe["close"] - dataframe["bb_lower"]) / (bb_range + 1e-10)

        # --- Squeeze Detection ---
        dataframe["sqz_on"] = (dataframe["bb_lower_20_20"] > dataframe["kc_lower_20_15"]) & (
            dataframe["bb_upper_20_20"] < dataframe["kc_upper_20_15"]
        )

        # Squeeze Momentum (linear regression of price deviation)
        hl_avg = (
            dataframe["high"].rolling(20).max() + dataframe["low"].rolling(20).min()
        ) / 2
        kc_basis = ta.EMA(dataframe, timeperiod=20)
        mid_val = (hl_avg + kc_basis) / 2
        dataframe["sqz_mom"] = self._linreg(dataframe["close"] - mid_val, 20)

        # --- Swing High / Low for SMC ---
        for smc_len in [5, 10, 15, 20]:
            dataframe[f"swing_high_{smc_len}"] = (
                dataframe["high"]
                .rolling(window=smc_len * 2 + 1, center=True)
                .max()
            )
            dataframe[f"swing_low_{smc_len}"] = (
                dataframe["low"]
                .rolling(window=smc_len * 2 + 1, center=True)
                .min()
            )

        # --- Divergence pre-computation ---
        dataframe["price_low_5"] = dataframe["low"].rolling(5).min()
        dataframe["price_high_5"] = dataframe["high"].rolling(5).max()
        dataframe["rsi_low_5"] = dataframe["rsi"].rolling(5).min()
        dataframe["rsi_high_5"] = dataframe["rsi"].rolling(5).max()

        # Nexus Score & Volume Ratio for plotting
        dataframe["nexus_score"] = self._compute_nexus_score(dataframe)
        dataframe["nexus_zero"] = 0

        vol_ma = dataframe[f"vol_ma_20"]
        dataframe["vol_ratio"] = dataframe["volume"] / (vol_ma + 1e-10)

        # RSI lines for plotting
        dataframe["rsi_ob"] = 70
        dataframe["rsi_os"] = 30

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Generate AV (Hunt) entry signals based on Nexus Score confluence."""

        # Get hyperopt parameter values
        fast_len = self.fast_ema_len.value
        mid_len = self.mid_ema_len.value
        slow_len = self.slow_ema_len.value
        trend_len = self.trend_ema_len.value
        rsi_len = self.rsi_length.value
        bb_len = self.bb_length.value
        bb_m = self.bb_mult.value
        kc_len = self.kc_length.value
        kc_m = self.kc_mult.value
        vol_len = self.vol_ma_len.value
        smc_len = self.smc_length.value
        entry_thresh = self.nexus_entry_threshold.value
        sens = self.sensitivity.value

        # Dynamic column lookups
        ema_fast = dataframe[f"ema_{fast_len}"]
        ema_mid = dataframe[f"ema_{mid_len}"]
        ema_slow = dataframe[f"ema_{slow_len}"]
        ema_trend = dataframe[f"ema_{trend_len}"]
        rsi = dataframe[f"rsi_{rsi_len}"]
        vol_ma = dataframe[f"vol_ma_{vol_len}"]

        # Compute full Nexus Score with current hyperopt params
        nexus = self._compute_nexus_score_parametric(
            dataframe, fast_len, mid_len, slow_len, trend_len, rsi_len,
            bb_len, bb_m, kc_len, kc_m, vol_len, smc_len,
        )
        nexus_smooth = nexus.ewm(span=3).mean()

        # Trend Score (simplified for entry check)
        trend_score = self._compute_trend_score(dataframe, fast_len, mid_len, slow_len, trend_len)

        # Momentum Score
        mom_score = self._compute_momentum_score(dataframe, rsi_len, bb_len, bb_m, kc_len, kc_m)

        # SMC Score
        smc_score = self._compute_smc_score(dataframe, smc_len)

        # Min bars between signals to prevent spam
        min_bars = sens * 3

        # ── LONG AV ──
        long_conditions = (
            (nexus_smooth > entry_thresh)
            & (nexus_smooth > nexus_smooth.shift(1))
            & (trend_score > 0)
            & ((mom_score > 0) | (smc_score > 20))
            & (dataframe["volume"] > vol_ma * 0.5)
            & (dataframe["volume"] > 0)
        )

        # Anti-spam: Ensure minimum bars between signals
        long_conditions = self._apply_min_bars_filter(long_conditions, min_bars)

        dataframe.loc[long_conditions, "enter_long"] = 1
        dataframe.loc[long_conditions, "enter_tag"] = "nexus_av_long"

        # ── SHORT AV ──
        short_conditions = (
            (nexus_smooth < -entry_thresh)
            & (nexus_smooth < nexus_smooth.shift(1))
            & (trend_score < 0)
            & ((mom_score < 0) | (smc_score < -20))
            & (dataframe["volume"] > vol_ma * 0.5)
            & (dataframe["volume"] > 0)
        )

        short_conditions = self._apply_min_bars_filter(short_conditions, min_bars)

        dataframe.loc[short_conditions, "enter_short"] = 1
        dataframe.loc[short_conditions, "enter_tag"] = "nexus_av_short"

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Generate KAPAT (Close) exit signals."""

        rsi_len = self.rsi_length.value
        fast_len = self.fast_ema_len.value
        mid_len = self.mid_ema_len.value
        slow_len = self.slow_ema_len.value
        trend_len = self.trend_ema_len.value
        bb_len = self.bb_length.value
        bb_m = self.bb_mult.value
        kc_len = self.kc_length.value
        kc_m = self.kc_mult.value
        vol_len = self.vol_ma_len.value
        smc_len = self.smc_length.value
        exit_thresh = self.nexus_exit_threshold.value
        sens = self.sensitivity.value

        rsi = dataframe[f"rsi_{rsi_len}"]

        nexus = self._compute_nexus_score_parametric(
            dataframe, fast_len, mid_len, slow_len, trend_len, rsi_len,
            bb_len, bb_m, kc_len, kc_m, vol_len, smc_len,
        )
        nexus_smooth = nexus.ewm(span=3).mean()

        # Adaptive RSI thresholds based on volatility regime
        atr_pct = dataframe["atr_percent"]
        atr_avg = dataframe["atr_sma50"]
        vol_regime_high = atr_pct > atr_avg * 1.5
        vol_regime_low = atr_pct < atr_avg * 0.5

        adaptive_rsi_ob = pd.Series(self.exit_rsi_upper.value, index=dataframe.index, dtype=float)
        adaptive_rsi_ob = adaptive_rsi_ob.where(~vol_regime_high, self.exit_rsi_upper.value + 5)
        adaptive_rsi_ob = adaptive_rsi_ob.where(~vol_regime_low, self.exit_rsi_upper.value - 5)

        adaptive_rsi_os = pd.Series(self.exit_rsi_lower.value, index=dataframe.index, dtype=float)
        adaptive_rsi_os = adaptive_rsi_os.where(~vol_regime_high, self.exit_rsi_lower.value - 5)
        adaptive_rsi_os = adaptive_rsi_os.where(~vol_regime_low, self.exit_rsi_lower.value + 5)

        # Divergence detection
        bull_div = (
            (dataframe["low"] <= dataframe["price_low_5"])
            & (rsi > dataframe["rsi_low_5"])
            & (rsi < 40)
        )
        bear_div = (
            (dataframe["high"] >= dataframe["price_high_5"])
            & (rsi < dataframe["rsi_high_5"])
            & (rsi > 60)
        )

        min_bars = sens * 3

        # ── LONG KAPAT ──
        long_exit = (
            (nexus_smooth < exit_thresh)
            & (nexus_smooth < nexus_smooth.shift(1))
            & ((rsi > adaptive_rsi_ob) | bear_div)
            & (dataframe["volume"] > 0)
        )
        long_exit = self._apply_min_bars_filter(long_exit, min_bars)

        dataframe.loc[long_exit, "exit_long"] = 1
        dataframe.loc[long_exit, "exit_tag"] = "nexus_kapat_long"

        # ── SHORT KAPAT ──
        short_exit = (
            (nexus_smooth > abs(exit_thresh))
            & (nexus_smooth > nexus_smooth.shift(1))
            & ((rsi < adaptive_rsi_os) | bull_div)
            & (dataframe["volume"] > 0)
        )
        short_exit = self._apply_min_bars_filter(short_exit, min_bars)

        dataframe.loc[short_exit, "exit_short"] = 1
        dataframe.loc[short_exit, "exit_tag"] = "nexus_kapat_short"

        return dataframe

    # ══════════════════════════════════════════════════════════════════
    # CUSTOM CALLBACKS
    # ══════════════════════════════════════════════════════════════════

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float:
        """ATR-based adaptive stoploss."""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) < 1:
            return self.stoploss

        last_candle = dataframe.iloc[-1]
        atr = last_candle.get("atr", 0)
        if atr == 0:
            return self.stoploss

        atr_multiplier = 1.5 if self.sensitivity.value <= 2 else 2.0 if self.sensitivity.value <= 4 else 2.5

        if trade.is_short:
            sl_price = current_rate + atr * atr_multiplier
            sl_pct = (current_rate - sl_price) / current_rate
        else:
            sl_price = current_rate - atr * atr_multiplier
            sl_pct = (sl_price - current_rate) / current_rate

        # Progressive tightening in profit
        if current_profit > 0.05:
            sl_pct = max(sl_pct, -0.02)
        elif current_profit > 0.03:
            sl_pct = max(sl_pct, -0.04)

        return sl_pct

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> Optional[str]:
        """Custom exit logic using Nexus Score."""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) < 1:
            return None

        last_candle = dataframe.iloc[-1]
        nexus = last_candle.get("nexus_score", 0)

        # Emergency exit on extreme score reversal
        if not trade.is_short and nexus < -60:
            return "nexus_emergency_exit_long"
        if trade.is_short and nexus > 60:
            return "nexus_emergency_exit_short"

        # Take profit at high confidence reversal
        if not trade.is_short and current_profit > 0.02 and nexus < -30:
            return "nexus_tp_reversal_long"
        if trade.is_short and current_profit > 0.02 and nexus > 30:
            return "nexus_tp_reversal_short"

        return None

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> float:
        """Dynamic leverage based on signal confidence."""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) < 1:
            return 1.0

        last_candle = dataframe.iloc[-1]
        nexus = abs(last_candle.get("nexus_score", 0))

        if nexus > 70:
            return min(3.0, max_leverage)
        elif nexus > 50:
            return min(2.0, max_leverage)
        else:
            return 1.0

    # ══════════════════════════════════════════════════════════════════
    # NEXUS SCORE COMPUTATION ENGINE
    # ══════════════════════════════════════════════════════════════════

    def _compute_nexus_score(self, dataframe: DataFrame) -> pd.Series:
        """Compute Nexus Score with default plotting parameters."""
        return self._compute_nexus_score_parametric(
            dataframe, 9, 21, 55, 200, 14, 20, 2.0, 20, 1.5, 20, 10,
        )

    def _compute_nexus_score_parametric(
        self, df: DataFrame,
        fast_len: int, mid_len: int, slow_len: int, trend_len: int,
        rsi_len: int, bb_len: int, bb_m: float,
        kc_len: int, kc_m: float, vol_len: int, smc_len: int,
    ) -> pd.Series:
        """Compute the full multi-dimensional Nexus Score."""
        trend = self._compute_trend_score(df, fast_len, mid_len, slow_len, trend_len)
        momentum = self._compute_momentum_score(df, rsi_len, bb_len, bb_m, kc_len, kc_m)
        volume = self._compute_volume_score(df, vol_len)
        smc = self._compute_smc_score(df, smc_len)
        volatility = self._compute_volatility_score(df, bb_len, bb_m, kc_len, kc_m)

        nexus = (trend * 0.25) + (momentum * 0.25) + (volume * 0.20) + (smc * 0.20) + (volatility * 0.10)
        return nexus.clip(-100, 100)

    def _compute_trend_score(
        self, df: DataFrame,
        fast_len: int, mid_len: int, slow_len: int, trend_len: int,
    ) -> pd.Series:
        """Trend dimension: EMA alignment, slope, position, ADX."""
        ema_f = df[f"ema_{fast_len}"]
        ema_m = df[f"ema_{mid_len}"]
        ema_s = df[f"ema_{slow_len}"]
        ema_t = df[f"ema_{trend_len}"]

        score = pd.Series(0.0, index=df.index)
        score += np.where(ema_f > ema_m, 20.0, -20.0)
        score += np.where(ema_m > ema_s, 15.0, -15.0)
        score += np.where(ema_s > ema_t, 15.0, -15.0)
        score += np.where(ema_m.diff(5) > 0, 15.0, -15.0)
        score += np.where(df["close"] > ema_t, 15.0, -15.0)
        score += np.where(
            df["adx"] > 25,
            np.where(df["di_plus"] > df["di_minus"], 20.0, -20.0),
            0.0,
        )
        return score.clip(-100, 100)

    def _compute_momentum_score(
        self, df: DataFrame, rsi_len: int,
        bb_len: int, bb_m: float, kc_len: int, kc_m: float,
    ) -> pd.Series:
        """Momentum dimension: RSI, MACD, StochRSI, Squeeze."""
        rsi = df[f"rsi_{rsi_len}"]
        score = pd.Series(0.0, index=df.index)

        # RSI contribution
        score += np.where(rsi > 55, np.minimum(25.0, (rsi - 50) * 1.0), 0.0)
        score += np.where(rsi < 45, np.maximum(-25.0, (rsi - 50) * 1.0), 0.0)

        # MACD
        score += np.where(df["macd"] > df["macd_signal"], 20.0, -20.0)
        score += np.where(
            (df["macd_hist"] > 0) & (df["macd_hist"] > df["macd_hist"].shift(1)),
            15.0,
            np.where(
                (df["macd_hist"] < 0) & (df["macd_hist"] < df["macd_hist"].shift(1)),
                -15.0,
                0.0,
            ),
        )

        # Stochastic RSI
        score += np.where(
            (df["stoch_k"] > df["stoch_d"]) & (df["stoch_k"] < 80), 15.0,
            np.where((df["stoch_k"] < df["stoch_d"]) & (df["stoch_k"] > 20), -15.0, 0.0),
        )

        # Squeeze Momentum
        bb_m_str = str(bb_m).replace(".", "")
        kc_m_str = str(kc_m).replace(".", "")
        bb_l_key = f"bb_lower_{bb_len}_{bb_m_str}"
        bb_u_key = f"bb_upper_{bb_len}_{bb_m_str}"
        kc_l_key = f"kc_lower_{kc_len}_{kc_m_str}"
        kc_u_key = f"kc_upper_{kc_len}_{kc_m_str}"

        if all(k in df.columns for k in [bb_l_key, bb_u_key, kc_l_key, kc_u_key]):
            sqz = (df[bb_l_key] > df[kc_l_key]) & (df[bb_u_key] < df[kc_u_key])
            sqz_weight = np.where(sqz, 1.5, 1.0)
            sqz_mom_rising = df["sqz_mom"] > df["sqz_mom"].shift(1)
            score += np.where(sqz, np.where(sqz_mom_rising, 25.0 * sqz_weight, -25.0 * sqz_weight), 0.0)

        return score.clip(-100, 100)

    def _compute_volume_score(self, df: DataFrame, vol_len: int) -> pd.Series:
        """Volume dimension: ratio, delta, VWAP, spike."""
        vol_ma = df[f"vol_ma_{vol_len}"]
        vol_ratio = df["volume"] / (vol_ma + 1e-10)
        bullish_candle = df["close"] > df["open"]

        score = pd.Series(0.0, index=df.index)

        # Volume ratio
        score += np.where(
            vol_ratio > 1.5,
            np.where(bullish_candle, 30.0, -30.0),
            np.where(vol_ratio > 1.0, np.where(bullish_candle, 15.0, -15.0), 0.0),
        )

        # Cumulative volume delta
        score += np.where(df["cum_vol_delta"] > 0, 25.0, -25.0)

        # VWAP position
        score += np.where(df["close"] > df["vwap"], 25.0, -25.0)

        # Volume spike
        spike = vol_ratio >= self.vol_spike_multi.value
        score += np.where(spike, np.where(bullish_candle, 20.0, -20.0), 0.0)

        return score.clip(-100, 100)

    def _compute_smc_score(self, df: DataFrame, smc_len: int) -> pd.Series:
        """Smart Money Concepts dimension: OB, FVG, BOS, divergence."""
        vol_ma = df[f"vol_ma_20"]
        score = pd.Series(0.0, index=df.index)

        # Order Blocks
        bull_ob = (
            (df["close"].shift(1) < df["open"].shift(1))
            & (df["close"] > df["open"])
            & (df["close"] > df["high"].shift(1))
            & (df["volume"] > vol_ma)
        )
        bear_ob = (
            (df["close"].shift(1) > df["open"].shift(1))
            & (df["close"] < df["open"])
            & (df["close"] < df["low"].shift(1))
            & (df["volume"] > vol_ma)
        )
        score += np.where(bull_ob, 25.0, np.where(bear_ob, -25.0, 0.0))

        # Fair Value Gaps
        fvg_min = self.fvg_min_size.value
        fvg_bull = (df["low"] > df["high"].shift(2)) & (
            (df["low"] - df["high"].shift(2)) / df["close"] * 100 >= fvg_min
        )
        fvg_bear = (df["high"] < df["low"].shift(2)) & (
            (df["low"].shift(2) - df["high"]) / df["close"] * 100 >= fvg_min
        )
        score += np.where(fvg_bull, 20.0, np.where(fvg_bear, -20.0, 0.0))

        # Break of Structure
        swing_high_key = f"swing_high_{smc_len}"
        swing_low_key = f"swing_low_{smc_len}"
        if swing_high_key in df.columns and swing_low_key in df.columns:
            bos_up = (df["close"] > df[swing_high_key].shift(1)) & (
                df["close"].shift(1) <= df[swing_high_key].shift(2)
            )
            bos_down = (df["close"] < df[swing_low_key].shift(1)) & (
                df["close"].shift(1) >= df[swing_low_key].shift(2)
            )
            score += np.where(bos_up, 30.0, np.where(bos_down, -30.0, 0.0))

        # Divergence
        rsi = df["rsi_14"]
        bull_div = (
            (df["low"] <= df["price_low_5"])
            & (rsi > df["rsi_low_5"])
            & (rsi < 40)
        )
        bear_div = (
            (df["high"] >= df["price_high_5"])
            & (rsi < df["rsi_high_5"])
            & (rsi > 60)
        )
        score += np.where(bull_div, 25.0, np.where(bear_div, -25.0, 0.0))

        return score.clip(-100, 100)

    def _compute_volatility_score(
        self, df: DataFrame, bb_len: int, bb_m: float, kc_len: int, kc_m: float,
    ) -> pd.Series:
        """Volatility dimension: BB%B, squeeze, ATR regime, Keltner."""
        score = pd.Series(0.0, index=df.index)

        # BB %B
        score += np.where(df["bb_pctb"] > 0.8, -20.0, np.where(df["bb_pctb"] < 0.2, 20.0, 0.0))

        # Squeeze
        bb_m_str = str(bb_m).replace(".", "")
        kc_m_str = str(kc_m).replace(".", "")
        bb_l = f"bb_lower_{bb_len}_{bb_m_str}"
        bb_u = f"bb_upper_{bb_len}_{bb_m_str}"
        kc_l = f"kc_lower_{kc_len}_{kc_m_str}"
        kc_u = f"kc_upper_{kc_len}_{kc_m_str}"

        if all(k in df.columns for k in [bb_l, bb_u, kc_l, kc_u]):
            sqz = (df[bb_l] > df[kc_l]) & (df[bb_u] < df[kc_u])
            score += np.where(sqz, 30.0, -10.0)

        # ATR regime
        atr_high = df["atr_percent"] > df["atr_sma50"] * 1.5
        atr_low = df["atr_percent"] < df["atr_sma50"] * 0.5
        score += np.where(atr_high, -20.0, np.where(atr_low, 20.0, 0.0))

        # Keltner position
        if kc_u in df.columns and kc_l in df.columns:
            score += np.where(
                df["close"] > df[kc_u], -15.0,
                np.where(df["close"] < df[kc_l], 15.0, 0.0),
            )

        return score.clip(-100, 100)

    # ══════════════════════════════════════════════════════════════════
    # UTILITY METHODS
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _bollinger_bands(df: DataFrame, length: int, mult: float) -> dict:
        """Calculate Bollinger Bands."""
        mid = df["close"].rolling(length).mean()
        std = df["close"].rolling(length).std()
        return {
            "upper": mid + std * mult,
            "mid": mid,
            "lower": mid - std * mult,
        }

    @staticmethod
    def _calculate_vwap(df: DataFrame) -> pd.Series:
        """Approximate VWAP calculation using rolling window."""
        typical = (df["high"] + df["low"] + df["close"]) / 3
        vol = df["volume"]
        cumvol = vol.rolling(window=50, min_periods=1).sum()
        cumtpv = (typical * vol).rolling(window=50, min_periods=1).sum()
        return cumtpv / (cumvol + 1e-10)

    @staticmethod
    def _linreg(series: pd.Series, length: int) -> pd.Series:
        """Linear regression value (simplified)."""
        result = pd.Series(0.0, index=series.index)
        for i in range(length, len(series)):
            y = series.iloc[i - length + 1 : i + 1].values
            if len(y) == length:
                x = np.arange(length)
                if np.std(y) > 0:
                    slope = np.polyfit(x, y, 1)[0]
                    result.iloc[i] = y[-1] + slope
                else:
                    result.iloc[i] = y[-1]
        return result

    @staticmethod
    def _apply_min_bars_filter(conditions: pd.Series, min_bars: int) -> pd.Series:
        """Prevent signal spam by enforcing minimum bars between signals."""
        result = conditions.copy()
        last_signal_idx = -min_bars - 1
        for i in range(len(result)):
            if result.iloc[i]:
                if (i - last_signal_idx) <= min_bars:
                    result.iloc[i] = False
                else:
                    last_signal_idx = i
        return result
