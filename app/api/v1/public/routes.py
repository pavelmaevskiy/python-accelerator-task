from fastapi import APIRouter, Depends, Response

from app.api.deps import BrandingServiceDep, FingerprintDep, ShareLinkServiceDep, rate_limit
from app.api.responses import error_responses
from app.api.v1.public.schemas import PublicBranding, ShareLinkPreview

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


@router.get(
    "/trainers/{slug}/branding",
    response_model=PublicBranding,
    summary="Public trainer branding (cached 5 minutes)",
    responses=error_responses(404),
)
async def get_public_branding(
    slug: str, response: Response, service: BrandingServiceDep
) -> PublicBranding:
    view = await service.get_public(slug)
    response.headers["Cache-Control"] = "public, max-age=300"
    return PublicBranding.model_validate(view)
