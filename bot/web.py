from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from bot.runtime import BotRuntime


runtime = BotRuntime()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await runtime.start(handle_signals=False)
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "polling_running": runtime.polling_running,
    }
