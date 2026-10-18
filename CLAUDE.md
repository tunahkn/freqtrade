# CLAUDE.md

Bu depo Freqtrade'in bir kopyası. Üzerine kullanıcıya özel **BTC/USDT çoklu bot trading
sistemi** kuruldu. Kullanıcı Türkçe konuşur, net ve kararlı cevap ister.

## BTC trader sistemi
- Ayrıntılı rehber ve "yeni bot ekleme" tarifi: `.claude/skills/btc-trader/SKILL.md`
- Geçmiş hatalar / dersler: `docs/btc-trader/LESSONS.md` (her işe başlamadan oku, yeni dersleri ekle)
- VPS kurulumu: `docs/btc-trader/DEPLOY.md`

Kısaca: `botnet/sensors/` altındaki 6 bot (trend, orderflow, orderbook, derivatives,
liquidity, sentiment) `user_data/botnet/signals.db`'ye sinyal yazar. Ana beyin
`user_data/strategies/BrainStrategy.py` (OKX futures, long+short, max 2x) bu sinyalleri
`botnet/brain/decision.py` ile birleştirip işlem açar. `botnet/learner/` her gün bot
ağırlıklarını isabetlerine göre günceller.

## Komutlar
```bash
python3 -m pytest botnet/tests -q -p no:cacheprovider -c /dev/null --rootdir .
ruff check botnet user_data/strategies/BrainStrategy.py
python3 -m botnet.tools.offline_backtest . backtesting
```

## Ortam notları
- Claude bulut oturumunda OKX'e ağ erişimi yok; canlı veri ve gerçek backtest VPS'te yapılır.
- Freqtrade çekirdeğine (`freqtrade/`) dokunma; sistem tamamen `botnet/` + strateji üzerinden genişler.
