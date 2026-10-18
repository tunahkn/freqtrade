"""
Ağ erişimi OLMADAN BrainStrategy'yi Freqtrade backtesting / lookahead-analysis içinde
çalıştırır (örn. borsaya erişimi kapalı Claude bulut oturumunda).

Sentetik BTC/USDT:USDT verisi üretir (yukarı, aşağı ve yatay rejimler) ve OKX borsa
nesnesini sahte market bilgisiyle yamalar. SONUÇLARIN KÂR DEĞERİ YOKTUR - sadece
stratejinin hatasız çalıştığını, işlem sıklığını ve risk mekaniğini kontrol eder.
Gerçek backtest için VPS'te `freqtrade download-data` ile gerçek veri kullan.

Kullanım:
    python -m botnet.tools.offline_backtest . backtesting
    python -m botnet.tools.offline_backtest . lookahead
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import numpy as np
import pandas as pd


ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
MODE = sys.argv[2] if len(sys.argv) > 2 else "backtesting"
DATADIR = ROOT / "user_data" / "data" / "okx"
PAIR = "BTC/USDT:USDT"


def make_data():
    rng = np.random.default_rng(42)
    n_days = 150
    n = n_days * 96
    regimes = np.repeat(rng.choice([0.0008, -0.0008, 0.0], size=n // 960 + 1), 960)[:n]
    rets = rng.normal(regimes / 4, 0.003, n)
    close = 60000 * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[60000], close[:-1]])
    wick = np.abs(rng.normal(0, 0.0015, n)) * close
    df = pd.DataFrame(
        {
            "date": pd.date_range("2026-03-01", periods=n, freq="15min", tz="UTC"),
            "open": open_,
            "high": np.maximum(open_, close) + wick,
            "low": np.minimum(open_, close) - wick,
            "close": close,
            "volume": rng.uniform(50, 500, n),
        }
    )
    return df


def resample(df, rule):
    out = (
        df.set_index("date")
        .resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    return out


def store():
    from freqtrade.data.history import get_datahandler
    from freqtrade.enums import CandleType

    (DATADIR / "futures").mkdir(parents=True, exist_ok=True)
    dh = get_datahandler(DATADIR, "feather")
    df15 = make_data()
    dh.ohlcv_store(PAIR, "15m", df15, CandleType.FUTURES)
    dh.ohlcv_store(PAIR, "1h", resample(df15, "1h"), CandleType.FUTURES)
    dh.ohlcv_store(PAIR, "4h", resample(df15, "4h"), CandleType.FUTURES)
    dh.ohlcv_store(PAIR, "1h", resample(df15, "1h"), CandleType.MARK)
    dh.ohlcv_store(PAIR, "4h", resample(df15, "4h"), CandleType.MARK)
    fr = resample(df15, "8h")
    fr[["open", "high", "low", "close"]] = 0.0001
    fr["volume"] = 0
    dh.ohlcv_store(PAIR, "8h", fr, CandleType.FUNDING_RATE)
    return df15["date"].iloc[0], df15["date"].iloc[-1]


MARKET = {
    PAIR: {
        "id": "BTC-USDT-SWAP",
        "symbol": PAIR,
        "base": "BTC",
        "quote": "USDT",
        "settle": "USDT",
        "baseId": "BTC",
        "quoteId": "USDT",
        "settleId": "USDT",
        "type": "swap",
        "spot": False,
        "margin": False,
        "swap": True,
        "future": False,
        "option": False,
        "contract": True,
        "linear": True,
        "inverse": False,
        "taker": 0.0005,
        "maker": 0.0002,
        "contractSize": 0.01,
        "active": True,
        "expiry": None,
        "limits": {
            "leverage": {"min": 1, "max": 100},
            "amount": {"min": 0.01, "max": 100000},
            "price": {"min": None, "max": None},
            "cost": {"min": None, "max": None},
        },
        "precision": {"price": 0.1, "amount": 0.01},
        "info": {},
    }
}

EXMS = "freqtrade.exchange.exchange.Exchange"


def main():
    start, end = store()
    bt_start = start + pd.Timedelta(days=12).to_pytimedelta()
    timerange = f"{bt_start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
    config = str(ROOT / "botnet" / "config" / "config_okx_futures.json")
    patches = [
        patch(f"{EXMS}._init_ccxt", MagicMock()),
        patch(f"{EXMS}._load_async_markets", return_value={}),
        patch(f"{EXMS}.markets", PropertyMock(return_value=MARKET)),
        patch(f"{EXMS}.validate_config", MagicMock()),
        patch(f"{EXMS}.get_fee", return_value=0.0005),
        patch(f"{EXMS}.precisionMode", PropertyMock(return_value=4)),
        patch(f"{EXMS}.precision_mode_price", PropertyMock(return_value=4)),
        patch(f"{EXMS}.timeframes", PropertyMock(return_value=["15m", "1h", "4h", "8h"])),
        patch(
            f"{EXMS}.load_leverage_tiers",
            return_value={
                PAIR: [
                    {
                        "minNotional": 0,
                        "maxNotional": 1e9,
                        "maintenanceMarginRate": 0.004,
                        "maxLeverage": 100,
                        "info": {"cum": 0},
                    }
                ]
            },
        ),
        patch(f"{EXMS}.ohlcv_candle_limit", return_value=300),
    ]
    for p in patches:
        p.start()

    from freqtrade.commands import Arguments

    common = [
        "--config",
        config,
        "--userdir",
        str(ROOT / "user_data"),
        "--datadir",
        str(DATADIR),
        "--timerange",
        timerange,
        "--data-format-ohlcv",
        "feather",
    ]
    if MODE == "lookahead":
        argv = [
            "lookahead-analysis",
            *common,
            "--strategy",
            "BrainStrategy",
            "--minimum-trade-amount",
            "5",
            "--targeted-trade-amount",
            "10",
        ]
    else:
        argv = [
            "backtesting",
            *common,
            "--strategy",
            "BrainStrategy",
            "--export",
            "none",
            "--breakdown",
            "month",
            "--enable-protections",
        ]
    args = Arguments(argv).get_parsed_arg()
    args["func"](args)


if __name__ == "__main__":
    main()
