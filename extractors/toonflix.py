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
        target_poster = None
        for res in search_results:
            t = res["title"].lower()
            if f"season {season:02d}" in t or f"season {season}" in t or (season == 1 and "season" not in t):
                target_page_url = res["url"]
                target_poster = res.get("poster")
                break

        if not target_page_url:
            target_page_url = search_results[0]["url"]
            target_poster = search_results[0].get("poster")

        log.info("ToonFlix: Inspecting season page: %s", target_page_url)
        try:
            r_page = s.get(target_page_url, timeout=15)
            if r_page.status_code != 200:
                return None
            soup_page = BeautifulSoup(r_page.text, "html.parser")
            if not target_poster:
                tf_p = soup_page.find("div", class_="tf-poster")
                if tf_p:
                    p_img = tf_p.find("img")
                    if p_img:
                        target_poster = p_img.get("src") or p_img.get("data-src")
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

        # Step 5: Score and rank quality cards (4K/2160p, 1080p HQ, 1080p, 720p, 480p)
        is_4k_request = quality_pref.lower() in ("4k", "2160p", "2160")

        def _score_toonflix_card(card_str: str) -> tuple[int, str]:
            t = card_str.lower()
            if is_4k_request:
                if any(k in t for k in ("4k", "2160", "uhd", "s2160")):
                    return (1000, "4K")
                if "1080" in t and "hq" in t and any(k in t for k in ("x265", "hevc", "10bit", "10-bit", "60fps")):
                    return (950, "1080p HQ x265")
                if "1080" in t and ("hq" in t or "s1080hq" in t):
                    return (900, "1080p HQ")
                if "1080" in t and any(k in t for k in ("10bit", "10-bit", "x265", "hevc", "fhd")):
                    return (860, "1080p HQ")
                if "1080" in t or "s1080" in t:
                    return (800, "1080p")
                if "720" in t and any(k in t for k in ("hq", "10bit", "10-bit")):
                    return (600, "720p HQ")
                if "720" in t or "s720" in t:
                    return (500, "720p")
                if "480" in t or "s480" in t:
                    return (300, "480p")
                return (100, "auto")
            else:
                qc = quality_pref.lower().replace("p", "")
                if qc in t:
                    return (900, quality_pref)
                if "1080" in t or "s1080" in t:
                    return (800 if "1080" in qc else 600, "1080p")
                if "720" in t or "s720" in t:
                    return (800 if "720" in qc else 500, "720p")
                if "480" in t or "s480" in t:
                    return (800 if "480" in qc else 400, "480p")
                return (100, "auto")

        card_candidates: list[tuple[int, str, str]] = []
        cards = soup_drive.find_all(class_=re.compile(r"quality-card|card"))
        for card in cards:
            m = re.search(r"handleLinkClick\('([^']+)',\s*'download'\)", str(card))
            if m:
                card_str = f"{card.get_text()} {card.get('class', '')} {str(card)}"
                sc, q_name = _score_toonflix_card(card_str)
                card_candidates.append((sc, m.group(1), q_name))

        card_candidates.sort(key=lambda x: -x[0])

        chosen_rel_go = None
        matched_quality = quality_pref
        if card_candidates:
            _, chosen_rel_go, matched_quality = card_candidates[0]
        else:
            # Fallback to any handleLinkClick on page
            m = re.search(r"handleLinkClick\('([^']+)',\s*'download'\)", r_drive.text)
            if m:
                chosen_rel_go = m.group(1)
                matched_quality = "1080p HQ" if is_4k_request else quality_pref

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
                "poster": target_poster,
            }
        except Exception as e:
            log.warning("ToonFlix: Stream resolution failed: %s", e)
            return None


toonflix = ToonflixExtractor()
