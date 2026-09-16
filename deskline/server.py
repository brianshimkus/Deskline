import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from telnyx import Telnyx

from .config import Settings
from .core import Engine
from .storage import Store
from .webhooks import read_event


def create_app(settings=None, provider=None, run_worker=True):
    settings = settings or Settings.from_env()
    settings.validate()
    store = Store(settings.db_path)
    engine = Engine(settings, store, provider)
    verifier = Telnyx(api_key=settings.api_key, public_key=settings.public_key)
    task = None

    async def worker():
        try:
            while True:
                await engine.process_next()
                await engine.expire_calls()
                await asyncio.sleep(0.1)
        except Exception:
            print(
                "Worker stopped unexpectedly. Hang up and inspect local state.",
                flush=True,
            )

    @asynccontextmanager
    async def lifespan(app):
        nonlocal task
        if run_worker:
            engine.recover()
            task = asyncio.create_task(worker())
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            verifier.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.engine = engine

    @app.get("/health")
    async def health():
        if task is not None and task.done():
            return JSONResponse({"status": "worker stopped"}, status_code=503)
        return {"status": "ok", "product": "Deskline"}

    @app.post("/webhooks/voice")
    async def voice_webhook(request: Request):
        event = await read_event(request, verifier, settings)
        if event is None:
            return {"received": True, "ignored": True}
        added = store.enqueue(event)
        return {"received": True, "duplicate": not added}

    return app
