# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# =============================================================================
# TrendMomentumPro — Freqtrade Strategy v1.0
# Pine Script indikatörümüzün Python/Freqtrade karşılığı
#
# Sinyal Mantığı (3 katman):
#   1. Hull MA (HMA)        → Trend yönü filtresi
#   2. WaveTrend Osilatörü  → Momentum / aşırı alım-satım teyidi
#   3. OBV + Hacim Artışı   → Likidite / sahte kırılım filtresi
#
# Risk Yönetimi:
#   • ATR tabanlı stoploss  (custom_stoploss)
#   • ATR tabanlı take-profit ROI tablosu
# =============================================================================
import numpy as np
import pandas as pd
from pandas import DataFrame
from functools import reduce

from freqtrade.strategy import (
    IStrategy,
    DecimalParameter,
    IntParameter,
    BooleanParameter,
    stoploss_from_absolute,
)
import talib.abstract as ta


class TrendMomentumPro(IStrategy):
    """
    TrendMomentumPro — HMA + WaveTrend + OBV Hacim Filtresi

    Önerilen zaman dilimleri : 4h (birincil), 1d (teyit), 1h (aktif trading)
    Önerilen varlıklar       : BTC/USDT, ETH/USDT, EUR/USD, SPY vb. yüksek likidite
    """

    # ─────────────────────────────────────────────────────────────────────────
    # TEMEL STRATEJİ AYARLARI
    # ─────────────────────────────────────────────────────────────────────────
    INTERFACE_VERSION = 3

    # Birincil zaman dilimi
    timeframe = "4h"

    # ROI tablosu — ATR TP çarpanı (~3.5×) için temsili değerler
    # Gerçek backtest sonuçlarına göre optimize edilmeli
    minimal_roi = {
        "0":   0.15,   # %15 anlık çıkış (çok güçlü hareket)
        "120": 0.08,   # 2 bar sonra %8
        "360": 0.04,   # 6 bar sonra %4
        "720": 0.02,   # 12 bar sonra %2 (zamana karşı güvenlik ağı)
    }

    # Sabit stoploss (custom_stoploss ATR versiyonunu ezecek)
    stoploss = -0.08

    # Trailing stop
    trailing_stop             = False
    trailing_only_offset_is_reached = False

    # İşlem tipine izin ver
    can_short = False   # Spot için False; Futures için True yapılabilir

    # Sinyal sayısı
    process_only_new_candles = True

    # ─────────────────────────────────────────────────────────────────────────
    # HYPEROPTİMİZASYON PARAMETRELERİ
    # ─────────────────────────────────────────────────────────────────────────

    # ── Hull MA ──────────────────────────────────────────────────────────────
    hma_period   = IntParameter(20, 100, default=55,  space="buy",  optimize=True)

    # ── WaveTrend ────────────────────────────────────────────────────────────
    wt_ch_len    = IntParameter(5,  20,  default=10,  space="buy",  optimize=True)
    wt_avg_len   = IntParameter(10, 40,  default=21,  space="buy",  optimize=True)
    wt_ob        = IntParameter(40, 70,  default=53,  space="buy",  optimize=False)
    wt_os        = IntParameter(-70,-30, default=-53, space="buy",  optimize=False)

    # ── Hacim Filtresi ────────────────────────────────────────────────────────
    vol_ma_len   = IntParameter(10, 50,  default=20,  space="buy",  optimize=True)
    vol_factor   = DecimalParameter(1.0, 2.0, default=1.2, decimals=1, space="buy", optimize=True)
    use_vol_filter = BooleanParameter(default=True, space="buy", optimize=False)

    # ── ATR Risk ──────────────────────────────────────────────────────────────
    atr_period   = IntParameter(7, 21,   default=14,  space="sell", optimize=True)
    sl_mult      = DecimalParameter(1.0, 3.0, default=1.8, decimals=1, space="sell", optimize=True)
    tp_mult      = DecimalParameter(2.0, 6.0, default=3.5, decimals=1, space="sell", optimize=True)

    # ─────────────────────────────────────────────────────────────────────────
    # YARDIMCI FONKSİYONLAR
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _wma(series: pd.Series, period: int) -> pd.Series:
        """Ağırlıklı Hareketli Ortalama (Weighted Moving Average)."""
        weights = np.arange(1, period + 1, dtype=float)
        return series.rolling(period).apply(
            lambda x: np.dot(x, weights) / weights.sum(), raw=True
        )

    def _hma(self, series: pd.Series, period: int) -> pd.Series:
        """Hull Moving Average: WMA(2×WMA(n/2) − WMA(n), √n)"""
        half   = max(int(period / 2), 1)
        sqrtn  = max(int(np.sqrt(period)), 1)
        raw    = 2.0 * self._wma(series, half) - self._wma(series, period)
        return self._wma(raw, sqrtn)

    @staticmethod
    def _wavetrend(hlc3: pd.Series, ch_len: int, avg_len: int) -> tuple[pd.Series, pd.Series]:
        """
        WaveTrend Osilatörü (LazyBear algoritması)
        Döndürür: (wt1, wt2)
        """
        esa  = hlc3.ewm(span=ch_len, adjust=False).mean()
        d    = (hlc3 - esa).abs().ewm(span=ch_len, adjust=False).mean()
        # Sıfır bölünmeyi önle
        ci   = (hlc3 - esa) / (0.015 * d.replace(0, np.nan)).fillna(0)
        wt1  = ci.ewm(span=avg_len, adjust=False).mean()
        wt2  = wt1.rolling(4).mean()
        return wt1, wt2

    # ─────────────────────────────────────────────────────────────────────────
    # POPULATE INDICATORS
    # ─────────────────────────────────────────────────────────────────────────

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # ── Hull MA ───────────────────────────────────────────────────────────
        dataframe["hma"] = self._hma(dataframe["close"], self.hma_period.value)
        dataframe["hma_up"] = dataframe["hma"] > dataframe["hma"].shift(1)

        # ── WaveTrend ─────────────────────────────────────────────────────────
        hlc3 = (dataframe["high"] + dataframe["low"] + dataframe["close"]) / 3.0
        wt1, wt2 = self._wavetrend(hlc3, self.wt_ch_len.value, self.wt_avg_len.value)
        dataframe["wt1"] = wt1
        dataframe["wt2"] = wt2

        # WaveTrend çapraz geçişleri
        dataframe["wt_cross_up"]   = (dataframe["wt1"] > dataframe["wt2"]) & \
                                     (dataframe["wt1"].shift(1) <= dataframe["wt2"].shift(1))
        dataframe["wt_cross_down"] = (dataframe["wt1"] < dataframe["wt2"]) & \
                                     (dataframe["wt1"].shift(1) >= dataframe["wt2"].shift(1))

        # ── OBV + Hacim Filtresi ──────────────────────────────────────────────
        dataframe["obv"]    = ta.OBV(dataframe)
        dataframe["obv_ma"] = dataframe["obv"].ewm(span=self.vol_ma_len.value, adjust=False).mean()
        dataframe["vol_ma"] = dataframe["volume"].rolling(self.vol_ma_len.value).mean()

        # OBV yukarı yönlü + hacim patlaması
        dataframe["obv_bull"]  = dataframe["obv"] > dataframe["obv_ma"]
        dataframe["obv_bear"]  = dataframe["obv"] < dataframe["obv_ma"]
        dataframe["vol_surge"] = dataframe["volume"] > dataframe["vol_ma"] * self.vol_factor.value

        # ── ATR ───────────────────────────────────────────────────────────────
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.atr_period.value)

        # ATR tabanlı Stop-Loss / Take-Profit seviyeleri (bilgi amaçlı)
        dataframe["sl_long"]  = dataframe["close"] - self.sl_mult.value * dataframe["atr"]
        dataframe["tp_long"]  = dataframe["close"] + self.tp_mult.value * dataframe["atr"]
        dataframe["sl_short"] = dataframe["close"] + self.sl_mult.value * dataframe["atr"]
        dataframe["tp_short"] = dataframe["close"] - self.tp_mult.value * dataframe["atr"]

        return dataframe

    # ─────────────────────────────────────────────────────────────────────────
    # ENTRY (AL) SİNYALİ
    # Koşullar (3 katman hizalanmalı):
    #   1. HMA yukarı yönlü
    #   2. WaveTrend aşırı satım bölgesinden yukarı kesiyor
    #   3. OBV yükseliş + hacim artışı (filtre aktifse)
    # ─────────────────────────────────────────────────────────────────────────

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # Hacim filtresi koşulu (ayara bağlı)
        vol_ok_bull = (
            dataframe["obv_bull"] & dataframe["vol_surge"]
            if self.use_vol_filter.value
            else pd.Series(True, index=dataframe.index)
        )

        conditions_long = [
            dataframe["hma_up"],                              # Katman 1: Trend
            dataframe["wt_cross_up"],                         # Katman 2: Momentum
            dataframe["wt1"] < self.wt_os.value,              # Aşırı satım bölgesi
            vol_ok_bull,                                      # Katman 3: Likidite
            dataframe["volume"] > 0,
        ]

        dataframe.loc[
            reduce(lambda a, b: a & b, conditions_long),
            ["enter_long", "enter_tag"]
        ] = (1, "hma_wt_obv_long")

        return dataframe

    # ─────────────────────────────────────────────────────────────────────────
    # EXIT (KAP) SİNYALİ
    # Koşullar (herhangi biri yeterli):
    #   a. HMA aşağı döndü (trend sona erdi)
    #   b. WaveTrend aşırı alım bölgesinden aşağı kesiyor
    # ─────────────────────────────────────────────────────────────────────────

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        conditions_exit = [
            ~dataframe["hma_up"] |                            # (a) Trend dönüşü
            (
                dataframe["wt_cross_down"] &                  # (b) Momentum dönüşü
                (dataframe["wt1"] > self.wt_ob.value)
            ),
            dataframe["volume"] > 0,
        ]

        dataframe.loc[
            reduce(lambda a, b: a & b, conditions_exit),
            ["exit_long", "exit_tag"]
        ] = (1, "hma_wt_exit")

        return dataframe

    # ─────────────────────────────────────────────────────────────────────────
    # ATR TABANLI DİNAMİK STOPLOSS
    # Giriş anındaki ATR değerinden hesaplanır; bar kapandıkça güncellenmez
    # (trailing_stop=False olduğu için giriş stoplossu korunur)
    # ─────────────────────────────────────────────────────────────────────────

    def custom_stoploss(
        self,
        pair: str,
        trade,
        current_time,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float:
        """
        ATR tabanlı dinamik stoploss.
        Giriş barındaki ATR'a göre hesaplanır; yüzdeden bağımsız, fiyat bazlı.
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return self.stoploss

        last_candle = dataframe.iloc[-1]
        atr_value   = last_candle.get("atr", 0)

        if atr_value and atr_value > 0:
            sl_distance = self.sl_mult.value * atr_value
            # stoploss_from_absolute: mutlak fiyat farkını yüzdeye çevirir
            return stoploss_from_absolute(
                stoploss    = trade.open_rate - sl_distance,
                current_rate = current_rate,
                is_short     = trade.is_short,
                leverage     = trade.leverage,
            )

        return self.stoploss

    # ─────────────────────────────────────────────────────────────────────────
    # CUSTOM EXIT — ATR Tabanlı Take-Profit
    # ─────────────────────────────────────────────────────────────────────────

    def custom_exit(
        self,
        pair: str,
        trade,
        current_time,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        """
        Giriş anındaki fiyat + ATR×TP_MULT hedefine ulaşıldığında pozisyonu kapat.
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return None

        last_candle = dataframe.iloc[-1]
        atr_value   = last_candle.get("atr", 0)

        if atr_value and atr_value > 0:
            tp_target = trade.open_rate + self.tp_mult.value * atr_value
            if current_rate >= tp_target:
                return "atr_take_profit"

        return None
