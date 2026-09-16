from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from telnyx import Telnyx

from .config import Settings
from .webhooks import read_event


def create_app(settings=None):
    settings = settings or Settings.from_env()
    settings.validate()
    verifier = Telnyx(api_key=settings.api_key, public_key=settings.public_key)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            verifier.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "product": "Deskline"}

    @app.post("/webhooks/voice")
    async def voice_webhook(request: Request):
        event = await read_event(request, verifier, settings)
        if event is None:
            return {"received": True, "ignored": True}
        print("Verified event:", event["event_type"], flush=True)
        return {"received": True}

    return app
