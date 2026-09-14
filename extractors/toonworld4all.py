"""ToonWorld4All extractor — high-speed alternative source with AI-powered fallback resolution."""

from __future__ import annotations
import asyncio
import base64
import html as html_mod
import json
import logging
import re
import urllib.parse
from urllib.parse import quote_plus, urljoin
from bs4 import BeautifulSoup
import cloudscraper

from extractors.shortener import detect_and_bypass

log = logging.getLogger(__name__)


def _get_scraper() -> cloudscraper.CloudScraper:
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )


def is_playable_media_url(url: str) -> bool:
    """Return True if URL is likely a playable direct video stream or file, not an ad shortener or landing page."""
    if not url or not url.startswith("http"):
        return False
    u = url.lower()
    # Ad shorteners or file lockers with interactive captchas/landing pages
    unplayable_domains = [
        "exe.io", "cuty.io", "cutt.ly", "gplinks", "vshort", "droplink",
        "filepress", "hubcloud.ist/video", "hubcloud.club", "hubcloud.org",
        "ouo.io", "linkvertise", "shrinkme", "shareus", "gofile.io"
    ]
    if any(bad in u for bad in unplayable_domains):
        return False
    # Known video stream / direct file patterns
    if any(ext in u for ext in (".m3u8", ".mpd", ".mp4", ".mkv", ".webm", ".ts")):
        return True
    if any(dom in u for dom in ("googleusercontent.com", "drive.google.com", "mega.nz", "workers.dev")):
        return True
    return False



