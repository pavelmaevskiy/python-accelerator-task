"""Trainer portal branding: private editing and the cached public projection."""

import json
from dataclasses import asdict, dataclass

from redis.asyncio import Redis

from app.core.enums import UserStatus
from app.core.errors import NotFoundError
from app.core.principal import Principal
from app.core.storage import ObjectStorage
from app.core.unit_of_work import UnitOfWork
from app.core.uploads import UploadKind
from app.models.identity import TrainerProfile
from app.repositories.profile_repo import ProfileRepo
from app.repositories.user_repo import UserRepo
from app.services.upload_service import PresignView, UploadService

PUBLIC_BRANDING_TTL = 300
_NULLABLE = (
    "address",
    "website",
    "description",
    "brand_primary_color",
    "brand_secondary_color",
)


def public_branding_key(slug: str) -> str:
    return f"public:branding:{slug}"


@dataclass(frozen=True, slots=True)
class BrandingView:
    business_name: str
    slug: str
    logo_url: str | None
    brand_primary_color: str | None
    brand_secondary_color: str | None
    address: str | None
    website: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class PublicBrandingView:
    business_name: str
    logo_url: str | None
    brand_primary_color: str | None
    brand_secondary_color: str | None


class BrandingService:
    def __init__(
        self,
        uow: UnitOfWork,
        profiles: ProfileRepo,
        users: UserRepo,
        uploads: UploadService,
        storage: ObjectStorage,
        redis: Redis,
    ) -> None:
        self._uow = uow
        self._profiles = profiles
        self._users = users
        self._uploads = uploads
        self._storage = storage
        self._redis = redis

    async def _own(self, principal: Principal) -> TrainerProfile:
        profile = await self._profiles.get_trainer_profile(principal.user_id)
        if profile is None:
            raise NotFoundError("NOT_FOUND", "Trainer profile not found")
        return profile

    def _view(self, p: TrainerProfile) -> BrandingView:
        return BrandingView(
            business_name=p.business_name,
            slug=p.slug,
            logo_url=self._storage.public_url(p.logo_key),
            brand_primary_color=p.brand_primary_color,
            brand_secondary_color=p.brand_secondary_color,
            address=p.address,
            website=p.website,
            description=p.description,
        )

    async def get(self, principal: Principal) -> BrandingView:
        async with self._uow:
            return self._view(await self._own(principal))

    async def update(self, principal: Principal, changes: dict[str, object]) -> BrandingView:
        """PATCH semantics; the public cache key is dropped after the commit."""
        async with self._uow:
            profile = await self._own(principal)
            if changes.get("business_name") is not None:
                profile.business_name = str(changes["business_name"])
            for name in _NULLABLE:
                if name in changes:
                    value = changes[name]
                    setattr(profile, name, None if value is None else str(value))
            if "logo_key" in changes:
                previous = profile.logo_key
                key = changes["logo_key"]
                profile.logo_key = (
                    None
                    if key is None
                    else await self._uploads.confirm(principal.user_id, str(key), UploadKind.LOGO)
                )
                if previous is not None and previous != profile.logo_key:
                    self._uow.enqueue("delete_s3_object", key=previous)
            view = self._view(profile)
            slug = profile.slug
        await self._redis.delete(public_branding_key(slug))  # after commit: no stale re-fill
        return view

    async def presign_logo(
        self, principal: Principal, *, content_type: str, content_length: int
    ) -> PresignView:
        return await self._uploads.presign(
            principal.user_id, content_type=content_type, content_length=content_length
        )

    async def get_public(self, slug: str) -> PublicBrandingView:
        """Redis read-through (TTL 300 s). Exposes exactly four fields."""
        cached = await self._redis.get(public_branding_key(slug))
        if cached is not None:
            return PublicBrandingView(**json.loads(cached))
        async with self._uow:
            profile = await self._profiles.get_trainer_profile_by_slug(slug)
            state = None if profile is None else await self._users.get_auth_state(profile.user_id)
            if profile is None or state is None or state.status != UserStatus.ACTIVE:
                raise NotFoundError("NOT_FOUND", "Trainer not found")
            view = PublicBrandingView(
                business_name=profile.business_name,
                logo_url=self._storage.public_url(profile.logo_key),
                brand_primary_color=profile.brand_primary_color,
                brand_secondary_color=profile.brand_secondary_color,
            )
        await self._redis.set(
            public_branding_key(slug), json.dumps(asdict(view)), ex=PUBLIC_BRANDING_TTL
        )
        return view
