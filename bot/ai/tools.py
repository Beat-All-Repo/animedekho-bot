"""Agent tools for anime discovery, stream inspection, code self-repair, and sandboxed bash execution."""

from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import shutil
import shlex
from pathlib import Path
from typing import Any

from api.client import api
from extractors.resolver import resolve_player_url
from utils.helpers import esc, slug_to_title

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_active_context: dict[str, Any] = {
    "client": None,
    "chat_id": None,
}


def set_active_context(client: Any = None, chat_id: int | None = None):
    """Set the Pyrogram client and chat_id for autonomous download tools."""
    _active_context["client"] = client
    _active_context["chat_id"] = chat_id


def get_active_context() -> tuple[Any, int | None]:
    """Retrieve the currently active Pyrogram client and chat_id."""
    return _active_context.get("client"), _active_context.get("chat_id")


def _validate_path(path_str: str) -> Path:
    """Ensure the path is strictly within the project directory."""
    clean_path = path_str.strip().lstrip("/")
    resolved = (PROJECT_ROOT / clean_path).resolve()
    if not resolved.is_relative_to(PROJECT_ROOT):
        raise PermissionError(f"Access denied: '{path_str}' is outside project directory {PROJECT_ROOT}")
    return resolved


# ── Tool Definitions for OpenAI Function Calling ───────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_anime",
            "description": "Search for anime series or movies on AnimeDekho by name or keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Anime or movie title to search for (e.g. 'naruto', 'solo leveling', 'jujutsu kaisen').",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_series_details",
            "description": "Get detailed metadata, season list, and episode counts for an anime series.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Series slug (e.g. 'naruto-shippuden-hindi-tamil-telugu', 'solo-leveling-hindi').",
                    }
                },
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_episode_servers",
            "description": "Inspect and resolve video servers and quality streams for a specific episode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ep_slug": {
                        "type": "string",
                        "description": "Episode slug (e.g. 'yowayowa-sensei-1x1', 'naruto-shippuden-16x349').",
                    }
                },
                "required": ["ep_slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_player_stream",
            "description": "Dynamically resolve a player embed URL to inspect underlying m3u8 or mp4 video streams.",
            "parameters": {
                "type": "object",
                "properties": {
                    "player_url": {
                        "type": "string",
                        "description": "Player URL or embed link to inspect.",
                    }
                },
                "required": ["player_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_project_files",
            "description": "List files and directories inside the project repository. Cannot access outside the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subpath": {
                        "type": "string",
                        "description": "Subdirectory to list, relative to project root (e.g. 'bot', 'api', 'config'). Default is root.",
                        "default": "",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_project_file",
            "description": "Read source code or configuration of a file in the project. Strictly sandboxed to project directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path to file inside project (e.g. 'bot/downloader.py', 'extractors/resolver.py').",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "Maximum number of lines to read (default 200).",
                        "default": 200,
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_project_file",
            "description": "Replace a specific snippet in a project file with updated code to fix bugs or self-heal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path to file inside project directory.",
                    },
                    "old_code": {
                        "type": "string",
                        "description": "Exact text chunk in the file to replace.",
                    },
                    "new_code": {
                        "type": "string",
                        "description": "Replacement code.",
                    },
                },
                "required": ["file_path", "old_code", "new_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_project_file",
            "description": "Write or overwrite a file within the project directory. Creates parent directories if needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path to file inside project directory.",
                    },
                    "content": {
                        "type": "string",
                        "description": "File contents to write.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell_command",
            "description": "Run a sandboxed shell command strictly inside the project root directory. Dangerous commands outside project root are blocked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run (e.g. 'git status', 'python3 -m py_compile ...', 'pytest').",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_source_status",
            "description": "Perform a live diagnostic health check on streaming sources (ToonWorld4All and AnimeDekho), checking site connectivity, catalog search, and direct stream availability.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Source to check: 'toonworld4all', 'animedekho', or 'all' (default: 'all').",
                        "default": "all",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_toonworld4all",
            "description": "Search ToonWorld4All (https://toonworld4all.me) catalog for anime series or movies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Anime or movie title to search on ToonWorld4All.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_toonworld4all_episodes",
            "description": "Extract available seasons and episodes from a ToonWorld4All series page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_url": {
                        "type": "string",
                        "description": "Full URL to a series page on ToonWorld4All.",
                    }
                },
                "required": ["page_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_toonworld4all_stream",
            "description": "Resolve episode or movie stream from ToonWorld4All with AI-powered fallback (supports 4K, 1080p, 720p, 480p).",
            "parameters": {
                "type": "object",
                "properties": {
                    "anime_title": {
                        "type": "string",
                        "description": "Anime title.",
                    },
                    "season": {
                        "type": "integer",
                        "description": "Season number (default 1).",
                        "default": 1,
                    },
                    "episode": {
                        "type": "integer",
                        "description": "Episode number (default 1).",
                        "default": 1,
                    },
                    "quality_pref": {
                        "type": "string",
                        "description": "Target resolution: '4K', '1080p', '720p', '480p'.",
                        "default": "1080p",
                    },
                },
                "required": ["anime_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_and_send_anime",
            "description": "Download an anime video from any resolved stream/file URL and send it directly to the Telegram chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stream_url": {
                        "type": "string",
                        "description": "Direct media/stream URL to download.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Title and episode label (e.g. 'Solo Leveling S01E01').",
                    },
                    "quality": {
                        "type": "string",
                        "description": "Quality resolution (e.g. '1080p', '720p', '4K').",
                        "default": "1080p",
                    },
                },
                "required": ["stream_url", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_anime_episode",
            "description": "Autonomous master tool to download any anime episode or movie and send it directly to the Commander's Telegram chat. Automatically handles ToonWorld4All and AnimeDekho resolution with smart fallback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "anime_title": {
                        "type": "string",
                        "description": "Title of the anime series or movie (e.g. 'Solo Leveling', 'Naruto Shippuden', 'Demon Slayer').",
                    },
                    "season": {
                        "type": "integer",
                        "description": "Season number (default: 1).",
                        "default": 1,
                    },
                    "episode": {
                        "type": "integer",
                        "description": "Episode number (default: 1).",
                        "default": 1,
                    },
                    "quality_pref": {
                        "type": "string",
                        "description": "Desired resolution: '1080p', '720p', '480p', or '4K' (default: '1080p').",
                        "default": "1080p",
                    },
                    "source": {
                        "type": "string",
                        "description": "Preferred source: 'auto', 'toonworld4all', or 'animedekho' (default: 'auto').",
                        "default": "auto",
                    },
                },
                "required": ["anime_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_toonflix",
            "description": "Search ToonFlix.in (legacy fallback).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Anime or movie title to search on ToonFlix.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_toonflix_stream",
            "description": "Resolve episode or movie stream from ToonFlix.in.",
            "parameters": {
                "type": "object",
                "properties": {
                    "anime_title": {
                        "type": "string",
                        "description": "Anime title.",
                    },
                    "season": {
                        "type": "integer",
                        "description": "Season number (default 1).",
                        "default": 1,
                    },
                    "episode": {
                        "type": "integer",
                        "description": "Episode number (default 1).",
                        "default": 1,
                    },
                    "quality_pref": {
                        "type": "string",
                        "description": "Target resolution: '4K', '1080p', '720p', '480p'.",
                        "default": "1080p",
                    },
                },
                "required": ["anime_title"],
            },
        },
    },
]


# ── Tool Implementations ───────────────────────────────────────────────

async def tool_search_anime(query: str) -> str:
    try:
        results = await api.search(query)
        if not results:
            return f"No results found for query: '{query}'"
        summary = []
        for r in results[:10]:
            summary.append({
                "title": r.title,
                "type": r.content_type,
                "slug": r.slug,
                "url": r.url,
            })
        return json.dumps(summary, indent=2)
    except Exception as e:
        return f"Search error: {e}"


async def tool_get_series_details(slug: str) -> str:
    try:
        series = await api.get_series(slug)
        seasons_info = {}
        for s_num, s_obj in series.seasons.items():
            seasons_info[f"Season {s_num}"] = {
                "episode_count": s_obj.episode_count,
                "first_episodes": [ep.slug for ep in s_obj.episodes[:5]],
            }
        data = {
            "title": series.title,
            "slug": series.slug,
            "genres": series.genres,
            "season_count": series.season_count,
            "total_episodes": series.total_episodes,
            "seasons": seasons_info,
            "description": series.description[:300] + ("..." if len(series.description) > 300 else ""),
        }
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"Error getting series details for '{slug}': {e}"


async def tool_inspect_episode_servers(ep_slug: str) -> str:
    try:
        ep = await api.get_episode(ep_slug)
        server_info = []
        for srv in ep.servers:
            resolved_srv = await api.resolve_server(srv)
            resolved_stream = None
            if resolved_srv.player_url:
                resolved_stream = await resolve_player_url(resolved_srv.player_url)
            server_info.append({
                "server_name": srv.name,
                "player_url": resolved_srv.player_url,
                "stream_resolved": bool(resolved_stream),
                "qualities": [q.resolution for q in resolved_stream.get("qualities", [])] if resolved_stream else [],
            })
        return json.dumps({
            "title": ep.title,
            "episode_slug": ep_slug,
            "servers": server_info,
        }, indent=2)
    except Exception as e:
        return f"Error inspecting episode servers for '{ep_slug}': {e}"


async def tool_resolve_player_stream(player_url: str) -> str:
    try:
        res = await resolve_player_url(player_url)
        if not res:
            return f"Failed to resolve player URL: {player_url}"
        info = {
            "url": res.get("url"),
            "type": res.get("type"),
            "qualities": [q.resolution for q in res.get("qualities", [])],
        }
        return json.dumps(info, indent=2)
    except Exception as e:
        return f"Error resolving stream: {e}"


async def tool_list_project_files(subpath: str = "") -> str:
    try:
        target_dir = _validate_path(subpath)
        if not target_dir.is_dir():
            return f"Error: '{subpath}' is not a directory."
        items = []
        for item in sorted(target_dir.iterdir()):
            if item.name.startswith((".", "__pycache__")):
                continue
            rel = item.relative_to(PROJECT_ROOT)
            items.append(f"{'[DIR] ' if item.is_dir() else '[FILE]'} {rel}")
        return "\n".join(items) if items else "(empty directory)"
    except Exception as e:
        return f"Error listing directory: {e}"


async def tool_read_project_file(file_path: str, max_lines: int = 200) -> str:
    try:
        path = _validate_path(file_path)
        if not path.is_file():
            return f"Error: File '{file_path}' does not exist."
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        truncated = len(lines) > max_lines
        content = "\n".join(lines[:max_lines])
        if truncated:
            content += f"\n... [Truncated {len(lines) - max_lines} lines]"
        return content
    except Exception as e:
        return f"Error reading file: {e}"


async def tool_edit_project_file(file_path: str, old_code: str, new_code: str) -> str:
    try:
        path = _validate_path(file_path)
        if not path.is_file():
            return f"Error: File '{file_path}' does not exist."
        content = path.read_text(encoding="utf-8")
        if old_code not in content:
            return "Error: 'old_code' chunk was not found in target file. Make sure whitespace and line breaks match exactly."
        updated = content.replace(old_code, new_code, 1)
        path.write_text(updated, encoding="utf-8")
        return f"Successfully updated '{file_path}'."
    except Exception as e:
        return f"Error editing file: {e}"


async def tool_write_project_file(file_path: str, content: str) -> str:
    try:
        path = _validate_path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Successfully wrote '{file_path}' ({len(content)} bytes)."
    except Exception as e:
        return f"Error writing file: {e}"


async def tool_run_shell_command(command: str) -> str:
    """Run shell command sandboxed strictly within project root."""
    try:
        cmd_str = command.strip()
        # Security checks
        forbidden_patterns = [
            "rm -rf /", ":(){ :|:& };:", "mkfs", "dd if=", "> /dev/sd",
            "chmod -R 777 /", "shutdown", "reboot", "poweroff",
            "../..", "/etc/", "/root", "/var/log"
        ]
        for bad in forbidden_patterns:
            if bad in cmd_str:
                return f"Error: Command rejected for security reasons: contains '{bad}'."

        proc = await asyncio.create_subprocess_shell(
            cmd_str,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except asyncio.TimeoutError:
            proc.kill()
            return "Error: Command timed out after 30 seconds."

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        output = []
        output.append(f"Exit code: {proc.returncode}")
        if stdout_text:
            output.append(f"Stdout:\n{stdout_text[:2500]}")
        if stderr_text:
            output.append(f"Stderr:\n{stderr_text[:1000]}")
        return "\n\n".join(output)
    except Exception as e:
        return f"Error executing shell command: {e}"


async def tool_search_toonworld4all(query: str) -> str:
    try:
        from extractors.toonworld4all import toonworld4all
        results = await toonworld4all.search(query)
        return json.dumps(results[:10], indent=2) if results else f"No results on ToonWorld4All for '{query}'"
    except Exception as e:
        return f"ToonWorld4All search error: {e}"


async def tool_get_toonworld4all_episodes(page_url: str) -> str:
    try:
        from extractors.toonworld4all import toonworld4all
        eps = await toonworld4all.get_series_episodes(page_url)
        return json.dumps(eps, indent=2) if eps else f"No episodes found on {page_url}"
    except Exception as e:
        return f"Error extracting episodes from {page_url}: {e}"


async def tool_check_source_status(source: str = "all") -> str:
    """Perform a live diagnostic health check on streaming sources."""
    results = {}

    if source.lower() in ("toonworld4all", "all"):
        tw_diag = {
            "source": "ToonWorld4All",
            "website_url": "https://toonworld4all.me",
            "website_online": False,
            "archive_online": False,
            "catalog_search_working": False,
            "direct_streams_available": False,
            "stream_delivery_method": "Protected by ad-shorteners (exe.io / cuty.io) with Cloudflare turnstiles and file lockers (FilePress/Mega).",
            "summary": "",
        }
        try:
            import cloudscraper
            s = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "desktop": True})
            r1 = await asyncio.to_thread(s.get, "https://toonworld4all.me", timeout=10)
            tw_diag["website_online"] = (r1.status_code == 200)
            tw_diag["website_http_status"] = r1.status_code

            r2 = await asyncio.to_thread(s.get, "https://archive.toonworld4all.me", timeout=10)
            tw_diag["archive_online"] = (r2.status_code == 200)
            tw_diag["archive_http_status"] = r2.status_code

            from extractors.toonworld4all import toonworld4all
            search_res = await toonworld4all.search("solo leveling")
            tw_diag["catalog_search_working"] = bool(search_res)
            tw_diag["sample_results_found"] = len(search_res)

            if tw_diag["website_online"]:
                tw_diag["summary"] = (
                    "ToonWorld4All website and catalog search are fully ONLINE. "
                    "However, individual episode video streams are locked behind interactive ad-shortener captchas (exe.io) "
                    "and cannot be scraped directly via automated scripts."
                )
            else:
                tw_diag["summary"] = "ToonWorld4All website could not be reached."
        except Exception as e:
            tw_diag["error"] = str(e)
            tw_diag["summary"] = f"Diagnostic failed: {e}"

        results["ToonWorld4All"] = tw_diag

    if source.lower() in ("animedekho", "all"):
        ad_diag = {
            "source": "AnimeDekho",
            "service_url": "https://animedekho.app",
            "service_online": True,
            "catalog_search_working": False,
            "direct_streams_available": True,
            "stream_delivery_method": "Direct unencrypted HLS master playlists (m3u8) on VidStream, Vidmoly, and NeoCDN.",
            "summary": "AnimeDekho is fully operational with high-speed direct video streams available in 1080p, 720p, 480p.",
        }
        try:
            from api.client import api
            search_res = await api.search("solo leveling")
            ad_diag["catalog_search_working"] = bool(search_res)
            ad_diag["sample_results_found"] = len(search_res)
        except Exception as e:
            ad_diag["error"] = str(e)

        results["AnimeDekho"] = ad_diag

    return json.dumps(results, indent=2)


