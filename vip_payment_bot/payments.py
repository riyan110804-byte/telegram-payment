from __future__ import annotations

import asyncio
import random
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PaymentRequest:
    transaction_id: str | None
    payment_url: str | None
    qr_path: str | None
    raw: Any


@dataclass(frozen=True)
class PaymentStatus:
    paid: bool
    status: str | None
    raw: Any


class PaymentGatewayError(RuntimeError):
    def __init__(
        self,
        user_message: str,
        admin_message: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(admin_message)
        self.user_message = user_message
        self.admin_message = admin_message
        self.status_code = status_code


class SaweriaPayments:
    def __init__(
        self,
        username: str,
        email: str,
        user_id: str,
        maelyn_api_key: str,
        maelyn_base_url: str,
    ) -> None:
        self.username = username
        self.email = email
        self.user_id = user_id
        self.maelyn_api_key = maelyn_api_key
        self.maelyn_base_url = maelyn_base_url.rstrip("/")
        self._session: Any | None = None

    async def create_payment(self, amount: int, message: str) -> PaymentRequest:
        return await asyncio.to_thread(self._create_payment_sync, amount, message)

    async def is_paid(self, transaction_id: str) -> bool:
        status = await self.get_status(transaction_id)
        return status.paid

    async def get_status(self, transaction_id: str) -> PaymentStatus:
        return await asyncio.to_thread(self._get_status_sync, transaction_id)

    def _create_payment_sync(self, amount: int, message: str) -> PaymentRequest:
        self._check_account()
        sender = self._random_sender()
        payload = {
            "user_id": self.user_id,
            "amount": int(amount),
            "name": sender,
            "email": self._email_with_tag(sender),
            "msg": message[:250],
        }
        response = self._request(
            "POST",
            "/payment/saweria/create/transaction",
            json=payload,
            timeout=30,
        )
        raw = self._read_json(response)
        if not response.ok or not raw.get("success"):
            self._raise_gateway_error(
                response,
                raw,
                "Saweria create payment via Maelyn gagal",
                "QRIS gagal dibuat karena gateway pembayaran sedang gagal. Order dibatalkan, jangan transfer dulu.",
            )

        transaction_id = self._find_first(raw, {"id", "payment_id", "paymentId", "transaction_id"})
        payment_url = self._find_first(raw, {"payment_url", "paymentUrl", "url"})
        qr_string = self._find_first(raw, {"qr_string", "qrString", "qris", "qr"})
        if not transaction_id:
            raise RuntimeError(
                "Response Maelyn tidak mengandung id transaksi. "
                f"{self._response_debug(response, raw)}"
            )
        if not qr_string and not payment_url:
            raise RuntimeError(
                "Response Maelyn tidak mengandung qr_string atau payment_url. "
                f"{self._response_debug(response, raw)}"
            )

        qr_path = self._generate_qr_image(str(qr_string or payment_url), transaction_id)
        return PaymentRequest(
            transaction_id=str(transaction_id),
            payment_url=str(payment_url) if payment_url else None,
            qr_path=qr_path,
            raw=raw,
        )

    def _get_status_sync(self, transaction_id: str) -> PaymentStatus:
        response = self._request(
            "GET",
            "/payment/saweria/check/transaction",
            params={"user_id": self.user_id, "payment_id": transaction_id},
            timeout=30,
        )
        raw = self._read_json(response)
        if not response.ok or not raw.get("success"):
            self._raise_gateway_error(
                response,
                raw,
                "Saweria check payment via Maelyn gagal",
                "Status pembayaran belum bisa dicek dari gateway.",
            )

        status = self._find_first(raw, {"status", "state"})
        paid_flag = self._find_first(raw, {"paid", "is_paid", "isPaid"})
        paid = self._is_paid_value(status) or paid_flag is True
        return PaymentStatus(paid=paid, status=str(status) if status else None, raw=raw)

    def _check_account(self) -> None:
        response = self._request(
            "GET",
            "/payment/saweria/check/account",
            params={"username": self.username},
            timeout=30,
        )
        raw = self._read_json(response)
        if not response.ok or not raw.get("success"):
            self._raise_gateway_error(
                response,
                raw,
                "Saweria check account via Maelyn gagal",
                "Akun Saweria belum bisa diverifikasi dari gateway.",
            )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        session = self._requests_session()
        url = f"{self.maelyn_base_url}{path}"
        headers = kwargs.pop("headers", {})
        headers.update(self._maelyn_headers())
        attempts = 4
        last_response: Any | None = None
        for attempt in range(1, attempts + 1):
            response = session.request(method, url, headers=headers, **kwargs)
            last_response = response
            if response.ok or response.status_code not in self._retryable_statuses() or attempt == attempts:
                return response
            delay = min(6.0, (0.8 * attempt) + random.uniform(0.2, 0.8))
            time.sleep(delay)
        return last_response

    def _requests_session(self) -> Any:
        if self._session is not None:
            return self._session
        import requests

        self._session = requests.Session()
        return self._session

    def _maelyn_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "telegram-vip-payment-bot/1.0",
            "x-maelyn-auth": self.maelyn_api_key,
        }

    def _retryable_statuses(self) -> set[int]:
        return {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}

    def _read_json(self, response: Any) -> dict[str, Any]:
        try:
            data = response.json()
        except Exception:
            return {}
        if isinstance(data, dict):
            return data
        return {"data": data}

    def _raise_gateway_error(
        self,
        response: Any,
        raw: dict[str, Any],
        admin_prefix: str,
        user_message: str,
    ) -> None:
        message = raw.get("message")
        detail = f" message={self._compact_text(str(message))}" if message else ""
        raise PaymentGatewayError(
            user_message=user_message,
            admin_message=(
                f"{admin_prefix}: HTTP {response.status_code}.{detail} "
                f"{self._response_debug(response, raw)}"
            ),
            status_code=response.status_code,
        )

    def _response_debug(self, response: Any, raw: dict[str, Any] | None = None) -> str:
        content_type = response.headers.get("content-type", "-")
        server = response.headers.get("server")
        pieces = [f"content-type={content_type}"]
        if server:
            pieces.append(f"server={server}")
        if raw:
            pieces.append(f"body={self._compact_text(str(raw))[:700]}")
        else:
            pieces.append(f"body={self._compact_text(response.text)[:700]}")
        return " ".join(pieces)

    def _compact_text(self, text: str) -> str:
        text = re.sub(r"<script\b.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:1000]

    def _find_first(self, value: Any, keys: set[str]) -> Any:
        if isinstance(value, dict):
            for key in keys:
                if value.get(key):
                    return value[key]
            for item in value.values():
                found = self._find_first(item, keys)
                if found:
                    return found
        if isinstance(value, list):
            for item in value:
                found = self._find_first(item, keys)
                if found:
                    return found
        return None

    def _is_paid_value(self, value: Any) -> bool:
        normalized = str(value or "").strip().upper()
        return normalized in {"PAID", "SUCCESS", "SUCCEEDED", "COMPLETED", "SETTLED"}

    def _generate_qr_image(self, value: str, transaction_id: Any) -> str:
        import qrcode

        safe_id = "".join(
            character if character.isalnum() else "-"
            for character in str(transaction_id).lower()
        ).strip("-")
        target = Path(tempfile.gettempdir()) / f"qris-{safe_id or 'payment'}.png"
        image = qrcode.make(value)
        image.save(str(target))
        return str(target)

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
