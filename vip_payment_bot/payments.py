from __future__ import annotations

import asyncio
import random
import string
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
    def __init__(
        self,
        username: str,
        email: str,
        user_id: str | None = None,
        proxy_url: str | None = None,
    ) -> None:
        self.username = username
        self.email = email
        self.user_id = user_id
        self.proxy_url = proxy_url

    async def create_payment(self, amount: int, message: str) -> PaymentRequest:
        return await asyncio.to_thread(self._create_payment_sync, amount, message)

    async def is_paid(self, transaction_id: str) -> bool:
        return await asyncio.to_thread(self._is_paid_sync, transaction_id)

    def _create_payment_sync(self, amount: int, message: str) -> PaymentRequest:
        if self.user_id:
            return self._create_payment_with_user_id(amount, message)

        from qris_saweria import create_payment_qr

        self._validate_saweria_profile()
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
                    f"Akun Saweria '{self.username}' tidak ditemukan dari server. "
                    "Pastikan SAWERIA_USERNAME isi username saja. Jika username sudah benar, "
                    "kemungkinan Saweria memberi response berbeda ke IP hosting."
                ) from exc
            raise
        return PaymentRequest(
            transaction_id=transaction_id,
            payment_url=None,
            qr_path=qr_path,
            raw={"qr_string": qr_string, "transaction_id": transaction_id},
        )

    def _is_paid_sync(self, transaction_id: str) -> bool:
        if self.user_id:
            return self._check_paid_status(transaction_id)

        from qris_saweria import check_paid_status

        return bool(check_paid_status(transaction_id))

    def _create_payment_with_user_id(self, amount: int, message: str) -> PaymentRequest:
        import requests
        from qris_saweria import generate_qr_image

        safe_message = "".join(
            character if character.isalnum() else "-"
            for character in message.lower()
        ).strip("-")
        target = Path(tempfile.gettempdir()) / f"qris-{safe_message or 'payment'}.png"
        sender = self._random_sender()
        payload = {
            "agree": True,
            "notUnderage": True,
            "message": message[:250],
            "amount": int(amount),
            "payment_type": "qris",
            "vote": "",
            "currency": "IDR",
            "customer_info": {
                "first_name": sender,
                "email": self._email_with_tag(sender),
                "phone": "",
            },
        }
        response = requests.post(
            f"https://backend.saweria.co/donations/{self.user_id}",
            json=payload,
            headers=self._saweria_headers(),
            proxies=self._proxies(),
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(
                f"Gagal membuat pembayaran Saweria: HTTP {response.status_code}. "
                "Jika berjalan di Railway, kemungkinan IP hosting diblok Saweria. "
                "Set SAWERIA_PROXY_URL atau gunakan hosting lain."
            )
        data = response.json().get("data", {})
        qr_string = data.get("qr_string")
        transaction_id = data.get("id")
        if not qr_string or not transaction_id:
            raise RuntimeError("Response Saweria tidak mengandung qr_string atau id transaksi.")
        qr_path = generate_qr_image(qr_string, str(target), template_path=None)
        return PaymentRequest(
            transaction_id=transaction_id,
            payment_url=None,
            qr_path=qr_path,
            raw={"qr_string": qr_string, "transaction_id": transaction_id},
        )

    def _check_paid_status(self, transaction_id: str) -> bool:
        import requests

        response = requests.get(
            f"https://backend.saweria.co/donations/qris/{transaction_id}",
            headers=self._saweria_headers(),
            proxies=self._proxies(),
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(
                f"Gagal cek status Saweria: HTTP {response.status_code}."
            )
        data = response.json().get("data", {})
        return data.get("qr_string") == ""

    def _validate_saweria_profile(self) -> None:
        import json
        import re
        import requests

        url = f"https://saweria.co/{self.username}"
        response = requests.get(
            url,
            headers=self._saweria_headers(),
            proxies=self._proxies(),
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(
                f"Saweria profile '{self.username}' gagal dibuka: HTTP {response.status_code}."
            )

        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            response.text,
            re.DOTALL,
        )
        if not match:
            raise RuntimeError(
                f"Saweria profile '{self.username}' tidak mengandung __NEXT_DATA__. "
                "Kemungkinan response Saweria dari IP hosting berbeda atau diblok."
            )

        data = json.loads(match.group(1))
        profile = data.get("props", {}).get("pageProps", {}).get("data", {})
        if not profile.get("id"):
            raise RuntimeError(
                f"Saweria profile '{self.username}' terbuka, tapi user id tidak ditemukan."
            )

    def _saweria_headers(self) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://saweria.co",
            "Referer": f"https://saweria.co/{self.username}",
        }

    def _proxies(self) -> dict[str, str] | None:
        if not self.proxy_url:
            return None
        return {"http": self.proxy_url, "https": self.proxy_url}

    def _random_sender(self) -> str:
        names = [
            "Ahmad",
            "Budi",
            "Dedi",
            "Rizky",
            "Fajar",
            "Bayu",
            "Doni",
            "Hendra",
            "Aji",
            "Rama",
            "Dewi",
            "Sari",
            "Maya",
            "Rina",
            "Nina",
            "Lina",
        ]
        return random.choice(names)

    def _email_with_tag(self, tag: str) -> str:
        normalized_name = "".join(
            character.lower() for character in tag if character.isalnum()
        ) or "user"
        number = random.randint(1000, 9999)
        return f"{normalized_name}{number}@gmail.com"
