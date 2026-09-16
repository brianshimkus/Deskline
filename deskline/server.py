from fastapi import FastAPI  # pyright: ignore[reportMissingImports]


def create_app():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health():
        return {"status": "ok", "product": "Deskline"}

    return app
