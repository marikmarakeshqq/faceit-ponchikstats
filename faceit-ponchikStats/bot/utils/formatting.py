from __future__ import annotations

from html import escape


def _to_float(value: object, fallback: float | None = 0.0) -> float | None:
    try:
        if value is None:
            return fallback
        if isinstance(value, str):
            return float(value.replace("%", "").strip())
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _to_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return None
            return int(float(cleaned))
        return int(value)
    except (TypeError, ValueError):
        return None


def score_line(snapshot: dict) -> str:
    teams = snapshot.get("teams", [])
    if len(teams) < 2:
        return "N/A"
    left = teams[0]
    right = teams[1]
    return (
        f"{escape(str(left.get('name', 'Команда A')))} {left.get('score', '?')} : "
        f"{right.get('score', '?')} {escape(str(right.get('name', 'Команда B')))}"
    )


def winner_team_name(snapshot: dict) -> str | None:
    teams = snapshot.get("teams", [])
    if len(teams) < 2:
        return None

    left = teams[0]
    right = teams[1]
    left_score = _to_int(left.get("score"))
    right_score = _to_int(right.get("score"))
    if left_score is None or right_score is None or left_score == right_score:
        return None
    return str(left.get("name")) if left_score > right_score else str(right.get("name"))


def _fmt_int(value: object, fallback: str = "0") -> str:
    parsed = _to_int(value)
    if parsed is None:
        return fallback
    return str(parsed)


def _fmt_float(value: object, digits: int, fallback: str = "0") -> str:
    parsed = _to_int(value) if digits == 0 else None
    if digits == 0:
        if parsed is None:
            return fallback
        return str(parsed)
    try:
        parsed_float = _to_float(value)
        if parsed_float is None:
            return fallback
        return f"{parsed_float:.{digits}f}"
    except (TypeError, ValueError):
        return fallback


def _kd_trend(kd_value: object) -> str:
    if kd_value is None:
        return ""

    try:
        kd = float(str(kd_value).replace("%", "").strip())
    except (TypeError, ValueError):
        return ""

    # Downtrend: <= 0.99 -> 🔻, <= 0.70 -> 🔻🔻
    if kd <= 0.70:
        return " 🔻🔻"
    if kd <= 0.99:
        return " 🔻"

    # Uptrend: > 1.00 -> 🔺, > 1.15 -> 🔺🔺
    if kd > 1.15:
        return " 🔺🔺"
    if kd > 1.00:
        return " 🔺"

    return ""


def _player_rating_line(player: dict) -> str:
    elo = _to_int(player.get("elo"))
    rank = _to_int(player.get("rank"))

    if elo is not None and rank is not None:
        return f"ELO {elo} | LVL {rank}"
    if elo is not None:
        return f"ELO {elo}"
    if rank is not None:
        return f"LVL {rank}"
    return "ELO N/A"


def _tracked_team_result(snapshot: dict, team: dict) -> str:
    teams = snapshot.get("teams", [])
    if len(teams) < 2:
        return "Результат неизвестен"

    tracked_team_id = str(team.get("team_id", "")).strip()
    tracked_team = None
    if tracked_team_id:
        tracked_team = next((t for t in teams if str(t.get("team_id", "")) == tracked_team_id), None)
    if tracked_team is None:
        tracked_team = team

    opponent = next((t for t in teams if t is not tracked_team), None)
    tracked_score = _to_int(tracked_team.get("score")) if tracked_team else None
    opponent_score = _to_int(opponent.get("score")) if opponent else None

    if tracked_score is None or opponent_score is None:
        return "Результат неизвестен"
    if tracked_score > opponent_score:
        return "Победа"
    if tracked_score < opponent_score:
        return "Поражение"
    return "Ничья"


def format_match_caption(snapshot: dict, team: dict, display_stats: list[str]) -> str:
    map_name = escape(str(snapshot.get("map_name", "Неизвестная карта")))
    match_id = escape(str(snapshot.get("match_id", "")))
    tracked_result = _tracked_team_result(snapshot, team)
    requested = set(display_stats)

    if tracked_result == "Победа":
        result_line = "Win ✅"
    elif tracked_result == "Поражение":
        result_line = "Loss ❌"
    elif tracked_result == "Ничья":
        result_line = "Draw 🤝"
    else:
        result_line = "Unknown"

    lines: list[str] = [
        f"<b>{score_line(snapshot)}</b>",
        f"Map: <b>{map_name}</b>",
        f"Result: <b>{result_line}</b>",
        "",
    ]

    tracked_players = team.get("tracked_players", [])
    if tracked_players:
        for player in tracked_players:
            nickname = escape(str(player.get("nickname", "Неизвестно")))
            rating_line = escape(_player_rating_line(player))
            lines.append(f"🎲 <b>{nickname}</b> - {rating_line}")

            kd_part = ""
            if "kd" in requested:
                kd_value = _to_float(player.get("kd"), fallback=None)
                kd_formatted = "0.00" if kd_value is None else f"{kd_value:.2f}"
                kd_part = f"🗡️ K/D {kd_formatted}{_kd_trend(kd_value)}"

            kda_part = ""
            if {"kills", "deaths", "assists"} & requested:
                kills = _fmt_int(player.get("kills"))
                deaths = _fmt_int(player.get("deaths"))
                assists = _fmt_int(player.get("assists"))
                kda_part = f"⚔️ KDA {kills}/{deaths}/{assists}"

            if kd_part and kda_part:
                lines.append(f"{kd_part} | {kda_part}")
            elif kd_part:
                lines.append(kd_part)
            elif kda_part:
                lines.append(kda_part)

            if "adr" in requested:
                lines.append(f"🩸 ADR {_fmt_float(player.get('adr'), 1, fallback='0.0')}")

            if "hs" in requested:
                hs_percent = _fmt_float(player.get("hs"), 0, fallback="0")
                headshots = _fmt_int(player.get("headshots"))
                lines.append(f"💀 HS% {hs_percent}% / {headshots}")

            lines.append("")
    else:
        lines.append("В этой команде нет отслеживаемых игроков.")

    if match_id:
        lines.append(f"ID матча: <code>{match_id}</code>")

    caption = "\n".join(lines).strip()
    if len(caption) > 1000:
        caption = caption[:996] + "..."
    return caption
