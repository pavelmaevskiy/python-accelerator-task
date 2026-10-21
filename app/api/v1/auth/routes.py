from fastapi import APIRouter, Depends

from app.api.deps import AuthServiceDep, FingerprintDep, rate_limit
from app.api.responses import error_responses
from app.api.v1.auth.schemas import LoginRequest, LoginResponse, UserSummary

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