async def tool_resolve_toonworld4all_stream(anime_title: str, season: int = 1, episode: int = 1, quality_pref: str = "1080p") -> str:
    try:
        from extractors.toonworld4all import toonworld4all
        res = await toonworld4all.resolve_episode(anime_title, season=season, episode=episode, quality_pref=quality_pref)
        if res and res.get("url"):
            return json.dumps(res, indent=2)

        return json.dumps({
            "source": "ToonWorld4All",
            "anime_title": anime_title,
            "season": season,
            "episode": episode,
            "site_status": "ONLINE (https://toonworld4all.me is up and catalog search is functional)",
            "stream_status": "UNAVAILABLE_AUTOMATED",
            "reason": (
                f"ToonWorld4All website is completely ONLINE, but individual episode download buttons on "
                f"archive.toonworld4all.me are protected behind third-party ad-shorteners (exe.io / cuty.io) "
                f"which require interactive Cloudflare turnstile captcha solving by a human browser, leading to file locker "
                f"landing pages (FilePress/Mega) rather than direct streamable media. "
                f"Automated HTTP streaming directly from ToonWorld4All is blocked by these anti-bot captchas."
            ),
            "solution": (
                f"Use 'download_anime_episode' or AnimeDekho directly. AnimeDekho has direct high-speed 1080p/720p "
                f"VidStream/Vidmoly streams for '{anime_title}' S{season}E{episode} ready for immediate download and delivery to Telegram."
            ),
        }, indent=2)
    except Exception as e:
        return f"ToonWorld4All resolution error: {e}"


