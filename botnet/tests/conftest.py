import numpy as np
import pandas as pd
import pytest


def make_ohlcv(
    n: int = 300, drift: float = 0.0, seed: int = 1, start: float = 60000.0
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.002, n)
    close = start * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start], close[:-1]])
    spread = np.abs(rng.normal(0, 0.0015, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(10, 100, n),
        }
    )


@pytest.fixture
def uptrend():
    return make_ohlcv(drift=0.0015)


@pytest.fixture
def downtrend():
    return make_ohlcv(drift=-0.0015)
