from __future__ import annotations

import asyncio
import random
import re
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
        user_id: str | None = None,
        proxy_url: str | None = None,
        use_cloudscraper: bool = False,
    ) -> None:
        self.username = username
        self.email = email
        self.user_id = user_id
        self.proxy_url = proxy_url
        self.use_cloudscraper = use_cloudscraper
        self._session: Any | None = None

    async def create_payment(self, amount: int, message: str) -> PaymentRequest:
        return await asyncio.to_thread(self._create_payment_sync, amount, message)

    async def is_paid(self, transaction_id: str) -> bool:
        return await asyncio.to_thread(self._is_paid_sync, transaction_id)

    def _create_payment_sync(self, amount: int, message: str) -> PaymentRequest:
        if self.user_id:
            return self._create_payment_with_user_id(amount, message)
        if self.use_cloudscraper:
            user_id = self._validate_saweria_profile()
            return self._create_payment_with_user_id(amount, message, user_id)

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

    def _create_payment_with_user_id(
        self,
        amount: int,
        message: str,
        user_id: str | None = None,
    ) -> PaymentRequest:
        from qris_saweria import generate_qr_image

        target_user_id = user_id or self.user_id
        if not target_user_id:
            raise RuntimeError("SAWERIA_USER_ID tidak tersedia untuk membuat pembayaran.")

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
            "amount": str(int(amount)),
            "payment_type": "qris",
            "vote": "",
            "currency": "IDR",
            "customer_info": {
                "first_name": sender,
                "email": self._email_with_tag(sender),
                "phone": "",
            },
        }
        response = self._request(
            "POST",
            f"https://backend.saweria.co/donations/snap/{target_user_id}",
            json=payload,
            headers=self._saweria_headers(),
            timeout=30,
        )
        if not response.ok:
            reason = self._response_debug(response)
            raise PaymentGatewayError(
                user_message=(
                    "QRIS gagal dibuat karena gateway pembayaran sedang menolak "
                    "request server. Order dibatalkan, jangan transfer dulu."
                ),
                admin_message=(
                    f"Saweria create payment gagal: HTTP {response.status_code}. "
                    f"{reason} "
                    "Kemungkinan IP hosting diblok Saweria. Set SAWERIA_PROXY_URL "
                    "atau gunakan hosting/IP lain."
                ),
                status_code=response.status_code,
            )
        raw = response.json()
        qr_string = self._find_first(raw, {"qr_string", "qrString"})
        transaction_id = self._find_first(raw, {"id", "transaction_id", "transactionId"})
        if not qr_string or not transaction_id:
            reason = self._response_debug(response)
            raise RuntimeError(
                "Response Saweria tidak mengandung qr_string atau id transaksi. "
                f"{reason}"
            )
        qr_path = generate_qr_image(qr_string, str(target), template_path=None)
        return PaymentRequest(
            transaction_id=str(transaction_id),
            payment_url=None,
            qr_path=qr_path,
            raw=raw,
        )

    def _check_paid_status(self, transaction_id: str) -> bool:
        response = self._request(
            "GET",
            f"https://backend.saweria.co/donations/qris/{transaction_id}",
            headers=self._saweria_headers(),
            timeout=30,
        )
        if not response.ok:
            reason = self._response_debug(response)
            raise PaymentGatewayError(
                user_message="Status pembayaran belum bisa dicek dari Saweria.",
                admin_message=(
                    f"Saweria check payment gagal: HTTP {response.status_code}. {reason}"
                ),
                status_code=response.status_code,
            )
        data = response.json().get("data", {})
        return data.get("qr_string") == ""

    def _validate_saweria_profile(self) -> str:
        import json
        import re
        url = f"https://saweria.co/{self.username}"
        response = self._request(
            "GET",
            url,
            headers=self._saweria_headers(),
            timeout=30,
        )
        if not response.ok:
            reason = self._response_debug(response)
            raise PaymentGatewayError(
                user_message=(
                    "QRIS gagal dibuat karena gateway pembayaran sedang menolak "
                    "request server. Order dibatalkan, jangan transfer dulu."
                ),
                admin_message=(
                    f"Saweria profile '{self.username}' gagal dibuka: "
                    f"HTTP {response.status_code}. {reason}"
                ),
                status_code=response.status_code,
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
        user_id = profile.get("id")
        if not user_id:
            raise RuntimeError(
                f"Saweria profile '{self.username}' terbuka, tapi user id tidak ditemukan."
            )
        return str(user_id)

    def _saweria_headers(self) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Content-Type": "application/json",
            "DNT": "1",
            "Origin": "https://saweria.co",
            "Referer": "https://saweria.co/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }

    def _proxies(self) -> dict[str, str] | None:
        if not self.proxy_url:
            return None
        return {"http": self.proxy_url, "https": self.proxy_url}

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("proxies", self._proxies())
        session = self._requests_session()
        return session.request(method, url, **kwargs)

    def _requests_session(self) -> Any:
        if self._session is not None:
            return self._session
        if self.use_cloudscraper:
            try:
                import cloudscraper
            except ImportError as exc:
                raise RuntimeError(
                    "SAWERIA_USE_CLOUDSCRAPER=true tapi package cloudscraper belum terinstall."
                ) from exc
            self._session = cloudscraper.create_scraper(
                browser={
                    "browser": "chrome",
                    "platform": "windows",
                    "desktop": True,
                }
            )
            return self._session

        import requests

        self._session = requests.Session()
        return self._session

    def _response_debug(self, response: Any) -> str:
        content_type = response.headers.get("content-type", "-")
        cf_ray = response.headers.get("cf-ray")
        server = response.headers.get("server")
        reason = ""
        try:
            data = response.json()
            reason = self._compact_text(str(data))
        except Exception:
            reason = self._compact_text(response.text)
        pieces = [f"content-type={content_type}"]
        if cf_ray:
            pieces.append(f"cf-ray={cf_ray}")
        if server:
            pieces.append(f"server={server}")
        if reason:
            pieces.append(f"body={reason[:700]}")
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
