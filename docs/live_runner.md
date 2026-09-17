# Sürekli koşu: cron yerine uzun ömürlü süreç

`scripts/live_loop.py`, turu bar kapanışlarında kendisi tetikler. GitHub Actions cron'unun
yerine geçer; **ölçümün hiçbir kuralına dokunmaz** — aynı `main.py`, aynı çekirdek, aynı
defter.

## Neden

Tetikleyici ölçüldü ve güvenilmez çıktı (karar 39):

| | Ölçülen |
|---|---|
| 15 dakikalık cron (scalp) | tetiklemelerin **~%91'i düşüyordu** |
| 4 saatlik katman (base) | barların **%26'sı sinyalsiz** geçiyordu, kaybolan bar hep 00:00/08:00 — yani kayıp gürültü değil YANLILIK |
| Gecikme | 2.6 saate varan |

`signals_per_bar` bu kaybı ölçüm tarafında telafi eder; sürekli süreç **sebebini** kapatır.
İkisi birbirinin alternatifi değildir: telafi, süreç çökse de barın kaybolmamasını sağlar.

## Neden websocket değil

İstenen "sürekli süreç + websocket"ti. Birincisi burada; ikincisi **bilinçli olarak yok**:

- **Kural 12:** `MarketData` yalnızca KAPANMIŞ barları içerir. Kapanmamış bardan gelen bir
  tick ölçüme giremez — girerse tüm sonuçlar geçersiz olur.
- **Kural 13:** dolum bir SONRAKİ barın açılışındadır. Bar kapanışını 3 saniye önce
  öğrenmek hiçbir dolum fiyatını değiştirmez.
- **Kural 5:** iki modelin aynı barda aynı veriyi görmesi gerekir. Websocket ikinci bir
  veri yolu demektir; REST önbelleğiyle ayrıştığı an bu kural sessizce delinir.

Yani websocket'in bu mimaride yapabileceği tek iş "bar kapandı" saatini duyurmaktır ve
süreç onu zaten takvimden bilir. Karşılığında yeni bir bağımlılık, yeniden bağlanma durumu
ve ikinci bir veri yolu getirirdi.

**Gerçek işlem emri gönderen bir katman eklenirse bu karar yeniden açılır:** orada
websocket'in işi fiyat değil, EMİR DURUMUDUR (dolum teyidi, kısmi dolum, red) ve onun REST
karşılığı yoktur.

## Çalıştırma

```bash
# 15 dakikalık katman, bar kapanışından 20 sn sonra, commit + push + bildirim ile
python scripts/live_loop.py --layer scalp

# ilk turu beklemeden koş, tek tur sonra çık (duman testi)
python scripts/live_loop.py --layer scalp --now --max-rounds 1 --no-commit --no-notify
```

| Bayrak | Anlamı |
|---|---|
| `--settle-seconds` | bar kapanışından sonra beklenen süre (varsayılan 20) |
| `--now` | ilk turu bar beklemeden koşar |
| `--max-rounds` | 0 = sınırsız |
| `--no-commit` / `--no-push` | defteri commit etme / commit et ama push etme |
| `--no-notify` | Telegram adımını atla |
| `--timeout` | tek bir adımın azami süresi (varsayılan 900 sn) |

Turlar **asla üst üste binmez**: döngü tek iş parçacıklıdır. Gecikmiş bir tur bir barı
kaçırırsa motor onu telafi eder ve kayıp `missing_bars` olarak sayılır — sessiz kalmaz.

## systemd birimi

```ini
# /etc/systemd/system/crypto-paper-bot.service
[Unit]
Description=crypto-paper-bot scalp katmanı (sürekli koşu)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=bot
WorkingDirectory=/opt/crypto-paper-bot
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/etc/crypto-paper-bot.env      # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
ExecStart=/opt/crypto-paper-bot/.venv/bin/python scripts/live_loop.py --layer scalp
Restart=always
RestartSec=30
# SIGTERM turu YARIDA KESMEZ: süreç bayrağı alır, içinde bulunulan tur biter ve çıkar.
KillSignal=SIGTERM
TimeoutStopSec=900

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now crypto-paper-bot
journalctl -u crypto-paper-bot -f
```

Push için makinede `git` kimliği (deploy key ya da token) kurulu olmalıdır; kurulu değilse
`--no-push` ile koşulur ve defter yalnızca yerelde birikir.

## Cron ile birlikte çalışmaz

Aynı defteri iki tetikleyici besleyemez: sürekli süreç devreye alınınca ilgili workflow'un
cron'u kaldırılır (ya da `workflow_dispatch`a düşürülür). İkisi birden koşarsa iki tur aynı
barı işlemeye çalışır; defter append-only olduğu için veri bozulmaz ama commit'ler
çakışır ve `last_processed_bar` yarışır.

## İzlenecek şey: kesiciler

`vwap_guarded` (model 18) iki risk kesicisi taşır ve ikisi de **loga `WARNING` yazar**:

```
vwap_guarded: günlük zarar limiti — 2026-09-17 gününde gerçekleşen R -2.30 ≤ -2.00
vwap_guarded: KILL-SWITCH — zirveden düşüş 8.40R ≥ 8.00R (≈ sermayenin %8'i)
```

Kesici tetiklendiğinde model **yeni kurulum aramayı bırakır**, açık pozisyonların
yönetimi sürer. Drawdown kesicisi pratikte kalıcıdır: durmuş bir model kendini
toparlayamaz. Yeniden açmak bir insan kararıdır ve bu depoda bir commit'tir — sayaç
defterin saf bir fonksiyonu olduğu için "sıfırlama" diye bir işlem yoktur.

Kesicinin tetiklendiği tur raporda da görünür: `round.models[].survey` altında
`kesici_gunluk_zarar` / `kesici_drawdown`.
