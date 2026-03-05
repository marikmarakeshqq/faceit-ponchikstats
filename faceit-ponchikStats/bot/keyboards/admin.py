from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def dashboard_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Игроки", callback_data="adm:players")
    builder.button(text="Настройки", callback_data="adm:settings")
    builder.button(text="Добавить", callback_data="adm:add")
    builder.button(text="Последний матч", callback_data="adm:last_match")
    builder.button(text="Обновить", callback_data="adm:refresh")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def tracked_players_keyboard(players: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for player in players:
        nickname = str(player.get("nickname", "Unknown"))
        player_id = str(player.get("faceit_player_id", ""))
        if not player_id:
            continue
        builder.button(text=f"Удалить: {nickname}"[:40], callback_data=f"rm:{player_id}")
    builder.button(text="Добавить игрока", callback_data="adm:add")
    builder.button(text="Назад", callback_data="adm:back")
    builder.adjust(1)
    return builder.as_markup()


def settings_keyboard(
    notifications_enabled: bool,
    poll_interval_sec: int,
    selected_stats: list[str],
) -> InlineKeyboardMarkup:
    selected = set(selected_stats)
    builder = InlineKeyboardBuilder()

    builder.button(
        text=f"Уведомления: {'ВКЛ' if notifications_enabled else 'ВЫКЛ'}",
        callback_data="set:toggle_notifications",
    )

    for interval in (60, 180, 300):
        prefix = "[x] " if poll_interval_sec == interval else ""
        builder.button(text=f"{prefix}{interval}с", callback_data=f"int:{interval}")
    builder.button(text="Свой интервал", callback_data="int:custom")

    stats = [
        ("kd", "K/D"),
        ("adr", "ADR"),
        ("hs", "HS%"),
        ("kills", "Фраги"),
        ("deaths", "Смерти"),
        ("assists", "Ассисты"),
    ]
    for stat_key, label in stats:
        prefix = "[x] " if stat_key in selected else ""
        builder.button(text=f"{prefix}{label}", callback_data=f"st:{stat_key}")

    builder.button(text="Обновить FACEIT API", callback_data="set:api_key")
    builder.button(text="Назад", callback_data="adm:back")

    builder.adjust(1, 3, 1, 3, 3, 1, 1)
    return builder.as_markup()