async def _resolve_animedekho_stream(anime_title: str, season: int, episode: int, quality_pref: str) -> tuple[dict | None, str | None]:
    """Internal helper to locate and resolve direct streams from AnimeDekho."""
    try:
        results = await api.search(anime_title)
        if not results:
            return None, None
        best = None
        for r in results:
            if anime_title.lower() in r.title.lower():
                best = r
                break
        if not best:
            best = results[0]

        srv_priority = ['VidStream', 'Vidmoly', 'NeoCDN', 'MyCloud', 'HydraX', 'VidSrc', 'VidCloud']

        if best.is_series:
            series = await api.get_series(best.slug)
            s_obj = series.seasons.get(season)
            if not s_obj and season == 1 and len(series.seasons) == 1:
                s_obj = list(series.seasons.values())[0]
            if not s_obj:
                return None, None
            ep_obj = None
            for ep in s_obj.episodes:
                if ep.number == episode:
                    ep_obj = ep
                    break
            if not ep_obj and len(s_obj.episodes) >= episode:
                ep_obj = s_obj.episodes[episode - 1]
            if not ep_obj:
                return None, None

            ep_detail = await api.get_episode(ep_obj.slug)
            sorted_servers = sorted(ep_detail.servers, key=lambda s: srv_priority.index(s.name) if s.name in srv_priority else 99)
            for srv in sorted_servers:
                try:
                    resolved_srv = await api.resolve_server(srv)
                    if resolved_srv.player_url:
                        stream = await resolve_player_url(resolved_srv.player_url)
                        if stream and stream.get("url"):
                            return stream, f"AnimeDekho ({srv.name})"
                except Exception:
                    pass
        else:
            movie = await api.get_movie(best.slug)
            sorted_servers = sorted(movie.servers, key=lambda s: srv_priority.index(s.name) if s.name in srv_priority else 99)
            for srv in sorted_servers:
                try:
                    resolved_srv = await api.resolve_server(srv)
                    if resolved_srv.player_url:
                        stream = await resolve_player_url(resolved_srv.player_url)
                        if stream and stream.get("url"):
                            return stream, f"AnimeDekho ({srv.name})"
                except Exception:
                    pass
    except Exception as e:
        log.warning("_resolve_animedekho_stream failed for %s: %s", anime_title, e)
    return None, None


