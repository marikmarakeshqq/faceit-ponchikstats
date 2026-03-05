from __future__ import annotations

import asyncio

from bot.runtime import BotRuntime


async def run_bot() -> None:
    runtime = BotRuntime()
    await runtime.start(handle_signals=True)
    try:
        await runtime.wait_until_stopped()
    finally:
        await runtime.stop()


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
