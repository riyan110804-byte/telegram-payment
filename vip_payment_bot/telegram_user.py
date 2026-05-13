from __future__ import annotations

from datetime import datetime, timedelta, timezone

from telethon import TelegramClient, functions, utils
from telethon.errors import RPCError
from telethon.sessions import StringSession

from .config import Settings


class TelegramUserClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = TelegramClient(
            StringSession(settings.telethon_session_string),
            settings.telethon_api_id,
            settings.telethon_api_hash,
        )
        self._vip_peer = None

    async def start(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("TELETHON_SESSION_STRING tidak valid atau belum login.")
        self._vip_peer = await self._resolve_vip_peer()

    async def close(self) -> None:
        await self.client.disconnect()

    async def create_vip_invite_link(self, order_id: int) -> str:
        expire_at = datetime.now(timezone.utc) + timedelta(
            hours=self.settings.vip_invite_expire_hours
        )
        peer = self._vip_peer or await self._resolve_vip_peer()
        result = await self.client(
            functions.messages.ExportChatInviteRequest(
                peer=peer,
                expire_date=expire_at,
                usage_limit=self.settings.vip_invite_usage_limit,
                title=f"VIP payment #{order_id}",
            )
        )
        link = getattr(result, "link", None)
        if not link:
            raise RuntimeError("Telethon tidak mengembalikan invite link.")
        return str(link)

    async def _resolve_vip_peer(self):
        target = self.settings.vip_group_id
        candidates = self._entity_candidates(target)

        for candidate in candidates:
            try:
                return await self.client.get_input_entity(candidate)
            except (ValueError, TypeError, RPCError):
                continue

        async for dialog in self.client.iter_dialogs():
            entity = dialog.entity
            peer_id = utils.get_peer_id(entity)
            raw_id = getattr(entity, "id", None)
            username = getattr(entity, "username", None)
            title = getattr(entity, "title", None)
            if target in {peer_id, raw_id, username, title} or str(target) in {
                str(peer_id),
                str(raw_id),
                str(username),
                str(title),
            }:
                return await self.client.get_input_entity(entity)

        raise RuntimeError(
            "VIP_GROUP_ID tidak bisa di-resolve oleh akun Telethon. Pastikan akun "
            "Telethon sudah join group VIP dan menjadi admin. Lebih aman isi "
            "VIP_GROUP_ID dengan username/link group jika group punya username, "
            "atau pakai id lengkap -100... dari group tersebut."
        )

    def _entity_candidates(self, target: int | str) -> list[int | str]:
        candidates: list[int | str] = [target]
        if isinstance(target, int):
            text = str(target)
        else:
            text = target.strip()
            if text.startswith("@"):
                candidates.append(text[1:])
            if text.startswith(("http://", "https://")):
                candidates.append(text.rstrip("/").rsplit("/", 1)[-1])

        if isinstance(target, int) and target < 0:
            as_text = str(target)
            if as_text.startswith("-100"):
                candidates.append(int(as_text[4:]))

        return list(dict.fromkeys(candidates))
