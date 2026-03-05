# FACEIT CS2 Telegram Bot

Телеграм-бот для отслеживания завершенных матчей FACEIT CS2 по выбранным игрокам.

После завершения матча бот отправляет в зарегистрированную группу:
- текст с результатами матча (карта, счет, статистика игроков),
- картинку-карточку матча (Pillow).

## Основные возможности

1. Настройка отслеживаемых игроков через ЛС (только админ)
- `/add_player <nickname>` — добавить игрока в отслеживание.
- `/remove_player <nickname>` — удалить игрока из отслеживания.
- `/list_players` — список отслеживаемых игроков.
- `/admin` — админ-панель с плитками и настройками.

2. Работа в группе
- В группе используется только команда `/register`.
- После регистрации бот молча отслеживает матчи и публикует результаты.
- Лишних диалогов в группе бот не ведет.

3. Публикация результатов
- Название карты.
- Итоговый счет.
- Данные по отслеживаемым игрокам (K/D, ADR, HS%, K/D/A и т.д. по настройке).
- Карточка матча в виде изображения.

4. Дедупликация уведомлений
- Для одного матча и одной команды отправляется только одно уведомление:
  `match_id + team_id`.

5. Тестовая кнопка в админ-панели
- `🧪 Последний матч` — временная кнопка для тестов.
- В ЛС отправляет результаты последнего сохраненного завершенного матча.

## Технологии

- Python 3.11+
- `aiogram` — Telegram Bot API
- `httpx` — Faceit API
- `aiosqlite` — SQLite
- `Pillow` — генерация карточек

## Структура проекта

```text
bot/
  main.py
  config.py
  states.py
  logging_config.py
  db/
    schema.sql
    database.py
  handlers/
    common.py
    admin.py
  keyboards/
    admin.py
  services/
    faceit.py
    poller.py
    notifier.py
    cards.py
  utils/
    formatting.py
.env.example
requirements.txt
Procfile
render.yaml
runtime.txt
```

## База данных (SQLite)

Файл схемы: [schema.sql](C:/Users/marik/OneDrive/Документы/Browser/faceit-ponchikStats/bot/db/schema.sql)

Основные таблицы:
- `bot_users` — пользователи бота (включая админов).
- `tracked_players` — отслеживаемые игроки FACEIT.
- `notification_chats` — зарегистрированные группы.
- `bot_settings` — глобальные настройки бота.
- `player_recent_matches` — последние матчи/статистика.
- `processed_match_teams` — защита от дублей уведомлений.
- `notification_logs` — лог отправки уведомлений.

## Быстрый старт (локально)

1. Установить зависимости:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Создать `.env` из шаблона:

```powershell
Copy-Item .env.example .env
```

3. Заполнить `.env`:
- `TELEGRAM_BOT_TOKEN`
- `FACEIT_API_KEY`
- `ADMIN_IDS` (через запятую, Telegram user_id админов)

4. Запустить:

```powershell
python -m bot.main
```

## Использование

1. В ЛС с ботом (админ):
- `/admin`
- `/add_player m1sterioso`
- `/list_players`

2. В группе:
- добавить бота в группу,
- дать право отправлять сообщения/медиа,
- выполнить `/register`.

После этого бот сам публикует результаты завершенных матчей.

## Деплой (24/7)

### Render (free web service)
- Использовать `render.yaml`.
- Команда запуска: `uvicorn bot.web:app --host 0.0.0.0 --port $PORT`.
- Для внешнего мониторинга доступен `GET /health`.
- Добавить env-переменные: `TELEGRAM_BOT_TOKEN`, `FACEIT_API_KEY`, `ADMIN_IDS`.

### Railway
- Использовать `Procfile` или указать `uvicorn bot.web:app --host 0.0.0.0 --port $PORT` как start command.
- Добавить те же env-переменные.

## Примечания

- Бот работает в режиме polling.
- Если токен бота попадал в открытый чат/лог, обязательно перевыпусти его в BotFather.
