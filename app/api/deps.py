"""FastAPI dependencies: container access, DB session, principal, guards, rate limits."""

import hashlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.core.enums import UserRole
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.principal import Principal
from app.core.unit_of_work import UnitOfWork
from app.repositories.approval_repo import ApprovalRepo
from app.repositories.audit_repo import AuditRepo
from app.repositories.availability_repo import AvailabilityRepo
from app.repositories.erasure_repo import ErasureRepo
from app.repositories.idempotency_repo import IdempotencyRepo
from app.repositories.impersonation_repo import ImpersonationRepo
from app.repositories.profile_repo import ProfileRepo
from app.repositories.share_link_repo import ShareLinkRepo
from app.repositories.tenancy_repo import TenancyRepo
from app.repositories.token_repo import TokenRepo
from app.repositories.user_repo import UserRepo
from app.services.admin_user_service import AdminUserService
from app.services.approval_service import ApprovalService
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.availability_service import AvailabilityService
from app.services.branding_service import BrandingService
from app.services.erasure_service import ErasureService
from app.services.impersonation_service import ImpersonationService
from app.services.player_service import PlayerService
from app.services.principal_service import PrincipalService
from app.services.registration_service import RegistrationService
from app.services.sharelink_service import ShareLinkService
from app.services.token_issuer import TokenIssuer
from app.services.upload_service import UploadService
from app.services.user_service import UserService

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth")

RATE_LIMITS: dict[str, tuple[int, int]] = {  # name -> (limit, window seconds), per IP
    "login": (10, 60),
    "verify_resend": (3, 3600),
    "share_preview": (60, 60),
    "register": (300, 60),  # the default unauthenticated limit, per IP
}
DEFAULT_PRINCIPAL_LIMIT = (300, 60)


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_session(container: ContainerDep) -> AsyncIterator[AsyncSession]:
    async with container.sessionmaker() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def hash_with_salt(container: Container, value: str) -> str:
    return hashlib.sha256(f"{container.settings.ip_hash_salt}:{value}".encode()).hexdigest()


class ClientFingerprint:
    """Salted hashes of IP and user agent; raw values are never stored."""

    def __init__(self, ip_hash: str, ua_hash: str) -> None:
        self.ip_hash = ip_hash
        self.ua_hash = ua_hash


def get_fingerprint(request: Request, container: ContainerDep) -> ClientFingerprint:
    ua = request.headers.get("user-agent", "")
    return ClientFingerprint(
        hash_with_salt(container, client_ip(request)), hash_with_salt(container, ua)
    )


FingerprintDep = Annotated[ClientFingerprint, Depends(get_fingerprint)]


async def _resolve(
    request: Request,
    response: Response,
    container: Container,
    credentials: HTTPAuthorizationCredentials,
    trainer_context: uuid.UUID | None,
) -> Principal:
    claims = container.codec.decode_access(credentials.credentials)
    principal = await PrincipalService(container.sessionmaker, container.clock).resolve(
        claims, trainer_context
    )
    if principal.act is not None:
        response.headers["X-Impersonated-By"] = str(principal.act)
        await AuditService(container.sessionmaker).record_isolated(
            actor_user_id=principal.user_id,
            impersonator_id=principal.act,
            action="impersonated_request",
            details={"method": request.method, "path": request.url.path},
        )
    return principal


async def current_principal(
    request: Request,
    response: Response,
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    x_trainer_context: Annotated[uuid.UUID | None, Header()] = None,
) -> Principal:
    if credentials is None:
        raise AuthenticationError("NOT_AUTHENTICATED", "Authentication required")
    return await _resolve(request, response, container, credentials, x_trainer_context)


async def optional_principal(
    request: Request,
    response: Response,
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    x_trainer_context: Annotated[uuid.UUID | None, Header()] = None,
) -> Principal | None:
    """``None`` when no Authorization header is sent; a malformed one is still 401."""
    if credentials is None:
        if request.headers.get("authorization"):
            raise AuthenticationError("INVALID_TOKEN", "Invalid authorization header")
        return None
    return await _resolve(request, response, container, credentials, x_trainer_context)


PrincipalDep = Annotated[Principal, Depends(current_principal)]
OptionalPrincipalDep = Annotated[Principal | None, Depends(optional_principal)]


def require_role(*roles: UserRole) -> Callable[[Principal], Awaitable[Principal]]:
    allowed = frozenset(roles)

    async def _guard(principal: PrincipalDep) -> Principal:
        if principal.role not in allowed:
            raise PermissionDeniedError("FORBIDDEN", "You do not have access to this resource")
        return principal

    return _guard


async def reject_impersonation(principal: PrincipalDep) -> None:
    if principal.is_impersonated:
        raise PermissionDeniedError(
            "ADMIN_FORBIDDEN_WHILE_IMPERSONATING", "Not available while impersonating"
        )


async def reject_child(principal: PrincipalDep) -> None:
    if principal.is_child:
        raise PermissionDeniedError("CHILD_FORBIDDEN", "Not available to child accounts")


async def require_tenant_scope(principal: PrincipalDep) -> uuid.UUID:
    """Derive ``trainer_id`` from the principal only; never from path, query or body."""
    if principal.trainer_id is None:
        raise PermissionDeniedError("FORBIDDEN", "No trainer context")
    return principal.trainer_id


TenantDep = Annotated[uuid.UUID, Depends(require_tenant_scope)]


def rate_limit(name: str) -> Callable[..., Awaitable[None]]:
    """Per-IP limit from ``RATE_LIMITS``. Per-email limits live in the services."""
    limit, window = RATE_LIMITS[name]

    async def _dep(request: Request, container: ContainerDep) -> None:
        await container.rate_limiter.hit(
            f"{name}:ip:{client_ip(request)}", limit=limit, window_seconds=window
        )

    return _dep


