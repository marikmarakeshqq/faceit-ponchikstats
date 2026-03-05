from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import BotConfig
from bot.db import Database
from bot.handlers import build_admin_router, build_common_router
from bot.logging_config import setup_logging
from bot.services import FaceitClient, MatchCardRenderer, MatchNotifier, MatchPoller


logger = logging.getLogger(__name__)


class BotRuntime:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._started = False

        self._db: Database | None = None
        self._faceit: FaceitClient | None = None
        self._bot: Bot | None = None
        self._dispatcher: Dispatcher | None = None
        self._poller: MatchPoller | None = None

        self._poller_task: asyncio.Task[None] | None = None
        self._polling_task: asyncio.Task[None] | None = None

    @property
    def polling_running(self) -> bool:
        return self._polling_task is not None and not self._polling_task.done()

    async def start(self, *, handle_signals: bool) -> None:
        async with self._lock:
            if self._started:
                logger.info("Bot runtime already started; skipping duplicate start.")
                return

            setup_logging()
            config = BotConfig.from_env()
            config.ensure_directories()

            db: Database | None = None
            faceit: FaceitClient | None = None
            bot: Bot | None = None
            dispatcher: Dispatcher | None = None
            poller: MatchPoller | None = None
            poller_task: asyncio.Task[None] | None = None
            polling_task: asyncio.Task[None] | None = None

            try:
                db = Database(config.database_path)
                await db.init()
                await db.ensure_default_settings(
                    default_poll_interval_sec=config.default_poll_interval_sec,
                )
                poll_interval_migrated = await db.get_setting("poll_interval_default_180_migrated", "0")
                if poll_interval_migrated != "1":
                    current_poll_interval = await db.get_setting("poll_interval_sec")
                    try:
                        parsed_poll_interval = (
                            int(float(current_poll_interval)) if current_poll_interval is not None else None
                        )
                    except ValueError:
                        parsed_poll_interval = None
                    if parsed_poll_interval == 60:
                        await db.set_setting("poll_interval_sec", "180")
                    await db.set_setting("poll_interval_default_180_migrated", "1")
                await db.clear_notification_logs()

                api_key_override = await db.get_setting("faceit_api_key_override")
                effective_api_key = api_key_override or config.faceit_api_key
                faceit = FaceitClient(api_key=effective_api_key, timeout_sec=config.request_timeout_sec)
                card_renderer = MatchCardRenderer(config.card_output_dir)

                bot = Bot(
                    token=config.telegram_token,
                    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
                )
                dispatcher = Dispatcher(storage=MemoryStorage())
                dispatcher.include_router(build_common_router(config, db))
                dispatcher.include_router(build_admin_router(config, db, faceit, card_renderer))

                notifier = MatchNotifier(bot=bot, card_renderer=card_renderer)
                poller = MatchPoller(
                    db=db,
                    faceit_client=faceit,
                    notifier=notifier,
                    default_poll_interval_sec=config.default_poll_interval_sec,
                )

                poller_task = asyncio.create_task(poller.run(), name="match-poller")
                polling_task = asyncio.create_task(
                    dispatcher.start_polling(
                        bot,
                        allowed_updates=dispatcher.resolve_used_update_types(),
                        handle_signals=handle_signals,
                        close_bot_session=False,
                    ),
                    name="telegram-polling",
                )
            except Exception:
                if poller is not None:
                    poller.stop()
                if polling_task is not None:
                    polling_task.cancel()
                    with suppress(Exception):
                        await polling_task
                if poller_task is not None:
                    with suppress(Exception):
                        await poller_task
                if faceit is not None:
                    with suppress(Exception):
                        await faceit.close()
                if db is not None:
                    with suppress(Exception):
                        await db.close()
                if bot is not None:
                    with suppress(Exception):
                        await bot.session.close()
                raise

            if dispatcher is None or poller is None or poller_task is None or polling_task is None:
                raise RuntimeError("Bot runtime startup failed: internal components were not initialized.")

            self._db = db
            self._faceit = faceit
            self._bot = bot
            self._dispatcher = dispatcher
            self._poller = poller
            self._poller_task = poller_task
            self._polling_task = polling_task
            self._started = True
            logger.info("Bot startup complete.")

    async def wait_until_stopped(self) -> None:
        polling_task = self._polling_task
        if polling_task is None:
            raise RuntimeError("Bot runtime is not started.")
        await polling_task

    async def stop(self) -> None:
        async with self._lock:
            if not self._started and self._polling_task is None and self._poller_task is None:
                return

            poller = self._poller
            dispatcher = self._dispatcher
            polling_task = self._polling_task
            poller_task = self._poller_task

            if poller is not None:
                poller.stop()

            if dispatcher is not None and polling_task is not None and not polling_task.done():
                with suppress(RuntimeError):
                    await dispatcher.stop_polling()

            tasks: list[asyncio.Task[None]] = []
            if polling_task is not None:
                tasks.append(polling_task)
            if poller_task is not None:
                tasks.append(poller_task)

            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                        logger.error("Background task failed during shutdown: %r", result)

            if self._faceit is not None:
                with suppress(Exception):
                    await self._faceit.close()
            if self._db is not None:
                with suppress(Exception):
                    await self._db.close()
            if self._bot is not None:
                with suppress(Exception):
                    await self._bot.session.close()

            self._db = None
            self._faceit = None
            self._bot = None
            self._dispatcher = None
            self._poller = None
            self._poller_task = None
            self._polling_task = None
            self._started = False
            logger.info("Shutdown complete.")
