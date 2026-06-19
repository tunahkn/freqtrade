#!/usr/bin/env python3
"""
CHIMERA · Sleeve 1 — FUNDING HARVEST (delta-neutral carry)
==========================================================
Spot long + perpetual short → collect funding, neutral to price direction.

The short perp leg earns the funding payment whenever funding is positive;
the spot long leg cancels the price delta. Net P&L ≈ funding collected −
(fees + spot/perp spread). Market-direction-independent yield.

This scanner:
  1. Pulls live funding rates for every USDT-perp on the chosen exchange
  2. Confirms a matching spot market exists (so the hedge is buildable)
  3. Annualizes funding, subtracts round-trip fee + spread drag
  4. Ranks by NET annualized yield and filters by liquidity + min-APY
  5. Prints a ranked opportunity table and equal-risk position sizing

NETWORK: needs exchange API access — run on a machine with connectivity
(your Mac), not in a sandboxed/egress-blocked environment.

    pip install ccxt
    python chimera/funding_harvest.py --exchange binanceusdm --min-apy 8 --top 15

RISK: yields compress over time; exchange/counterparty risk (diversify venues);
funding can flip negative (the live bot must exit). Not financial advice.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass


# ─────────────────────────── CONFIG ───────────────────────────
FUNDING_INTERVALS_PER_DAY = 3          # most venues settle funding every 8h
DAYS_PER_YEAR = 365
# Conservative cost assumptions (override via CLI)
DEFAULT_TAKER_FEE = 0.0005             # 0.05% per side
DEFAULT_SPREAD = 0.0003                # ~0.03% spot/perp execution slippage
DEFAULT_HOLD_DAYS = 7                  # amortize entry/exit cost over holding


@dataclass
class Opportunity:
    symbol: str
    funding_rate: float        # per-interval (e.g. per 8h)
    funding_apy: float         # gross annualized
    net_apy: float             # after fees + spread amortized over hold
    mark_price: float
    quote_volume: float        # 24h notional, liquidity proxy

    def as_row(self) -> str:
        return (f"  {self.symbol:<18} {self.funding_rate*100:+7.4f}%  "
                f"{self.funding_apy:+7.1f}%  {self.net_apy:+7.1f}%  "
                f"{self.mark_price:>12,.4f}  {self.quote_volume/1e6:>8,.1f}M")


# ─────────────────────────── CORE ───────────────────────────
def annualize(funding_rate: float) -> float:
    """Per-interval funding → annualized %, compounding intervals."""
    return funding_rate * FUNDING_INTERVALS_PER_DAY * DAYS_PER_YEAR * 100


def net_after_costs(funding_apy: float, taker_fee: float, spread: float,
                    hold_days: int) -> float:
    """
    Subtract round-trip cost (2 legs × open+close = 4 fills) + spread,
    amortized over the holding period and expressed as annualized %.
    """
    roundtrip_cost = 4 * taker_fee + 2 * spread          # fraction of notional
    annualized_cost = roundtrip_cost * (DAYS_PER_YEAR / max(hold_days, 1)) * 100
    return funding_apy - annualized_cost


def build_exchange(name: str):
    try:
        import ccxt
    except ImportError:
        sys.exit("ccxt yok. Kur: pip install ccxt")
    if not hasattr(ccxt, name):
        sys.exit(f"Bilinmeyen borsa: {name}")
    ex = getattr(ccxt, name)({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    return ex


def scan(exchange_name: str, taker_fee: float, spread: float, hold_days: int,
         min_quote_volume: float) -> list[Opportunity]:
    ex = build_exchange(exchange_name)
    ex.load_markets()

    # Spot symbols available for the long hedge leg
    spot_bases = set()
    for m in ex.markets.values():
        if m.get("spot") and m.get("quote") == "USDT":
            spot_bases.add(m["base"])

    try:
        rates = ex.fetch_funding_rates()           # all perps
    except Exception as e:                          # noqa: BLE001
        sys.exit(f"Funding çekilemedi: {e}")

    try:
        tickers = ex.fetch_tickers()
    except Exception:                               # noqa: BLE001
        tickers = {}

    opps: list[Opportunity] = []
    for sym, info in rates.items():
        fr = info.get("fundingRate")
        if fr is None:
            continue
        market = ex.markets.get(sym)
        if not market or market.get("quote") != "USDT":
            continue
        base = market.get("base")
        if base not in spot_bases:                  # need a spot leg to hedge
            continue

        tk = tickers.get(sym, {})
        qv = tk.get("quoteVolume") or 0.0
        if qv < min_quote_volume:
            continue
        mark = info.get("markPrice") or tk.get("last") or 0.0

        g_apy = annualize(fr)
        n_apy = net_after_costs(g_apy, taker_fee, spread, hold_days)
        opps.append(Opportunity(sym, fr, g_apy, n_apy, mark, qv))

    return opps


def equal_risk_sizing(opps: list[Opportunity], capital: float,
                      max_per_pair: float) -> dict[str, float]:
    """
    Naive risk-parity across selected legs: funding carry vol is dominated by
    funding variability, roughly similar across majors, so start with equal
    notional capped per pair. (Refined vol-weighting comes with live data.)
    """
    if not opps:
        return {}
    per = min(capital / len(opps), capital * max_per_pair)
    return {o.symbol: per for o in opps}


# ─────────────────────────── CLI ───────────────────────────
def main() -> None:
    p = argparse.ArgumentParser(description="CHIMERA Sleeve 1 — Funding Harvest scanner")
    p.add_argument("--exchange", default="binanceusdm",
                   help="ccxt id (binanceusdm, bybit, okx, ...)")
    p.add_argument("--min-apy", type=float, default=8.0, help="min NET annualized %% ")
    p.add_argument("--top", type=int, default=15, help="show top N")
    p.add_argument("--taker-fee", type=float, default=DEFAULT_TAKER_FEE)
    p.add_argument("--spread", type=float, default=DEFAULT_SPREAD)
    p.add_argument("--hold-days", type=int, default=DEFAULT_HOLD_DAYS)
    p.add_argument("--min-volume", type=float, default=5_000_000,
                   help="min 24h quote volume (liquidity filter)")
    p.add_argument("--capital", type=float, default=10_000)
    p.add_argument("--max-per-pair", type=float, default=0.25,
                   help="max capital fraction per pair")
    args = p.parse_args()

    print("=" * 78)
    print(f"  CHIMERA · FUNDING HARVEST  —  {args.exchange}")
    print(f"  Filtre: net-APY ≥ {args.min_apy}%  |  hacim ≥ ${args.min_volume/1e6:.0f}M  "
          f"|  tut {args.hold_days}g")
    print("=" * 78)

    opps = scan(args.exchange, args.taker_fee, args.spread, args.hold_days,
                args.min_volume)
    opps = [o for o in opps if o.net_apy >= args.min_apy]
    opps.sort(key=lambda o: o.net_apy, reverse=True)
    top = opps[: args.top]

    if not top:
        print("\n  Filtreyi geçen fırsat yok. --min-apy düşür veya piyasa sakin.")
        return

    print(f"\n  {'Sembol':<18} {'Funding':>8}  {'GrosAPY':>8} {'NetAPY':>8}  "
          f"{'Mark':>12}  {'24sHacim':>9}")
    print("  " + "-" * 72)
    for o in top:
        print(o.as_row())

    sizing = equal_risk_sizing(top, args.capital, args.max_per_pair)
    print("\n  KURULUM (her fırsat = spot LONG + perp SHORT, eşit notional):")
    for sym, notional in sizing.items():
        print(f"    {sym:<18} spot long ${notional:,.0f}  +  perp short ${notional:,.0f}")

    blended = sum(o.net_apy for o in top) / len(top)
    print(f"\n  Sepet ortalama NET yield ≈ {blended:.1f}% APY (delta-nötr)")
    print("  Uyarı: funding negatife dönerse o bacağı KAPAT. Borsa riskini dağıt.")
    print("=" * 78)


if __name__ == "__main__":
    main()
