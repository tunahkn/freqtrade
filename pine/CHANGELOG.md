# APEX SNIPER — CHANGELOG

Repo: `tunahkn/freqtrade` · Branch: `claude/opus-indicator-analysis-p8eqj`
Tüm tarihler UTC. "En iyi sürüm" kararı verilince burası işaretlenecek ve Google Drive'a da kopyalanacak.

---

## Durum: 🧪 TEST AŞAMASI (henüz "best" işaretlenmedi)

| Sürüm | Tarih | Commit | Durum | Not |
|-------|-------|--------|-------|-----|
| v9.1  | 2026-05-31 | `3e4ac71` | 🧪 test | Çıkış modeli düzeltmesi |
| v9.0  | 2026-05-30 | `fcd9f79` | ⬅ eski | İlk v9 |

---

## 📒 DOĞRULAMA GÜNLÜĞÜ

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

## [v9.1] — 2026-05-31  (commit `3e4ac71`)  ← EN SON GÜNCELLEME
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
