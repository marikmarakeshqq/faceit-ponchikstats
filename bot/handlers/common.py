from __future__ import annotations

import asyncio

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import ChatMemberUpdated, Message

from bot.config import BotConfig
from bot.db import Database


def build_common_router(config: BotConfig, db: Database) -> Router:
    router = Router(name="common")
    private_autodelete_sec = 600

    async def _delete_later(bot: Bot, chat_id: int, message_id: int, delay_sec: int) -> None:
        await asyncio.sleep(delay_sec)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:  # noqa: BLE001
            return

    def schedule_message_delete(bot: Bot, chat_id: int, message_id: int, delay_sec: int = private_autodelete_sec) -> None:
        asyncio.create_task(_delete_later(bot, chat_id, message_id, delay_sec))

    async def answer_private_and_autodelete(message: Message, text: str) -> None:
        sent = await message.answer(text)
        schedule_message_delete(message.bot, message.chat.id, sent.message_id)

    @router.message(CommandStart(), F.chat.type == "private")
    async def start_private_handler(message: Message) -> None:
        schedule_message_delete(message.bot, message.chat.id, message.message_id)
        user = message.from_user
        if user:
            full_name = (f"{user.first_name or ''} {user.last_name or ''}").strip() or "Unknown"
            await db.upsert_user(
                user_id=user.id,
                username=user.username,
                full_name=full_name,
                is_admin=user.id in config.admin_ids,
            )

        if user and user.id in config.admin_ids:
            await answer_private_and_autodelete(
                message,
                "Панель администратора: /admin\n"
                "Команды в ЛС:\n"
                "/add_player &lt;nickname&gt;\n"
                "/remove_player &lt;nickname&gt;\n"
                "/list_players\n\n"
                "В группе используется только /register.",
            )
            return

        await answer_private_and_autodelete(
            message,
            "Бот отслеживает матчи FACEIT CS2 и отправляет результаты в группу.\n"
            "Для подключения группы используйте /register в самой группе.",
        )

    @router.message(Command("register"), F.chat.type.in_({"group", "supergroup"}))
    async def register_group(message: Message) -> None:
        await db.upsert_chat(message.chat.id, message.chat.title)
        await db.set_chat_notifications(message.chat.id, True)
        await message.answer("Группа зарегистрирована. Буду отправлять результаты матчей сюда.")

    @router.message(Command("register"), F.chat.type == "private")
    async def register_private(message: Message) -> None:
        schedule_message_delete(message.bot, message.chat.id, message.message_id)
        await answer_private_and_autodelete(
            message,
            "Команда /register работает только в группе.\n"
            "Добавь бота в группу и вызови /register там.",
        )

    @router.my_chat_member()
    async def bot_membership_handler(event: ChatMemberUpdated) -> None:
        if event.chat.type not in {"group", "supergroup"}:
            return
        if event.new_chat_member.status in {"member", "administrator"}:
            await db.upsert_chat(event.chat.id, event.chat.title)

    return router


