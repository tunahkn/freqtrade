# pragma pylint: disable=missing-docstring, invalid-name, too-few-public-methods
"""
ApexSniper — freqtrade port of the APEX SNIPER v9 SMC/ICT confluence engine.

Entry  : confirmed Grade A/B sniper signal (score >= sniper_th, >= min_cats).
Stop   : ATR-based, moved to (fee-aware) breakeven after price reaches TP1.
Exit   : R-multiple targets via custom_exit + partial scale-out at TP1/TP2
         when position adjustment is enabled.

Indicators come from apex_engine (pure pandas, no TA-Lib), so this strategy
runs without the TA-Lib C library installed.

Run:
  freqtrade backtesting -s ApexSniper --timeframe 5m \
      --timerange 20240101-  --datadir user_data/data/<exchange>
"""
from __future__ import annotations

from datetime import datetime

from pandas import DataFrame

from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade

import apex_engine


class ApexSniper(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "5m"
    can_short = True

    # exits are handled by custom_exit / custom_stoploss
    minimal_roi = {"0": 100}
    stoploss = -0.99               # wide; real stop is custom_stoploss (ATR)
    use_custom_stoploss = True
    trailing_stop = False

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # partial scale-out support (optional)
    position_adjustment_enable = True

    startup_candle_count: int = 250

    # --- tunables (hyperopt-friendly defaults mirror Pine v9) ---
    atr_stop_m = 1.5
    rr_target = 1.5
    fee_buf_pct = 0.12

    # engine params (override via config 'strategy' section if desired)
    engine_params: dict = {}

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = apex_engine.compute(dataframe, self.engine_params)
        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["enter_long"], ["enter_long", "enter_tag"]] = (1, "apex_long")
        dataframe.loc[dataframe["enter_short"], ["enter_short", "enter_tag"]] = (1, "apex_short")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # exits handled in custom_exit; opposite signal also closes
        dataframe.loc[dataframe["enter_short"], "exit_long"] = 1
        dataframe.loc[dataframe["enter_long"], "exit_short"] = 1
        return dataframe

    # ------------------------------------------------------------------ #
    def _levels(self, trade: Trade):
        """Return (entry, stop_dist, tp1, tp2, tp3) using stored ATR at entry."""
        atr = trade.get_custom_data("atr_at_entry")
        if atr is None or atr <= 0:
            return None
        stop_dist = atr * self.atr_stop_m
        e = trade.open_rate
        if not trade.is_short:
            return e, stop_dist, e + stop_dist * self.rr_target, \
                e + stop_dist * self.rr_target * 2, e + stop_dist * self.rr_target * 3
        return e, stop_dist, e - stop_dist * self.rr_target, \
            e - stop_dist * self.rr_target * 2, e - stop_dist * self.rr_target * 3

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force,
                            current_time, entry_tag, side, **kwargs) -> bool:
        # stash ATR at entry for stop/target math
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(df):
            atr = float(df["atr"].iloc[-1])
            self._pending_atr = atr
        return True

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        atr = trade.get_custom_data("atr_at_entry")
        if atr is None:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if len(df):
                atr = float(df["atr"].iloc[-1])
                trade.set_custom_data("atr_at_entry", atr)
        if not atr or atr <= 0:
            return 1
        stop_dist = atr * self.atr_stop_m
        e = trade.open_rate

        # breakeven after TP1 reached (fee-aware)
        lv = self._levels(trade)
        be_moved = trade.get_custom_data("be_moved")
        if lv:
            _, _, tp1, _, _ = lv
            reached = (current_rate >= tp1) if not trade.is_short else (current_rate <= tp1)
            if reached and not be_moved:
                trade.set_custom_data("be_moved", True)
                be_moved = True
        if be_moved:
            buf = e * self.fee_buf_pct / 100.0
            be = (e + buf) if not trade.is_short else (e - buf)
            return abs(be - current_rate) / current_rate * (1 if be <= current_rate else -1) \
                if not trade.is_short else abs(be - current_rate) / current_rate

        # initial ATR stop as relative distance
        return -(stop_dist / e)

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        lv = self._levels(trade)
        if not lv:
            return None
        _, _, _, _, tp3 = lv
        if not trade.is_short and current_rate >= tp3:
            return "apex_tp3"
        if trade.is_short and current_rate <= tp3:
            return "apex_tp3"
        return None

    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake, max_stake: float, current_entry_rate: float,
                              current_exit_rate: float, current_entry_profit: float,
                              current_exit_profit: float, **kwargs):
        """Scale out ~1/3 at TP1 and ~1/2 of remainder at TP2 (thirds total)."""
        lv = self._levels(trade)
        if not lv:
            return None
        _, _, tp1, tp2, _ = lv
        long = not trade.is_short

        if not trade.get_custom_data("tp1_done"):
            hit = (current_rate >= tp1) if long else (current_rate <= tp1)
            if hit:
                trade.set_custom_data("tp1_done", True)
                return -(trade.stake_amount * 0.33)
        if trade.get_custom_data("tp1_done") and not trade.get_custom_data("tp2_done"):
            hit = (current_rate >= tp2) if long else (current_rate <= tp2)
            if hit:
                trade.set_custom_data("tp2_done", True)
                return -(trade.stake_amount * 0.50)
        return None

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side, **kwargs) -> float:
        return 1.0
