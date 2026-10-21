"""Builds and sends transactional emails. Called only from background jobs."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.email import EmailSender
from app.repositories.notification_repo import NotificationRepo


class NotificationService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        sender: EmailSender,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._sender = sender
        self._base = settings.public_base_url.rstrip("/")

    async def _recipient(self, user_id: str) -> tuple[str, str] | None:
        """(email, first_name) of a live user; ``None`` once erased or missing."""
        async with self._sessionmaker() as session:
            return await NotificationRepo(session).get_recipient(uuid.UUID(user_id))

    async def _send_to_user(self, template: str, user_id: str, extra: dict[str, str]) -> None:
        recipient = await self._recipient(user_id)
        if recipient is None:
            return
        email, first_name = recipient
        await self._sender.send(template, email, {"first_name": first_name, **extra})

    async def verification(self, user_id: str, token: str) -> None:
        link = f"{self._base}/verify-email?token={token}"
        await self._send_to_user("verify_email", user_id, {"link": link})

    async def welcome(self, user_id: str) -> None:
        await self._send_to_user("welcome", user_id, {"link": self._base})

    async def password_reset(self, user_id: str, token: str) -> None:
        link = f"{self._base}/reset-password?token={token}"
        await self._send_to_user("password_reset", user_id, {"link": link})

    async def trainer_invite(self, user_id: str, token: str) -> None:
        link = f"{self._base}/trainer-setup?token={token}"
        await self._send_to_user("trainer_invite", user_id, {"link": link})

    async def email_change(self, user_id: str, token: str, new_email: str) -> None:
        recipient = await self._recipient(user_id)
        if recipient is None:
            return
        link = f"{self._base}/verify-email?token={token}"
        await self._sender.send(
            "email_change", new_email, {"first_name": recipient[1], "link": link}
        )

    async def coach_invite(self, share_link_id: str) -> None:
        async with self._sessionmaker() as session:
            invite = await NotificationRepo(session).get_coach_invite(uuid.UUID(share_link_id))
        if invite is None:
            return
        target_email, code, business_name = invite
        link = f"{self._base}/join/{code}"
        await self._sender.send(
            "coach_invite", target_email, {"business_name": business_name, "link": link}
        )

    async def approval_notification(self, approval_id: str) -> None:
        async with self._sessionmaker() as session:
            data = await NotificationRepo(session).get_approval_recipient(uuid.UUID(approval_id))
        if data is None:
            return
        email, first_name, player_name = data
        await self._sender.send(
            "approval_requested",
            email,
            {
                "first_name": first_name,
                "player_name": player_name,
                "link": f"{self._base}/approvals",
            },
        )
