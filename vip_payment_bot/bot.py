from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from .config import Settings, load_settings
from .payments import SaweriaPayments
from .storage import Order, Store
from .telegram_user import TelegramUserClient

logger = logging.getLogger(__name__)


class VipPaymentBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = Store(settings.db_path)
        self.payments = SaweriaPayments(
            settings.saweria_username,
            settings.payment_email,
            settings.saweria_user_id,
            settings.saweria_proxy_url,
        )
        self.telegram_user = TelegramUserClient(settings)
        self.poller_task: asyncio.Task[None] | None = None

    async def post_init(self, application: Application) -> None:
        await self.telegram_user.start()
        self.poller_task = asyncio.create_task(self.payment_poller(application))
        logger.info("Bot ready. Admins=%s", sorted(self.settings.admin_ids))

    async def post_shutdown(self, application: Application) -> None:
        if self.poller_task:
            self.poller_task.cancel()
            try:
                await self.poller_task
            except asyncio.CancelledError:
                pass
        await self.telegram_user.close()
        self.store.close()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None:
            return
        if update.effective_chat is not None and update.effective_chat.type != ChatType.PRIVATE:
            await update.effective_message.reply_text(
                "Pembayaran hanya bisa dibuat lewat chat private dengan bot."
            )
            return
        amount = self._format_rupiah(self.settings.payment_amount)
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"Bayar VIP {amount}", callback_data="buy")]]
        )
        await update.effective_message.reply_text(
            "Pembayaran VIP tersedia lewat QRIS Saweria.\n"
            f"Nominal: <b>{amount}</b>\n\n"
            "Tekan tombol bayar untuk membuat QRIS.",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )

    async def buy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is not None:
            await query.answer()
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return
        if update.effective_chat is not None and update.effective_chat.type != ChatType.PRIVATE:
            await message.reply_text(
                "Pembayaran hanya bisa dibuat lewat chat private dengan bot."
            )
            return

        active = self.store.get_active_order_for_user(user.id)
        if active and not self._is_expired(active):
            await message.reply_text(
                "Kamu masih punya pembayaran pending.\n"
                f"Order: <code>#{active.id}</code>\n"
                "Selesaikan pembayaran itu dulu atau tunggu sampai expired.",
                parse_mode=ParseMode.HTML,
            )
            return

        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=self.settings.payment_expire_minutes
        )
        order = self.store.create_order(
            user_id=user.id,
            username=user.username,
            amount=self.settings.payment_amount,
            expires_at=expires_at.isoformat(),
        )

        try:
            payment = await self.payments.create_payment(
                self.settings.payment_amount,
                f"VIP Telegram order #{order.id} user {user.id}",
            )
        except Exception as exc:
            logger.exception("Failed to create payment for order %s", order.id)
            self.store.mark_failed(order.id, str(exc))
            await message.reply_text(
                "Gagal membuat QRIS. Admin sudah bisa cek log aplikasi."
            )
            return

        try:
            self.store.attach_payment(
                order.id,
                payment.transaction_id,
                payment.payment_url,
                payment.qr_path,
            )
        except Exception as exc:
            logger.exception("Failed to attach payment for order %s", order.id)
            self.store.mark_failed(order.id, str(exc))
            await message.reply_text(
                "Gagal menyimpan transaksi pembayaran. Admin sudah bisa cek log aplikasi."
            )
            return

        caption = (
            f"Order: <code>#{order.id}</code>\n"
            f"Nominal: <b>{self._format_rupiah(order.amount)}</b>\n"
            f"Expired: <b>{self.settings.payment_expire_minutes} menit</b>\n\n"
            "Setelah pembayaran masuk, bot akan otomatis mengirim link group VIP."
        )
        if payment.qr_path:
            with open(payment.qr_path, "rb") as photo:
                await message.reply_photo(photo=photo, caption=caption, parse_mode=ParseMode.HTML)
        elif payment.payment_url:
            await message.reply_text(
                f"{caption}\n\nLink pembayaran:\n{html.escape(payment.payment_url)}",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.reply_text(
                f"{caption}\n\nQRIS berhasil dibuat, tapi file QR tidak ditemukan. Hubungi admin.",
                parse_mode=ParseMode.HTML,
            )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return
        active = self.store.get_active_order_for_user(user.id)
        if not active:
            await message.reply_text("Tidak ada pembayaran pending.")
            return
        await message.reply_text(
            f"Order <code>#{active.id}</code> masih <b>{active.status}</b>.\n"
            f"Nominal: <b>{self._format_rupiah(active.amount)}</b>",
            parse_mode=ParseMode.HTML,
        )

    async def admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return
        if user.id not in self.settings.admin_ids:
            await message.reply_text("Akses ditolak.")
            return
        stats = self.store.stats()
        lines = ["Statistik order:"]
        for status, total in sorted(stats.items()):
            lines.append(f"- {status}: {total}")
        await message.reply_text("\n".join(lines))

    async def payment_poller(self, application: Application) -> None:
        while True:
            try:
                await self._check_pending_orders(application)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Payment poller failed")
            await asyncio.sleep(self.settings.payment_check_interval_seconds)

    async def _check_pending_orders(self, application: Application) -> None:
        for order in self.store.pending_orders():
            if self._is_expired(order):
                self.store.mark_expired(order.id)
                await application.bot.send_message(
                    chat_id=order.user_id,
                    text=f"Order #{order.id} expired. Silakan buat pembayaran baru.",
                )
                continue
            if not order.transaction_id:
                continue
            try:
                paid = await self.payments.is_paid(order.transaction_id)
            except Exception as exc:
                logger.warning("Payment check failed for order %s: %s", order.id, exc)
                self.store.add_event(order.id, "payment_check_error", str(exc))
                continue
            if not paid:
                continue

            try:
                invite_link = await self.telegram_user.create_vip_invite_link(order.id)
            except Exception as exc:
                logger.exception("Failed to create invite link for order %s", order.id)
                self.store.add_event(order.id, "invite_error", str(exc))
                await self._notify_admins(
                    application,
                    f"Pembayaran order #{order.id} sudah paid, tapi gagal membuat link VIP: {exc}",
                )
                continue

            self.store.mark_paid(order.id, invite_link)
            await application.bot.send_message(
                chat_id=order.user_id,
                text=(
                    "Pembayaran terkonfirmasi.\n\n"
                    f"Link group VIP:\n{invite_link}\n\n"
                    f"Expired: {self.settings.vip_invite_expire_hours} jam\n"
                    f"Batas pakai: {self.settings.vip_invite_usage_limit}x"
                ),
            )

    async def _notify_admins(self, application: Application, text: str) -> None:
        for admin_id in self.settings.admin_ids:
            try:
                await application.bot.send_message(chat_id=admin_id, text=text)
            except Exception:
                logger.exception("Failed to notify admin %s", admin_id)

    def _is_expired(self, order: Order) -> bool:
        expires_at = datetime.fromisoformat(order.expires_at)
        return datetime.now(timezone.utc) >= expires_at

    def _format_rupiah(self, amount: int) -> str:
        return f"Rp{amount:,.0f}".replace(",", ".")


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    bot = VipPaymentBot(settings)
    application = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .post_init(bot.post_init)
        .post_shutdown(bot.post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("buy", bot.buy))
    application.add_handler(CommandHandler("status", bot.status))
    application.add_handler(CommandHandler("admin", bot.admin))
    application.add_handler(CallbackQueryHandler(bot.buy, pattern="^buy$"))
    application.run_polling(drop_pending_updates=True)
