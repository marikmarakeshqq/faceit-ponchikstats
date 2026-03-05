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


def _health_payload() -> dict[str, object]:
    return {
        "status": "ok",
        "polling_running": runtime.polling_running,
    }


@app.api_route("/", methods=["GET", "HEAD", "POST", "OPTIONS"])
async def root() -> dict[str, object]:
    return _health_payload()


@app.api_route("/health", methods=["GET", "HEAD", "POST", "OPTIONS"])
async def health() -> dict[str, object]:
    return _health_payload()
