from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite


DEFAULT_DISPLAY_STATS = "kd,adr,hs,kills,deaths,assists"


class Database:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path.as_posix())
        self._conn.row_factory = aiosqlite.Row

        schema_path = Path(__file__).with_name("schema.sql")
        schema_sql = schema_path.read_text(encoding="utf-8")
        async with self._lock:
            await self._conn.executescript(schema_sql)
            await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not initialized.")
        return self._conn

    async def _fetchone(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        conn = self._connection()
        async with self._lock:
            cursor = await conn.execute(query, params)
            row = await cursor.fetchone()
            await cursor.close()
        return dict(row) if row else None

    async def _fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        conn = self._connection()
        async with self._lock:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            await cursor.close()
        return [dict(row) for row in rows]

    async def _execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        conn = self._connection()
        async with self._lock:
            await conn.execute(query, params)
            await conn.commit()

    async def ensure_default_settings(self, default_poll_interval_sec: int) -> None:
        defaults = {
            "notifications_enabled": "1",
            "poll_interval_sec": str(default_poll_interval_sec),
            "display_stats": DEFAULT_DISPLAY_STATS,
        }
        conn = self._connection()
        async with self._lock:
            for key, value in defaults.items():
                await conn.execute(
                    """
                    INSERT INTO bot_settings(key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO NOTHING
                    """,
                    (key, value),
                )
            await conn.commit()

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = await self._fetchone("SELECT value FROM bot_settings WHERE key = ?", (key,))
        if not row:
            return default
        return str(row["value"])

    async def set_setting(self, key: str, value: str) -> None:
        await self._execute(
            """
            INSERT INTO bot_settings(key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, value),
        )

    async def is_notifications_enabled(self) -> bool:
        value = await self.get_setting("notifications_enabled", "1")
        return str(value) == "1"

    async def set_notifications_enabled(self, enabled: bool) -> None:
        await self.set_setting("notifications_enabled", "1" if enabled else "0")

    async def get_poll_interval(self, fallback_value: int) -> int:
        raw = await self.get_setting("poll_interval_sec", str(fallback_value))
        try:
            parsed = int(raw or fallback_value)
        except ValueError:
            parsed = fallback_value
        return max(10, parsed)

    async def get_display_stats(self) -> list[str]:
        raw = await self.get_setting("display_stats", DEFAULT_DISPLAY_STATS)
        return [item.strip() for item in (raw or "").split(",") if item.strip()]

    async def set_display_stats(self, stats: list[str]) -> None:
        value = ",".join(sorted(set(stats)))
        await self.set_setting("display_stats", value)

    async def upsert_chat(self, chat_id: int, title: str | None) -> None:
        await self._execute(
            """
            INSERT INTO notification_chats(chat_id, title, notifications_enabled, updated_at)
            VALUES (?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, title or ""),
        )

    async def upsert_user(self, user_id: int, username: str | None, full_name: str, is_admin: bool) -> None:
        await self._execute(
            """
            INSERT INTO bot_users(user_id, username, full_name, is_admin, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                is_admin = excluded.is_admin,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, username or "", full_name, 1 if is_admin else 0),
        )

    async def set_chat_notifications(self, chat_id: int, enabled: bool) -> None:
        await self._execute(
            """
            UPDATE notification_chats
            SET notifications_enabled = ?, updated_at = CURRENT_TIMESTAMP
            WHERE chat_id = ?
            """,
            (1 if enabled else 0, chat_id),
        )

    async def list_notification_chats(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        if enabled_only:
            return await self._fetchall(
                """
                SELECT chat_id, title, notifications_enabled
                FROM notification_chats
                WHERE notifications_enabled = 1
                ORDER BY created_at ASC
                """
            )
        return await self._fetchall(
            """
            SELECT chat_id, title, notifications_enabled
            FROM notification_chats
            ORDER BY created_at ASC
            """
        )

    async def upsert_tracked_player(
        self,
        faceit_player_id: str,
        nickname: str,
        avatar_url: str | None,
        country: str | None,
        added_by: int,
    ) -> None:
        await self._execute(
            """
            INSERT INTO tracked_players(faceit_player_id, nickname, avatar_url, country, added_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(faceit_player_id) DO UPDATE SET
                nickname = excluded.nickname,
                avatar_url = excluded.avatar_url,
                country = excluded.country
            """,
            (faceit_player_id, nickname, avatar_url, country, added_by),
        )

    async def remove_tracked_player(self, faceit_player_id: str) -> None:
        await self._execute(
            "DELETE FROM tracked_players WHERE faceit_player_id = ?",
            (faceit_player_id,),
        )

    async def remove_tracked_player_by_nickname(self, nickname: str) -> bool:
        conn = self._connection()
        async with self._lock:
            cursor = await conn.execute(
                "DELETE FROM tracked_players WHERE LOWER(nickname) = LOWER(?)",
                (nickname,),
            )
            await conn.commit()
            deleted = cursor.rowcount or 0
            await cursor.close()
        return deleted > 0

    async def get_tracked_player_by_nickname(self, nickname: str) -> dict[str, Any] | None:
        return await self._fetchone(
            """
            SELECT id, faceit_player_id, nickname, avatar_url, country, created_at
            FROM tracked_players
            WHERE LOWER(nickname) = LOWER(?)
            LIMIT 1
            """,
            (nickname,),
        )

    async def list_tracked_players(self) -> list[dict[str, Any]]:
        return await self._fetchall(
            """
            SELECT id, faceit_player_id, nickname, avatar_url, country, created_at
            FROM tracked_players
            ORDER BY nickname COLLATE NOCASE ASC
            """
        )

    async def mark_match_team_notified(self, match_id: str, team_id: str) -> bool:
        conn = self._connection()
        async with self._lock:
            try:
                await conn.execute(
                    """
                    INSERT INTO processed_match_teams(match_id, team_id)
                    VALUES (?, ?)
                    """,
                    (match_id, team_id),
                )
                await conn.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    async def upsert_recent_match(
        self,
        player_id: str,
        match_id: str,
        map_name: str,
        score: str,
        kd: float | None,
        adr: float | None,
        hs: float | None,
        kills: int | None,
        deaths: int | None,
        assists: int | None,
        result: str,
        played_at: str | None,
    ) -> None:
        await self._execute(
            """
            INSERT INTO player_recent_matches(
                player_id,
                match_id,
                map_name,
                score,
                kd,
                adr,
                hs,
                kills,
                deaths,
                assists,
                result,
                played_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(player_id, match_id) DO UPDATE SET
                map_name = excluded.map_name,
                score = excluded.score,
                kd = excluded.kd,
                adr = excluded.adr,
                hs = excluded.hs,
                kills = excluded.kills,
                deaths = excluded.deaths,
                assists = excluded.assists,
                result = excluded.result,
                played_at = excluded.played_at
            """,
            (
                player_id,
                match_id,
                map_name,
                score,
                kd,
                adr,
                hs,
                kills,
                deaths,
                assists,
                result,
                played_at,
            ),
        )

    async def list_recent_matches(self, limit: int = 20) -> list[dict[str, Any]]:
        return await self._fetchall(
            """
            SELECT
                prm.player_id,
                tp.nickname,
                prm.match_id,
                prm.map_name,
                prm.score,
                prm.kd,
                prm.adr,
                prm.hs,
                prm.kills,
                prm.deaths,
                prm.assists,
                prm.result,
                prm.played_at
            FROM player_recent_matches prm
            LEFT JOIN tracked_players tp ON tp.faceit_player_id = prm.player_id
            ORDER BY COALESCE(prm.played_at, prm.created_at) DESC
            LIMIT ?
            """,
            (limit,),
        )

    async def get_latest_match_id(self) -> str | None:
        row = await self._fetchone(
            """
            SELECT match_id
            FROM player_recent_matches
            ORDER BY COALESCE(played_at, created_at) DESC
            LIMIT 1
            """
        )
        if not row:
            return None
        match_id = str(row.get("match_id") or "").strip()
        return match_id or None

    async def add_notification_log(
        self,
        chat_id: int,
        match_id: str,
        team_id: str,
        message_text: str,
        media_type: str,
        status: str,
        error: str | None,
    ) -> None:
        await self._execute(
            """
            INSERT INTO notification_logs(
                chat_id,
                match_id,
                team_id,
                message_text,
                media_type,
                status,
                error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (chat_id, match_id, team_id, message_text, media_type, status, error),
        )

    async def clear_notification_logs(self) -> None:
        await self._execute("DELETE FROM notification_logs")

    async def list_notification_logs(self, limit: int = 30) -> list[dict[str, Any]]:
        return await self._fetchall(
            """
            SELECT id, chat_id, match_id, team_id, media_type, status, error, created_at
            FROM notification_logs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )

    async def optimize_storage(
        self,
        max_processed_match_teams: int = 5000,
        max_recent_matches: int = 5000,
        max_notification_logs: int = 1000,
    ) -> None:
        conn = self._connection()
        async with self._lock:
            if max_processed_match_teams > 0:
                await conn.execute(
                    """
                    DELETE FROM processed_match_teams
                    WHERE id NOT IN (
                        SELECT id
                        FROM processed_match_teams
                        ORDER BY id DESC
                        LIMIT ?
                    )
                    """,
                    (max_processed_match_teams,),
                )

            if max_recent_matches > 0:
                await conn.execute(
                    """
                    DELETE FROM player_recent_matches
                    WHERE id NOT IN (
                        SELECT id
                        FROM player_recent_matches
                        ORDER BY COALESCE(played_at, created_at) DESC, id DESC
                        LIMIT ?
                    )
                    """,
                    (max_recent_matches,),
                )

            if max_notification_logs > 0:
                await conn.execute(
                    """
                    DELETE FROM notification_logs
                    WHERE id NOT IN (
                        SELECT id
                        FROM notification_logs
                        ORDER BY id DESC
                        LIMIT ?
                    )
                    """,
                    (max_notification_logs,),
                )

            await conn.commit()

            # Best-effort pragmas: skip if SQLite is temporarily locked.
            try:
                await conn.execute("PRAGMA optimize")
            except sqlite3.OperationalError:
                pass

            try:
                await conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.OperationalError:
                pass
