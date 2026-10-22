import uuid

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import (
    AuthServiceDep,
    FingerprintDep,
    ImpersonationServiceDep,
    OptionalPrincipalDep,
    PrincipalDep,
    RegistrationServiceDep,
    rate_limit,
)
from app.api.responses import error_responses
from app.api.v1.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    TokenPair,
    UserSummary,
    VerifyEmailRequest,
)
from app.services.registration_service import RegistrationInput

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


@router.post(
    "/password-reset/request",
    status_code=202,
    summary="Request a password-reset email (always 202)",
    responses=error_responses(422, 429),
)
async def request_password_reset(
    payload: PasswordResetRequest, service: AuthServiceDep
) -> Response:
    await service.request_password_reset(payload.email)
    return Response(status_code=202)


@router.post(
    "/password-reset/confirm",
    status_code=204,
    summary="Set a new password (also completes a trainer invite)",
    responses=error_responses(422),
)
async def confirm_password_reset(
    payload: PasswordResetConfirm, service: AuthServiceDep
) -> Response:
    await service.confirm_password_reset(payload.token, payload.new_password)
    return Response(status_code=204)


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=201,
    dependencies=[Depends(rate_limit("register"))],
    summary="Register (or add a link to an existing account) via a share link",
    responses={
        200: {"model": RegisterResponse, "description": "Existing account: link added"},
        **error_responses(403, 404, 409, 422, 429),
    },
)
async def register(
    payload: RegisterRequest,
    response: Response,
    service: RegistrationServiceDep,
    caller: OptionalPrincipalDep,
    fingerprint: FingerprintDep,
    share_code: str | None = Query(None, min_length=22, max_length=22),
    camp_submission_id: uuid.UUID | None = None,
) -> RegisterResponse:
    result = await service.register(
        caller=caller,
        share_code=share_code,
        camp_submission_id=camp_submission_id,
        data=RegistrationInput(
            email=payload.email,
            password=payload.password,
            first_name=payload.first_name,
            last_name=payload.last_name,
            phone=payload.phone,
            timezone=payload.timezone,
            birth_date=payload.birth_date,
            player_profile_ids=payload.player_profile_ids,
        ),
        ip_hash=fingerprint.ip_hash,
        ua_hash=fingerprint.ua_hash,
    )
    if not result.created:
        response.status_code = 200
    tokens = result.tokens
    return RegisterResponse(
        user=UserSummary.model_validate(result.account),
        tokens=None
        if tokens is None
        else TokenPair(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in=tokens.expires_in,
        ),
        verification_required=result.verification_required,
        linked_trainer_id=result.linked_trainer_id,
    )


@router.post(
    "/impersonation/exit",
    status_code=204,
    summary="End the current impersonation session",
    responses=error_responses(401, 403),
)
async def exit_impersonation(principal: PrincipalDep, service: ImpersonationServiceDep) -> Response:
    await service.exit(principal)
    return Response(status_code=204)
