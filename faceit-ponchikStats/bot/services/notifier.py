from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from bot.utils import format_match_caption

from .cards import MatchCardRenderer


logger = logging.getLogger(__name__)


class MatchNotifier:
    def __init__(self, bot: Bot, card_renderer: MatchCardRenderer) -> None:
        self._bot = bot
        self._card_renderer = card_renderer

    async def notify_team_result(
        self,
        chat_ids: list[int],
        snapshot: dict,
        team: dict,
        display_stats: list[str],
    ) -> None:
        caption = format_match_caption(snapshot, team, display_stats)
        media_path, _ = await self._card_renderer.render(snapshot, team, mode="image")
        try:
            for chat_id in chat_ids:
                try:
                    media = FSInputFile(media_path.as_posix())
                    await self._bot.send_photo(
                        chat_id=chat_id,
                        photo=media,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Notification send failed for chat=%s match=%s team=%s",
                        chat_id,
                        snapshot.get("match_id"),
                        team.get("team_id"),
                    )
        finally:
            self._cleanup_media(media_path)

    @staticmethod
    def _cleanup_media(path: Path) -> None:
        try:
            if path.exists():
                path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete temporary media file: %s", path)
