from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.openapi import (
    API_DESCRIPTION,
    API_TITLE,
    API_VERSION,
    TAGS_METADATA,
    build_openapi,
    unique_operation_id,
)
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.container import Container, build_container


def create_app(container: Container | None = None) -> FastAPI:
    """Build the app. Tests pass a ready ``container``; production builds one in the lifespan."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if getattr(app.state, "container", None) is not None:
            yield
            return
        async with build_container(get_settings()) as built:
            app.state.container = built
            yield

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        generate_unique_id_function=unique_operation_id,
        lifespan=lifespan,
    )
    app.openapi = lambda: build_openapi(app)  # type: ignore[method-assign]
    if container is not None:
        app.state.container = container

    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
