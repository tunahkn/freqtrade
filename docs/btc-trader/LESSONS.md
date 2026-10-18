# Öğrenilen Dersler

Her önemli bulgu buraya tarih ile eklenir. Claude yeni oturumda bu dosyayı okur ve aynı
hatayı tekrarlamaz. En yeni üstte.

## 2026-10-18: İlk kurulum

- **15m ATR ile stop çok dar.** Takip eden stop 2 × ATR(15m) olunca sentetik testte günde
  ~9 işlem ve %70 trailing-stop kaybı çıktı. Çözüm: stop ve pozisyon boyutu ATR(1h) ile,
  stop önce giriş fiyatına sabit, fiyat 1 ATR kâra geçince takip başlıyor.
- **Her mumda tekrar giriş.** Trend boyunca skor eşiğin üstünde kaldıkça her stop sonrası
  yeniden giriliyordu. Çözüm: sadece eşiğin *yeni kesildiği* mumda gir.
- **Backtest'te koruma kuralları varsayılan kapalı.** `--enable-protections` vermeden
  StoplossGuard / MaxDrawdown çalışmaz; her backtest komutunda kullan.
- **Ortam:** Claude bulut oturumundan OKX'e erişim kapalı (proxy 403). Strateji testleri
  için `python -m botnet.tools.offline_backtest . backtesting|lookahead` kullan; sonuçların
  kâr değeri yoktur, sadece mekaniği doğrular.
- **Python 3.13 + bu Freqtrade sürümü:** `requirements.txt` sabit sürümleri kurulmaz
  (pandas-ta, TA-Lib, sdnotify). Yerelde test için sürümsüz binary paketler + `pandas<3`
  yeterli; asıl çalışma ortamı Docker imajıdır.