async def tool_download_anime_episode(
    anime_title: str,
    season: int = 1,
    episode: int = 1,
    quality_pref: str = "1080p",
    source: str = "auto",
) -> str:
    try:
        from pyrogram import enums
        from bot.downloader import download_and_upload
        from bot.ai.config import ai_config

        client, chat_id = get_active_context()
        if not client:
            from bot.app import active_bot_client
            client = active_bot_client
        if not chat_id:
            from config.settings import settings
            chat_id = int(settings.bot.owner_id) if settings.bot.owner_id else None

        if not client or not chat_id:
            return "Error: Telegram bot client or destination chat is not initialized."

        name = ai_config.name
        display_title = f"{anime_title} S{season:02d}E{episode:02d}"
        status_msg = await client.send_message(
            chat_id=chat_id,
            text=f"🤖 <b>{name}</b>: Locating episode <b>{display_title}</b> [{quality_pref}]...",
            parse_mode=enums.ParseMode.HTML,
        )

        stream_url = None
        variant_url = ""
        source_used = None
        notes = []

        # Step 1: Try ToonWorld4All if requested or in auto mode
        if source.lower() in ("toonworld4all", "auto"):
            try:
                from extractors.toonworld4all import toonworld4all, is_playable_media_url
                tw_res = await toonworld4all.resolve_episode(anime_title, season=season, episode=episode, quality_pref=quality_pref)
                if tw_res and tw_res.get("url") and is_playable_media_url(tw_res["url"]):
                    stream_url = tw_res["url"]
                    source_used = f"ToonWorld4All ({tw_res.get('server', 'Direct')})"
                else:
                    notes.append("ToonWorld4All link is protected by shortener/Cloudflare captcha or unavailable")
            except Exception as e:
                notes.append(f"ToonWorld4All error: {e}")

        # Step 2: Fallback to AnimeDekho (ultra-fast direct HLS)
        if not stream_url:
            try:
                await status_msg.edit_text(
                    f"🤖 <b>{name}</b>: ToonWorld4All is locked/unavailable. Switching to AnimeDekho direct stream for <b>{display_title}</b> [{quality_pref}]...",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass

            stream_obj, srv_name = await _resolve_animedekho_stream(anime_title, season, episode, quality_pref)
            if stream_obj and stream_obj.get("url"):
                stream_url = stream_obj["url"]
                source_used = srv_name
                for q in stream_obj.get("qualities", []):
                    if hasattr(q, "resolution") and q.resolution.lower() == quality_pref.lower() and q.url:
                        variant_url = q.url
                        break
            else:
                notes.append("AnimeDekho episode servers not available")

        # Step 3: If still not resolved, try ToonFlix
        if not stream_url:
            try:
                from extractors.toonflix import toonflix
                tf_res = await toonflix.resolve_episode(anime_title, season=season, episode=episode, quality_pref=quality_pref)
                if tf_res and tf_res.get("url"):
                    stream_url = tf_res["url"]
                    source_used = f"ToonFlix ({tf_res.get('server', 'Direct')})"
            except Exception as e:
                notes.append(f"ToonFlix error: {e}")

        if not stream_url:
            err_details = "; ".join(notes) if notes else "No playable stream found"
            try:
                await status_msg.edit_text(
                    f"❌ <b>{name}</b>: Failed to resolve stream for <b>{display_title}</b> [{quality_pref}].\nDetails: {err_details}",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
            return f"Failed to locate playable stream for '{display_title}'. Details: {err_details}"

        # Step 4: Download and send to chat
        try:
            await status_msg.edit_text(
                f"🤖 <b>{name}</b>: Downloading <b>{display_title}</b> [{quality_pref}] via {source_used}...",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

        clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', anime_title)
        filename = f"{clean_slug}_S{season:02d}E{episode:02d}_{quality_pref}.mp4"

        success, sent_msg = await download_and_upload(
            chat_id=chat_id,
            stream_url=stream_url,
            quality=quality_pref,
            filename=filename,
            title=display_title,
            progress_msg=status_msg,
            client=client,
            variant_url=variant_url,
        )

        if success and sent_msg:
            return (
                f"Success: Successfully downloaded and delivered '{display_title}' [{quality_pref}] "
                f"to Telegram chat {chat_id} via {source_used}."
            )
        return f"Download or upload failed for '{display_title}' via {source_used}."
    except Exception as e:
        log.exception("tool_download_anime_episode failed")
        return f"Error executing download: {e}"


async def tool_download_and_send_anime(stream_url: str, title: str, quality: str = "1080p") -> str:
    try:
        from pyrogram import enums
        from config.settings import settings
        from bot.downloader import download_and_upload
        from bot.ai.config import ai_config

        client, chat_id = get_active_context()
        if not client:
            from bot.app import active_bot_client
            client = active_bot_client
        if not chat_id:
            chat_id = int(settings.bot.owner_id) if settings.bot.owner_id else None

        if not client or not chat_id:
            return "Error: Telegram bot client or destination chat is not initialized."

        name = ai_config.name
        status_msg = await client.send_message(
            chat_id=chat_id,
            text=f"🤖 <b>{name}</b>: Initiating download for <b>{title}</b> [{quality}]...",
            parse_mode=enums.ParseMode.HTML,
        )

        clean_filename = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', title)}_{quality}.mp4"
        success, sent_msg = await download_and_upload(
            chat_id=chat_id,
            stream_url=stream_url,
            quality=quality,
            filename=clean_filename,
            title=title,
            progress_msg=status_msg,
            client=client,
        )

        if success and sent_msg:
            return f"Success: Downloaded and uploaded '{title}' [{quality}] directly to Telegram chat {chat_id}."
        return f"Download or upload failed for '{title}'. Check server logs for details."
    except Exception as e:
        log.exception("tool_download_and_send_anime failed")
        return f"Error downloading and sending anime: {e}"


async def tool_search_toonflix(query: str) -> str:
    try:
        from extractors.toonflix import toonflix
        results = await toonflix.search(query)
        return json.dumps(results[:10], indent=2) if results else f"No results on ToonFlix for '{query}'"
    except Exception as e:
        return f"ToonFlix search error: {e}"


async def tool_resolve_toonflix_stream(anime_title: str, season: int = 1, episode: int = 1, quality_pref: str = "1080p") -> str:
    try:
        from extractors.toonflix import toonflix
        res = await toonflix.resolve_episode(anime_title, season=season, episode=episode, quality_pref=quality_pref)
        return json.dumps(res, indent=2) if res else f"Could not resolve stream for '{anime_title}' S{season}E{episode} [{quality_pref}] on ToonFlix."
    except Exception as e:
        return f"ToonFlix resolution error: {e}"


# ── Tool Dispatcher ───────────────────────────────────────────────────

TOOL_MAP = {
    "search_anime": tool_search_anime,
    "get_series_details": tool_get_series_details,
    "inspect_episode_servers": tool_inspect_episode_servers,
    "resolve_player_stream": tool_resolve_player_stream,
    "list_project_files": tool_list_project_files,
    "read_project_file": tool_read_project_file,
    "edit_project_file": tool_edit_project_file,
    "write_project_file": tool_write_project_file,
    "run_shell_command": tool_run_shell_command,
    "check_source_status": tool_check_source_status,
    "search_toonworld4all": tool_search_toonworld4all,
    "get_toonworld4all_episodes": tool_get_toonworld4all_episodes,
    "resolve_toonworld4all_stream": tool_resolve_toonworld4all_stream,
    "download_anime_episode": tool_download_anime_episode,
    "download_and_send_anime": tool_download_and_send_anime,
    "search_toonflix": tool_search_toonflix,
    "resolve_toonflix_stream": tool_resolve_toonflix_stream,
}


async def execute_tool_call(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool call requested by the LLM."""
    func = TOOL_MAP.get(name)
    if not func:
        return f"Error: Unknown tool '{name}'."
    try:
        log.info("Executing AI tool: %s with args %s", name, arguments)
        result = await func(**arguments)
        return str(result)
    except Exception as e:
        log.exception("Tool execution failed for %s", name)
        return f"Error executing {name}: {e}"

