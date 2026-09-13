"""Agent tools for anime discovery, stream inspection, code self-repair, and sandboxed bash execution."""

from __future__ import annotations
import asyncio
import json
import logging
import os
import shutil
import shlex
from pathlib import Path
from typing import Any

from api.client import api
from extractors.resolver import resolve_player_url
from utils.helpers import esc, slug_to_title

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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
            "name": "search_toonflix",
            "description": "Search ToonFlix.in (fallback source with 4K / high quality anime).",
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
            "description": "Resolve episode or movie stream from ToonFlix.in, supporting 4K, 1080p, 720p.",
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
