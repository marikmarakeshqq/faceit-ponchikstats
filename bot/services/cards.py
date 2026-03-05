from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from bot.utils.formatting import winner_team_name


logger = logging.getLogger(__name__)


def _font_candidates(bold: bool) -> list[Path]:
    env_var = "CARD_FONT_BOLD_PATH" if bold else "CARD_FONT_REGULAR_PATH"
    env_font_path = os.getenv(env_var, "").strip()
    assets_fonts = Path(__file__).resolve().parents[1] / "assets" / "fonts"

    if bold:
        names = [
            "Card-Bold.ttf",
            "Card-Bold.otf",
            "CardBold.ttf",
            "DejaVuSans-Bold.ttf",
            "LiberationSans-Bold.ttf",
            "NotoSans-Bold.ttf",
        ]
        system_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
        ]
    else:
        names = [
            "Card-Regular.ttf",
            "Card-Regular.otf",
            "Card.ttf",
            "DejaVuSans.ttf",
            "LiberationSans-Regular.ttf",
            "NotoSans-Regular.ttf",
        ]
        system_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\arial.ttf",
        ]

    candidates: list[Path] = []
    if env_font_path:
        candidates.append(Path(env_font_path))
    candidates.extend(assets_fonts / name for name in names)
    candidates.extend(Path(path) for path in system_candidates)
    return candidates


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _font_candidates(bold):
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                pass

    logger.warning(
        "No TrueType font found for match card rendering; using Pillow default bitmap font. "
        "Put Card-Regular.ttf/Card-Bold.ttf into bot/assets/fonts or set "
        "CARD_FONT_REGULAR_PATH/CARD_FONT_BOLD_PATH."
    )
    return ImageFont.load_default()


def _score_to_int(value: object) -> int | None:
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


