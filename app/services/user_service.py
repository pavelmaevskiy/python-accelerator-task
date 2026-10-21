"""Current-user profile, avatar and email change."""

import uuid
from dataclasses import dataclass
from datetime import timedelta

import anyio.to_thread

from app.core.clock import Clock
from app.core.enums import OneTimeTokenKind, UserRole, UserStatus
from app.core.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.principal import Principal
from app.core.security import PasswordHasher, generate_opaque_token, hash_token
from app.core.storage import ObjectStorage
from app.core.unit_of_work import UnitOfWork
from app.core.uploads import UploadKind
from app.models.identity import Profile, User
from app.repositories.profile_repo import ProfileRepo
from app.repositories.token_repo import TokenRepo
from app.repositories.user_repo import UserRepo
from app.services.upload_service import PresignView, UploadService

EMAIL_CHANGE_TTL = timedelta(hours=24)
_NOT_NULL_PROFILE_FIELDS = frozenset({"first_name", "last_name", "timezone"})
_EDITABLE_PROFILE_FIELDS = ("first_name", "last_name", "phone", "school", "timezone")


@dataclass(frozen=True, slots=True)
class TrainerProfileView:
    business_name: str
    slug: str
    logo_url: str | None
    brand_primary_color: str | None
    brand_secondary_color: str | None
    address: str | None
    website: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class CoachProfileView:
    bio: str | None
    credentials: str | None
    certifications: list[object]
    is_public: bool
    trainer_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class MeView:
    id: uuid.UUID
    email: str
    role: UserRole
    status: UserStatus
    first_name: str
    last_name: str
    phone: str | None
    school: str | None
    timezone: str
    avatar_url: str | None
    is_email_verified: bool
    must_change_password: bool
    is_child: bool
    trainer_id: uuid.UUID | None
    trainer_profile: TrainerProfileView | None
    coach_profile: CoachProfileView | None


class UserService:
    def __init__(
        self,
        uow: UnitOfWork,
        users: UserRepo,
        profiles: ProfileRepo,
        tokens: TokenRepo,
        uploads: UploadService,
        storage: ObjectStorage,
        hasher: PasswordHasher,
        clock: Clock,
    ) -> None:
        self._uow = uow
        self._users = users
        self._profiles = profiles
        self._tokens = tokens
        self._uploads = uploads
        self._storage = storage
        self._hasher = hasher
        self._clock = clock

    async def _load(self, principal: Principal) -> tuple[User, Profile]:
        user = await self._users.get_by_id(principal.user_id)
        profile = await self._users.get_profile(principal.user_id)
        if user is None or profile is None:
            raise NotFoundError("NOT_FOUND", "User not found")
        return user, profile

    async def _view(self, principal: Principal, user: User, profile: Profile) -> MeView:
        trainer_view: TrainerProfileView | None = None
        coach_view: CoachProfileView | None = None
        if user.role == UserRole.TRAINER:
            tp = await self._profiles.get_trainer_profile(user.id)
            if tp is not None:
                trainer_view = TrainerProfileView(
                    business_name=tp.business_name,
                    slug=tp.slug,
                    logo_url=self._storage.public_url(tp.logo_key),
                    brand_primary_color=tp.brand_primary_color,
                    brand_secondary_color=tp.brand_secondary_color,
                    address=tp.address,
                    website=tp.website,
                    description=tp.description,
                )
        elif user.role == UserRole.COACH:
            cp = await self._profiles.get_coach_profile(user.id)
            if cp is not None:
                coach_view = CoachProfileView(
                    bio=cp.bio,
                    credentials=cp.credentials,
                    certifications=list(cp.certifications),
                    is_public=cp.is_public,
                    trainer_id=principal.trainer_id,
                )
        return MeView(
            id=user.id,
            email=user.email,
            role=user.role,
            status=user.status,
            first_name=profile.first_name,
            last_name=profile.last_name,
            phone=profile.phone,
            school=profile.school,
            timezone=profile.timezone,
            avatar_url=self._storage.public_url(profile.avatar_key),
            is_email_verified=user.is_email_verified,
            must_change_password=user.must_change_password,
            is_child=principal.is_child,
            trainer_id=principal.trainer_id,
            trainer_profile=trainer_view,
            coach_profile=coach_view,
        )

    async def get_me(self, principal: Principal) -> MeView:
        async with self._uow:
            user, profile = await self._load(principal)
            return await self._view(principal, user, profile)

    async def update_me(self, principal: Principal, changes: dict[str, object]) -> MeView:
        """Apply only the fields present in ``changes`` (PATCH semantics)."""
        async with self._uow:
            user, profile = await self._load(principal)
            for name in _EDITABLE_PROFILE_FIELDS:
                if name not in changes:
                    continue
                value = changes[name]
                if value is None and name in _NOT_NULL_PROFILE_FIELDS:
                    continue
                setattr(profile, name, value)
            if "avatar_key" in changes:
                previous = profile.avatar_key
                key = changes["avatar_key"]
                profile.avatar_key = (
                    None
                    if key is None
                    else await self._uploads.confirm(user.id, str(key), UploadKind.AVATAR)
                )
                if previous is not None and previous != profile.avatar_key:
                    self._uow.enqueue("delete_s3_object", key=previous)
            return await self._view(principal, user, profile)

    async def presign_avatar(
        self, principal: Principal, *, content_type: str, content_length: int
    ) -> PresignView:
        return await self._uploads.presign(
            principal.user_id, content_type=content_type, content_length=content_length
        )

    async def request_email_change(
        self, principal: Principal, new_email: str, current_password: str
    ) -> None:
        if principal.is_child:
            raise PermissionDeniedError(
                "CHILD_CANNOT_CHANGE_EMAIL", "Child accounts cannot change their email"
            )
        async with self._uow:
            user = await self._users.get_by_id(principal.user_id)
            if user is None:
                raise NotFoundError("NOT_FOUND", "User not found")
            ok = await anyio.to_thread.run_sync(
                self._hasher.verify, user.password_hash, current_password
            )
            if not ok:
                raise AuthenticationError("INVALID_CREDENTIALS", "Current password is incorrect")
            if await self._users.get_by_email(new_email) is not None:
                raise ConflictError("EMAIL_TAKEN", "Email is already in use")
            token = generate_opaque_token()
            now = self._clock.now()
            await self._tokens.issue_one_time(
                user.id,
                OneTimeTokenKind.EMAIL_CHANGE,
                hash_token(token),
                now + EMAIL_CHANGE_TTL,
                payload={"new_email": new_email},
                now=now,
            )
            self._uow.enqueue(
                "send_email_change_email", user_id=str(user.id), token=token, new_email=new_email
            )
