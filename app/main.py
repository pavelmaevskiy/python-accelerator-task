from fastapi import FastAPI

from app.api.errors import register_exception_handlers


def create_app() -> FastAPI:
    app = FastAPI(title="Trainer Platform API", version="1.0.0")

    register_exception_handlers(app)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
