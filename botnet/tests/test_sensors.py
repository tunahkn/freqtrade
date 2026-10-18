import time

import pandas as pd

from botnet.common.indicators import rsi, trend_score_frame
from botnet.sensors import derivatives, liquidity, orderbook, orderflow, sentiment, trend

from .conftest import make_ohlcv


def test_indicators_direction(uptrend, downtrend):
    assert trend_score_frame(uptrend).iloc[-1] > 0.3
    assert trend_score_frame(downtrend).iloc[-1] < -0.3
    r = rsi(uptrend["close"])
    assert r.dropna().between(0, 100).all()


def test_trend_sensor(uptrend, downtrend):
    up = trend.analyze({"15m": uptrend, "1H": uptrend, "4H": uptrend})
    dn = trend.analyze({"15m": downtrend, "1H": downtrend, "4H": downtrend})
    assert up.score > 0.4 and "htf_bull" in up.tags
    assert dn.score < -0.4 and "htf_bear" in dn.tags
    assert up.confidence > 0.7
    assert trend.analyze({}).tags == ["no_data"]


def _trades(buy_btc, sell_btc, price_start=60000, price_end=60000, whale=None):
    rows = []
    n = 40
    for i in range(n):
        p = price_start + (price_end - price_start) * i / (n - 1)
        rows.append({"ts": time.time() - n + i, "price": p, "btc": buy_btc / n, "side": "buy"})
        rows.append({"ts": time.time() - n + i, "price": p, "btc": sell_btc / n, "side": "sell"})
    if whale:
        rows.append({"ts": time.time(), "price": price_end, "btc": whale[1], "side": whale[0]})
    return pd.DataFrame(rows)


def test_orderflow_whale_buy_pressure():
    sig = orderflow.analyze(_trades(300, 100, 60000, 60200, whale=("buy", 60)), whale_btc=10)
    assert sig.score > 0.3
    assert "whale_buy" in sig.tags


def test_orderflow_bull_absorption():
    # Ağır satış ama fiyat yükseliyor -> alıcı emiyor
    sig = orderflow.analyze(_trades(100, 400, 60000, 60050), whale_btc=10)
    assert "bull_absorption" in sig.tags
    assert sig.score > 0


def _book(mid, bid_size=1.0, ask_size=1.0, bid_wall=None, ask_wall=None):
    bids = [(mid - 1 - i, bid_size) for i in range(300)]
    asks = [(mid + 1 + i, ask_size) for i in range(300)]
    if bid_wall:
        bids[bid_wall[0]] = (bids[bid_wall[0]][0], bid_wall[1])
    if ask_wall:
        asks[ask_wall[0]] = (asks[ask_wall[0]][0], ask_wall[1])
    return bids, asks


def test_orderbook_imbalance_and_spoof():
    bids, asks = _book(60000, bid_size=3.0, ask_size=1.0, bid_wall=(50, 100))
    sig, bw, aw = orderbook.analyze(bids, asks)
    assert sig.score > 0.3 and "bid_wall" in sig.tags and bw
    # Bir sonraki görüntüde duvar kayboldu, fiyat oraya inmedi -> spoofing
    bids2, asks2 = _book(60000, bid_size=1.0, ask_size=1.0)
    sig2, _, _ = orderbook.analyze(bids2, asks2, bw, aw)
    assert "bid_wall_pulled" in sig2.tags and sig2.score < 0


def test_derivatives_crowded_longs_bearish():
    oi = pd.DataFrame({"ts": range(20), "oi": [100] * 7 + [100 - i for i in range(13)], "vol": 1})
    ls = pd.DataFrame({"ts": [1], "ratio": [2.6]})
    taker = pd.DataFrame({"ts": range(12), "sell": [2.0] * 12, "buy": [1.0] * 12})
    sig = derivatives.analyze(0.0008, oi, ls, taker, price_change=-0.01)
    assert sig.score < -0.3
    assert {"crowd_long_squeeze_risk", "long_liquidation", "taker_sell_pressure"} <= set(sig.tags)


def test_liquidity_sweep_low_and_liq_wave():
    df = make_ohlcv(100, seed=3)
    prior_low = df["low"].iloc[-21:-1].min()
    i = df.index[-1]
    df.loc[i, ["open", "close", "high", "low"]] = [
        prior_low + 50,
        prior_low + 80,
        prior_low + 90,
        prior_low - 200,
    ]
    liqs = pd.DataFrame(
        [{"ts": time.time() - 60, "pos_side": "long", "btc": 120.0, "price": prior_low}]
    )
    sig = liquidity.analyze(df, liqs)
    assert {"sweep_low", "long_liq_wave", "stop_hunt_reversal"} <= set(sig.tags)
    assert sig.score > 0.8 and sig.confidence >= 0.9


def test_sentiment_contrarian():
    assert sentiment.analyze(10).score > 0.7
    assert sentiment.analyze(90).score < -0.7
    assert "extreme_fear" in sentiment.analyze(10).tags
