# APEX SNIPER — full suite

An SMC/ICT confluence-scoring system, delivered in three forms that share one
scoring logic so signals stay consistent across them.

## Files

| File | What it is |
|------|------------|
| `pine/APEX_SNIPER_v9.pine` | TradingView **strategy** (backtest engine). v8 → v9 fixes: risk-based sizing, fee-aware breakeven, daily-loss + consecutive-loss guards, gated derivatives inputs, overfit reduction, POC fix, walk-forward marker. |
| `pine/APEX_SNIPER_v9_PRO_DASHBOARD.pine` | TradingView **indicator** with a pro dashboard: live LONG/SHORT signal, grade A/B/C, score /20, per-factor breakdown, FVG zones, liquidity/PD/OTE drawings, JSON webhook alerts. |
| `user_data/strategies/apex_engine.py` | Framework-free **engine** (pure pandas/numpy, no TA-Lib). Faithful port of the v9 logic. |
| `user_data/strategies/ApexSniper.py` | **freqtrade** `IStrategy` using the engine. ATR stop + tiered TP + breakeven. |
| `scripts/apex_backtest.py` | Standalone **backtester** (no freqtrade install needed) that replays the engine and prints honest stats. |

## Scoring (0–20)

Four capped categories, weighted and normalized:

- **Trend** — HTF bias + VWAP agreement
- **Structure** — BOS/CHoCH + FVG CE tap
- **Liquidity** — sweep + CVD divergence (+ optional funding/L-S)
- **Location** — premium/discount, OTE, POC reclaim, kill zone

Entry requires `score ≥ sniper_th` (default 14) **and** `≥ min_cats` (default 3)
distinct categories active, on a confirmed bar, with debounce.

## Running the standalone backtest

```bash
python scripts/apex_backtest.py tests/testdata/UNITTEST_BTC-5m.feather
python scripts/apex_backtest.py <your_data.feather> --rr 2 --risk 1 --sniper-th 15
```

## Running in freqtrade

```bash
freqtrade backtesting -s ApexSniper --timeframe 5m \
    --timerange 20240101- --datadir user_data/data/<exchange>
```

## Honest verdict (read this)

No indicator is "award-winning" or guarantees profit — including this one.
What separates a tradeable system from a pretty hypothesis is **evidence**, not
visuals:

1. **Out-of-sample** backtest (don't judge on the data you tuned on).
2. **Walk-forward** — optimize only on past windows, never touch the future window.
3. **Forward / paper test** for weeks before any real capital.
4. **≥ 100 trades** before trusting win-rate / profit-factor numbers.
5. Costs (fee + slippage + funding) included and still positive.

On random-walk data this engine intentionally does **not** manufacture profit
(it just pays the fees) — a sign it isn't curve-fit to noise. Prove it on real,
large, out-of-sample data before trusting it.
