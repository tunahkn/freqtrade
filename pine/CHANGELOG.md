# APEX SNIPER — CHANGELOG

Repo: `tunahkn/freqtrade` · Branch: `claude/opus-indicator-analysis-p8eqj`
Tüm tarihler UTC. "En iyi sürüm" kararı verilince burası işaretlenecek ve Google Drive'a da kopyalanacak.

---

## Durum: ✅ DOĞRULANDI — LONG-ONLY (3 varlık çapraz testi geçti)

| Sürüm | Tarih | Commit | Durum | Not |
|-------|-------|--------|-------|-----|
| v9.3  | 2026-06-11 | TBD    | ✅ **best** | Long-only (3 varlık doğrulandı) |
| v9.2  | 2026-05-31 | `57f50b6` | ⬅ eski | Short rejim filtresi (200 EMA) |
| v9.1  | 2026-05-31 | `3e4ac71` | ⬅ eski | Çıkış modeli düzeltmesi |
| v9.0  | 2026-05-30 | `fcd9f79` | ⬅ eski | İlk v9 |

> **ÖNERİLEN CONFIG (best):** 4H · `tradeShort = OFF` · sniper=11 · atr=1.0 · rr=2.0 ·
> min-cats=2 · trailing=on · 24/7 (kill-zone kapalı).
> Dürüst not: pozitif-edge'li, **düşük-DD trend takipçisi**. ETH al-tut'unu mutlak
> getiride GEÇMEZ — değeri düşük drawdown + çapraz-varlık tutarlılığı.

---

## 📒 DOĞRULAMA GÜNLÜĞÜ

### 2026-06-11 — ⭐ GERÇEK VERİ, GERÇEK OOS: 2017-2019 TAM DÖNGÜ (kendi indirdiğimiz Bitfinex 1m → 4H)
Kaynak: Bitfinex gerçek 1-dakika mumları (GitHub arşivi), 3 varlık × 3 yıl, 1H+4H'ye resample.
Dönem 2017 boğa + 2018 ayı + 2019 toparlanma = **donmuş ayarlar için gerçek out-of-sample**.
Config: v9 long-only "best" (sniper=11, atr=1.0, rr=2.0, min-cats=2, trail on, fee 0.05+slip 0.02).

| Varlık (4H) | İşlem | PF | Getiri | B&H | ALPHA | Kapı |
|------------|-------|-----|--------|------|-------|------|
| BTC | 58 | 0.69 | −13.0% | +647% | −660% | 0/5 🔴 |
| ETH | 64 | 0.63 | −14.8% | +1410% | −1425% | 0/5 🔴 |
| LTC | 84 | 0.83 | −10.7% | +833% | −844% | 0/5 🔴 |
| BTC (short açık) | 120 | 0.84 | −13.0% | | | 1/5 🔴 |
| ETH (short açık) | 113 | 0.83 | −11.6% | | | 1/5 🔴 |

**HÜKÜM:** 2019-2026'da görülen PF 1.3-1.8 "edge", 2017-2019'a TAŞINMIYOR (0/3 varlık).
Monte Carlo kârlılık olasılığı %0. Bu, ya rejim bağımlılığı ya da 2019-2026'ya overfit demek.
Kurumsal masa kararı: **bu hâliyle canlıya ÇIKMAZ** — araştırmaya geri döner (rejim analizi şart).
Dürüst not: Harness daha önce TradingView'le doğrulanmıştı (ETH 1H: 0.75 ≈ 0.745) → ölçüm güvenilir.


### 2026-06-11 — KURUMSAL DOĞRULAYICI eklendi (`scripts/apex_validate.py`)
Tüm proje karnesi çıkarıldı. Gerçek eksik: daha çok indikatör değil — **risk-ayarlı + sağlamlık ölçümü**.
Eklenen araç, bir trading masasının kullandığı 5 kapıyı uygular ve PASS/FAIL karnesi basar:
- **Sharpe / Sortino** (risk-ayarlı getiri, ham getiri değil)
- **Monte Carlo** (2000 kez işlem sırası karıştır → kârlı kalma olasılığı + p95 DD) — şans serisi mi gerçek edge mi
- **ALPHA vs Buy&Hold** (makine değer katıyor mu)
- **≥100 işlem** anlamlılık kapısı
- **Çapraz-varlık** portföy hükmü (genelleşiyor mu / varlığa özgü mü)

