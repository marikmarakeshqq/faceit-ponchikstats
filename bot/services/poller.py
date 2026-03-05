from __future__ import annotations

import asyncio
import logging
from typing import Any

from bot.db import Database

from .faceit import FaceitClient, FaceitError
from .notifier import MatchNotifier


logger = logging.getLogger(__name__)


class MatchPoller:
    _MAINTENANCE_INTERVAL_SEC = 3600
    _MAX_PROCESSED_MATCH_TEAMS = 5000
    _MAX_RECENT_MATCHES = 5000
    _MAX_NOTIFICATION_LOGS = 1000

    def __init__(
        self,
        db: Database,
        faceit_client: FaceitClient,
        notifier: MatchNotifier,
        default_poll_interval_sec: int,
    ) -> None:
        self._db = db
        self._faceit = faceit_client
        self._notifier = notifier
        self._default_poll_interval_sec = default_poll_interval_sec
        self._stop_event = asyncio.Event()
        self._last_maintenance_monotonic = 0.0

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        logger.info("Match poller started.")
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001
                logger.exception("Poll iteration failed.")

            try:
                await self._maybe_run_maintenance()
            except Exception:  # noqa: BLE001
                logger.exception("Database maintenance failed.")

            interval = await self._db.get_poll_interval(self._default_poll_interval_sec)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except TimeoutError:
                continue
        logger.info("Match poller stopped.")

    async def run_once(self) -> None:
        if not await self._db.is_notifications_enabled():
            return

        tracked_players = await self._db.list_tracked_players()
        if not tracked_players:
            return

        chats = await self._db.list_notification_chats(enabled_only=True)
        chat_ids = [int(chat["chat_id"]) for chat in chats]
        if not chat_ids:
            return

        tracked_ids = {str(player["faceit_player_id"]) for player in tracked_players}
        candidate_match_ids = await self._collect_candidate_matches(tracked_players)
        if not candidate_match_ids:
            return

        display_stats = await self._db.get_display_stats()

        for match_id in candidate_match_ids:
            snapshot = await self._faceit.build_match_snapshot(match_id, tracked_ids)
            if not snapshot:
                continue

            for team in snapshot.get("teams", []):
                tracked = team.get("tracked_players", [])
                if not tracked:
                    continue

                team_id = str(team.get("team_id") or team.get("faction") or "")
                if not team_id:
                    continue

                should_send = await self._db.mark_match_team_notified(match_id, team_id)
                if not should_send:
                    continue

                await self._notifier.notify_team_result(
                    chat_ids=chat_ids,
                    snapshot=snapshot,
                    team=team,
                    display_stats=display_stats,
                )
                await self._store_recent_stats(snapshot, team)

    async def _maybe_run_maintenance(self) -> None:
        now = asyncio.get_running_loop().time()
        if now - self._last_maintenance_monotonic < self._MAINTENANCE_INTERVAL_SEC:
            return

        self._last_maintenance_monotonic = now
        await self._db.optimize_storage(
            max_processed_match_teams=self._MAX_PROCESSED_MATCH_TEAMS,
            max_recent_matches=self._MAX_RECENT_MATCHES,
            max_notification_logs=self._MAX_NOTIFICATION_LOGS,
        )

    async def _collect_candidate_matches(self, tracked_players: list[dict[str, Any]]) -> list[str]:
        tasks = [
            self._faceit.get_player_recent_matches(str(player["faceit_player_id"]), game="cs2", limit=2)
            for player in tracked_players
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        candidate_ids: set[str] = set()
        for result in results:
            if isinstance(result, Exception):
                if isinstance(result, FaceitError):
                    logger.warning("Faceit error during history fetch: %s", result)
                else:
                    logger.exception("Unexpected error during history fetch: %s", result)
                continue

            for match in result:
                if self._is_finished_match(match):
                    match_id = str(match.get("match_id", "")).strip()
                    if match_id:
                        candidate_ids.add(match_id)

        return sorted(candidate_ids)

    @staticmethod
    def _is_finished_match(match_item: dict[str, Any]) -> bool:
        status = str(match_item.get("status", "")).upper()
        if status in {"FINISHED", "DONE"}:
            return True
        return bool(match_item.get("finished_at"))

    async def _store_recent_stats(self, snapshot: dict, team: dict) -> None:
        teams = snapshot.get("teams", [])
        score = "N/A"
        if len(teams) >= 2:
            score = f"{teams[0].get('score', '?')}:{teams[1].get('score', '?')}"

        for player in team.get("tracked_players", []):
            await self._db.upsert_recent_match(
                player_id=str(player.get("player_id", "")),
                match_id=str(snapshot.get("match_id", "")),
                map_name=str(snapshot.get("map_name", "Unknown map")),
                score=score,
                kd=player.get("kd"),
                adr=player.get("adr"),
                hs=player.get("hs"),
                kills=player.get("kills"),
                deaths=player.get("deaths"),
                assists=player.get("assists"),
                result=str(team.get("score", "?")),
                played_at=snapshot.get("finished_at"),
            )
