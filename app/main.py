from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import register_exception_handlers
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

    app = FastAPI(title="Trainer Platform API", version="1.0.0", lifespan=lifespan)
    if container is not None:
        app.state.container = container

    register_exception_handlers(app)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
