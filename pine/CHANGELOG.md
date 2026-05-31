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
