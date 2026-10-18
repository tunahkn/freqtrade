---
name: btc-trader
description: BTC/USDT çoklu bot trading sistemi (botnet + BrainStrategy, OKX Futures) üzerinde çalışırken kullan - yeni sensör bot eklemek, beynin karar mantığını değiştirmek, stratejiyi test etmek, learner/ağırlıkları incelemek, VPS kurulumu veya sinyal/işlem analizi. Tetikleyiciler - "bot ekle", "sensör", "beyin", "long/short sinyal", "balina", "likidite", "backtest", "BrainStrategy", "signals.db", "learner".
---

# BTC Trader Sistemi

Kullanıcı Türkçe konuşur ve net, kararlı cevap ister. Botlar VPS'te Docker ile 7/24 çalışır;
Claude geliştirme, test ve analiz yapar.

## Önce oku
1. `docs/btc-trader/LESSONS.md`: geçmiş hatalar, tekrarlama.
2. `CLAUDE.md`: mimari özeti.

## Mimari
```
sensör botları ──> user_data/botnet/signals.db (SQLite WAL) ──> BrainStrategy (Freqtrade) ──> OKX
                                   └──> learner ──> user_data/botnet/weights.json ──┘
```
- `botnet/common/bus.py`: `Signal(bot, score[-1..1], confidence[0..1], price, tags, details, ts, ttl)`, `Bus.publish/latest/history`
- `botnet/common/okx.py`: OKX public REST (anahtarsız). `BTC-USDT-SWAP` 1 kontrat = 0.01 BTC
- `botnet/common/indicators.py`: saf pandas EMA/RSI/ATR/MACD, `trend_score_frame`, `market_structure`
- `botnet/sensors/*.py`: her sensörde saf `analyze(...) -> Signal` + `XxxSensor.step()` + `main()`
- `botnet/brain/decision.py`: `fuse()` ağırlıklı füzyon, `decide()` LONG/SHORT/None ve entry_tag
- `botnet/learner/learner.py`: isabet ölçümü, ağırlık güncelleme (max %35, min %3, adım ±0.10, ≥30 örnek)
- `user_data/strategies/BrainStrategy.py`: teknik çekirdek (backtest edilebilir) + canlıda sensör füzyonu
- `botnet/config/config_okx_futures.json`, `docker-compose.botnet.yml`, `.env.example`

## Yeni sensör bot ekleme (tarif)
1. `botnet/sensors/<ad>.py` oluştur: `NAME`, saf `analyze(veri) -> Signal`, `<Ad>Sensor` (`name`, `interval`, `step()`), `main()` içinde `run_forever(...)`.
2. Skor kuralı: + = LONG, − = SHORT. Güven 0.2–0.9 arası; gürültülü veride düşük tut.
3. Önemli olayları kısa `tags` olarak yaz; beyin entry_tag'e eklemek isterse `decision._TAG_SHORT`'a ekle.
4. `decision.DEFAULT_WEIGHTS`'e başlangıç ağırlığı ekle (toplam ~1).
5. `docker-compose.botnet.yml`'a `<<: *sensor` ile servis ekle.
6. `botnet/tests/test_sensors.py`'a sentetik veriyle test yaz.
7. Doğrula (aşağıda), `LESSONS.md`'ye not düş, commit + push.

## Doğrulama komutları
```bash
python3 -m pytest botnet/tests -q -p no:cacheprovider -c /dev/null --rootdir .
ruff check botnet user_data/strategies/BrainStrategy.py && ruff format --check botnet user_data/strategies/BrainStrategy.py
python3 -m botnet.tools.offline_backtest . backtesting   # mekanik kontrol (sentetik veri)
python3 -m botnet.tools.offline_backtest . lookahead     # geleceği görme hatası
```
Freqtrade kurulu değilse: `pip install -e . --no-deps` + sürümsüz binary bağımlılıklar + `"pandas<3"` (bkz. LESSONS.md).

## Kurallar
- Kaldıraç en fazla 2x, işlem başına risk %1, tek pozisyon. Kullanıcı açıkça istemeden gevşetme.
- Sentetik backtest kârını asla "strateji kazandırıyor" diye sunma.
- API anahtarı / `.env` asla commit edilmez.
- Kâr garantisi verme; sinyaller olasılıktır.
- Geliştirme dalı: `claude/upbeat-babbage-wxvkuk`.
