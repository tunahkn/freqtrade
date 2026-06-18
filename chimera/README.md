# CHIMERA — Çok-Edge'li Ensemble Trading Sistemi

> Tek sihirli sinyal yoktur. **Korelasyonsuz zayıf edge'leri risk-pariteyle istiflemek** finanstaki tek bedava öğle yemeğidir.

## Felsefe

Bir hafta tek bir "dahi" yönlü sinyal aradık (APEX SNIPER v10, OTT Fusion, APEX-M).
Hepsi kırılgandı: in-sample Sharpe 0.007, tek-trade bağımlılığı, BTC-şansı.
**Çünkü tek edge her zaman kırılgandır.**

CHIMERA bunun tersini yapar: 4 ayrı, **birbirinden bağımsız** verimsizliği
yiyen sleeve'i risk-pariteyle istifler. Matematik:

```
4 sleeve × her biri Sharpe ~0.6, korelasyonsuz
→ ensemble Sharpe ≈ 0.6 × √4 ≈ 1.2
```

Korelasyon ne kadar düşükse, ensemble o kadar üstün. Genius = mimari, sinyal değil.

## 4 Sleeve

| # | Sleeve | Yediği verimsizlik | Yön | Dosya |
|---|--------|--------------------|-----|-------|
| 1 | **Funding Harvest** | Perp funding primi | Delta-NÖTR | `funding_harvest.py` ✅ |
| 2 | Cross-Sectional Momentum | Kesitsel momentum | Piyasa-NÖTR | (planlı) |
| 3 | Vol-Regime Directional | Trend süreklliği (sadece düşük-vol) | Yönlü | (APEX-M uyarlaması) |
| 4 | Mean-Reversion | Aşırı sapma dönüşü | Karşı-trend (#3 ile neg. korele) | (planlı) |

## Meta-katman (asıl zekâ)

1. **Risk-parite sizing** — her sleeve eşit RİSK alır (eşit sermaye değil). Düşük-vol
   funding çok sermaye, yüksek-vol yönlü az sermaye → tek sleeve domine edemez.
2. **Korelasyon kill-switch** — sleeve'ler stresle birlikte hareket etmeye başlarsa
   brüt maruziyet otomatik kesilir (2022 tipi çöküş koruması).
3. **Funding çekirdek (%50)** — piyasa-bağımsız gelir = "edge gerçek mi" tartışması biter.

## Sleeve 1: Funding Harvest (HAZIR)

Delta-nötr carry: **spot long + perp short**. Funding pozitifken short tarafı
funding'i toplar; spot long delta'yı nötrler. Piyasa yönünden bağımsız yield.

```bash
# Mac terminalinde (ağ erişimi olan yerde):
pip install ccxt
python chimera/funding_harvest.py --exchange binanceusdm --min-apy 8 --top 15
```

### Dürüst risk notu
- Yield 2026'da normal ~%5-15 APY (boğa euforisinde %30+ spike). Sonsuz değil.
- Borsa riski → çok-borsa dağıt. Funding negatife dönerse → çık (bot izler).
- Spot-perp spread + işlem ücreti carry'yi yer; net-APY filtresi bunu hesaplar.
- Bu **kurumsal değil**, kurumsal MANTIKLA kurulmuş retail sistemi. Garanti yok.
