from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Trainer Platform API", version="1.0.0")

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
