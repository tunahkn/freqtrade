"""
botnet - BTC/USDT çoklu bot sistemi.

Sensör botları (trend, orderflow, orderbook, derivatives, liquidity, sentiment) piyasayı
izleyip ortak bir SQLite veritabanına (signals.db) sinyal yazar. Ana beyin
(user_data/strategies/BrainStrategy.py) bu sinyalleri birleştirip LONG/SHORT kararı verir.
learner botu her sensörün isabetini ölçüp ağırlıkları günceller.
"""