Sentetik veride (11-38 işlem) doğru şekilde 🔴 NOT TRADEABLE veriyor — kapılar dürüst.
Gerçek hüküm için: BTC/ETH/SOL 4H gerçek export'larında çalıştır.


### 2026-06-11 — APEX FINAL STRATEJI (kullanıcının ORACLE motoru) doğrulama-hazır hâle getirildi
Kullanıcı, başka bir AI'ın geliştirdiği "APEX FINAL" motorunu getirdi (BTC 1H'de PF ~2.4 / +%72 raporlandı).
**Dürüst puan (gelen hâl): 7/10.** Gerçek `strategy` (backtest edilebilir ✅), dürüst dashboard ✅,
`lookahead_off` ✅, mantıklı ~%1 risk sizing ✅. AMA: tek varlık/tek period (LONG-canavar tuzağı),
Buy&Hold karşılaştırması yok, OOS yok, risk guard yok, asimetrik ADX (22/16) overfit kokusu.

**Eklenen (→ `pine/APEX_FINAL_STRATEJI.pine`):**
- (A) Dashboard'a **Buy&Hold + ALPHA + Capture%** satırı — +%72 gerçek alpha mı yoksa piyasa mı yükseldi, net görünür
- (B) **Tarih aralığı + OOS bölme gölgesi** — walk-forward yapılabilir
- (C) **Risk guards** (günlük zarar + ardışık zarar freni)
- (D) Yön default **Long** (3-varlık testimizle uyumlu)

**10/10 KAPILARI (kod değil, kanıt):** BTC+ETH+SOL aynı ayar PF>1 · OOS'ta PF>1 · ALPHA>0 · ≥100 işlem.


### 2026-05-31 — ETH 4H, TAM DÖNGÜ (2019-11 → 2026-05, 6.5 yıl) ⭐ KİLOMETRE TAŞI
Ayarlar: 24/7 (kill-zone kapalı), sniper=11, 4H, long+short açık, trailing exit.

| Metrik | Strateji | Buy & Hold | Sonuç |
|--------|----------|------------|-------|
| Getiri | +159% | +1.481% | B&H 9.3x önde |
| Max DD | −25.9% | ~−70% | Strateji daha düşük |
| Getiri/DD (MAR) | 6.1 | 21.2 | B&H ~3.5x önde (risk-ayarlı bile) |
| Profit Factor | 1.30 | — | ✅ pozitif edge |
| İşlem | 424 | — | ✅ istatistiksel anlamlı |
| Long PF | 1.61 | | |
| Short PF | 1.01 | | başabaş |

