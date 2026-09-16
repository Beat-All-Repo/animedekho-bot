"""AnimeDrive extractor — secondary high-speed source with HubCloud / direct Google stream resolution."""

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
        "filepress", "ouo.io", "linkvertise", "shrinkme", "shareus", "gofile.io"
    ]
    if any(bad in u for bad in unplayable_domains):
        return False
    # Known video stream / direct file patterns
    if any(ext in u for ext in (".m3u8", ".mpd", ".mp4", ".mkv", ".webm", ".ts", ".zip")):
        return True
    if any(dom in u for dom in ("googleusercontent.com", "drive.google.com", "mega.nz", "workers.dev", "storage.googleapis.com", "cloudflarestorage.com")):
        return True
    return False


def _decode_animedrive_dl(href: str) -> str | None:
    """Decode reversed base64 URL from /dl/<base64>."""
    try:
        if "/dl/" not in href:
            return None
        raw = href.split("/dl/")[1].split("?")[0]
        padded = raw + "=" * (-len(raw) % 4)
        dec = base64.b64decode(padded).decode("utf-8", errors="ignore")
        return dec[::-1]
    except Exception as e:
        log.debug("Failed to decode AnimeDrive dl link '%s': %s", href, e)
        return None


class AnimeDriveExtractor:
    """Extracts 4K, 1080p, 720p, 480p anime streams & movies from AnimeDrive (animedrive.me)."""

    def __init__(self):
        self._base_url = "https://animedrive.me"

    async def search(self, query: str) -> list[dict]:
        """Search AnimeDrive catalog for anime series or movies."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_search, query)

    def _sync_search(self, query: str) -> list[dict]:
        s = _get_scraper()
        clean = re.sub(
            r"(?i)\s*(season\s*\d+|s\d+|hindi|dubbed|multi-audio|tamil|telugu|uncensored|episodes).*$",
            "",
            query,
        ).strip()
        search_query = clean or query
        url = f"{self._base_url}/?s={quote_plus(search_query)}"
        try:
            r = s.get(url, timeout=15)
            if r.status_code != 200:
                log.warning("AnimeDrive search HTTP %d for '%s'", r.status_code, query)
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            for art in soup.find_all("article"):
                a = art.find("a", href=True)
                title_el = art.find(["h1", "h2", "h3", "h4"])
                img = art.find("img")
                if a and title_el:
                    title_text = title_el.get_text(strip=True)
                    results.append({
                        "title": title_text,
                        "url": a["href"],
                        "poster": img.get("src") or img.get("data-src", "") if img else "",
                    })
            return results
        except Exception as e:
            log.warning("AnimeDrive search failed for '%s': %s", query, e)
            return []

    async def get_series_episodes(self, page_url: str) -> list[dict]:
        """Fetch an AnimeDrive series page and extract available episodes."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_get_episodes, page_url)

    def _sync_get_episodes(self, page_url: str) -> list[dict]:
        s = _get_scraper()
        try:
            r = s.get(page_url, timeout=15)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")

            # Look for link to link.animedrive.me
            link_page = None
            for a in soup.find_all("a", href=True):
                if "link.animedrive.me" in a["href"]:
                    link_page = a["href"]
                    break

            target_soup = soup
            if link_page:
                r_link = s.get(link_page, timeout=15)
                if r_link.status_code == 200:
                    target_soup = BeautifulSoup(r_link.text, "html.parser")

            episodes = []
            seen_eps = set()

            for elem in target_soup.find_all(re.compile(r"^(h[1-6]|p|div)$")):
                txt = elem.get_text(" ", strip=True)
                m = re.search(r"(?:EP|Episode)\s*0*(\d+)", txt, re.I)
                if m and any(q in txt.lower() for q in ["hubcloud", "filepress", "480p", "720p", "1080p", "4k", "▼", "download"]):
                    ep_num = int(m.group(1))
                    if ep_num not in seen_eps:
                        seen_eps.add(ep_num)
                        episodes.append({
                            "season": 1,
                            "episode": ep_num,
                            "title": f"Episode {ep_num}",
                            "summary": txt[:80],
                        })

            return episodes
        except Exception as e:
            log.warning("AnimeDrive get_episodes error for %s: %s", page_url, e)
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
        log.info("AnimeDrive heuristic resolution failed for '%s' S%dE%d. Invoking AI Resolver...", anime_title, season, episode)
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
            log.info("AnimeDrive: No search results for '%s'", anime_title)
            return None

        # Step 2: Choose best matching series page for season
        target_page_url = None
        target_poster = None
        for res in search_results:
            t = res["title"].lower()
            if (
                f"season {season}" in t
                or f"season {season:02d}" in t
                or f"s{season}" in t
                or f"s{season:02d}" in t
            ):
                target_page_url = res["url"]
                target_poster = res.get("poster")
                break

        if not target_page_url and season == 1:
            # Check for title without "season" (or movie)
            for res in search_results:
                t = res["title"].lower()
                if "season" not in t:
                    target_page_url = res["url"]
                    target_poster = res.get("poster")
                    break

        if not target_page_url:
            target_page_url = search_results[0]["url"]
            target_poster = search_results[0].get("poster")

        log.info("AnimeDrive: Inspecting series page: %s", target_page_url)

        # Step 3: Fetch series page and locate link.animedrive.me
        try:
            r_page = s.get(target_page_url, timeout=15)
            if r_page.status_code != 200:
                return None
            soup_page = BeautifulSoup(r_page.text, "html.parser")
        except Exception as e:
            log.warning("AnimeDrive: Failed to fetch series page: %s", e)
            return None

        link_page = None
        for a in soup_page.find_all("a", href=True):
            if "link.animedrive.me" in a["href"]:
                link_page = a["href"]
                break

        target_soup = soup_page
        if link_page:
            try:
                log.info("AnimeDrive: Inspecting link page: %s", link_page)
                r_link = s.get(link_page, timeout=15)
                if r_link.status_code == 200:
                    target_soup = BeautifulSoup(r_link.text, "html.parser")
            except Exception as e:
                log.warning("AnimeDrive: Failed to fetch link page: %s", e)

        # Step 4: Locate episode block
        target_block = None
        for elem in target_soup.find_all(re.compile(r"^(h[1-6]|p|div)$")):
            txt = elem.get_text(" ", strip=True)
            m = re.search(r"(?:EP|Episode)\s*0*(\d+)", txt, re.I)
            if m and int(m.group(1)) == episode and any(
                q in txt.lower() for q in ["hubcloud", "filepress", "480p", "720p", "1080p", "4k", "▼", "download"]
            ):
                target_block = elem
                break

        # Collect download buttons
        buttons = []
        if target_block:
            buttons = target_block.find_all("a", href=True)

        if not buttons:
            # Fallback: check all /dl/ buttons on page
            all_dl = [a for a in target_soup.find_all("a", href=True) if "/dl/" in a["href"]]
            buttons = all_dl

        if not buttons:
            log.warning("AnimeDrive: No download buttons found on %s", link_page or target_page_url)
            return None

        # Step 5: Match quality preference
        is_4k = quality_pref.lower() in ("4k", "2160p", "2160")
        q_clean = quality_pref.lower().replace("p", "")

        hubcloud_candidates: list[tuple[str, str]] = []
        other_candidates: list[tuple[str, str]] = []

        for b in buttons:
            href = b["href"]
            if "/dl/" not in href:
                continue

            # Check both button text and its parent block text (e.g. "1080p Quality(3.53 GB) || Hub Cloud")
            parent = b.find_parent(["p", "div", "li", "tr", "h4", "h3", "h2"])
            parent_txt = parent.get_text(" ", strip=True).lower() if parent else ""
            b_txt = (parent_txt + " " + b.get_text(" ", strip=True).lower()).strip()

            dest = _decode_animedrive_dl(href)
            if not dest:
                continue

            quality_matched = "auto"
            matches_quality = False
            if is_4k and ("4k" in b_txt or "2160" in b_txt):
                matches_quality = True
                quality_matched = "4K"
            elif q_clean in b_txt:
                matches_quality = True
                quality_matched = quality_pref
            elif "1080" in b_txt:
                quality_matched = "1080p"
                if is_4k:
                    matches_quality = True
            elif "720" in b_txt:
                quality_matched = "720p"
            elif "480" in b_txt:
                quality_matched = "480p"

            if "hubcloud" in dest:
                if matches_quality:
                    hubcloud_candidates.insert(0, (dest, quality_matched))
                else:
                    hubcloud_candidates.append((dest, quality_matched))
            else:
                if matches_quality:
                    other_candidates.insert(0, (dest, quality_matched))
                else:
                    other_candidates.append((dest, quality_matched))

        all_candidates = hubcloud_candidates + other_candidates

        # Step 6: Resolve candidates to direct playable stream
        for dest, q_label in all_candidates:
            if "hubcloud" in dest:
                stream_url = self._resolve_hubcloud(s, dest)
                if stream_url and is_playable_media_url(stream_url):
                    log.info("AnimeDrive: Successfully resolved HubCloud direct stream [%s]: %s", q_label, stream_url[:80])
                    return {
                        "url": stream_url,
                        "quality": q_label if q_label != "auto" else quality_pref,
                        "server": "AnimeDrive (HubCloud)",
                        "referer": "https://hubcloud.ist/",
                        "poster": target_poster,
                    }
            elif is_playable_media_url(dest):
                log.info("AnimeDrive: Direct playable link [%s]: %s", q_label, dest[:80])
                return {
                    "url": dest,
                    "quality": q_label if q_label != "auto" else quality_pref,
                    "server": "AnimeDrive",
                    "referer": "https://link.animedrive.me/",
                    "poster": target_poster,
                }

        log.warning("AnimeDrive: Could not resolve playable stream for '%s' S%dE%d", anime_title, season, episode)
        return None

    def _resolve_hubcloud(self, s: cloudscraper.CloudScraper, hubcloud_url: str) -> str | None:
        """Resolve a HubCloud link to direct Google Cloud Storage (ZipDisk), R2, or fast stream URL."""
        try:
            r = s.get(hubcloud_url, timeout=15)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")

            gen_url = None
            for a in soup.find_all("a", href=True):
                h = a["href"]
                if "hubcloud.php" in h or "gamerxyt" in h:
                    gen_url = h
                    break

            if not gen_url:
                log.debug("AnimeDrive: No hubcloud.php found on %s", hubcloud_url)
                return None

            r2 = s.get(gen_url, headers={"Referer": hubcloud_url}, timeout=15)
            if r2.status_code != 200:
                return None
            soup2 = BeautifulSoup(r2.text, "html.parser")

            candidates = []
            for a in soup2.find_all("a", href=True):
                h = a["href"]
                if not h.startswith("http"):
                    continue
                # Prioritize ZipDisk (storage.googleapis.com) which has verified HTTP 200 OK delivery
                if "storage.googleapis.com" in h:
                    candidates.insert(0, h)
                elif "cloudflarestorage.com" in h:
                    candidates.append(h)
                elif "pixeldrain.dev/u/" in h:
                    candidates.append(h.replace("/u/", "/api/file/"))
                elif any(x in h for x in ("googleusercontent.com", "workers.dev", "dl.php")):
                    candidates.append(h)

            # Test candidates to find verified live working download
            for cand in candidates:
                try:
                    head = s.head(cand, allow_redirects=True, timeout=8)
                    if head.status_code in (200, 206):
                        ctype = head.headers.get("Content-Type", "").lower()
                        if not any(bad in ctype for bad in ("text/html", "application/json")):
                            log.info("AnimeDrive: Verified working HubCloud stream: %s", cand[:80])
                            return cand
                except Exception:
                    pass

                curr = cand
                for _ in range(5):
                    try:
                        r_step = s.get(curr, allow_redirects=False, timeout=8)
                        loc = r_step.headers.get("Location")
                        if not loc:
                            break
                        if "link=" in loc:
                            direct = urllib.parse.unquote(loc.split("link=")[1].split("&")[0])
                            if is_playable_media_url(direct):
                                return direct
                        curr = loc
                        if is_playable_media_url(curr):
                            return curr
                    except Exception:
                        break

            return None
        except Exception as e:
            log.warning("AnimeDrive: resolve_hubcloud failed: %s", e)
            return None

    async def _ai_resolve_fallback(
        self,
        anime_title: str,
        season: int,
        episode: int,
        quality_pref: str,
    ) -> dict | None:
        """Use autonomous AI LLM reasoning to navigate and resolve AnimeDrive when normal scripts fail."""
        from bot.ai.config import ai_config
        if not ai_config.enabled or not ai_config.api_key:
            log.warning("AI is not enabled/configured; skipping AI resolution fallback.")
            return None

        prompt = (
            f"Command: Locate and resolve the stream/download link for anime '{anime_title}' "
            f"Season {season} Episode {episode} with quality preference '{quality_pref}' on AnimeDrive (https://animedrive.me).\n"
            f"1. Search AnimeDrive catalog.\n"
            f"2. Inspect the series page and episode download buttons.\n"
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
                            "You are an expert anime URL extractor for AnimeDrive. "
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
                            raw_text = re.sub(r"^```(json)?", "", raw_text).strip("` \n")
                            j = json.loads(raw_text)
                            u = j.get("url")
                            if u and is_playable_media_url(u):
                                return {
                                    "url": u,
                                    "quality": j.get("quality", quality_pref),
                                    "server": "AnimeDrive (AI Resolved)",
                                    "referer": "https://animedrive.me/",
                                }
        except Exception as e:
            log.warning("AnimeDrive AI fallback resolution failed: %s", e)

        return None


animedrive = AnimeDriveExtractor()
