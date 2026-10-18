# BTC Çoklu Bot Sistemi: VPS Kurulumu

Bu rehber botları kiralık bir Linux sunucuda (VPS) 7/24 çalıştırır.
Önerilen sunucu: Hetzner CX22 veya DigitalOcean 2 GB RAM, Ubuntu 24.04 (ayda ~5–12 $).

## 1. Sunucuyu hazırla

```bash
ssh root@SUNUCU_IP
apt update && apt -y upgrade
curl -fsSL https://get.docker.com | sh
# Saat senkronu: borsa imzaları için şart
timedatectl set-ntp true
```

## 2. Depoyu çek

```bash
git clone -b claude/upbeat-babbage-wxvkuk https://github.com/tunahkn/freqtrade.git
cd freqtrade
cp .env.example .env
nano .env    # Telegram token / chat id ve API şifrelerini doldur
```

Dry-run (sahte para) için OKX anahtarı **gerekmez**. Canlıya geçerken:
- OKX > API: yetki **sadece Trade**. **Withdraw (para çekme) kapalı.**
- IP kısıtlaması: VPS'in IP adresi.
- Hesap modu: **Single-currency margin** veya **Multi-currency margin**. Pozisyon modu: **One-way** (net mode).

## 3. Gerçek veriyle backtest (canlıdan ÖNCE)

```bash
docker compose -f docker-compose.botnet.yml run --rm brain download-data \
  --config /freqtrade/botnet/config/config_okx_futures.json \
  --timeframes 15m 1h 4h --days 400 --trading-mode futures

docker compose -f docker-compose.botnet.yml run --rm brain backtesting \
  --config /freqtrade/botnet/config/config_okx_futures.json \
  --strategy BrainStrategy --enable-protections --breakdown month

docker compose -f docker-compose.botnet.yml run --rm brain lookahead-analysis \
  --config /freqtrade/botnet/config/config_okx_futures.json --strategy BrainStrategy
```

Not: backtest sadece **teknik çekirdeği** ölçer. Sensör verisinin geçmişi yoktur;
sensörlerin katkısı dry-run sırasında toplanan `signals.db` ile ölçülür (learner raporu).

## 4. Başlat

```bash
docker compose -f docker-compose.botnet.yml up -d
docker compose -f docker-compose.botnet.yml ps          # 8 servis "Up" olmalı
docker compose -f docker-compose.botnet.yml logs -f brain
docker compose -f docker-compose.botnet.yml logs -f liquidity orderflow
```

Sinyalleri kontrol et:

```bash
sqlite3 user_data/botnet/signals.db \
  "SELECT bot, datetime(ts,'unixepoch'), round(score,2), round(confidence,2), tags
   FROM signals ORDER BY ts DESC LIMIT 20;"
```

## 5. Telegram komutları

`/status` açık işlem · `/profit` kâr · `/daily` günlük · `/balance` bakiye ·
`/stop` yeni işlem açmayı durdur · `/start` devam · `/forceexit all` hepsini kapat

Learner her gün bot ağırlık raporunu da Telegram'a gönderir.

## 6. Haftalık iyileştirme (hyperopt)

Pazar gecesi teknik çekirdeğin parametrelerini son 90 güne göre yeniden optimize et:

```bash
crontab -e
# Her pazar 03:00
0 3 * * 0 cd /root/freqtrade && docker compose -f docker-compose.botnet.yml run --rm brain download-data --config /freqtrade/botnet/config/config_okx_futures.json --timeframes 15m 1h 4h --days 120 --trading-mode futures && docker compose -f docker-compose.botnet.yml run --rm brain hyperopt --config /freqtrade/botnet/config/config_okx_futures.json --strategy BrainStrategy --hyperopt-loss SharpeHyperOptLossDaily --spaces buy sell --epochs 300 --timerange $(date -d '-90 days' +\%Y\%m\%d)- >> user_data/logs/hyperopt.log 2>&1
```

Hyperopt sonucu `user_data/strategies/BrainStrategy.json` dosyasına yazılır ve bot
yeniden başlayınca kullanılır. **Önce sonucu incele**; aşırı uydurma (overfit) riskine karşı
çok az işlemle yüksek kâr gösteren sonuçları reddet.

## 7. Güncelleme

```bash
cd /root/freqtrade
git pull
docker compose -f docker-compose.botnet.yml up -d --force-recreate
```

## 8. Canlıya geçiş kontrol listesi

- [ ] En az 2 hafta dry-run, sonuçlar `/profit` ile incelendi
- [ ] Learner raporlarında en az 2 sensörün isabeti %55 üstünde
- [ ] Gerçek veriyle backtest drawdown < %20
- [ ] OKX API: withdraw kapalı, IP kısıtlı
- [ ] `.env` içinde `FREQTRADE__DRY_RUN=false`, küçük bakiye ile başla
- [ ] Kaldıraç en fazla 2x (`BrainStrategy.MAX_LEVERAGE`)
