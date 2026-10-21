from fastapi import APIRouter, Depends, Response

from app.api.deps import AuthServiceDep, FingerprintDep, PrincipalDep, rate_limit
from app.api.responses import error_responses
from app.api.v1.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    RefreshRequest,
    ResendVerificationRequest,
    TokenPair,
    UserSummary,
    VerifyEmailRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=LoginResponse,
    dependencies=[Depends(rate_limit("login"))],
    summary="Log in with email and password",
    responses=error_responses(401, 403, 422, 429),
)
async def login(
    payload: LoginRequest, service: AuthServiceDep, fingerprint: FingerprintDep
) -> LoginResponse:
    result = await service.login(
        payload.email, payload.password, ip_hash=fingerprint.ip_hash, ua_hash=fingerprint.ua_hash
    )
    return LoginResponse(
        access_token=result.tokens.access_token,
        refresh_token=result.tokens.refresh_token,
        expires_in=result.tokens.expires_in,
        user=UserSummary.model_validate(result.user),
    )


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Rotate a refresh token",
    responses=error_responses(401, 422),
)
async def refresh(
    payload: RefreshRequest, service: AuthServiceDep, fingerprint: FingerprintDep
) -> TokenPair:
    tokens = await service.refresh(
        payload.refresh_token, ip_hash=fingerprint.ip_hash, ua_hash=fingerprint.ua_hash
    )
    return TokenPair(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
    )


@router.post(
    "/logout",
    status_code=204,
    summary="Revoke the refresh-token family",
    responses=error_responses(401, 422),
)
async def logout(
    payload: LogoutRequest, principal: PrincipalDep, service: AuthServiceDep
) -> Response:
    await service.logout(principal.user_id, payload.refresh_token)
    return Response(status_code=204)


@router.post(
    "/verify-email",
    status_code=204,
    summary="Verify an email address (or confirm a pending email change)",
    responses=error_responses(422),
)
async def verify_email(payload: VerifyEmailRequest, service: AuthServiceDep) -> Response:
    await service.verify_email(payload.token)
    return Response(status_code=204)


@router.post(
    "/verify-email/resend",
    status_code=202,
    dependencies=[Depends(rate_limit("verify_resend"))],
    summary="Resend the verification email (always 202)",
    responses=error_responses(422, 429),
)
async def resend_verification(
    payload: ResendVerificationRequest, service: AuthServiceDep
) -> Response:
    await service.resend_verification(payload.email)
    return Response(status_code=202)