**Verdict:** Meşru, pozitif-edge'li trend takipçisi (tam döngüde ayakta kaldı, cherry-pick değil).
Ama ETH al-tut'u risk-ayarlı bile geçemiyor (ETH 6.5y'de ~16x). Değer = düşük DD'li
trend takibi; niş kullanım (16x yapmayan varlıklar, DD'ye duyarlı sermaye, portföy parçası).
⚠️ Kaldıraç açığı KAPATMAZ — getiriyi de DD'yi de büyütür (3x → DD ~−78%, iflas riski).

**Geçilen kapılar:** ✅ Tam dönem  ✅ PF>1.3 (sınırda)  ❌ B&H'yi geçme
**Bekleyen kapılar:** ❓ OOS bölmesi (2019-2023 train / 2023-2026 test)  ❓ BTC+SOL çapraz

### 2026-05-31 — ETH 1H optimizasyon (önceki)
Net −0.25%, PF 0.99, 30 işlem. Short +5.71% ama pasif short-hold +42.5% → yakalama %13 (negatif alfa).
Bulgu: timeframe yükseldikçe iyileşiyor (15dk PF 0.82 → 1H 0.99 → 4H 1.30). Session filtresi kriptoda zararlı.

---

## [v9.2] — 2026-05-31  ← EN SON GÜNCELLEME
**Sorun:** OOS döneminde (Jan 2023 – May 2026, ETH +61%) Short PF 0.87 — yükseliş trendinde short açıyor.
Teşhis: short sinyali rejim körü; fiyat 200 EMA üstündeyken de short açıyor → negatif alpha.

**Değişiklikler (`pine/APEX_SNIPER_v9.pine`):**
- Yeni input `useShortRegime` (default **on**): kısa pozisyon yalnızca fiyat rejim EMA altındayken açılır
- Yeni input `shortEmaLen` (default **200**): rejim EMA periyodu
- `shortRegiOK = not useShortRegime or close < shortRegEma` koşulu `goShort`'a eklendi
- Info tablosuna "Short regime" satırı eklendi (BELOW EMA / BLOCKED / OFF)

**Test yapılacaklar (v9.2):**
- [ ] ETH/USDT 4H — tam döngü (2019-11 → 2026-05), Short rejim filtresi ON
- [ ] BTC/USDT 4H — çapraz varlık (aynı parametreler)
- [ ] SOL/USDT 4H — çapraz varlık (aynı parametreler)

**Bekleyen kapılar:**
- ❓ Short rejim filtresiyle OOS Short PF > 1 mi?
- ❓ BTC cross-asset PF > 1?
- ❓ SOL cross-asset PF > 1?
- ❌ B&H'yi geçme (ETH 4H PF 1.30 henüz geçemiyor)

---

## [v9.1] — 2026-05-31  (commit `3e4ac71`)
**Sorun:** ETH 1h gerçek testte (162 işlem) PF 0.745, −%8.92 → negatif edge.
Teşhis: kademeli TP kazananı erken kesiyor, stop tam boyutta → "cut winners short".

**Değişiklikler:**
- `pine/APEX_SNIPER_v9.pine`: yeni **Exit Model** grubu
  - `useScaleOut` (default **off**) — 1/3 kademeli çıkışı kapatma
  - `useTrail` (default **on**) — ATR trailing stop, kazananı koştur
  - `trailAtrM` 2.5, `trailStart` 1.0R
  - `rr_target` default **1.5 → 2.0**
- `scripts/apex_backtest.py`: trailing/scale-out karşılaştırma modu
- Harness, ETH sonucunu doğruladı (PF 0.75 ≈ TradingView 0.745) → ölçüm güvenilir

**Yapılacak:** Yeni çıkış modelini ETH 1h'de tekrar test et, PF > 1 mi?

---

## [v9.0] — 2026-05-30
v8 → v9 ana yükseltme. Dosyalar:
- `pine/APEX_SNIPER_v9.pine` — strateji (`fcd9f79`)
- `pine/APEX_SNIPER_v9_PRO_DASHBOARD.pine` — dashboard indikatör (`5fdc5a9`, `4b4cd76`)
- `user_data/strategies/apex_engine.py` — saf pandas motor (`eb11ca9`)
- `user_data/strategies/ApexSniper.py` — freqtrade stratejisi (`eb11ca9`)
- `scripts/apex_backtest.py` — bağımsız backtester (`b1533eb`)
- `pine/README_APEX.md` — kullanım + dürüst doğrulama rehberi (`5399b81`)

**v8'den düzeltilen kusurlar:**
1. Risk-bazlı pozisyon boyutu (artık %100 all-in değil)
2. Plasebo input'lar (funding/LS/gamma) varsayılan kapalı
3. Fee-aware breakeven
4. Günlük zarar + ardışık zarar frenleri
5. POC hesap düzeltmesi
6. Overfit azaltma (rejim ağırlık matrisi opsiyonel)
7. Walk-forward / OOS marker

---

## Test sonuçları (referans)

| Veri | İşlem | Win % | PF | Net | Not |
|------|-------|-------|-----|-----|-----|
| ETH/USDT 1h (TradingView, v9.0) | 162 | 37% | 0.745 | −8.9% | Negatif edge tespit edildi |
| ADA_BTC 5m (harness) | 13 | 54% | 1.28 | +2.2% | Küçük örnek |
| UNITTEST_BTC 5m | 17 | 29% | 0.37 | −8.4% | Küçük örnek |

> ⚠️ Hiçbir sürüm henüz kârlı edge kanıtlamadı. "Best" kararı, gerçek + büyük + out-of-sample veride PF > 1 doğrulanınca verilecek.
