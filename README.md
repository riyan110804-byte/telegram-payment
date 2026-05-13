# Telegram VIP Payment Bot

Bot Telegram untuk menjual akses group VIP. User membayar lewat QRIS Saweria, lalu bot mengirim link invite group VIP yang dibuat oleh akun Telegram Telethon.

## Platform

Target deploy sekarang adalah Railway sebagai Python worker. Ini lebih cocok untuk bot Telegram + Telethon karena prosesnya long-running dan tidak perlu Worker launcher atau container wakeup.

## Fitur

- Konfigurasi lewat Railway variables.
- Admin whitelist lewat `ADMIN_IDS`.
- Payment QRIS Saweria lewat package `qris-saweria`, dikirim sebagai QR polos tanpa template.
- Telethon user session untuk membuat invite link group VIP.
- SQLite untuk menyimpan order.
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
- `VIP_GROUP_ID`: id group VIP lengkap, contoh `-1001234567890`. Jika group punya username, bisa pakai username atau link `https://t.me/...`.
- `SAWERIA_USERNAME`: username Saweria penerima pembayaran, isi username saja tanpa `@` dan tanpa URL.
- `PAYMENT_AMOUNT`: nominal VIP, contoh `50000`, minimal `1000`.

Direkomendasikan:

- `PAYMENT_EMAIL`: email donor untuk Saweria.
- `PAYMENT_EXPIRE_MINUTES`: default `30`.
- `PAYMENT_CHECK_INTERVAL_SECONDS`: default `20`, minimal `5`.
- `VIP_INVITE_EXPIRE_HOURS`: default `6`, maksimal `24`. Nilai ini ditampilkan di pesan link VIP.
- `VIP_INVITE_USAGE_LIMIT`: wajib `1`, link VIP single-use.
- `DB_PATH`: default `payments.db`. Untuk Railway persistent volume, gunakan path di mounted volume, misalnya `/data/payments.db`.
- `LOG_LEVEL`: default `INFO`.

## Deploy Railway

1. Push repository ini ke GitHub.
2. Buat project Railway dari repo `nealmtroy/telegram-payment`.
3. Tambahkan semua variable environment di Railway.
4. Jika ingin order tersimpan setelah restart/redeploy, tambahkan Railway Volume dan set `DB_PATH=/data/payments.db`.
5. Deploy. Railway akan menjalankan:

```bash
python -m vip_payment_bot
```

File deploy yang dipakai:

- `Procfile`
- `railway.json`
- `runtime.txt`
- `requirements.txt`

## Perintah Bot

- `/start`: tampilkan tombol pembayaran.
- `/buy`: buat order QRIS.
- `/status`: cek order pending milik user.
- `/admin`: statistik order, hanya untuk admin.

## Catatan Security

Bot hanya membuat order dari private chat supaya QRIS dan invite link tidak tercampur di group publik. Link VIP yang dikirim dibuat lewat Telethon dengan `expire_date` dan `usage_limit=1`.

Akun Telethon harus menjadi admin di group VIP dan punya izin membuat invite link. `VIP_GROUP_ID` bisa didapat dari bot info/chat id helper atau dari log update bot.

Jika muncul error Telethon `Could not find the input entity`, akun Telethon belum pernah melihat group itu atau `VIP_GROUP_ID` salah. Pastikan akun Telethon sudah join group VIP, sudah admin, lalu pakai id lengkap `-100...` atau username/link group.

Jangan kirim log yang berisi URL `api.telegram.org/bot...` ke publik karena itu memuat `BOT_TOKEN`. Jika token pernah terlihat di log publik/chat, rotate token lewat BotFather.
