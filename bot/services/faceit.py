from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx


logger = logging.getLogger(__name__)


class FaceitError(RuntimeError):
    """Raised when Faceit API request fails."""


def _to_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.replace("%", "").strip()
            if cleaned == "":
                return None
            return float(cleaned)
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    parsed = _to_float(value)
    if parsed is None:
        return None
    return int(parsed)


def _sort_metric_float(value: object) -> float:
    parsed = _to_float(value)
    if parsed is None:
        return -1.0
    return float(parsed)


def _sort_metric_int(value: object) -> int:
    parsed = _to_int(value)
    if parsed is None:
        return -1
    return int(parsed)


def _sorted_team_players(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Build leaderboard-like order inside team:
    1) K/D, 2) Kills, 3) ADR, 4) HS%, 5) nickname.
    """
    return sorted(
        players,
        key=lambda p: (
            -_sort_metric_float(p.get("kd")),
            -_sort_metric_int(p.get("kills")),
            -_sort_metric_float(p.get("adr")),
            -_sort_metric_float(p.get("hs")),
            str(p.get("nickname", "")).lower(),
        ),
    )


def _extract_rws(pstats: dict[str, Any]) -> float | None:
    """
    Read real RWS only from explicit RWS-like keys.
    Never fallback to generic "Rating", because it is a different metric.
    """
    explicit_keys = (
        "RWS",
        "RWS %",
        "Round Win Share",
        "Round Win Share %",
    )
    for key in explicit_keys:
        value = _to_float(pstats.get(key))
        if value is not None:
            return value

    for key, raw_value in pstats.items():
        normalized = str(key).strip().lower()
        if "rws" in normalized or "round win share" in normalized:
            value = _to_float(raw_value)
            if value is not None:
                return value
    return None


def _calc_rws_from_kr_ratio(kr_ratio: float | None, total_rounds: int | None) -> float | None:
    """
    FACEIT v4 match stats often omit real RWS and only provide K/R Ratio.
    For card display we normalize K/R to an RWS-like scale:
    RWS ~= K/R * (200 / total_rounds)
    """
    if kr_ratio is None:
        return None
    if total_rounds is None or total_rounds <= 0:
        return None
    return float(kr_ratio) * (200.0 / float(total_rounds))


class FaceitClient:
    def __init__(self, api_key: str, timeout_sec: float = 20.0) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(base_url="https://open.faceit.com", timeout=timeout_sec)
        self._player_rank_cache: dict[str, tuple[float, int | None, int | None]] = {}
        self._player_rank_ttl_sec = 900.0

    def set_api_key(self, api_key: str) -> None:
        self._api_key = api_key.strip()

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not self._api_key:
            raise FaceitError("FACEIT API key is empty.")

        response = await self._client.get(
            path,
            params=params,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )

        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise FaceitError(f"Faceit API error {response.status_code}: {response.text[:300]}")
        return response.json()

    async def get_player_by_nickname(self, nickname: str) -> dict[str, Any] | None:
        if not nickname.strip():
            return None
        return await self._request("/data/v4/players", params={"nickname": nickname.strip()})

    async def get_player_recent_matches(
        self, player_id: str, game: str = "cs2", limit: int = 10, offset: int = 0
    ) -> list[dict[str, Any]]:
        payload = await self._request(
            f"/data/v4/players/{player_id}/history",
            params={"game": game, "limit": limit, "offset": offset},
        )
        if not payload:
            return []
        items = payload.get("items")
        if isinstance(items, list):
            return items
        return []

    async def get_match(self, match_id: str) -> dict[str, Any] | None:
        return await self._request(f"/data/v4/matches/{match_id}")

    async def get_match_stats(self, match_id: str) -> dict[str, Any] | None:
        return await self._request(f"/data/v4/matches/{match_id}/stats")

    async def get_player(self, player_id: str) -> dict[str, Any] | None:
        return await self._request(f"/data/v4/players/{player_id}")

    async def get_player_rank_elo(self, player_id: str, game: str = "cs2") -> tuple[int | None, int | None]:
        now = time.monotonic()
        cached = self._player_rank_cache.get(player_id)
        if cached and now - cached[0] < self._player_rank_ttl_sec:
            return cached[1], cached[2]

        payload = await self.get_player(player_id)
        rank: int | None = None
        elo: int | None = None
        if payload:
            games = payload.get("games", {})
            if isinstance(games, dict):
                game_payload = games.get(game, {})
                if isinstance(game_payload, dict):
                    rank = _to_int(game_payload.get("skill_level"))
                    elo = _to_int(game_payload.get("faceit_elo"))
        self._player_rank_cache[player_id] = (now, rank, elo)
        return rank, elo

    def _extract_match_map(self, match_payload: dict[str, Any], stats_payload: dict[str, Any] | None) -> str:
        if stats_payload:
            rounds = stats_payload.get("rounds")
            if isinstance(rounds, list) and rounds:
                round_stats = rounds[-1].get("round_stats", {})
                map_name = round_stats.get("Map")
                if map_name:
                    return str(map_name)

        voting = match_payload.get("voting", {})
        picked = voting.get("map", {}).get("pick", [])
        if isinstance(picked, list) and picked:
            return str(picked[0])

        configured_map = match_payload.get("competition_name")
        if configured_map:
            return str(configured_map)

        return "Unknown map"

    def _extract_stats(
        self, stats_payload: dict[str, Any] | None
    ) -> tuple[dict[str, dict[str, Any]], dict[str, int | None]]:
        player_stats: dict[str, dict[str, Any]] = {}
        team_scores: dict[str, int | None] = {}
        if not stats_payload:
            return player_stats, team_scores

        rounds = stats_payload.get("rounds")
        if not isinstance(rounds, list) or not rounds:
            return player_stats, team_scores

        latest_round = rounds[-1]
        teams = latest_round.get("teams", [])
        if not isinstance(teams, list):
            return player_stats, team_scores

        for team in teams:
            team_id = str(team.get("team_id", ""))
            team_stats = team.get("team_stats", {})
            final_score = (
                team_stats.get("Final Score")
                or team_stats.get("Team Final Score")
                or team_stats.get("Score")
            )
            if team_id:
                team_scores[team_id] = _to_int(final_score)

            players = team.get("players", [])
            if not isinstance(players, list):
                continue

            for player in players:
                player_id = str(player.get("player_id", ""))
                if not player_id:
                    continue
                pstats = player.get("player_stats", {})
                player_stats[player_id] = {
                    "kd": _to_float(pstats.get("K/D Ratio") or pstats.get("K/D")),
                    "adr": _to_float(pstats.get("ADR")),
                    "hs": _to_float(pstats.get("Headshots %") or pstats.get("HS%")),
                    "rws": _extract_rws(pstats),
                    "kr": _to_float(pstats.get("K/R Ratio")),
                    "mvp": _to_int(
                        pstats.get("MVPs")
                        or pstats.get("MVP")
                        or pstats.get("MVP Count")
                    ),
                    "headshots": _to_int(pstats.get("Headshots")),
                    "kills": _to_int(pstats.get("Kills")),
                    "deaths": _to_int(pstats.get("Deaths")),
                    "assists": _to_int(pstats.get("Assists")),
                }

        return player_stats, team_scores

    def _extract_match_scores(self, match_payload: dict[str, Any]) -> dict[str, int | None]:
        result: dict[str, int | None] = {}
        raw_scores = match_payload.get("results", {}).get("score", {})
        if not isinstance(raw_scores, dict):
            return result
        for faction, value in raw_scores.items():
            parsed = _to_int(value)
            result[str(faction)] = parsed
        return result

    async def build_match_snapshot(self, match_id: str, tracked_player_ids: set[str]) -> dict[str, Any] | None:
        match_payload = await self.get_match(match_id)
        if not match_payload:
            return None

        stats_payload = None
        try:
            stats_payload = await self.get_match_stats(match_id)
        except FaceitError as exc:
            logger.warning("Could not fetch /stats for %s: %s", match_id, exc)

        player_stats, team_scores = self._extract_stats(stats_payload)
        score_by_faction = self._extract_match_scores(match_payload)
        map_name = self._extract_match_map(match_payload, stats_payload)

        teams_payload = match_payload.get("teams", {})
        all_player_ids: set[str] = set()
        for faction in ("faction1", "faction2"):
            team = teams_payload.get(faction, {})
            roster = team.get("roster", [])
            if not isinstance(roster, list):
                continue
            for member in roster:
                player_id = str(member.get("player_id", ""))
                if player_id:
                    all_player_ids.add(player_id)

        rank_elo_map: dict[str, tuple[int | None, int | None]] = {}
        if all_player_ids:
            tasks = [self.get_player_rank_elo(player_id=pid, game="cs2") for pid in all_player_ids]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for pid, result in zip(all_player_ids, results):
                if isinstance(result, Exception):
                    rank_elo_map[pid] = (None, None)
                else:
                    rank_elo_map[pid] = result

        teams: list[dict[str, Any]] = []
        faction_order = ["faction1", "faction2"]

        for faction in faction_order:
            team = teams_payload.get(faction, {})
            team_id = str(team.get("faction_id") or faction)
            roster = team.get("roster", [])

            players: list[dict[str, Any]] = []
            if isinstance(roster, list):
                for member in roster:
                    player_id = str(member.get("player_id", ""))
                    if not player_id:
                        continue
                    merged_stats = player_stats.get(player_id, {})
                    rank, elo = rank_elo_map.get(player_id, (None, None))
                    roster_rank = _to_int(member.get("game_skill_level") or member.get("skill_level"))
                    final_rank = roster_rank if roster_rank is not None else rank
                    players.append(
                        {
                            "player_id": player_id,
                            "nickname": member.get("nickname") or "Unknown",
                            "rank": final_rank,
                            "elo": elo,
                            "kd": merged_stats.get("kd"),
                            "adr": merged_stats.get("adr"),
                            "hs": merged_stats.get("hs"),
                            "rws": merged_stats.get("rws"),
                            "kr": merged_stats.get("kr"),
                            "mvp": merged_stats.get("mvp"),
                            "headshots": merged_stats.get("headshots"),
                            "kills": merged_stats.get("kills"),
                            "deaths": merged_stats.get("deaths"),
                            "assists": merged_stats.get("assists"),
                            "is_tracked": player_id in tracked_player_ids,
                        }
                    )

            tracked_players = [player for player in players if bool(player.get("is_tracked"))]
            fallback_score = score_by_faction.get(faction)
            team_score = team_scores.get(team_id, fallback_score)

            teams.append(
                {
                    "faction": faction,
                    "team_id": team_id,
                    "name": team.get("name") or faction,
                    "score": team_score if team_score is not None else "?",
                    "players": players,
                    "tracked_players": tracked_players,
                }
            )

        total_rounds: int | None = None
        if len(teams) >= 2:
            left_score = _to_int(teams[0].get("score"))
            right_score = _to_int(teams[1].get("score"))
            if left_score is not None and right_score is not None:
                total_rounds = left_score + right_score

        if total_rounds:
            for team in teams:
                for player in team.get("players", []):
                    current_rws = _to_float(player.get("rws"))
                    if current_rws is not None:
                        continue
                    kr_ratio = _to_float(player.get("kr"))
                    proxy_rws = _calc_rws_from_kr_ratio(kr_ratio, total_rounds)
                    if proxy_rws is not None:
                        player["rws"] = proxy_rws

        for team in teams:
            team["players"] = _sorted_team_players(team.get("players", []))
            team["tracked_players"] = [player for player in team["players"] if bool(player.get("is_tracked"))]

        finished_at = match_payload.get("finished_at")
        if finished_at is not None:
            finished_at = str(finished_at)

        return {
            "match_id": match_id,
            "status": match_payload.get("status"),
            "finished_at": finished_at,
            "map_name": map_name,
            "teams": teams,
        }
