"""Registration via ShareLink: one transaction, atomic redemption (C8), D1 token policy."""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

import anyio.to_thread

from app.core.clock import Clock
from app.core.enums import (
    LinkSource,
    LinkStatus,
    OneTimeTokenKind,
    ShareLinkEventKind,
    ShareLinkKind,
    UserRole,
    UserStatus,
)
from app.core.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.password_policy import check_password
from app.core.principal import Principal
from app.core.security import PasswordHasher, generate_opaque_token, hash_token
from app.core.unit_of_work import UnitOfWork
from app.models.identity import (
    CoachMembership,
    CoachProfile,
    PlayerProfile,
    Profile,
    ShareLinkEvent,
    TrainerPlayerLink,
    User,
)
from app.repositories.profile_repo import ProfileRepo
from app.repositories.share_link_repo import LinkWithTrainer, ShareLinkRepo
from app.repositories.tenancy_repo import TenancyRepo
from app.repositories.token_repo import TokenRepo
from app.repositories.user_repo import UserRepo
from app.services.sharelink_service import is_redeemable
from app.services.token_issuer import TokenIssuer
from app.services.views import AccountView, IssuedTokens, account_view

VERIFY_TTL = timedelta(hours=48)


@dataclass(frozen=True, slots=True)
class RegistrationInput:
    email: str
    password: str
    first_name: str
    last_name: str
    phone: str | None
    timezone: str
    birth_date: date | None
    player_profile_ids: list[uuid.UUID] | None


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    account: AccountView
    tokens: IssuedTokens | None
    verification_required: bool
    linked_trainer_id: uuid.UUID
    created: bool


def _invalid_link() -> NotFoundError:
    return NotFoundError("SHARE_LINK_INVALID", "This link is not valid")