async def default_rate_limit(principal: PrincipalDep, container: ContainerDep) -> None:
    limit, window = DEFAULT_PRINCIPAL_LIMIT
    await container.rate_limiter.hit(
        f"default:{principal.user_id}", limit=limit, window_seconds=window
    )


# --- service providers ----------------------------------------------------------------------


def get_unit_of_work(session: SessionDep, container: ContainerDep) -> UnitOfWork:
    return UnitOfWork(session, container.queue)


UowDep = Annotated[UnitOfWork, Depends(get_unit_of_work)]


def get_token_issuer(session: SessionDep, container: ContainerDep) -> TokenIssuer:
    return TokenIssuer(
        container.codec,
        TokenRepo(session),
        TenancyRepo(session),
        container.clock,
        container.settings,
    )


TokenIssuerDep = Annotated[TokenIssuer, Depends(get_token_issuer)]


def get_audit_service(session: SessionDep, container: ContainerDep) -> AuditService:
    return AuditService(container.sessionmaker, AuditRepo(session))


AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]


def get_auth_service(
    session: SessionDep,
    uow: UowDep,
    issuer: TokenIssuerDep,
    audit: AuditServiceDep,
    container: ContainerDep,
) -> AuthService:
    return AuthService(
        uow=uow,
        users=UserRepo(session),
        tokens=TokenRepo(session),
        issuer=issuer,
        hasher=container.hasher,
        clock=container.clock,
        rate_limiter=container.rate_limiter,
        audit=audit,
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_sharelink_service(
    session: SessionDep, uow: UowDep, container: ContainerDep
) -> ShareLinkService:
    return ShareLinkService(
        uow, ShareLinkRepo(session), container.clock, container.settings, container.storage
    )


ShareLinkServiceDep = Annotated[ShareLinkService, Depends(get_sharelink_service)]


def get_registration_service(
    session: SessionDep, uow: UowDep, issuer: TokenIssuerDep, container: ContainerDep
) -> RegistrationService:
    return RegistrationService(
        uow=uow,
        users=UserRepo(session),
        profiles=ProfileRepo(session),
        tenancy=TenancyRepo(session),
        links=ShareLinkRepo(session),
        tokens=TokenRepo(session),
        issuer=issuer,
        hasher=container.hasher,
        clock=container.clock,
    )


RegistrationServiceDep = Annotated[RegistrationService, Depends(get_registration_service)]


def get_upload_service(container: ContainerDep) -> UploadService:
    return UploadService(container.storage)


UploadServiceDep = Annotated[UploadService, Depends(get_upload_service)]


def get_user_service(
    session: SessionDep, uow: UowDep, uploads: UploadServiceDep, container: ContainerDep
) -> UserService:
    return UserService(
        uow=uow,
        users=UserRepo(session),
        profiles=ProfileRepo(session),
        tokens=TokenRepo(session),
        uploads=uploads,
        storage=container.storage,
        hasher=container.hasher,
        clock=container.clock,
    )


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


def get_availability_service(session: SessionDep, uow: UowDep) -> AvailabilityService:
    return AvailabilityService(
        uow, AvailabilityRepo(session), ProfileRepo(session), TenancyRepo(session)
    )


AvailabilityServiceDep = Annotated[AvailabilityService, Depends(get_availability_service)]


def get_player_service(session: SessionDep, uow: UowDep, container: ContainerDep) -> PlayerService:
    return PlayerService(
        uow,
        ProfileRepo(session),
        TenancyRepo(session),
        container.clock,
        container.commitments,
        container.storage,
    )


PlayerServiceDep = Annotated[PlayerService, Depends(get_player_service)]


def get_approval_service(
    session: SessionDep, uow: UowDep, container: ContainerDep
) -> ApprovalService:
    return ApprovalService(
        uow,
        ApprovalRepo(session),
        IdempotencyRepo(session),
        container.approval_registry,
        container.clock,
    )


ApprovalServiceDep = Annotated[ApprovalService, Depends(get_approval_service)]


def get_branding_service(
    session: SessionDep, uow: UowDep, uploads: UploadServiceDep, container: ContainerDep
) -> BrandingService:
    return BrandingService(
        uow,
        ProfileRepo(session),
        UserRepo(session),
        uploads,
        container.storage,
        container.redis,
    )


BrandingServiceDep = Annotated[BrandingService, Depends(get_branding_service)]


def get_admin_user_service(
    session: SessionDep, uow: UowDep, audit: AuditServiceDep, container: ContainerDep
) -> AdminUserService:
    return AdminUserService(
        uow,
        UserRepo(session),
        ProfileRepo(session),
        TokenRepo(session),
        audit,
        container.hasher,
        container.clock,
    )


AdminUserServiceDep = Annotated[AdminUserService, Depends(get_admin_user_service)]


def get_impersonation_service(
    session: SessionDep, uow: UowDep, audit: AuditServiceDep, container: ContainerDep
) -> ImpersonationService:
    return ImpersonationService(
        uow, UserRepo(session), ImpersonationRepo(session), audit, container.codec, container.clock
    )


ImpersonationServiceDep = Annotated[ImpersonationService, Depends(get_impersonation_service)]


def get_erasure_service(
    session: SessionDep, uow: UowDep, audit: AuditServiceDep, container: ContainerDep
) -> ErasureService:
    return ErasureService(
        uow,
        UserRepo(session),
        ErasureRepo(session),
        TokenRepo(session),
        ImpersonationRepo(session),
        audit,
        container.commitments,
        container.hasher,
        container.clock,
        container.redis,
    )


ErasureServiceDep = Annotated[ErasureService, Depends(get_erasure_service)]