class Toonworld4allExtractor:
    """Extracts 4K, 1080p, 720p, 480p anime episodes & movies from ToonWorld4All with AI fallback."""

    def __init__(self):
        self._base_url = "https://toonworld4all.me"

    async def search(self, query: str) -> list[dict]:
        """Search ToonWorld4All catalog for anime series or movies."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_search, query)

    def _sync_search(self, query: str) -> list[dict]:
        s = _get_scraper()
        clean = re.sub(r"(?i)\s*(season\s*\d+|hindi|dubbed|multi-audio|tamil|telugu|uncensored|episodes).*$", "", query).strip()
        search_query = clean or query
        url = f"{self._base_url}/?s={quote_plus(search_query)}"
        try:
            r = s.get(url, timeout=15)
            if r.status_code != 200:
                log.warning("ToonWorld4All search HTTP %d for '%s'", r.status_code, query)
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            for art in soup.find_all("article"):
                a = art.find("a", href=True)
                title_el = art.find(["h2", "h3", "h4", "h1"])
                img = art.find("img")
                if a and title_el:
                    title_text = title_el.get_text(strip=True)
                    # Exclude general list pages
                    if "anime shows list" in title_text.lower():
                        continue
                    results.append({
                        "title": title_text,
                        "url": a["href"],
                        "poster": img.get("src") or img.get("data-src", "") if img else "",
                    })
            return results
        except Exception as e:
            log.warning("ToonWorld4All search failed for '%s': %s", query, e)
            return []

    async def get_series_episodes(self, page_url: str) -> list[dict]:
        """Fetch a ToonWorld4All series page and extract available episode links."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_get_episodes, page_url)

    def _sync_get_episodes(self, page_url: str) -> list[dict]:
        s = _get_scraper()
        try:
            r = s.get(page_url, timeout=15)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            episodes = []
            seen_urls = set()

            for a in soup.find_all("a", href=True):
                href = a["href"]
                txt = a.get_text(strip=True)
                if href in seen_urls:
                    continue

                # Pattern 1: Archive episode links: /episode/solo-leveling-1x1
                m = re.search(r"/episode/([a-zA-Z0-9_-]+)-(\d+)x(\d+)", href)
                if m:
                    seen_urls.add(href)
                    episodes.append({
                        "slug": m.group(1),
                        "season": int(m.group(2)),
                        "episode": int(m.group(3)),
                        "title": f"Episode {m.group(3)}",
                        "url": href,
                    })
                    continue

                # Pattern 2: Direct redirect links on page
                if "redirect/main.php" in href:
                    seen_urls.add(href)
                    parent = a.find_parent(lambda tag: tag.name in ["tr", "p", "div"] and re.search(r"ep\w*\s*(\d+)", tag.get_text(), re.I))
                    ep_num = 1
                    if parent:
                        ep_m = re.search(r"ep\w*\s*(\d+)", parent.get_text(), re.I)
                        if ep_m:
                            ep_num = int(ep_m.group(1))
                    episodes.append({
                        "slug": "direct",
                        "season": 1,
                        "episode": ep_num,
                        "title": f"Episode {ep_num} ({txt or 'Download'})",
                        "url": href,
                    })

            return episodes
        except Exception as e:
            log.warning("ToonWorld4All get_episodes error for %s: %s", page_url, e)
            return []

    async def resolve_episode(
        self,
        anime_title: str,
        season: int = 1,
        episode: int = 1,
        quality_pref: str = "1080p",
    ) -> dict | None:
        """Resolve direct stream/download link for an anime episode matching quality preference."""
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(
            None, self._sync_resolve, anime_title, season, episode, quality_pref
        )
        if res:
            return res

        # AI-powered fallback when standard heuristic parsing cannot resolve the stream
        log.info("ToonWorld4All heuristic resolution failed for '%s' S%dE%d. Invoking AI Resolver...", anime_title, season, episode)
        return await self._ai_resolve_fallback(anime_title, season, episode, quality_pref)

    def _sync_resolve(
        self,
        anime_title: str,
        season: int,
        episode: int,
        quality_pref: str,
    ) -> dict | None:
        s = _get_scraper()

        # Step 1: Search catalog
        search_results = self._sync_search(anime_title)
        if not search_results:
            log.info("ToonWorld4All: No search results for '%s'", anime_title)
            return None

        # Step 2: Choose best matching series page for season
        target_page_url = None
        for res in search_results:
            t = res["title"].lower()
            if f"season {season:02d}" in t or f"season {season}" in t or f"s{season}" in t:
                target_page_url = res["url"]
                break
            if season == 1 and "season" not in t:
                target_page_url = res["url"]
                break

        if not target_page_url:
            target_page_url = search_results[0]["url"]

        log.info("ToonWorld4All: Inspecting series page: %s", target_page_url)

        # Step 3: Get episode link from series page
        try:
            r_page = s.get(target_page_url, timeout=15)
            if r_page.status_code != 200:
                return None
            soup_page = BeautifulSoup(r_page.text, "html.parser")
        except Exception as e:
            log.warning("ToonWorld4All: Failed to fetch series page: %s", e)
            return None

        target_ep_url = None
        # Try archive link matching season and episode
        for a in soup_page.find_all("a", href=True):
            href = a["href"]
            if f"-{season}x{episode}" in href or f"_{season}x{episode}" in href or f"s0?{season}e0?{episode}" in href.lower():
                target_ep_url = href
                break

        if not target_ep_url:
            # Fallback: scan all archive episode links in order
            all_archive = [a["href"] for a in soup_page.find_all("a", href=True) if "/episode/" in a["href"]]
            if len(all_archive) >= episode:
                target_ep_url = all_archive[episode - 1]

        if not target_ep_url:
            # Check redirect/main.php links
            all_redirects = [a["href"] for a in soup_page.find_all("a", href=True) if "redirect/main.php" in a["href"]]
            if len(all_redirects) >= episode:
                target_ep_url = all_redirects[episode - 1]

        if not target_ep_url:
            log.info("ToonWorld4All: Episode S%dE%d not found on %s", season, episode, target_page_url)
            return None

        log.info("ToonWorld4All: Resolving episode URL: %s", target_ep_url)

        # Step 4: If target is redirect/main.php, decode 302 location
        if "redirect/main.php" in target_ep_url:
            return self._resolve_main_redirect(s, target_ep_url, quality_pref)

        # Step 5: Target is archive.toonworld4all.me/episode/...
        return self._resolve_archive_episode(s, target_ep_url, quality_pref)

    def _resolve_main_redirect(self, s: cloudscraper.CloudScraper, url: str, quality_pref: str) -> dict | None:
        try:
            r = s.get(url, allow_redirects=False, timeout=15)
            loc = r.headers.get("location", "")
            if "url=" in loc:
                raw = loc.split("url=")[1].split("&")[0]
                dec = urllib.parse.unquote(base64.b64decode(urllib.parse.unquote(raw)).decode("utf-8", errors="ignore"))
                if dec.startswith("http") and is_playable_media_url(dec):
                    return {
                        "url": dec,
                        "quality": quality_pref,
                        "server": "ToonWorld4All",
                        "referer": "https://toonworld4all.me/",
                    }
        except Exception as e:
            log.warning("ToonWorld4All main.php resolution failed: %s", e)
        return None

    def _resolve_archive_episode(self, s: cloudscraper.CloudScraper, archive_url: str, quality_pref: str) -> dict | None:
        try:
            r = s.get(archive_url, timeout=15)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            log.warning("ToonWorld4All: Failed to fetch archive episode %s: %s", archive_url, e)
            return None

        is_4k = quality_pref.lower() in ("4k", "2160p", "2160")
        target_q_clean = quality_pref.lower().replace("p", "")

        # Collect quality candidate blocks
        matched_redirect_url = None
        matched_quality = quality_pref

        # Search for headings/labels with matching quality
        for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "div", "button", "span"]):
            txt = h.get_text(strip=True).lower()
            matches = False
            if is_4k and ("4k" in txt or "2160" in txt):
                matches = True
                matched_quality = "4K"
            elif target_q_clean in txt:
                matches = True
                matched_quality = quality_pref

            if matches:
                parent = h.find_parent(lambda tag: tag.name in ["div", "section", "tr", "td"] and tag.find("a", href=True))
                if parent:
                    for a in parent.find_all("a", href=True):
                        href = a["href"]
                        if "/redirect/" in href:
                            matched_redirect_url = href if href.startswith("http") else urljoin("https://archive.toonworld4all.me", href)
                            break
                if matched_redirect_url:
                    break

        if not matched_redirect_url:
            # Fallback to any /redirect/ link available
            for a in soup.find_all("a", href=True):
                if "/redirect/" in a["href"]:
                    matched_redirect_url = a["href"] if a["href"].startswith("http") else urljoin("https://archive.toonworld4all.me", a["href"])
                    break

        if not matched_redirect_url:
            log.warning("ToonWorld4All: No download redirect found on %s", archive_url)
            return None

        # Fetch redirect endpoint to extract props or destination
        try:
            r_red = s.get(matched_redirect_url, timeout=15)
            props_match = re.search(r"window\.__PROPS__\s*=\s*(\{.*?\});", r_red.text)
            if props_match:
                props = json.loads(props_match.group(1))
                link_data = props.get("link") or {}
                domain = link_data.get("domain", "")
                hidden = link_data.get("hidden", "")

                # If domain and hidden are present, construct direct URL and verify it's playable
                if domain and hidden:
                    direct_url = f"{domain.rstrip('/')}/{hidden.lstrip('/')}"
                    if is_playable_media_url(direct_url):
                        return {
                            "url": direct_url,
                            "quality": matched_quality,
                            "server": "ToonWorld4All",
                            "referer": "https://archive.toonworld4all.me/",
                        }

                # Otherwise check destination shortener and verify if playable
                dest = props.get("destination")
                if dest and is_playable_media_url(dest):
                    return {
                        "url": dest,
                        "quality": matched_quality,
                        "server": "ToonWorld4All",
                        "referer": "https://archive.toonworld4all.me/",
                    }

                log.info("ToonWorld4All link is behind protected ad-shortener/filepress: %s", dest or domain)
        except Exception as e:
            log.warning("ToonWorld4All redirect parse error: %s", e)

        return None

    async def _ai_resolve_fallback(
        self,
        anime_title: str,
        season: int,
        episode: int,
        quality_pref: str,
    ) -> dict | None:
        """Use autonomous AI LLM reasoning to navigate and resolve ToonWorld4All when normal scripts fail."""
        from bot.ai.config import ai_config
        if not ai_config.enabled or not ai_config.api_key:
            log.warning("AI is not enabled/configured; skipping AI resolution fallback.")
            return None

        prompt = (
            f"Command: Locate and resolve the stream/download link for anime '{anime_title}' "
            f"Season {season} Episode {episode} with quality preference '{quality_pref}' on ToonWorld4All (https://toonworld4all.me).\n"
            f"1. Search ToonWorld4All.\n"
            f"2. Inspect the series page and episode download page.\n"
            f"3. Return the direct video/file URL or stream URL.\n"
            f"Output strictly valid JSON with keys 'url' and 'quality'."
        )

        try:
            import aiohttp
            endpoint = f"{ai_config.base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {ai_config.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": ai_config.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an expert anime URL extractor for ToonWorld4All. "
                            "You parse page layouts and extract direct stream and download links. "
                            "Respond ONLY with valid JSON: {\"url\": \"...\", \"quality\": \"...\"}"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
            }
            timeout = aiohttp.ClientTimeout(total=45)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(endpoint, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        choices = data.get("choices", [])
                        if choices:
                            raw_text = choices[0].get("message", {}).get("content", "").strip()
                            # Clean code fence
                            raw_text = re.sub(r"^```(json)?", "", raw_text).strip("` \n")
                            j = json.loads(raw_text)
                            u = j.get("url")
                            if u and is_playable_media_url(u):
                                return {
                                    "url": u,
                                    "quality": j.get("quality", quality_pref),
                                    "server": "ToonWorld4All (AI Resolved)",
                                    "referer": "https://toonworld4all.me/",
                                }
                            else:
                                log.info("AI fallback returned non-playable or locker link: %s", u)
        except Exception as e:
            log.warning("AI fallback resolution failed: %s", e)

        return None


toonworld4all = Toonworld4allExtractor()