class MatchCardRenderer:
    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)

    async def render(self, snapshot: dict[str, Any], team: dict[str, Any], mode: str = "image") -> tuple[Path, str]:
        _ = mode  # GIF mode is removed; keep arg for backward compatibility.
        image_path = await self.render_match_card(snapshot, team)
        return image_path, "photo"

    async def render_match_card(self, snapshot: dict[str, Any], team: dict[str, Any]) -> Path:
        return await asyncio.to_thread(self._render_match_card_sync, snapshot, team)

    def _create_background(self, width: int, height: int) -> Image.Image:
        image = Image.new("RGB", (width, height), color=(14, 19, 27))
        draw = ImageDraw.Draw(image)
        for y in range(height):
            ratio = y / max(1, height - 1)
            r = int(14 + 16 * ratio)
            g = int(19 + 20 * ratio)
            b = int(27 + 26 * ratio)
            draw.line([(0, y), (width, y)], fill=(r, g, b))
        return image

    def _winner_loser(self, teams: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        if len(teams) >= 2:
            first = teams[0]
            second = teams[1]
            first_score = _score_to_int(first.get("score"))
            second_score = _score_to_int(second.get("score"))

            if first_score is None and second_score is None:
                return first, second
            if second_score is not None and (first_score is None or second_score > first_score):
                return second, first
            return first, second

        if len(teams) == 1:
            return teams[0], {"name": "Unknown", "score": "?", "players": []}
        return (
            {"name": "Winner", "score": "?", "players": []},
            {"name": "Loser", "score": "?", "players": []},
        )

    def _tracked_result(self, snapshot: dict[str, Any], tracked_team_id: str) -> tuple[str, tuple[int, int, int]]:
        teams = snapshot.get("teams", [])
        if len(teams) < 2:
            return "Результат неизвестен", (198, 210, 222)

        tracked_team = None
        if tracked_team_id:
            tracked_team = next((t for t in teams if str(t.get("team_id", "")) == tracked_team_id), None)
        if tracked_team is None:
            tracked_team = next((t for t in teams if t.get("tracked_players")), teams[0])

        opponent = next((t for t in teams if t is not tracked_team), None)
        tracked_score = _score_to_int(tracked_team.get("score")) if tracked_team else None
        opponent_score = _score_to_int(opponent.get("score")) if opponent else None

        if tracked_score is None or opponent_score is None:
            return "Результат неизвестен", (198, 210, 222)
        if tracked_score > opponent_score:
            return "Win", (120, 188, 154)
        if tracked_score < opponent_score:
            return "Loss", (204, 116, 130)
        return "Ничья", (198, 210, 222)

    def _fmt_float(self, value: Any, digits: int) -> str:
        try:
            if value is None:
                return "0"
            return f"{float(value):.{digits}f}"
        except (TypeError, ValueError):
            return "0"

    def _fmt_int(self, value: Any) -> str:
        try:
            if value is None:
                return "0"
            return str(int(float(value)))
        except (TypeError, ValueError):
            return "0"

    def _fmt_rank_elo(self, player: dict[str, Any]) -> str:
        rank_raw = player.get("rank")
        elo_raw = player.get("elo")
        rank = self._fmt_int(rank_raw) if rank_raw not in (None, 0, "0") else "-"
        elo = self._fmt_int(elo_raw) if elo_raw not in (None, 0, "0") else "-"

        if rank != "-" and elo != "-":
            return f"{rank} ({elo})"
        if elo != "-":
            return elo
        return rank

    def _draw_team_panel(
        self,
        draw: ImageDraw.ImageDraw,
        panel_rect: tuple[int, int, int, int],
        team_payload: dict[str, Any],
        is_winner: bool,
        title_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        header_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        row_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        row_small_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ) -> None:
        x1, y1, x2, y2 = panel_rect
        draw.rounded_rectangle(
            panel_rect,
            radius=16,
            fill=(16, 22, 32),
            outline=(51, 64, 80),
            width=2,
        )

        team_name = str(team_payload.get("name", "Unknown team"))
        team_score = team_payload.get("score", "?")
        status_text = "Winner" if is_winner else "Loser"
        status_color = (134, 204, 168) if is_winner else (215, 136, 146)

        draw.text((x1 + 18, y1 + 12), team_name, font=title_font, fill=(238, 244, 250))
        draw.text((x1 + 18, y1 + 42), f"{status_text} | score: {team_score}", font=header_font, fill=status_color)

        header_y = y1 + 76
        draw.rounded_rectangle(
            (x1 + 12, header_y, x2 - 12, header_y + 34),
            radius=8,
            fill=(26, 34, 47),
            outline=(62, 78, 98),
            width=1,
        )

        col_name = x1 + 20
        col_rank_elo = x2 - 560
        col_k = x2 - 388
        col_d = x2 - 340
        col_a = x2 - 292
        col_adr = x2 - 236
        col_hs = x2 - 146
        col_hs_pct = x2 - 82

        label_color = (221, 233, 245)
        draw.text((col_name, header_y + 8), "Player", font=header_font, fill=label_color)
        draw.text((col_rank_elo, header_y + 8), "Rank(elo)", font=header_font, fill=label_color)
        draw.text((col_k, header_y + 8), "K", font=header_font, fill=label_color)
        draw.text((col_d, header_y + 8), "D", font=header_font, fill=label_color)
        draw.text((col_a, header_y + 8), "A", font=header_font, fill=label_color)
        draw.text((col_adr, header_y + 8), "ADR", font=header_font, fill=label_color)
        draw.text((col_hs, header_y + 8), "HS", font=header_font, fill=label_color)
        draw.text((col_hs_pct, header_y + 8), "HS%", font=header_font, fill=label_color)

        players = team_payload.get("players", [])
        row_top = header_y + 44
        row_h = 52
        row_gap = 6

        for i in range(5):
            player = players[i] if i < len(players) else {}
            is_tracked = bool(player.get("is_tracked"))

            if is_tracked:
                if is_winner:
                    fill_color = (28, 46, 40)
                    border_color = (90, 154, 122)
                    text_color = (245, 252, 248)
                    metric_color = (181, 229, 205)
                else:
                    fill_color = (56, 28, 37)
                    border_color = (156, 80, 97)
                    text_color = (248, 250, 253)
                    metric_color = (237, 158, 170)
            else:
                fill_color = (24, 32, 45)
                border_color = (61, 77, 97)
                text_color = (240, 246, 252)
                metric_color = (224, 236, 247)

            y = row_top + i * (row_h + row_gap)
            draw.rounded_rectangle(
                (x1 + 12, y, x2 - 12, y + row_h),
                radius=8,
                fill=fill_color,
                outline=border_color,
                width=1,
            )

            nickname = str(player.get("nickname", "-"))[:18]
            draw.text((col_name, y + 14), nickname, font=row_font, fill=text_color)
            draw.text((col_rank_elo, y + 14), self._fmt_rank_elo(player), font=row_small_font, fill=metric_color)
            draw.text((col_k, y + 14), self._fmt_int(player.get("kills")), font=row_small_font, fill=metric_color)
            draw.text((col_d, y + 14), self._fmt_int(player.get("deaths")), font=row_small_font, fill=metric_color)
            draw.text((col_a, y + 14), self._fmt_int(player.get("assists")), font=row_small_font, fill=metric_color)
            draw.text((col_adr, y + 14), self._fmt_float(player.get("adr"), 1), font=row_small_font, fill=metric_color)
            draw.text((col_hs, y + 14), self._fmt_int(player.get("headshots")), font=row_small_font, fill=metric_color)
            draw.text((col_hs_pct, y + 14), f"{self._fmt_float(player.get('hs'), 0)}%", font=row_small_font, fill=metric_color)

    def _render_match_card_sync(self, snapshot: dict[str, Any], team: dict[str, Any]) -> Path:
        width, height = 1920, 720
        image = self._create_background(width, height)
        draw = ImageDraw.Draw(image)

        title_font = _load_font(42, bold=True)
        subtitle_font = _load_font(28, bold=True)
        info_font = _load_font(24)
        panel_title_font = _load_font(26, bold=True)
        panel_header_font = _load_font(19, bold=True)
        panel_row_font = _load_font(20, bold=True)
        panel_row_small_font = _load_font(19)
        footer_font = _load_font(18)

        teams = snapshot.get("teams", [])
        winner_team, loser_team = self._winner_loser(teams)
        winner_score = winner_team.get("score", "?")
        loser_score = loser_team.get("score", "?")
        map_name = str(snapshot.get("map_name", "Unknown map"))
        winner_name = winner_team_name(snapshot) or str(winner_team.get("name", "Unknown"))

        tracked_team_id = str(team.get("team_id", ""))
        result_text, result_color = self._tracked_result(snapshot, tracked_team_id)

        header_rect = (28, 22, width - 28, 186)
        draw.rounded_rectangle(
            header_rect,
            radius=16,
            fill=(18, 24, 35),
            outline=(55, 68, 85),
            width=2,
        )

        draw.text((54, 42), f"{result_text}", font=title_font, fill=result_color)
        draw.text((56, 98), f"Map: {map_name}", font=subtitle_font, fill=(211, 224, 238))
        draw.text((350, 98), f"Score: {winner_score} : {loser_score}", font=subtitle_font, fill=(236, 219, 176))
        winner_text = f"{winner_name} wins"
        winner_text_bbox = draw.textbbox((0, 0), winner_text, font=subtitle_font)
        winner_text_width = winner_text_bbox[2] - winner_text_bbox[0]
        winner_text_x = (width - winner_text_width) // 2
        draw.text((winner_text_x, 98), winner_text, font=subtitle_font, fill=(161, 214, 183))
        draw.text((56, 138), "", font=info_font, fill=(179, 196, 214))

        left_panel = (28, 206, 944, 620)
        right_panel = (976, 206, 1892, 620)

        self._draw_team_panel(
            draw=draw,
            panel_rect=left_panel,
            team_payload=winner_team,
            is_winner=True,
            title_font=panel_title_font,
            header_font=panel_header_font,
            row_font=panel_row_font,
            row_small_font=panel_row_small_font,
        )
        self._draw_team_panel(
            draw=draw,
            panel_rect=right_panel,
            team_payload=loser_team,
            is_winner=False,
            title_font=panel_title_font,
            header_font=panel_header_font,
            row_font=panel_row_font,
            row_small_font=panel_row_small_font,
        )

        draw.text((52, 656), f"ID : {snapshot.get('match_id', '-')}", font=footer_font, fill=(173, 190, 208))

        output_path = self._output_dir / (
            f"{snapshot.get('match_id', 'match')}_{team.get('team_id', 'team')}_{uuid.uuid4().hex[:8]}.png"
        )
        image.save(output_path, format="PNG")
        return output_path