class RegistrationService:
    def __init__(
        self,
        uow: UnitOfWork,
        users: UserRepo,
        profiles: ProfileRepo,
        tenancy: TenancyRepo,
        links: ShareLinkRepo,
        tokens: TokenRepo,
        issuer: TokenIssuer,
        hasher: PasswordHasher,
        clock: Clock,
    ) -> None:
        self._uow = uow
        self._users = users
        self._profiles = profiles
        self._tenancy = tenancy
        self._links = links
        self._tokens = tokens
        self._issuer = issuer
        self._hasher = hasher
        self._clock = clock

    async def register(
        self,
        *,
        caller: Principal | None,
        share_code: str | None,
        camp_submission_id: uuid.UUID | None,
        data: RegistrationInput,
        ip_hash: str,
        ua_hash: str,
    ) -> RegistrationResult:
        if (share_code is None) == (camp_submission_id is None):
            raise DomainValidationError(
                "EXACTLY_ONE_SOURCE", "Provide exactly one of share_code or camp_submission_id"
            )
        if share_code is None:
            # O6: Epic-08 will register a camp-submission handler; none exists yet.
            raise ConflictError("CAMP_SUBMISSION_UNAVAILABLE", "Camp registration is unavailable")
        if caller is not None:
            return await self._redeem_as_existing(caller, share_code, data, ip_hash, ua_hash)
        return await self._register_new(share_code, data, ip_hash, ua_hash)

    # --- shared helpers -------------------------------------------------------------------

    async def _resolve_link(self, code: str) -> LinkWithTrainer:
        found = await self._links.get_by_code(code)
        if (
            found is None
            or found.trainer_status != UserStatus.ACTIVE
            or not is_redeemable(found.link, self._clock.now())
        ):
            raise _invalid_link()
        return found

    async def _redeem(self, found: LinkWithTrainer) -> None:
        now = self._clock.now()
        if found.link.kind == ShareLinkKind.COACH_UNIQUE:
            ok = await self._links.redeem_unique(found.link.id, now=now)
        else:
            ok = await self._links.redeem_static(found.link.id)
        if not ok:
            raise _invalid_link()

    async def _record_registered(self, found: LinkWithTrainer, ip_hash: str, ua_hash: str) -> None:
        await self._links.add_event(
            ShareLinkEvent(
                share_link_id=found.link.id,
                kind=ShareLinkEventKind.REGISTERED,
                occurred_at=self._clock.now(),
                ip_hash=ip_hash,
                user_agent_hash=ua_hash,
            )
        )

    async def _ensure_link(self, found: LinkWithTrainer, profile: PlayerProfile) -> None:
        """Create the tenancy link, or re-activate an inactive one; never a duplicate row."""
        existing = await self._tenancy.get_link(found.link.trainer_id, profile.id)
        if existing is None:
            await self._tenancy.add_link(
                TrainerPlayerLink(
                    trainer_id=found.link.trainer_id,
                    player_profile_id=profile.id,
                    source=LinkSource.SHARE_LINK,
                    share_link_id=found.link.id,
                )
            )
        elif existing.status != LinkStatus.ACTIVE:
            existing.status = LinkStatus.ACTIVE

    # --- new account ----------------------------------------------------------------------

    async def _register_new(
        self, code: str, data: RegistrationInput, ip_hash: str, ua_hash: str
    ) -> RegistrationResult:
        if data.player_profile_ids is not None:
            raise _invalid_link()  # a brand-new account owns no profiles
        inputs = [data.email.split("@")[0], data.first_name, data.last_name]
        await anyio.to_thread.run_sync(check_password, data.password, inputs)
        password_hash = await anyio.to_thread.run_sync(self._hasher.hash, data.password)
        now = self._clock.now()
        async with self._uow:
            found = await self._resolve_link(code)
            link = found.link
            is_coach = link.kind == ShareLinkKind.COACH_UNIQUE
            if is_coach:
                if (link.target_email or "").lower() != data.email.lower():
                    raise PermissionDeniedError(
                        "LINK_EMAIL_MISMATCH", "This invitation was issued to another email"
                    )
                # First, and before the email check: serialises competing redemptions so late
                # arrivals get 404 SHARE_LINK_INVALID rather than ACCOUNT_EXISTS.
                await self._redeem(found)
            if await self._users.get_by_email(data.email) is not None:
                raise ConflictError(
                    "ACCOUNT_EXISTS",
                    "An account with this email already exists",
                    details={"action": "login_then_redeem"},
                )
            user = await self._users.add(
                User(
                    email=data.email,
                    password_hash=password_hash,
                    role=UserRole.COACH if is_coach else UserRole.PLAYER,
                    status=UserStatus.ACTIVE,
                    is_email_verified=is_coach,  # a matching invite proves mailbox control
                )
            )
            await self._profiles.add_profile(
                Profile(
                    user_id=user.id,
                    first_name=data.first_name,
                    last_name=data.last_name,
                    phone=data.phone,
                    timezone=data.timezone,
                )
            )
            if is_coach:
                await self._profiles.add_coach_profile(CoachProfile(user_id=user.id))
                await self._tenancy.add_membership(
                    CoachMembership(coach_user_id=user.id, trainer_id=link.trainer_id)
                )
            else:
                own = await self._profiles.add_player_profile(
                    PlayerProfile(
                        owner_user_id=user.id,
                        display_name=f"{data.first_name} {data.last_name}",
                        birth_date=data.birth_date,
                        is_dependent=False,
                    )
                )
                await self._ensure_link(found, own)
                await self._redeem(found)  # last: keeps the link row lock as short as possible
            await self._record_registered(found, ip_hash, ua_hash)
            self._uow.enqueue("send_welcome_email", user_id=str(user.id))
            tokens: IssuedTokens | None = None
            if is_coach:
                tokens = await self._issuer.issue_pair(
                    user.id, user.role, family_id=None, ip_hash=ip_hash, ua_hash=ua_hash
                )
            else:
                raw = generate_opaque_token()
                await self._tokens.issue_one_time(
                    user.id,
                    OneTimeTokenKind.EMAIL_VERIFY,
                    hash_token(raw),
                    now + VERIFY_TTL,
                    now=now,
                )
                self._uow.enqueue("send_verification_email", user_id=str(user.id), token=raw)
            profile = await self._users.get_profile(user.id)
            assert profile is not None  # noqa: S101
            return RegistrationResult(
                account=account_view(user, profile),
                tokens=tokens,
                verification_required=not user.is_email_verified,
                linked_trainer_id=link.trainer_id,
                created=True,
            )

    # --- existing authenticated account ---------------------------------------------------

    async def _redeem_as_existing(
        self,
        caller: Principal,
        code: str,
        data: RegistrationInput,
        ip_hash: str,
        ua_hash: str,
    ) -> RegistrationResult:
        if caller.is_child:
            raise PermissionDeniedError(
                "CHILD_CANNOT_REDEEM", "Child accounts cannot redeem share links"
            )
        async with self._uow:
            found = await self._resolve_link(code)
            link = found.link
            user = await self._users.get_by_id(caller.user_id)
            profile = await self._users.get_profile(caller.user_id)
            assert user is not None and profile is not None  # noqa: S101
            if link.kind == ShareLinkKind.COACH_UNIQUE:
                await self._redeem_coach_link(user, found)
            else:
                await self._redeem_player_link(user, found, data)
            await self._record_registered(found, ip_hash, ua_hash)
            return RegistrationResult(
                account=account_view(user, profile),
                tokens=None,
                verification_required=not user.is_email_verified,
                linked_trainer_id=link.trainer_id,
                created=False,
            )

    async def _redeem_coach_link(self, user: User, found: LinkWithTrainer) -> None:
        link = found.link
        if user.role != UserRole.COACH:
            raise ConflictError("WRONG_LINK_KIND", "This link is for coaches")
        if (link.target_email or "").lower() != user.email.lower():
            raise PermissionDeniedError(
                "LINK_EMAIL_MISMATCH", "This invitation was issued to another email"
            )
        membership = await self._tenancy.get_active_membership(user.id)
        if membership is not None:
            if membership.trainer_id == link.trainer_id:
                return  # already this trainer's coach: nothing to add, link not consumed
            raise ConflictError(
                "COACH_ALREADY_ASSIGNED",
                "Coach is already assigned to another trainer",
                details={"trainer_id": str(membership.trainer_id)},
            )
        await self._redeem(found)
        await self._tenancy.add_membership(
            CoachMembership(coach_user_id=user.id, trainer_id=link.trainer_id)
        )

    async def _redeem_player_link(
        self, user: User, found: LinkWithTrainer, data: RegistrationInput
    ) -> None:
        if user.role != UserRole.PLAYER:
            raise ConflictError("WRONG_LINK_KIND", "This link is for players")
        if data.player_profile_ids is not None:
            wanted = set(data.player_profile_ids)
            targets = await self._profiles.get_owned_profiles(user.id, list(wanted))
            if {p.id for p in targets} != wanted:
                raise _invalid_link()  # not owned: 404, never 403
        else:
            own = await self._profiles.get_own_player_profile(user.id)
            if own is None:
                own = await self._profiles.add_player_profile(
                    PlayerProfile(
                        owner_user_id=user.id,
                        display_name=f"{data.first_name} {data.last_name}",
                        birth_date=data.birth_date,
                        is_dependent=False,
                    )
                )
            targets = [own]
        for target in targets:
            await self._ensure_link(found, target)
        await self._redeem(found)
