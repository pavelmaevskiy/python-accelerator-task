from fastapi import APIRouter, Depends

from app.api.deps import FingerprintDep, ShareLinkServiceDep, rate_limit
from app.api.responses import error_responses
from app.api.v1.public.schemas import ShareLinkPreview

router = APIRouter(prefix="/public", tags=["public"], responses=error_responses(429))


@router.get(
    "/share-links/{code}",
    response_model=ShareLinkPreview,
    dependencies=[Depends(rate_limit("share_preview"))],
    summary="Preview a share link (branding only)",
    responses=error_responses(404),
)
async def preview_share_link(
    code: str, service: ShareLinkServiceDep, fingerprint: FingerprintDep
) -> ShareLinkPreview:
    view = await service.preview(code, ip_hash=fingerprint.ip_hash, ua_hash=fingerprint.ua_hash)
    return ShareLinkPreview(
        trainer_business_name=view.trainer_business_name,
        trainer_slug=view.trainer_slug,
        logo_url=view.logo_url,
        kind=view.kind.value,
    )
