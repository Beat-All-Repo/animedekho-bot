"""Anime Airing Schedule System (Today, Weekly, Upcoming, Release Times & Artwork)."""

from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from bot.telegram import enums
from bot.telegram.types import InlineKeyboardMarkup, InlineKeyboardButton

log = logging.getLogger(__name__)

ANILIST_GRAPHQL_URL = "https://graphql.anilist.co"

AIRING_SCHEDULE_QUERY = """
query ($airingAt_greater: Int, $airingAt_lesser: Int, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      total
      currentPage
      lastPage
      hasNextPage
    }
    airingSchedules(
      airingAt_greater: $airingAt_greater,
      airingAt_lesser: $airingAt_lesser,
      sort: TIME
    ) {
      id
      airingAt
      timeUntilAiring
      episode
      media {
        id
        idMal
        title {
          romaji
          english
          native
        }
        coverImage {
          extraLarge
          large
        }
        bannerImage
        genres
        status
        episodes
        duration
        averageScore
        countryOfOrigin
      }
    }
  }
}
"""

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class ScheduleService:
    """Fetches, formats, and manages anime release schedules."""

    def __init__(self):
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._cache_ttl = 300  # 5 minutes cache

    async def fetch_schedule(
        self,
        start_ts: int,
        end_ts: int,
        page: int = 1,
        per_page: int = 25,
    ) -> list[dict]:
        """Fetch airing schedules within a timestamp window from AniList."""
        cache_key = f"{start_ts}_{end_ts}_{page}_{per_page}"
        now = time.time()
        if cache_key in self._cache:
            ts, cached_data = self._cache[cache_key]
            if now - ts < self._cache_ttl:
                return cached_data

        payload = {
            "query": AIRING_SCHEDULE_QUERY,
            "variables": {
                "airingAt_greater": start_ts,
                "airingAt_lesser": end_ts,
                "page": page,
                "perPage": per_page,
            },
        }

        try:
            from utils.http import http_client
            # If http_client is available, use it, else aiohttp
            if hasattr(http_client, "session") and http_client.session:
                async with http_client.session.post(
                    ANILIST_GRAPHQL_URL,
                    json=payload,
                    headers={"Content-Type": "application/json", "User-Agent": "AnimeDekho/1.0"},
                    timeout=10,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        schedules = data.get("data", {}).get("Page", {}).get("airingSchedules", [])
                        self._cache[cache_key] = (now, schedules)
                        return schedules
            else:
                import aiohttp
                async with aiohttp.ClientSession() as s:
                    async with s.post(
                        ANILIST_GRAPHQL_URL,
                        json=payload,
                        headers={"Content-Type": "application/json", "User-Agent": "AnimeDekho/1.0"},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            schedules = data.get("data", {}).get("Page", {}).get("airingSchedules", [])
                            self._cache[cache_key] = (now, schedules)
                            return schedules
        except Exception as e:
            log.warning("Failed to fetch schedule from AniList: %s", e)

        return []

    async def get_today_schedule(self) -> list[dict]:
        """Get anime schedule for today (UTC/IST midnight to midnight)."""
        now = int(time.time())
        # Align with start of day in UTC
        start_of_day = now - (now % 86400)
        end_of_day = start_of_day + 86400
        return await self.fetch_schedule(start_of_day, end_of_day, per_page=30)

    async def get_day_schedule(self, day_index: int) -> list[dict]:
        """
        Get anime schedule for a specific day of the current week.
        day_index: 0=Monday, 6=Sunday.
        """
        now_dt = datetime.now(timezone.utc)
        current_weekday = now_dt.weekday()  # 0=Monday
        days_diff = day_index - current_weekday

        target_dt = (now_dt + timedelta(days=days_diff)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start_ts = int(target_dt.timestamp())
        end_ts = start_ts + 86400
        return await self.fetch_schedule(start_ts, end_ts, per_page=30)

    async def get_upcoming_schedule(self, hours: int = 48) -> list[dict]:
        """Get anime schedule airing in the next N hours."""
        now = int(time.time())
        end_ts = now + (hours * 3600)
        return await self.fetch_schedule(now, end_ts, per_page=30)

    @staticmethod
    def format_countdown(target_ts: int) -> str:
        """Format countdown to release time nicely."""
        now = int(time.time())
        diff = target_ts - now
        if diff <= 0:
            return "Aired / Available"
        hours = diff // 3600
        mins = (diff % 3600) // 60
        if hours > 24:
            days = hours // 24
            rem_h = hours % 24
            return f"in {days}d {rem_h}h"
        elif hours > 0:
            return f"in {hours}h {mins}m"
        else:
            return f"in {mins}m"

    @staticmethod
    def format_ist_time(target_ts: int) -> str:
        """Format timestamp as Indian Standard Time (IST, UTC+5:30)."""
        ist = timezone(timedelta(hours=5, minutes=30))
        dt = datetime.fromtimestamp(target_ts, tz=ist)
        return dt.strftime("%I:%M %p IST (%d %b)")


schedule_service = ScheduleService()
