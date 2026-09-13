"""ToonFlix extractor — fallback source for 4K / high quality anime & missing episodes."""

from __future__ import annotations
import asyncio
import base64
import html as html_mod
import logging
import re
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
import cloudscraper

log = logging.getLogger(__name__)


def _get_scraper() -> cloudscraper.CloudScraper:
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )


class ToonflixExtractor:
    """Extracts high-speed video streams and 4K media from ToonFlix."""

    def __init__(self):
        self._base_url = "https://toonflix.in"

    async def search(self, query: str) -> list[dict]:
        """Search ToonFlix catalog for anime or movies."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_search, query)

    def _sync_search(self, query: str) -> list[dict]:
        s = _get_scraper()
        url = f"{self._base_url}/?s={quote_plus(query)}"
        try:
            r = s.get(url, timeout=15)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            for a in soup.find_all("article"):
                link = a.find("a", href=True)
                title_el = a.find(["h2", "h3", "h4", "h1"])
                img = a.find("img")
                if link and title_el:
                    results.append({
                        "title": title_el.get_text(strip=True),
                        "url": link["href"],
                        "poster": img.get("src") or img.get("data-src", "") if img else "",
                    })
            return results
        except Exception as e:
            log.warning("ToonFlix search failed for '%s': %s", query, e)
            return []

    async def resolve_episode(
        self,
        anime_title: str,
        season: int = 1,
        episode: int = 1,
        quality_pref: str = "1080p",
    ) -> dict | None:
        """Resolve an episode stream from ToonFlix matching quality_pref (including 4K)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._sync_resolve, anime_title, season, episode, quality_pref
        )

    def _sync_resolve(
        self,
        anime_title: str,
        season: int,
        episode: int,
        quality_pref: str,
    ) -> dict | None:
        s = _get_scraper()

        # Step 1: Clean query and search ToonFlix
        # Strip common trailing tags like "Season 1", "Hindi", "Dubbed"
        clean_title = re.sub(r"(?i)\s*(season\s*\d+|hindi|dubbed|multi-audio|tamil|telugu).*$", "", anime_title).strip()
        search_results = self._sync_search(clean_title or anime_title)
        if not search_results:
            search_results = self._sync_search(anime_title)
        if not search_results:
            log.info("ToonFlix: No search results found for '%s'", anime_title)
            return None

        # Step 2: Pick the best matching season page
        target_page_url = None
        for res in search_results:
            t = res["title"].lower()
            if f"season {season:02d}" in t or f"season {season}" in t or (season == 1 and "season" not in t):
                target_page_url = res["url"]
                break

        if not target_page_url:
            target_page_url = search_results[0]["url"]

        log.info("ToonFlix: Inspecting season page: %s", target_page_url)
        try:
            r_page = s.get(target_page_url, timeout=15)
            if r_page.status_code != 200:
                return None
            soup_page = BeautifulSoup(r_page.text, "html.parser")
        except Exception as e:
            log.warning("ToonFlix: Failed to fetch season page: %s", e)
            return None

        # Step 3: Find matching episode drive link
        drive_url = None
        for a in soup_page.find_all("a", href=True):
            href = a["href"]
            if "drive.toonflix.in" in href and "data=" in href:
                raw = href.split("data=")[1].split("&")[0]
                try:
                    dec = base64.b64decode(raw + "===").decode("utf-8", errors="ignore")
                    ep_match = re.search(r"ep=(\d+)", dec)
                    if ep_match and int(ep_match.group(1)) == episode:
                        drive_url = href
                        break
                    # For movies
                    if "type=movie" in dec and episode == 1:
                        drive_url = href
                        break
                except Exception:
                    pass

        if not drive_url:
            # Check if there are other episode links on page
            all_drive = [a["href"] for a in soup_page.find_all("a", href=True) if "drive.toonflix.in" in a["href"]]
            if len(all_drive) >= episode:
                drive_url = all_drive[episode - 1]

        if not drive_url:
            log.info("ToonFlix: Episode %d not found on %s", episode, target_page_url)
            return None

        # Step 4: Visit drive.toonflix.in/index.php
        try:
            s.cookies.set("toon_short_count", "3")
            s.cookies.set("tf_sys_pref", "12h")
            r_drive = s.get(drive_url, timeout=15)
            if r_drive.status_code != 200:
                return None
            soup_drive = BeautifulSoup(r_drive.text, "html.parser")
        except Exception as e:
            log.warning("ToonFlix: Failed to fetch drive page: %s", e)
            return None

        # Step 5: Find matching quality card (e.g. 4K/2160p, 1080p, 720p, 480p)
        chosen_rel_go = None
        matched_quality = quality_pref
        is_4k_request = quality_pref.lower() in ("4k", "2160p", "2160")

        cards = soup_drive.find_all(class_=re.compile(r"quality-card|card"))
        for card in cards:
            card_text = card.get_text().lower()
            q_clean = quality_pref.lower().replace("p", "")

            matches = False
            if is_4k_request and ("4k" in card_text or "2160" in card_text):
                matches = True
                matched_quality = "4K"
            elif q_clean in card_text:
                matches = True

            if matches:
                m = re.search(r"handleLinkClick\('([^']+)',\s*'download'\)", str(card))
                if m:
                    chosen_rel_go = m.group(1)
                    break

        if not chosen_rel_go:
            # Fallback to highest available quality button on card list
            m = re.search(r"handleLinkClick\('([^']+)',\s*'download'\)", r_drive.text)
            if m:
                chosen_rel_go = m.group(1)

        if chosen_rel_go:
            chosen_rel_go = html_mod.unescape(chosen_rel_go)
        else:
            log.warning("ToonFlix: No download button found on drive page %s", drive_url)
            return None

        full_go_url = f"https://drive.toonflix.in/{chosen_rel_go}&sys=12h"

        # Step 6: Fetch go.php to get action=start token
        try:
            r_go = s.get(full_go_url, headers={"Referer": drive_url}, timeout=15)
            tok_match = re.search(r"action=start&token=([a-zA-Z0-9]+)", r_go.text)
            if not tok_match:
                log.warning("ToonFlix: No token in go.php response")
                return None

            token = tok_match.group(1)
            start_url = f"https://drive.toonflix.in/go.php?action=start&token={token}"
            r_start = s.get(start_url, headers={"Referer": full_go_url}, allow_redirects=False, timeout=15)

            final_stream_url = r_start.headers.get("Location")
            if not final_stream_url or "drive.toonflix.in" in final_stream_url:
                return None

            log.info("ToonFlix: Successfully resolved direct stream [%s]: %s", matched_quality, final_stream_url[:80])
            return {
                "url": final_stream_url,
                "quality": matched_quality,
                "server": "ToonFlix",
                "referer": "https://drive.toonflix.in/",
            }
        except Exception as e:
            log.warning("ToonFlix: Stream resolution failed: %s", e)
            return None


toonflix = ToonflixExtractor()
