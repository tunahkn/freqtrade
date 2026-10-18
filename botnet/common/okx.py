"""
OKX public REST istemcisi (API anahtarı gerektirmez).

Sadece stdlib (urllib) kullanır. Sensörlerin hepsi bu sınıf üzerinden veri çeker.
Belgeler: https://www.okx.com/docs-v5/en/
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request

import pandas as pd


logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("OKX_BASE_URL", "https://www.okx.com")
INST_ID = os.environ.get("BOTNET_INST_ID", "BTC-USDT-SWAP")
# BTC-USDT-SWAP için 1 kontrat = 0.01 BTC
CONTRACT_VALUE_BTC = float(os.environ.get("BOTNET_CT_VAL", "0.01"))


class OkxError(RuntimeError):
    pass


class OkxClient:
    def __init__(self, base_url: str = BASE_URL, inst_id: str = INST_ID, timeout: float = 10):
        self.base_url = base_url.rstrip("/")
        self.inst_id = inst_id
        self.timeout = timeout
        # "BTC-USDT-SWAP" -> uly "BTC-USDT", ccy "BTC"
        self.uly = "-".join(inst_id.split("-")[:2])
        self.ccy = inst_id.split("-")[0]

    def get(self, path: str, params: dict | None = None, retries: int = 3) -> list:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "botnet/1.0"})  # noqa: S310
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                    payload = json.loads(resp.read().decode())
                if str(payload.get("code")) != "0":
                    raise OkxError(f"{path}: {payload.get('code')} {payload.get('msg')}")
                return payload.get("data", [])
            except Exception as e:  # noqa: BLE001 - ağ hatalarında tekrar dene
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        raise OkxError(f"{path} başarısız: {last_err}")

    # --- Piyasa verisi -------------------------------------------------

    def candles(self, bar: str = "15m", limit: int = 300) -> pd.DataFrame:
        """Artan zaman sırasında OHLCV. Sadece kapanmış mumlar döner."""
        rows = self.get(
            "/api/v5/market/candles",
            {"instId": self.inst_id, "bar": bar, "limit": min(limit, 300)},
        )
        df = pd.DataFrame(
            rows,
            columns=["ts", "open", "high", "low", "close", "vol", "volCcy", "volQuote", "confirm"],
        )
        if df.empty:
            return df
        df = df[df["confirm"] == "1"]
        for col in ["open", "high", "low", "close", "vol"]:
            df[col] = df[col].astype(float)
        df["date"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
        df = df.rename(columns={"vol": "volume"})
        return (
            df[["date", "open", "high", "low", "close", "volume"]]
            .sort_values("date")
            .reset_index(drop=True)
        )

    def trades(self, limit: int = 500) -> pd.DataFrame:
        """Son işlemler: ts, price, btc (miktar), side ('buy' = agresif alıcı), trade_id."""
        rows = self.get("/api/v5/market/trades", {"instId": self.inst_id, "limit": limit})
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=["ts", "price", "btc", "side", "trade_id"])
        return pd.DataFrame(
            {
                "ts": df["ts"].astype("int64") / 1000.0,
                "price": df["px"].astype(float),
                "btc": df["sz"].astype(float) * CONTRACT_VALUE_BTC,
                "side": df["side"],
                "trade_id": df["tradeId"],
            }
        ).sort_values("ts")

    def orderbook(
        self, depth: int = 400
    ) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        """(bids, asks) -> [(fiyat, btc), ...]"""
        data = self.get("/api/v5/market/books", {"instId": self.inst_id, "sz": depth})
        book = data[0]

        def conv(levels):
            return [(float(p), float(s) * CONTRACT_VALUE_BTC) for p, s, *_ in levels]

        return conv(book["bids"]), conv(book["asks"])

    def ticker_price(self) -> float:
        data = self.get("/api/v5/market/ticker", {"instId": self.inst_id})
        return float(data[0]["last"])

    # --- Türev verisi --------------------------------------------------

    def funding_rate(self) -> float:
        data = self.get("/api/v5/public/funding-rate", {"instId": self.inst_id})
        return float(data[0]["fundingRate"])

    def open_interest_history(self, period: str = "5m") -> pd.DataFrame:
        """OI (USD) ve hacim geçmişi, artan sırada."""
        rows = self.get(
            "/api/v5/rubik/stat/contracts/open-interest-volume",
            {"ccy": self.ccy, "period": period},
        )
        df = pd.DataFrame(rows, columns=["ts", "oi", "vol"]).astype(float)
        return df.sort_values("ts").reset_index(drop=True)

    def long_short_ratio(self, period: str = "5m") -> pd.DataFrame:
        rows = self.get(
            "/api/v5/rubik/stat/contracts/long-short-account-ratio",
            {"ccy": self.ccy, "period": period},
        )
        df = pd.DataFrame(rows, columns=["ts", "ratio"]).astype(float)
        return df.sort_values("ts").reset_index(drop=True)

    def taker_volume(self, period: str = "5m") -> pd.DataFrame:
        rows = self.get(
            "/api/v5/rubik/stat/taker-volume",
            {"ccy": self.ccy, "instType": "CONTRACTS", "period": period},
        )
        df = pd.DataFrame(rows, columns=["ts", "sell", "buy"]).astype(float)
        return df.sort_values("ts").reset_index(drop=True)

    def liquidations(self) -> pd.DataFrame:
        """
        Gerçekleşmiş likidasyonlar: ts, side, btc, price.
        posSide='long' -> long pozisyon likide oldu (zorla satış).
        """
        data = self.get(
            "/api/v5/public/liquidation-orders",
            {"instType": "SWAP", "uly": self.uly, "state": "filled", "limit": 100},
        )
        rows = []
        for block in data:
            for d in block.get("details", []):
                rows.append(
                    {
                        "ts": int(d["ts"]) / 1000.0,
                        "pos_side": d.get("posSide", ""),
                        "btc": float(d["sz"]) * CONTRACT_VALUE_BTC,
                        "price": float(d["bkPx"]),
                    }
                )
        return pd.DataFrame(rows, columns=["ts", "pos_side", "btc", "price"])


def fear_greed_index() -> int:
    """alternative.me Fear & Greed endeksi (0 = aşırı korku, 100 = aşırı açgözlülük)."""
    url = "https://api.alternative.me/fng/?limit=1"
    req = urllib.request.Request(url, headers={"User-Agent": "botnet/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode())
    return int(payload["data"][0]["value"])
