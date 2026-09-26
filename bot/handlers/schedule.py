"""Interactive Anime Schedule Handlers (/schedule) with Today, Weekly & Upcoming views."""

from __future__ import annotations
import html
import logging
import math
from datetime import datetime, timezone, timedelta

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
    """Build classic navigation keyboard for schedule views."""
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


def _build_modern_schedule_markup(
    current_mode: str,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """Build modern schedule navigation matching Issue #4 screenshots."""
    keyboard: list[list[InlineKeyboardButton]] = []

    # Main action row: Today / Upcoming toggle + Close
    if current_mode == "upcoming":
        row1 = [
            InlineKeyboardButton("📅 Today's Schedule", callback_data="sch:today:1"),
            InlineKeyboardButton("❌ Close", callback_data="sch:close"),
        ]
    else:
        row1 = [
            InlineKeyboardButton("📆 Upcoming Schedule", callback_data="sch:upcoming:1"),
            InlineKeyboardButton("❌ Close", callback_data="sch:close"),
        ]
    keyboard.append(row1)

    # Pagination if needed
    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("◀ Prev", callback_data=f"sch:{current_mode}:{page-1}"))
        nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="sch:noop"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("Next ▶", callback_data=f"sch:{current_mode}:{page+1}"))
        keyboard.append(nav_row)

    return InlineKeyboardMarkup(keyboard)


def _format_schedule_text(
    schedules: list[dict],
    mode: str,
    page: int,
    selected_day: int | None = None,
) -> tuple[str, int]:
    """Format classic schedule page text."""
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


def _format_modern_schedule_text(
    schedules: list[dict],
    mode: str,
    page: int,
) -> tuple[str, int]:
    """Format modern schedule box layout matching Issue #4 screenshots."""
    is_upcoming = (mode == "upcoming")
    header_title = "✦ <b>UPCOMING ANIME SCHEDULE</b> ✦" if is_upcoming else "✦ <b>TODAY'S ANIME SCHEDULE</b> ✦"
    end_tag = "END OF UPCOMING LIST" if is_upcoming else "END OF TODAY'S LIST"

    if not schedules:
        text = (
            f"{header_title}\n"
            f"<blockquote>╔══════════════════════════════════\n"
            f"╠ ℹ️ <i>No scheduled anime releases found.</i>\n"
            f"╚══════════════════════════════════</blockquote>\n"
            f"<blockquote>《 ✧ {end_tag} ✧ 》</blockquote>"
        )
        return text, 1

    total_items = len(schedules)
    total_pages = max(1, math.ceil(total_items / ITEMS_PER_PAGE))
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = schedules[start_idx:end_idx]

    blocks = []
    for s in page_items:
        media = s.get("media", {})
        title_dict = media.get("title", {})
        anime_title = title_dict.get("english") or title_dict.get("romaji") or "Unknown Anime"
        anime_title = html.escape(anime_title)
        ep = s.get("episode", 1)
        airing_ts = s.get("airingAt", 0)

        if is_upcoming:
            if airing_ts:
                dt = datetime.fromtimestamp(airing_ts, tz=timezone.utc)
                ist_dt = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
                day_name = ist_dt.strftime("%A")
                time_str = ist_dt.strftime("%I:%M %p IST")
                date_str = ist_dt.strftime("%d %b %Y")
                detail_lines = (
                    f"╠ ✎ S01-Coming Soon (Dub)\n"
                    f"╠ 📅 Day: {day_name}\n"
                    f"╠ ⏰ Time: {time_str}\n"
                    f"╠ 🚀 Date: {date_str}\n"
                    f"╠ 🇮🇳 Audio: Hindi Dub"
                )
            else:
                detail_lines = (
                    f"╠ ✎ S01-Coming Soon (Dub)\n"
                    f"╠ ⚠️ Date: Not Announced\n"
                    f"╠ 🇮🇳 Audio: Hindi Dub"
                )
        else:
            ist_time = schedule_service.format_ist_time(airing_ts)
            detail_lines = (
                f"╠ ✎ S01-EP{ep:02d} (Dub)\n"
                f"╠ 🇮🇳 Hindi: {ist_time}"
            )

        item_str = f"╠ ❖ <b>{anime_title}</b>\n{detail_lines}"
        blocks.append(item_str)

    box_content = "\n║\n".join(blocks)
    text = (
        f"{header_title}\n"
        f"<blockquote>╔══════════════════════════════════\n"
        f"{box_content}\n"
        f"╚══════════════════════════════════</blockquote>\n"
        f"<blockquote>《 ✧ {end_tag} ✧ 》</blockquote>"
    )
    return text, total_pages


async def cmd_schedule(client: Client, message: Message):
    """Handle /schedule command — show today's anime release schedule."""
    from bot.database import db
    sched_style = await db.get_sched_style() if db else "classic"

    status_msg = await message.reply_text("🔄 <i>Fetching anime schedule...</i>", parse_mode=enums.ParseMode.HTML)
    schedules = await schedule_service.get_today_schedule()

    if sched_style == "modern":
        text, total_pages = _format_modern_schedule_text(schedules, mode="today", page=1)
        markup = _build_modern_schedule_markup("today", page=1, total_pages=total_pages)
    else:
        text, total_pages = _format_schedule_text(schedules, mode="today", page=1)
        markup = _build_schedule_menu_markup("today", page=1, total_pages=total_pages)

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

    if data == "sch:close":
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.answer("Schedule closed.")
        return

    from bot.database import db
    sched_style = await db.get_sched_style() if db else "classic"

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

    if sched_style == "modern":
        text, total_pages = _format_modern_schedule_text(schedules, mode=mode, page=page)
        markup = _build_modern_schedule_markup(mode, page=page, total_pages=total_pages)
    else:
        text, total_pages = _format_schedule_text(schedules, mode=mode, page=page, selected_day=selected_day)
        markup = _build_schedule_menu_markup(mode, page=page, total_pages=total_pages, selected_day=selected_day)

    try:
        await query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        await query.answer()
    except Exception as e:
        log.debug("Schedule edit error: %s", e)
        await query.answer()

