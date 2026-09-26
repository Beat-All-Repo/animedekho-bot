"""Interactive Anime Schedule Handlers (/schedule) with Today, Weekly & Upcoming views."""

from __future__ import annotations
import html
import logging
import math
from datetime import datetime, timezone

from bot.telegram import Client, enums, filters
from bot.telegram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.schedule import schedule_service, DAY_NAMES

log = logging.getLogger(__name__)

ITEMS_PER_PAGE = 5


def _build_schedule_menu_markup(
    current_mode: str,
    page: int,
    total_pages: int,
    selected_day: int | None = None,
) -> InlineKeyboardMarkup:
    """Build navigation keyboard for schedule views."""
    keyboard: list[list[InlineKeyboardButton]] = []

    # Mode Selector
    mode_buttons = [
        InlineKeyboardButton(f"{'• ' if current_mode == 'today' else ''}📌 Today", callback_data="sch:today:1"),
        InlineKeyboardButton(f"{'• ' if current_mode == 'week' else ''}📆 Weekly", callback_data=f"sch:week:{selected_day or 0}:1"),
        InlineKeyboardButton(f"{'• ' if current_mode == 'upcoming' else ''}🔜 Upcoming", callback_data="sch:upcoming:1"),
    ]
    keyboard.append(mode_buttons)

    # Weekly day selector if in weekly mode
    if current_mode == "week":
        day_row_1 = []
        day_row_2 = []
        for i in range(4):  # Mon-Thu
            d_name = DAY_NAMES[i][:3]
            mark = "● " if selected_day == i else ""
            day_row_1.append(InlineKeyboardButton(f"{mark}{d_name}", callback_data=f"sch:week:{i}:1"))
        for i in range(4, 7):  # Fri-Sun
            d_name = DAY_NAMES[i][:3]
            mark = "● " if selected_day == i else ""
            day_row_2.append(InlineKeyboardButton(f"{mark}{d_name}", callback_data=f"sch:week:{i}:1"))
        keyboard.append(day_row_1)
        keyboard.append(day_row_2)

    # Pagination row
    nav_row = []
    if page > 1:
        cb = f"sch:{current_mode}:{selected_day}:{page-1}" if current_mode == "week" else f"sch:{current_mode}:{page-1}"
        nav_row.append(InlineKeyboardButton("◀ Prev", callback_data=cb))

    nav_row.append(InlineKeyboardButton(f"Page {page}/{max(1, total_pages)}", callback_data="sch:noop"))

    if page < total_pages:
        cb = f"sch:{current_mode}:{selected_day}:{page+1}" if current_mode == "week" else f"sch:{current_mode}:{page+1}"
        nav_row.append(InlineKeyboardButton("Next ▶", callback_data=cb))

    keyboard.append(nav_row)
    return InlineKeyboardMarkup(keyboard)


def _format_schedule_text(
    schedules: list[dict],
    mode: str,
    page: int,
    selected_day: int | None = None,
) -> tuple[str, int]:
    """Format schedule page text."""
    if not schedules:
        header = f"📅 <b>Anime Airing Schedule</b>\n\nℹ️ <i>No scheduled releases found for this timeframe.</i>"
        return header, 1

    total_items = len(schedules)
    total_pages = max(1, math.ceil(total_items / ITEMS_PER_PAGE))
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = schedules[start_idx:end_idx]

    if mode == "today":
        title_mode = "Today's Releases 📌"
    elif mode == "week":
        day_str = DAY_NAMES[selected_day] if selected_day is not None and 0 <= selected_day < 7 else "This Week"
        title_mode = f"Weekly Schedule — {day_str} 📆"
    else:
        title_mode = "Upcoming Anime (Next 48 Hours) 🔜"

    lines = [
        f"📅 <b>Anime Airing Schedule</b> — <i>{title_mode}</i>",
        "──────────────────────────",
    ]

    for idx, s in enumerate(page_items, start=start_idx + 1):
        media = s.get("media", {})
        title_dict = media.get("title", {})
        anime_title = title_dict.get("english") or title_dict.get("romaji") or "Unknown Anime"
        anime_title = html.escape(anime_title)
        ep = s.get("episode", 1)
        airing_ts = s.get("airingAt", 0)
        countdown = schedule_service.format_countdown(airing_ts)
        ist_time = schedule_service.format_ist_time(airing_ts)
        genres = ", ".join(media.get("genres", [])[:2]) or "Anime"
        score = media.get("averageScore")
        score_str = f"⭐ {score}%" if score else ""

        lines.append(
            f"<b>{idx}. {anime_title}</b>\n"
            f"   ├ 🎬 <b>Episode:</b> <code>{ep}</code>\n"
            f"   ├ ⏰ <b>Release:</b> <code>{countdown}</code>\n"
            f"   ├ 🕐 <b>Time:</b> {ist_time}\n"
            f"   └ 🏷 <b>Info:</b> {genres} {score_str}"
        )

    lines.append("──────────────────────────")
    lines.append("<i>Click buttons below to switch days or view upcoming!</i>")
    return "\n".join(lines), total_pages


async def cmd_schedule(client: Client, message: Message):
    """Handle /schedule command — show today's anime release schedule."""
    status_msg = await message.reply_text("🔄 <i>Fetching anime schedule...</i>", parse_mode=enums.ParseMode.HTML)
    schedules = await schedule_service.get_today_schedule()
    text, total_pages = _format_schedule_text(schedules, mode="today", page=1)
    markup = _build_schedule_menu_markup("today", page=1, total_pages=total_pages)

    # If first item has cover photo, we can show it
    first_poster = None
    if schedules:
        first_poster = schedules[0].get("media", {}).get("coverImage", {}).get("large")

    try:
        await status_msg.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    except Exception as e:
        log.warning("Failed displaying schedule: %s", e)


async def schedule_callback(client: Client, query: CallbackQuery):
    """Handle schedule navigation buttons."""
    data = query.data
    if data == "sch:noop":
        await query.answer()
        return

    parts = data.split(":")
    mode = parts[1] if len(parts) > 1 else "today"

    selected_day = None
    page = 1

    if mode == "week":
        selected_day = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        schedules = await schedule_service.get_day_schedule(selected_day)
    elif mode == "upcoming":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        schedules = await schedule_service.get_upcoming_schedule(hours=48)
    else:  # today
        mode = "today"
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        schedules = await schedule_service.get_today_schedule()

    text, total_pages = _format_schedule_text(schedules, mode=mode, page=page, selected_day=selected_day)
    markup = _build_schedule_menu_markup(mode, page=page, total_pages=total_pages, selected_day=selected_day)

    try:
        await query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        await query.answer()
    except Exception as e:
        log.debug("Schedule edit error: %s", e)
        await query.answer()
