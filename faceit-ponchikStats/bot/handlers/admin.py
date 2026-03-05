from __future__ import annotations

import asyncio
import time
from html import escape
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from bot.config import BotConfig
from bot.db import Database
from bot.keyboards import dashboard_keyboard, settings_keyboard, tracked_players_keyboard
from bot.states import AdminStates
from bot.utils import format_match_caption

from ..services.cards import MatchCardRenderer
from ..services.faceit import FaceitClient, FaceitError


def build_admin_router(
    config: BotConfig,
    db: Database,
    faceit: FaceitClient,
    card_renderer: MatchCardRenderer,
) -> Router:
    router = Router(name="admin")
    last_preview_by_user: dict[int, tuple[str, float]] = {}
    preview_cooldown_sec = 15.0
    private_autodelete_sec = 600

    async def _delete_later(bot: Bot, chat_id: int, message_id: int, delay_sec: int) -> None:
        await asyncio.sleep(delay_sec)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:  # noqa: BLE001
            return

    def schedule_message_delete(bot: Bot, chat_id: int, message_id: int, delay_sec: int = private_autodelete_sec) -> None:
        asyncio.create_task(_delete_later(bot, chat_id, message_id, delay_sec))

    async def answer_and_autodelete(message: Message, text: str, **kwargs: Any) -> Message:
        sent = await message.answer(text, **kwargs)
        schedule_message_delete(message.bot, message.chat.id, sent.message_id)
        return sent

    async def answer_photo_and_autodelete(message: Message, **kwargs: Any) -> Message:
        sent = await message.answer_photo(**kwargs)
        schedule_message_delete(message.bot, message.chat.id, sent.message_id)
        return sent

    def is_admin(user_id: int | None) -> bool:
        return bool(user_id and user_id in config.admin_ids)

    def is_private(chat_type: str) -> bool:
        return chat_type == "private"

    def parse_nickname_arg(args: str | None) -> str | None:
        if not args:
            return None
        value = args.strip()
        if not value:
            return None
        return value.split()[0].strip() or None

    def cleanup_temp_media(path: Path) -> None:
        try:
            if path.exists():
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def to_int(value: object) -> int:
        try:
            if value is None:
                return 0
            if isinstance(value, str):
                cleaned = value.strip()
                if not cleaned:
                    return 0
                return int(float(cleaned))
            return int(value)
        except (TypeError, ValueError):
            return 0

    def is_finished_match(match_item: dict[str, Any]) -> bool:
        status = str(match_item.get("status", "")).upper()
        if status in {"FINISHED", "DONE"}:
            return True
        return bool(match_item.get("finished_at"))

    async def register_user_by_message(message: Message) -> None:
        user = message.from_user
        if not user:
            return
        full_name = (f"{user.first_name or ''} {user.last_name or ''}").strip() or "Unknown"
        await db.upsert_user(
            user_id=user.id,
            username=user.username,
            full_name=full_name,
            is_admin=is_admin(user.id),
        )

    async def register_user_by_callback(callback: CallbackQuery) -> None:
        user = callback.from_user
        if not user:
            return
        full_name = (f"{user.first_name or ''} {user.last_name or ''}").strip() or "Unknown"
        await db.upsert_user(
            user_id=user.id,
            username=user.username,
            full_name=full_name,
            is_admin=is_admin(user.id),
        )

    async def ensure_private_admin_message(message: Message) -> bool:
        if not is_private(message.chat.type):
            return False
        schedule_message_delete(message.bot, message.chat.id, message.message_id)
        if not is_admin(message.from_user.id if message.from_user else None):
            await answer_and_autodelete(message, "Команда доступна только администраторам.")
            return False
        await register_user_by_message(message)
        return True

    async def ensure_private_admin_callback(callback: CallbackQuery) -> bool:
        if callback.message is None or callback.from_user is None:
            return False
        if not is_private(callback.message.chat.type) or not is_admin(callback.from_user.id):
            await callback.answer("Только в ЛС администратора.", show_alert=True)
            return False
        await register_user_by_callback(callback)
        return True

    async def send_or_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
        if callback.message is None:
            await callback.answer()
            return
        try:
            await callback.message.edit_text(text, reply_markup=reply_markup)
            schedule_message_delete(callback.bot, callback.message.chat.id, callback.message.message_id)
        except TelegramBadRequest:
            sent = await callback.message.answer(text, reply_markup=reply_markup)
            schedule_message_delete(callback.bot, sent.chat.id, sent.message_id)
        await callback.answer()

    async def add_player_by_nickname(nickname: str, added_by: int) -> tuple[bool, str]:
        try:
            player = await faceit.get_player_by_nickname(nickname)
        except FaceitError as exc:
            return False, f"Ошибка FACEIT API: {escape(str(exc))}"

        if not player:
            return False, "Игрок не найден в FACEIT."

        player_id = str(player.get("player_id", "")).strip()
        player_nickname = str(player.get("nickname") or nickname).strip()
        if not player_id:
            return False, "Не удалось получить player_id из FACEIT."

        await db.upsert_tracked_player(
            faceit_player_id=player_id,
            nickname=player_nickname,
            avatar_url=player.get("avatar"),
            country=player.get("country"),
            added_by=added_by,
        )
        return True, f"Игрок <b>{escape(player_nickname)}</b> добавлен в отслеживание."

    async def send_players_text(message: Message) -> None:
        players = await db.list_tracked_players()
        if not players:
            await answer_and_autodelete(message, "Список отслеживаемых игроков пуст.")
            return

        lines = ["<b>Отслеживаемые игроки:</b>"]
        for player in players:
            lines.append(
                f"- <b>{escape(str(player['nickname']))}</b> "
                f"(<code>{escape(str(player['faceit_player_id']))}</code>)"
            )
        text = "\n".join(lines)
        if len(text) > 3900:
            text = text[:3900] + "..."
        await answer_and_autodelete(message, text)

    async def resolve_latest_match_id(tracked_players: list[dict[str, Any]]) -> str | None:
        if not tracked_players:
            return None

        tasks = [
            faceit.get_player_recent_matches(str(player["faceit_player_id"]), game="cs2", limit=1)
            for player in tracked_players
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        best_match_id = ""
        best_finished_at = -1
        for result in results:
            if isinstance(result, Exception):
                continue
            for match_item in result:
                if not is_finished_match(match_item):
                    continue
                candidate_id = str(match_item.get("match_id", "")).strip()
                if not candidate_id:
                    continue
                finished_at = to_int(match_item.get("finished_at"))
                if finished_at > best_finished_at:
                    best_finished_at = finished_at
                    best_match_id = candidate_id

        if best_match_id:
            return best_match_id
        return await db.get_latest_match_id()

    async def send_last_match_preview(message: Message, requester_id: int) -> None:
        tracked_players = await db.list_tracked_players()
        if not tracked_players:
            await answer_and_autodelete(message, "Нет отслеживаемых игроков. Добавь игрока через /add_player &lt;nickname&gt;.")
            return

        latest_match_id = await resolve_latest_match_id(tracked_players)
        if not latest_match_id:
            await answer_and_autodelete(message, "Пока нет завершенных матчей.")
            return

        now = time.time()
        last_preview = last_preview_by_user.get(requester_id)
        if last_preview and last_preview[0] == latest_match_id and now - last_preview[1] < preview_cooldown_sec:
            await answer_and_autodelete(message, "Последний матч уже отправлен. Подожди несколько секунд.")
            return
        last_preview_by_user[requester_id] = (latest_match_id, now)

        tracked_ids = {str(player["faceit_player_id"]) for player in tracked_players}
        snapshot = await faceit.build_match_snapshot(latest_match_id, tracked_ids)
        if not snapshot:
            await answer_and_autodelete(message, "Не удалось получить данные последнего матча из FACEIT.")
            return

        teams = snapshot.get("teams", [])
        if not teams:
            await answer_and_autodelete(message, "Данные последнего матча неполные.")
            return

        target_team = next((team for team in teams if team.get("tracked_players")), teams[0])
        display_stats = await db.get_display_stats()
        caption = format_match_caption(snapshot, target_team, display_stats)
        media_path, _ = await card_renderer.render(snapshot, target_team, mode="image")

        try:
            media = FSInputFile(media_path.as_posix())
            await answer_photo_and_autodelete(
                message,
                photo=media,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        finally:
            cleanup_temp_media(media_path)

    async def render_dashboard(target: Message | CallbackQuery) -> None:
        text = "<b>Панель администратора</b>"
        if isinstance(target, CallbackQuery):
            await send_or_edit(target, text, reply_markup=dashboard_keyboard())
            return
        await target.answer(text, reply_markup=dashboard_keyboard())

    async def render_players(callback: CallbackQuery) -> None:
        players = await db.list_tracked_players()
        if not players:
            text = "<b>Отслеживаемые игроки</b>\nСписок пуст."
        else:
            rows = [
                f"- <b>{escape(str(player['nickname']))}</b> "
                f"(<code>{escape(str(player['faceit_player_id']))}</code>)"
                for player in players
            ]
            text = "<b>Отслеживаемые игроки</b>\n" + "\n".join(rows)
            if len(text) > 3900:
                text = text[:3900] + "..."
        await send_or_edit(callback, text, reply_markup=tracked_players_keyboard(players))

    async def render_recent_matches(callback: CallbackQuery) -> None:
        matches = await db.list_recent_matches(limit=20)
        if not matches:
            await send_or_edit(
                callback,
                "<b>Последние матчи</b>\nПока нет сохраненных матчей.",
                reply_markup=dashboard_keyboard(),
            )
            return

        lines = ["<b>Последние матчи</b>"]
        for item in matches:
            nickname = escape(str(item.get("nickname") or item.get("player_id")))
            map_name = escape(str(item.get("map_name") or "Неизвестно"))
            score = escape(str(item.get("score") or "N/A"))
            kd = item.get("kd")
            adr = item.get("adr")
            hs = item.get("hs")
            lines.append(
                f"- <b>{nickname}</b> | {map_name} | {score} | "
                f"K/D {0.0 if kd is None else float(kd):.2f} "
                f"ADR {0.0 if adr is None else float(adr):.1f} "
                f"HS {0.0 if hs is None else float(hs):.0f}%"
            )
        text = "\n".join(lines)
        if len(text) > 3900:
            text = text[:3900] + "..."
        await send_or_edit(callback, text, reply_markup=dashboard_keyboard())

    async def render_settings(callback: CallbackQuery) -> None:
        enabled = await db.is_notifications_enabled()
        interval = await db.get_poll_interval(config.default_poll_interval_sec)
        selected_stats = await db.get_display_stats()

        text = (
            "<b>Настройки</b>\n"
            f"Уведомления: <b>{'ВКЛ' if enabled else 'ВЫКЛ'}</b>\n"
            f"Интервал опроса: <b>{interval} сек</b>\n"
            f"Поля статистики: <code>{escape(','.join(selected_stats))}</code>\n"
            "Медиа: <b>Изображение</b>\n"
        )
        await send_or_edit(
            callback,
            text,
            reply_markup=settings_keyboard(enabled, interval, selected_stats),
        )

    async def render_logs(callback: CallbackQuery) -> None:
        logs = await db.list_notification_logs(limit=20)
        if not logs:
            await send_or_edit(
                callback,
                "<b>Лог уведомлений</b>\nЗаписей пока нет.",
                reply_markup=dashboard_keyboard(),
            )
            return

        lines = ["<b>Лог уведомлений</b>"]
        for row in logs:
            status = "отправлено" if row.get("status") == "sent" else "ошибка"
            lines.append(
                f"- {escape(str(row.get('created_at', '')))} | "
                f"чат {escape(str(row.get('chat_id', '')))} | "
                f"{status} | матч {escape(str(row.get('match_id', '')))} | "
                f"команда {escape(str(row.get('team_id', '')))}"
            )
        text = "\n".join(lines)
        if len(text) > 3900:
            text = text[:3900] + "..."
        await send_or_edit(callback, text, reply_markup=dashboard_keyboard())

    @router.message(Command("admin"))
    async def admin_command(message: Message, state: FSMContext) -> None:
        if not await ensure_private_admin_message(message):
            return
        await state.clear()
        await render_dashboard(message)

    @router.message(Command("list_players"))
    async def list_players_command(message: Message) -> None:
        if not await ensure_private_admin_message(message):
            return
        await send_players_text(message)

    @router.message(Command("add_player"))
    async def add_player_command(message: Message, command: CommandObject, state: FSMContext) -> None:
        if not await ensure_private_admin_message(message):
            return
        await state.clear()

        nickname = parse_nickname_arg(command.args if command else None)
        if not nickname:
            await answer_and_autodelete(message, "Использование: /add_player &lt;nickname&gt;")
            return

        success, text = await add_player_by_nickname(
            nickname=nickname,
            added_by=message.from_user.id if message.from_user else 0,
        )
        await answer_and_autodelete(message, text, reply_markup=dashboard_keyboard() if success else None)

    @router.message(Command("remove_player"))
    async def remove_player_command(message: Message, command: CommandObject, state: FSMContext) -> None:
        if not await ensure_private_admin_message(message):
            return
        await state.clear()

        nickname = parse_nickname_arg(command.args if command else None)
        if not nickname:
            await answer_and_autodelete(message, "Использование: /remove_player &lt;nickname&gt;")
            return

        removed = await db.remove_tracked_player_by_nickname(nickname)
        if removed:
            await answer_and_autodelete(message, f"Игрок <b>{escape(nickname)}</b> удален из отслеживания.")
            return

        tracked = await db.get_tracked_player_by_nickname(nickname)
        if tracked:
            await db.remove_tracked_player(str(tracked["faceit_player_id"]))
            await answer_and_autodelete(message, f"Игрок <b>{escape(nickname)}</b> удален из отслеживания.")
            return

        await answer_and_autodelete(message, f"Игрок <b>{escape(nickname)}</b> не найден в списке отслеживания.")

    @router.callback_query(F.data.startswith("adm:"))
    async def dashboard_callbacks(callback: CallbackQuery, state: FSMContext) -> None:
        if not await ensure_private_admin_callback(callback):
            return

        action = callback.data.split(":", maxsplit=1)[1]
        if action in {"back", "refresh"}:
            await state.clear()
            await render_dashboard(callback)
        elif action == "players":
            await state.clear()
            await render_players(callback)
        elif action == "recent":
            await state.clear()
            await callback.answer("Кнопка удалена.")
            await render_dashboard(callback)
        elif action == "settings":
            await state.clear()
            await render_settings(callback)
        elif action == "logs":
            await state.clear()
            await callback.answer("Раздел логов отключен.")
            await render_dashboard(callback)
        elif action == "add":
            await state.set_state(AdminStates.waiting_player_nickname)
            await send_or_edit(
                callback,
                "Отправь никнейм FACEIT для добавления в отслеживание.\n"
                "Или используй команду /add_player &lt;nickname&gt;.",
                reply_markup=None,
            )
        elif action == "last_match":
            await state.clear()
            await callback.answer("Отправляю последний матч...")
            if callback.message:
                await send_last_match_preview(callback.message, callback.from_user.id)
        else:
            await callback.answer("Неизвестное действие.")

    @router.callback_query(F.data.startswith("rm:"))
    async def remove_player_callback(callback: CallbackQuery) -> None:
        if not await ensure_private_admin_callback(callback):
            return

        player_id = callback.data.split(":", maxsplit=1)[1].strip()
        if not player_id:
            await callback.answer("Некорректный id игрока.", show_alert=True)
            return

        await db.remove_tracked_player(player_id)
        await callback.answer("Игрок удален.")
        await render_players(callback)

    @router.message(AdminStates.waiting_player_nickname)
    async def add_player_state(message: Message, state: FSMContext) -> None:
        if not await ensure_private_admin_message(message):
            return

        nickname = (message.text or "").strip()
        if not nickname:
            await answer_and_autodelete(message, "Никнейм не может быть пустым. Отправь ник FACEIT.")
            return

        success, text = await add_player_by_nickname(
            nickname=nickname,
            added_by=message.from_user.id if message.from_user else 0,
        )
        if success:
            await state.clear()
        await answer_and_autodelete(message, text, reply_markup=dashboard_keyboard() if success else None)

    @router.callback_query(F.data == "set:toggle_notifications")
    async def toggle_notifications(callback: CallbackQuery) -> None:
        if not await ensure_private_admin_callback(callback):
            return
        current = await db.is_notifications_enabled()
        await db.set_notifications_enabled(not current)
        await callback.answer("Готово.")
        await render_settings(callback)

    @router.callback_query(F.data.startswith("int:"))
    async def set_interval(callback: CallbackQuery, state: FSMContext) -> None:
        if not await ensure_private_admin_callback(callback):
            return

        raw = callback.data.split(":", maxsplit=1)[1]
        if raw == "custom":
            await state.set_state(AdminStates.waiting_custom_interval)
            await send_or_edit(
                callback,
                "Отправь свой интервал опроса в секундах (минимум 10).",
                reply_markup=None,
            )
            return

        try:
            interval = max(10, int(raw))
        except ValueError:
            await callback.answer("Неверный интервал.", show_alert=True)
            return

        await db.set_setting("poll_interval_sec", str(interval))
        await callback.answer("Интервал обновлен.")
        await render_settings(callback)

    @router.message(AdminStates.waiting_custom_interval)
    async def set_custom_interval_state(message: Message, state: FSMContext) -> None:
        if not await ensure_private_admin_message(message):
            return

        try:
            interval = max(10, int((message.text or "").strip()))
        except ValueError:
            await answer_and_autodelete(message, "Отправь корректное число. Например: 60")
            return

        await db.set_setting("poll_interval_sec", str(interval))
        await state.clear()
        await answer_and_autodelete(message, f"Интервал сохранен: {interval} сек.", reply_markup=dashboard_keyboard())

    @router.callback_query(F.data.startswith("st:"))
    async def toggle_display_stat(callback: CallbackQuery) -> None:
        if not await ensure_private_admin_callback(callback):
            return

        stat = callback.data.split(":", maxsplit=1)[1]
        allowed = {"kd", "adr", "hs", "kills", "deaths", "assists"}
        if stat not in allowed:
            await callback.answer("Неверный параметр.", show_alert=True)
            return

        selected = set(await db.get_display_stats())
        if stat in selected:
            selected.remove(stat)
        else:
            selected.add(stat)
        if not selected:
            selected = {"kd"}

        await db.set_display_stats(sorted(selected))
        await callback.answer("Поля статистики обновлены.")
        await render_settings(callback)

    @router.callback_query(F.data == "set:api_key")
    async def request_api_key(callback: CallbackQuery, state: FSMContext) -> None:
        if not await ensure_private_admin_callback(callback):
            return

        await state.set_state(AdminStates.waiting_faceit_api_key)
        await send_or_edit(
            callback,
            "Отправь новый FACEIT API ключ. Он сохранится в SQLite.",
            reply_markup=None,
        )

    @router.message(AdminStates.waiting_faceit_api_key)
    async def save_api_key_state(message: Message, state: FSMContext) -> None:
        if not await ensure_private_admin_message(message):
            return

        api_key = (message.text or "").strip()
        if len(api_key) < 10:
            await answer_and_autodelete(message, "Ключ выглядит некорректно. Отправь полный FACEIT API ключ.")
            return

        await db.set_setting("faceit_api_key_override", api_key)
        faceit.set_api_key(api_key)
        await state.clear()
        await answer_and_autodelete(message, "FACEIT API ключ обновлен.", reply_markup=dashboard_keyboard())

    return router

