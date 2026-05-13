# Telegram VIP Payment Bot

Bot Telegram untuk menjual akses group VIP. User membayar lewat QRIS Saweria, lalu bot mengirim link invite group VIP yang dibuat oleh akun Telegram Telethon.

## Platform

Target deploy sekarang adalah Cloudflare Containers. Workers biasa tidak cocok untuk bot ini karena Python + Telethon butuh runtime Linux dan proses polling yang berjalan lama.

Cloudflare Containers berjalan lewat Worker launcher:

- Worker route `/start` atau `/health` akan menyalakan container.
- Cron trigger Worker ping container tiap 30 menit.
- Container menjalankan `python -m vip_payment_bot`.
- Bot Telegram tetap memakai polling.
- Health server internal berjalan di port `8080` untuk readiness container.

## Fitur

- Konfigurasi lewat Cloudflare Worker secrets.
- Admin whitelist lewat `ADMIN_IDS`.
- Payment QRIS Saweria lewat package `qris-saweria`.
- Telethon user session untuk membuat invite link group VIP.
- SQLite lokal untuk menyimpan order selama container berjalan.
- Background poller untuk cek pembayaran.
- Invite link temporary dan single-use.

## Setup Lokal

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Isi `.env`, lalu buat session Telethon:

```powershell
python scripts/create_telethon_session.py
```

Masukkan hasilnya ke `TELETHON_SESSION_STRING`.

Jalankan bot lokal:

```powershell
python -m vip_payment_bot
```

## Environment

Wajib:

- `BOT_TOKEN`: token bot dari BotFather.
- `ADMIN_IDS`: Telegram user id admin, pisahkan koma.
- `TELETHON_API_ID`: API ID dari my.telegram.org.
- `TELETHON_API_HASH`: API hash dari my.telegram.org.
- `TELETHON_SESSION_STRING`: hasil script `scripts/create_telethon_session.py`.
- `VIP_GROUP_ID`: id group VIP, contoh `-1001234567890`.
- `SAWERIA_USERNAME`: username Saweria penerima pembayaran.
- `PAYMENT_AMOUNT`: nominal VIP, contoh `50000`, minimal `1000`.
- `CLOUDFLARE_BOOT_TOKEN`: token random panjang untuk akses `/start` dan `/health`.

Direkomendasikan:

- `PAYMENT_EMAIL`: email donor untuk Saweria.
- `PAYMENT_EXPIRE_MINUTES`: default `30`.
- `PAYMENT_CHECK_INTERVAL_SECONDS`: default `20`.
- `VIP_INVITE_EXPIRE_HOURS`: default `6`, maksimal `24`.
- `VIP_INVITE_USAGE_LIMIT`: wajib `1`, link VIP single-use.
- `DB_PATH`: default lokal `payments.db`; untuk Cloudflare gunakan `/tmp/payments.db`.
- `LOG_LEVEL`: default `INFO`.

## Deploy Cloudflare Containers

Prerequisite:

- Cloudflare Workers Paid plan dengan Containers enabled.
- Docker Desktop aktif saat deploy.
- Node.js dan npm tersedia.

Install dependency Worker:

```powershell
npm install
```

Login Cloudflare:

```powershell
npx wrangler login
```

Set secrets satu per satu:

```powershell
npx wrangler secret put BOT_TOKEN
npx wrangler secret put ADMIN_IDS
npx wrangler secret put TELETHON_API_ID
npx wrangler secret put TELETHON_API_HASH
npx wrangler secret put TELETHON_SESSION_STRING
npx wrangler secret put VIP_GROUP_ID
npx wrangler secret put SAWERIA_USERNAME
npx wrangler secret put PAYMENT_AMOUNT
npx wrangler secret put PAYMENT_EMAIL
npx wrangler secret put PAYMENT_EXPIRE_MINUTES
npx wrangler secret put PAYMENT_CHECK_INTERVAL_SECONDS
npx wrangler secret put VIP_INVITE_EXPIRE_HOURS
npx wrangler secret put VIP_INVITE_USAGE_LIMIT
npx wrangler secret put DB_PATH
npx wrangler secret put LOG_LEVEL
npx wrangler secret put CLOUDFLARE_BOOT_TOKEN
```

Deploy:

```powershell
npm run deploy
```

Setelah deploy, buka endpoint ini untuk menyalakan container:

```powershell
curl.exe -H "x-boot-token: TOKEN_KAMU" https://telegram-payment.NAMA_AKUN.workers.dev/start
```

Cek status container:

```powershell
npx wrangler containers list
```

Worker juga punya cron trigger `*/30 * * * *` untuk menjaga container tetap aktif. Kalau container sempat tidur atau restart, cron berikutnya akan mencoba menyalakannya lagi.

## Perintah Bot

- `/start`: tampilkan tombol pembayaran.
- `/buy`: buat order QRIS.
- `/status`: cek order pending milik user.
- `/admin`: statistik order, hanya untuk admin.

## Catatan Security

Bot hanya membuat order dari private chat supaya QRIS dan invite link tidak tercampur di group publik. Link VIP yang dikirim dibuat lewat Telethon dengan `expire_date` dan `usage_limit=1`.

Akun Telethon harus menjadi admin di group VIP dan punya izin membuat invite link. `VIP_GROUP_ID` bisa didapat dari bot info/chat id helper atau dari log update bot.

SQLite di Cloudflare Container bersifat cocok untuk state pendek selama container aktif. Untuk pembayaran produksi yang harus tahan restart/migrasi container, migrasikan storage order ke database eksternal.
