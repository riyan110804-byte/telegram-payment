from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PaymentRequest:
    transaction_id: str | None
    payment_url: str | None
    qr_path: str | None
    raw: Any


class SaweriaPayments:
    def __init__(self, username: str, email: str) -> None:
        self.username = username
        self.email = email

    async def create_payment(self, amount: int, message: str) -> PaymentRequest:
        return await asyncio.to_thread(self._create_payment_sync, amount, message)

    async def is_paid(self, transaction_id: str) -> bool:
        return await asyncio.to_thread(self._is_paid_sync, transaction_id)

    def _create_payment_sync(self, amount: int, message: str) -> PaymentRequest:
        from qris_saweria import create_payment_qr

        safe_message = "".join(
            character if character.isalnum() else "-"
            for character in message.lower()
        ).strip("-")
        target = Path(tempfile.gettempdir()) / f"qris-{safe_message or 'payment'}.png"
        try:
            qr_string, transaction_id, qr_path = create_payment_qr(
                self.username,
                amount,
                self.email,
                output_path=str(target),
                use_template=False,
            )
        except Exception as exc:
            if "Saweria account not found" in str(exc):
                raise RuntimeError(
                    "Akun Saweria tidak ditemukan. Isi SAWERIA_USERNAME dengan username "
                    "Saweria saja, tanpa @ dan tanpa URL."
                ) from exc
            raise
        return PaymentRequest(
            transaction_id=transaction_id,
            payment_url=None,
            qr_path=qr_path,
            raw={"qr_string": qr_string, "transaction_id": transaction_id},
        )

    def _is_paid_sync(self, transaction_id: str) -> bool:
        from qris_saweria import check_paid_status

        return bool(check_paid_status(transaction_id))
